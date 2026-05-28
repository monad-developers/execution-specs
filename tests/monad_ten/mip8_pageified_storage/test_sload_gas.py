"""
Tests page-level SLOAD gas costs under MIP-8.
"""

from typing import Callable

import pytest
from execution_testing import (
    AccessList,
    Account,
    Address,
    Alloc,
    Block,
    BlockchainTestFiller,
    Bytecode,
    CodeGasMeasure,
    Conditional,
    Hash,
    Op,
    StateTestFiller,
    Transaction,
    gas_test,
    oog_test,
)
from execution_testing.forks.helpers import Fork

from .helpers import TxPageState, generous_gas, page_index, simulate_sstore
from .spec import Spec, ref_spec_8

REFERENCE_SPEC_GIT_PATH = ref_spec_8.git_path
REFERENCE_SPEC_VERSION = ref_spec_8.version

slot_code_worked = 0x01
slot_gas_measured = 0x02
slot_gas_measured_2 = 0x03
slot_gas_measured_3 = 0x04
value_code_worked = 0x1234

pytestmark = [
    pytest.mark.valid_from("MONAD_NEXT"),
]


@pytest.mark.parametrize(
    "slot",
    [
        0,
        1,
        127,
        128,
        255,
        256,
        # Final page (page index 2**249 - 1) — page-arithmetic boundary.
        2**256 - 128,
        2**256 - 1,
    ],
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
        setup_code=Op.PUSH32(slot),
        subject_code=Op.SLOAD,
        tear_down_code=Op.POP + Op.STOP,
        cold_gas=Op.SLOAD(page_load_warm=False).gas_cost(fork),
        warm_gas=Op.SLOAD(page_load_warm=True).gas_cost(fork),
    )


# Slots covering distinct binary subtree branches within a page.
_PAGE_BRANCH_SLOTS = [0, 1, 2, 16, 32, 64, 96, 127]


@pytest.mark.parametrize("warming_mode", ["sload", "acl"])
@pytest.mark.parametrize("warmed_page", [1, 2**7 - 2])
@pytest.mark.parametrize("warmed_offset", _PAGE_BRANCH_SLOTS)
@pytest.mark.parametrize("target_offset", _PAGE_BRANCH_SLOTS)
@pytest.mark.parametrize(
    "target_page_diff",
    [0, 1, -1],
    ids=["same_page", "next_page", "prev_page"],
)
def test_sload_cross_page_warming(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    warmed_page: int,
    warmed_offset: int,
    target_offset: int,
    target_page_diff: int,
    warming_mode: str,
) -> None:
    """
    Cold/warm SLOAD across page boundaries.

    Warm one slot (warmed_page, warmed_offset). Read slot
    on a target page (warmed_page + target_page_diff). Result
    warm if both slots share a page, cold otherwise.
    """
    target_page = warmed_page + target_page_diff

    warmed_slot = warmed_page * Spec.SLOTS_PER_PAGE + warmed_offset
    target_slot = target_page * Spec.SLOTS_PER_PAGE + target_offset

    same_page = page_index(warmed_slot) == page_index(target_slot)
    expected_gas = Op.SLOAD(page_load_warm=same_page).gas_cost(fork)

    measure = CodeGasMeasure(
        code=Op.SLOAD(target_slot),
        overhead_cost=Op.PUSH2(0).gas_cost(fork),
        extra_stack_items=1,
        sstore_key=slot_gas_measured,
    )

    if warming_mode == "sload":
        contract_address = pre.deploy_contract(Op.SLOAD(warmed_slot) + measure)
        tx = Transaction(
            gas_limit=generous_gas(fork),
            to=contract_address,
            sender=pre.fund_eoa(),
        )
    else:
        contract_address = pre.deploy_contract(measure)
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


@pytest.mark.parametrize(
    "warm_slot,measured_slot",
    [
        pytest.param(
            2**256 - Spec.SLOTS_PER_PAGE,
            2**256 - 1,
            id="first_then_last",
        ),
        pytest.param(
            2**256 - 1,
            2**256 - Spec.SLOTS_PER_PAGE,
            id="last_then_first",
        ),
    ],
)
def test_sload_max_slot_page_boundary(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    warm_slot: int,
    measured_slot: int,
) -> None:
    """Verify SLOAD page arithmetic at the slot-key field boundary."""
    expected_gas = Op.SLOAD(page_load_warm=True).gas_cost(fork)

    contract_address = pre.deploy_contract(
        Op.SLOAD(warm_slot)
        + CodeGasMeasure(
            code=Op.SLOAD(measured_slot),
            overhead_cost=Op.PUSH32(0).gas_cost(fork),
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
    contract_address = pre.deploy_contract(
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

    sender = pre.fund_eoa()
    tx_setup = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=sender,
    )
    tx_measure = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=sender,
        data=b"\x01",
    )

    if across == "tx":
        blocks = [Block(txs=[tx_setup, tx_measure])]
    else:
        blocks = [Block(txs=[tx_setup]), Block(txs=[tx_measure])]

    expected_gas = Op.SLOAD(page_load_warm=False).gas_cost(fork)
    blockchain_test(
        pre=pre,
        blocks=blocks,
        post={
            contract_address: Account(
                storage={slot_gas_measured: expected_gas},
            ),
        },
    )


def test_sstore_warms_sload(
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
                    slot_gas_measured: Op.SLOAD(page_load_warm=True).gas_cost(
                        fork
                    ),
                },
            ),
        },
        tx=tx,
    )


def test_tstore_and_sload(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """TLOAD/TSTORE independent of SLOAD/SSTORE."""
    overhead = Op.PUSH1(0).gas_cost(fork)
    sstore_overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)

    page = TxPageState(read_warm=True)
    simulate_sstore(page, slot_gas_measured, 1, fork)
    expected_sstore_gas = simulate_sstore(page, 0, 1, fork)

    contract_address = pre.deploy_contract(
        Op.TSTORE(0, 99)
        + Op.TLOAD(0)
        + CodeGasMeasure(
            code=Op.SLOAD(0),
            overhead_cost=overhead,
            extra_stack_items=1,
            sstore_key=slot_gas_measured,
            stop=False,
        )
        + CodeGasMeasure(
            code=Op.SSTORE(0, 1),
            overhead_cost=sstore_overhead,
            extra_stack_items=0,
            sstore_key=slot_gas_measured_2,
            stop=False,
        )
        + CodeGasMeasure(
            code=Op.TLOAD(0),
            overhead_cost=overhead,
            extra_stack_items=1,
            sstore_key=slot_gas_measured_3,
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
                    0: 1,
                    slot_gas_measured: Op.SLOAD(page_load_warm=False).gas_cost(
                        fork
                    ),
                    slot_gas_measured_2: expected_sstore_gas,
                    slot_gas_measured_3: Op.TLOAD.gas_cost(fork),
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
    assert tx_gas_cap is not None
    intrinsic = fork.transaction_intrinsic_cost_calculator()(
        calldata=b"", contract_creation=False
    )
    per_iter_gas = (Op.SLOAD(0, page_load_warm=False) + Op.POP).gas_cost(fork)
    fresh_sstore_gas = Op.SSTORE(
        page_load_warm=False,
        page_write_warm=False,
        current_value=0,
        new_value=value_code_worked,
    ).gas_cost(fork)
    marker_cost = (Op.PUSH2(0) + Op.PUSH3(0)).gas_cost(fork) + fresh_sstore_gas
    available = tx_gas_cap - intrinsic - marker_cost
    max_n = available // per_iter_gas

    # sanity check we're testing anything at all
    assert max_n > 10

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
    assert tx_gas_cap is not None
    intrinsic = fork.transaction_intrinsic_cost_calculator()(
        calldata=b"", contract_creation=False
    )
    prefix = Op.PUSH3(0)  # placeholder; rebuilt below with real n_slots
    loop_dest = len(prefix)

    def _loop_body(page_warm: bool) -> Bytecode:
        return (
            Op.JUMPDEST
            + Op.PUSH1(1)
            + Op.SWAP1
            + Op.SUB
            + Op.DUP1
            + Op.SLOAD(page_load_warm=page_warm)
            + Op.POP
            + Op.DUP1
            + Op.PUSH3(loop_dest)
            + Op.JUMPI
        )

    cold_iter = _loop_body(False).gas_cost(fork)
    warm_iter = _loop_body(True).gas_cost(fork)

    setup_overhead = Op.PUSH3(0).gas_cost(fork)
    fresh_sstore_gas = Op.SSTORE(
        page_load_warm=False,
        page_write_warm=False,
        current_value=0,
        new_value=value_code_worked,
    ).gas_cost(fork)
    marker_cost = (Op.PUSH2(0) + Op.PUSH3(0) + Op.POP).gas_cost(
        fork
    ) + fresh_sstore_gas
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

    # sanity check we're testing anything at all
    assert max_n > 10

    n_slots = max_n if at_limit else max_n + 1
    marker_slot = (max_n // Spec.SLOTS_PER_PAGE + 100) * Spec.SLOTS_PER_PAGE

    code = (
        Op.PUSH3(n_slots)
        + _loop_body(False)
        + Op.POP
        + Op.SSTORE(marker_slot, value_code_worked)
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


@pytest.mark.parametrize("read_pattern", ["same_slot", "same_page"])
@pytest.mark.parametrize("at_limit", [True, False])
def test_max_warm_sload_iters_in_tx(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    at_limit: bool,
    read_pattern: str,
) -> None:
    """
    Max SLOAD iterations fitting in tx gas, all targeting the
    same warmed page.

    `same_slot`: SLOAD(0) every iter.
    `same_page`: SLOAD(counter & 0x7F) every iter — slot rotates
    within page 0 across the loop range.

    First iter pays cold-page load; rest are warm.
    """
    tx_gas_cap = fork.transaction_gas_limit_cap()
    assert tx_gas_cap is not None
    intrinsic = fork.transaction_intrinsic_cost_calculator()(
        calldata=b"", contract_creation=False
    )

    prefix = Op.PUSH3(0)  # placeholder
    loop_dest = len(prefix)

    def _loop_body(page_warm: bool) -> Bytecode:
        if read_pattern == "same_slot":
            sload_seq = Op.SLOAD(0, page_load_warm=page_warm)
        else:
            # Slot = counter & 0x7F — counter still needs DUP for AND.
            sload_seq = Op.SLOAD(
                Op.AND(Op.DUP1, Spec.SLOTS_PER_PAGE - 1),
                page_load_warm=page_warm,
            )
        return (
            Op.JUMPDEST
            + sload_seq
            + Op.POP
            + Op.PUSH1(1)
            + Op.SWAP1
            + Op.SUB
            + Op.DUP1
            + Op.PUSH3(loop_dest)
            + Op.JUMPI
        )

    cold_iter = _loop_body(False).gas_cost(fork)
    warm_iter = _loop_body(True).gas_cost(fork)

    setup_overhead = Op.PUSH3(0).gas_cost(fork)
    fresh_sstore_gas = Op.SSTORE(
        page_load_warm=False,
        page_write_warm=False,
        current_value=0,
        new_value=value_code_worked,
    ).gas_cost(fork)
    marker_cost = (Op.PUSH2(0) + Op.PUSH3(0) + Op.POP).gas_cost(
        fork
    ) + fresh_sstore_gas
    available = tx_gas_cap - intrinsic - setup_overhead - marker_cost

    # First iter cold (one cold page touch), rest warm.
    assert available >= cold_iter
    max_n = 1 + (available - cold_iter) // warm_iter

    assert max_n > 1000

    n_slots = max_n if at_limit else max_n + 1
    # Marker on a separate page so it doesn't affect page-0 warming
    # accounting until after the loop.
    marker_slot = 100 * Spec.SLOTS_PER_PAGE

    code = (
        Op.PUSH3(n_slots)
        + _loop_body(False)
        + Op.POP
        + Op.SSTORE(marker_slot, value_code_worked)
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
        Op.SLOAD(0) + Op.SSTORE(slot_code_worked, Op.CALL(address=contract_b))
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
                    slot_gas_measured: Op.SLOAD(page_load_warm=False).gas_cost(
                        fork
                    ),
                },
            ),
        },
        tx=tx,
    )


@pytest.mark.parametrize(
    "al_shape",
    [
        "duplicate_keys",
        "empty_keys",
        "wrong_contract",
        "wrong_eoa",
        "wrong_precompile",
    ],
)
def test_sload_acl_edge_cases(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    al_shape: str,
) -> None:
    """
    Access-list edge cases for page warming.

    `duplicate_keys`: same (address, slot) listed twice — page is
    warm for the measured SLOAD (warming idempotent; second entry
    still pays its 1900 gas).
    `empty_keys`: AL entry for the target address with no storage
    keys — warms the account but no pages.
    `wrong_contract` / `wrong_eoa` / `wrong_precompile`: AL entry
    declares a slot on a non-target address. The tx-target's
    identical slot remains cold.
    """
    contract_address = pre.deploy_contract(
        CodeGasMeasure(
            code=Op.SLOAD(0),
            overhead_cost=Op.PUSH1(0).gas_cost(fork),
            extra_stack_items=1,
            sstore_key=slot_gas_measured,
        )
    )
    sender = pre.fund_eoa()
    if al_shape == "duplicate_keys":
        access_list = [
            AccessList(
                address=contract_address,
                storage_keys=[Hash(0), Hash(0)],
            ),
        ]
        expected_warm = True
    elif al_shape == "empty_keys":
        access_list = [
            AccessList(address=contract_address, storage_keys=[]),
        ]
        expected_warm = False
    else:
        if al_shape == "wrong_contract":
            other = pre.deploy_contract(Op.STOP)
        elif al_shape == "wrong_eoa":
            other = sender
        else:  # wrong_precompile
            other = Address(0x01)
        access_list = [
            AccessList(address=other, storage_keys=[Hash(0)]),
        ]
        expected_warm = False

    tx = Transaction(
        ty=1,
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=sender,
        access_list=access_list,
    )

    expected_gas = Op.SLOAD(page_load_warm=expected_warm).gas_cost(fork)
    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={slot_gas_measured: expected_gas},
            ),
        },
        tx=tx,
    )


def test_sload_acl_multipage_warming(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Multi-key access list warms each declared page independently.

    AL declares two keys on page 0 (slot 0) and page 2 (slot 256).
    Page 1 has no AL entry. The contract measures cold/warm SLOAD
    cost on each of the three pages:

    - page 0 (slot 1, sibling of declared slot 0): WARM.
    - page 1 (slot 128, no AL entry): COLD.
    - page 2 (slot 257, sibling of declared slot 256): WARM.
    """
    p0_slot = 1
    p1_slot = Spec.SLOTS_PER_PAGE
    p2_slot = 2 * Spec.SLOTS_PER_PAGE + 1

    # Measurement slots on far-away pages so the recording SSTORE
    # does not warm the pages under measurement.
    p0_meas = 1000 * Spec.SLOTS_PER_PAGE
    p1_meas = 1001 * Spec.SLOTS_PER_PAGE
    p2_meas = 1002 * Spec.SLOTS_PER_PAGE

    overhead = Op.PUSH2(0).gas_cost(fork)
    contract_address = pre.deploy_contract(
        CodeGasMeasure(
            code=Op.SLOAD(p0_slot),
            overhead_cost=overhead,
            extra_stack_items=1,
            sstore_key=p0_meas,
            stop=False,
        )
        + CodeGasMeasure(
            code=Op.SLOAD(p1_slot),
            overhead_cost=overhead,
            extra_stack_items=1,
            sstore_key=p1_meas,
            stop=False,
        )
        + CodeGasMeasure(
            code=Op.SLOAD(p2_slot),
            overhead_cost=overhead,
            extra_stack_items=1,
            sstore_key=p2_meas,
        )
    )

    warm = Op.SLOAD(page_load_warm=True).gas_cost(fork)
    cold = Op.SLOAD(page_load_warm=False).gas_cost(fork)

    tx = Transaction(
        ty=1,
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
        access_list=[
            AccessList(
                address=contract_address,
                storage_keys=[Hash(0), Hash(2 * Spec.SLOTS_PER_PAGE)],
            ),
        ],
    )
    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={p0_meas: warm, p1_meas: cold, p2_meas: warm},
            ),
        },
        tx=tx,
    )


@pytest.mark.parametrize(
    "probe_code",
    [
        pytest.param(lambda addr: Op.BALANCE(addr), id="balance"),
        pytest.param(lambda addr: Op.EXTCODESIZE(addr), id="extcodesize"),
        pytest.param(lambda addr: Op.EXTCODEHASH(addr), id="extcodehash"),
        pytest.param(
            lambda addr: Op.EXTCODECOPY(addr, 0, 0, 32), id="extcodecopy"
        ),
    ],
)
def test_account_probe_does_not_warm_pages(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    probe_code: Callable[[Address], Bytecode],
) -> None:
    """
    Address-level probes (BALANCE/EXTCODESIZE/EXTCODEHASH/EXTCODECOPY)
    warm the account for EIP-2929 but never warm its storage pages.
    """
    target_address = pre.deploy_contract(
        CodeGasMeasure(
            code=Op.SLOAD(0),
            overhead_cost=Op.PUSH1(0).gas_cost(fork),
            extra_stack_items=1,
            sstore_key=slot_gas_measured,
        )
    )

    caller = pre.deploy_contract(
        probe_code(target_address)
        + Op.SSTORE(slot_code_worked, Op.CALL(address=target_address))
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=caller,
        sender=pre.fund_eoa(),
    )

    state_test(
        pre=pre,
        post={
            caller: Account(storage={slot_code_worked: 1}),
            target_address: Account(
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
    "scenario",
    [
        "cold",
        "warm_via_sload",
        "warm_via_sstore",
        "cross_page_cold",
    ],
)
def test_sload_oog(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    scenario: str,
) -> None:
    """SLOAD OOG across page-warming variants."""
    setup: Bytecode | None = None
    if scenario == "cold":
        subject = Op.SLOAD(0)
    elif scenario == "warm_via_sload":
        setup = Op.SLOAD(0)
        subject = Op.SLOAD(0, page_load_warm=True)
    elif scenario == "warm_via_sstore":
        setup = Op.SSTORE(1, 1)
        subject = Op.SLOAD(0, page_load_warm=True)
    else:  # cross_page_cold
        setup = Op.SLOAD(0)
        subject = Op.SLOAD(Spec.SLOTS_PER_PAGE)

    oog_test(
        fork=fork,
        state_test=state_test,
        pre=pre,
        setup_code=setup,
        subject_code=subject,
    )
