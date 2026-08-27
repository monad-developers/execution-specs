"""
MONAD_NEXT fork is a placeholder for upcoming Monad changes. It builds on
MONAD_TEN, adopting EIP-7708, EIP-7843 and EIP-8024 from Amsterdam
together with the Amsterdam block header layout; the [EIP-7928] block
access list hash header slot is carried but always zero.

[EIP-7928]: https://eips.ethereum.org/EIPS/eip-7928
"""

from ethereum.fork_criteria import ByTimestamp, ForkCriteria

# TODO: just a bit after MONAD_TEN
FORK_CRITERIA: ForkCriteria = ByTimestamp(1774898553)
