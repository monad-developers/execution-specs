"""
Tests linear gas cost of the MIP-3 memory model.
"""

from typing import Generator

import pytest
from execution_testing import (
    Account,
    Alloc,
    Bytecode,
    Op,
    ParameterSet,
    StateTestFiller,
    Transaction,
    gas_test,
)
from execution_testing.forks.forks.forks import MONAD_NEXT
from execution_testing.forks.helpers import Fork
from execution_testing.vm import Opcode

from .helpers import COLD_ACCESS_TARGET_ADDRESS, prepare_stack_memory_opcode
from .spec import Spec, ref_spec_3

REFERENCE_SPEC_GIT_PATH = ref_spec_3.git_path
REFERENCE_SPEC_VERSION = ref_spec_3.version

slot_code_worked = 0x1
slot_gas_used = 0x2
value_code_worked = 0x1234

pytestmark = [
    pytest.mark.pre_alloc_group(
        "mip3_tests",
        reason="Tests linear memory MIP-3",
    ),
]


@pytest.mark.valid_from("MONAD_EIGHT")
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
) -> Generator[tuple[Op, int, int], None, None]:
    """
    Memory-reading opcodes which allocate memory.
    Includes copy, hashing, and logging opcodes.
    """
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
        # RETURNDATACOPY tested separately in test_returndatacopy_gas_cost
    }

    for opcode, warm_gas in memory_opcodes.items():
        # Cold/warm gas testing is outside of the scope of the test
        # Each test should warm accessed accounts in prelude_code
        cold_gas = warm_gas
        yield opcode, warm_gas, cold_gas


def memory_stack_opcodes(
    fork: Fork,
) -> Generator[ParameterSet, None, None]:
    """
    Stack-memory opcodes which always read at least 1 byte or 1 word.
    """
    gas_costs = fork.gas_costs()

    memory_opcodes = {
        Op.MLOAD: gas_costs.G_VERY_LOW,
        Op.MSTORE: gas_costs.G_VERY_LOW,
        Op.MSTORE8: gas_costs.G_VERY_LOW,
    }

    for opcode, warm_gas in memory_opcodes.items():
        cold_gas = warm_gas
        yield pytest.param(opcode, warm_gas, cold_gas, id=f"{opcode}")


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


@pytest.mark.valid_from("MONAD_EIGHT")
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
        prelude_code=Op.BALANCE(COLD_ACCESS_TARGET_ADDRESS),
        setup_code=setup_code,
        subject_code=opcode,
        tear_down_code=Op.STOP,
        cold_gas=cold_gas + dynamic_gas_cost + memory_expansion_cost,
        warm_gas=warm_gas + dynamic_gas_cost + memory_expansion_cost,
        # OOG testing depends on CALL status code, doesn't work with REVERT
        # or OOM.
        out_of_gas_testing=False
        if opcode == Op.REVERT or size > Spec.MAX_TX_MEMORY_USAGE
        else True,
    )


@pytest.mark.valid_from("MONAD_EIGHT")
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
        out_of_gas_testing=False if size > Spec.MAX_TX_MEMORY_USAGE else True,
    )


@pytest.mark.valid_from("MONAD_EIGHT")
@pytest.mark.parametrize_by_fork("size", memory_sizes)
@pytest.mark.parametrize(
    "initial_memory",
    [bytes(range(0x00, 0x100)), bytes()],
    ids=["from_existent_memory", "from_empty_memory"],
)
def test_returndatacopy_gas_cost(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    size: int,
    initial_memory: bytes,
) -> None:
    """
    Test that RETURNDATACOPY consumes correct gas under MIP-3.

    RETURNDATACOPY requires a return data buffer to exist, so we first call
    a contract that returns the required amount of data in the setup_code.
    """
    gas_costs = fork.gas_costs()
    cost_memory_bytes = fork.memory_expansion_gas_calculator()

    memory_expansion_cost = cost_memory_bytes(
        new_bytes=size,
        previous_bytes=len(initial_memory),
    )
    dynamic_gas_cost = gas_costs.G_COPY * ((size + 31) // 32)
    base_gas = gas_costs.G_VERY_LOW

    returner_address = pre.deploy_contract(Op.RETURN(0, size))

    setup_code = (
        Op.CALL(address=returner_address)
        + Op.CALLDATACOPY(0x00, 0x00, len(initial_memory))
        + Op.PUSH32(size)
        + Op.PUSH0
        + Op.PUSH0
    )

    gas_test(
        fork=fork,
        state_test=state_test,
        pre=pre,
        # Warm the address to CALL to have stable gas cost.
        prelude_code=Op.BALANCE(returner_address),
        setup_code=setup_code,
        subject_code=Op.RETURNDATACOPY,
        tear_down_code=Op.STOP,
        cold_gas=base_gas + dynamic_gas_cost + memory_expansion_cost,
        warm_gas=base_gas + dynamic_gas_cost + memory_expansion_cost,
        out_of_gas_testing=False if size > Spec.MAX_TX_MEMORY_USAGE else True,
    )


@pytest.mark.parametrize(
    "offsets,expected_memory_cost",
    [
        pytest.param([0], 0, id="offset_0_first_word_free"),
        pytest.param([15], 0, id="offset_15_first_word_free"),
        pytest.param([31], 0, id="offset_31_first_word_free"),
        pytest.param([32], 1, id="offset_32_second_word_costs"),
        pytest.param([33], 1, id="offset_33_second_word_costs"),
        pytest.param([63], 1, id="offset_63_second_word_costs"),
        pytest.param(
            [63, 95, 127],
            2,
            id="delta_even_odd_mstore",
        ),
        pytest.param(
            [63, 127],
            2,
            id="even_word_expansion",
        ),
        pytest.param(
            [31, 95],
            1,
            id="odd_word_expansion",
        ),
        pytest.param(
            [31, 63, 95, 127, 159],
            2,
            id="multiple_expansions_1word_5times",
        ),
        pytest.param(
            [159],
            2,
            id="single_expansion_5words",
        ),
    ],
)
@pytest.mark.valid_from("MONAD_NEXT")
def test_consecutive_expansions(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    offsets: list[int],
    expected_memory_cost: int,
) -> None:
    """
    Test consecutive memory expansions under MIP-3's `words // 2` formula.

    Verifies that:
    - First memory word is free (words // 2 = 0 for 1 word)
    - Delta charges work correctly for successive expansions
    - Even word expansions: 2 words cost 1, 4 words cost 2
    - Odd word expansions: 1 word cost 0, 3 words cost 1
    - Multiple small expansions equal one large expansion
    """
    gas_costs = fork.gas_costs()
    base_gas_per_op = gas_costs.G_VERY_LOW

    setup_code = Bytecode()
    for offset in reversed(offsets):
        setup_code += Op.PUSH1(0xFF) + Op.PUSH1(offset)

    subject_code = Bytecode()
    for _ in offsets:
        subject_code += Op.MSTORE8

    total_gas = len(offsets) * base_gas_per_op + expected_memory_cost

    gas_test(
        fork=fork,
        state_test=state_test,
        pre=pre,
        setup_code=setup_code,
        subject_code=subject_code,
        tear_down_code=Op.STOP,
        cold_gas=total_gas,
        warm_gas=total_gas,
    )
