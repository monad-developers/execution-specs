"""
Tests MONAD_NINE -> MONAD_NEXT fork transition for MIP-8 storage.
"""

import pytest
from execution_testing import (
    Account,
    Alloc,
    Block,
    BlockchainTestFiller,
    CodeGasMeasure,
    Op,
    Transaction,
)
from execution_testing.forks.helpers import Fork

from .helpers import generous_gas
from .spec import Spec, ref_spec_8

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
    Test that storage written in MONAD_NINE is readable in MONAD_NEXT.

    Block 1 (pre-fork): SSTORE(0, 0xBEEF)
    Block 2 (post-fork): SLOAD(0) and store result in slot 1
    """
    sender = pre.fund_eoa()
    writer_code = Op.SSTORE(0, 0xBEEF) + Op.STOP
    reader_code = Op.SSTORE(1, Op.SLOAD(0)) + Op.STOP

    writer_address = pre.deploy_contract(writer_code)
    reader_address = pre.deploy_contract(reader_code)

    blocks = [
        Block(
            timestamp=14_999,
            txs=[
                Transaction(
                    to=writer_address,
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
                    to=reader_address,
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
            writer_address: Account(storage={0: 0xBEEF}),
            reader_address: Account(storage={1: 0}),
        },
    )


@pytest.mark.valid_at_transition_to("MONAD_NEXT")
def test_storage_state_unchanged_across_fork(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test that fork transition doesn't alter pre-existing storage.

    Pre-populate storage, send trivial tx in transition block,
    verify values unchanged via SLOAD in post-fork block.
    """
    sender = pre.fund_eoa()

    contract_address = pre.deploy_contract(
        Op.SSTORE(0x10, Op.SLOAD(0))
        + Op.SSTORE(0x11, Op.SLOAD(1))
        + Op.SSTORE(0x12, Op.SLOAD(2))
        + Op.STOP,
        storage={0: 0xAA, 1: 0xBB, 2: 0xCC},
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
                    0: 0xAA,
                    1: 0xBB,
                    2: 0xCC,
                    0x10: 0xAA,
                    0x11: 0xBB,
                    0x12: 0xCC,
                },
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
        + Op.POP
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
                    14_999: Spec.GAS_COLD_PAGE_READ,
                    # Post-fork (MONAD_NEXT): page-level, SLOAD(1)
                    # is warm (same page as SLOAD(0))
                    15_000: Spec.GAS_BASE_SLOAD,
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
    Test that storage written in MONAD_NINE can be read with
    page-level warming in MONAD_NEXT.

    Block 1 (pre-fork): SSTORE to slots 0 and 1 (same page)
    Block 2 (post-fork): SLOAD(0) then SLOAD(1) — second SLOAD
    should be warm because page 0 was warmed by first SLOAD.
    """
    sender = pre.fund_eoa()
    overhead = Op.PUSH1(0).gas_cost(fork)

    writer_address = pre.deploy_contract(
        Op.SSTORE(0, 0xAA) + Op.SSTORE(1, 0xBB) + Op.STOP
    )

    reader_address = pre.deploy_contract(
        Op.SLOAD(0)
        + Op.POP
        + CodeGasMeasure(
            code=Op.SLOAD(1),
            overhead_cost=overhead,
            extra_stack_items=1,
            sstore_key=slot_gas_measured,
        )
    )

    blocks = [
        Block(
            timestamp=14_999,
            txs=[
                Transaction(
                    to=writer_address,
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
                    to=reader_address,
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
            writer_address: Account(storage={0: 0xAA, 1: 0xBB}),
            reader_address: Account(
                storage={slot_gas_measured: Spec.GAS_BASE_SLOAD},
            ),
        },
    )
