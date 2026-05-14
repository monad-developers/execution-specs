"""
Tests page-level SSTORE gas costs under MIP-8.
"""

from typing import Tuple

import pytest
from execution_testing import (
    Account,
    Alloc,
    Block,
    BlockchainTestFiller,
    Bytecode,
    CodeGasMeasure,
    Op,
    StateTestFiller,
    Transaction,
)
from execution_testing.forks import MONAD_NEXT
from execution_testing.forks.helpers import Fork

from .helpers import generous_gas
from .spec import Spec, ref_spec_8

REFERENCE_SPEC_GIT_PATH = ref_spec_8.git_path
REFERENCE_SPEC_VERSION = ref_spec_8.version

slot_gas_measured = 0x100
slot_gas_measured_2 = 0x101
value_code_worked = 0x1234


# --- State transition tests ------------------------------------------------

# Tuples (v_original, v_current, v_new) covering all zero-ness and
# same-ness combinations of the slot value at tx start (v_original),
# right before the measured SSTORE (v_current), and after (v_new).
# Letters X, Y, Z represent distinct nonzero values.
_STATE_TRANSITIONS = [
    pytest.param(0, 0, 0, id="0_0_0"),
    pytest.param(0, 0, 1, id="0_0_X"),
    pytest.param(0, 1, 0, id="0_X_0"),
    pytest.param(0, 1, 1, id="0_X_X"),
    pytest.param(0, 1, 2, id="0_X_Y"),
    pytest.param(5, 0, 0, id="X_0_0"),
    pytest.param(5, 0, 5, id="X_0_X"),
    pytest.param(5, 0, 6, id="X_0_Y"),
    pytest.param(5, 5, 0, id="X_X_0"),
    pytest.param(5, 5, 5, id="X_X_X"),
    pytest.param(5, 5, 6, id="X_X_Y"),
    pytest.param(5, 6, 0, id="X_Y_0"),
    pytest.param(5, 6, 5, id="X_Y_X"),
    pytest.param(5, 6, 6, id="X_Y_Y"),
    pytest.param(5, 6, 7, id="X_Y_Z"),
]


def _setup_current(slot: int, orig: int, curr: int) -> Bytecode:
    """Bytecode that drives the slot to `curr` from initial `orig`."""
    if orig == curr:
        return Bytecode()
    return Op.SSTORE(slot, curr)


def _expected_setup_growth(orig: int, curr: int) -> Tuple[int, int]:
    """Return (current_state_growth, net_state_growth) after setup."""
    if orig == 0 and curr != 0:
        return (1, 1)
    if orig != 0 and curr == 0:
        return (-1, 0)
    return (0, 0)


@pytest.mark.valid_from("MONAD_NEXT")
@pytest.mark.parametrize("orig,curr,new", _STATE_TRANSITIONS)
def test_sstore_state_transitions(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    orig: int,
    curr: int,
    new: int,
) -> None:
    """
    Measured SSTORE on a slot in state (orig, curr); compares
    consumed gas against `Op.SSTORE().gas_cost(fork)` using
    metadata derived from page warming and per-page counters
    after the setup phase.
    """
    slot = 0
    setup = _setup_current(slot, orig, curr)
    page_load_warm = bool(setup)  # any SSTORE in setup loaded page
    page_write_warm = bool(setup) and orig != curr
    growth, peak = _expected_setup_growth(orig, curr)

    expected = Op.SSTORE(
        page_load_warm=page_load_warm,
        page_write_warm=page_write_warm,
        current_value=curr,
        new_value=new,
        current_state_growth=growth,
        net_state_growth=peak,
    ).gas_cost(fork)

    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)
    contract_address = pre.deploy_contract(
        setup
        + CodeGasMeasure(
            code=Op.SSTORE(slot, new),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=slot_gas_measured,
        ),
        storage={slot: orig} if orig != 0 else {},
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    expected_storage = {slot_gas_measured: expected}
    if new != 0:
        expected_storage[slot] = new

    state_test(
        pre=pre,
        post={contract_address: Account(storage=expected_storage)},
        tx=tx,
    )


# --- Page warming via SLOAD only -------------------------------------------


@pytest.mark.valid_from("MONAD_NEXT")
def test_sstore_after_sload_pays_write_cost(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    SLOAD warms a page (page_load_warm=True) but does not write
    (page_write_warm=False). A subsequent SSTORE on that page
    pays WRITE_COST but skips LOAD_COST.
    """
    slot = 1
    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)
    contract_address = pre.deploy_contract(
        Op.SLOAD(0)
        + Op.POP
        + CodeGasMeasure(
            code=Op.SSTORE(slot, value_code_worked),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=slot_gas_measured,
        ),
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    expected = Op.SSTORE(
        page_load_warm=True,
        page_write_warm=False,
        current_value=0,
        new_value=value_code_worked,
        current_state_growth=0,
        net_state_growth=0,
    ).gas_cost(fork)

    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={slot: value_code_worked, slot_gas_measured: expected},
            ),
        },
        tx=tx,
    )


# --- Page write cost: first vs subsequent ----------------------------------


@pytest.mark.valid_from("MONAD_NEXT")
def test_sstore_page_write_cost(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    First SSTORE on a page pays LOAD+WRITE; second SSTORE (same
    page, different slot) pays only BASE+STATE_GROWTH.
    """
    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)
    contract_address = pre.deploy_contract(
        CodeGasMeasure(
            code=Op.SSTORE(0, 1),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=slot_gas_measured,
            stop=False,
        )
        + CodeGasMeasure(
            code=Op.SSTORE(1, 1),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=slot_gas_measured_2,
        )
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    first_cost = Op.SSTORE(
        page_load_warm=False,
        page_write_warm=False,
        current_value=0,
        new_value=1,
        current_state_growth=0,
        net_state_growth=0,
    ).gas_cost(fork)
    second_cost = Op.SSTORE(
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
            contract_address: Account(
                storage={
                    0: 1,
                    1: 1,
                    slot_gas_measured: first_cost,
                    slot_gas_measured_2: second_cost,
                },
            ),
        },
        tx=tx,
    )


# --- Same value SSTORE across tx / block boundaries ------------------------


@pytest.mark.parametrize(
    "across",
    [
        pytest.param("same_tx", id="same_tx"),
        pytest.param("same_block", id="diff_tx_same_block"),
        pytest.param("diff_block", id="diff_block"),
    ],
)
@pytest.mark.valid_from("MONAD_NEXT")
def test_sstore_same_value_no_page_write(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
    across: str,
) -> None:
    """
    SSTORE writing the same value the slot already holds skips
    page write cost, regardless of tx/block boundary. Only
    BASE_COST is charged.

    `across=same_tx`: setup SSTORE (same value to pre-populated
        slot) then measure SSTORE (same value again) in single tx.
    `across=same_block`: setup tx, then measure tx, same block.
    `across=diff_block`: setup tx in block 1, measure in block 2.
    """
    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)
    measure_code = CodeGasMeasure(
        code=Op.SSTORE(0, 42),
        overhead_cost=overhead,
        extra_stack_items=0,
        sstore_key=slot_gas_measured,
    )

    if across == "same_tx":
        # Same-value SSTORE in setup leaves page sets untouched
        # (no I/O, no LOAD, no WRITE) so measure SSTORE is still
        # the first touch of the page in this tx.
        contract = pre.deploy_contract(
            Op.SSTORE(0, 42) + measure_code,
            storage={0: 42},
        )
    else:
        contract = pre.deploy_contract(measure_code, storage={0: 42})

    expected = Op.SSTORE(
        page_load_warm=False,
        page_write_warm=False,
        current_value=42,
        new_value=42,
        current_state_growth=0,
        net_state_growth=0,
    ).gas_cost(fork)

    tx_setup = Transaction(
        gas_limit=generous_gas(fork),
        to=contract,
        sender=pre.fund_eoa(),
    )
    tx_measure = Transaction(
        gas_limit=generous_gas(fork),
        to=contract,
        sender=pre.fund_eoa(),
    )

    if across == "same_tx":
        blocks = [Block(txs=[tx_measure])]
    elif across == "same_block":
        blocks = [Block(txs=[tx_setup, tx_measure])]
    else:
        blocks = [Block(txs=[tx_setup]), Block(txs=[tx_measure])]

    blockchain_test(
        pre=pre,
        blocks=blocks,
        post={
            contract: Account(
                storage={0: 42, slot_gas_measured: expected},
            ),
        },
    )


# --- Boundary tests --------------------------------------------------------


@pytest.mark.valid_from("MONAD_NEXT")
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
    per_iter_gas = (
        Op.PUSH1(0).gas_cost(fork) + Op.PUSH3(0).gas_cost(fork) + fresh_sstore
    )
    available = tx_gas_cap - intrinsic
    max_n = available // per_iter_gas
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


@pytest.mark.valid_from("MONAD_NEXT")
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
    intrinsic = fork.transaction_intrinsic_cost_calculator()(
        calldata=b"", contract_creation=False
    )
    # Loop body: JUMPDEST DUP1 DUP1 SSTORE PUSH1 SWAP1 SUB DUP1
    #            PUSH3 JUMPI. Gas excl. SSTORE: 1+3+3+3+3+3+3+3+10 = 32.
    loop_overhead = 32
    fresh_cold = Op.SSTORE(
        page_load_warm=False,
        page_write_warm=False,
        current_value=0,
        new_value=1,
        current_state_growth=0,
        net_state_growth=0,
    ).gas_cost(fork)
    fresh_warm = Op.SSTORE(
        page_load_warm=True,
        page_write_warm=True,
        current_value=0,
        new_value=1,
        current_state_growth=1,
        net_state_growth=1,
    ).gas_cost(fork)

    setup_overhead = Op.PUSH3(0).gas_cost(fork)
    cleanup = Op.POP.gas_cost(fork)
    available = tx_gas_cap - intrinsic - setup_overhead - cleanup

    max_n = 0
    used = 0
    seen_pages: set[int] = set()
    while True:
        slot = max_n
        page = slot // Spec.SLOTS_PER_PAGE
        sstore_cost = fresh_cold if page not in seen_pages else fresh_warm
        iter_cost = loop_overhead + sstore_cost
        if used + iter_cost > available:
            break
        used += iter_cost
        seen_pages.add(page)
        max_n += 1

    n_slots = max_n if at_limit else max_n + 1

    prefix = Op.PUSH3(n_slots)
    loop_dest = len(prefix)
    loop_body = (
        Op.JUMPDEST
        + Op.DUP1
        + Op.DUP1
        + Op.SSTORE
        + Op.PUSH1(1)
        + Op.SWAP1
        + Op.SUB
        + Op.DUP1
        + Op.PUSH3(loop_dest)
        + Op.JUMPI
    )
    code = prefix + loop_body + Op.POP

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


# --- Refund removal --------------------------------------------------------


@pytest.mark.valid_from("MONAD_NINE")
def test_sstore_refund_removed(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    MIP-8 removes SSTORE refunds. MONAD_NINE still has them.

    Smoke test that storage clearing succeeds across both forks;
    framework still runs but doesn't differentiate refund amount.
    """
    del fork
    contract_address = pre.deploy_contract(
        Op.SSTORE(0, 0) + Op.SSTORE(1, value_code_worked),
        storage={0: 99},
    )
    tx = Transaction(
        gas_limit=generous_gas(MONAD_NEXT),
        to=contract_address,
        sender=pre.fund_eoa(),
    )
    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={1: value_code_worked},
            ),
        },
        tx=tx,
    )


# --- State growth peak tracking --------------------------------------------


@pytest.mark.valid_from("MONAD_NEXT")
def test_sstore_state_growth_peak(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    STATE_GROWTH_COST charged only when current exceeds peak.

    Sequence on page 0:
    1. SSTORE(0, 1): delta=1, peak 0→1, charges GROWTH
    2. SSTORE(1, 1): delta=2, peak 1→2, charges GROWTH
    3. SSTORE(0, 0): delta=1 (decrement), no charge
    4. SSTORE(0, 1) measured: delta=2, peak=2 (not exceeded), no GROWTH

    The 4th SSTORE pays only BASE_COST (page already loaded+written).
    """
    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)
    contract_address = pre.deploy_contract(
        Op.SSTORE(0, 1)
        + Op.SSTORE(1, 1)
        + Op.SSTORE(0, 0)
        + CodeGasMeasure(
            code=Op.SSTORE(0, 1),
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
    expected = Op.SSTORE(
        page_load_warm=True,
        page_write_warm=True,
        current_value=0,
        new_value=1,
        current_state_growth=1,
        net_state_growth=2,
    ).gas_cost(fork)
    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={0: 1, 1: 1, slot_gas_measured: expected},
            ),
        },
        tx=tx,
    )


@pytest.mark.valid_from("MONAD_NEXT")
def test_sstore_cross_page_independent_tracking(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Per-page state growth counters are independent.
    """
    overhead = (Op.PUSH2(0) + Op.PUSH1(0)).gas_cost(fork)
    contract_address = pre.deploy_contract(
        Op.SSTORE(0, 1)
        + CodeGasMeasure(
            code=Op.SSTORE(128, 1),
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
    expected = Op.SSTORE(
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
            contract_address: Account(
                storage={0: 1, 128: 1, slot_gas_measured: expected},
            ),
        },
        tx=tx,
    )


@pytest.mark.valid_from("MONAD_NEXT")
def test_sstore_full_page_peak_tracking(
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

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    expected = Op.SSTORE(
        page_load_warm=True,
        page_write_warm=True,
        current_value=0,
        new_value=1,
        current_state_growth=Spec.SLOTS_PER_PAGE - 1,
        net_state_growth=Spec.SLOTS_PER_PAGE,
    ).gas_cost(fork)

    expected_storage = dict.fromkeys(range(Spec.SLOTS_PER_PAGE), 1)
    expected_storage[slot_gas_measured] = expected

    state_test(
        pre=pre,
        post={contract_address: Account(storage=expected_storage)},
        tx=tx,
    )
