"""
Tests cross-call page warming propagation under MIP-8.
"""

from dataclasses import dataclass, field

import pytest
from execution_testing import (
    Account,
    Alloc,
    Bytecode,
    CodeGasMeasure,
    Conditional,
    Initcode,
    Op,
    StateTestFiller,
    Transaction,
)
from execution_testing.base_types.conversions import NumberConvertible
from execution_testing.forks.helpers import Fork
from execution_testing.test_types.helpers import compute_create_address

from .helpers import generous_gas, generous_gas_with_create
from .spec import Spec, ref_spec_8

REFERENCE_SPEC_GIT_PATH = ref_spec_8.git_path
REFERENCE_SPEC_VERSION = ref_spec_8.version

slot_result = 0x100
slot_gas_measured = 0x101
slot_gas_measured_2 = 0x102
slot_caller = 0x103
value_code_worked = 0x1234

pytestmark = [
    pytest.mark.valid_from("MONAD_NEXT"),
]


@pytest.mark.with_all_call_opcodes(
    selector=lambda call_opcode: call_opcode != Op.STATICCALL,
)
def test_call_child_warm(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    call_opcode: Op,
) -> None:
    """
    Self-call propagates page warming to the child frame.

    Parent warms page 0 of parent_address via SLOAD(0), then
    self-calls (Op.ADDRESS) with 1 byte of calldata to branch
    into the measure leg. The child SLOAD(1) targets the same
    (parent_addr, page 0) which is already warm.

    STATICCALL is covered separately by
    `test_staticcall_child_warm` since it cannot SSTORE inside
    the child branch to record the measurement.
    """
    overhead = Op.PUSH1(0).gas_cost(fork)
    measure_code = CodeGasMeasure(
        code=Op.SLOAD(1),
        overhead_cost=overhead,
        extra_stack_items=1,
        sstore_key=slot_gas_measured,
    )
    parent_code = Conditional(
        condition=Op.CALLDATASIZE,
        if_true=measure_code,
        if_false=(
            Op.SLOAD(0)
            + Op.SSTORE(
                slot_result,
                call_opcode(
                    address=Op.ADDRESS,
                    args_offset=0,
                    args_size=1,
                ),
            )
        ),
    )
    parent_address = pre.deploy_contract(parent_code)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=parent_address,
        sender=pre.fund_eoa(),
    )

    expected_gas = Op.SLOAD(page_load_warm=True).gas_cost(fork)

    state_test(
        pre=pre,
        post={
            parent_address: Account(
                storage={
                    slot_result: 1,
                    slot_gas_measured: expected_gas,
                },
            ),
        },
        tx=tx,
    )


def test_staticcall_child_warm(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Self-STATICCALL propagates page warming; STATICCALL gas
    measured at the parent (child can't SSTORE).

    Parent warms page 0 via SLOAD(0), self-STATICCALLs with 1
    byte calldata. Child SLOAD(1) hits warm (parent_addr, 0).
    Total measured = STATICCALL warm-account cost + the child
    execution path (Conditional preamble + warm SLOAD + STOP).
    """
    # Overhead absorbs all non-measured opcodes: STATICCALL's
    # 4 PUSH1 stack args + ADDRESS + GAS, plus the child path
    # (Conditional preamble + JUMPDEST + SLOAD key PUSH + STOP).
    overhead_cost = (
        Op.PUSH1(0) * 4
        + Op.ADDRESS
        + Op.GAS
        + Op.CALLDATASIZE
        + Op.PUSH1(0)
        + Op.PC
        + Op.ADD
        + Op.JUMPI
        + Op.JUMPDEST
        + Op.PUSH1(0)
        + Op.STOP
    ).gas_cost(fork)

    parent_code = Conditional(
        condition=Op.CALLDATASIZE,
        if_true=Op.SLOAD(1) + Op.STOP,
        if_false=(
            Op.SLOAD(0)
            + CodeGasMeasure(
                code=Op.STATICCALL(
                    address=Op.ADDRESS,
                    args_offset=0,
                    args_size=1,
                ),
                overhead_cost=overhead_cost,
                extra_stack_items=1,
                sstore_key=slot_gas_measured,
            )
        ),
    )
    parent_address = pre.deploy_contract(parent_code)

    expected = Op.STATICCALL(address_warm=True).gas_cost(fork) + Op.SLOAD(
        page_load_warm=True
    ).gas_cost(fork)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=parent_address,
        sender=pre.fund_eoa(),
    )
    state_test(
        pre=pre,
        post={
            parent_address: Account(
                storage={slot_gas_measured: expected},
            ),
        },
        tx=tx,
    )


@pytest.mark.parametrize("success_mode", [Op.STOP, Op.RETURN(0, 0)])
@pytest.mark.with_all_call_opcodes
def test_call_child_warm_pages_propagate_on_success(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    call_opcode: Op,
    success_mode: Op,
) -> None:
    """
    Self-call propagates page warming back to parent on success.

    Child branch warms page 1 via SLOAD(128) and exits with
    `success_mode`. Parent SLOAD(129) hits warm regardless of
    call opcode: self-call keeps the page key address equal to
    parent_address for every opcode (DELEGATECALL/CALLCODE
    inherit, CALL/STATICCALL target parent_address explicitly).
    """
    overhead = Op.PUSH2(0).gas_cost(fork)
    parent_code = Conditional(
        condition=Op.CALLDATASIZE,
        if_true=Op.SLOAD(128) + success_mode,
        if_false=(
            Op.SSTORE(
                slot_result,
                call_opcode(
                    address=Op.ADDRESS,
                    args_offset=0,
                    args_size=1,
                ),
            )
            + CodeGasMeasure(
                code=Op.SLOAD(129),
                overhead_cost=overhead,
                extra_stack_items=1,
                sstore_key=slot_gas_measured,
            )
        ),
    )
    parent_address = pre.deploy_contract(parent_code)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=parent_address,
        sender=pre.fund_eoa(),
    )

    expected_gas = Op.SLOAD(page_load_warm=True).gas_cost(fork)

    state_test(
        pre=pre,
        post={
            parent_address: Account(
                storage={
                    slot_result: 1,
                    slot_gas_measured: expected_gas,
                },
            ),
        },
        tx=tx,
    )


@pytest.mark.parametrize(
    "revert_cause",
    [
        Op.REVERT(0, 0),
        Op.INVALID,
        pytest.param(Op.MLOAD(8 * 1024 * 1024), id="oom"),
        pytest.param(Op.JUMP(Op.PC), id="oog"),
    ],
)
@pytest.mark.with_all_call_opcodes
def test_call_child_warm_pages_lost_on_revert(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    call_opcode: Op,
    revert_cause: Op,
) -> None:
    """
    Child frame warming is dropped on revert/halt.

    Child branch warms page 1 via SLOAD(128), then reverts.
    Parent SLOAD(129) on page 1 is cold for all call opcodes.
    """
    overhead = Op.PUSH2(0).gas_cost(fork)
    parent_code = Conditional(
        condition=Op.CALLDATASIZE,
        if_true=Op.SLOAD(128) + revert_cause,
        if_false=(
            Op.SSTORE(
                slot_result,
                call_opcode(
                    address=Op.ADDRESS,
                    args_offset=0,
                    args_size=1,
                    # Cap child gas so parent retains enough
                    # for CodeGasMeasure after child OOGs.
                    gas=generous_gas(fork),
                ),
            )
            + CodeGasMeasure(
                code=Op.SLOAD(129),
                overhead_cost=overhead,
                extra_stack_items=1,
                sstore_key=slot_gas_measured,
            )
        ),
    )
    parent_address = pre.deploy_contract(parent_code)

    tx = Transaction(
        gas_limit=2 * generous_gas(fork),
        to=parent_address,
        sender=pre.fund_eoa(),
    )

    state_test(
        pre=pre,
        post={
            parent_address: Account(
                storage={
                    slot_result: 0,
                    slot_gas_measured: Op.SLOAD(page_load_warm=False).gas_cost(
                        fork
                    ),
                },
            ),
        },
        tx=tx,
    )


@pytest.mark.with_all_create_opcodes
def test_create_child_cold_pages(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    create_opcode: Op,
) -> None:
    """
    Test that created contract doesn't inherit parent's warm pages.

    Parent warms page 0 via SLOAD(0), then CREATE/CREATE2.
    Initcode does SLOAD(1) — cold page.
    """
    initcode = Initcode(
        deploy_code=Op.STOP,
        initcode_prefix=CodeGasMeasure(
            code=Op.SLOAD(1),
            overhead_cost=Op.PUSH1(0).gas_cost(fork),
            extra_stack_items=1,
            sstore_key=slot_gas_measured,
            stop=False,
        ),
    )

    parent_code = (
        Op.SLOAD(0)
        + Op.CALLDATACOPY(0, 0, Op.CALLDATASIZE)
        + create_opcode(size=Op.CALLDATASIZE)
        + Op.SSTORE(slot_result, value_code_worked)
    )
    parent_address = pre.deploy_contract(parent_code)

    new_contract_address = compute_create_address(
        address=parent_address,
        nonce=1,
        initcode=initcode,
        opcode=create_opcode,
    )

    tx = Transaction(
        gas_limit=generous_gas_with_create(fork),
        to=parent_address,
        sender=pre.fund_eoa(),
        data=bytes(initcode),
    )

    state_test(
        pre=pre,
        post={
            parent_address: Account(
                storage={slot_result: value_code_worked},
            ),
            new_contract_address: Account(
                storage={
                    slot_gas_measured: Op.SLOAD(page_load_warm=False).gas_cost(
                        fork
                    ),
                },
            ),
        },
        tx=tx,
    )


@pytest.mark.parametrize(
    "initcode_body",
    [
        # SLOAD warms (new, page 1) then halts via STOP / RETURN.
        pytest.param(
            Op.SLOAD(128) + Op.STOP,
            id="sload_then_stop",
        ),
        pytest.param(
            Op.SLOAD(128) + Op.RETURN(0, 0),
            id="sload_then_return",
        ),
        # Multi-page
        pytest.param(
            Op.SSTORE(0, 1) + Op.SSTORE(128, 1),
            id="multipage_sstore",
        ),
        # page-load-warm but page-write-cold path.
        pytest.param(
            Op.SLOAD(0) + Op.SSTORE(0, 1),
            id="sload_before_sstore",
        ),
    ],
)
@pytest.mark.with_all_create_opcodes
def test_create_child_no_warm_parent_pages(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    create_opcode: Op,
    initcode_body: Op,
) -> None:
    """
    Pages warmed inside initcode stay cold in parent.
    """
    initcode_bytes = bytes(initcode_body)
    padded = initcode_bytes + b"\x00" * (32 - (len(initcode_bytes) % 32))
    assert len(padded) == 32, "initcode must fit one PUSH32"

    parent_code = (
        Op.MSTORE(0, Op.PUSH32(padded[:32]))
        + create_opcode(size=len(initcode_bytes))
        + Op.SSTORE(slot_result, value_code_worked)
        + CodeGasMeasure(
            code=Op.SLOAD(129),
            overhead_cost=Op.PUSH1(0).gas_cost(fork),
            extra_stack_items=1,
            sstore_key=slot_gas_measured,
        )
    )
    parent_address = pre.deploy_contract(parent_code)

    tx = Transaction(
        gas_limit=generous_gas_with_create(fork),
        to=parent_address,
        sender=pre.fund_eoa(),
    )

    state_test(
        pre=pre,
        post={
            parent_address: Account(
                storage={
                    slot_result: value_code_worked,
                    slot_gas_measured: Op.SLOAD(page_load_warm=False).gas_cost(
                        fork
                    ),
                },
            ),
        },
        tx=tx,
    )


@pytest.mark.parametrize(
    "revert_cause",
    [
        Op.REVERT(0, 0),
        Op.INVALID,
        pytest.param(Op.MLOAD(8 * 1024 * 1024), id="oom"),
        pytest.param(Op.JUMP(Op.PC), id="oog"),
    ],
)
@pytest.mark.with_all_create_opcodes
def test_create_child_warm_pages_lost_on_revert(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    create_opcode: Op,
    revert_cause: Op,
) -> None:
    """
    Test that failed create doesn't propagate warm pages.
    """
    overhead = Op.PUSH2(0).gas_cost(fork)
    measure_contract = pre.deploy_contract(
        CodeGasMeasure(
            code=Op.SLOAD(129),
            overhead_cost=overhead,
            extra_stack_items=1,
            sstore_key=slot_gas_measured,
            stop=False,
        )
        # Sanity check to ensure it's not the initcode frame
        # storing the measured cold gas.
        + Op.SSTORE(slot_caller, Op.CALLER)
    )

    initcode_code = (
        Op.SLOAD(128) + Op.CALL(address=measure_contract) + revert_cause
    )

    parent_code = (
        Op.CALLDATACOPY(0, 0, Op.CALLDATASIZE)
        + create_opcode(size=Op.CALLDATASIZE)
        + Op.CALL(address=measure_contract)
        + Op.SSTORE(slot_result, value_code_worked)
    )
    parent_address = pre.deploy_contract(parent_code)

    tx = Transaction(
        gas_limit=fork.transaction_gas_limit_cap(),
        to=parent_address,
        sender=pre.fund_eoa(),
        data=bytes(initcode_code),
    )

    state_test(
        pre=pre,
        post={
            parent_address: Account(
                storage={slot_result: value_code_worked},
            ),
            measure_contract: Account(
                storage={
                    slot_gas_measured: Op.SLOAD(page_load_warm=False).gas_cost(
                        fork
                    ),
                    # Sanity check to ensure it's not the initcode frame
                    # storing the measured cold gas.
                    slot_caller: parent_address,
                },
            ),
        },
        tx=tx,
    )


@pytest.mark.parametrize(
    "revert_cause",
    [
        Op.REVERT(0, 0),
        Op.INVALID,
        pytest.param(Op.MLOAD(8 * 1024 * 1024), id="oom"),
        pytest.param(Op.JUMP(Op.PC), id="oog"),
    ],
)
@pytest.mark.with_all_create_opcodes
def test_create_child_state_growth_lost_on_revert(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    create_opcode: Op,
    revert_cause: Op,
) -> None:
    """
    Test that failed CREATE rolls back state-growth counters.
    """
    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)
    measure_contract = pre.deploy_contract(
        CodeGasMeasure(
            code=Op.SSTORE(0, 1),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=slot_gas_measured,
            stop=False,
        )
        # Sanity check to ensure it's not the initcode frame
        # storing the measured fresh-growth SSTORE cost.
        + Op.SSTORE(slot_caller, Op.CALLER)
    )

    initcode_code = Op.CALL(address=measure_contract) + revert_cause

    parent_code = (
        Op.CALLDATACOPY(0, 0, Op.CALLDATASIZE)
        + create_opcode(size=Op.CALLDATASIZE)
        + Op.CALL(address=measure_contract)
        + Op.SSTORE(slot_result, value_code_worked)
    )
    parent_address = pre.deploy_contract(parent_code)

    tx = Transaction(
        gas_limit=fork.transaction_gas_limit_cap(),
        to=parent_address,
        sender=pre.fund_eoa(),
        data=bytes(initcode_code),
    )

    expected_cost = Op.SSTORE(
        page_load_warm=False,
        page_write_warm=False,
        current_value=0,
        new_value=1,
        current_state_growth=0,
        net_state_growth=0,
    ).gas_cost(fork)

    state_test(
        pre=pre,
        post={
            parent_address: Account(
                storage={slot_result: value_code_worked},
            ),
            measure_contract: Account(
                storage={
                    0: 1,
                    slot_gas_measured: expected_cost,
                    # Sanity check to ensure it's not the initcode frame
                    # storing the measured fresh-growth SSTORE cost.
                    slot_caller: parent_address,
                },
            ),
        },
        tx=tx,
    )


def test_delegatecall_self_warms_pages(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test that DELEGATECALL to self warms pages and they propagate
    back to the caller frame.
    """
    overhead = Op.PUSH2(0).gas_cost(fork)

    inner_code = Op.SLOAD(128) + Op.STOP
    outer_code = Op.DELEGATECALL(
        gas=Op.GAS,
        address=Op.ADDRESS,
        args_offset=0,
        args_size=1,
        ret_offset=0,
        ret_size=0,
    ) + CodeGasMeasure(
        code=Op.SLOAD(129),
        overhead_cost=overhead,
        extra_stack_items=1,
        sstore_key=slot_gas_measured,
    )

    contract_address = pre.deploy_contract(
        Conditional(
            condition=Op.CALLDATASIZE,
            if_true=inner_code,
            if_false=outer_code,
        )
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_gas_measured: Op.SLOAD(page_load_warm=True).gas_cost(
                        fork
                    )
                },
            ),
        },
        tx=tx,
    )


@pytest.mark.parametrize(
    "call_kind",
    ["call", "callcode", "delegatecall_chain", "call_chain"],
)
def test_cross_account_page_propagation(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    call_kind: str,
) -> None:
    """
    Page warming is keyed by (storage-context-address, page).
    """
    inner = pre.deploy_contract(Op.SSTORE(0, value_code_worked))
    expected_warm: bool

    if call_kind == "call":
        outer_call = Op.CALL(address=inner)
        expected_warm = False
    elif call_kind == "callcode":
        outer_call = Op.CALLCODE(address=inner)
        expected_warm = True
    elif call_kind == "delegatecall_chain":
        mid = pre.deploy_contract(Op.DELEGATECALL(address=inner) + Op.STOP)
        outer_call = Op.DELEGATECALL(address=mid)
        expected_warm = True
    else:  # call_chain
        mid = pre.deploy_contract(Op.CALL(address=inner) + Op.STOP)
        outer_call = Op.CALL(address=mid)
        expected_warm = False

    overhead = Op.PUSH1(0).gas_cost(fork)
    outer = pre.deploy_contract(
        outer_call
        + CodeGasMeasure(
            code=Op.SLOAD(1),
            overhead_cost=overhead,
            extra_stack_items=1,
            sstore_key=slot_gas_measured,
        )
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=outer,
        sender=pre.fund_eoa(),
    )

    expected_storage: dict[int, int] = {
        slot_gas_measured: Op.SLOAD(page_load_warm=expected_warm).gas_cost(
            fork
        ),
    }
    if expected_warm:
        # CALLCODE/DELEGATECALL: inner's SSTORE wrote to outer's
        # storage context.
        expected_storage[0] = value_code_worked

    state_test(
        pre=pre,
        post={outer: Account(storage=expected_storage)},
        tx=tx,
    )


def test_in_frame_revert_loses_warming(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Pages warmed by the parent itself survive a sub-call revert;
    pages warmed only inside the reverted sub-call do not.
    """
    child_address = pre.deploy_contract(Op.SLOAD(384) + Op.REVERT(0, 0))

    overhead = Op.PUSH2(0).gas_cost(fork)
    parent_address = pre.deploy_contract(
        Op.SLOAD(0)
        + Op.DELEGATECALL(address=child_address, gas=100_000)
        + CodeGasMeasure(
            code=Op.SLOAD(1),
            overhead_cost=overhead,
            extra_stack_items=1,
            sstore_key=slot_gas_measured,
            stop=False,
        )
        + CodeGasMeasure(
            code=Op.SLOAD(385),
            overhead_cost=overhead,
            extra_stack_items=1,
            sstore_key=slot_gas_measured_2,
        )
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=parent_address,
        sender=pre.fund_eoa(),
    )

    state_test(
        pre=pre,
        post={
            parent_address: Account(
                storage={
                    slot_gas_measured: Op.SLOAD(page_load_warm=True).gas_cost(
                        fork
                    ),
                    slot_gas_measured_2: Op.SLOAD(
                        page_load_warm=False
                    ).gas_cost(fork),
                },
            ),
        },
        tx=tx,
    )


def test_staticcall_sstore_aborts_no_warming(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    STATICCALL into a child that attempts SSTORE aborts the child
    (static-context violation). Any page warming the failed SSTORE
    would have created is lost together with the aborted frame.
    """
    child_address = pre.deploy_contract(
        Conditional(
            condition=Op.CALLDATASIZE,
            if_true=CodeGasMeasure(
                code=Op.SLOAD(1),
                overhead_cost=Op.PUSH1(0).gas_cost(fork),
                extra_stack_items=1,
                sstore_key=slot_gas_measured,
            ),
            if_false=Op.SSTORE(0, 1),
        )
    )

    parent_address = pre.deploy_contract(
        Op.STATICCALL(address=child_address, gas=100_000)
        + Op.CALL(address=child_address, args_offset=0, args_size=1)
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=parent_address,
        sender=pre.fund_eoa(),
    )

    state_test(
        pre=pre,
        post={
            child_address: Account(
                storage={
                    slot_gas_measured: Op.SLOAD(page_load_warm=False).gas_cost(
                        fork
                    ),
                },
            ),
        },
        tx=tx,
    )


@pytest.mark.parametrize("op", ["SSTORE", "SLOAD"])
def test_call_value_stipend_storage_oog(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    op: str,
) -> None:
    """
    A value-bearing CALL grants the child a 2300-gas stipend.
    Both cold-page SSTORE (~10900) and cold-page SLOAD (~8100)
    exceed the stipend; the child OOGs.
    """
    if op == "SSTORE":
        child_code = Op.SSTORE(0, 1)
    else:
        child_code = Op.SLOAD(0)

    child_address = pre.deploy_contract(child_code)

    parent_address = pre.deploy_contract(
        Op.SSTORE(
            slot_result,
            Op.CALL(gas=0, value=1, address=child_address),
        ),
        balance=10**16,
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=parent_address,
        sender=pre.fund_eoa(),
    )

    state_test(
        pre=pre,
        post={
            parent_address: Account(storage={slot_result: 0}),
            # Child storage unchanged — SSTORE never committed.
            child_address: Account(storage={}),
        },
        tx=tx,
    )


def test_call_insufficient_gas_no_warming(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Tiny forwarded gas OOGs the child immediately; parent's pages
    that were never touched stay cold.
    """
    child_address = pre.deploy_contract(Op.SLOAD(0))

    overhead = Op.PUSH2(0).gas_cost(fork)
    parent_address = pre.deploy_contract(
        Op.SLOAD(0)
        + Op.CALL(address=child_address, gas=10)
        + CodeGasMeasure(
            code=Op.SLOAD(128),
            overhead_cost=overhead,
            extra_stack_items=1,
            sstore_key=slot_gas_measured,
        )
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=parent_address,
        sender=pre.fund_eoa(),
    )

    state_test(
        pre=pre,
        post={
            parent_address: Account(
                storage={
                    slot_gas_measured: Op.SLOAD(page_load_warm=False).gas_cost(
                        fork
                    ),
                },
            ),
        },
        tx=tx,
    )


def test_selfdestruct_storage_warming(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    EIP-6780's not-same-tx SELFDESTRUCT leaves pages warmed.
    """
    beneficiary = pre.fund_eoa(amount=10**16)

    child_address = pre.deploy_contract(
        Conditional(
            condition=Op.CALLDATASIZE,
            if_true=CodeGasMeasure(
                code=Op.SLOAD(1),
                overhead_cost=Op.PUSH1(0).gas_cost(fork),
                extra_stack_items=1,
                sstore_key=slot_gas_measured,
            ),
            if_false=(Op.SLOAD(0) + Op.SELFDESTRUCT(beneficiary)),
        ),
        storage={0: 99, 1: 88},
    )

    parent_address = pre.deploy_contract(
        Op.CALL(address=child_address)
        + Op.CALL(address=child_address, args_offset=0, args_size=1)
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=parent_address,
        sender=pre.fund_eoa(),
    )

    state_test(
        pre=pre,
        post={
            child_address: Account(
                storage={
                    0: 99,
                    1: 88,
                    slot_gas_measured: Op.SLOAD(page_load_warm=True).gas_cost(
                        fork
                    ),
                },
            ),
        },
        tx=tx,
    )


def test_selfdestruct_persists_state_growth(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """EIP-6780 not-same-tx SELFDESTRUCT preserves state-growth counters."""
    beneficiary = pre.fund_eoa(amount=10**16)

    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)
    child_address = pre.deploy_contract(
        Conditional(
            condition=Op.CALLDATASIZE,
            if_true=CodeGasMeasure(
                code=Op.SSTORE(1, 1),
                overhead_cost=overhead,
                extra_stack_items=0,
                sstore_key=slot_gas_measured,
            ),
            if_false=Op.SSTORE(0, 1) + Op.SELFDESTRUCT(beneficiary),
        ),
    )

    parent_address = pre.deploy_contract(
        Op.CALL(address=child_address)
        + Op.CALL(address=child_address, args_offset=0, args_size=1)
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=parent_address,
        sender=pre.fund_eoa(),
    )

    expected_cost = Op.SSTORE(
        page_load_warm=True,
        page_write_warm=True,
        current_value=0,
        new_value=1,
        current_state_growth=1,
        net_state_growth=1,
    ).gas_cost(fork)

    state_test(
        pre=pre,
        post={
            child_address: Account(
                storage={
                    0: 1,
                    1: 1,
                    slot_gas_measured: expected_cost,
                },
            ),
        },
        tx=tx,
    )


@dataclass
class _Page:
    """Simulated page-0 state under the MIP-8 SSTORE algorithm."""

    slots: dict[int, int] = field(default_factory=dict)
    current_growth: int = 0
    peak_growth: int = 0
    read_warm: bool = False
    write_warm: bool = False


def _simulate_sstore(
    page: _Page, slot: int, new_value: int, fork: Fork
) -> int:
    """Apply SSTORE(slot, new_value) to `page`; return its gas cost."""
    old_value = page.slots.get(slot, 0)
    cost = Op.SSTORE(
        page_load_warm=page.read_warm,
        page_write_warm=page.write_warm,
        current_value=old_value,
        new_value=new_value,
        current_state_growth=page.current_growth,
        net_state_growth=page.peak_growth,
    ).gas_cost(fork)
    page.read_warm = True
    if old_value != new_value and not page.write_warm:
        page.write_warm = True
    if old_value == 0 and new_value != 0:
        page.current_growth += 1
    elif old_value != 0 and new_value == 0:
        page.current_growth -= 1
    if page.current_growth > page.peak_growth:
        page.peak_growth = page.current_growth
    page.slots[slot] = new_value
    return cost


@pytest.mark.parametrize("call_op", [Op.DELEGATECALL, Op.CALL, Op.CREATE])
@pytest.mark.parametrize("prestate_clear_child", [0, 1, 32])
@pytest.mark.parametrize("prestate_clear_parent", [0, 1, 32])
@pytest.mark.parametrize("state_clear_child", [0, 1, 32])
@pytest.mark.parametrize("state_growth_child", [0, 1, 32])
@pytest.mark.parametrize("state_clear_parent", [0, 1, 32])
@pytest.mark.parametrize("state_growth_parent", [0, 1, 32])
def test_state_growth_counters_inside_subcall(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    state_growth_parent: int,
    state_clear_parent: int,
    state_growth_child: int,
    state_clear_child: int,
    prestate_clear_parent: int,
    prestate_clear_child: int,
    call_op: Op,
) -> None:
    """
    Test state costs in child after a DELEGATECALL or plain-CALL.
    """
    parent_prestate_slots = list(range(prestate_clear_parent))
    a = prestate_clear_parent
    child_prestate_slots = list(range(a, a + prestate_clear_child))
    b = a + prestate_clear_child
    parent_growth_slots = list(range(b, b + state_growth_parent))
    parent_clear_slots = list(range(b, b + state_clear_parent))
    c = b + state_growth_parent
    child_growth_slots = list(range(c, c + state_growth_child))
    child_clear_slots = list(range(c, c + state_clear_child))
    del a, b, c

    if call_op == Op.DELEGATECALL:
        page = _Page(
            slots=dict.fromkeys(
                parent_prestate_slots + child_prestate_slots, 1
            )
        )
    elif call_op == Op.CALL:
        page = _Page(slots=dict.fromkeys(child_prestate_slots, 1))
    else:
        page = _Page()

    parent_pre = Bytecode()
    for i in parent_prestate_slots:
        parent_pre += Op.SSTORE(i, 0)
        if call_op == Op.DELEGATECALL:
            _simulate_sstore(page, i, 0, fork)
    for i in parent_growth_slots:
        parent_pre += Op.SSTORE(i, 1)
        if call_op == Op.DELEGATECALL:
            _simulate_sstore(page, i, 1, fork)
    for i in parent_clear_slots:
        parent_pre += Op.SSTORE(i, 0)
        if call_op == Op.DELEGATECALL:
            _simulate_sstore(page, i, 0, fork)

    child_code = Bytecode()
    for i in child_prestate_slots:
        child_code += Op.SSTORE(i, 0)
        _simulate_sstore(page, i, 0, fork)
    for i in child_growth_slots:
        child_code += Op.SSTORE(i, 1)
        _simulate_sstore(page, i, 1, fork)
    for i in child_clear_slots:
        child_code += Op.SSTORE(i, 0)
        _simulate_sstore(page, i, 0, fork)

    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)
    measure_offset = Spec.SLOTS_PER_PAGE
    expected_storage: dict[int, int] = {}
    for i in range(Spec.SLOTS_PER_PAGE):
        cost = _simulate_sstore(page, i, 1, fork)
        child_code += CodeGasMeasure(
            code=Op.SSTORE(i, 1),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=measure_offset + i,
            stop=False,
        )
        expected_storage[measure_offset + i] = cost
    child_code += Op.RETURN(0, 0) if call_op == Op.CREATE else Op.STOP
    for i in range(Spec.SLOTS_PER_PAGE):
        expected_storage[i] = 1

    tx_data: bytes = b""
    if call_op == Op.CREATE:
        tx_data = bytes(child_code)
        sub_call_code = Op.CALLDATACOPY(0, 0, Op.CALLDATASIZE) + Op.CREATE(
            value=0, offset=0, size=Op.CALLDATASIZE
        )
    else:
        child_storage: dict[NumberConvertible, NumberConvertible] = (
            dict.fromkeys(child_prestate_slots, 1)
            if call_op == Op.CALL
            else {}
        )
        child_address = pre.deploy_contract(child_code, storage=child_storage)
        sub_call_code = call_op(address=child_address)

    parent_storage: dict[NumberConvertible, NumberConvertible] = (
        dict.fromkeys(parent_prestate_slots + child_prestate_slots, 1)
        if call_op == Op.DELEGATECALL
        else dict.fromkeys(parent_prestate_slots, 1)
    )
    parent_address = pre.deploy_contract(
        parent_pre + sub_call_code, storage=parent_storage
    )

    if call_op == Op.DELEGATECALL:
        target_address = parent_address
    elif call_op == Op.CALL:
        target_address = child_address
    else:
        target_address = compute_create_address(
            address=parent_address, nonce=1, opcode=Op.CREATE
        )

    tx = Transaction(
        gas_limit=fork.transaction_gas_limit_cap(),
        to=parent_address,
        sender=pre.fund_eoa(),
        data=tx_data,
    )

    state_test(
        pre=pre,
        post={target_address: Account(storage=expected_storage)},
        tx=tx,
    )


@pytest.mark.parametrize("call_op", [Op.DELEGATECALL, Op.CALL, Op.CREATE])
@pytest.mark.parametrize("child_exit", [Op.REVERT(0, 0), Op.STOP])
@pytest.mark.parametrize("prestate_clear_child", [0, 1, 32])
@pytest.mark.parametrize("prestate_clear_parent", [0, 1, 32])
@pytest.mark.parametrize("state_clear_child", [0, 1, 32])
@pytest.mark.parametrize("state_growth_child", [0, 1, 32])
@pytest.mark.parametrize("state_clear_parent", [0, 1, 32])
@pytest.mark.parametrize("state_growth_parent", [0, 1, 32])
def test_state_growth_counters_after_subcall(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    state_growth_parent: int,
    state_clear_parent: int,
    state_growth_child: int,
    state_clear_child: int,
    prestate_clear_parent: int,
    prestate_clear_child: int,
    child_exit: Op,
    call_op: Op,
) -> None:
    """
    Test state costs in parent after a DELEGATECALLed or plain-CALLed child,
    with the child ending in REVERT or STOP.
    """
    # Split slots into 4 consecutive sequences to set/clear.
    parent_prestate_slots = list(range(prestate_clear_parent))
    a = prestate_clear_parent
    child_prestate_slots = list(range(a, a + prestate_clear_child))
    b = a + prestate_clear_child
    parent_growth_slots = list(range(b, b + state_growth_parent))
    parent_clear_slots = list(range(b, b + state_clear_parent))
    c = b + state_growth_parent
    child_growth_slots = list(range(c, c + state_growth_child))
    child_clear_slots = list(range(c, c + state_clear_child))
    del a, b, c
    prestate_slots = parent_prestate_slots + child_prestate_slots

    page = _Page(slots=dict.fromkeys(prestate_slots, 1))

    parent_pre = Bytecode()
    for i in parent_prestate_slots:
        parent_pre += Op.SSTORE(i, 0)
        _simulate_sstore(page, i, 0, fork)
    for i in parent_growth_slots:
        parent_pre += Op.SSTORE(i, 1)
        _simulate_sstore(page, i, 1, fork)
    for i in parent_clear_slots:
        parent_pre += Op.SSTORE(i, 0)
        _simulate_sstore(page, i, 0, fork)

    child_persists = child_exit == Op.STOP and call_op == Op.DELEGATECALL
    child_code = Bytecode()
    for i in child_prestate_slots:
        child_code += Op.SSTORE(i, 0)
        if child_persists:
            _simulate_sstore(page, i, 0, fork)
    for i in child_growth_slots:
        child_code += Op.SSTORE(i, 1)
        if child_persists:
            _simulate_sstore(page, i, 1, fork)
    for i in child_clear_slots:
        child_code += Op.SSTORE(i, 0)
        if child_persists:
            _simulate_sstore(page, i, 0, fork)
    child_code += child_exit

    tx_data: bytes = b""
    if call_op == Op.CREATE:
        tx_data = bytes(child_code)
        sub_call_code = Op.CALLDATACOPY(0, 0, Op.CALLDATASIZE) + Op.CREATE(
            value=0, offset=0, size=Op.CALLDATASIZE
        )
    else:
        child_address = pre.deploy_contract(child_code)
        sub_call_code = call_op(address=child_address)

    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)
    measure_offset = Spec.SLOTS_PER_PAGE
    measure_code = Bytecode()
    expected_storage: dict[int, int] = {}
    for i in range(Spec.SLOTS_PER_PAGE):
        cost = _simulate_sstore(page, i, 1, fork)
        measure_code += CodeGasMeasure(
            code=Op.SSTORE(i, 1),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=measure_offset + i,
            stop=(i == Spec.SLOTS_PER_PAGE - 1),
        )
        expected_storage[measure_offset + i] = cost
    for i in range(Spec.SLOTS_PER_PAGE):
        expected_storage[i] = 1

    parent_address = pre.deploy_contract(
        parent_pre + sub_call_code + measure_code,
        storage=dict.fromkeys(prestate_slots, 1),
    )

    tx = Transaction(
        gas_limit=fork.transaction_gas_limit_cap(),
        to=parent_address,
        sender=pre.fund_eoa(),
        data=tx_data,
    )

    state_test(
        pre=pre,
        post={parent_address: Account(storage=expected_storage)},
        tx=tx,
    )


def test_subcall_page_warming_persists_across_subcalls(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Page warming map is tx-scoped: warming from sub-call N
    persists into sub-call N+1.
    """
    child_address = pre.deploy_contract(
        Conditional(
            condition=Op.CALLDATASIZE,
            if_true=CodeGasMeasure(
                code=Op.SLOAD(1),
                overhead_cost=Op.PUSH1(0).gas_cost(fork),
                extra_stack_items=1,
                sstore_key=slot_gas_measured,
            ),
            if_false=Op.SLOAD(0),
        )
    )
    parent_address = pre.deploy_contract(
        Op.CALL(address=child_address)
        + Op.CALL(address=child_address, args_offset=0, args_size=1)
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=parent_address,
        sender=pre.fund_eoa(),
    )

    state_test(
        pre=pre,
        post={
            child_address: Account(
                storage={
                    slot_gas_measured: Op.SLOAD(page_load_warm=True).gas_cost(
                        fork
                    ),
                },
            ),
        },
        tx=tx,
    )


@pytest.mark.with_all_create_opcodes
def test_create_initcode_warm_persists_to_post_deploy_call(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    create_opcode: Op,
) -> None:
    """
    Initcode SSTORE warms (new_addr, page 0). Within the same tx,
    the factory CALLs the newly deployed contract — the page
    warming persists, so the runtime's SLOAD(1) on the same page
    is WARM.
    """
    runtime = CodeGasMeasure(
        code=Op.SLOAD(1),
        overhead_cost=Op.PUSH1(0).gas_cost(fork),
        extra_stack_items=1,
        sstore_key=slot_gas_measured,
    )
    initcode = Initcode(
        deploy_code=runtime,
        initcode_prefix=Op.SSTORE(0, value_code_worked),
    )

    factory_code = (
        Op.CALLDATACOPY(0, 0, Op.CALLDATASIZE)
        + Op.CALL(address=create_opcode(size=Op.CALLDATASIZE))
        + Op.SSTORE(slot_result, value_code_worked)
    )
    factory_address = pre.deploy_contract(factory_code)

    new_contract_address = compute_create_address(
        address=factory_address,
        nonce=1,
        initcode=initcode,
        opcode=create_opcode,
    )

    tx = Transaction(
        gas_limit=generous_gas_with_create(fork),
        to=factory_address,
        sender=pre.fund_eoa(),
        data=bytes(initcode),
    )

    expected_gas = Op.SLOAD(page_load_warm=True).gas_cost(fork)

    state_test(
        pre=pre,
        post={
            factory_address: Account(
                storage={slot_result: value_code_worked},
            ),
            new_contract_address: Account(
                storage={
                    0: value_code_worked,
                    slot_gas_measured: expected_gas,
                },
            ),
        },
        tx=tx,
    )
