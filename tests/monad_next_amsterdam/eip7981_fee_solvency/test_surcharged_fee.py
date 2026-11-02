"""
Tests for the EIP-7981 access list surcharge against the sender's balance.

Monad includes a transaction once the sender covers its gas fees at the
effective gas price and bills the whole gas limit. MONAD_TEN fills the
same boundaries without the surcharge, for comparison.
"""

import pytest
from execution_testing import (
    AccessList,
    Account,
    Address,
    Alloc,
    EIPChecklist,
    Fork,
    Hash,
    RecipientType,
    StateTestFiller,
    TransactionException,
    TransactionReceipt,
)

from ...amsterdam.eip7981_increase_access_list_cost.helpers import (
    calculate_access_list_data_cost,
)
from ...amsterdam.eip7981_increase_access_list_cost.spec import ref_spec_7981
from ...monad_eight.reserve_balance.helpers import priced_transaction

REFERENCE_SPEC_GIT_PATH = ref_spec_7981.git_path
REFERENCE_SPEC_VERSION = ref_spec_7981.version

GAS_PRICE = 10
ACCESS_LIST = [AccessList(address=Address(1), storage_keys=[Hash(0)])]

pytestmark = pytest.mark.valid_from("MONAD_TEN")


@pytest.mark.parametrize(
    "value,balance_delta",
    [
        pytest.param(
            1,
            -1,
            id="fee_short_by_one",
            marks=[
                pytest.mark.exception_test,
                EIPChecklist.TransactionType.Test.IntrinsicValidity.ValueNonZeroInsufficientBalance(),
            ],
        ),
        pytest.param(
            1,
            0,
            id="fee_exact",
            marks=EIPChecklist.TransactionType.Test.IntrinsicValidity.ValueNonZeroInsufficientBalance(),
        ),
        pytest.param(2, 1, id="value_short_by_one"),
    ],
)
@pytest.mark.parametrize(
    "tx_type",
    [pytest.param(1, id="type_1"), pytest.param(2, id="type_2")],
)
def test_surcharged_fee_inclusion(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    tx_type: int,
    value: int,
    balance_delta: int,
) -> None:
    """
    Fund the surcharged fee one wei short, exactly, or with part of the
    value on top; the value stays out of the inclusion check.
    """
    recipient = pre.fund_eoa(amount=0)
    intrinsic_cost = fork.transaction_intrinsic_cost_calculator()
    gas_limit = intrinsic_cost(
        access_list=ACCESS_LIST,
        sends_value=True,
        recipient_type=RecipientType.EMPTY_ACCOUNT,
    )
    # Pin where the fee boundary sits on either side of EIP-7981.
    costs = fork.gas_costs()
    surcharge = (
        calculate_access_list_data_cost(ACCESS_LIST, fork)
        if fork.is_eip_enabled(7981)
        else 0
    )
    assert gas_limit == (
        intrinsic_cost(
            sends_value=True, recipient_type=RecipientType.EMPTY_ACCOUNT
        )
        + costs.TX_ACCESS_LIST_ADDRESS
        + costs.TX_ACCESS_LIST_STORAGE_KEY
        + surcharge
    )
    sender = pre.fund_eoa(amount=gas_limit * GAS_PRICE + balance_delta)
    sender_nonce = sender.nonce
    rejected = balance_delta < 0
    tx = priced_transaction(
        tx_type,
        GAS_PRICE,
        sender=sender,
        to=recipient,
        value=value,
        gas_limit=gas_limit,
        access_list=ACCESS_LIST,
        error=TransactionException.INSUFFICIENT_ACCOUNT_FUNDS
        if rejected
        else None,
        expected_receipt=None
        if rejected
        else TransactionReceipt(status=0, gas_used=gas_limit),
    )
    post: dict[Address, Account | None] = {recipient: Account.NONEXISTENT}
    if not rejected:
        post[sender] = Account(nonce=sender_nonce + 1, balance=balance_delta)
    state_test(pre=pre, post=post, tx=tx)
