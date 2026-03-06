"""
Tests for reserve balance transaction revert mechanism.

Tests verify that the reserve balance transaction reversion rules
from MONAD_EIGHT are not affected by the new precompile in MIP-4.
"""

import pytest
from execution_testing import (
    Account,
    Address,
    Alloc,
    Block,
    BlockchainTestFiller,
    Op,
    Transaction,
)
from execution_testing.forks import MONAD_NEXT
from execution_testing.forks.helpers import Fork

from .helpers import (
    RefillFactory,
    call_dipped_into_reserve,
    generous_gas,
)
from .spec import (
    Spec,
    ref_spec_mip4,
)

REFERENCE_SPEC_GIT_PATH = ref_spec_mip4.git_path
REFERENCE_SPEC_VERSION = ref_spec_mip4.version

slot_code_worked = 0x1
value_code_worked = 0x1234
slot_violation_result = 0x10

pytestmark = [
    pytest.mark.valid_from("MONAD_EIGHT"),
    pytest.mark.pre_alloc_group(
        "mip4_tx_revert_tests",
        reason="Tests reserve balance tx revert mechanism",
    ),
]


@pytest.mark.parametrize(
    "violation_for_check,violation_for_tx_revert",
    [
        pytest.param(False, False, id="no_violation-success"),
        pytest.param(False, True, id="no_violation-revert"),
        pytest.param(True, False, id="violation-refill-success"),
        pytest.param(True, True, id="violation-no_refill-revert"),
    ],
)
def test_precompile_does_not_alter_revert_mechanism(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    refill_factory: RefillFactory,
    fork: Fork,
    violation_for_check: bool,
    violation_for_tx_revert: bool,
) -> None:
    """
    Test that calling the precompile doesn't alter the reserve balance
    violation revert mechanism at the end of the transaction.

    The sender is always delegated. The contract controls when violation
    occurs by calling back to the sender (which triggers wallet code that
    sends value, depleting the sender's balance below reserve).
    """
    refill_call = refill_factory()

    wallet_code = Op.CALL(address=Address(0x0111), value=1)
    wallet_address = pre.deploy_contract(code=wallet_code)

    if not violation_for_check and not violation_for_tx_revert:
        initial_balance = 2 * Spec.RESERVE_BALANCE
    else:
        initial_balance = Spec.RESERVE_BALANCE

    sender = pre.fund_eoa(initial_balance, delegation=wallet_address)

    contract_code = Op.SSTORE(slot_code_worked, value_code_worked)

    if violation_for_check:
        contract_code += Op.CALL(address=sender)

    if fork >= MONAD_NEXT:
        contract_code += Op.SSTORE(
            slot_violation_result, call_dipped_into_reserve()
        )

    if not violation_for_tx_revert:
        contract_code += refill_call(sender)
    elif not violation_for_check:
        contract_code += Op.CALL(address=sender)

    contract_address = pre.deploy_contract(contract_code)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=sender,
    )

    if violation_for_tx_revert:
        storage = {}
    else:
        storage = {slot_code_worked: value_code_worked}
        if fork >= MONAD_NEXT:
            storage[slot_violation_result] = 1 if violation_for_check else 0

    blockchain_test(
        pre=pre,
        post={contract_address: Account(storage=storage)},
        blocks=[Block(txs=[tx])],
    )
