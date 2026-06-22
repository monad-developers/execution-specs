"""
Tests SSTORE refund behavior upheld with MIP-8.
"""

import pytest
from execution_testing import (
    Account,
    Alloc,
    Bytecode,
    Op,
    StateTestFiller,
    Transaction,
    TransactionReceipt,
)
from execution_testing.base_types.conversions import NumberConvertible
from execution_testing.forks.helpers import Fork

from .helpers import full_page_sweep_gas, generous_gas
from .spec import Spec, ref_spec_8

REFERENCE_SPEC_GIT_PATH = ref_spec_8.git_path
REFERENCE_SPEC_VERSION = ref_spec_8.version

sender_initial_balance = 20 * 10**18  # well above RESERVE_BALANCE (10 MON)
pre_storage_value = 99


@pytest.mark.valid_from("MONAD_NINE")
def test_sstore_refund_removed(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    MIP-8 has no SSTORE refunds just as MONAD_NINE.

    Assert exact cumulative_gas_used via TransactionReceipt and
    sender balance change matches the computed gas charge.
    """
    pre_storage: dict[NumberConvertible, NumberConvertible] = dict.fromkeys(
        range(Spec.SLOTS_PER_PAGE), pre_storage_value
    )

    code = Bytecode()
    for i in range(Spec.SLOTS_PER_PAGE):
        code += Op.SSTORE(i, 0)

    contract_address = pre.deploy_contract(code, storage=pre_storage)

    sender = pre.fund_eoa(amount=sender_initial_balance)
    gas_price = 10**9
    gas_limit = generous_gas(fork) + full_page_sweep_gas(fork)

    tx = Transaction(
        gas_limit=gas_limit,
        gas_price=gas_price,
        to=contract_address,
        sender=sender,
        expected_receipt=TransactionReceipt(
            cumulative_gas_used=gas_limit,
        ),
    )

    state_test(
        pre=pre,
        post={
            contract_address: Account(storage={}),
            sender: Account(
                balance=sender_initial_balance - gas_limit * gas_price,
                nonce=1,
            ),
        },
        tx=tx,
    )
