"""
Tests reserve balance when a transaction's gas fees reach the reserve or
its fee cap exceeds its balance.
"""

from enum import Enum, auto, unique
from typing import Any

import pytest
from execution_testing import (
    Account,
    Address,
    Alloc,
    Block,
    BlockchainTestFiller,
    Header,
    Op,
    Transaction,
    TransactionException,
)
from execution_testing.forks.helpers import Fork
from execution_testing.test_types.block_types import DEFAULT_BASE_FEE

from .spec import Spec, ref_spec_7702

REFERENCE_SPEC_GIT_PATH = ref_spec_7702.git_path
REFERENCE_SPEC_VERSION = ref_spec_7702.version

slot_code_worked = 0x1
value_code_worked = 0x1234

GAS_LIMIT = 500_000
GAS_PRICE = 10
TX_FEE = GAS_PRICE * GAS_LIMIT

pytestmark = [
    pytest.mark.valid_from("MONAD_EIGHT"),
    pytest.mark.pre_alloc_group(
        "reserve_balance_tests",
        reason="Tests reserve balance",
    ),
]


def priced_transaction(
    tx_type: int, gas_price: int, **fields: Any
) -> Transaction:
    """
    Build a transaction of `tx_type` charged at `gas_price`.

    A type-2 transaction caps its fee at twice that price, with the tip
    chosen so that the effective price stays at `gas_price`.
    """
    if tx_type == 0:
        return Transaction(ty=0, gas_price=gas_price, **fields)
    if tx_type == 2:
        return Transaction(
            ty=2,
            max_fee_per_gas=2 * gas_price,
            max_priority_fee_per_gas=gas_price - DEFAULT_BASE_FEE,
            **fields,
        )
    raise ValueError(f"unsupported transaction type {tx_type}")


@unique
class GasFees(Enum):
    """Gas fees of a max-gas transaction, relative to the reserve."""

    HALF_RESERVE = auto()
    JUST_BELOW_RESERVE = auto()
    JUST_ABOVE_RESERVE = auto()
    DOUBLE_RESERVE = auto()

    def __str__(self) -> str:
        """Return string representation."""
        return self.name.lower()

    def gas_price(self, gas_limit: int) -> int:
        """
        Compute the gas price placing the fees at this point.

        The reserve is not an exact multiple of the gas limit, so the
        boundary price is the largest one whose fees still fit within the
        reserve, and the pair straddling it is the tightest available.
        """
        boundary = Spec.RESERVE_BALANCE // gas_limit
        match self:
            case GasFees.HALF_RESERVE:
                return boundary // 2
            case GasFees.JUST_BELOW_RESERVE:
                return boundary
            case GasFees.JUST_ABOVE_RESERVE:
                return boundary + 1
            case GasFees.DOUBLE_RESERVE:
                return 2 * boundary


@unique
class Value(Enum):
    """The value sent, relative to the fee held above the reserve."""

    ZERO = auto()
    EQUAL_TO_FEE = auto()
    ABOVE_FEE = auto()

    def __str__(self) -> str:
        """Return string representation."""
        return self.name.lower()

    def compute(self, fee: int) -> int:
        """Compute the value for a transaction charged `fee`."""
        match self:
            case Value.ZERO:
                return 0
            case Value.EQUAL_TO_FEE:
                return fee
            case Value.ABOVE_FEE:
                return fee + 1


@pytest.mark.parametrize("tx_type", [0, 2])
@pytest.mark.parametrize("value", list(Value))
@pytest.mark.parametrize(
    ["gas_fees", "pre_delegated"],
    [
        pytest.param(GasFees.HALF_RESERVE, False, id="half_reserve"),
        pytest.param(GasFees.HALF_RESERVE, True, id="half_reserve_delegated"),
        pytest.param(
            GasFees.JUST_BELOW_RESERVE, False, id="just_below_reserve"
        ),
        pytest.param(
            GasFees.JUST_BELOW_RESERVE,
            True,
            id="just_below_reserve_delegated",
        ),
        # Consensus only sequences a delegated sender whose gas fees fit
        # within the reserve, so the larger fees run undelegated only.
        pytest.param(
            GasFees.JUST_ABOVE_RESERVE, False, id="just_above_reserve"
        ),
        pytest.param(GasFees.DOUBLE_RESERVE, False, id="double_reserve"),
    ],
)
def test_gas_fees_never_violate_reserve(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
    gas_fees: GasFees,
    pre_delegated: bool,
    value: Value,
    tx_type: int,
) -> None:
    """
    Test that a transaction is allowed through however large its gas fees
    are, including at and above the whole reserve, and that only the value
    it sends counts against the reserve.

    The sender is funded to sit exactly at the reserve once the gas is
    debited, so the fee is also the value it may send without dipping
    into the reserve.
    """
    gas_limit = fork.transaction_gas_limit_cap()
    assert gas_limit is not None, "fork must cap the transaction gas limit"
    gas_price = gas_fees.gas_price(gas_limit)
    fee = gas_price * gas_limit
    balance = Spec.RESERVE_BALANCE + fee
    amount = value.compute(fee)

    contract_address = pre.deploy_contract(
        Op.SSTORE(slot_code_worked, value_code_worked) + Op.STOP
    )
    sender = pre.fund_eoa(
        balance, delegation=Address(0x0111) if pre_delegated else None
    )

    tx = priced_transaction(
        tx_type,
        gas_price,
        to=contract_address,
        gas_limit=gas_limit,
        value=amount,
        sender=sender,
    )

    # The value fails once the balance left after the fee cannot cover it,
    # and dips into the reserve once it exceeds what the sender held above
    # the reserve.
    insufficient = amount > balance - fee
    violation = amount > balance - min(Spec.RESERVE_BALANCE, balance)
    reverted = insufficient or (violation and pre_delegated)
    storage = {} if reverted else {slot_code_worked: value_code_worked}

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage=storage, balance=0 if reverted else amount
            ),
            sender: Account(
                nonce=sender.nonce,
                balance=balance - fee - (0 if reverted else amount),
            ),
        },
        blocks=[
            Block(
                txs=[tx],
                header_verify=Header(base_fee_per_gas=DEFAULT_BASE_FEE),
            )
        ],
    )


@pytest.mark.parametrize(
    ["value", "balance", "violation"],
    # The sender holds less than the fee cap of `TX_FEE * 2` in every case.
    [
        pytest.param(
            0,
            TX_FEE - 1,
            False,
            id="zero_value_below_fee",
            marks=pytest.mark.exception_test,
        ),
        pytest.param(0, TX_FEE, False, id="zero_value_spend_all_on_gas"),
        pytest.param(1, TX_FEE + 1, True, id="non_zero_value"),
        pytest.param(
            TX_FEE - 1,
            2 * TX_FEE - 1,
            True,
            id="value_equal_to_balance_minus_gas",
        ),
        pytest.param(
            TX_FEE,
            2 * TX_FEE - 1,
            True,
            id="value_above_balance_minus_gas",
        ),
    ],
)
@pytest.mark.parametrize("pre_delegated", [True, False])
def test_max_fee_above_balance(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    violation: bool,
    pre_delegated: bool,
) -> None:
    """
    Test that a transaction is included when the sender's balance covers
    its gas fees at the effective gas price but not at the max fee per gas,
    and that its value is then judged against the reserve as usual.
    """
    sender = pre.fund_eoa(
        balance, delegation=Address(0x0111) if pre_delegated else None
    )
    sender_nonce = sender.nonce
    contract_address = pre.deploy_contract(
        Op.SSTORE(slot_code_worked, value_code_worked) + Op.STOP
    )

    rejected = balance < TX_FEE
    exception = (
        TransactionException.INSUFFICIENT_ACCOUNT_FUNDS if rejected else None
    )
    tx = priced_transaction(
        2,
        GAS_PRICE,
        to=contract_address,
        gas_limit=GAS_LIMIT,
        value=value,
        sender=sender,
        error=exception,
    )

    insufficient = value > balance - TX_FEE
    reverted = insufficient or (violation and pre_delegated)
    executed = not rejected and not reverted
    storage = {slot_code_worked: value_code_worked} if executed else {}

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage=storage, balance=value if executed else 0
            ),
            sender: Account(
                nonce=sender_nonce if rejected else sender_nonce + 1,
                balance=balance
                if rejected
                else balance - TX_FEE - (value if executed else 0),
            ),
        },
        blocks=[
            Block(
                txs=[tx],
                exception=exception,
                header_verify=None
                if rejected
                else Header(base_fee_per_gas=DEFAULT_BASE_FEE),
            )
        ],
    )
