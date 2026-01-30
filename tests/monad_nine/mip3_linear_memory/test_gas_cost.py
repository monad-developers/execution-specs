"""
Tests linear gas cost of the MIP-3 memory model.
"""

from typing import Generator

import pytest
from execution_testing import (
    Account,
    Alloc,
    Op,
    ParameterSet,
    StateTestFiller,
    Transaction,
    gas_test,
)
from execution_testing.forks.forks.forks import MONAD_NEXT
from execution_testing.forks.helpers import Fork
from execution_testing.vm import Opcode

from .helpers import prepare_stack_memory_opcode
from .spec import Spec, ref_spec_3

REFERENCE_SPEC_GIT_PATH = ref_spec_3.git_path
REFERENCE_SPEC_VERSION = ref_spec_3.version

slot_code_worked = 0x1
value_code_worked = 0x1234

pytestmark = [
    pytest.mark.valid_from("MONAD_EIGHT"),
    pytest.mark.pre_alloc_group(
        "mip3_tests",
        reason="Tests linear memory MIP-3",
    ),
]


@pytest.mark.parametrize("fail", [True, False])
def test_cost_non_quadratic(
    state_test: StateTestFiller,
    pre: Alloc,
    fail: bool,
    fork: Fork,
) -> None:
    """
    Simplest smoke test for checking if memory isn't quadratic cost anymore.
    """
    contract = Op.MLOAD(
        Spec.MAX_TX_MEMORY_USAGE - (0 if fail else 32)
    ) + Op.SSTORE(slot_code_worked, value_code_worked)
    contract_address = pre.deploy_contract(contract)

    tx = Transaction(
        gas_limit=fork.gas_costs().G_MEMORY * Spec.MAX_TX_MEMORY_USAGE // 32
        + 100_000,
        to=contract_address,
        sender=pre.fund_eoa(),
    )
    storage = (
        {slot_code_worked: value_code_worked}
        if not fail and fork >= MONAD_NEXT
        else {}
    )

    state_test(
        pre=pre,
        post={contract_address: Account(storage=storage)},
        tx=tx,
    )


def memory_copy_opcodes(
    fork: Fork,
) -> Generator[ParameterSet, None, None]:
    """
    Memory-reading opcodes which allocate memory.
    Includes copy, hashing, and logging opcodes.
    """
    valid_opcodes = set(fork.valid_opcodes())
    gas_costs = fork.gas_costs()

    memory_opcodes = {
        Op.CALLDATACOPY: gas_costs.G_VERY_LOW,
        Op.CODECOPY: gas_costs.G_VERY_LOW,
        Op.EXTCODECOPY: gas_costs.G_WARM_ACCOUNT_ACCESS,
        Op.MCOPY: gas_costs.G_VERY_LOW,
        Op.SHA3: gas_costs.G_KECCAK_256,
        Op.LOG0: gas_costs.G_LOG,
        Op.LOG1: gas_costs.G_LOG + gas_costs.G_LOG_TOPIC,
        Op.LOG2: gas_costs.G_LOG + 2 * gas_costs.G_LOG_TOPIC,
        Op.LOG3: gas_costs.G_LOG + 3 * gas_costs.G_LOG_TOPIC,
        Op.LOG4: gas_costs.G_LOG + 4 * gas_costs.G_LOG_TOPIC,
        Op.RETURN: 0,
        Op.REVERT: 0,
        Op.CREATE: gas_costs.G_CREATE,
        Op.CREATE2: gas_costs.G_CREATE,
        Op.CALL: gas_costs.G_WARM_ACCOUNT_ACCESS,
        Op.DELEGATECALL: gas_costs.G_WARM_ACCOUNT_ACCESS,
        Op.STATICCALL: gas_costs.G_WARM_ACCOUNT_ACCESS,
        Op.CALLCODE: gas_costs.G_WARM_ACCOUNT_ACCESS,
        # FIXME: this goes out of bounds, no way to setup return buffer easily
        # Op.RETURNDATACOPY: gas_costs.G_VERY_LOW,
    }

    cold_access_opcodes = (
        Op.EXTCODECOPY,
        Op.CALL,
        Op.DELEGATECALL,
        Op.STATICCALL,
        Op.CALLCODE,
    )

    for opcode, base_gas in memory_opcodes.items():
        if opcode not in valid_opcodes:
            continue
        cold_gas = base_gas
        if opcode in cold_access_opcodes:
            cold_gas = gas_costs.G_COLD_ACCOUNT_ACCESS
        yield opcode, base_gas, cold_gas


def memory_stack_opcodes(
    fork: Fork,
) -> Generator[ParameterSet, None, None]:
    """
    Stack-memory opcodes which always read at least 1 byte or 1 word.
    """
    valid_opcodes = set(fork.valid_opcodes())
    gas_costs = fork.gas_costs()

    memory_opcodes = {
        Op.MLOAD: gas_costs.G_VERY_LOW,
        Op.MSTORE: gas_costs.G_VERY_LOW,
        Op.MSTORE8: gas_costs.G_VERY_LOW,
    }

    for opcode, base_gas in memory_opcodes.items():
        if opcode not in valid_opcodes:
            continue
        cold_gas = base_gas
        yield pytest.param(opcode, base_gas, cold_gas, id=f"{opcode}")


def memory_sizes(
    fork: Fork,
) -> Generator[ParameterSet, None, None]:
    """
    Memory sizes to allocate up to during testing.
    """
    yield pytest.param(0x00, id="zero_bytes")
    yield pytest.param(0x01, id="single_byte")
    yield pytest.param(0x20, id="single_word")
    yield pytest.param(0x100, id="large_copy")
    yield pytest.param(0x2000, id="above_quadratic_threshold_copy")
    if fork >= MONAD_NEXT:
        yield pytest.param(Spec.MAX_TX_MEMORY_USAGE, id="max")


def memory_copy_opcodes_with_size(
    fork: Fork,
) -> Generator[ParameterSet, None, None]:
    """
    Memory-reading opcodes with appropriate size ranges.

    LOGn opcodes exclude the "max" size parameter, because its high per-byte
    cost doesn't fit in transaction gas limits. CREATEn opcodes exclude the
    "max" size parameter, because that exceeds max initcode size.
    """
    exclude_max_opcodes = [
        Op.LOG0,
        Op.LOG1,
        Op.LOG2,
        Op.LOG3,
        Op.LOG4,
        Op.CREATE,
        Op.CREATE2,
    ]

    for opcode, warm_gas, cold_gas in memory_copy_opcodes(fork):
        for size_param in memory_sizes(fork):
            if opcode in exclude_max_opcodes and size_param.id == "max":
                continue
            yield pytest.param(
                opcode,
                warm_gas,
                cold_gas,
                size_param.values[0],
                id=f"{opcode}-{size_param.id}",
            )


@pytest.mark.parametrize_by_fork(
    "opcode,warm_gas,cold_gas,size", memory_copy_opcodes_with_size
)
@pytest.mark.parametrize(
    "initial_memory",
    [
        bytes(range(0x00, 0x100)),
        bytes(),
    ],
    ids=["from_existent_memory", "from_empty_memory"],
)
def test_memory_copy_opcodes(
    state_test: StateTestFiller,
    pre: Alloc,
    opcode: Opcode,
    fork: Fork,
    warm_gas: int,
    cold_gas: int,
    size: int,
    initial_memory: bytes,
) -> None:
    """
    Test that memory-reading opcodes consume correct gas under MIP-3.

    Verifies that memory-reading opcodes (CALLDATACOPY, CODECOPY, EXTCODECOPY,
    MCOPY, SHA3, LOG0-LOG4, RETURN, REVERT, CREATE, CREATE2, CALL,
    DELEGATECALL, STATICCALL, CALLCODE) use linear gas costs for memory
    expansion instead of quadratic costs.

    `initial_memory` is the memory allocated before the measured opcode
    extends it.
    """
    cost_memory_bytes = fork.memory_expansion_gas_calculator()

    memory_expansion_cost = cost_memory_bytes(
        new_bytes=size,
        previous_bytes=len(initial_memory),
    )

    if opcode in (
        Op.CALLDATACOPY,
        Op.CODECOPY,
        Op.EXTCODECOPY,
        Op.MCOPY,
        Op.RETURNDATACOPY,
    ):
        dynamic_gas_cost = fork.gas_costs().G_COPY * ((size + 31) // 32)
    if opcode == Op.SHA3:
        dynamic_gas_cost = fork.gas_costs().G_KECCAK_256_WORD * (
            (size + 31) // 32
        )
    if opcode in (Op.LOG0, Op.LOG1, Op.LOG2, Op.LOG3, Op.LOG4):
        dynamic_gas_cost = fork.gas_costs().G_LOG_DATA * size
    if opcode in (Op.RETURN, Op.REVERT):
        dynamic_gas_cost = 0
    if opcode in (Op.CREATE, Op.CREATE2):
        init_code_cost = fork.gas_costs().G_INITCODE_WORD * ((size + 31) // 32)
        if opcode == Op.CREATE2:
            hash_cost = fork.gas_costs().G_KECCAK_256_WORD * (
                (size + 31) // 32
            )
            dynamic_gas_cost = init_code_cost + hash_cost
        else:
            dynamic_gas_cost = init_code_cost
    if opcode in (Op.CALL, Op.DELEGATECALL, Op.STATICCALL, Op.CALLCODE):
        dynamic_gas_cost = 0

    setup_code = Op.CALLDATACOPY(
        0x00, 0x00, len(initial_memory)
    ) + prepare_stack_memory_opcode(opcode, size)

    gas_test(
        fork=fork,
        state_test=state_test,
        pre=pre,
        setup_code=setup_code,
        subject_code=opcode,
        tear_down_code=Op.STOP,
        cold_gas=cold_gas + dynamic_gas_cost + memory_expansion_cost,
        warm_gas=warm_gas + dynamic_gas_cost + memory_expansion_cost,
        # OOG testing depends on CALL status code, doesn't work with REVERT.
        out_of_gas_testing=False if opcode == Op.REVERT else True,
    )


@pytest.mark.parametrize_by_fork(
    "opcode,warm_gas,cold_gas", memory_stack_opcodes
)
@pytest.mark.parametrize_by_fork("size", memory_sizes)
@pytest.mark.parametrize(
    "initial_memory",
    [bytes(range(0x00, 0x100)), bytes(range(0x00, 0x20))],
    ids=["from_existent_memory", "from_minimal_nonextendable_memory"],
)
def test_memory_stack_opcodes(
    state_test: StateTestFiller,
    pre: Alloc,
    opcode: Opcode,
    fork: Fork,
    warm_gas: int,
    cold_gas: int,
    size: int,
    initial_memory: bytes,
) -> None:
    """
    Test that stack-memory opcodes consume correct gas under MIP-3.

    Verifies that stack-memory opcodes (MLOAD, MSTORE, MSTORE8) use linear gas
    costs for memory expansion instead of quadratic costs.

    `initial_memory` is the memory allocated before the measured opcode
    extends it.
    """
    cost_memory_bytes = fork.memory_expansion_gas_calculator()

    memory_expansion_cost = cost_memory_bytes(
        new_bytes=size, previous_bytes=len(initial_memory)
    )

    setup_code = Op.CALLDATACOPY(
        0x00, 0x00, len(initial_memory)
    ) + prepare_stack_memory_opcode(opcode, size)

    gas_test(
        fork=fork,
        state_test=state_test,
        pre=pre,
        setup_code=setup_code,
        subject_code=opcode,
        tear_down_code=Op.STOP,
        cold_gas=cold_gas + memory_expansion_cost,
        warm_gas=warm_gas + memory_expansion_cost,
    )
