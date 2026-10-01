"""
MONAD_NEXT fork introduces the extended opcodes of MIP-7 on top of
MONAD_TEN.
"""

from ethereum.fork_criteria import ByTimestamp, ForkCriteria

# TODO: just a bit after MONAD_TEN
FORK_CRITERIA: ForkCriteria = ByTimestamp(1774898553)
