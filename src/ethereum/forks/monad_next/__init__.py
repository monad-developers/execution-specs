"""
MONAD_NEXT fork builds on MONAD_TEN, adopting part of Amsterdam.

The Amsterdam changes it does not adopt stay out of the fork entirely.
The [EIP-7928] block access list hash header slot is carried but always
zero, so the header layout matches Amsterdam while no block access list
is built.

### Changes

- [EIP-7708: ETH transfers emit a log][EIP-7708]
- [EIP-7843: SLOTNUM][EIP-7843]
- [EIP-7981: Increase Access List Cost][EIP-7981]
- [EIP-7997: Deterministic Factory Predeploy][EIP-7997]
- [EIP-8024: Stack Access Instructions][EIP-8024]
- [EIP-8246: Remove SELFDESTRUCT balance burn][EIP-8246]

[EIP-7708]: https://eips.ethereum.org/EIPS/eip-7708
[EIP-7843]: https://eips.ethereum.org/EIPS/eip-7843
[EIP-7928]: https://eips.ethereum.org/EIPS/eip-7928
[EIP-7981]: https://eips.ethereum.org/EIPS/eip-7981
[EIP-7997]: https://eips.ethereum.org/EIPS/eip-7997
[EIP-8024]: https://eips.ethereum.org/EIPS/eip-8024
[EIP-8246]: https://eips.ethereum.org/EIPS/eip-8246
"""

from ethereum.fork_criteria import ByTimestamp, ForkCriteria

# TODO: just a bit after MONAD_TEN
FORK_CRITERIA: ForkCriteria = ByTimestamp(1774898553)
