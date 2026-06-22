"""
MONAD_TEN fork introduces Monad specific changes to the Ethereum protocol.
"""

from ethereum.fork_criteria import ByTimestamp, ForkCriteria

# TODO: just a bit after MONAD_NINE
FORK_CRITERIA: ForkCriteria = ByTimestamp(1774898552)
