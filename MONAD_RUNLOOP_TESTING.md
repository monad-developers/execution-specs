# Running EEST fixtures on the monad runloop

Executes blockchain fixtures (including those generated from state
tests) against the production monad execution client, via its
`runloop` and the consensus ledger directory. The oracle is the
fixture's `postState`, compared account by account
(balance/nonce/code/storage).

## Repos

| Repo / branch | Role |
|---|---|
| `monad-exp/monad-eest-rust-harness` @ `execute-with-eestnet` | `eest-runner` harness: builds consensus blocks from a digested fixture and runs them on the runloop |
| `monad-bft` @ `execute-with-eestnet` (submodule of the above) | consensus block types + ledger writer; pins monad-execution below |
| `monad` @ `execute-with-eestnet` (submodule of monad-bft) | execution client with the `EestNet` chain (id 30143, per-fixture revision schedule, runtime genesis) and the extended `monad_runloop_*` FFI |
| this repo @ `execute-with-eestnet` | `MonadFixtureConsumer` (`packages/testing/.../client_clis/clis/monad.py`) wired into `consume direct` |

## One-time setup

Requirements: docker, ~10 GB disk for the builder image and build
artifacts, ~6 GB RAM for hugepages.

```sh
git clone --branch execute-with-eestnet \
    https://github.com/monad-exp/monad-eest-rust-harness.git
cd monad-eest-rust-harness
git submodule update --init --recursive

# Toolchain image (gcc-15 + rust), from monad-bft's Dockerfile:
curl -fsSL https://raw.githubusercontent.com/category-labs/monad-bft/master/docker/builder/Dockerfile \
    | docker build -t monad-builder:latest -

./build.sh             # builds + syncs binaries/libs into install/
bin/eest-runner --version
```

In this repo:

```sh
uv sync
```

Always rebuild with `./build.sh`; it syncs `libmonad_execution.so`
alongside the binary (copying only the binary leaves a stale library
that fails silently).

## Fill + consume

```sh
uv run fill --clean -m blockchain_test <test paths...> \
    --fork MONAD_NINE --chain-id 30143 --monad-runloop \
    --output ../fixtures_eestnet

uv run consume direct --input ../fixtures_eestnet \
    --bin ../monad-eest-rust-harness/bin/eest-runner
```

- `--monad-runloop` stamps monad blocks with the consensus-derived
  header fields the runloop produces (prev_randao from the round-0 BLS
  signature, 32-byte extra_data, the proposal gas limit, zero
  requests_hash) so filled block hashes and EIP-2935 history storage
  match the runloop; without it those slots diverge and fail the
  post-state compare.
- `--chain-id 30143` is EestNet's chain id; transactions must be
  signed for it, so it is required at fill time.
- Block timestamps need no special handling: the consumer derives the
  monad revision schedule from the fixture's `network`
  (`FORK_REVISION_SCHEDULES` in `clis/monad.py`) and the harness
  injects it into the chain.
- `consume` parallelism is CPU-bound: one runloop peaks near 4 cores (~386% CPU
  across ~14 threads), so budget ~5 vCPUs per worker (`-n N` needs
  roughly `5 * N` cores). On a small host (e.g. 4 vCPUs) run
  sequentially — `-n` above 1 oversubscribes and runs slower.

## Behavior and known limits

- Fixtures containing expected-invalid blocks are skipped: the ledger
  machine validates blocks at proposal time and cannot write invalid
  ones.
- The runloop runs in a privileged container (io_uring) and needs
  `vm.nr_hugepages >= 2048` on the host; the `bin/` wrappers
  re-provision this automatically (it resets on host reboot).

## Fork-transition flow (MIP-8 dual-db)

End-to-end trace of one `MONAD_NINEToMONAD_NEXTAtTime15k` blockchain-test
fixture, from JSON input through the consumer, the `eest-runner` harness,
the C++ FFI, the dual-db, and back to the consumer's assertions. The
execution core (everything in `monad-bft/monad-execution`) is the same
code a production node runs when consuming consensus blocks; only the
block source and the revision schedule differ. MONAD_NINE storage is
slot-encoded; MONAD_NEXT (MIP-8) is page-encoded, so a transition fixture
crosses an encoding boundary mid-run.

### The fixture

Declared in `tests/monad_ten/mip8_pageified_storage/test_fork_transition.py`
with `@pytest.mark.valid_at_transition_to("MONAD_NEXT")`; the transition
fork is `@transition_fork(to_fork=MONAD_NEXT, from_fork=MONAD_NINE,
at_timestamp=15_000)` (`forks/forks/transition.py`). The filler emits two
blocks straddling the boundary: block 1 at `timestamp=14_999` (executes
under MONAD_NINE, slot-encoded) and block 2 at `timestamp=15_000`
(MONAD_NEXT, page-encoded). The fixture carries
`"network": "MONAD_NINEToMONAD_NEXTAtTime15k"`, `genesisRLP`, `pre`,
`blocks[]`, and `postState`. The final block's `blockHeader.stateRoot` is
the page-encoded root.

### Consumer: fixture to harness input

`MonadFixtureConsumer.consume_fixture()` in `clis/monad.py` loads the
fixture, asserts the network is in `FORK_REVISION_SCHEDULES`, and skips
`expectException` blocks (the ledger cannot propose invalid blocks). The
schedule maps EEST fork names to `(revision, from_timestamp)` tuples:

```python
"MONAD_NINE":                       [(9, 0)],
"MONAD_NEXT":                       [(10, 0)],
"MONAD_NINEToMONAD_NEXTAtTime15k":  [(9, 0), (10, 15_000)],
```

It builds the harness input doc (`genesis_alloc`, `genesis_rlp`,
`revision_schedule`, digested `blocks`).

### Dual-db provisioning (`_init_triedb`)

The slot/page decision is made before the harness runs:

```python
uses_page = any(rev >= 10 for rev in revisions)   # MONAD_NEXT+
uses_slot = any(rev < 10 for rev in revisions)     # pre-MIP-8
primary = "monad" if uses_page and not uses_slot else "ethereum"
```

- Pure MONAD_NINE  -> `ethereum` (slot) primary, single timeline.
- Pure MONAD_NEXT  -> `monad` (page) primary, single timeline.
- Transition       -> `ethereum` primary **plus** a second invocation:

```bash
monad-mpt --storage <db> --create --chunk-capacity 26 \
    --root-offsets-chunk-count 2 --state-machine ethereum
monad-mpt --storage <db> --activate-secondary --state-machine monad
```

`--activate-secondary` stamps the secondary timeline's
`state_machine_kind = monad`, shrinks the primary chunk ring, and hands
chunks to the secondary. Result on disk: one triedb file with a
slot-encoded **primary** timeline and an empty page-encoded **secondary**
timeline (`timeline_id` is `{primary=0, secondary=1}`).

### Harness: simulate consensus, then execute

`_run_harness` launches `eest-runner` (`--input/--output/--ledger-dir/
--db`) with `PR_SET_PDEATHSIG` and a named container for cleanup. The
Rust harness (`rust-harness/eest-runner/src/main.rs`):

1. Parses the input JSON.
2. Mimics consensus with a Ledger simulator (`propose` then `finalize`
   per block), writing BFT headers/bodies into `--ledger-dir` — the same
   artifacts monad-bft persists on a real network.
3. Builds the runloop over the FFI (`monad_runloop_new_eest` parses the
   schedule into an `EestNet` chain).
4. Calls `runloop.run(n_blocks)`, then reads the root and post-state
   back, writing `output.json`.

The `monad-cxx` crate links dynamically against `libmonad_execution.so`.

### FFI: opening the dual-db

`MonadRunloopImpl` ctor in
`category/execution/runloop/runloop_interface_monad.cpp` registers the
state machines (`register_ethereum_state_machines` -> `OnDiskMachine`
slot; `register_monad_state_machines` -> `MonadOnDiskMachine` page) before
constructing `mpt::Db`, since the ctor rebuilds each timeline's machine
from its persisted kind. Then:

```cpp
if (raw_db.timeline_active(mpt::timeline_id::secondary)) {
    secondary_raw_db = raw_db.open_secondary_timeline();
    secondary_triedb.emplace(*secondary_raw_db);
    MONAD_ASSERT(secondary_triedb->is_page_encoded(), ...);
}
```

Genesis is loaded into both timelines. For pure fixtures the secondary
stays empty and the paths below collapse to a single timeline.

### Execution loop (`runloop_monad`)

`monad_runloop_run` calls `runloop_monad(... secondary_db, is_first_run)`,
passing the secondary as a raw pointer. `runloop_monad` reads each block
from the ledger directory (the same files the harness, or production
consensus, wrote) and dispatches per block:

```cpp
auto const rev = chain.get_monad_revision(header.timestamp);  // 9 or 10
SWITCH_MONAD_TRAITS(propose_block, ..., db, ..., secondary_db);
```

`SWITCH_MONAD_TRAITS` turns the runtime revision into a compile-time
`MonadTraits<...>`. Block 1 dispatches to MONAD_NINE, block 2 to
MONAD_NEXT — the fork boundary is purely `get_monad_revision(timestamp)`.
The secondary db is threaded through every state-mutating call
(`set_block_and_prefix`, `update_voted_metadata`, `finalize`,
`BlockState(db, vm, secondary_db)`).

### Per-block dual-write (`commit_block`)

`category/execution/monad/db/commit_block_migration.cpp`. Single timeline
(`secondary_db == nullptr`): `PageCommitBuilder` at MONAD_NEXT+, else
`CommitBuilder`. Dual timeline (the transition path): both timelines get
the same `StateDeltas` every block, only the encoding differs, and the
canonical db commits first so its live roots feed the other's
`populate_header`:

```cpp
CommitBuilder     builder (header.number);                 // slot  -> primary
PageCommitBuilder builder2(header.number, *secondary_db);  // page  -> secondary
if constexpr (traits::monad_rev() >= MONAD_NEXT) {  // post-fork: page canonical
    correct_db = secondary_db;
    secondary_db->commit(...);  primary_db.commit(...);
} else {                                            // pre-fork: slot canonical
    correct_db = &primary_db;
    primary_db.commit(...);     secondary_db->commit(...);
}
```

The flip at `>= MONAD_NEXT` is the entire transition: pre-fork the slot
timeline is authoritative, post-fork the page timeline is. Each
`Db::commit` is two-stage (upsert deltas, populate header roots from
stage-1 state, upsert header + write root).

Encoding difference: slot stores trie path `keccak(addr)|keccak(slot)`
-> raw 32-byte value. Page groups slots by `page_key = slot >> 7` (128
slots/page), reads the whole page on first touch, merges deltas, and
stores one entry per page keyed `keccak(addr)|keccak(page_key)`; the page
hash is MIP-8's Induced-Subtree Merkle Commitment (BLAKE3 over a sparse
128-slot bitmap). A single static-encoding db cannot span the transition
because block 1 must emit slot entries and block 2 page entries against a
consistent account trie; the dual-db runs both encodings in lockstep so
each side stays internally consistent across the boundary.

### Final root and assertions

`monad_runloop_get_state_root` returns the secondary (page) root in
dual-db mode, else the single timeline's root. A transition fixture ends
in block 2 (MONAD_NEXT), so the canonical root is the page timeline's —
matching the fixture's final `blockHeader.stateRoot`. The consumer then
asserts (`clis/monad.py`):

- `output["state_root"]` == `fixture["blocks"][-1]["blockHeader"]["stateRoot"]`.
- Every `postState` account's balance, nonce, code, and per-slot storage
  matches `output["post_state"]`, with no unexpected non-zero slots.

### Correspondence to production

The execution core is identical; only the block source and schedule
differ.

| Concern | eest-runner | Production node |
|---|---|---|
| Block source | Ledger sim writes headers/bodies to `--ledger-dir` | `monad-bft` `MonadBlockFileLedger` persists consensus-committed blocks to the ledger dir |
| Revision schedule | runtime, from fixture (`EestNet::get_monad_revision`) | compiled-in timestamps (`MonadMainnet::get_monad_revision`) |
| Execution loop | `runloop_monad` | the same `runloop_monad` |
| Per-block dispatch | `SWITCH_MONAD_TRAITS` on `get_monad_revision(timestamp)` | the same |
| Block execution | `execute_block<traits>` | the same |
| Commit | `commit_block<traits>` with dual-write | the same function; dual-write engages whenever a secondary timeline is active |
| State root / db | `mpt::Db` + `TrieDb`, optional secondary | the same |

eest-only shims: the C ABI in `runloop_interface_monad.cpp`, the `EestNet`
chain (runtime schedule), `MonadRunloopDbCache` / `set_balance` (balance
overrides, unused here), genesis loading from the fixture, and the Rust
Ledger simulator that fabricates the BFT blocks consensus would deliver.
The production fork migration uses the same dual-db mechanism: an operator
runs `monad-mpt --activate-secondary --state-machine monad` on the live
slot-encoded db before the fork, the node opens the secondary timeline and
dual-writes both encodings across the boundary, and the authoritative root
flips from slot to page at the first MONAD_NEXT block — exactly what these
transition fixtures verify.
