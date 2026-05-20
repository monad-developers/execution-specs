"""
Tests MONAD_NINE -> MONAD_NEXT fork transition for MIP-8 storage.
"""

import pytest
from execution_testing import (
    AccessList,
    Account,
    Alloc,
    Block,
    BlockchainTestFiller,
    CodeGasMeasure,
    Conditional,
    Hash,
    Op,
    Transaction,
)
from execution_testing.forks import MONAD_NEXT, MONAD_NINE
from execution_testing.forks.helpers import Fork

from .helpers import STATE_TRANSITIONS, expected_setup_growth, generous_gas
from .spec import ref_spec_8

REFERENCE_SPEC_GIT_PATH = ref_spec_8.git_path
REFERENCE_SPEC_VERSION = ref_spec_8.version

slot_code_worked = 0x01
value_code_worked = 0x1234
slot_gas_measured = 0x10


@pytest.mark.valid_at_transition_to("MONAD_NEXT")
def test_storage_written_before_fork_readable_after(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Storage written pre-fork is readable post-fork.
    """
    sender = pre.fund_eoa()
    contract_code = (
        Op.SSTORE(1, Op.SLOAD(0)) + Op.SSTORE(0, value_code_worked) + Op.STOP
    )
    contract_address = pre.deploy_contract(contract_code)

    blocks = [
        Block(
            timestamp=14_999,
            txs=[
                Transaction(
                    to=contract_address,
                    sender=sender,
                    nonce=0,
                    gas_limit=generous_gas(fork),
                ),
            ],
        ),
        Block(
            timestamp=15_000,
            txs=[
                Transaction(
                    to=contract_address,
                    sender=sender,
                    nonce=1,
                    gas_limit=generous_gas(fork),
                ),
            ],
        ),
    ]

    blockchain_test(
        pre=pre,
        blocks=blocks,
        post={
            contract_address: Account(
                storage={0: value_code_worked, 1: value_code_worked},
            ),
        },
    )


@pytest.mark.valid_at_transition_to("MONAD_NEXT")
def test_page_warming_works_after_fork(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test that page-level warming works in post-fork block.

    In MONAD_NINE (slot-level): warming slot 0 does NOT warm
    slot 1 — each slot tracked independently.
    In MONAD_NEXT (page-level): warming slot 0 warms entire
    page 0, so slot 1 is also warm.

    Pre-fork block: SLOAD(0) then measure SLOAD(1) gas.
    Post-fork block: same — but SLOAD(1) should be cheaper
    because page warming kicks in.
    """
    sender = pre.fund_eoa()
    overhead = Op.PUSH1(0).gas_cost(fork)

    contract_address = pre.deploy_contract(
        Op.SLOAD(0)
        + CodeGasMeasure(
            code=Op.SLOAD(1),
            overhead_cost=overhead,
            extra_stack_items=1,
            sstore_key=Op.TIMESTAMP,
        )
    )

    blocks = [
        Block(
            timestamp=14_999,
            txs=[
                Transaction(
                    to=contract_address,
                    sender=sender,
                    nonce=0,
                    gas_limit=generous_gas(fork),
                ),
            ],
        ),
        Block(
            timestamp=15_000,
            txs=[
                Transaction(
                    to=contract_address,
                    sender=sender,
                    nonce=1,
                    gas_limit=generous_gas(fork),
                ),
            ],
        ),
    ]

    blockchain_test(
        pre=pre,
        blocks=blocks,
        post={
            contract_address: Account(
                storage={
                    # Pre-fork (MONAD_NINE): slot-level, SLOAD(1)
                    # is cold (different slot from SLOAD(0))
                    14_999: Op.SLOAD(key_warm=False).gas_cost(MONAD_NINE),
                    # Post-fork (MONAD_NEXT): page-level, SLOAD(1)
                    # is warm (same page as SLOAD(0))
                    15_000: Op.SLOAD(page_load_warm=True).gas_cost(MONAD_NEXT),
                },
            ),
        },
    )


@pytest.mark.valid_at_transition_to("MONAD_NEXT")
def test_write_before_fork_read_after_page_warming(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Storage written pre-fork is read post-fork with page-level
    warming.

    Single contract, calldata-branched:
    - Block 1 (pre-fork, empty calldata): SSTORE slots 0 and 1.
    - Block 2 (post-fork, 1-byte calldata): SLOAD(0) (cold page
      load) + POP, then measure SLOAD(1) — same page, WARM under
      MIP-8.
    The measurement only matches if the pre-fork SSTOREs persisted.
    """
    sender = pre.fund_eoa()
    overhead = Op.PUSH1(0).gas_cost(fork)

    contract_address = pre.deploy_contract(
        Conditional(
            condition=Op.CALLDATASIZE,
            if_true=(
                Op.SLOAD(0)
                + CodeGasMeasure(
                    code=Op.SLOAD(1),
                    overhead_cost=overhead,
                    extra_stack_items=1,
                    sstore_key=slot_gas_measured,
                )
            ),
            if_false=Op.SSTORE(0, 0xAA) + Op.SSTORE(1, 0xBB) + Op.STOP,
        )
    )

    blocks = [
        Block(
            timestamp=14_999,
            txs=[
                Transaction(
                    to=contract_address,
                    sender=sender,
                    nonce=0,
                    gas_limit=generous_gas(fork),
                ),
            ],
        ),
        Block(
            timestamp=15_000,
            txs=[
                Transaction(
                    to=contract_address,
                    sender=sender,
                    nonce=1,
                    gas_limit=generous_gas(fork),
                    data=b"\x01",
                ),
            ],
        ),
    ]

    blockchain_test(
        pre=pre,
        blocks=blocks,
        post={
            contract_address: Account(
                storage={
                    0: 0xAA,
                    1: 0xBB,
                    slot_gas_measured: Op.SLOAD(page_load_warm=True).gas_cost(
                        MONAD_NEXT
                    ),
                },
            ),
        },
    )


@pytest.mark.parametrize("scheme", ["1pre_2post", "2pre_1post"])
@pytest.mark.parametrize("orig,curr,new", STATE_TRANSITIONS)
@pytest.mark.valid_at_transition_to("MONAD_NEXT")
def test_sstore_at_fork_transition_block(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
    orig: int,
    curr: int,
    new: int,
    scheme: str,
) -> None:
    """
    SSTORE state-transition matrix split across the MONAD_NINE →
    MONAD_NEXT fork.

    The 0 → orig → curr → new sequence is materialized as up to 3
    SSTOREs, distributed across the two blocks:
    - `1pre_2post`: SSTORE 1 (0→orig) pre-fork; SSTORE 2 (orig→curr,
      setup — only if orig != curr) and SSTORE 3 (curr→new, measured)
      post-fork. Measured SSTORE runs on a warm page (when setup ran).
    - `2pre_1post`: SSTORE 1 and (if orig != curr) SSTORE 2 pre-fork;
      only SSTORE 3 (curr→new, measured) post-fork — cold page.
    """
    slot = 0
    sender = pre.fund_eoa()
    overhead = (Op.PUSH1(0) + Op.PUSH1(0)).gas_cost(fork)

    measured = CodeGasMeasure(
        code=Op.SSTORE(slot, new),
        overhead_cost=overhead,
        extra_stack_items=0,
        sstore_key=slot_gas_measured,
    )
    if scheme == "1pre_2post":
        pre_branch = Op.SSTORE(slot, orig)
        post_branch = Op.SSTORE(slot, curr) + measured
        page_load_warm = True
        page_write_warm = orig != curr
        growth, peak = expected_setup_growth(orig, curr)
    else:  # 2pre_1post
        pre_branch = Op.SSTORE(slot, orig) + Op.SSTORE(slot, curr)
        post_branch = measured
        page_load_warm = False
        page_write_warm = False
        growth, peak = 0, 0

    contract_address = pre.deploy_contract(
        Conditional(
            condition=Op.CALLDATASIZE,
            if_true=post_branch,
            if_false=pre_branch,
        )
    )

    expected_gas = Op.SSTORE(
        page_load_warm=page_load_warm,
        page_write_warm=page_write_warm,
        current_value=curr,
        new_value=new,
        current_state_growth=growth,
        net_state_growth=peak,
    ).gas_cost(MONAD_NEXT)

    blocks = [
        Block(
            timestamp=14_999,
            txs=[
                Transaction(
                    to=contract_address,
                    sender=sender,
                    nonce=0,
                    gas_limit=generous_gas(fork),
                ),
            ],
        ),
        Block(
            timestamp=15_000,
            txs=[
                Transaction(
                    to=contract_address,
                    sender=sender,
                    nonce=1,
                    gas_limit=generous_gas(fork),
                    data=b"\x01",
                ),
            ],
        ),
    ]

    expected_storage = {slot_gas_measured: expected_gas}
    if new != 0:
        expected_storage[slot] = new

    blockchain_test(
        pre=pre,
        blocks=blocks,
        post={contract_address: Account(storage=expected_storage)},
    )


@pytest.mark.valid_at_transition_to("MONAD_NEXT")
def test_access_list_warming_fork_transition(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    EIP-2930 access-list semantics differ across the MIP-8 fork.

    Pre-fork (MONAD_NINE, slot-level): AL warms only the declared
    slot; SLOAD on a different slot of the same page is cold.
    Post-fork (MONAD_NEXT, page-level): AL warms the entire page.
    """
    sender = pre.fund_eoa()
    overhead = Op.PUSH1(0).gas_cost(fork)

    contract_address = pre.deploy_contract(
        CodeGasMeasure(
            code=Op.SLOAD(1),
            overhead_cost=overhead,
            extra_stack_items=1,
            sstore_key=Op.TIMESTAMP,
        )
    )

    al = [AccessList(address=contract_address, storage_keys=[Hash(0)])]

    blocks = [
        Block(
            timestamp=14_999,
            txs=[
                Transaction(
                    ty=1,
                    to=contract_address,
                    sender=sender,
                    nonce=0,
                    gas_limit=generous_gas(fork),
                    access_list=al,
                ),
            ],
        ),
        Block(
            timestamp=15_000,
            txs=[
                Transaction(
                    ty=1,
                    to=contract_address,
                    sender=sender,
                    nonce=1,
                    gas_limit=generous_gas(fork),
                    access_list=al,
                ),
            ],
        ),
    ]

    blockchain_test(
        pre=pre,
        blocks=blocks,
        post={
            contract_address: Account(
                storage={
                    # Pre-fork: slot 1 NOT in AL → cold (slot-level).
                    14_999: Op.SLOAD(key_warm=False).gas_cost(MONAD_NINE),
                    # Post-fork: slot 1 shares page with AL's slot 0 → warm.
                    15_000: Op.SLOAD(page_load_warm=True).gas_cost(MONAD_NEXT),
                },
            ),
        },
    )
