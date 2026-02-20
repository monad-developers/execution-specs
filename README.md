# Ethereum Execution Client Specifications

[![GitPOAP Badge](https://public-api.gitpoap.io/v1/repo/ethereum/execution-specs/badge)](https://www.gitpoap.io/gh/ethereum/execution-specs)
[![codecov](https://codecov.io/gh/ethereum/execution-specs/graph/badge.svg?token=0LQZO56RTM)](https://codecov.io/gh/ethereum/execution-specs)
[![Python Specification](https://github.com/ethereum/execution-specs/actions/workflows/test.yaml/badge.svg)](https://github.com/ethereum/execution-specs/actions/workflows/test.yaml)

## Monadized `execution-specs` and spec tests

**IMPORTANT: Read this first if you're planning to use it for [Monad](https://www.monad.xyz/).**

---

This fork implements the execution layer client features specific to Monad.

It mainly serves as an alternative implementation of https://github.com/category-labs/monad, and a tool to generate (fill) spec tests for it to consume.

### Getting started

1. Install `uv`: `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. Clone and setup:

```bash
git clone --depth 1 https://github.com/monad-developers/execution-specs
cd execution-specs
uv python install 3.12
uv python pin 3.12
uv sync --all-extras
```
3. Fill all monadized spec tests as of writing this (see below for explanation of flags):

```bash
uv run fill --clean -m "blockchain_test or transaction_test" --from MONAD_EIGHT --until MONAD_NEXT --chain-id 143 -n auto tests
```

4. The test fixtures to be found in `fixtures/` directory under repo root.

(c.f. [somewhat outdated upstream "Getting started"](https://eest.ethereum.org/main/getting_started/installation/))

### Filling tests

Filling tests is the process of running Python generators of spec tests [like ones you can find here](./tests/monad_eight/reserve_balance/test_transfers.py), to generate JSON files with test fixtures. The fixtures are then in turn consumed by `monad`. In the process of filling the tests, the Python implementation of Monad execution layer is used to produce reference results including the state root.

#### `uv run fill` flags

- **`-m blockchain_test or transaction_test`**: causes these two flavors of fixtures to be generated
  - `blockchain_test` is the currently supported by `monad` flavor of a spec test checking correctness of the state transition. Note this includes `blockchain_test_from_state_test`, meaning that all `state_test(...)` fillers are included
  - `transaction_test` is also supported, tests only correctness of static transaction checks
- **`--from MONAD_EIGHT --until MONAD_NEXT`**: hardforks for which to generate fixtures. Must match with those defined in `monad`, inclusive
- **`--chain-id 143`**: must be specified for signatures and EIP-7702 to work correctly
- **`-n auto`**: from `pytest`, parallel execution of tests
- **`tests`**: root directory to traverse to discover tests
  - inside the tests are organized by EIP/MIP and the hardfork when they **became relevant**. For Monad these are `monad_eight` and `monad_nine` as of writing this. Here hardfork is informative only, doesn't need to match with `monad`.
  - note that tests relevant for previous hardforks, e.g. `tests/prague/eip7702_set_code_tx`, are filled for hardforks requested with `--from`, `--until` flags, respecting any constraints the test itself might define. In other words, in the invocation above, EIP-7702 tests **will be filled**

`pytest` flags are in general supported, while `--traces` will give you detailed EVM step traces. Refer to https://eest.ethereum.org/main/filling_tests/ for more details.

### Gotchas

- not all Ethereum-only features have been switched off, e.g. eth-specific system contracts and tx types are available in the monadized `execution-specs` implementation. We're skipping their respective spec tests, and we don't have tests testing lack of these features in Monad.
- we're going to be keeping up with upstream changes (currently `forks/amsterdam` branch). This should work by opening a PR with the upstream changes to be commit-merged into our default branch (currently `forks/monad_nine`).
- when adding a new Monad-hardfork we should use the [`ethereum-spec-new-fork` tool](./CONTRIBUTING.md#new-fork-tool) provided to clone the parent hardfork into the new hardfork. If that new Monad-hardfork also adopts upstream (Ethereum) hardfork improvements, you will need to apply these yourself.

---

## Description

This repository contains the specifications related to the Ethereum execution client, specifically the [pyspec](/src/ethereum/__init__.py) and specifications for [network upgrades](/src/ethereum/__init__.py). The [JSON-RPC API specification](https://github.com/ethereum/execution-apis) can be found in a separate repository.

### Ethereum Protocol Releases

| Version and Code Name | Block No. | Released | Incl EIPs | Specs | Blog |
|-----------------------|-----------|----------|-----------|-------|-------|
| Prague | 22431084 | 2025-05-07 | [EIP-2537] <br> [EIP-2935] <br> [EIP-6110] <br> [EIP-7002] <br> [EIP-7251] <br> [EIP-7549] <br> [EIP-7623] <br> [EIP-7685] <br> [EIP-7691] <br> [EIP-7702] | [Specs](https://eips.ethereum.org/EIPS/eip-7600) | [Blog](https://blog.ethereum.org/2025/04/23/pectra-mainnet) |
| Cancun | 19426587 | 2024-03-13<br />(1710338135) | [EIP-1153](https://eips.ethereum.org/EIPS/eip-1153) </br> [EIP-4788](https://eips.ethereum.org/EIPS/eip-4788)</br> [EIP-4844](https://eips.ethereum.org/EIPS/eip-4844)</br> [EIP-5656](https://eips.ethereum.org/EIPS/eip-5656)</br> [EIP-6780](https://eips.ethereum.org/EIPS/eip-6780) </br> [EIP-7044](https://eips.ethereum.org/EIPS/eip-7044) </br> [EIP-7045](https://eips.ethereum.org/EIPS/eip-7045) </br> [EIP-7514](https://eips.ethereum.org/EIPS/eip-7514) </br> [EIP-7516](https://eips.ethereum.org/EIPS/eip-7516)| [Specification](/src/ethereum/forks/cancun/__init__.py) | [Blog](https://blog.ethereum.org/2024/02/27/dencun-mainnet-announcement) |
| Shanghai | 17034870 | 2023-04-12<br/>(1681338455) | [EIP-3651](https://eips.ethereum.org/EIPS/eip-3651) <br/> [EIP-3855](https://eips.ethereum.org/EIPS/eip-3855) <br/> [EIP-3860](https://eips.ethereum.org/EIPS/eip-3860) <br/> [EIP-4895](https://eips.ethereum.org/EIPS/eip-4895) | [Specification](/src/ethereum/forks/shanghai/__init__.py) | [Blog](https://blog.ethereum.org/2023/03/28/shapella-mainnet-announcement) |
| Paris | 15537394 | 2022-09-15 | [EIP-3675](https://eips.ethereum.org/EIPS/eip-3675) <br/> [EIP-4399](https://eips.ethereum.org/EIPS/eip-4399) | [Specification](/src/ethereum/forks/paris/__init__.py) | [Blog](https://blog.ethereum.org/2022/08/24/mainnet-merge-announcement) |
| Gray Glacier | 15050000 | 2022-06-30 | [EIP-5133](https://eips.ethereum.org/EIPS/eip-5133) | [Specification](/src/ethereum/forks/gray_glacier/__init__.py) | [Blog](https://blog.ethereum.org/2022/06/16/gray-glacier-announcement/) |
| Arrow Glacier | 13773000 | 2021-12-09 | [EIP-4345](https://eips.ethereum.org/EIPS/eip-4345) | [Specification](/src/ethereum/forks/arrow_glacier/__init__.py) | [Blog](https://blog.ethereum.org/2021/11/10/arrow-glacier-announcement/) |
| London | 12965000 |  2021-08-05 | [EIP-1559](https://eips.ethereum.org/EIPS/eip-1559) <br> [EIP-3198](https://eips.ethereum.org/EIPS/eip-3198) <br/> [EIP-3529](https://eips.ethereum.org/EIPS/eip-3529) <br/> [EIP-3541](https://eips.ethereum.org/EIPS/eip-3541) <br> [EIP-3554](https://eips.ethereum.org/EIPS/eip-3554)| [Specification](/src/ethereum/forks/london/__init__.py) | [Blog](https://blog.ethereum.org/2021/07/15/london-mainnet-announcement/) |
| Berlin | 12244000 | 2021-04-15 | [EIP-2565](https://eips.ethereum.org/EIPS/eip-2565) <br/> [EIP-2929](https://eips.ethereum.org/EIPS/eip-2929) <br/> [EIP-2718](https://eips.ethereum.org/EIPS/eip-2718) <br/> [EIP-2930](https://eips.ethereum.org/EIPS/eip-2930) | ~[HFM-2070](https://eips.ethereum.org/EIPS/eip-2070)~ <br/> [Specification](/src/ethereum/forks/berlin/__init__.py) | [Blog](https://blog.ethereum.org/2021/03/08/ethereum-berlin-upgrade-announcement/) |
| Muir Glacier | 9200000 | 2020-01-02 | [EIP-2384](https://eips.ethereum.org/EIPS/eip-2384) | [HFM-2387](https://eips.ethereum.org/EIPS/eip-2387) | [Blog](https://blog.ethereum.org/2019/12/23/ethereum-muir-glacier-upgrade-announcement/) |
| Istanbul | 9069000 | 2019-12-07 | [EIP-152](https://eips.ethereum.org/EIPS/eip-152) <br/> [EIP-1108](https://eips.ethereum.org/EIPS/eip-1108) <br/> [EIP-1344](https://eips.ethereum.org/EIPS/eip-1344) <br/> [EIP-1884](https://eips.ethereum.org/EIPS/eip-1884) <br/> [EIP-2028](https://eips.ethereum.org/EIPS/eip-2028) <br/> [EIP-2200](https://eips.ethereum.org/EIPS/eip-2200) | [HFM-1679](https://eips.ethereum.org/EIPS/eip-1679) | [Blog](https://blog.ethereum.org/2019/11/20/ethereum-istanbul-upgrade-announcement/) |
| Petersburg | 7280000 | 2019-02-28 | [EIP-145](https://eips.ethereum.org/EIPS/eip-145) <br/> [EIP-1014](https://eips.ethereum.org/EIPS/eip-1014) <br/> [EIP-1052](https://eips.ethereum.org/EIPS/eip-1052) <br/> [EIP-1234](https://eips.ethereum.org/EIPS/eip-1234) | [HFM-1716](https://eips.ethereum.org/EIPS/eip-1716) | [Blog](https://blog.ethereum.org/2019/02/22/ethereum-constantinople-st-petersburg-upgrade-announcement/) |
| Constantinople | 7280000 | 2019-02-28 | [EIP-145](https://eips.ethereum.org/EIPS/eip-145) <br/> [EIP-1014](https://eips.ethereum.org/EIPS/eip-1014) <br/> [EIP-1052](https://eips.ethereum.org/EIPS/eip-1052) <br/> [EIP-1234](https://eips.ethereum.org/EIPS/eip-1234) <br/> [EIP-1283](https://eips.ethereum.org/EIPS/eip-1283) | [HFM-1013](https://eips.ethereum.org/EIPS/eip-1013) | [Blog](https://blog.ethereum.org/2019/02/22/ethereum-constantinople-st-petersburg-upgrade-announcement/) |
| Byzantium | 4370000 | 2017-10-16 | [EIP-100](https://eips.ethereum.org/EIPS/eip-100) <br/> [EIP-140](https://eips.ethereum.org/EIPS/eip-140) <br/> [EIP-196](https://eips.ethereum.org/EIPS/eip-196) <br/> [EIP-197](https://eips.ethereum.org/EIPS/eip-197) <br/> [EIP-198](https://eips.ethereum.org/EIPS/eip-198) <br/> [EIP-211](https://eips.ethereum.org/EIPS/eip-211) <br/> [EIP-214](https://eips.ethereum.org/EIPS/eip-214) <br/> [EIP-649](https://eips.ethereum.org/EIPS/eip-649) <br/> [EIP-658](https://eips.ethereum.org/EIPS/eip-658) | [HFM-609](https://eips.ethereum.org/EIPS/eip-609) | [Blog](https://blog.ethereum.org/2017/10/12/byzantium-hf-announcement/) |
| Spurious Dragon | 2675000 | 2016-11-22 | [EIP-155](https://eips.ethereum.org/EIPS/eip-155) <br/> [EIP-160](https://eips.ethereum.org/EIPS/eip-160) <br/> [EIP-161](https://eips.ethereum.org/EIPS/eip-161) <br/> [EIP-170](https://eips.ethereum.org/EIPS/eip-170) | [HFM-607](https://eips.ethereum.org/EIPS/eip-607) | [Blog](https://blog.ethereum.org/2016/11/18/hard-fork-no-4-spurious-dragon/) |
| Tangerine Whistle | 2463000 | 2016-10-18 | [EIP-150](https://eips.ethereum.org/EIPS/eip-150) | [HFM-608](https://eips.ethereum.org/EIPS/eip-608) | [Blog](https://blog.ethereum.org/2016/10/13/announcement-imminent-hard-fork-eip150-gas-cost-changes/) |
| DAO Fork | 1920000 | 2016-07-20 |  | [HFM-779](https://eips.ethereum.org/EIPS/eip-779) | [Blog](https://blog.ethereum.org/2016/07/15/to-fork-or-not-to-fork/) |
| DAO Wars | aborted | aborted |  |  | [Blog](https://blog.ethereum.org/2016/06/24/dao-wars-youre-voice-soft-fork-dilemma/) |
| Homestead | 1150000 | 2016-03-14 | [EIP-2](https://eips.ethereum.org/EIPS/eip-2) <br/> [EIP-7](https://eips.ethereum.org/EIPS/eip-7) <br/> [EIP-8](https://eips.ethereum.org/EIPS/eip-8) | [HFM-606](https://eips.ethereum.org/EIPS/eip-606) | [Blog](https://blog.ethereum.org/2016/02/29/homestead-release/) |
| Frontier Thawing | 200000 | 2015-09-07 | | | [Blog](https://blog.ethereum.org/2015/08/04/the-thawing-frontier/) |
| Frontier | 1 | 2015-07-30 | | | [Blog](https://blog.ethereum.org/2015/07/22/frontier-is-coming-what-to-expect-and-how-to-prepare/) |

*Note:* Starting with Paris, updates are no longer rolled out based on block numbers. Paris was enabled once proof-of-work Total Difficulty reached 58750000000000000000000. As of Shanghai (at 1681338455), upgrade activation is based on timestamps.

[EIP-2537]: https://eips.ethereum.org/EIPS/eip-2537
[EIP-2935]: https://eips.ethereum.org/EIPS/eip-2935
[EIP-6110]: https://eips.ethereum.org/EIPS/eip-6110
[EIP-7002]: https://eips.ethereum.org/EIPS/eip-7002
[EIP-7251]: https://eips.ethereum.org/EIPS/eip-7251
[EIP-7549]: https://eips.ethereum.org/EIPS/eip-7549
[EIP-7623]: https://eips.ethereum.org/EIPS/eip-7623
[EIP-7685]: https://eips.ethereum.org/EIPS/eip-7685
[EIP-7691]: https://eips.ethereum.org/EIPS/eip-7691
[EIP-7702]: https://eips.ethereum.org/EIPS/eip-7702

Some clarifications were enabled without protocol releases:

| EIP | Block No. |
|-----|-----------|
| [EIP-2681](https://eips.ethereum.org/EIPS/eip-2681) | 0 |
| [EIP-3607](https://eips.ethereum.org/EIPS/eip-3607) | 0 |
| [EIP-7523](https://eips.ethereum.org/EIPS/eip-7523) | 15537394 |
| [EIP-7610](https://eips.ethereum.org/EIPS/eip-7610) | 0 |

## Execution Specification (work-in-progress)

The execution specification is a python implementation of Ethereum that prioritizes readability and simplicity. It will be accompanied by both narrative and API level documentation of the various components written in markdown and rendered using docc...

* [Rendered specification](https://ethereum.github.io/execution-specs/)

## Usage

The Ethereum specification is maintained as a Python library, for better integration with tooling and testing.

Requires Python 3.11+

### Building Specification Documentation

Building the spec documentation is most easily done through [`tox`](https://tox.readthedocs.io/en/latest/):

```bash
uvx --with=tox-uv tox -e spec-docs
```

The path to the generated HTML will be printed to the console.

### Browsing Updated Documentation

To view the updated local documentation, run:

```bash
uv run mkdocs serve
```

then connect to `localhost:8000` in a browser

## License

The Ethereum Execution Layer Specification code is licensed under the [Creative Commons Zero v1.0 Universal](LICENSE.md).
