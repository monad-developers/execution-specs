"""
MONAD_EIGHT fork introduces Monad specific changes to the Ethereum protocol.
"""

from ethereum.fork_criteria import ByTimestamp, ForkCriteria

FORK_CRITERIA: ForkCriteria = ByTimestamp(0)
