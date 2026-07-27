"""
MONAD_NEXT fork is a placeholder for upcoming Monad changes and is
currently identical to MONAD_TEN.
"""

from ethereum.fork_criteria import ByTimestamp, ForkCriteria

# TODO: just a bit after MONAD_TEN
FORK_CRITERIA: ForkCriteria = ByTimestamp(1774898553)
