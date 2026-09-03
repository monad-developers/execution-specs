# monad runloop harness

Executes EEST (execution-spec-tests) blockchain fixtures against the
monad runloop, feeding blocks through the consensus ledger directory.
Each fixture's `postState` is compared against the executed result by
the `MonadFixtureConsumer` in this repo via `consume direct`. See
`MONAD_RUNLOOP_TESTING.md` at the repo root for the full fill + consume
pipeline.

The sources under `rust-harness/ledger/` and `rust-harness/runloop/`
carry Category Labs GPL-3 headers, so this subtree keeps its own
`LICENSE` rather than the repo's CC0.

## Layout

- `rust-harness/` — cargo workspace:
  - `ledger/` — builds, signs, and writes consensus blocks (ported
    from `monad-execution-fuzzer`, OCaml dependency dropped),
  - `runloop/` — Rust bindings for the `monad_runloop_*` C++ FFI,
  - `eest-runner/` — the harness binary: installs the fixture `pre`
    as genesis, proposes/finalizes the fixture blocks, runs them on
    the monad runloop (EestNet chain, id 30143), emits the
    post-state as JSON.
- `monad-bft` — submodule; its own `monad-execution` submodule carries
  the `EestNet` chain and the extended runloop FFI.
- `docker/builder/Dockerfile` — vendored byte-identical from
  monad-bft, so the toolchain image builds with no network fetch and
  the CI cache key can hash it directly.
- `build.sh` — builds everything in the `monad-builder` container and
  syncs binaries + shared libraries into `install/`. Always rebuild
  with this script; copying only the binary leaves a stale
  `libmonad_execution.so` that fails silently.
- `bin/eest-runner`, `bin/monad-mpt` — host wrappers that run the
  installed binaries inside the container (privileged: io_uring +
  hugepages) and auto-provision `vm.nr_hugepages`.

## Flow

```
┌───────────────────────────────────────────────────────────────────────────────┐
│  execution-specs · MonadFixtureConsumer       (Python, `consume direct`)      │
│  digest fixture ─▶ input.json              reads output.json, compares each   │
│                                            postState account (bal/nonce/…)    │
└───────────────┬──────────────────────────────────────────▲────────────────────┘
                │ spawn eest-runner                        │ output.json
                │ --input --output --ledger-dir --db       │
                ▼                                          │
┌──────────────────────────────────────────────────────────┴────────────────────┐
│  crate: eest-runner   (bin · main.rs)  ── orchestrator                        │
│    parse Input ─▶ build engine ─▶ per-block loop ─▶ read back ─▶ output       │
└────┬───────────────────────────────────────────────┬──────────────────────────┘
     │ propose(txs) / finalize()                     │ new_eest / run(1) /
     │                                               │ get_*_state_root / dump_json
     ▼                                               ▼
┌──────────────────────────┐               ┌────────────────────────────────────┐
│ crate: ledger            │               │ crate: runloop                     │
│   Ledger                 │               │   MonadRunloop  (wraps raw ptr)    │
│   fake consensus:        │               │   extern "C"  monad_runloop_*      │
│   build + validate the   │               └────────────────┬───────────────────┘
│   consensus block        │                                │ FFI (dylib link)
└───────────┬──────────────┘                                ▼
            │ write                         ┌───────────────────────────────────┐
            │ headers/bodies                │ libmonad_execution.so   (C++)     │
            ▼                               │   runloop_monad:                  │
      ┌─────────────┐     read each block   │     read block ─ execute<traits>  │
      │ ledger dir  │ ────────────────────▶ │     ─ commit_block                │
      └─────────────┘                       └────────────────┬──────────────────┘
                                                             │ commit state
                                                             ▼
                                                      ┌─────────────┐
                                                      │ triedb (db) │
                                                      └─────────────┘
```

Per-block sequence (what main's loop does, block by block):

```
for block N:
  eest-runner ─ ledger.propose(txs, base_fee, beneficiary) ─▶ ledger dir      [Proposed]
  eest-runner ─ ledger.finalize() ──────────────────────────▶ ledger dir      [Finalized]
  eest-runner ─ runloop.run(1) ─▶ C++ engine ─ reads ledger dir ─ executes ─▶ triedb

after the last block:
  eest-runner ─ runloop.get_primary_state_root() or
                get_secondary_state_root() (page canonical from MONAD_TEN)
              + dump_json()
              ─▶ normalize_dump() ─▶ output.json ─▶ (Python compares vs fixture)
```

## Build

From the repo root:

```sh
git submodule update --init --recursive monad-runloop/monad-bft
docker build -t monad-builder:latest - < monad-runloop/docker/builder/Dockerfile
./monad-runloop/build.sh
monad-runloop/bin/eest-runner --version
```

The `--recursive` is required here: `monad-bft/monad-execution` holds
the execution client and its own third-party submodules. Cloning the
superproject without `--recurse-submodules` leaves all of this empty,
which costs nothing for contributors who never run the harness.

Requirements: docker, ~10 GB disk for the builder image and build
artifacts, ~6 GB RAM for hugepages.
