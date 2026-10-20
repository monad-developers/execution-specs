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
    --fork MONAD_TEN --chain-id 30143 --monad-runloop \
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

## Storage perf-regression tests

`tests/benchmark/stateful/mip8_pageified_storage/test_perf_regression.py`
fills SLOAD/SSTORE workloads at both forks and times block execution on
the runloop to compare MONAD_TEN vs MONAD_ELEVEN, both on page-encoded
(MIP-8) storage.

### Setup

```sh
sudo tee /etc/sysctl.d/99-benchmark.conf >/dev/null <<'EOF'
kernel.randomize_va_space = 0
kernel.perf_event_paranoid = 1
vm.nr_hugepages = 3072
EOF
sudo sysctl --system
sudo cpupower idle-set -D 1
```

### Run

From the repo root:

```sh
tmux new -s perf 'TAG=v4 scripts/perf_cycle.sh'
```

Fills once, consumes `RUNS` times, and writes the TEN-vs-ELEVEN table to
`../timing_${TAG}_<utc>_table.md` (headed with the
cycle time and the four repo SHAs). Knobs:

- `TAG` (required) names every artifact; use a fresh one per experiment.
- `RUNS` consume passes (default 13), `REPEATS` page-disjoint copies per
  fixture (cold samples reduced to a `min` within each pass).
- `BLOCK_GAS_M=N` sets the block gas budget in millions, passed through
  as `--gas-benchmark-values`; the default 200 matches the gas the
  runloop stamps. Fixtures land under `for_{fork}_at_0200M/`, so the
  budget a fixture was built with is visible in its path.
  `SKIP_FILL=1` reuses an existing `../fixtures_${TAG}`.

Run on a quiet host; timings are noisy under contention.

## Behavior and known limits

- Fixtures containing expected-invalid blocks are skipped: the ledger
  machine validates blocks at proposal time and cannot write invalid
  ones.
- Fixtures with blocks before MONAD_TEN fail before the runloop starts,
  transitions into MONAD_TEN included: monad executes only on a
  page-encoded (MIP-8) triedb, and pre-MIP-8 blocks need the
  slot-encoded one.
- The runloop runs in a privileged container (io_uring) and needs
  `vm.nr_hugepages >= 2048` on the host; the `bin/` wrappers
  re-provision this automatically (it resets on host reboot).

## Execution flow

- **Consumer** (`clis/monad.py`): loads the fixture, maps its
  `network` to a `(revision, from_timestamp)` schedule, skips
  `expectException` blocks, and provisions the db with `monad-mpt
  --create --state-machine monad`.
- **Harness** (`eest-runner`): fakes consensus with a Ledger
  simulator (`propose` then `finalize` per block) that writes BFT
  headers/bodies to `--ledger-dir` — the same artifacts monad-bft
  persists on a real network — then drives the runloop over the FFI
  and writes `output.json`.
- **Execution** (`runloop_monad`): reads each block from the ledger
  dir and dispatches on `get_monad_revision(timestamp)` via
  `SWITCH_MONAD_TRAITS`, e.g. MONAD_TEN then MONAD_ELEVEN in a
  `MONAD_TENToMONAD_ELEVENAtTime15k` fixture.
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
| Commit | `commit_block<traits>` | the same |
| State root / db | page-encoded `mpt::Db` + `TrieDb` | the same |

eest-only shims: the C ABI in `runloop_interface_monad.cpp`, the `EestNet`
chain (runtime schedule), `MonadRunloopDbCache` / `set_balance` (balance
overrides, unused here), genesis loading from the fixture, and the Rust
Ledger simulator that fabricates the BFT blocks consensus would deliver.
