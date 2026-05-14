"""
Tests cross-call page warming propagation under MIP-8.
"""

import pytest
from execution_testing import (
    Account,
    Alloc,
    CodeGasMeasure,
    Initcode,
    Op,
    StateTestFiller,
    Transaction,
)
from execution_testing.forks.helpers import Fork

from .helpers import generous_gas
from .spec import ref_spec_8

REFERENCE_SPEC_GIT_PATH = ref_spec_8.git_path
REFERENCE_SPEC_VERSION = ref_spec_8.version

slot_result = 0x100
slot_gas_measured = 0x101
value_code_worked = 0x1234

pytestmark = [
    pytest.mark.valid_from("MONAD_NEXT"),
]

# --- Call opcode tests ---


@pytest.mark.with_all_call_opcodes
def test_call_child_inherits_warm_pages(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    call_opcode: Op,
) -> None:
    """
    Test page warming inheritance across call opcodes.

    Parent warms page 0 of parent_address via SLOAD(0), then
    calls child. Page keys are (address, page_index):
    - DELEGATECALL/CALLCODE: child runs as parent_address, so
      (parent_addr, 0) is warm → child SLOAD(1) costs 100
    - CALL/STATICCALL: child runs at child_address, so
      (child_addr, 0) is cold → child SLOAD(1) costs 8100
    """
    overhead = Op.PUSH1(0).gas_cost(fork)
    runs_in_parent_context = call_opcode in (
        Op.DELEGATECALL,
        Op.CALLCODE,
    )

    if call_opcode == Op.STATICCALL:
        child_code = Op.SLOAD(1) + Op.STOP
    else:
        child_code = CodeGasMeasure(
            code=Op.SLOAD(1),
            overhead_cost=overhead,
            extra_stack_items=1,
            sstore_key=slot_gas_measured,
        )
    child_address = pre.deploy_contract(child_code)

    parent_code = (
        Op.SLOAD(0)
        + Op.POP
        + Op.SSTORE(
            slot_result,
            call_opcode(address=child_address),
        )
    )
    parent_address = pre.deploy_contract(parent_code)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=parent_address,
        sender=pre.fund_eoa(),
    )

    expected_gas = Op.SLOAD(page_warm=runs_in_parent_context).gas_cost(fork)

    parent_storage = {slot_result: 1}
    child_storage = {}

    if runs_in_parent_context:
        parent_storage[slot_gas_measured] = expected_gas
    elif call_opcode == Op.CALL:
        child_storage[slot_gas_measured] = expected_gas

    post = {parent_address: Account(storage=parent_storage)}
    if child_storage:
        post[child_address] = Account(storage=child_storage)

    state_test(pre=pre, post=post, tx=tx)


@pytest.mark.parametrize(
    "success_mode",
    [
        pytest.param(Op.STOP, id="stop"),
        pytest.param(Op.RETURN(0, 0), id="return"),
    ],
)
@pytest.mark.with_all_call_opcodes
def test_call_child_warm_pages_propagate_on_success(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    call_opcode: Op,
    success_mode: Op,
) -> None:
    """
    Test page warming propagation on successful child call.

    Child warms page 1 via SLOAD(128), then exits successfully.
    Parent does SLOAD(129) on page 1 afterward.

    Page keys are (address, page_index):
    - DELEGATECALL/CALLCODE: child runs as parent_address, so
      warming (parent_addr, 1) propagates → parent SLOAD warm
    - CALL/STATICCALL: child warms (child_addr, 1), parent
      checks (parent_addr, 1) which is different → cold
    """
    overhead = Op.PUSH2(0).gas_cost(fork)
    runs_in_parent_context = call_opcode in (
        Op.DELEGATECALL,
        Op.CALLCODE,
    )

    child_code = Op.SLOAD(128) + Op.POP + success_mode
    child_address = pre.deploy_contract(child_code)

    parent_code = Op.SSTORE(
        slot_result,
        call_opcode(address=child_address),
    ) + CodeGasMeasure(
        code=Op.SLOAD(129),
        overhead_cost=overhead,
        extra_stack_items=1,
        sstore_key=slot_gas_measured,
    )
    parent_address = pre.deploy_contract(parent_code)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=parent_address,
        sender=pre.fund_eoa(),
    )

    expected_gas = Op.SLOAD(page_warm=runs_in_parent_context).gas_cost(fork)

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
        pytest.param(Op.REVERT(0, 0), id="revert"),
        pytest.param(Op.INVALID, id="invalid"),
        pytest.param(Op.MLOAD(8 * 1024 * 1024), id="oom"),
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
    Test that child's warm pages don't propagate on revert.

    Child warms page 1 via SLOAD(128), then reverts/fails.
    Parent does SLOAD(129) on page 1 — should be cold.

    Use explicit gas limit for child call so parent retains
    enough gas for CodeGasMeasure after child OOGs.
    """
    overhead = Op.PUSH2(0).gas_cost(fork)

    child_code = Op.SLOAD(128) + Op.POP + revert_cause
    child_address = pre.deploy_contract(child_code)

    parent_code = Op.SSTORE(
        slot_result,
        call_opcode(address=child_address, gas=100_000),
    ) + CodeGasMeasure(
        code=Op.SLOAD(129),
        overhead_cost=overhead,
        extra_stack_items=1,
        sstore_key=slot_gas_measured,
    )
    parent_address = pre.deploy_contract(parent_code)

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
                    slot_result: 0,
                    slot_gas_measured: Op.SLOAD(page_warm=False).gas_cost(
                        fork
                    ),
                },
            ),
        },
        tx=tx,
    )


# --- Create opcode tests ---


@pytest.mark.with_all_create_opcodes
def test_create_child_inherits_warm_pages(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    create_opcode: Op,
) -> None:
    """
    Test that created contract inherits parent's warm pages.

    Parent warms page 0 via SLOAD(0), then CREATE/CREATE2.
    Initcode does SLOAD(1) — the created contract has its own
    storage, but page warming is tracked globally per address.
    We verify the CREATE succeeds (warm pages don't interfere).
    """
    initcode = Initcode(deploy_code=Op.STOP)

    parent_code = (
        Op.SLOAD(0)
        + Op.POP
        + Op.MSTORE(0, Op.PUSH32(bytes(initcode)))
        + Op.POP(create_opcode(size=len(initcode)))
        + Op.SSTORE(slot_result, value_code_worked)
    )
    parent_address = pre.deploy_contract(parent_code)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=parent_address,
        sender=pre.fund_eoa(),
    )

    state_test(
        pre=pre,
        post={
            parent_address: Account(
                storage={slot_result: value_code_worked},
            ),
        },
        tx=tx,
    )


@pytest.mark.parametrize(
    "success_mode",
    [
        pytest.param(Op.STOP, id="stop"),
        pytest.param(Op.RETURN(0, 0), id="return"),
    ],
)
@pytest.mark.with_all_create_opcodes
def test_create_child_warm_pages_propagate_on_success(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    create_opcode: Op,
    success_mode: Op,
) -> None:
    """
    Test that created contract's warm pages propagate to parent.

    Initcode warms page 1 (slot 128) of the new contract, then
    deploys. Parent does SLOAD on its own page 1 — since page
    warming is per (address, page), parent's page 1 is still cold.
    We verify the create succeeded.
    """
    initcode_code = Op.SLOAD(128) + Op.POP + success_mode
    initcode_bytes = bytes(initcode_code)
    padded = initcode_bytes + b"\x00" * (32 - (len(initcode_bytes) % 32))

    parent_code = (
        Op.MSTORE(0, Op.PUSH32(padded[:32]))
        + Op.POP(create_opcode(size=len(initcode_bytes)))
        + Op.SSTORE(slot_result, value_code_worked)
    )
    parent_address = pre.deploy_contract(parent_code)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=parent_address,
        sender=pre.fund_eoa(),
    )

    state_test(
        pre=pre,
        post={
            parent_address: Account(
                storage={slot_result: value_code_worked},
            ),
        },
        tx=tx,
    )


@pytest.mark.parametrize(
    "revert_cause",
    [
        pytest.param(Op.REVERT(0, 0), id="revert"),
        pytest.param(Op.INVALID, id="invalid"),
        pytest.param(Op.MLOAD(8 * 1024 * 1024), id="oom"),
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

    Initcode warms page 1 then reverts. Parent verifies
    the CREATE returned 0 (failure).
    """
    initcode_code = Op.SLOAD(128) + Op.POP + revert_cause
    initcode_bytes = bytes(initcode_code)
    padded = initcode_bytes + b"\x00" * (32 - (len(initcode_bytes) % 32))

    parent_code = (
        Op.MSTORE(0, Op.PUSH32(padded[:32]))
        + Op.POP(create_opcode(size=len(initcode_bytes)))
        + Op.SSTORE(slot_result, value_code_worked)
    )
    parent_address = pre.deploy_contract(parent_code)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=parent_address,
        sender=pre.fund_eoa(),
    )

    state_test(
        pre=pre,
        post={
            parent_address: Account(
                storage={slot_result: value_code_worked},
            ),
        },
        tx=tx,
    )


# --- DELEGATECALL self-warming test ---


def test_delegatecall_self_warms_pages(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test that DELEGATECALL to self warms pages and they propagate
    back to the caller frame.

    DELEGATECALL runs in caller's storage context, so warming
    page (parent_addr, 1) inside child propagates back as
    (parent_addr, 1) in parent.

    Use Conditional bytecode: branch on calldatasize.
    - calldata empty (top-level): DELEGATECALL self with 1 byte
      data, then measure SLOAD(129) gas (must be warm).
    - calldata nonempty (inner): SLOAD(128) and STOP.
    """
    from execution_testing import Conditional

    overhead = Op.PUSH2(0).gas_cost(fork)

    inner_code = Op.SLOAD(128) + Op.POP + Op.STOP
    outer_code = Op.POP(
        Op.DELEGATECALL(
            gas=Op.GAS,
            address=Op.ADDRESS,
            args_offset=0,
            args_size=1,
            ret_offset=0,
            ret_size=0,
        )
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
                    slot_gas_measured: Op.SLOAD(page_warm=True).gas_cost(fork)
                },
            ),
        },
        tx=tx,
    )
