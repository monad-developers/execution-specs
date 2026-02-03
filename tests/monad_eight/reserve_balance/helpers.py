"""
Helper types, functions and classes for testing reserve balance.
"""

from enum import Enum, auto, unique
from typing import List

from execution_testing import Op
from execution_testing.forks.helpers import Fork

from .spec import Spec


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


@unique
class Stage1Balance(Enum):
    """Initial balance states for Stage 1."""

    BELOW_RESERVE = auto()
    AT_RESERVE = auto()
    ABOVE_RESERVE = auto()

    def __str__(self) -> str:
        """Return string representation."""
        return self.name.lower()

    def compute_balance(self) -> int:
        """Compute the actual balance for this stage."""
        match self:
            case Stage1Balance.BELOW_RESERVE:
                return Spec.RESERVE_BALANCE // 2
            case Stage1Balance.AT_RESERVE:
                return Spec.RESERVE_BALANCE
            case Stage1Balance.ABOVE_RESERVE:
                return 2 * Spec.RESERVE_BALANCE


@unique
class StageBalance(Enum):
    """Balance states for Stage 2/3 relative to min/max of reserve, initial."""

    BELOW_MIN = auto()
    AT_MIN = auto()
    BETWEEN = auto()
    AT_MAX = auto()
    ABOVE_MAX = auto()

    def __str__(self) -> str:
        """Return string representation."""
        return self.name.lower()

    def compute_balance(self, previous_balances: List[int]) -> int:
        """Compute the actual balance for this stage."""
        min_val = min(Spec.RESERVE_BALANCE, *previous_balances)
        max_val = max(Spec.RESERVE_BALANCE, *previous_balances)
        match self:
            case StageBalance.BELOW_MIN:
                assert min_val >= 1
                return min_val - 1
            case StageBalance.AT_MIN:
                return min_val
            case StageBalance.BETWEEN:
                return (min_val + max_val) // 2
            case StageBalance.AT_MAX:
                return max_val
            case StageBalance.ABOVE_MAX:
                return max_val + 1
