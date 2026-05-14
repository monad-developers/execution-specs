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

from .helpers import generous_gas, page_index
from .spec import Spec, ref_spec_8

REFERENCE_SPEC_GIT_PATH = ref_spec_8.git_path
REFERENCE_SPEC_VERSION = ref_spec_8.version

slot_code_worked = 0x01
slot_gas_measured = 0x02
slot_aux = 0x03
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
    """SLOAD on never-accessed page is cold; second SLOAD warm."""
    gas_test(
        fork=fork,
        state_test=state_test,
        pre=pre,
        setup_code=Op.PUSH2(slot),
        subject_code=Op.SLOAD,
        tear_down_code=Op.POP + Op.STOP,
        cold_gas=Op.SLOAD(page_warm=False).gas_cost(fork),
        warm_gas=Op.SLOAD(page_warm=True).gas_cost(fork),
    )


# Slots representing different binary subtree branches within a page.
# Powers of 2 plus extremes hit distinct paths in the 7-level tree.
_PAGE_BRANCH_SLOTS = [0, 1, 32, 64, 96, 127]


@pytest.mark.parametrize("warmed_page", [0, 1], ids=["page_0", "page_1"])
@pytest.mark.parametrize(
    "warmed_offset", _PAGE_BRANCH_SLOTS, ids=lambda s: f"warm_via_{s}"
)
@pytest.mark.parametrize(
    "target_offset", _PAGE_BRANCH_SLOTS, ids=lambda s: f"read_{s}"
)
@pytest.mark.parametrize(
    "target_page_diff",
    [0, 1, -1],
    ids=["same_page", "next_page", "prev_page"],
)
def test_sload_warm_cold_pages(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    warmed_page: int,
    warmed_offset: int,
    target_offset: int,
    target_page_diff: int,
) -> None:
    """
    Cold/warm SLOAD across page boundaries.

    Warm one slot (warmed_page, warmed_offset). Read slot
    on a target page (warmed_page + target_page_diff). Result
    warm if both slots share a page, cold otherwise.
    """
    target_page = warmed_page + target_page_diff
    if target_page < 0:
        pytest.skip("target_page negative")
    warmed_slot = warmed_page * Spec.SLOTS_PER_PAGE + warmed_offset
    target_slot = target_page * Spec.SLOTS_PER_PAGE + target_offset

    if warmed_slot == target_slot:
        # The 'warm' SLOAD becomes the measured one — same slot is fine
        # but contributes no boundary information; skip.
        pytest.skip("warmed and target slot are identical")

    same_page = page_index(warmed_slot) == page_index(target_slot)
    expected_gas = Op.SLOAD(page_warm=same_page).gas_cost(fork)

    contract_address = pre.deploy_contract(
        Op.SLOAD(warmed_slot)
        + Op.POP
        + CodeGasMeasure(
            code=Op.SLOAD(target_slot),
            overhead_cost=Op.PUSH2(0).gas_cost(fork),
            extra_stack_items=1,
            sstore_key=slot_gas_measured,
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
                storage={slot_gas_measured: expected_gas},
            ),
        },
        tx=tx,
    )


@pytest.mark.parametrize("warmed_page", [0, 1], ids=["page_0", "page_1"])
@pytest.mark.parametrize(
    "warmed_offset", _PAGE_BRANCH_SLOTS, ids=lambda s: f"acl_via_{s}"
)
@pytest.mark.parametrize(
    "target_offset", _PAGE_BRANCH_SLOTS, ids=lambda s: f"read_{s}"
)
@pytest.mark.parametrize(
    "target_page_diff",
    [0, 1, -1],
    ids=["same_page", "next_page", "prev_page"],
)
def test_sload_acl_warms_page(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    warmed_page: int,
    warmed_offset: int,
    target_offset: int,
    target_page_diff: int,
) -> None:
    """
    Access list entry warms the entire page, including all slots
    on the same page; cross-page accesses remain cold.
    """
    target_page = warmed_page + target_page_diff
    if target_page < 0:
        pytest.skip("target_page negative")
    warmed_slot = warmed_page * Spec.SLOTS_PER_PAGE + warmed_offset
    target_slot = target_page * Spec.SLOTS_PER_PAGE + target_offset

    same_page = page_index(warmed_slot) == page_index(target_slot)
    expected_gas = Op.SLOAD(page_warm=same_page).gas_cost(fork)

    contract_address = pre.deploy_contract(
        CodeGasMeasure(
            code=Op.SLOAD(target_slot),
            overhead_cost=Op.PUSH2(0).gas_cost(fork),
            extra_stack_items=1,
            sstore_key=slot_gas_measured,
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
                storage_keys=[Hash(warmed_slot)],
            ),
        ],
    )
    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={slot_gas_measured: expected_gas},
            ),
        },
        tx=tx,
    )


# Subtree branch slots within a page (page commits use a binary tree
# over 64 pair-leaves; pick slots that occupy distinct branches at
# different tree levels). 7 picks ≈ O(log2 SLOTS_PER_PAGE).
_TREE_BRANCH_SLOTS = [0, 1, 2, 16, 32, 64, 127]


@pytest.mark.parametrize(
    "warming_slot", _TREE_BRANCH_SLOTS, ids=lambda s: f"warm_via_{s}"
)
@pytest.mark.parametrize(
    "target_slot", _TREE_BRANCH_SLOTS, ids=lambda s: f"read_{s}"
)
def test_sload_all_slots_warm_page(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    warming_slot: int,
    target_slot: int,
) -> None:
    """
    Warming any slot on page 0 makes other slots on page 0 warm.

    Slot picks span distinct binary subtree branches in the
    page's pair-leaf merkle tree.
    """
    contract_address = pre.deploy_contract(
        Op.SLOAD(warming_slot)
        + Op.POP
        + CodeGasMeasure(
            code=Op.SLOAD(target_slot),
            overhead_cost=Op.PUSH1(0).gas_cost(fork),
            extra_stack_items=1,
            sstore_key=slot_gas_measured,
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


@pytest.mark.parametrize(
    "across",
    ["tx", "block"],
)
def test_sload_page_not_warm_across_txs(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
    across: str,
) -> None:
    """
    Page warming does not persist across txs or blocks.

    tx1 warms page 0 via SLOAD(0); tx2 measures SLOAD(1) without
    warming. Both tx the same contract, branched by calldata.

    `across=tx`: both txs in same block.
    `across=block`: txs in two separate blocks.
    """
    from execution_testing import Conditional

    contract_address = pre.deploy_contract(
        Conditional(
            condition=Op.CALLDATASIZE,
            if_true=CodeGasMeasure(
                code=Op.SLOAD(1),
                overhead_cost=Op.PUSH1(0).gas_cost(fork),
                extra_stack_items=1,
                sstore_key=slot_gas_measured,
            ),
            if_false=Op.SLOAD(0) + Op.POP,
        )
    )

    tx1 = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )
    tx2 = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
        data=b"\x01",
    )

    if across == "tx":
        blocks = [Block(txs=[tx1, tx2])]
    else:
        blocks = [Block(txs=[tx1]), Block(txs=[tx2])]

    expected = Op.SLOAD(page_warm=False).gas_cost(fork)
    blockchain_test(
        pre=pre,
        blocks=blocks,
        post={
            contract_address: Account(
                storage={slot_gas_measured: expected},
            ),
        },
    )


def test_sstore_write_warms_sload(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    SSTORE on a cold page pays LOAD_COST and adds the page to
    read_accessed_pages, so a subsequent SLOAD on the same page
    is warm.
    """
    contract_address = pre.deploy_contract(
        Op.SSTORE(0, 42)
        + CodeGasMeasure(
            code=Op.SLOAD(1),
            overhead_cost=Op.PUSH1(0).gas_cost(fork),
            extra_stack_items=1,
            sstore_key=slot_gas_measured,
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
                    slot_gas_measured: Op.SLOAD(page_warm=True).gas_cost(fork),
                },
            ),
        },
        tx=tx,
    )


def test_tstorage_does_not_impact_paged_storage(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    TLOAD/TSTORE on the same slot keys as a paged storage slot
    must not warm the page for SLOAD/SSTORE.
    """
    contract_address = pre.deploy_contract(
        Op.TSTORE(0, 99)
        + Op.POP(Op.TLOAD(0))
        + CodeGasMeasure(
            code=Op.SLOAD(1),
            overhead_cost=Op.PUSH1(0).gas_cost(fork),
            extra_stack_items=1,
            sstore_key=slot_gas_measured,
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
                    slot_gas_measured: Op.SLOAD(page_warm=False).gas_cost(
                        fork
                    ),
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
    Maximum cold SLOAD pages fitting in tx gas limit.

    at_limit=True: N = max → success, marker present.
    at_limit=False: N = max + 1 → OOG, marker missing.
    """
    tx_gas_cap = fork.transaction_gas_limit_cap()
    intrinsic = fork.transaction_intrinsic_cost_calculator()(
        calldata=b"", contract_creation=False
    )
    per_iter_gas = (
        Op.PUSH3(0).gas_cost(fork)
        + Op.SLOAD(page_warm=False).gas_cost(fork)
        + Op.POP.gas_cost(fork)
    )
    fresh_sstore_gas = Op.SSTORE(
        page_load_warm=False,
        page_write_warm=False,
        current_value=0,
        new_value=value_code_worked,
    ).gas_cost(fork)
    marker_cost = (
        Op.PUSH2(0).gas_cost(fork)
        + Op.PUSH3(0).gas_cost(fork)
        + fresh_sstore_gas
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
    Maximum consecutive SLOAD slots fitting in tx gas limit.

    Counted loop reads slots N-1 down to 0. First slot per page
    is cold, subsequent on same page are warm. Marker SSTORE
    after loop proves all reads succeeded.
    """
    tx_gas_cap = fork.transaction_gas_limit_cap()
    intrinsic = fork.transaction_intrinsic_cost_calculator()(
        calldata=b"", contract_creation=False
    )
    # Loop body opcodes: JUMPDEST PUSH1 SWAP1 SUB DUP1 SLOAD POP DUP1
    # PUSH3 JUMPI. Gas (excl. SLOAD): 1+3+3+3+3+2+3+3+10 = 31.
    loop_overhead = 31
    cold_iter = loop_overhead + Op.SLOAD(page_warm=False).gas_cost(fork)
    warm_iter = loop_overhead + Op.SLOAD(page_warm=True).gas_cost(fork)

    setup_overhead = Op.PUSH3(0).gas_cost(fork)
    fresh_sstore_gas = Op.SSTORE(
        page_load_warm=False,
        page_write_warm=False,
        current_value=0,
        new_value=value_code_worked,
    ).gas_cost(fork)
    marker_cost = (
        Op.PUSH2(0).gas_cost(fork)
        + Op.PUSH3(0).gas_cost(fork)
        + fresh_sstore_gas
        + Op.POP.gas_cost(fork)
    )
    available = tx_gas_cap - intrinsic - setup_overhead - marker_cost

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
        + Op.PUSH1(1)
        + Op.SWAP1
        + Op.SUB
        + Op.DUP1
        + Op.SLOAD
        + Op.POP
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
    Warming page X in account A doesn't warm page X in account B.

    Page key is (address, page_index): (A, 0) and (B, 0) are
    independent.
    """
    contract_b = pre.deploy_contract(
        CodeGasMeasure(
            code=Op.SLOAD(0),
            overhead_cost=Op.PUSH1(0).gas_cost(fork),
            extra_stack_items=1,
            sstore_key=slot_gas_measured,
        )
    )
    contract_a = pre.deploy_contract(
        Op.SLOAD(0)
        + Op.POP
        + Op.SSTORE(slot_code_worked, Op.CALL(address=contract_b))
    )
    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_a,
        sender=pre.fund_eoa(),
    )
    state_test(
        pre=pre,
        post={
            contract_a: Account(storage={slot_code_worked: 1}),
            contract_b: Account(
                storage={
                    slot_gas_measured: Op.SLOAD(page_warm=False).gas_cost(
                        fork
                    ),
                },
            ),
        },
        tx=tx,
    )
