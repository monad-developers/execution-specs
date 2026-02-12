"""
The Amsterdam fork ([EIP-7773]).

### Releases

[EIP-7773]: https://eips.ethereum.org/EIPS/eip-7773
"""

from ethereum.fork_criteria import ForkCriteria, Unscheduled

FORK_CRITERIA: ForkCriteria = Unscheduled(order_index=3)
