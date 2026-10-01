// EEST fixture runner: executes a digested EEST blockchain fixture
// against the production monad runloop (EestNet chain) and emits the
// resulting post-state as JSON.
//
// Input document (produced by the EEST `consume direct` Monad consumer):
// {
//   "genesis_alloc": {
//     "0x<address>": {
//       "wei_balance": "<decimal or 0x-hex>",
//       "nonce": "0x..",            // optional
//       "code": "0x..",             // optional
//       "storage": {"0x..": "0x.."} // optional
//     }, ...
//   },
//   "blocks": [
//     {
//       "timestamp": <seconds>,
//       "base_fee": <wei>,
//       "beneficiary": "0x<address>",
//       "txs": ["0x<raw signed tx rlp>", ...]
//     }, ...
//   ]
// }
//
// Output document:
// {
//   "state_root": "0x..",
//   "post_state": {
//     "0x<address>": {
//       "balance": "0x..",
//       "nonce": "0x..",
//       "code": "0x..",
//       "storage": {"0x<slot>": "0x<value>", ...}
//     }, ...
//   }
// }

use std::collections::BTreeMap;
use std::path::PathBuf;

use alloy_eips::eip2718::Decodable2718;
use alloy_consensus::TxEnvelope;
use alloy_primitives::{Address, U256};
use clap::Parser;
use ledger::Ledger;
use monad_chain_config::MonadChainConfig;
use runloop::{MonadRunloop, Runloop};
use serde::Deserialize;
use serde_json::json;

pub const EEST_NET_CHAIN_ID: u64 = 30143;

#[derive(Parser)]
#[command(name = "eest-runner", version)]
struct Args {
    /// Path to the digested fixture input document (JSON).
    #[arg(long)]
    input: PathBuf,

    /// Path to write the post-state output document (JSON).
    #[arg(long)]
    output: PathBuf,

    /// Directory for the consensus ledger (headers/bodies).
    #[arg(long)]
    ledger_dir: PathBuf,

    /// Path to the triedb storage (file or block device).
    #[arg(long)]
    db: PathBuf,
}

#[derive(Deserialize)]
struct InputBlock {
    timestamp: u64,
    base_fee: u64,
    beneficiary: Address,
    txs: Vec<String>,
}

#[derive(Deserialize)]
struct InputRevision {
    revision: u8,
    from_timestamp: u64,
}

#[derive(Deserialize)]
struct Input {
    genesis_alloc: serde_json::Value,
    genesis_rlp: String,
    revision_schedule: Vec<InputRevision>,
    blocks: Vec<InputBlock>,
}

fn eest_net_chain_config() -> MonadChainConfig {
    serde_json::from_value(json!({
        "chain_id": EEST_NET_CHAIN_ID,
        "epoch_length": 50_000,
        "epoch_start_delay": 5_000,
        "v_0_7_0_activation": 0,
        "v_0_8_0_activation": 0,
        "v_0_10_0_activation": 0,
        "v_0_11_0_activation": 0,
        // Pin the consensus revision at V_0_11_0: its proposal_gas_limit
        // (200M) is the block gas limit `MonadRunloopDefaults.gas_limit`
        // stamps into filled headers. V_0_12_0 lowers it to 150M, which
        // would change every block hash and break the EIP-2935 history a
        // later block reads back. Moving to V_0_12_0 means updating that
        // default and re-filling.
        "v_0_12_0_activation": u64::MAX,
        "staking_config": {
            "staking_activation": u64::MAX,
            // BlockRewardActivation is an externally tagged enum, so the
            // never-activate value is a variant, not a bare integer.
            "block_reward_v_one": {
                "block_reward_activation": { "Epoch": u64::MAX },
                "block_reward_mon": 0,
            },
            "block_reward_v_two": {
                "block_reward_activation": { "Epoch": u64::MAX },
                "block_reward_mon": 0,
            },
        },
        "execution_v_one_activation": 0,
        "execution_v_two_activation": 0,
        "execution_v_four_activation": 0,
    }))
    .expect("valid eest-net chain config")
}

fn decode_tx(raw: &str) -> TxEnvelope {
    let bytes = hex_decode(raw);
    TxEnvelope::decode_2718(&mut bytes.as_slice()).expect("valid signed tx")
}

fn hex_decode(raw: &str) -> Vec<u8> {
    let raw = raw.strip_prefix("0x").unwrap_or(raw);
    (0..raw.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&raw[i..i + 2], 16).expect("valid hex"))
        .collect()
}

// Convert the triedb dump (accounts keyed by hashed trie path, storage
// keyed by hashed slot with plaintext slot/value entries) into an
// address-keyed post-state document.
fn normalize_dump(dump: &str) -> serde_json::Value {
    let dump: serde_json::Value =
        serde_json::from_str(dump).expect("valid dump json");
    let mut post_state = BTreeMap::new();
    for (_, account) in dump.as_object().expect("dump is an object") {
        let address = account["address"]
            .as_str()
            .expect("account address")
            .to_lowercase();
        let balance_dec =
            account["balance"].as_str().expect("account balance");
        let balance =
            U256::from_str_radix(balance_dec, 10).expect("decimal balance");
        let mut storage = BTreeMap::new();
        if let Some(slots) = account["storage"].as_object() {
            for (_, entry) in slots {
                let slot =
                    entry["slot"].as_str().expect("slot key").to_lowercase();
                let value = entry["value"]
                    .as_str()
                    .expect("slot value")
                    .to_lowercase();
                storage.insert(slot, value);
            }
        }
        post_state.insert(
            address,
            json!({
                "balance": format!("0x{:x}", balance),
                "nonce": account["nonce"],
                "code": account["code"],
                "storage": storage,
            }),
        );
    }
    serde_json::to_value(post_state).expect("valid post state")
}

fn main() {
    let args = Args::parse();

    let input: Input = serde_json::from_str(
        &std::fs::read_to_string(&args.input).expect("readable input"),
    )
    .expect("valid input document");

    let genesis_alloc = serde_json::to_string(&input.genesis_alloc)
        .expect("valid genesis alloc");

    let genesis_rlp_hex =
        input.genesis_rlp.strip_prefix("0x").unwrap_or(&input.genesis_rlp);
    let revision_schedule = input
        .revision_schedule
        .iter()
        .map(|r| format!("{}:{}", r.revision, r.from_timestamp))
        .collect::<Vec<_>>()
        .join(",");
    let mut runloop = MonadRunloop::new_eest(
        args.ledger_dir.clone(),
        args.db.clone(),
        &genesis_alloc,
        genesis_rlp_hex,
        &revision_schedule,
    );

    let chain_config = eest_net_chain_config();
    let proposer_private_key = [1u8; 32];
    let mut machine = Ledger::new(
        chain_config,
        args.ledger_dir.clone(),
        proposer_private_key,
    );

    // Drive the runloop one block at a time, finalizing each block before
    // the next executes. A single run(n_blocks) over a fully-written ledger
    // would instead replay everything with nothing finalized until the end,
    // leaving a deep unfinalized proposal chain: the runloop's proposal walk
    // then truncates (DEPTH_LIMIT / unresolved genesis) and marks reads
    // uncacheable, defeating the read-through page cache. Executing each
    // block against an already-finalized parent keeps the walk shallow so
    // the commit-time RMW read hits the cache.
    let n_blocks = input.blocks.len() as u64;
    for block in &input.blocks {
        machine.set_timestamp(block.timestamp as u128 * 1_000_000_000);
        let txs: Vec<TxEnvelope> =
            block.txs.iter().map(|tx| decode_tx(tx)).collect();
        machine.propose(
            txs,
            block.base_fee,
            /* base_fee_trend: */ 0,
            /* base_fee_moment: */ 0,
            block.beneficiary,
        );
        machine.finalize();
        runloop.run(1);
    }

    // The canonical state root is the page-encoded secondary timeline's
    // from MONAD_NEXT (MIP-8, revision 10) on, the slot-encoded
    // primary's before. The schedule is ascending, so the last entry is
    // the revision the run ends in.
    let final_revision = input
        .revision_schedule
        .last()
        .expect("non-empty revision schedule")
        .revision;
    let state_root = if final_revision >= 10 {
        runloop.get_secondary_state_root()
    } else {
        runloop.get_primary_state_root()
    };
    let post_state = normalize_dump(&runloop.dump_json());

    let output = json!({
        "state_root": format!("0x{:064x}", state_root),
        "post_state": post_state,
    });
    std::fs::write(
        &args.output,
        serde_json::to_string_pretty(&output).expect("valid output"),
    )
    .expect("writable output");

    println!("eest-runner: executed {} block(s)", n_blocks);
}
