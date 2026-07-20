# Running EEST fixtures on the monad runloop

Executes blockchain fixtures (including those generated from state
tests) against the monad execution client's `runloop`, using the
consensus ledger directory as the block source. Each `postState`
account is compared (balance/nonce/code/storage) against the
executed result.

## Repos

| Repo / branch | Role |
|---|---|
| `monad-exp/monad-eest-rust-harness` @ `execute-with-eestnet-new-secondary` | `eest-runner` harness: builds consensus blocks from a fixture and runs them on the runloop |
| `monad-bft` @ `execute-with-eestnet-new-secondary` (submodule of the above) | consensus block types + ledger writer; pins monad-execution below |
| `monad` @ `execute-with-eestnet-new-secondary` (submodule of monad-bft) | execution client with the `EestNet` chain (id 30143, per-fixture revision schedule, runtime genesis) added on top of upstream's runloop C library |
| this repo | `MonadFixtureConsumer` (`packages/testing/.../client_clis/clis/monad.py`) wired into `consume direct` |

## One-time setup

Requirements: docker, ~10 GB disk for the builder image and build
artifacts, ~6 GB RAM for hugepages.

```sh
git clone --branch execute-with-eestnet-new-secondary \
    git@github.com:monad-exp/monad-eest-rust-harness.git
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
  requests_hash).
- `--chain-id 30143` is EestNet's chain id.
- Block timestamps need no special handling: the consumer derives the
  monad revision schedule from the fixture's `network`
  (`FORK_REVISION_SCHEDULES` in `clis/monad.py`) and the harness
  injects it into the chain.
- `consume` parallelism is CPU-bound: one runloop peaks near 4 cores (~386% CPU
  across ~14 threads), so budget ~5 vCPUs per worker (`-n N` needs
  roughly `5 * N` cores).

## Behavior and known limits

- Fixtures containing expected-invalid blocks are skipped: the ledger
  machine validates blocks at proposal time and cannot write invalid
  ones.
- The runloop runs in a privileged container (io_uring) and needs
  `vm.nr_hugepages >= 2048` on the host; the `bin/` wrappers
  re-provision this automatically (it resets on host reboot).

## Dual-db flow (MIP-8)

MONAD_NINE storage is slot-encoded; MONAD_NEXT (MIP-8) is
page-encoded. Every run keeps both encodings live in one triedb: a
slot-encoded primary timeline and a page-encoded secondary timeline,
so a transition fixture (e.g. `MONAD_NINEToMONAD_NEXTAtTime15k`) can
cross the boundary mid-run.

- **Consumer** (`clis/monad.py`): loads the fixture, maps its
  `network` to a `(revision, from_timestamp)` schedule, skips
  `expectException` blocks, and provisions a slot-encoded db; the
  runloop C library opens or activates the page-encoded secondary
  timeline itself at startup.
- **Harness** (`eest-runner`): fakes consensus with a Ledger
  simulator (`propose` then `finalize` per block) that writes BFT
  headers/bodies to `--ledger-dir` — the same artifacts monad-bft
  persists on a real network — then drives the runloop over the FFI
  and writes `output.json`.
- **Execution** (`runloop_monad`): reads each block from the ledger
  dir and dispatches on `get_monad_revision(timestamp)` via
  `SWITCH_MONAD_TRAITS` — block 1 runs MONAD_NINE, block 2 MONAD_NEXT.
- **Dual-write** (`commit_block`): both timelines get the same
  `StateDeltas` every block. Slot is canonical before the fork, page
  after; the canonical root flips to page at the first MONAD_NEXT
  block, matching the fixture's final `blockHeader.stateRoot`.
- **Assertions**: `state_root` and every `postState` account
  (balance, nonce, code, per-slot storage) match `output`, with no
  unexpected non-zero slots.

### Correspondence to production

eest-runner runs the same execution core as a production node, with
two differences: the block source and the revision schedule.

| Concern | eest-runner | Production node |
|---|---|---|
| Block source | Ledger sim writes headers/bodies to `--ledger-dir` | `monad-bft` `MonadBlockFileLedger` persists consensus-committed blocks to the ledger dir |
| Revision schedule | runtime, from fixture (`EestNet::get_monad_revision`) | compiled-in timestamps (`MonadMainnet::get_monad_revision`) |
| Execution loop | `runloop_monad` | the same `runloop_monad` |
| Per-block dispatch | `SWITCH_MONAD_TRAITS` on `get_monad_revision(timestamp)` | the same |
| Block execution | `execute_block<traits>` | the same |
| Commit | `commit_block<traits>` dual-write to both timelines | the same function; dual-write engages whenever a secondary timeline is active |
| Db shape | slot primary + page secondary, every run (the runloop C library activates the secondary at open) | slot primary; page secondary activated by the operator (`monad-mpt --activate-secondary`) for the MIP-8 migration |
| Read cross-check | every storage read asserted equal on both timelines (`BlockState`) | the same, whenever the secondary is active |

Every run models a node inside the MIP-8 migration window; the
fixture's fork selects the phase:

| Fixture fork | Traits | Canonical root | Production state modeled |
|---|---|---|---|
| MONAD_NINE | NINE throughout | primary (slot) | armed pre-fork: secondary activated, fork not reached |
| MONAD_NINE→MONAD_NEXT | flip at the fork block | flips to secondary (page) at the fork | the fork crossing |
| MONAD_NEXT | NEXT throughout | secondary (page) | post-fork, pre-promotion: slot primary retained, page canonical |

Not modeled: the slot-only steady state before activation (MONAD_NINE
semantics and roots are identical there; the run only adds the shadow
dual-write) and the page-only steady state after promotion, where the
page timeline serves execution reads directly. The per-read
cross-check asserts slot- and page-served results match, so the page
read path is exercised as the checker rather than as the source.

eest-only shims: the `EestNet` chain (runtime schedule, genesis from
the fixture), the `monad_runloop_new_eest` and `monad_runloop_dump_json`
FFI additions, and the Rust Ledger simulator that fabricates the BFT
blocks consensus would deliver. The runloop C library itself
(`runloop_interface_monad.cpp`, including the balance overrides used
by the VM fuzzer) is upstream `monad` code.
