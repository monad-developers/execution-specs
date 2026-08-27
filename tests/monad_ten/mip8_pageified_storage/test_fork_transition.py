"""
Tests MONAD_NINE -> MONAD_TEN fork transition for MIP-8 storage.
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
    Conditional,
    Hash,
    Op,
    Storage,
    Transaction,
)
from execution_testing.base_types.conversions import NumberConvertible
from execution_testing.forks import MONAD_NINE, MONAD_TEN
from execution_testing.forks.helpers import Fork

from .helpers import (
    STATE_TRANSITIONS,
    TxPageState,
    generous_gas,
    simulate_sstore,
)
from .spec import Spec, ref_spec_8

REFERENCE_SPEC_GIT_PATH = ref_spec_8.git_path
REFERENCE_SPEC_VERSION = ref_spec_8.version

value_code_worked = 0x1234
slot_gas_measured = 0x10


@pytest.mark.valid_at_transition_to("MONAD_TEN")
def test_storage_persists_at_fork(
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


@pytest.mark.valid_at_transition_to("MONAD_TEN")
def test_page_warming_activates_at_fork(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Page-level warming activates in post-fork block.

    In MONAD_NINE (slot-level): warming slot 0 does NOT warm
    slot 1 — each slot tracked independently.
    In MONAD_TEN (page-level): warming slot 0 warms entire
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
                    # Post-fork (MONAD_TEN): page-level, SLOAD(1)
                    # is warm (same page as SLOAD(0))
                    15_000: Op.SLOAD(page_load_warm=True).gas_cost(MONAD_TEN),
                },
            ),
        },
    )


@pytest.mark.valid_at_transition_to("MONAD_TEN")
def test_existing_storage_warms_page_at_fork(
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
                        MONAD_TEN
                    ),
                },
            ),
        },
    )


@pytest.mark.parametrize("scheme", ["1pre_2post", "2pre_1post"])
@pytest.mark.parametrize("orig,curr,new", STATE_TRANSITIONS)
@pytest.mark.valid_at_transition_to("MONAD_TEN")
def test_sstore_state_transitions_at_fork(
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
    MONAD_TEN fork.

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

    page = TxPageState(slots={slot: orig if scheme == "1pre_2post" else curr})
    if scheme == "1pre_2post":
        pre_branch = Op.SSTORE(slot, orig)
        post_branch = Op.SSTORE(slot, curr) + measured
        simulate_sstore(page, slot, curr, MONAD_TEN)
    else:  # 2pre_1post
        pre_branch = Op.SSTORE(slot, orig) + Op.SSTORE(slot, curr)
        post_branch = measured

    contract_address = pre.deploy_contract(
        Conditional(
            condition=Op.CALLDATASIZE,
            if_true=post_branch,
            if_false=pre_branch,
        )
    )

    expected_gas = simulate_sstore(page, slot, new, MONAD_TEN)

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


@pytest.mark.valid_at_transition_to("MONAD_TEN")
def test_access_list_warming_at_fork(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    EIP-2930 access-list semantics differ across the MIP-8 fork.

    Pre-fork (MONAD_NINE, slot-level): AL warms only the declared
    slot; SLOAD on a different slot of the same page is cold.
    Post-fork (MONAD_TEN, page-level): AL warms the entire page.
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
                    15_000: Op.SLOAD(page_load_warm=True).gas_cost(MONAD_TEN),
                },
            ),
        },
    )


@pytest.mark.valid_at_transition_to("MONAD_TEN")
def test_blockhash_stable_across_fork(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    BLOCKHASH of pre-fork blocks stays the same when queried post-fork.

    MIP-8 changes the state-root commitment; pre-fork block hashes must
    not change when read post-fork.
    """
    sender = pre.fund_eoa()

    def slot_blockhash(i: int) -> Bytecode:
        return Op.ADD(Op.MUL(Op.TIMESTAMP, 16), i)

    def slot_nonzero(i: int) -> Bytecode:
        return Op.ADD(Op.MUL(Op.TIMESTAMP, 16), 4 + i)

    def prev_slot(i: int) -> Bytecode:
        return Op.ADD(Op.MUL(Op.SUB(Op.TIMESTAMP, 1), 16), i)

    slot_stable = Op.MUL(Op.TIMESTAMP, 16)

    def stable_i(i: int) -> Bytecode:
        return Op.OR(
            Op.ISZERO(Op.SLOAD(prev_slot(i))),
            Op.EQ(Op.SLOAD(prev_slot(i)), Op.BLOCKHASH(i)),
        )

    contract_code = (
        Op.SSTORE(slot_blockhash(1), Op.BLOCKHASH(1))
        + Op.SSTORE(slot_blockhash(2), Op.BLOCKHASH(2))
        + Op.SSTORE(slot_blockhash(3), Op.BLOCKHASH(3))
        + Op.SSTORE(slot_nonzero(1), Op.ISZERO(Op.ISZERO(Op.BLOCKHASH(1))))
        + Op.SSTORE(slot_nonzero(2), Op.ISZERO(Op.ISZERO(Op.BLOCKHASH(2))))
        + Op.SSTORE(slot_nonzero(3), Op.ISZERO(Op.ISZERO(Op.BLOCKHASH(3))))
        + Op.SSTORE(
            slot_stable,
            Op.AND(Op.AND(stable_i(1), stable_i(2)), stable_i(3)),
        )
    )
    contract_address = pre.deploy_contract(contract_code)

    timestamps = [14_998, 14_999, 15_000, 15_001]
    blocks = [
        Block(
            timestamp=ts,
            txs=[
                Transaction(
                    to=contract_address,
                    sender=sender,
                    nonce=i,
                    gas_limit=generous_gas(fork),
                ),
            ],
        )
        for i, ts in enumerate(timestamps)
    ]

    # Per-timestamp tuple is (is_nonzero(BLOCKHASH(1)),
    # is_nonzero(BLOCKHASH(2)), is_nonzero(BLOCKHASH(3))) computed
    # during that block. BLOCKHASH(n) is non-zero iff n is a past
    # block (1 <= n < current_block_number).
    #   ts=14_998 -> block 1: queries blocks 1,2,3 — all current/future
    #   ts=14_999 -> block 2: block 1 is past, 2 is current, 3 future
    #   ts=15_000 -> block 3 (post-fork): blocks 1,2 past, 3 current
    #   ts=15_001 -> block 4 (post-fork): blocks 1,2,3 all past
    nonzero_pattern = {
        14_998: (0, 0, 0),
        14_999: (1, 0, 0),
        15_000: (1, 1, 0),
        15_001: (1, 1, 1),
    }
    # Per-block slot layout (offset within ts*16 base):
    storage = Storage()
    for ts in timestamps:
        # ts*16: is BLOCKHASH stable
        storage[ts * 16] = 1
        for i in (1, 2, 3):
            flag = nonzero_pattern[ts][i - 1]
            if flag:
                # ts*16 + 1..+3 : BLOCKHASH(1..3) value
                storage.set_expect_any(ts * 16 + i)
            # ts*16 + 5..+7 : is_nonzero(BLOCKHASH(1..3))
            storage[ts * 16 + 4 + i] = flag

    blockchain_test(
        pre=pre,
        blocks=blocks,
        post={contract_address: Account(storage=storage)},
    )


@pytest.mark.parametrize("other_account_touched", [False, True])
@pytest.mark.valid_at_transition_to("MONAD_TEN")
def test_state_root_untouched_storage_at_fork(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    other_account_touched: bool,
) -> None:
    """
    Storage untouched by the post-fork block still commits under the
    MIP-8 page scheme.
    """
    sender = pre.fund_eoa()

    storage: dict[NumberConvertible, NumberConvertible] = {
        0: 1,
        1: 2**256 - 1,
        Spec.SLOTS_PER_PAGE - 1: 0x2A,
        Spec.SLOTS_PER_PAGE: 0x2B,
        2 * Spec.SLOTS_PER_PAGE - 1: 0x2C,
        2**256 - 1: 2**255,
    }
    contract_address = pre.deploy_contract(Op.STOP, storage=storage)

    timestamps = [14_999, 15_000]
    post: dict = {contract_address: Account(storage=storage)}

    if other_account_touched:
        target = pre.deploy_contract(Op.SSTORE(0, Op.TIMESTAMP))
        post[target] = Account(storage={0: timestamps[-1]})
    else:
        target = pre.fund_eoa(amount=0)

    blocks = [
        Block(
            timestamp=ts,
            txs=[
                Transaction(
                    to=target,
                    value=1,
                    sender=sender,
                    nonce=nonce,
                ),
            ],
        )
        for nonce, ts in enumerate(timestamps)
    ]

    blockchain_test(pre=pre, blocks=blocks, post=post)
