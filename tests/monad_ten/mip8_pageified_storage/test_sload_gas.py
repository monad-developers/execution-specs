"""
Tests page-level SLOAD gas costs under MIP-8.
"""

import pytest
from execution_testing import (
    AccessList,
    Account,
    Alloc,
    Block,
    BlockchainTestFiller,
    Bytecode,
    CodeGasMeasure,
    Hash,
    Op,
    StateTestFiller,
    Transaction,
    gas_test,
)
from execution_testing.forks.helpers import Fork

from .helpers import generous_gas
from .spec import Spec, ref_spec_8

REFERENCE_SPEC_GIT_PATH = ref_spec_8.git_path
REFERENCE_SPEC_VERSION = ref_spec_8.version

slot_code_worked = 0x1
value_code_worked = 0x1234

pytestmark = [
    pytest.mark.valid_from("MONAD_NEXT"),
]


@pytest.mark.parametrize(
    "slot",
    [0, 1, 127, 128, 255, 256],
    ids=lambda s: f"slot_{s}",
)
def test_sload_cold_page(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    slot: int,
) -> None:
    """
    Test that SLOAD on a never-accessed page costs GAS_COLD_PAGE_READ,
    and a subsequent SLOAD on the same page costs GAS_BASE_SLOAD.
    """
    gas_test(
        fork=fork,
        state_test=state_test,
        pre=pre,
        setup_code=Op.PUSH2(slot),
        subject_code=Op.SLOAD,
        tear_down_code=Op.POP + Op.STOP,
        cold_gas=Spec.GAS_COLD_PAGE_READ,
        warm_gas=Spec.GAS_BASE_SLOAD,
    )


def test_sload_same_page_warm(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test that a second SLOAD on the same page is warm.

    SLOAD(0) warms page 0, then SLOAD(1) should cost
    GAS_BASE_SLOAD since both slots are on page 0.
    """
    overhead = Op.PUSH1(0).gas_cost(fork)
    contract_address = pre.deploy_contract(
        Op.SLOAD(0)
        + Op.POP
        + CodeGasMeasure(
            code=Op.SLOAD(1),
            overhead_cost=overhead,
            extra_stack_items=1,
            sstore_key=0,
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
                storage={0: Spec.GAS_BASE_SLOAD},
            ),
        },
        tx=tx,
    )


def test_sload_cross_page_cold(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test that SLOAD on a different page is cold even after
    warming the first page.

    SLOAD(0) warms page 0, then SLOAD(128) accesses page 1
    which is still cold.
    """
    overhead = Op.PUSH2(0).gas_cost(fork)
    contract_address = pre.deploy_contract(
        Op.SLOAD(0)
        + Op.POP
        + CodeGasMeasure(
            code=Op.SLOAD(128),
            overhead_cost=overhead,
            extra_stack_items=1,
            sstore_key=0,
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
                storage={0: Spec.GAS_COLD_PAGE_READ},
            ),
        },
        tx=tx,
    )


@pytest.mark.parametrize(
    "target_slot,expected_warm",
    [
        (127, True),
        (128, False),
    ],
    ids=["last_slot_page_0", "first_slot_page_1"],
)
def test_sload_page_boundary(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    target_slot: int,
    expected_warm: bool,
) -> None:
    """
    Test page boundary: slot 127 is on page 0, slot 128 is on page 1.

    After warming page 0 via slot 0, slot 127 is warm but slot 128
    is cold.
    """
    expected_gas = (
        Spec.GAS_BASE_SLOAD if expected_warm else Spec.GAS_COLD_PAGE_READ
    )
    overhead = Op.PUSH2(0).gas_cost(fork)
    contract_address = pre.deploy_contract(
        Op.SLOAD(0)
        + Op.POP
        + CodeGasMeasure(
            code=Op.SLOAD(target_slot),
            overhead_cost=overhead,
            extra_stack_items=1,
            sstore_key=0,
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
            contract_address: Account(storage={0: expected_gas}),
        },
        tx=tx,
    )


@pytest.mark.parametrize(
    "target_slot,expected_warm",
    [
        (10, True),
        (200, False),
    ],
    ids=["same_page_warm", "different_page_cold"],
)
def test_sload_acl_warms_page(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    target_slot: int,
    expected_warm: bool,
) -> None:
    """
    Test that an access list entry warms the entire page.

    ACL warms slot 5 (page 0). Slot 10 (page 0) is warm,
    slot 200 (page 1) is cold.
    """
    expected_gas = (
        Spec.GAS_BASE_SLOAD if expected_warm else Spec.GAS_COLD_PAGE_READ
    )
    overhead = Op.PUSH2(0).gas_cost(fork)
    contract_address = pre.deploy_contract(
        CodeGasMeasure(
            code=Op.SLOAD(target_slot),
            overhead_cost=overhead,
            extra_stack_items=1,
            sstore_key=0,
        )
    )

    tx = Transaction(
        ty=1,
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
        access_list=[
            AccessList(
                address=contract_address,
                storage_keys=[Hash(5)],
            ),
        ],
    )

    state_test(
        pre=pre,
        post={
            contract_address: Account(storage={0: expected_gas}),
        },
        tx=tx,
    )


@pytest.mark.parametrize(
    "warming_slot",
    list(range(Spec.SLOTS_PER_PAGE)),
    ids=lambda s: f"warm_via_{s}",
)
@pytest.mark.parametrize(
    "target_slot",
    list(range(Spec.SLOTS_PER_PAGE)),
    ids=lambda s: f"read_{s}",
)
def test_sload_all_slots_warm_page(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    warming_slot: int,
    target_slot: int,
) -> None:
    """
    Test that warming any slot on page 0 makes all other slots
    on page 0 warm.

    Parametrized over all 128 × 128 slot pairs on page 0.
    """
    overhead = Op.PUSH1(0).gas_cost(fork)
    contract_address = pre.deploy_contract(
        Op.SLOAD(warming_slot)
        + Op.POP
        + CodeGasMeasure(
            code=Op.SLOAD(target_slot),
            overhead_cost=overhead,
            extra_stack_items=1,
            sstore_key=0,
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
                storage={0: Spec.GAS_BASE_SLOAD},
            ),
        },
        tx=tx,
    )


def test_sload_page_not_warm_across_txs(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test that page warming does not persist across transactions.

    tx1 warms page 0 via SLOAD(0). tx2 in the same block does
    SLOAD(1) on the same page — should be cold because page
    warming is per-transaction.
    """
    contract_address = pre.deploy_contract(
        Op.SLOAD(0) + Op.SSTORE(slot_code_worked, value_code_worked)
    )

    sender1 = pre.fund_eoa()
    sender2 = pre.fund_eoa()

    tx1 = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=sender1,
    )

    contract2_address = pre.deploy_contract(
        Op.SLOAD(1) + Op.SSTORE(slot_code_worked, value_code_worked)
    )

    tx2 = Transaction(
        gas_limit=generous_gas(fork),
        to=contract2_address,
        sender=sender2,
    )

    blockchain_test(
        pre=pre,
        blocks=[Block(txs=[tx1, tx2])],
        post={
            contract_address: Account(
                storage={slot_code_worked: value_code_worked},
            ),
            contract2_address: Account(
                storage={slot_code_worked: value_code_worked},
            ),
        },
    )


def test_sstore_write_does_not_warm_sload(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test that SSTORE page write does not warm the page for SLOAD.

    SSTORE to slot 0 adds page 0 to write_accessed_pages.
    SLOAD of slot 1 (same page) should still be cold because
    read_accessed_pages is separate from write_accessed_pages.
    """
    overhead = Op.PUSH1(0).gas_cost(fork)
    contract_address = pre.deploy_contract(
        Op.SSTORE(0, 42)
        + CodeGasMeasure(
            code=Op.SLOAD(1),
            overhead_cost=overhead,
            extra_stack_items=1,
            sstore_key=2,
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
                    0: 42,
                    2: Spec.GAS_COLD_PAGE_READ,
                },
            ),
        },
        tx=tx,
    )


@pytest.mark.parametrize("at_limit", [True, False])
def test_max_cold_sload_pages_in_tx(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    at_limit: bool,
) -> None:
    """
    Test maximum cold SLOAD pages fitting in tx gas limit.

    Per iter (PUSH3 + SLOAD + POP) = 3 + 8100 + 2 = 8105 gas.
    Marker SSTORE on a page outside the loaded range proves
    all preceding SLOADs succeeded.

    at_limit=True: N = max → success, marker present.
    at_limit=False: N = max + 1 → OOG, no marker (revert).
    """
    tx_gas_cap = fork.transaction_gas_limit_cap()
    intrinsic = fork.transaction_intrinsic_cost_calculator()(
        calldata=b"", contract_creation=False
    )
    per_iter_gas = (
        Op.PUSH3(0).gas_cost(fork)
        + Spec.GAS_COLD_PAGE_READ
        + Op.POP.gas_cost(fork)
    )
    marker_cost = (
        Op.PUSH1(0).gas_cost(fork)
        + Op.PUSH3(0).gas_cost(fork)
        + Spec.GAS_PAGE_WRITE
        + Spec.GAS_BASE_SSTORE
        + Spec.GAS_NEW_SLOT
    )
    available = tx_gas_cap - intrinsic - marker_cost
    max_n = available // per_iter_gas

    n = max_n if at_limit else max_n + 1
    marker_slot = (max_n + 100) * Spec.SLOTS_PER_PAGE

    code = Bytecode()
    for i in range(n):
        code += Op.POP(Op.SLOAD(i * Spec.SLOTS_PER_PAGE))
    code += Op.SSTORE(marker_slot, value_code_worked)

    contract_address = pre.deploy_contract(code)

    tx = Transaction(
        gas_limit=tx_gas_cap,
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    post_storage = {marker_slot: value_code_worked} if at_limit else {}

    state_test(
        pre=pre,
        post={contract_address: Account(storage=post_storage)},
        tx=tx,
    )


@pytest.mark.parametrize("at_limit", [True, False])
def test_max_consecutive_sload_slots_in_tx(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    at_limit: bool,
) -> None:
    """
    Test maximum consecutive SLOAD slots fitting in tx gas limit.

    Uses counted loop to avoid hitting MAX_CODE_SIZE.
    Per slot (loop body): SLOAD + push/pop/jump overhead.
    First slot per page is cold (8100), rest warm (100).

    Loop body:
      JUMPDEST DUP1 SLOAD POP PUSH1(1) SWAP1 SUB DUP1 PUSH3(loop) JUMPI
      gas: 1 + 3 + sload + 2 + 3 + 3 + 3 + 3 + 3 + 10 = 31 + sload

    Per page (128 slots): (31+8100) + 127*(31+100)
                        = 8131 + 16637 = 24,768 gas.

    at_limit=True: N pages such that total fits.
    at_limit=False: N+1 pages → OOG.
    """
    tx_gas_cap = fork.transaction_gas_limit_cap()
    intrinsic = fork.transaction_intrinsic_cost_calculator()(
        calldata=b"", contract_creation=False
    )
    loop_overhead = 31  # see docstring
    cold_iter = loop_overhead + Spec.GAS_COLD_PAGE_READ
    warm_iter = loop_overhead + Spec.GAS_BASE_SLOAD

    setup_overhead = Op.PUSH3(0).gas_cost(fork)
    marker_cost = (
        Op.PUSH1(0).gas_cost(fork)
        + Op.PUSH3(0).gas_cost(fork)
        + Spec.GAS_PAGE_WRITE
        + Spec.GAS_BASE_SSTORE
        + Spec.GAS_NEW_SLOT
        + Op.POP.gas_cost(fork)
    )
    available = tx_gas_cap - intrinsic - setup_overhead - marker_cost

    # Loop reads slots N-1, N-2, ..., 0 (counter decrements).
    # Simulate to find precise max N.
    max_n = 0
    used = 0
    seen_pages: set[int] = set()
    while True:
        page = max_n // Spec.SLOTS_PER_PAGE
        is_cold = page not in seen_pages
        iter_cost = cold_iter if is_cold else warm_iter
        if used + iter_cost > available:
            break
        used += iter_cost
        seen_pages.add(page)
        max_n += 1

    n_slots = max_n if at_limit else max_n + 1
    marker_slot = (max_n // Spec.SLOTS_PER_PAGE + 100) * Spec.SLOTS_PER_PAGE

    prefix = Op.PUSH3(n_slots)
    loop_dest = len(prefix)
    loop_body = (
        Op.JUMPDEST
        + Op.DUP1
        + Op.SLOAD
        + Op.POP
        + Op.PUSH1(1)
        + Op.SWAP1
        + Op.SUB
        + Op.DUP1
        + Op.PUSH3(loop_dest)
        + Op.JUMPI
    )
    code = (
        prefix + loop_body + Op.POP + Op.SSTORE(marker_slot, value_code_worked)
    )

    contract_address = pre.deploy_contract(code)

    tx = Transaction(
        gas_limit=tx_gas_cap,
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    post_storage = {marker_slot: value_code_worked} if at_limit else {}

    state_test(
        pre=pre,
        post={contract_address: Account(storage=post_storage)},
        tx=tx,
    )


def test_page_warming_per_account(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test that warming page X in account A doesn't warm page X
    in account B.

    Page key is (address, page_index). Warming (A, 0) leaves
    (B, 0) cold even though page index is the same.
    """
    overhead = Op.PUSH1(0).gas_cost(fork)

    # Account B: SLOAD slot 0, measure gas (must be cold)
    contract_b = pre.deploy_contract(
        CodeGasMeasure(
            code=Op.SLOAD(0),
            overhead_cost=overhead,
            extra_stack_items=1,
            sstore_key=0x100,
        )
    )

    # Account A: warm own page 0 via SLOAD, then CALL B
    contract_a = pre.deploy_contract(
        Op.SLOAD(0) + Op.POP + Op.SSTORE(0x101, Op.CALL(address=contract_b))
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_a,
        sender=pre.fund_eoa(),
    )

    state_test(
        pre=pre,
        post={
            contract_a: Account(storage={0x101: 1}),
            contract_b: Account(
                storage={0x100: Spec.GAS_COLD_PAGE_READ},
            ),
        },
        tx=tx,
    )
