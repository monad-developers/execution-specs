"""
MONAD_EIGHT fork introduces Monad specific changes to the Ethereum protocol.
"""

from ethereum.fork_criteria import ByTimestamp, ForkCriteria

# TODO: just a bit after Prague
FORK_CRITERIA: ForkCriteria = ByTimestamp(1746627311)
