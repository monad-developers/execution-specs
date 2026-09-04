# Running EEST fixtures on the monad runloop

Executes blockchain fixtures (including those generated from state
tests) against the monad execution client's `runloop`, using the
consensus ledger directory as the block source. Each `postState`
account is compared (balance/nonce/code/storage) against the
executed result.

## Repos

| Repo / branch | Role |
|---|---|
| this repo | the `eest-runner` harness under `monad-runloop/`, plus the `MonadFixtureConsumer` (`packages/testing/.../client_clis/clis/monad.py`) wired into `consume direct` |
| `monad-bft` @ `eestnet/master` (submodule `monad-runloop/monad-bft`) | consensus block types + ledger writer; pins monad-execution below |
| `monad` @ `eestnet/main` (submodule of monad-bft) | execution client with the `EestNet` chain (id 30143, per-fixture revision schedule, runtime genesis) and the extended `monad_runloop_*` FFI |

Both submodule branches are upstream (`category-labs/monad-bft@master`,
`category-labs/monad@main`) plus, on monad only, the one commit that adds
the EEST entry points.

## One-time setup

Requirements: docker, ~10 GB disk for the builder image and build
artifacts, ~6 GB RAM for hugepages.

```sh
./monad-runloop/init-stack.sh   # monad-bft + monad-execution, ~300 MB

# Toolchain image (gcc-15 + rust), from the vendored Dockerfile:
docker build -t monad-builder:latest \
    - < monad-runloop/docker/builder/Dockerfile

./monad-runloop/build.sh   # builds + syncs binaries/libs into install/
monad-runloop/bin/eest-runner --version

uv sync
```

Always rebuild with `./monad-runloop/build.sh`; it syncs
`libmonad_execution.so` alongside the binary (copying only the binary
leaves a stale library that fails silently).

## Fill + consume

```sh
uv run fill --clean -m blockchain_test <test paths...> \
    --fork MONAD_NINE --chain-id 30143 --monad-runloop \
    --output ../fixtures_eestnet

uv run consume direct --input ../fixtures_eestnet \
    --bin monad-runloop/bin/eest-runner
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

## Fork-transition flow (MIP-8 dual-db)

MONAD_NINE storage is slot-encoded; MONAD_TEN (MIP-8) is
page-encoded. A transition fixture (e.g.
`MONAD_NINEToMONAD_TENAtTime15k`) crosses that boundary mid-run, so
the run keeps both encodings live in one triedb: a slot-encoded
primary timeline and a page-encoded secondary timeline.

- **Consumer** (`clis/monad.py`): loads the fixture, maps its
  `network` to a `(revision, from_timestamp)` schedule, skips
  `expectException` blocks, and provisions the db. Revisions spanning
  the fork activate the secondary timeline via `monad-mpt
  --activate-secondary`.
- **Harness** (`eest-runner`): fakes consensus with a Ledger
  simulator (`propose` then `finalize` per block) that writes BFT
  headers/bodies to `--ledger-dir` — the same artifacts monad-bft
  persists on a real network — then drives the runloop over the FFI
  and writes `output.json`.
- **Execution** (`runloop_monad`): reads each block from the ledger
  dir and dispatches on `get_monad_revision(timestamp)` via
  `SWITCH_MONAD_TRAITS` — block 1 runs MONAD_NINE, block 2 MONAD_TEN.
- **Dual-write** (`commit_block`): both timelines get the same
  `StateDeltas` every block. Slot is canonical before the fork, page
  after; the canonical root flips to page at the first MONAD_TEN
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
flips from slot to page at the first MONAD_TEN block.
