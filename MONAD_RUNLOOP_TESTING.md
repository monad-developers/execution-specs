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
