"""
Tests OOM (out-of-memory) behavior of the MIP-3 memory model.
"""

import itertools
from typing import List

import pytest
from execution_testing import (
    Account,
    Alloc,
    Op,
    StateTestFiller,
    Transaction,
)
from execution_testing.base_types.base_types import Address
from execution_testing.forks.helpers import Fork
from execution_testing.test_types.helpers import compute_create_address
from execution_testing.vm import Opcode

from .helpers import generous_gas, prepare_stack_memory_opcode
from .spec import Spec, ref_spec_3

REFERENCE_SPEC_GIT_PATH = ref_spec_3.git_path
REFERENCE_SPEC_VERSION = ref_spec_3.version

_slot = itertools.count(1)
slot_code_worked = next(_slot)
slot_call_result = next(_slot)
slot_inner_worked = next(_slot)
slot_all_gas_consumed = next(_slot)
slot_inner_gas_consumed = next(_slot)
slot_returndata_size_before = next(_slot)
slot_returndata_size_after = next(_slot)
slot_outer_memory_preserved = next(_slot)
slot_msize_before_call = next(_slot)
slot_msize_after_call = next(_slot)
slot_outer_msize = next(_slot)
slot_inner_msize_before = next(_slot)
slot_inner_msize_after = next(_slot)

value_code_worked = 0x1234
value_returndata_magic = 0xDEADBEEF
outer_memory_offset = 0x100
outer_marker_value = 0xDEADBEEF

pytestmark = [
    pytest.mark.valid_from("MONAD_NINE"),
    pytest.mark.pre_alloc_group(
        "mip3_tests",
        reason="Tests linear memory MIP-3",
    ),
]


@pytest.mark.parametrize(
    "offset",
    [
        pytest.param(Spec.MAX_TX_MEMORY_USAGE - 32, id="at_limit"),
        pytest.param(Spec.MAX_TX_MEMORY_USAGE - 31, id="exceed_by_1_byte"),
        pytest.param(Spec.MAX_TX_MEMORY_USAGE - 1, id="exceed_by_31_bytes"),
        pytest.param(Spec.MAX_TX_MEMORY_USAGE, id="exceed_by_1_word"),
        pytest.param(Spec.MAX_TX_MEMORY_USAGE + 1, id="exceed_by_33_bytes"),
        pytest.param(Spec.MAX_TX_MEMORY_USAGE + 1024 - 32, id="exceed_by_1KB"),
        pytest.param(2 * Spec.MAX_TX_MEMORY_USAGE - 32, id="exceed_by_8MB"),
        pytest.param(2**256 - 1, id="exceed_by_max"),
    ],
)
def test_top_level_oom(
    state_test: StateTestFiller,
    pre: Alloc,
    offset: int,
    fork: Fork,
) -> None:
    """
    Test that exceeding the 8 MiB memory limit causes the call to revert.
    """
    exceed = offset + 32 > Spec.MAX_TX_MEMORY_USAGE

    contract = Op.MLOAD(offset) + Op.SSTORE(
        slot_code_worked, value_code_worked
    )
    contract_address = pre.deploy_contract(contract)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    storage = {} if exceed else {slot_code_worked: value_code_worked}

    state_test(
        pre=pre,
        post={contract_address: Account(storage=storage)},
        tx=tx,
    )


@pytest.mark.parametrize("exceed", [True, False])
def test_top_level_oom_value_transfer(
    state_test: StateTestFiller,
    pre: Alloc,
    exceed: bool,
    fork: Fork,
) -> None:
    """
    Test that OOM reverts the call but gas is still charged.
    Value transfer only happens if the call succeeds.
    """
    offset = (
        Spec.MAX_TX_MEMORY_USAGE if exceed else Spec.MAX_TX_MEMORY_USAGE - 32
    )

    contract = Op.MLOAD(offset) + Op.SSTORE(
        slot_code_worked, value_code_worked
    )
    contract_address = pre.deploy_contract(contract)

    gas_limit = generous_gas(fork)
    gas_price = 100 * 10**9
    tx_value = 10**18
    gas_cost = gas_limit * gas_price
    initial_balance = gas_cost + 2 * tx_value

    sender = pre.fund_eoa(initial_balance)

    tx = Transaction(
        gas_limit=gas_limit,
        max_fee_per_gas=gas_price,
        max_priority_fee_per_gas=gas_price,
        to=contract_address,
        value=tx_value,
        sender=sender,
    )

    storage = {} if exceed else {slot_code_worked: value_code_worked}
    contract_balance = 0 if exceed else tx_value
    sender_balance = (
        initial_balance - gas_cost
        if exceed
        else initial_balance - gas_cost - tx_value
    )

    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage=storage, balance=contract_balance
            ),
            sender: Account(balance=sender_balance),
        },
        tx=tx,
    )


@pytest.mark.parametrize("exceed", [True, False])
@pytest.mark.with_all_call_opcodes
def test_nested_call_oom(
    state_test: StateTestFiller,
    pre: Alloc,
    exceed: bool,
    call_opcode: Op,
    fork: Fork,
) -> None:
    """
    Test OOM behavior in a nested call using various call opcodes.
    """
    offset = (
        Spec.MAX_TX_MEMORY_USAGE if exceed else Spec.MAX_TX_MEMORY_USAGE - 32
    )

    if call_opcode == Op.STATICCALL:
        inner_contract = Op.MLOAD(offset) + Op.STOP
    else:
        inner_contract = Op.MLOAD(offset) + Op.SSTORE(
            slot_inner_worked, value_code_worked
        )
    inner_address = pre.deploy_contract(inner_contract)

    outer_contract = Op.SSTORE(
        slot_call_result, call_opcode(address=inner_address)
    ) + Op.SSTORE(slot_code_worked, value_code_worked)
    outer_address = pre.deploy_contract(outer_contract)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=outer_address,
        sender=pre.fund_eoa(),
    )

    outer_storage = {
        slot_code_worked: value_code_worked,
        slot_call_result: 0 if exceed else 1,
    }
    if call_opcode in (Op.DELEGATECALL, Op.CALLCODE) and not exceed:
        outer_storage[slot_inner_worked] = value_code_worked

    inner_storage = {}
    if call_opcode == Op.CALL and not exceed:
        inner_storage[slot_inner_worked] = value_code_worked

    post = {outer_address: Account(storage=outer_storage)}
    if inner_storage:
        post[inner_address] = Account(storage=inner_storage)

    state_test(
        pre=pre,
        post=post,
        tx=tx,
    )


@pytest.mark.parametrize(
    "callee_code,expected_call_result,consumes_all_gas",
    [
        pytest.param(Op.STOP, 1, False, id="success"),
        pytest.param(Op.MLOAD(Spec.MAX_TX_MEMORY_USAGE), 0, True, id="oom"),
        pytest.param(Op.MLOAD(2**256 - 1), 0, True, id="oog_before_oom"),
        pytest.param(Op.REVERT(0, 0), 0, False, id="revert"),
        pytest.param(Op.INVALID, 0, True, id="invalid"),
    ],
)
def test_nested_call_gas_consumption(
    state_test: StateTestFiller,
    pre: Alloc,
    callee_code: Op,
    expected_call_result: int,
    consumes_all_gas: bool,
    fork: Fork,
) -> None:
    """
    Test gas consumption behavior of CALL with different callee outcomes.
    OOM should consume all gas, like OOG and INVALID.

    NOTE: OOM is indisinguishable from OOG
    """
    inner_address = pre.deploy_contract(callee_code)

    gas_limit = generous_gas(fork)
    gas_threshold = gas_limit // 64

    # Need to preset storage slots with non-zero values in order to have
    # cheaper SSTORE after the potentially OOGing Op.CALL
    outer_contract = (
        Op.SSTORE(slot_code_worked, value_code_worked)
        + Op.SSTORE(slot_call_result, 1)
        + Op.SSTORE(slot_all_gas_consumed, 1)
        + Op.SSTORE(slot_call_result, Op.CALL(address=inner_address))
        + Op.SSTORE(slot_all_gas_consumed, Op.LT(Op.GAS, gas_threshold))
    )
    outer_address = pre.deploy_contract(outer_contract)

    tx = Transaction(
        gas_limit=gas_limit,
        to=outer_address,
        sender=pre.fund_eoa(),
    )

    state_test(
        pre=pre,
        post={
            outer_address: Account(
                storage={
                    slot_code_worked: value_code_worked,
                    slot_call_result: expected_call_result,
                    slot_all_gas_consumed: 1 if consumes_all_gas else 0,
                }
            )
        },
        tx=tx,
    )


@pytest.mark.parametrize("exceed", [True, False])
@pytest.mark.with_all_create_opcodes
def test_nested_create_oom(
    state_test: StateTestFiller,
    pre: Alloc,
    exceed: bool,
    create_opcode: Op,
    fork: Fork,
) -> None:
    """
    Test OOM behavior in initcode executed via CREATE/CREATE2.
    """
    # We need to subtract a word extra because the outer frame allocated it
    # for the initcode!
    offset = (
        Spec.MAX_TX_MEMORY_USAGE - 32
        if exceed
        else Spec.MAX_TX_MEMORY_USAGE - 32 - 32
    )

    initcode = Op.MLOAD(offset) + Op.STOP
    initcode_bytes = bytes(initcode) + b"\x00" * (32 - (len(initcode) % 32))

    factory = (
        Op.MSTORE(0, Op.PUSH32(initcode_bytes))
        + Op.SSTORE(slot_call_result, create_opcode(size=len(initcode)))
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    factory_address = pre.deploy_contract(factory)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=factory_address,
        sender=pre.fund_eoa(),
    )
    new_contract_address = compute_create_address(
        address=factory_address,
        nonce=1,
        initcode=initcode,
        opcode=create_opcode,
    )

    factory_storage = {
        slot_code_worked: value_code_worked,
        slot_call_result: 0 if exceed else new_contract_address,
    }

    state_test(
        pre=pre,
        post={
            factory_address: Account(storage=factory_storage),
            new_contract_address: Account.NONEXISTENT
            if exceed
            else Account(code=b""),
        },
        tx=tx,
    )


@pytest.mark.parametrize("exceed", [True, False])
@pytest.mark.with_all_contract_creating_tx_types
def test_top_level_oom_creation_tx(
    state_test: StateTestFiller,
    pre: Alloc,
    exceed: bool,
    tx_type: int,
    fork: Fork,
) -> None:
    """
    Test OOM behavior in initcode of a contract creation transaction (to=None).
    """
    offset = (
        Spec.MAX_TX_MEMORY_USAGE if exceed else Spec.MAX_TX_MEMORY_USAGE - 32
    )

    initcode = Op.MLOAD(offset) + Op.STOP

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=None,
        ty=tx_type,
        sender=pre.fund_eoa(),
        data=initcode,
    )

    state_test(
        pre=pre,
        post={
            tx.created_contract: Account.NONEXISTENT
            if exceed
            else Account(code=b"")
        },
        tx=tx,
    )


@pytest.mark.parametrize(
    "opcode",
    [
        Op.CALLDATACOPY,
        Op.CODECOPY,
        Op.EXTCODECOPY,
        Op.MCOPY,
        Op.SHA3,
        Op.LOG0,
        Op.LOG1,
        Op.LOG2,
        Op.LOG3,
        Op.LOG4,
        Op.CREATE,
        Op.CREATE2,
        Op.MLOAD,
        Op.MSTORE,
        Op.MSTORE8,
        Op.CALL,
        Op.DELEGATECALL,
        Op.STATICCALL,
        Op.CALLCODE,
    ],
)
@pytest.mark.parametrize("exceed", [True, False])
def test_all_memory_opcodes_oom(
    state_test: StateTestFiller,
    pre: Alloc,
    opcode: Opcode,
    exceed: bool,
    fork: Fork,
) -> None:
    """
    Test OOM behavior for all memory-allocating opcodes.

    RETURNDATACOPY tested separately.
    """
    # LOG opcodes have high per-byte cost, CREATE opcodes have initcode size
    # limits
    small_size_opcodes = (
        Op.LOG0,
        Op.LOG1,
        Op.LOG2,
        Op.LOG3,
        Op.LOG4,
        Op.CREATE,
        Op.CREATE2,
    )
    if exceed:
        size = Spec.MAX_TX_MEMORY_USAGE + 32
    elif opcode in small_size_opcodes:
        size = 0x2000
    else:
        size = Spec.MAX_TX_MEMORY_USAGE - 32

    contract = (
        prepare_stack_memory_opcode(opcode, size)
        + opcode
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    contract_address = pre.deploy_contract(contract)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    storage = {} if exceed else {slot_code_worked: value_code_worked}

    state_test(
        pre=pre,
        post={contract_address: Account(storage=storage)},
        tx=tx,
    )


@pytest.mark.parametrize("exceed", [True, False])
def test_returndatacopy_oom(
    state_test: StateTestFiller,
    pre: Alloc,
    exceed: bool,
    fork: Fork,
) -> None:
    """
    Test OOM behavior for RETURNDATACOPY.

    For exceed=False: Call a contract that returns data of the target size,
    then use RETURNDATACOPY to copy it. This validates no OOM occurs.

    For exceed=True: We cannot create return data of that size because the
    RETURN opcode would OOM. Instead, skip the CALL and call RETURNDATACOPY
    directly. RETURNDATACOPY will fail with an out-of-buffer read error
    (not OOM) because there is no return data to copy from.
    """
    size = Spec.MAX_TX_MEMORY_USAGE + (1 if exceed else 0)

    contract = Op.SSTORE(slot_code_worked, value_code_worked)

    if not exceed:
        returner_address = pre.deploy_contract(Op.RETURN(0, size))

        contract += Op.CALL(address=returner_address)
    contract += Op.RETURNDATACOPY(0, 0, size)

    contract_address = pre.deploy_contract(contract)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    storage = {} if exceed else {slot_code_worked: value_code_worked}

    state_test(
        pre=pre,
        post={contract_address: Account(storage=storage)},
        tx=tx,
    )


@pytest.mark.parametrize("exceeds_at_depth", [1, 2, 3, 15, 128])
def test_nested_frames_oom(
    state_test: StateTestFiller,
    pre: Alloc,
    exceeds_at_depth: int,
    fork: Fork,
) -> None:
    """
    Test OOM behavior across nested call frames.

    Each frame allocates chunk_size = MAX / exceeds_at_depth bytes.
    Memory limit is checked cumulatively across all call frames.
    At the deepest frame, cumulative memory exceeds MAX and causes OOM.
    Each successful frame stores 1 to its depth slot.

    See test_oom_deep.py for a variant with MONAD_EIGHT comparison.
    """
    slot_depth = 0x100  # Base slot for depth markers

    # Add extra to ensure cumulative total exceeds MAX at deepest level
    chunk_size = (Spec.MAX_TX_MEMORY_USAGE // exceeds_at_depth) + 64

    # Deploy contracts from deepest to shallowest
    addresses: List[Address] = []
    for depth in range(exceeds_at_depth - 1, -1, -1):
        callee = addresses[-1] if addresses else Address(0x0)
        contract = (
            Op.SSTORE(slot_depth + depth, 1)
            + Op.MLOAD(chunk_size - 32)
            # Use DELEGATECALL so storage writes go to entry contract
            + Op.DELEGATECALL(address=callee)
        )
        addresses.append(pre.deploy_contract(contract))

    entry_address = addresses[-1]

    tx = Transaction(
        gas_limit=fork.transaction_gas_limit_cap()
        if exceeds_at_depth > 16
        else generous_gas(fork),
        to=entry_address,
        sender=pre.fund_eoa(),
    )

    # Depths 0 through exceeds_at_depth-2 succeed
    # Depth exceeds_at_depth-1 fails due to OOM
    storage = {slot_depth + d: 1 for d in range(exceeds_at_depth - 1)}

    state_test(
        pre=pre,
        post={entry_address: Account(storage=storage)},
        tx=tx,
    )


@pytest.mark.parametrize(
    "chunk_size,expected_successful_calls",
    [
        (Spec.MAX_TX_MEMORY_USAGE // 8, 8),
        (Spec.MAX_TX_MEMORY_USAGE // 4, 4),
        (Spec.MAX_TX_MEMORY_USAGE // 2, 2),
    ],
)
def test_recursive_frames_oom(
    state_test: StateTestFiller,
    pre: Alloc,
    chunk_size: int,
    expected_successful_calls: int,
    fork: Fork,
) -> None:
    """
    Test recursive calls until cumulative memory exceeds limit.
    """
    slot_depth_base = 0x100
    slot_counter = next(_slot)

    code_increment_counter = (
        Op.TLOAD(slot_counter)
        + Op.DUP1
        + Op.TSTORE(slot_counter, Op.PUSH1(1) + Op.ADD)
    )
    contract = (
        Op.SSTORE(Op.ADD(slot_depth_base, code_increment_counter), 1)
        + Op.MLOAD(chunk_size - 32)
        + Op.DELEGATECALL(address=Op.ADDRESS)
    )
    contract_address = pre.deploy_contract(contract)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    storage = {
        slot_depth_base + d: 1 for d in range(expected_successful_calls)
    }

    state_test(
        pre=pre,
        post={contract_address: Account(storage=storage)},
        tx=tx,
    )


def test_inner_frame_incremental_memory_allocation(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test repeated calls to inner contract with increasing memory allocation.

    Tests memory recovery after OOM and successful inner call releases memory.

    The outer frame allocates 32 bytes for calldata (MSTORE). The inner
    contract computes: offset = target - 64, then MLOAD(offset) which
    allocates (offset + 32) bytes. Total = 32 + (offset + 32) = target.
    """
    inner_contract = Op.MLOAD(Op.CALLDATALOAD(0))
    inner_address = pre.deploy_contract(inner_contract)

    sizes = [
        Spec.MAX_TX_MEMORY_USAGE,
        Spec.MAX_TX_MEMORY_USAGE // 4,
        Spec.MAX_TX_MEMORY_USAGE // 2,
        Spec.MAX_TX_MEMORY_USAGE // 2,
        Spec.MAX_TX_MEMORY_USAGE - 32,
        Spec.MAX_TX_MEMORY_USAGE,
        Spec.MAX_TX_MEMORY_USAGE + 32,
        2 * Spec.MAX_TX_MEMORY_USAGE,
    ]

    outer = Op.SSTORE(slot_code_worked, value_code_worked)
    for size in sizes:
        outer = (
            # pre-allocate for MSIZE to work
            Op.MSTORE(0, 0)
            # store size to allocate in the inner frame, minus
            # size of MLOAD allocation, minus current allocation
            + Op.MSTORE(0, Op.SUB(size - 32, Op.MSIZE))
            + Op.SSTORE(
                size,
                Op.CALL(
                    gas=Op.DIV(Op.GAS, len(sizes)),
                    address=inner_address,
                    args_size=32,
                ),
            )
        ) + outer

    outer_address = pre.deploy_contract(outer)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=outer_address,
        sender=pre.fund_eoa(),
    )

    storage = {slot_code_worked: value_code_worked}
    for size in sizes:
        storage[size] = 1 if size <= Spec.MAX_TX_MEMORY_USAGE else 0

    state_test(
        pre=pre,
        post={outer_address: Account(storage=storage)},
        tx=tx,
    )


@pytest.mark.parametrize(
    "inner_exit",
    [
        pytest.param(Op.MLOAD(Spec.MAX_TX_MEMORY_USAGE), id="oom"),
        Op.STOP,
        Op.RETURN(0, 0),
        Op.REVERT(0, 0),
        Op.INVALID,
        Op.SELFDESTRUCT,
    ],
)
def test_inner_frame_memory_wipe(
    state_test: StateTestFiller,
    pre: Alloc,
    inner_exit: Op,
    fork: Fork,
) -> None:
    """
    Test that inner frame memory is wiped after call ends, outer frame memory
    is preserved, and MSIZE reflects the outer frame's allocation only.
    """
    inner_contract = Op.MLOAD(0) + inner_exit
    inner_address = pre.deploy_contract(inner_contract)

    expected_msize = outer_memory_offset + 32

    # Preset storage slots for cheaper SSTORE after gas-consuming CALL
    outer_contract = (
        Op.SSTORE(slot_msize_after_call, 1)
        + Op.SSTORE(slot_outer_memory_preserved, 1)
        + Op.SSTORE(slot_code_worked, 1)
        + Op.MSTORE(outer_memory_offset, outer_marker_value)
        + Op.SSTORE(slot_msize_before_call, Op.MSIZE)
        + Op.CALL(address=inner_address)
        + Op.SSTORE(slot_msize_after_call, Op.MSIZE)
        + Op.SSTORE(slot_outer_memory_preserved, Op.MLOAD(outer_memory_offset))
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    outer_address = pre.deploy_contract(outer_contract)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=outer_address,
        sender=pre.fund_eoa(),
    )

    storage = {
        slot_code_worked: value_code_worked,
        slot_msize_before_call: expected_msize,
        slot_msize_after_call: expected_msize,
        slot_outer_memory_preserved: outer_marker_value,
    }

    state_test(
        pre=pre,
        post={outer_address: Account(storage=storage)},
        tx=tx,
    )


@pytest.mark.parametrize("oom_opcode", [Op.MLOAD, Op.RETURN, Op.REVERT])
@pytest.mark.parametrize("exceed", [True, False])
def test_oom_clears_returndata(
    state_test: StateTestFiller,
    pre: Alloc,
    exceed: bool,
    oom_opcode: Op,
    fork: Fork,
) -> None:
    """
    Test that OOM clears the returndata buffer.

    Includes also OOMing opcodes which otherwise would _set_ returndata.
    """
    filling_contract_code = Op.MSTORE(0, value_returndata_magic) + Op.RETURN(
        0, 32
    )
    filling_callee_address = pre.deploy_contract(filling_contract_code)

    offset = (
        Spec.MAX_TX_MEMORY_USAGE if exceed else Spec.MAX_TX_MEMORY_USAGE - 32
    )

    oom_callee_address = pre.deploy_contract(
        oom_opcode(offset)
        if oom_opcode == Op.MLOAD
        else oom_opcode(offset, 32)
    )

    outer_contract = (
        Op.CALL(address=filling_callee_address)
        + Op.SSTORE(slot_returndata_size_before, Op.RETURNDATASIZE)
        + Op.CALL(address=oom_callee_address)
        + Op.SSTORE(slot_returndata_size_after, Op.RETURNDATASIZE)
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    outer_address = pre.deploy_contract(outer_contract)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=outer_address,
        sender=pre.fund_eoa(),
    )

    expected_returndata_after = 0 if exceed or oom_opcode == Op.MLOAD else 32

    storage = {
        slot_code_worked: value_code_worked,
        slot_returndata_size_before: 32,
        slot_returndata_size_after: expected_returndata_after,
    }

    state_test(
        pre=pre,
        post={outer_address: Account(storage=storage)},
        tx=tx,
    )


@pytest.mark.parametrize(
    "inner_exit",
    [
        pytest.param(Op.MLOAD(Spec.MAX_TX_MEMORY_USAGE - 32), id="oom"),
        Op.STOP,
        Op.RETURN(0, 0),
        Op.REVERT(0, 0),
        Op.INVALID,
    ],
)
@pytest.mark.with_all_create_opcodes
def test_inner_frame_memory_wipe_create(
    state_test: StateTestFiller,
    pre: Alloc,
    inner_exit: Op,
    create_opcode: Op,
    fork: Fork,
) -> None:
    """
    Test that CREATE/CREATE2 frame memory is wiped after initcode ends,
    outer frame memory is preserved, and MSIZE reflects outer allocation only.
    """
    initcode = Op.MLOAD(0) + inner_exit
    initcode_bytes = bytes(initcode) + b"\x00" * (32 - (len(initcode) % 32))

    expected_msize = outer_memory_offset + 32

    # Preset storage slots for cheaper SSTORE after gas-consuming CREATE
    outer_contract = (
        Op.SSTORE(slot_msize_after_call, 1)
        + Op.SSTORE(slot_outer_memory_preserved, 1)
        + Op.SSTORE(slot_code_worked, 1)
        + Op.MSTORE(outer_memory_offset, outer_marker_value)
        + Op.SSTORE(slot_msize_before_call, Op.MSIZE)
        + Op.MSTORE(0, Op.PUSH32(initcode_bytes))
        + create_opcode(size=len(initcode))
        + Op.SSTORE(slot_msize_after_call, Op.MSIZE)
        + Op.SSTORE(slot_outer_memory_preserved, Op.MLOAD(outer_memory_offset))
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    outer_address = pre.deploy_contract(outer_contract)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=outer_address,
        sender=pre.fund_eoa(),
    )

    storage = {
        slot_code_worked: value_code_worked,
        slot_msize_before_call: expected_msize,
        slot_msize_after_call: expected_msize,
        slot_outer_memory_preserved: outer_marker_value,
    }

    state_test(
        pre=pre,
        post={outer_address: Account(storage=storage)},
        tx=tx,
    )


@pytest.mark.parametrize("exceed", [True, False])
@pytest.mark.with_all_create_opcodes
def test_oom_clears_returndata_create(
    state_test: StateTestFiller,
    pre: Alloc,
    exceed: bool,
    create_opcode: Op,
    fork: Fork,
) -> None:
    """
    Test that OOM in CREATE/CREATE2 initcode clears the returndata buffer.
    """
    filling_contract_code = Op.MSTORE(0, value_returndata_magic) + Op.RETURN(
        0, 32
    )
    filling_callee_address = pre.deploy_contract(filling_contract_code)

    # Subtract 32 because outer frame uses memory for initcode
    offset = (
        Spec.MAX_TX_MEMORY_USAGE - 32
        if exceed
        else Spec.MAX_TX_MEMORY_USAGE - 32 - 32
    )

    initcode = Op.MLOAD(offset)
    initcode_bytes = bytes(initcode) + b"\x00" * (32 - (len(initcode) % 32))

    outer_contract = (
        Op.CALL(address=filling_callee_address)
        + Op.SSTORE(slot_returndata_size_before, Op.RETURNDATASIZE)
        + Op.MSTORE(0, Op.PUSH32(initcode_bytes))
        + create_opcode(size=len(initcode))
        + Op.SSTORE(slot_returndata_size_after, Op.RETURNDATASIZE)
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    outer_address = pre.deploy_contract(outer_contract)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=outer_address,
        sender=pre.fund_eoa(),
    )

    storage = {
        slot_code_worked: value_code_worked,
        slot_returndata_size_before: 32,
        slot_returndata_size_after: 0,
    }

    state_test(
        pre=pre,
        post={outer_address: Account(storage=storage)},
        tx=tx,
    )


@pytest.mark.parametrize("exceed", [True, False])
@pytest.mark.with_all_create_opcodes
def test_create_return_oom(
    state_test: StateTestFiller,
    pre: Alloc,
    exceed: bool,
    create_opcode: Op,
    fork: Fork,
) -> None:
    """
    Test OOM in CREATE's RETURN phase (not in initcode execution).

    Factory allocates some memory, then initcode tries to RETURN large data.
    The RETURN itself causes OOM, not the initcode execution.
    """
    factory_alloc = Spec.MAX_TX_MEMORY_USAGE

    return_size = 32 if exceed else 0

    initcode = Op.RETURN(0, return_size)
    initcode_bytes = bytes(initcode) + b"\x00" * (32 - (len(initcode) % 32))

    factory = (
        Op.MLOAD(factory_alloc - 32)
        + Op.MSTORE(0, Op.PUSH32(initcode_bytes))
        + Op.SSTORE(slot_call_result, create_opcode(size=len(initcode)))
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    factory_address = pre.deploy_contract(factory)
    new_contract_address = compute_create_address(
        address=factory_address,
        nonce=1,
        initcode=initcode,
        opcode=create_opcode,
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=factory_address,
        sender=pre.fund_eoa(),
    )

    storage = {
        slot_code_worked: value_code_worked,
        slot_call_result: 0 if exceed else new_contract_address,
    }

    state_test(
        pre=pre,
        post={factory_address: Account(storage=storage)},
        tx=tx,
    )


@pytest.mark.parametrize(
    "outer_alloc_size,inner_alloc_size",
    [
        pytest.param(0, 0, id="zero_zero"),
        pytest.param(0, 1, id="zero_one"),
        pytest.param(1, 0, id="one_zero"),
        pytest.param(1, 1, id="single_byte_both"),
        pytest.param(32, 32, id="word_both"),
        pytest.param(256, 512, id="outer_smaller"),
        pytest.param(512, 256, id="inner_smaller"),
        pytest.param(4096, 4096, id="equal_4KB"),
        pytest.param(
            Spec.MAX_TX_MEMORY_USAGE // 2,
            Spec.MAX_TX_MEMORY_USAGE // 2,
            id="half_half",
        ),
        pytest.param(
            Spec.MAX_TX_MEMORY_USAGE - 32,
            1,
            id="almostlimit_one",
        ),
        pytest.param(
            1,
            Spec.MAX_TX_MEMORY_USAGE - 32,
            id="one_almostlimit",
        ),
        pytest.param(33, 33, id="one_past_word_both"),
        pytest.param(
            31, 65, id="one_before_word_outer_one_past_two_words_inner"
        ),
        pytest.param(256, 257, id="outer_aligned_inner_unaligned"),
        pytest.param(257, 256, id="outer_unaligned_inner_aligned"),
    ],
)
def test_msize_across_frames(
    state_test: StateTestFiller,
    pre: Alloc,
    outer_alloc_size: int,
    inner_alloc_size: int,
    fork: Fork,
) -> None:
    """
    Test MSIZE behavior in outer and inner frames with different allocations.

    Uses CALLDATACOPY for precise allocation sizes. Allocations always round
    up to the nearest 32-byte word boundary, which is reflected by MSIZE.
    """
    inner_contract = (
        Op.SSTORE(slot_inner_msize_before, Op.MSIZE)
        + Op.CALLDATACOPY(0, 0, inner_alloc_size)
        + Op.SSTORE(slot_inner_msize_after, Op.MSIZE)
        + Op.STOP
    )
    inner_address = pre.deploy_contract(inner_contract)

    outer_contract = (
        Op.CALLDATACOPY(0, 0, outer_alloc_size)
        + Op.SSTORE(slot_outer_msize, Op.MSIZE)
        + Op.CALL(address=inner_address)
        + Op.SSTORE(slot_msize_after_call, Op.MSIZE)
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    outer_address = pre.deploy_contract(outer_contract)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=outer_address,
        sender=pre.fund_eoa(),
    )

    outer_msize = ((outer_alloc_size + 31) // 32) * 32
    inner_msize = ((inner_alloc_size + 31) // 32) * 32

    outer_storage = {
        slot_code_worked: value_code_worked,
        slot_outer_msize: outer_msize,
        slot_msize_after_call: outer_msize,
    }

    inner_storage = {
        slot_inner_msize_before: 0,
        slot_inner_msize_after: inner_msize,
    }

    state_test(
        pre=pre,
        post={
            outer_address: Account(storage=outer_storage),
            inner_address: Account(storage=inner_storage),
        },
        tx=tx,
    )


@pytest.mark.parametrize(
    "outer_alloc_size,inner_alloc_size",
    [
        pytest.param(
            Spec.MAX_TX_MEMORY_USAGE // 2 + 1,
            Spec.MAX_TX_MEMORY_USAGE // 2 - 31,
            id="halfplus_halfminus",
        ),
        pytest.param(
            Spec.MAX_TX_MEMORY_USAGE // 2 - 31,
            Spec.MAX_TX_MEMORY_USAGE // 2 + 1,
            id="halfminus_halfplus",
        ),
        pytest.param(
            1,
            Spec.MAX_TX_MEMORY_USAGE - 31,
            id="one_limitminus",
        ),
        pytest.param(
            Spec.MAX_TX_MEMORY_USAGE - 31,
            1,
            id="limitminus_one",
        ),
    ],
)
def test_memory_word_rounding_at_limit(
    state_test: StateTestFiller,
    pre: Alloc,
    outer_alloc_size: int,
    inner_alloc_size: int,
    fork: Fork,
) -> None:
    """
    Test that memory usage is rounded up to the nearest 32-byte word.

    Inner and outer frames technically allocate less than limit, but
    due to rounding limit is exceeded.
    """
    assert outer_alloc_size + inner_alloc_size <= Spec.MAX_TX_MEMORY_USAGE

    inner_contract = Op.CALLDATACOPY(0, 0, inner_alloc_size) + Op.STOP
    inner_address = pre.deploy_contract(inner_contract)

    outer_contract = (
        Op.CALLDATACOPY(0, 0, outer_alloc_size)
        + Op.SSTORE(slot_call_result, Op.CALL(address=inner_address))
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    outer_address = pre.deploy_contract(outer_contract)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=outer_address,
        sender=pre.fund_eoa(),
    )

    # Memory is rounded to nearest word, so inner allocation fails
    storage = {
        slot_code_worked: value_code_worked,
        slot_call_result: 0,
    }

    state_test(
        pre=pre,
        post={outer_address: Account(storage=storage)},
        tx=tx,
    )


@pytest.mark.parametrize("exceed", [True, False])
@pytest.mark.parametrize("trigger_oog", [True, False])
@pytest.mark.parametrize(
    "gas_cost_type",
    ["account_create", "value_transfer", "access_cost", "memory_expansion"],
)
def test_charge_gas_before_oom_check(
    state_test: StateTestFiller,
    pre: Alloc,
    gas_cost_type: str,
    exceed: bool,
    trigger_oog: bool,
    fork: Fork,
) -> None:
    """
    Test that charge_gas happens BEFORE OOM check in CALL opcode.

    Sets up an inner CALL that will OOG if trigger_oog=True (due to various
    gas costs), with a return buffer that causes OOM if exceed=True.

    If charge_gas runs before OOM check, OOG happens regardless of exceed.

    NOTE: OOM is indisinguishable from OOG
    """
    gas_limit = generous_gas(fork)
    gas_costs = fork.gas_costs()

    target: Address

    if gas_cost_type == "account_create":
        target = pre.fund_eoa(amount=0 if trigger_oog else 1)
        call_value = 1
        inner_offset = 32
        inner_gas = (
            7 * gas_costs.GAS_VERY_LOW
            + gas_costs.GAS_WARM_ACCOUNT_ACCESS
            + gas_costs.GAS_CALL_VALUE
            + gas_costs.GAS_NEW_ACCOUNT
            + fork.memory_expansion_gas_calculator()(new_bytes=inner_offset)
            - 1
        )
        warm_target = True
    elif gas_cost_type == "value_transfer":
        target = pre.fund_eoa(amount=1)
        call_value = 1 if trigger_oog else 0
        inner_offset = 32
        inner_gas = (
            7 * gas_costs.GAS_VERY_LOW
            + gas_costs.GAS_WARM_ACCOUNT_ACCESS
            + gas_costs.GAS_CALL_VALUE
            + fork.memory_expansion_gas_calculator()(new_bytes=inner_offset)
            - 1
        )
        warm_target = True
    elif gas_cost_type == "access_cost":
        target = pre.nonexistent_account()
        call_value = 0
        inner_offset = 32
        inner_gas = (
            7 * gas_costs.GAS_VERY_LOW
            + gas_costs.GAS_COLD_ACCOUNT_ACCESS
            + fork.memory_expansion_gas_calculator()(new_bytes=inner_offset)
            - 1
        )
        warm_target = not trigger_oog
    elif gas_cost_type == "memory_expansion":
        target = pre.fund_eoa(amount=1)
        call_value = 0
        inner_offset = 1024 * 1024 if trigger_oog else 32
        inner_gas = (
            7
            + gas_costs.GAS_VERY_LOW
            + gas_costs.GAS_WARM_ACCOUNT_ACCESS
            + fork.memory_expansion_gas_calculator()(new_bytes=1024 * 1024)
            - 1
        )
        warm_target = True
    else:
        raise Exception(f"Unknown scenario: {gas_cost_type}")

    # ret_size=inner_offset is allocating extra to cause OOM if exceed.
    inner_contract = Op.CALL(
        gas=0, address=target, value=call_value, ret_size=inner_offset
    )
    inner_address = pre.deploy_contract(inner_contract)

    offset = (
        Spec.MAX_TX_MEMORY_USAGE
        if exceed
        else Spec.MAX_TX_MEMORY_USAGE - inner_offset
    )
    mem_gas, mem_result = 0, 32

    outer_contract = Op.MLOAD(offset - inner_offset) + Op.POP(
        Op.BALANCE(inner_address)
    )
    if warm_target:
        outer_contract += Op.POP(Op.BALANCE(target))

    outer_contract += (
        # use MSTORE to avoid expensive SSTOREs in the test
        Op.MSTORE(mem_gas, Op.GAS)
        + Op.MSTORE(
            mem_result, Op.DELEGATECALL(gas=inner_gas, address=inner_address)
        )
        + Op.SSTORE(
            slot_inner_gas_consumed,
            Op.LT(
                inner_gas
                + gas_costs.GAS_WARM_ACCOUNT_ACCESS
                + gas_costs.GAS_VERY_LOW * 8
                + gas_costs.GAS_COPY,
                Op.SUB(Op.MLOAD(mem_gas), Op.GAS),
            ),
        )
        + Op.SSTORE(slot_call_result, Op.MLOAD(mem_result))
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    outer_address = pre.deploy_contract(outer_contract, balance=10**18)

    state_test(
        pre=pre,
        post={
            outer_address: Account(
                storage={
                    slot_code_worked: value_code_worked,
                    slot_call_result: 0 if exceed or trigger_oog else 1,
                    # OOM indistinguishable from OOG
                    slot_inner_gas_consumed: 1 if exceed or trigger_oog else 0,
                }
            )
        },
        tx=Transaction(
            gas_limit=gas_limit, to=outer_address, sender=pre.fund_eoa()
        ),
    )


@pytest.mark.parametrize("exceed", [True, False])
@pytest.mark.parametrize("static_violation", [True, False])
def test_static_check_after_oom_check(
    state_test: StateTestFiller,
    pre: Alloc,
    exceed: bool,
    static_violation: bool,
    fork: Fork,
) -> None:
    """
    Test that static call violation check happens AFTER OOM check.

    If OOM check runs before static check, OOM happens first when exceed=True.

    NOTE: OOM is indisinguishable from OOG
    """
    gas_limit = generous_gas(fork)
    offset = (
        Spec.MAX_TX_MEMORY_USAGE if exceed else Spec.MAX_TX_MEMORY_USAGE - 32
    )
    gas_threshold = gas_limit // 64
    warm_account = pre.nonexistent_account()

    # ret_size=32 is allocating the extra 32 bytes to cause OOM if exceed.
    inner_contract = Op.CALL(
        address=warm_account, value=1 if static_violation else 0, ret_size=32
    )
    inner_address = pre.deploy_contract(inner_contract, balance=1)

    outer_contract = (
        Op.MLOAD(offset - 32)
        + Op.POP(Op.BALANCE(warm_account))
        + Op.POP(Op.BALANCE(inner_address))
        + Op.SSTORE(slot_call_result, 123)
        + Op.SSTORE(slot_all_gas_consumed, 123)
        + Op.SSTORE(slot_call_result, Op.STATICCALL(address=inner_address))
        + Op.SSTORE(slot_all_gas_consumed, Op.LT(Op.GAS, gas_threshold))
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    outer_address = pre.deploy_contract(outer_contract)

    state_test(
        pre=pre,
        post={
            outer_address: Account(
                storage={
                    slot_code_worked: value_code_worked,
                    slot_call_result: 0 if exceed or static_violation else 1,
                    # OOM indistinguishable from OOG
                    slot_all_gas_consumed: 1
                    if exceed or static_violation
                    else 0,
                }
            )
        },
        tx=Transaction(
            gas_limit=gas_limit, to=outer_address, sender=pre.fund_eoa()
        ),
    )


@pytest.mark.parametrize("exceed", [True, False])
@pytest.mark.parametrize("out_of_bounds", [True, False])
def test_returndatacopy_check_after_oom_check(
    state_test: StateTestFiller,
    pre: Alloc,
    exceed: bool,
    out_of_bounds: bool,
    fork: Fork,
) -> None:
    """
    Test that returndatacopy out-of-bounds check happens AFTER OOM check.

    OOM happens first when exceed=True.

    NOTE: OOM is indisinguishable from OOG
    """
    gas_limit = generous_gas(fork)
    returner_size = 64
    offset = (
        Spec.MAX_TX_MEMORY_USAGE - returner_size
        if exceed
        else Spec.MAX_TX_MEMORY_USAGE - returner_size - 32
    )
    gas_threshold = gas_limit // 64
    pre.nonexistent_account()

    returner_address = pre.deploy_contract(Op.RETURN(0, returner_size))

    # ret_size=32 is allocating the extra 32 bytes to cause OOM if exceed.
    inner_contract = Op.CALL(
        address=Address(0x0111) if out_of_bounds else returner_address
    )
    copy_offset = 32
    assert offset + returner_size <= Spec.MAX_TX_MEMORY_USAGE
    assert exceed == (
        offset + copy_offset + returner_size > Spec.MAX_TX_MEMORY_USAGE
    )
    inner_contract += Op.RETURNDATACOPY(copy_offset, 0, returner_size)
    inner_address = pre.deploy_contract(inner_contract)

    outer_contract = (
        Op.MLOAD(offset - 32)
        + Op.SSTORE(slot_call_result, 123)
        + Op.SSTORE(slot_all_gas_consumed, 123)
        + Op.SSTORE(slot_call_result, Op.CALL(address=inner_address))
        + Op.SSTORE(slot_all_gas_consumed, Op.LT(Op.GAS, gas_threshold))
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    outer_address = pre.deploy_contract(outer_contract)

    state_test(
        pre=pre,
        post={
            outer_address: Account(
                storage={
                    slot_code_worked: value_code_worked,
                    slot_call_result: 0 if exceed or out_of_bounds else 1,
                    # OOM indistinguishable from OOG
                    slot_all_gas_consumed: 1 if exceed or out_of_bounds else 0,
                }
            )
        },
        tx=Transaction(
            gas_limit=gas_limit, to=outer_address, sender=pre.fund_eoa()
        ),
    )


@pytest.mark.parametrize("exceed", [True, False])
@pytest.mark.parametrize("insufficient_balance", [True, False])
def test_balance_check_after_oom_check(
    state_test: StateTestFiller,
    pre: Alloc,
    exceed: bool,
    insufficient_balance: bool,
    fork: Fork,
) -> None:
    """
    Test that balance check happens AFTER OOM check.

    If OOM check runs before balance check, OOM happens first when exceed=True.
    """
    gas_limit = generous_gas(fork)
    offset = (
        Spec.MAX_TX_MEMORY_USAGE if exceed else Spec.MAX_TX_MEMORY_USAGE - 32
    )
    gas_threshold = gas_limit // 64

    warm_account = pre.nonexistent_account()
    # ret_size=32 is allocating the extra 32 bytes to cause OOM if exceed.
    inner_contract = Op.CALL(
        gas=0,
        address=warm_account,
        value=1,
        ret_size=32,
    )
    inner_address = pre.deploy_contract(inner_contract)

    outer_contract = (
        Op.MLOAD(offset - 32)
        + Op.POP(Op.BALANCE(warm_account))
        + Op.POP(Op.BALANCE(inner_address))
        + Op.SSTORE(slot_call_result, 123)
        + Op.SSTORE(slot_all_gas_consumed, 123)
        + Op.SSTORE(slot_call_result, Op.DELEGATECALL(address=inner_address))
        + Op.SSTORE(slot_all_gas_consumed, Op.LT(Op.GAS, gas_threshold))
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    outer_address = pre.deploy_contract(
        outer_contract, balance=0 if insufficient_balance else 1
    )

    state_test(
        pre=pre,
        post={
            outer_address: Account(
                storage={
                    slot_code_worked: value_code_worked,
                    # outer call fails first if OOM, otherwise outer call ok
                    slot_call_result: 0 if exceed else 1,
                    # in either case not all gas is consumed
                    slot_all_gas_consumed: 1 if exceed else 0,
                }
            )
        },
        tx=Transaction(
            gas_limit=gas_limit, to=outer_address, sender=pre.fund_eoa()
        ),
    )


@pytest.mark.parametrize("exceed", [True, False])
@pytest.mark.parametrize(
    "log_opcode",
    [
        Op.LOG0,
        Op.LOG1,
        Op.LOG2,
        Op.LOG3,
        Op.LOG4,
    ],
)
def test_oom_check_ordering_static_log(
    state_test: StateTestFiller,
    pre: Alloc,
    log_opcode: Op,
    exceed: bool,
    fork: Fork,
) -> None:
    """
    Test OOM check placement relative to static mode violation from LOGn.

    LOGn opcodes are not allowed in static context. This test verifies that
    the OOM check runs before the static mode violation check.
    With exceed=True, OOM occurs first and prevents reaching the LOG check.
    With exceed=False, OOM passes and LOG triggers static violation.

    NOTE: OOM is indisinguishable from OOG
    """
    gas_limit = generous_gas(fork)
    gas_threshold = gas_limit // 64

    # Size for the LOG operation's memory access
    log_size = 32

    # Outer allocates memory, leaving just enough room (or not) for inner
    if exceed:
        outer_alloc = Spec.MAX_TX_MEMORY_USAGE - log_size + 1
    else:
        outer_alloc = Spec.MAX_TX_MEMORY_USAGE - log_size - 32

    inner_contract = (
        prepare_stack_memory_opcode(log_opcode, log_size) + log_opcode
    )
    inner_address = pre.deploy_contract(inner_contract)

    outer_contract = (
        Op.MLOAD(outer_alloc - 32)
        + Op.SSTORE(slot_code_worked, value_code_worked)
        + Op.SSTORE(slot_call_result, 123)
        + Op.SSTORE(slot_all_gas_consumed, 123)
        + Op.SSTORE(slot_call_result, Op.STATICCALL(address=inner_address))
        + Op.SSTORE(slot_all_gas_consumed, Op.LT(Op.GAS, gas_threshold))
    )
    outer_address = pre.deploy_contract(outer_contract)

    tx = Transaction(
        gas_limit=gas_limit,
        to=outer_address,
        sender=pre.fund_eoa(),
    )

    state_test(
        pre=pre,
        post={
            outer_address: Account(
                storage={
                    slot_code_worked: value_code_worked,
                    slot_call_result: 0,
                    # OOM indistinguishable from OOG
                    slot_all_gas_consumed: 1,
                }
            )
        },
        tx=tx,
    )


@pytest.mark.parametrize("exceed", [True, False])
@pytest.mark.with_all_create_opcodes
def test_oom_check_ordering_static_create(
    state_test: StateTestFiller,
    pre: Alloc,
    create_opcode: Op,
    exceed: bool,
    fork: Fork,
) -> None:
    """
    Test OOM check placement relative to static mode violation from CREATE.

    CREATE/CREATE2 opcodes are not allowed in static context. This test
    verifies that the OOM check runs before the static mode violation check.
    With exceed=True, OOM occurs first and prevents reaching the CREATE check.
    With exceed=False, OOM passes and CREATE triggers static violation.

    NOTE: OOM is indisinguishable from OOG
    """
    gas_limit = generous_gas(fork)
    gas_threshold = gas_limit // 64
    size = 32

    # Outer allocates memory, leaving just enough room (or not) for inner
    if exceed:
        outer_alloc = Spec.MAX_TX_MEMORY_USAGE - size + 1
    else:
        outer_alloc = Spec.MAX_TX_MEMORY_USAGE - size - 32

    if create_opcode == Op.CREATE:
        prepare_stack = Op.PUSH32(size) + Op.PUSH0 + Op.PUSH0
    elif create_opcode == Op.CREATE2:
        prepare_stack = Op.PUSH0 + Op.PUSH32(size) + Op.PUSH0 + Op.PUSH0

    inner_contract = prepare_stack + create_opcode
    inner_address = pre.deploy_contract(inner_contract)

    outer_contract = (
        Op.MLOAD(outer_alloc - 32)
        + Op.SSTORE(slot_code_worked, value_code_worked)
        + Op.SSTORE(slot_call_result, 123)
        + Op.SSTORE(slot_all_gas_consumed, 123)
        + Op.SSTORE(slot_call_result, Op.STATICCALL(address=inner_address))
        + Op.SSTORE(slot_all_gas_consumed, Op.LT(Op.GAS, gas_threshold))
    )
    outer_address = pre.deploy_contract(outer_contract)

    tx = Transaction(
        gas_limit=gas_limit,
        to=outer_address,
        sender=pre.fund_eoa(),
    )

    state_test(
        pre=pre,
        post={
            outer_address: Account(
                storage={
                    slot_code_worked: value_code_worked,
                    slot_call_result: 0,
                    # OOM indistinguishable from OOG
                    slot_all_gas_consumed: 1,
                }
            )
        },
        tx=tx,
    )


@pytest.mark.parametrize(
    "opcode",
    [
        Op.CALLDATACOPY(0, 0, 0),
        Op.CODECOPY(0, 0, 0),
        Op.MCOPY(0, 0, 0),
        Op.SHA3(0, 0),
        Op.LOG0(0, 0),
    ],
)
def test_zero_length_at_boundary(
    state_test: StateTestFiller,
    pre: Alloc,
    opcode: Opcode,
    fork: Fork,
) -> None:
    """
    Test that zero-length memory operations don't allocate memory.

    Allocates to MAX, then performs zero-length operation which should
    NOT cause OOM.
    """
    offset = Spec.MAX_TX_MEMORY_USAGE - 32

    contract = (
        Op.MLOAD(offset)
        + opcode
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    contract_address = pre.deploy_contract(contract)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={slot_code_worked: value_code_worked}
            )
        },
        tx=tx,
    )


@pytest.mark.parametrize_by_fork(
    "precompile_address", lambda fork: fork.precompiles()
)
@pytest.mark.parametrize("exceed", [True, False])
def test_precompile_memory_at_limit(
    state_test: StateTestFiller,
    pre: Alloc,
    precompile_address: int,
    exceed: bool,
    fork: Fork,
) -> None:
    """
    Test precompile calls with memory at limit.
    """
    ret_size = 64

    if exceed:
        inner_alloc = Spec.MAX_TX_MEMORY_USAGE - ret_size + 32
    else:
        inner_alloc = Spec.MAX_TX_MEMORY_USAGE - ret_size

    inner_contract = Op.MLOAD(inner_alloc - 32) + Op.CALL(
        gas=Op.GAS,
        address=precompile_address,
        args_offset=0,
        args_size=inner_alloc,
        ret_offset=inner_alloc,
        ret_size=ret_size,
    )
    inner_address = pre.deploy_contract(inner_contract)

    outer_contract = Op.SSTORE(
        slot_code_worked, value_code_worked
    ) + Op.SSTORE(slot_call_result, Op.CALL(address=inner_address))
    outer_address = pre.deploy_contract(outer_contract)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=outer_address,
        sender=pre.fund_eoa(),
    )

    storage = {
        slot_code_worked: value_code_worked,
        slot_call_result: 0 if exceed else 1,
    }

    state_test(
        pre=pre,
        post={outer_address: Account(storage=storage)},
        tx=tx,
    )


@pytest.mark.parametrize("forward", [True, False])
@pytest.mark.parametrize("exceed", [True, False])
def test_mcopy_overlap_at_boundary(
    state_test: StateTestFiller,
    pre: Alloc,
    forward: bool,
    exceed: bool,
    fork: Fork,
) -> None:
    """
    Test MCOPY with overlapping regions near memory limit.

    Tests both forward (dest > src) and backward (src > dest) copies
    with overlapping ranges at the memory boundary.
    """
    size = 64
    if forward:
        if exceed:
            src = Spec.MAX_TX_MEMORY_USAGE - size - 16
            dest = Spec.MAX_TX_MEMORY_USAGE - 32
        else:
            src = Spec.MAX_TX_MEMORY_USAGE - size - 64
            dest = Spec.MAX_TX_MEMORY_USAGE - size - 32
        # forward & overlaps & ooms when exceed
        assert (
            src < dest
            and src + size > src
            and exceed == (dest + size > Spec.MAX_TX_MEMORY_USAGE)
        )
    else:
        if exceed:
            dest = Spec.MAX_TX_MEMORY_USAGE - size - 16
            src = Spec.MAX_TX_MEMORY_USAGE - 32
        else:
            dest = Spec.MAX_TX_MEMORY_USAGE - size - 64
            src = Spec.MAX_TX_MEMORY_USAGE - size - 32
        # backward & overlaps & ooms when exceed
        assert (
            dest < src
            and dest + size > src
            and exceed == (src + size > Spec.MAX_TX_MEMORY_USAGE)
        )

    contract = Op.MCOPY(dest, src, size) + Op.SSTORE(
        slot_code_worked, value_code_worked
    )
    contract_address = pre.deploy_contract(contract)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    storage = {} if exceed else {slot_code_worked: value_code_worked}

    state_test(
        pre=pre,
        post={contract_address: Account(storage=storage)},
        tx=tx,
    )


def test_memory_access_without_allocation(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test that memory allocation doesn't decrease or increase
    when it is accessed.
    """
    inner_address = pre.deploy_contract(Op.MLOAD(0))

    outer_contract = (
        Op.MLOAD(Spec.MAX_TX_MEMORY_USAGE - 32)
        + Op.MLOAD(0)
        + Op.MLOAD(32)
        + Op.MLOAD(Spec.MAX_TX_MEMORY_USAGE - 32)
        + Op.SSTORE(slot_call_result, Op.CALL(address=inner_address))
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    outer_address = pre.deploy_contract(outer_contract)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=outer_address,
        sender=pre.fund_eoa(),
    )

    state_test(
        pre=pre,
        post={
            outer_address: Account(
                storage={
                    slot_code_worked: value_code_worked,
                    slot_call_result: 0,
                }
            ),
        },
        tx=tx,
    )
