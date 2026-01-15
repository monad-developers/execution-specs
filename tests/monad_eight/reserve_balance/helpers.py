"""
Helper types, functions and classes for testing reserve balance.
"""

from execution_testing import (
    Op,
)
from execution_testing.forks.helpers import Fork


def generous_gas(fork: Fork) -> int:
    """
    Return generous parametrized gas to always be enough.
    """
    constant = 100_000
    gas_costs = fork.gas_costs()
    sstore_cost = gas_costs.G_STORAGE_SET + gas_costs.G_COLD_SLOAD
    deploy_cost = gas_costs.G_CODE_DEPOSIT_BYTE * len(Op.STOP)
    access_cost = gas_costs.G_COLD_ACCOUNT_ACCESS
    selfdestruct_cost = gas_costs.G_SELF_DESTRUCT
    return (
        constant
        + sstore_cost
        + deploy_cost
        + 5 * access_cost
        + selfdestruct_cost
    )
