"""
Tests for staking precompile getter return data.

Verify that getter functions return the expected stub data structures
including correct sizes, word values, and ABI encoding.
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

from .helpers import (
    CORRECT_SEL_ARGS_OFFSET,
    build_calldata,
    calldata_mem_end,
    generous_gas,
)
from .spec import (
    GETTER_FUNCTIONS,
    STAKING_PRECOMPILE,
    FunctionInfo,
    ref_spec_staking,
)

REFERENCE_SPEC_GIT_PATH = ref_spec_staking.git_path
REFERENCE_SPEC_VERSION = ref_spec_staking.version

slot_code_worked = 0x1
value_code_worked = 0x1234
slot_call_success = 0x2
slot_return_size = 0x3
# Slots 0x10..0x10+N store individual return words
slot_return_word_base = 0x10

pytestmark = [
    pytest.mark.valid_from("MONAD_EIGHT"),
    pytest.mark.pre_alloc_group(
        "staking_precompile_getter_tests",
        reason="Tests staking precompile getter return data",
    ),
]


@pytest.mark.parametrize(
    "func",
    [pytest.param(f, id=f.name) for f in GETTER_FUNCTIONS],
)
def test_getter_return_data(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    func: FunctionInfo,
    fork: Fork,
) -> None:
    """
    Test that each getter returns the expected data size and content.

    Call the getter and read back RETURNDATASIZE plus each 32-byte
    word of the return data.
    """
    num_words = func.return_size // 32
    rdc_offset = calldata_mem_end(func.calldata_size)

    contract = (
        build_calldata(func.selector, func.calldata_size)
        + Op.SSTORE(
            slot_call_success,
            Op.CALL(
                gas=func.gas_cost + 10000,
                address=STAKING_PRECOMPILE,
                args_offset=CORRECT_SEL_ARGS_OFFSET,
                args_size=func.calldata_size,
            ),
        )
        + Op.SSTORE(slot_return_size, Op.RETURNDATASIZE)
    )

    if num_words > 0:
        contract += Op.RETURNDATACOPY(rdc_offset, 0, Op.RETURNDATASIZE)
        contract += Op.SSTORE(slot_return_word_base, Op.MLOAD(rdc_offset))

    contract += Op.SSTORE(slot_code_worked, value_code_worked)

    contract_address = pre.deploy_contract(contract)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    storage: dict[int, int] = {
        slot_call_success: 1,
        slot_return_size: func.return_size,
        slot_code_worked: value_code_worked,
    }

    if num_words > 0:
        storage[slot_return_word_base] = func.first_return_word

    blockchain_test(
        pre=pre,
        post={contract_address: Account(storage=storage)},
        blocks=[Block(txs=[tx])],
    )


@pytest.mark.parametrize(
    "func",
    [pytest.param(f, id=f.name) for f in GETTER_FUNCTIONS],
)
def test_getter_idempotent(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    func: FunctionInfo,
    fork: Fork,
) -> None:
    """
    Test that calling a getter twice in the same transaction returns
    identical results both times.
    """
    slot_size_1 = 0x20
    slot_size_2 = 0x21
    slot_word_1 = 0x22
    slot_word_2 = 0x23
    slot_success_1 = 0x24
    slot_success_2 = 0x25

    rdc_offset = calldata_mem_end(func.calldata_size)

    contract = (
        build_calldata(func.selector, func.calldata_size)
        # First call
        + Op.SSTORE(
            slot_success_1,
            Op.CALL(
                gas=func.gas_cost + 10000,
                address=STAKING_PRECOMPILE,
                args_offset=CORRECT_SEL_ARGS_OFFSET,
                args_size=func.calldata_size,
            ),
        )
        + Op.SSTORE(slot_size_1, Op.RETURNDATASIZE)
        + Op.RETURNDATACOPY(rdc_offset, 0, Op.RETURNDATASIZE)
        + Op.SSTORE(slot_word_1, Op.MLOAD(rdc_offset))
        # Second call (same calldata still in memory)
        + Op.SSTORE(
            slot_success_2,
            Op.CALL(
                gas=func.gas_cost + 10000,
                address=STAKING_PRECOMPILE,
                args_offset=CORRECT_SEL_ARGS_OFFSET,
                args_size=func.calldata_size,
            ),
        )
        + Op.SSTORE(slot_size_2, Op.RETURNDATASIZE)
        + Op.RETURNDATACOPY(rdc_offset, 0, Op.RETURNDATASIZE)
        + Op.SSTORE(slot_word_2, Op.MLOAD(rdc_offset))
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    contract_address = pre.deploy_contract(contract)

    tx = Transaction(
        gas_limit=generous_gas(fork) + 2 * func.gas_cost,
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_success_1: 1,
                    slot_success_2: 1,
                    slot_size_1: func.return_size,
                    slot_size_2: func.return_size,
                    slot_word_1: func.first_return_word,
                    slot_word_2: func.first_return_word,
                    slot_code_worked: value_code_worked,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )
