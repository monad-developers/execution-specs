"""
Tests OOM (out-of-memory) behavior of the MIP-3 memory model.
"""

import itertools

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
    pytest.mark.valid_from("MONAD_NEXT"),
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
    gas_price = 10
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
        pytest.param(Op.MLOAD(Spec.MAX_TX_MEMORY_USAGE), 0, False, id="oom"),
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
    REVERT should not consume all gas, while INVALID does.
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


def test_nested_call_oom_insufficient_gas(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test OOM behavior when CALL is given insufficient gas for memory expansion.

    This tests which check comes first: gas availability or memory limit.
    """
    inner_contract = Op.MLOAD(Spec.MAX_TX_MEMORY_USAGE)
    inner_address = pre.deploy_contract(inner_contract)

    memory_expansion_gas = fork.memory_expansion_gas_calculator()(
        new_bytes=Spec.MAX_TX_MEMORY_USAGE
    )
    insufficient_gas = memory_expansion_gas // 2
    gas_limit = generous_gas(fork)

    outer_contract = (
        Op.SSTORE(slot_code_worked, value_code_worked)
        + Op.SSTORE(
            slot_call_result,
            Op.CALL(address=inner_address, gas=insufficient_gas),
        )
        + Op.SSTORE(
            slot_all_gas_consumed, Op.LT(Op.GAS, gas_limit - insufficient_gas)
        )
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
                    slot_all_gas_consumed: 1,
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

    factory_storage = {
        slot_code_worked: value_code_worked,
    }
    new_contract_address = compute_create_address(
        address=factory_address,
        nonce=1,
        initcode=initcode,
        opcode=create_opcode,
    )
    if exceed:
        factory_storage[slot_call_result] = 0
        new_contract = Account.NONEXISTENT
    else:
        factory_storage[slot_call_result] = new_contract_address
        new_contract = Account(code=b"")

    state_test(
        pre=pre,
        post={
            factory_address: Account(storage=factory_storage),
            new_contract_address: new_contract,
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
        input=initcode,
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
    addresses = []
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
@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param("static_violation"),
        pytest.param("oog_value_transfer"),
        pytest.param("oog_access_cost"),
        pytest.param("insufficient_balance"),
    ],
)
def test_oom_check_ordering_in_call(
    state_test: StateTestFiller,
    pre: Alloc,
    scenario: str,
    exceed: bool,
    fork: Fork,
) -> None:
    """
    Test OOM check placement relative to other CALL checks.

    CALL check order in system.py:
    1. charge_gas (memory extension + access cost + value transfer cost)
    2. OOM check (update_memory_high_watermark)
    3. Static call violation check (WriteInStaticContext)
    4. Balance check (returns 0, refunds gas)

    Each scenario triggers a specific check to verify ordering relative to OOM.
    The exceed parameter controls whether the memory access would cause OOM.

    NOTE: for checking the order of checks wrt. memory expansion _gas cost_
    refert to test_oom_deep.py, as it includes testing against previous fork.
    """
    gas_limit = generous_gas(fork)
    gas_costs = fork.gas_costs()

    offset = (
        Spec.MAX_TX_MEMORY_USAGE if exceed else Spec.MAX_TX_MEMORY_USAGE - 32
    )

    if scenario == "static_violation":
        # Test: static check happens AFTER OOM check in CALL opcode.
        # Setup: Inner does CALL with value (static violation) with return
        # buffer that may or may not OOM.
        gas_threshold = gas_limit // 64

        inner_contract = Op.CALL(
            address=pre.empty_account(),
            value=1,
            ret_offset=0,
            ret_size=32,
        )
        inner_address = pre.deploy_contract(inner_contract, balance=10**18)

        outer_contract = (
            # Allocate almost the entire memory, so next allocation of 32 bytes
            # OOMs if exceed is True.
            Op.MLOAD(offset - 32)
            + Op.SSTORE(slot_code_worked, value_code_worked)
            + Op.SSTORE(slot_call_result, 123)
            + Op.SSTORE(slot_all_gas_consumed, 123)
            + Op.SSTORE(slot_call_result, Op.STATICCALL(address=inner_address))
            + Op.SSTORE(slot_all_gas_consumed, Op.LT(Op.GAS, gas_threshold))
        )
        outer_address = pre.deploy_contract(outer_contract)

        post = {
            outer_address: Account(
                storage={
                    slot_code_worked: value_code_worked,
                    slot_call_result: 0,
                    # OOM check runs first:
                    slot_all_gas_consumed: 0 if exceed else 1,
                }
            )
        }

    elif scenario == "oog_value_transfer":
        # Test: charge_gas (for value transfer cost) happens BEFORE OOM check.
        warm_account = pre.empty_account()
        inner_gas = (
            gas_costs.G_WARM_ACCOUNT_ACCESS + gas_costs.G_CALL_VALUE - 10
        )
        gas_threshold = gas_limit - inner_gas

        inner_contract = Op.MSTORE(
            0,
            Op.CALL(
                gas=0,
                address=warm_account,
                value=1,
                ret_offset=0,
                ret_size=32,
            ),
        ) + Op.RETURN(0, 32)
        inner_address = pre.deploy_contract(inner_contract)

        outer_contract = (
            # Allocate almost the entire memory, so next allocation of 32 bytes
            # OOMs if exceed is True.
            Op.MLOAD(offset - 32)
            + Op.BALANCE(warm_account)
            + Op.SSTORE(slot_code_worked, value_code_worked)
            + Op.SSTORE(slot_call_result, 123)
            + Op.SSTORE(slot_all_gas_consumed, 123)
            + Op.DELEGATECALL(
                gas=inner_gas,
                address=inner_address,
            )
            + Op.SSTORE(slot_all_gas_consumed, Op.LT(Op.GAS, gas_threshold))
            + Op.RETURNDATACOPY(0, 0, Op.RETURNDATASIZE)
            + Op.SSTORE(slot_call_result, Op.MLOAD(0))
        )
        outer_address = pre.deploy_contract(outer_contract, balance=10**18)

        post = {
            outer_address: Account(
                storage={
                    slot_code_worked: value_code_worked,
                    slot_call_result: 0,
                    # oog check comes first so regardless of OOM:
                    slot_all_gas_consumed: 1,
                }
            )
        }

    elif scenario == "oog_access_cost":
        # Test: charge_gas (for cold access cost) happens BEFORE OOM check.
        # Setup: CALL to cold address with contract with memory access.
        # Result: With exceed=True, callee OOMs. With exceed=False, succeeds.
        cold_account = pre.empty_account()
        inner_gas = gas_costs.G_COLD_ACCOUNT_ACCESS - 10
        gas_threshold = gas_limit - inner_gas

        inner_contract = Op.MSTORE(
            0,
            Op.CALL(
                gas=0,
                address=cold_account,
                ret_offset=0,
                ret_size=32,
            ),
        ) + Op.RETURN(0, 32)
        inner_address = pre.deploy_contract(inner_contract)

        outer_contract = (
            # Allocate almost the entire memory, so next allocation of 32 bytes
            # OOMs if exceed is True.
            Op.MLOAD(offset - 32)
            + Op.SSTORE(slot_code_worked, value_code_worked)
            + Op.SSTORE(slot_call_result, 123)
            + Op.SSTORE(slot_all_gas_consumed, 123)
            + Op.DELEGATECALL(
                gas=inner_gas,
                address=inner_address,
            )
            + Op.SSTORE(slot_all_gas_consumed, Op.LT(Op.GAS, gas_threshold))
            + Op.RETURNDATACOPY(0, 0, Op.RETURNDATASIZE)
            + Op.SSTORE(slot_call_result, Op.MLOAD(0))
        )
        outer_address = pre.deploy_contract(outer_contract, balance=10**18)

        post = {
            outer_address: Account(
                storage={
                    slot_code_worked: value_code_worked,
                    slot_call_result: 0,
                    # oog check comes first so regardless of OOM:
                    slot_all_gas_consumed: 1,
                }
            )
        }

    else:  # insufficient_balance
        # Test: balance check happens AFTER OOM check.
        # Setup: CALL with value > sender balance, ret_offset may cause OOM.
        # With exceed=True, OOM check fails first (before balance check).
        # With exceed=False, OOM check passes, balance check fails.
        warm_account = pre.empty_account()
        gas_threshold = gas_limit // 64

        inner_contract = Op.MSTORE(
            0,
            Op.CALL(
                gas=0,
                address=warm_account,
                value=1,
                ret_offset=0,
                ret_size=32,
            ),
        ) + Op.RETURN(0, 32)
        inner_address = pre.deploy_contract(inner_contract)

        outer_contract = (
            # Allocate almost the entire memory, so next allocation of 32 bytes
            # OOMs if exceed is True.
            Op.MLOAD(offset - 32)
            + Op.BALANCE(warm_account)
            + Op.SSTORE(slot_code_worked, value_code_worked)
            + Op.SSTORE(slot_call_result, 123)
            + Op.SSTORE(slot_all_gas_consumed, 123)
            + Op.DELEGATECALL(address=inner_address)
            + Op.SSTORE(slot_all_gas_consumed, Op.LT(Op.GAS, gas_threshold))
            + Op.RETURNDATACOPY(0, 0, Op.RETURNDATASIZE)
            + Op.SSTORE(slot_call_result, Op.MLOAD(0))
        )
        outer_address = pre.deploy_contract(outer_contract, balance=0)

        post = {
            outer_address: Account(
                storage={
                    slot_code_worked: value_code_worked,
                    slot_call_result: 0,
                    # in either case not all gas is consumed
                    slot_all_gas_consumed: 0,
                }
            )
        }

    tx = Transaction(
        gas_limit=gas_limit,
        to=outer_address,
        sender=pre.fund_eoa(),
    )

    state_test(
        pre=pre,
        post=post,
        tx=tx,
    )
