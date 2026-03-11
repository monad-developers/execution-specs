"""
Tests for staking precompile stateful lifecycle operations.

These tests verify that staking operations modify state correctly:
addValidator, delegate, undelegate, compound, claimRewards, withdraw.
They are expected to FAIL against the current stub implementation,
which does not maintain any staking state.
"""

import pytest
from execution_testing import (
    Account,
    Alloc,
    Block,
    BlockchainTestFiller,
    Op,
    Transaction,
)
from execution_testing.forks.helpers import Fork

from .helpers import build_calldata, generous_gas
from .spec import (
    CALLDATA_SIZE_ADD_VALIDATOR,
    CALLDATA_SIZE_CLAIM_REWARDS,
    CALLDATA_SIZE_COMPOUND,
    CALLDATA_SIZE_DELEGATE,
    CALLDATA_SIZE_GET_DELEGATOR,
    CALLDATA_SIZE_GET_VALIDATOR,
    CALLDATA_SIZE_UNDELEGATE,
    GAS_ADD_VALIDATOR,
    GAS_CLAIM_REWARDS,
    GAS_COMPOUND,
    GAS_DELEGATE,
    GAS_GET_DELEGATOR,
    GAS_GET_VALIDATOR,
    GAS_UNDELEGATE,
    SELECTOR_ADD_VALIDATOR,
    SELECTOR_CLAIM_REWARDS,
    SELECTOR_COMPOUND,
    SELECTOR_DELEGATE,
    SELECTOR_GET_DELEGATOR,
    SELECTOR_GET_VALIDATOR,
    SELECTOR_UNDELEGATE,
    STAKING_PRECOMPILE,
    ref_spec_staking,
)

REFERENCE_SPEC_GIT_PATH = ref_spec_staking.git_path
REFERENCE_SPEC_VERSION = ref_spec_staking.version

slot_code_worked = 0x1
value_code_worked = 0x1234
slot_add_val_success = 0x2
slot_add_val_return = 0x3
slot_delegate_success = 0x4
slot_get_val_success = 0x5
slot_get_val_word0 = 0x6
slot_get_delegator_success = 0x7
slot_get_delegator_word0 = 0x8
slot_undelegate_success = 0x9
slot_compound_success = 0xA
slot_claim_success = 0xB

# Stake amount: 1 MON
STAKE_AMOUNT = 10**18

pytestmark = [
    pytest.mark.valid_from("MONAD_EIGHT"),
    pytest.mark.pre_alloc_group(
        "staking_precompile_lifecycle_tests",
        reason="Tests staking precompile lifecycle operations",
    ),
]


def _call_with_value(
    selector: int,
    calldata_size: int,
    gas_cost: int,
    value: int,
    ret_offset: int = 0,
    ret_size: int = 0,
) -> tuple:
    """
    Return (calldata_setup, call_op) for a payable precompile call.
    """
    setup = build_calldata(selector, calldata_size)
    call_op = Op.CALL(
        gas=gas_cost + 10000,
        address=STAKING_PRECOMPILE,
        value=value,
        args_offset=28,
        args_size=calldata_size,
        ret_offset=ret_offset,
        ret_size=ret_size,
    )
    return setup, call_op


def _call_no_value(
    selector: int,
    calldata_size: int,
    gas_cost: int,
    ret_offset: int = 0,
    ret_size: int = 0,
) -> tuple:
    """
    Return (calldata_setup, call_op) for a non-payable precompile call.
    """
    setup = build_calldata(selector, calldata_size)
    call_op = Op.CALL(
        gas=gas_cost + 10000,
        address=STAKING_PRECOMPILE,
        args_offset=28,
        args_size=calldata_size,
        ret_offset=ret_offset,
        ret_size=ret_size,
    )
    return setup, call_op


def test_add_validator_returns_id(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test that addValidator returns a non-zero validator ID.

    The stub returns validator ID = 1. A real implementation should
    return incrementing IDs.
    """
    add_setup, add_call = _call_with_value(
        SELECTOR_ADD_VALIDATOR,
        CALLDATA_SIZE_ADD_VALIDATOR,
        GAS_ADD_VALIDATOR,
        value=STAKE_AMOUNT,
        ret_offset=256,
        ret_size=32,
    )

    contract = (
        add_setup
        + Op.SSTORE(slot_add_val_success, add_call)
        + Op.SSTORE(slot_add_val_return, Op.MLOAD(256))
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    contract_address = pre.deploy_contract(contract, balance=STAKE_AMOUNT)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_add_val_success: 1,
                    # Expect validator ID = 1
                    slot_add_val_return: 1,
                    slot_code_worked: value_code_worked,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )


def test_add_validator_then_get(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test that getValidator reflects state set by addValidator.

    After addValidator, getValidator(id) should return non-zero
    fields. The stub returns all zeros, so this test is expected
    to FAIL.
    """
    # addValidator returns validator ID at ret_offset=256
    add_setup, add_call = _call_with_value(
        SELECTOR_ADD_VALIDATOR,
        CALLDATA_SIZE_ADD_VALIDATOR,
        GAS_ADD_VALIDATOR,
        value=STAKE_AMOUNT,
        ret_offset=256,
        ret_size=32,
    )

    # getValidator(uint64 validatorId) - ID=1 at mem[32]
    get_setup, get_call = _call_no_value(
        SELECTOR_GET_VALIDATOR,
        CALLDATA_SIZE_GET_VALIDATOR,
        GAS_GET_VALIDATOR,
        ret_offset=512,
        ret_size=32 * 20,
    )

    contract = (
        add_setup
        + Op.SSTORE(slot_add_val_success, add_call)
        + Op.SSTORE(slot_add_val_return, Op.MLOAD(256))
        # Now call getValidator with ID=1 (already stored at mem[32])
        + get_setup
        + Op.SSTORE(slot_get_val_success, get_call)
        # First word of validator struct should be non-zero
        # (e.g., stake amount or validator pubkey hash)
        + Op.SSTORE(slot_get_val_word0, Op.MLOAD(512))
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    contract_address = pre.deploy_contract(contract, balance=STAKE_AMOUNT)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_add_val_success: 1,
                    slot_add_val_return: 1,
                    slot_get_val_success: 1,
                    # FAIL: stub returns 0; real impl returns
                    # non-zero validator data
                    slot_get_val_word0: 1,
                    slot_code_worked: value_code_worked,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )


def test_delegate_then_get_delegator(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test that getDelegator reflects state set by delegate.

    After addValidator + delegate, getDelegator should return
    non-zero delegation info. Expected to FAIL against stub.
    """
    # addValidator
    add_setup, add_call = _call_with_value(
        SELECTOR_ADD_VALIDATOR,
        CALLDATA_SIZE_ADD_VALIDATOR,
        GAS_ADD_VALIDATOR,
        value=STAKE_AMOUNT,
        ret_offset=256,
        ret_size=32,
    )

    # delegate(uint64 validatorId) with value
    del_setup, del_call = _call_with_value(
        SELECTOR_DELEGATE,
        CALLDATA_SIZE_DELEGATE,
        GAS_DELEGATE,
        value=STAKE_AMOUNT,
    )

    # getDelegator(uint64 validatorId, address delegator)
    get_setup, get_call = _call_no_value(
        SELECTOR_GET_DELEGATOR,
        CALLDATA_SIZE_GET_DELEGATOR,
        GAS_GET_DELEGATOR,
        ret_offset=512,
        ret_size=32 * 10,
    )

    contract = (
        add_setup
        + Op.SSTORE(slot_add_val_success, add_call)
        + del_setup
        + Op.SSTORE(slot_delegate_success, del_call)
        + get_setup
        + Op.SSTORE(slot_get_delegator_success, get_call)
        # First word should contain delegation amount
        + Op.SSTORE(slot_get_delegator_word0, Op.MLOAD(512))
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    contract_address = pre.deploy_contract(contract, balance=STAKE_AMOUNT * 2)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_add_val_success: 1,
                    slot_delegate_success: 1,
                    slot_get_delegator_success: 1,
                    # FAIL: stub returns 0; real impl returns
                    # non-zero delegation data
                    slot_get_delegator_word0: 1,
                    slot_code_worked: value_code_worked,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )


def test_undelegate_after_delegate(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test that undelegate succeeds after a prior delegation.

    Expected to FAIL against stub (undelegate stub doesn't verify
    prior delegation exists).
    """
    # addValidator
    add_setup, add_call = _call_with_value(
        SELECTOR_ADD_VALIDATOR,
        CALLDATA_SIZE_ADD_VALIDATOR,
        GAS_ADD_VALIDATOR,
        value=STAKE_AMOUNT,
        ret_offset=256,
        ret_size=32,
    )

    # delegate
    del_setup, del_call = _call_with_value(
        SELECTOR_DELEGATE,
        CALLDATA_SIZE_DELEGATE,
        GAS_DELEGATE,
        value=STAKE_AMOUNT,
    )

    # undelegate(uint64 validatorId, uint256 amount, uint8 type)
    undel_setup, undel_call = _call_no_value(
        SELECTOR_UNDELEGATE,
        CALLDATA_SIZE_UNDELEGATE,
        GAS_UNDELEGATE,
    )

    contract = (
        add_setup
        + Op.SSTORE(slot_add_val_success, add_call)
        + del_setup
        + Op.SSTORE(slot_delegate_success, del_call)
        + undel_setup
        + Op.SSTORE(slot_undelegate_success, undel_call)
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    contract_address = pre.deploy_contract(contract, balance=STAKE_AMOUNT * 2)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_add_val_success: 1,
                    slot_delegate_success: 1,
                    slot_undelegate_success: 1,
                    slot_code_worked: value_code_worked,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )


def test_compound_rewards(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test that compound succeeds after delegation.

    Expected to FAIL against stub (compound doesn't verify state).
    """
    # addValidator
    add_setup, add_call = _call_with_value(
        SELECTOR_ADD_VALIDATOR,
        CALLDATA_SIZE_ADD_VALIDATOR,
        GAS_ADD_VALIDATOR,
        value=STAKE_AMOUNT,
        ret_offset=256,
        ret_size=32,
    )

    # delegate
    del_setup, del_call = _call_with_value(
        SELECTOR_DELEGATE,
        CALLDATA_SIZE_DELEGATE,
        GAS_DELEGATE,
        value=STAKE_AMOUNT,
    )

    # compound(uint64 validatorId)
    comp_setup, comp_call = _call_no_value(
        SELECTOR_COMPOUND,
        CALLDATA_SIZE_COMPOUND,
        GAS_COMPOUND,
    )

    contract = (
        add_setup
        + Op.SSTORE(slot_add_val_success, add_call)
        + del_setup
        + Op.SSTORE(slot_delegate_success, del_call)
        + comp_setup
        + Op.SSTORE(slot_compound_success, comp_call)
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    contract_address = pre.deploy_contract(contract, balance=STAKE_AMOUNT * 2)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_add_val_success: 1,
                    slot_delegate_success: 1,
                    slot_compound_success: 1,
                    slot_code_worked: value_code_worked,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )


def test_claim_rewards(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test that claimRewards succeeds after delegation.

    Expected to FAIL against stub (claimRewards doesn't verify
    state).
    """
    # addValidator
    add_setup, add_call = _call_with_value(
        SELECTOR_ADD_VALIDATOR,
        CALLDATA_SIZE_ADD_VALIDATOR,
        GAS_ADD_VALIDATOR,
        value=STAKE_AMOUNT,
        ret_offset=256,
        ret_size=32,
    )

    # delegate
    del_setup, del_call = _call_with_value(
        SELECTOR_DELEGATE,
        CALLDATA_SIZE_DELEGATE,
        GAS_DELEGATE,
        value=STAKE_AMOUNT,
    )

    # claimRewards(uint64 validatorId)
    claim_setup, claim_call = _call_no_value(
        SELECTOR_CLAIM_REWARDS,
        CALLDATA_SIZE_CLAIM_REWARDS,
        GAS_CLAIM_REWARDS,
    )

    contract = (
        add_setup
        + Op.SSTORE(slot_add_val_success, add_call)
        + del_setup
        + Op.SSTORE(slot_delegate_success, del_call)
        + claim_setup
        + Op.SSTORE(slot_claim_success, claim_call)
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    contract_address = pre.deploy_contract(contract, balance=STAKE_AMOUNT * 2)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_add_val_success: 1,
                    slot_delegate_success: 1,
                    slot_claim_success: 1,
                    slot_code_worked: value_code_worked,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )
