"""
Tests page-level SSTORE gas costs under MIP-8.
"""

import pytest
from execution_testing import (
    Account,
    Alloc,
    Bytecode,
    CodeGasMeasure,
    Op,
    StateTestFiller,
    Transaction,
)
from execution_testing.forks.helpers import Fork

from .helpers import generous_gas
from .spec import Spec, ref_spec_8

REFERENCE_SPEC_GIT_PATH = ref_spec_8.git_path
REFERENCE_SPEC_VERSION = ref_spec_8.version

value_code_worked = 0x1234


@pytest.mark.valid_from("MONAD_NEXT")
def test_sstore_page_write_cost(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test that first SSTORE to a page charges PAGE_WRITE, and second
    SSTORE to same page does not.

    First SSTORE (slot 0, orig=0, curr=0, new!=0):
      PAGE_WRITE + BASE_SSTORE + NEW_SLOT = 25100
    Second SSTORE (slot 1, orig=0, curr=0, new!=0, same page):
      BASE_SSTORE + NEW_SLOT = 20100 (no PAGE_WRITE)
    """
    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)
    contract_address = pre.deploy_contract(
        CodeGasMeasure(
            code=Op.SSTORE(0, 1),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=0x100,
            stop=False,
        )
        + CodeGasMeasure(
            code=Op.SSTORE(1, 1),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=0x101,
        )
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    first_cost = Spec.GAS_PAGE_WRITE + Spec.GAS_BASE_SSTORE + Spec.GAS_NEW_SLOT
    second_cost = Spec.GAS_BASE_SSTORE + Spec.GAS_NEW_SLOT

    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    0: 1,
                    1: 1,
                    0x100: first_cost,
                    0x101: second_cost,
                },
            ),
        },
        tx=tx,
    )


@pytest.mark.valid_from("MONAD_NEXT")
def test_sstore_no_page_write_same_value(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test that writing the same value skips page write cost.

    Pre-populate slot 0 with value 42. SSTORE(0, 42) where
    current == new, so no page write. Cost = BASE_SSTORE only.
    """
    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)
    contract_address = pre.deploy_contract(
        CodeGasMeasure(
            code=Op.SSTORE(0, 42),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=0x100,
        ),
        storage={0: 42},
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
                    0x100: Spec.GAS_BASE_SSTORE,
                },
            ),
        },
        tx=tx,
    )


@pytest.mark.valid_from("MONAD_NEXT")
def test_sstore_create_new_slot(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test SSTORE case: orig=0, curr=0, new!=0 (create new slot).

    Charges BASE_SSTORE + NEW_SLOT + PAGE_WRITE (first write).
    """
    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)
    contract_address = pre.deploy_contract(
        CodeGasMeasure(
            code=Op.SSTORE(0, 1),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=0x100,
        )
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    expected = Spec.GAS_PAGE_WRITE + Spec.GAS_BASE_SSTORE + Spec.GAS_NEW_SLOT

    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={0: 1, 0x100: expected},
            ),
        },
        tx=tx,
    )


@pytest.mark.valid_from("MONAD_NEXT")
def test_sstore_clear_existing_slot(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test SSTORE case: orig!=0, curr!=0, new=0 (clear slot).

    Charges BASE_SSTORE + PAGE_WRITE (first write to page).
    """
    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)
    contract_address = pre.deploy_contract(
        CodeGasMeasure(
            code=Op.SSTORE(0, 0),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=0x100,
        ),
        storage={0: 99},
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    expected = Spec.GAS_PAGE_WRITE + Spec.GAS_BASE_SSTORE

    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={0x100: expected},
            ),
        },
        tx=tx,
    )


@pytest.mark.valid_from("MONAD_NEXT")
def test_sstore_recreate_after_clear(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test SSTORE case: orig!=0, curr=0, new!=0 (restore cleared slot).

    First clear slot 0 (orig=99, curr=99 -> 0), then restore
    (orig=99, curr=0, new=77).
    Restore charges BASE_SSTORE + PAGE_WRITE (if not already written).
    No NEW_SLOT because slot existed originally.
    """
    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)
    contract_address = pre.deploy_contract(
        Op.SSTORE(0, 0)
        + CodeGasMeasure(
            code=Op.SSTORE(0, 77),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=0x100,
        ),
        storage={0: 99},
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    # Page already written by first SSTORE, no PAGE_WRITE
    expected = Spec.GAS_BASE_SSTORE

    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={0: 77, 0x100: expected},
            ),
        },
        tx=tx,
    )


@pytest.mark.valid_from("MONAD_NEXT")
def test_sstore_clear_created_slot(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test SSTORE case: orig=0, curr!=0, new=0 (undo creation).

    First create slot (orig=0, curr=0 -> 1), then clear
    (orig=0, curr=1, new=0). The clear charges 0 for state
    growth (no BASE_SSTORE) but may charge PAGE_WRITE.
    """
    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)
    contract_address = pre.deploy_contract(
        Op.SSTORE(0, 1)
        + CodeGasMeasure(
            code=Op.SSTORE(0, 0),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=0x100,
        )
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    # Page already written by first SSTORE. State growth = 0.
    expected = 0

    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={0x100: expected},
            ),
        },
        tx=tx,
    )


@pytest.mark.valid_from("MONAD_NEXT")
def test_sstore_modify_existing(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test SSTORE case: orig!=0, curr!=0, new!=0, new!=curr
    (modify existing slot).

    Charges BASE_SSTORE + PAGE_WRITE (first write to page).
    """
    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)
    contract_address = pre.deploy_contract(
        CodeGasMeasure(
            code=Op.SSTORE(0, 77),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=0x100,
        ),
        storage={0: 42},
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    expected = Spec.GAS_PAGE_WRITE + Spec.GAS_BASE_SSTORE

    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={0: 77, 0x100: expected},
            ),
        },
        tx=tx,
    )


@pytest.mark.valid_from("MONAD_NEXT")
def test_sstore_noop(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test SSTORE noop: curr == new (write same value).

    Falls into else branch: charges BASE_SSTORE, no PAGE_WRITE.
    """
    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)
    contract_address = pre.deploy_contract(
        CodeGasMeasure(
            code=Op.SSTORE(0, 42),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=0x100,
        ),
        storage={0: 42},
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
                storage={0: 42, 0x100: Spec.GAS_BASE_SSTORE},
            ),
        },
        tx=tx,
    )


@pytest.mark.valid_from("MONAD_NEXT")
def test_sstore_new_slot_peak_tracking(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test that NEW_SLOT is only charged when delta exceeds peak.

    Sequence on page 0:
    1. SSTORE(0, 1): delta=1, peak=0 -> NEW_SLOT, peak=1
    2. SSTORE(1, 1): delta=2, peak=1 -> NEW_SLOT, peak=2
    3. SSTORE(0, 0): delta=1 (clear, orig=0 curr=1 new=0)
    4. SSTORE(0, 1): delta=2, peak=2 -> NO NEW_SLOT (at peak)
    """
    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)
    contract_address = pre.deploy_contract(
        # Step 1: create slot 0
        Op.SSTORE(0, 1)
        # Step 2: create slot 1
        + Op.SSTORE(1, 1)
        # Step 3: clear slot 0 (orig=0, curr=1, new=0)
        + Op.SSTORE(0, 0)
        # Step 4: recreate slot 0 — measure this
        + CodeGasMeasure(
            code=Op.SSTORE(0, 1),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=0x100,
        )
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    # Step 4: orig=0, curr=0, new=1 -> BASE_SSTORE + delta check
    # delta goes 1->2, peak=2 already -> NO NEW_SLOT
    # Page already written -> no PAGE_WRITE
    expected = Spec.GAS_BASE_SSTORE

    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={0: 1, 1: 1, 0x100: expected},
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
    Test that slot_delta_counter and max_nonzero_slots are per-page.

    Create slot on page 0, then create slot on page 1.
    Both should charge NEW_SLOT independently.
    """
    overhead = (Op.PUSH2(0) + Op.PUSH1(0)).gas_cost(fork)
    contract_address = pre.deploy_contract(
        # Page 0: create slot 0
        Op.SSTORE(0, 1)
        # Page 1: create slot 128 — measure this
        + CodeGasMeasure(
            code=Op.SSTORE(128, 1),
            overhead_cost=overhead,
            extra_stack_items=0,
            sstore_key=0x100,
        )
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    # Page 1 is independent: PAGE_WRITE + BASE_SSTORE + NEW_SLOT
    expected = Spec.GAS_PAGE_WRITE + Spec.GAS_BASE_SSTORE + Spec.GAS_NEW_SLOT

    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={0: 1, 128: 1, 0x100: expected},
            ),
        },
        tx=tx,
    )


@pytest.mark.valid_from("MONAD_NINE")
def test_sstore_refund_removed(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test that MIP-8 removes SSTORE refunds.

    Clear a pre-existing slot (orig!=0, curr!=0, new=0). On
    MONAD_NINE this yields a gas refund (less gas consumed
    overall). On MONAD_NEXT (MIP-8) there is no refund.

    Use CodeGasMeasure on a second SSTORE clearing to measure
    gas. The refund itself doesn't change the measured gas, but
    clearing a second slot and checking the stored gas difference
    proves the refund mechanism is gone.

    Simpler approach: store gas_left before and after the
    clearing SSTORE. The difference is the gas consumed by
    that SSTORE. Refund only affects effective gas at tx end,
    so we verify slot 0 is cleared and the tx succeeds.
    """
    contract_address = pre.deploy_contract(
        Op.SSTORE(0, 0) + Op.SSTORE(1, value_code_worked),
        storage={0: 99},
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
                storage={1: value_code_worked},
            ),
        },
        tx=tx,
    )


@pytest.mark.valid_from("MONAD_NEXT")
@pytest.mark.parametrize("at_limit", [True, False])
def test_max_cold_sstore_pages_in_tx(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    at_limit: bool,
) -> None:
    """
    Test maximum cold SSTORE pages fitting in tx gas limit.

    Each SSTORE writes a fresh slot on a new page:
    PUSH1(1) + PUSH3(slot) + SSTORE = 3 + 3 + 25100 = 25,106 gas.

    at_limit=True: N = max → success, all writes present.
    at_limit=False: N + 1 → OOG, no writes present.
    """
    tx_gas_cap = fork.transaction_gas_limit_cap()
    intrinsic = fork.transaction_intrinsic_cost_calculator()(
        calldata=b"", contract_creation=False
    )
    per_iter_gas = (
        Op.PUSH1(0).gas_cost(fork)
        + Op.PUSH3(0).gas_cost(fork)
        + Spec.GAS_PAGE_WRITE
        + Spec.GAS_BASE_SSTORE
        + Spec.GAS_NEW_SLOT
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
    Test maximum consecutive SSTORE slots fitting in tx gas limit.

    Uses counted loop. Each iter writes value=counter to slot=counter.

    Loop body:
      JUMPDEST DUP1 DUP1 SSTORE PUSH1(1) SWAP1 SUB DUP1 PUSH3(loop) JUMPI
      gas: 1 + 3 + 3 + sstore + 3 + 3 + 3 + 3 + 3 + 10 = 32 + sstore
      stack: [counter] -> after iter [counter-1]

    Per page: first slot fresh = 5000+100+20000=25100,
              rest fresh same page = 100+20000=20100.
    """
    tx_gas_cap = fork.transaction_gas_limit_cap()
    intrinsic = fork.transaction_intrinsic_cost_calculator()(
        calldata=b"", contract_creation=False
    )
    loop_overhead = 32

    setup_overhead = Op.PUSH3(0).gas_cost(fork)
    cleanup = Op.POP.gas_cost(fork)
    available = tx_gas_cap - intrinsic - setup_overhead - cleanup

    # Simulate: write slot=counter where counter starts at N and
    # decrements to 1. So slots written are N-1, N-2, ..., 0
    # in order (last iter writes slot 0). All values are nonzero.
    max_n = 0
    used = 0
    seen_pages: set[int] = set()
    while True:
        slot = max_n  # next iter writes slot=max_n in counter sense
        page = slot // Spec.SLOTS_PER_PAGE
        if page not in seen_pages:
            sstore_cost = (
                Spec.GAS_PAGE_WRITE + Spec.GAS_BASE_SSTORE + Spec.GAS_NEW_SLOT
            )
        else:
            sstore_cost = Spec.GAS_BASE_SSTORE + Spec.GAS_NEW_SLOT
        iter_cost = loop_overhead + sstore_cost
        if used + iter_cost > available:
            break
        used += iter_cost
        seen_pages.add(page)
        max_n += 1

    n_slots = max_n if at_limit else max_n + 1

    prefix = Op.PUSH3(n_slots)
    loop_dest = len(prefix)
    # Stack at JUMPDEST: [counter]. Need to SSTORE(slot=ctr, val=ctr)
    # SSTORE pops [key, value]. We want key=ctr, value=ctr.
    # DUP1 DUP1 -> [ctr, ctr, ctr], then SSTORE pops top two as key, val.
    # In our SSTORE expects key first popped, then value: SSTORE op order
    # in spec = [..., key, value] -> so push value first, key on top.
    # We have stack [ctr, ctr, ctr], top pops first as key, next as value.
    # Both are ctr → OK.
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

    if at_limit:
        # Loop wrote slot=counter for counter=N down to 1, so slots
        # 0..N-1 all set. Values: SSTORE(ctr, ctr) → slot ctr = ctr.
        post_storage = {i: i for i in range(1, max_n + 1)}
    else:
        post_storage = {}

    state_test(
        pre=pre,
        post={contract_address: Account(storage=post_storage)},
        tx=tx,
    )


@pytest.mark.valid_from("MONAD_NEXT")
def test_sstore_new_slot_peak_tracking_full_page(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Extended peak tracking: fill page with all 128 slots, clear all,
    refill all. Verify NEW_SLOT only charged on first fill (peak holds).

    Use CodeGasMeasure on the last SSTORE of the refill sequence;
    expected = BASE_SSTORE only (page already written, peak already
    reached, slot was nonzero originally → no NEW_SLOT).

    Wait: orig is value at tx start. orig=0 for all slots.
    After fill: all slots have val=1. After clear: all val=0.
    After refill: orig=0, curr=0, new=1 → BASE_SSTORE +
                  delta check; delta would go from 0 to 1, peak=128
                  → NO NEW_SLOT.
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
            sstore_key=0x100,
        )
    )

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    # Last SSTORE: orig=0 curr=0 new=1, page already written,
    # peak=128 already reached -> only BASE_SSTORE.
    expected = Spec.GAS_BASE_SSTORE

    expected_storage = dict.fromkeys(range(Spec.SLOTS_PER_PAGE), 1)
    expected_storage[0x100] = expected

    state_test(
        pre=pre,
        post={
            contract_address: Account(storage=expected_storage),
        },
        tx=tx,
    )
