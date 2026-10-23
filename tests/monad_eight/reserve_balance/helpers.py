"""
Helper types, functions and classes for testing reserve balance.
"""

from enum import Enum, auto, unique
from typing import Any, List

from execution_testing import EOA, Address, AuthorizationTuple, Op, Transaction
from execution_testing.forks.helpers import Fork
from execution_testing.test_types.block_types import DEFAULT_BASE_FEE

from tests.prague.eip7702_set_code_tx.spec import Spec as Spec7702

from .spec import Spec


def generous_gas(fork: Fork) -> int:
    """
    Return generous parametrized gas to always be enough.
    """
    constant = 100_000
    gas_costs = fork.gas_costs()
    sstore_cost = gas_costs.STORAGE_SET + gas_costs.COLD_STORAGE_ACCESS
    deploy_cost = gas_costs.CODE_DEPOSIT_PER_BYTE * len(Op.STOP)
    access_cost = gas_costs.COLD_ACCOUNT_ACCESS
    selfdestruct_cost = gas_costs.OPCODE_SELFDESTRUCT_BASE
    return (
        constant
        + sstore_cost
        + deploy_cost
        + 5 * access_cost
        + selfdestruct_cost
    )


def priced_transaction(
    tx_type: int, gas_price: int, **fields: Any
) -> Transaction:
    """
    Build a transaction of `tx_type` charged at `gas_price`.

    A type-2 transaction caps its fee at twice that price, with the tip
    chosen so that the effective price stays at `gas_price`.
    """
    if tx_type in (0, 1):
        return Transaction(ty=tx_type, gas_price=gas_price, **fields)
    if tx_type == 2:
        return Transaction(
            ty=2,
            max_fee_per_gas=2 * gas_price,
            max_priority_fee_per_gas=gas_price - DEFAULT_BASE_FEE,
            **fields,
        )
    raise ValueError(f"unsupported transaction type {tx_type}")


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


@unique
class InertAuthorization(Enum):
    """
    Ways an authorization recovers its signer yet is never applied.
    """

    WRONG_CHAIN_ID = auto()
    NONCE_OVERFLOW = auto()
    NONCE_MISMATCH = auto()

    def __str__(self) -> str:
        """Return string representation."""
        return self.name.lower()

    def authorization(
        self, signer: EOA, valid_nonce: int, chain_id: int
    ) -> AuthorizationTuple:
        """
        Sign a delegation for `signer` that `valid_nonce` and `chain_id`
        would make valid, broken in this one way.
        """
        nonce = valid_nonce
        match self:
            case InertAuthorization.WRONG_CHAIN_ID:
                chain_id += 1
            case InertAuthorization.NONCE_OVERFLOW:
                nonce = Spec7702.MAX_NONCE
            case InertAuthorization.NONCE_MISMATCH:
                nonce += 1
        return AuthorizationTuple(
            chain_id=chain_id,
            address=Address(0x1111),
            nonce=nonce,
            signer=signer,
            writes_delegation=False,
            first_write=False,
        )
