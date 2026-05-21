"""
Tests page-level SSTORE gas costs under MIP-8.
"""

from typing import cast

import pytest
from execution_testing import (
    AccessList,
    Account,
    Alloc,
    Block,
    BlockchainTestFiller,
    Bytecode,
    CodeGasMeasure,
    Conditional,
    Hash,
    Op,
    Opcode,
    StateTestFiller,
    Transaction,
    gas_test,
)
from execution_testing.base_types.conversions import NumberConvertible
from execution_testing.forks.helpers import Fork

from .helpers import (
    STATE_TRANSITIONS,
    TxPageState,
    full_page_sweep_gas,
    generous_gas,
    page_index,
    simulate_sstore,
)
from .spec import Spec, ref_spec_8

REFERENCE_SPEC_GIT_PATH = ref_spec_8.git_path
REFERENCE_SPEC_VERSION = ref_spec_8.version

slot_gas_measured = 0x100
slot_gas_measured_2 = 0x101
value_code_worked = 0x1234

pytestmark = [pytest.mark.valid_from("MONAD_NEXT")]


def _setup_current(slot: int, orig: int, curr: int) -> Bytecode:
    """Bytecode that drives the slot to `curr` from initial `orig`."""
    if orig == curr:
        return Bytecode()
    return Op.SSTORE(slot, curr)


@pytest.mark.parametrize(
    "target_loc", ["same_slot", "same_page", "different_page"]
)
@pytest.mark.parametrize("orig,curr,new", STATE_TRANSITIONS)
def test_sstore_state_transitions(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    orig: int,
    curr: int,
    new: int,
    target_loc: str,
) -> None:
    """
    Measured SSTORE on a slot whose page warming varies by
    `target_loc`. Setup drives slot 0 (page 0) from `orig`
    to `curr`.
    """
    setup_slot = 0
    setup = _setup_current(setup_slot, orig, curr)

    if target_loc == "same_slot":
        target_slot = setup_slot
        same_page = True
    elif target_loc == "same_page":
        target_slot = 1
        same_page = True
    else:
        target_slot = Spec.SLOTS_PER_PAGE
        same_page = False

    page = TxPageState(slots={setup_slot: orig} if orig != 0 else {})
    if same_page and orig != curr:
        simulate_sstore(page, setup_slot, curr, fork)
    elif not same_page:
        # Target on a different page — fresh page state.
        page = TxPageState()
    expected_gas = simulate_sstore(page, target_slot, new, fork)

    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)
    contract_address = pre.deploy_contract(
        setup
        + CodeGasMeasure(
            code=Op.SSTORE(target_slot, new),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=slot_gas_measured,
        ),
        storage={setup_slot: orig} if orig != 0 else {},
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    expected_storage = {slot_gas_measured: expected_gas}
    if target_loc == "same_slot":
        if new != 0:
            expected_storage[setup_slot] = new
    else:
        if new != 0:
            expected_storage[target_slot] = new
        if curr != 0:
            expected_storage[setup_slot] = curr

    state_test(
        pre=pre,
        post={contract_address: Account(storage=expected_storage)},
        tx=tx,
    )


@pytest.mark.parametrize(
    "curr,new",
    [
        pytest.param(0, 0, id="0_0"),
        pytest.param(0, 1, id="0_X"),
        pytest.param(5, 5, id="X_X"),
        pytest.param(5, 0, id="X_0"),
        pytest.param(5, 6, id="X_Y"),
    ],
)
def test_sstore_cold_then_warm(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    curr: int,
    new: int,
) -> None:
    """
    SSTORE on cold page transitions curr→new; subsequent calls run
    on a warm page where SSTORE(slot, new) becomes a noop. Covers
    both curr==new (noop on the first call) and curr!=new (write).
    """
    slot = 1
    page = TxPageState(slots={slot: curr} if curr != 0 else {})
    cold_gas = simulate_sstore(page, slot, new, fork)
    # After cold call, slot holds `new`; the second SSTORE(slot, new)
    # is a warm noop.
    warm_gas = simulate_sstore(page, slot, new, fork)
    gas_test(
        fork=fork,
        state_test=state_test,
        pre=pre,
        setup_code=Op.PUSH2(new) + Op.PUSH2(slot),
        subject_code=Op.SSTORE,
        tear_down_code=Op.STOP,
        cold_gas=cold_gas,
        warm_gas=warm_gas,
        subject_storage={slot: curr} if curr != 0 else None,
        # SSTORE's 2300-gas stipend fires before the gas charge for
        # any sub-stipend warm cost (e.g. the noop BASE = 100 gas),
        # which would also OOG the sanity run.
        out_of_gas_testing=False,
    )


_PAGE_BRANCH_SLOTS = [0, 1, 2, 16, 32, 64, 96, 127]


@pytest.mark.parametrize("warming_mode", ["sstore", "sload", "acl"])
@pytest.mark.parametrize("new_equals_current", [True, False])
@pytest.mark.parametrize("warmed_page", [1, 2**7 - 2])
@pytest.mark.parametrize("warmed_offset", _PAGE_BRANCH_SLOTS)
@pytest.mark.parametrize("target_offset", _PAGE_BRANCH_SLOTS)
@pytest.mark.parametrize(
    "target_page_diff",
    [0, 1, -1],
    ids=["same_page", "next_page", "prev_page"],
)
def test_sstore_cross_page_warming(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    warmed_page: int,
    warmed_offset: int,
    target_offset: int,
    target_page_diff: int,
    new_equals_current: bool,
    warming_mode: str,
) -> None:
    """
    Cold/warm SSTORE across page boundaries.
    """
    target_page = warmed_page + target_page_diff

    warmed_slot = warmed_page * Spec.SLOTS_PER_PAGE + warmed_offset
    target_slot = target_page * Spec.SLOTS_PER_PAGE + target_offset

    same_page = page_index(warmed_slot) == page_index(target_slot)
    same_slot = target_slot == warmed_slot

    write_setup = warming_mode == "sstore"

    warm_value = 2
    # Only the SSTORE setup actually populates warmed_slot.
    current_value = warm_value if (same_slot and write_setup) else 0
    new_value = current_value if new_equals_current else 1

    page = TxPageState()
    if same_page:
        if write_setup:
            simulate_sstore(page, warmed_slot, warm_value, fork)
        else:
            # SLOAD or ACL warm the read set only.
            page.read_warm = True
    expected_gas = simulate_sstore(page, target_slot, new_value, fork)

    # Measurement slot far from the matrix to avoid collision
    # with target_slot (which ranges up to page ~127).
    gas_slot = 200 * Spec.SLOTS_PER_PAGE

    overhead = (Op.PUSH1(0) + Op.PUSH2(0)).gas_cost(fork)
    measure = CodeGasMeasure(
        code=Op.SSTORE(target_slot, new_value),
        overhead_cost=overhead,
        extra_stack_items=0,
        sstore_key=gas_slot,
    )

    if warming_mode == "sstore":
        contract_address = pre.deploy_contract(
            Op.SSTORE(warmed_slot, warm_value) + measure
        )
        tx = Transaction(
            gas_limit=generous_gas(fork),
            to=contract_address,
            sender=pre.fund_eoa(),
        )
    elif warming_mode == "sload":
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

    expected_storage = {gas_slot: expected_gas}
    if same_slot:
        expected_storage[warmed_slot] = new_value
    else:
        if write_setup:
            expected_storage[warmed_slot] = warm_value
        expected_storage[target_slot] = new_value

    state_test(
        pre=pre,
        post={contract_address: Account(storage=expected_storage)},
        tx=tx,
    )


@pytest.mark.parametrize(
    "curr,new",
    [
        pytest.param(42, 42, id="same"),
        pytest.param(42, 99, id="different"),
    ],
)
@pytest.mark.parametrize(
    "across", ["same_tx", "diff_tx_same_block", "diff_block"]
)
def test_sstore_same_value_no_page_write(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
    across: str,
    curr: int,
    new: int,
) -> None:
    """
    Pre-populated slot 0 holds `curr`. A setup SSTORE on slot 1
    warms page 0; the measured SSTORE on slot 0 writes `new` —
    same value (noop, BASE only) or different (cold I/O on first
    measure-tx touch).

    `across` controls whether setup runs in the same tx, a prior
    tx in the same block, or a prior block.
    """
    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)
    measure_code = CodeGasMeasure(
        code=Op.SSTORE(0, new),
        overhead_cost=overhead,
        extra_stack_items=0,
        sstore_key=slot_gas_measured,
    )

    if across == "same_tx":
        contract_address = pre.deploy_contract(
            Op.SSTORE(1, 1) + measure_code,
            storage={0: curr},
        )
    else:
        contract_address = pre.deploy_contract(
            Conditional(
                condition=Op.CALLDATASIZE,
                if_true=measure_code,
                if_false=Op.SSTORE(1, 1),
            ),
            storage={0: curr},
        )

    page = TxPageState(slots={0: curr})
    if across == "same_tx":
        simulate_sstore(page, 1, 1, fork)
    expected_gas = simulate_sstore(page, 0, new, fork)

    tx_setup = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )
    tx_measure = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
        data=b"\x01",
    )

    if across == "same_tx":
        blocks = [Block(txs=[tx_measure])]
    elif across == "diff_tx_same_block":
        blocks = [Block(txs=[tx_setup, tx_measure])]
    else:
        blocks = [Block(txs=[tx_setup]), Block(txs=[tx_measure])]

    expected_storage = {
        0: new,
        1: 1,
        slot_gas_measured: expected_gas,
    }

    blockchain_test(
        pre=pre,
        blocks=blocks,
        post={contract_address: Account(storage=expected_storage)},
    )


@pytest.mark.parametrize("at_limit", [True, False])
def test_max_cold_sstore_pages_in_tx(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    at_limit: bool,
) -> None:
    """
    Maximum cold SSTORE pages fitting in tx gas limit.
    """
    tx_gas_cap = fork.transaction_gas_limit_cap()
    assert tx_gas_cap is not None
    intrinsic = fork.transaction_intrinsic_cost_calculator()(
        calldata=b"", contract_creation=False
    )
    fresh_sstore = Op.SSTORE(
        page_load_warm=False,
        page_write_warm=False,
        current_value=0,
        new_value=1,
        current_state_growth=0,
        net_state_growth=0,
    ).gas_cost(fork)
    per_iter_gas = (Op.PUSH1(0) + Op.PUSH3(0)).gas_cost(fork) + fresh_sstore
    available = tx_gas_cap - intrinsic

    max_n = available // per_iter_gas

    # sanity check we're testing anything at all
    assert max_n > 10
    n = max_n if at_limit else max_n + 1

    code = Bytecode()
    for i in range(n):
        code += Op.SSTORE(i * Spec.SLOTS_PER_PAGE, 1)

    contract_address = pre.deploy_contract(code)
    tx = Transaction(
        gas_limit=tx_gas_cap,
        to=contract_address,
        sender=pre.fund_eoa(),
    )
    if at_limit:
        post_storage = {i * Spec.SLOTS_PER_PAGE: 1 for i in range(n)}
    else:
        post_storage = {}
    state_test(
        pre=pre,
        post={contract_address: Account(storage=post_storage)},
        tx=tx,
    )


@pytest.mark.parametrize("at_limit", [True, False])
def test_max_consecutive_sstore_slots_in_tx(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    at_limit: bool,
) -> None:
    """
    Maximum consecutive SSTORE slots fitting in tx gas limit.

    Counted loop writes slot=counter for counter=N..1.
    First slot per page pays full I/O; subsequent on same page
    pay only BASE+STATE_GROWTH.
    """
    tx_gas_cap = fork.transaction_gas_limit_cap()
    assert tx_gas_cap is not None
    intrinsic = fork.transaction_intrinsic_cost_calculator()(
        calldata=b"", contract_creation=False
    )
    prefix = Op.PUSH3(0)  # placeholder; rebuilt below with real n_slots
    loop_dest = len(prefix)

    def _loop_body(sstore: Opcode) -> Bytecode:
        return (
            Op.JUMPDEST
            + Op.DUP1
            + Op.DUP1
            + sstore
            + Op.PUSH1(1)
            + Op.SWAP1
            + Op.SUB
            + Op.DUP1
            + Op.PUSH3(loop_dest)
            + Op.JUMPI
        )

    fresh_cold = _loop_body(
        cast(
            Opcode,
            Op.SSTORE(
                page_load_warm=False,
                page_write_warm=False,
                current_value=0,
                new_value=1,
                current_state_growth=0,
                net_state_growth=0,
            ),
        )
    ).gas_cost(fork)
    fresh_warm = _loop_body(
        cast(
            Opcode,
            Op.SSTORE(
                page_load_warm=True,
                page_write_warm=True,
                current_value=0,
                new_value=1,
                current_state_growth=1,
                net_state_growth=1,
            ),
        )
    ).gas_cost(fork)

    setup_and_cleanup = (Op.PUSH3(0) + Op.POP).gas_cost(fork)
    available = tx_gas_cap - intrinsic - setup_and_cleanup

    max_n = 0
    used = 0
    seen_pages: set[int] = set()
    while True:
        slot = max_n
        page = slot // Spec.SLOTS_PER_PAGE
        iter_cost = fresh_cold if page not in seen_pages else fresh_warm
        if used + iter_cost > available:
            break
        used += iter_cost
        seen_pages.add(page)
        max_n += 1

    # sanity check we're testing anything at all
    assert max_n > 10

    n_slots = max_n if at_limit else max_n + 1
    code = Op.PUSH3(n_slots) + _loop_body(Op.SSTORE) + Op.POP

    contract_address = pre.deploy_contract(code)
    tx = Transaction(
        gas_limit=tx_gas_cap,
        to=contract_address,
        sender=pre.fund_eoa(),
    )
    # Loop wrote slot=counter for counter=N..1: slots 1..N each set
    # to themselves.
    if at_limit:
        post_storage = {i: i for i in range(1, max_n + 1)}
    else:
        post_storage = {}
    state_test(
        pre=pre,
        post={contract_address: Account(storage=post_storage)},
        tx=tx,
    )


@pytest.mark.parametrize("write_pattern", ["same_slot", "same_page"])
@pytest.mark.parametrize("at_limit", [True, False])
def test_max_warm_sstore_iters_in_tx(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    at_limit: bool,
    write_pattern: str,
) -> None:
    """
    Max SSTORE iterations fitting in tx gas, all on the same
    pre-populated page so each SSTORE is a noop (curr==new).

    `same_slot`: SSTORE(0, 1) every iter.
    `same_page`: SSTORE(counter & 0x7F, 1) every iter — slot
    rotates within page 0.

    Pre-populated storage makes every iter a same-value SSTORE:
    BASE_COST only, no page I/O, no state growth.
    """
    tx_gas_cap = fork.transaction_gas_limit_cap()
    assert tx_gas_cap is not None
    intrinsic = fork.transaction_intrinsic_cost_calculator()(
        calldata=b"", contract_creation=False
    )

    prefix = Op.PUSH3(0)  # placeholder
    loop_dest = len(prefix)

    def _loop_body(sstore: Opcode) -> Bytecode:
        if write_pattern == "same_slot":
            sstore_seq = sstore(0, 1)
        else:
            sstore_seq = sstore(Op.AND(Op.DUP1, Spec.SLOTS_PER_PAGE - 1), 1)
        return (
            Op.JUMPDEST
            + sstore_seq
            + Op.PUSH1(1)
            + Op.SWAP1
            + Op.SUB
            + Op.DUP1
            + Op.PUSH3(loop_dest)
            + Op.JUMPI
        )

    cold_noop_sstore = cast(
        Opcode,
        Op.SSTORE(
            page_load_warm=False,
            page_write_warm=False,
            current_value=1,
            new_value=1,
            current_state_growth=0,
            net_state_growth=0,
        ),
    )
    warm_noop_sstore = cast(
        Opcode,
        Op.SSTORE(
            page_load_warm=True,
            page_write_warm=False,
            current_value=1,
            new_value=1,
            current_state_growth=0,
            net_state_growth=0,
        ),
    )
    cold_iter = _loop_body(cold_noop_sstore).gas_cost(fork)
    warm_iter = _loop_body(warm_noop_sstore).gas_cost(fork)

    setup_overhead = Op.PUSH3(0).gas_cost(fork)
    marker_slot = 100 * Spec.SLOTS_PER_PAGE
    marker_sstore = Op.SSTORE(
        page_load_warm=False,
        page_write_warm=False,
        current_value=0,
        new_value=value_code_worked,
    ).gas_cost(fork)
    marker_cost = (Op.PUSH2(0) + Op.PUSH3(0) + Op.POP).gas_cost(
        fork
    ) + marker_sstore
    available = tx_gas_cap - intrinsic - setup_overhead - marker_cost

    assert available >= cold_iter
    max_n = 1 + (available - cold_iter) // warm_iter

    # sanity check we're testing anything at all
    assert max_n > 10

    n_slots = max_n if at_limit else max_n + 1

    # Pre-populate so every SSTORE is a noop (curr=1, new=1).
    prepop: dict[NumberConvertible, NumberConvertible]
    if write_pattern == "same_slot":
        prepop = {0: 1}
    else:
        prepop = dict.fromkeys(range(Spec.SLOTS_PER_PAGE), 1)

    code = (
        Op.PUSH3(n_slots)
        + _loop_body(Op.SSTORE)
        + Op.POP
        + Op.SSTORE(marker_slot, value_code_worked)
    )

    contract_address = pre.deploy_contract(code, storage=prepop)
    tx = Transaction(
        gas_limit=tx_gas_cap,
        to=contract_address,
        sender=pre.fund_eoa(),
    )
    if at_limit:
        post_storage = dict(prepop)
        post_storage[marker_slot] = value_code_worked
    else:
        post_storage = prepop  # tx OOG, no state changes commit
    state_test(
        pre=pre,
        post={contract_address: Account(storage=post_storage)},
        tx=tx,
    )


@pytest.mark.parametrize("measure_slot", [0, Spec.SLOTS_PER_PAGE])
def test_sstore_no_growth_below_peak(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    measure_slot: int,
) -> None:
    """
    STATE_GROWTH_COST charged only when current exceeds peak.
    """
    setup_page = 0
    setup = Op.SSTORE(0, 1) + Op.SSTORE(1, 1) + Op.SSTORE(0, 0)
    on_setup_page = measure_slot // Spec.SLOTS_PER_PAGE == setup_page

    page = TxPageState()
    if on_setup_page:
        simulate_sstore(page, 0, 1, fork)
        simulate_sstore(page, 1, 1, fork)
        simulate_sstore(page, 0, 0, fork)

    overhead = (Op.PUSH2(0) + Op.PUSH1(0)).gas_cost(fork)
    contract_address = pre.deploy_contract(
        setup
        + CodeGasMeasure(
            code=Op.SSTORE(measure_slot, 1),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=slot_gas_measured,
        )
    )
    expected_gas = simulate_sstore(page, measure_slot, 1, fork)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    slot_storage = {1: 1, measure_slot: 1}
    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={**slot_storage, slot_gas_measured: expected_gas},
            ),
        },
        tx=tx,
    )


def test_sstore_peak_holds_after_full_page_clear(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Fill page (128 slots), clear all, refill last slot:
    no STATE_GROWTH charge on refill (peak already 128).
    """
    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)

    fill_code = Bytecode()
    for i in range(Spec.SLOTS_PER_PAGE):
        fill_code += Op.SSTORE(i, 1)

    clear_code = Bytecode()
    for i in range(Spec.SLOTS_PER_PAGE):
        clear_code += Op.SSTORE(i, 0)

    refill_first = Bytecode()
    for i in range(Spec.SLOTS_PER_PAGE - 1):
        refill_first += Op.SSTORE(i, 1)

    page = TxPageState()
    for i in range(Spec.SLOTS_PER_PAGE):
        simulate_sstore(page, i, 1, fork)
    for i in range(Spec.SLOTS_PER_PAGE):
        simulate_sstore(page, i, 0, fork)
    for i in range(Spec.SLOTS_PER_PAGE - 1):
        simulate_sstore(page, i, 1, fork)

    contract_address = pre.deploy_contract(
        fill_code
        + clear_code
        + refill_first
        + CodeGasMeasure(
            code=Op.SSTORE(Spec.SLOTS_PER_PAGE - 1, 1),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=slot_gas_measured,
        )
    )
    expected_gas = simulate_sstore(page, Spec.SLOTS_PER_PAGE - 1, 1, fork)

    # Fill + clear + refill = 3 full-page sweeps over the same page.
    tx = Transaction(
        gas_limit=generous_gas(fork) + 3 * full_page_sweep_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    expected_storage = dict.fromkeys(range(Spec.SLOTS_PER_PAGE), 1)
    expected_storage[slot_gas_measured] = expected_gas

    state_test(
        pre=pre,
        post={contract_address: Account(storage=expected_storage)},
        tx=tx,
    )


@pytest.mark.parametrize(
    "refill_slot",
    [
        pytest.param(0, id="same_slot"),
        pytest.param(1, id="different_slot_same_page"),
    ],
)
def test_sstore_no_growth_after_clear(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    refill_slot: int,
) -> None:
    """
    Cleared slot replacement in the same page bypasses
    STATE_GROWTH (per MIP-8 backwards-compat clause).

    Pre-state: slot 0 holds value 1, occupying one slot in page 0.
    Setup SSTORE(0, 0) clears slot 0 in the same tx: counters
    initialize to (0, 0) on first write, then current decrements
    to -1, peak stays 0.

    Measured SSTORE(refill_slot, 1) refills either the same slot
    or a sibling slot on the same page. Counter goes to 0; since
    0 is not strictly greater than peak=0, no STATE_GROWTH is
    charged. The page remains write-warm so only BASE is paid.
    """
    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)
    contract_address = pre.deploy_contract(
        Op.SSTORE(0, 0)
        + CodeGasMeasure(
            code=Op.SSTORE(refill_slot, 1),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=slot_gas_measured,
        ),
        storage={0: 1},
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    page = TxPageState(slots={0: 1})
    simulate_sstore(page, 0, 0, fork)  # setup clear
    expected_gas = simulate_sstore(page, refill_slot, 1, fork)

    expected_storage = {refill_slot: 1, slot_gas_measured: expected_gas}
    state_test(
        pre=pre,
        post={contract_address: Account(storage=expected_storage)},
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
        pytest.param(2**256 - 1, 2**256 - 1, id="last_then_last"),
    ],
)
def test_sstore_max_slot_page_boundary(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    warm_slot: int,
    measured_slot: int,
) -> None:
    """
    Verify page arithmetic at the slot-key field boundary.
    """
    same_slot = warm_slot == measured_slot
    page = TxPageState()
    simulate_sstore(page, warm_slot, 1, fork)
    expected_gas = simulate_sstore(page, measured_slot, 2, fork)

    overhead = (Op.PUSH1(0) + Op.PUSH32(0)).gas_cost(fork)
    contract_address = pre.deploy_contract(
        Op.SSTORE(warm_slot, 1)
        + CodeGasMeasure(
            code=Op.SSTORE(measured_slot, 2),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=slot_gas_measured,
        )
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    expected_storage = {
        warm_slot: 2 if same_slot else 1,
        measured_slot: 2,
        slot_gas_measured: expected_gas,
    }

    state_test(
        pre=pre,
        post={contract_address: Account(storage=expected_storage)},
        tx=tx,
    )
