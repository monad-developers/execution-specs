"""
MONAD_NINE fork introduces Monad specific changes to the Ethereum protocol.
"""

from ethereum.fork_criteria import ByTimestamp, ForkCriteria

# TODO: just a bit after MONAD_EIGHT and Osaka
FORK_CRITERIA: ForkCriteria = ByTimestamp(max(1746627312, 1764898551))
