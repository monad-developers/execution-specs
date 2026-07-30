"""MIP-11 distribution across multiple blocks."""

import pytest
from execution_testing import (
    Account,
    Alloc,
    Block,
    BlockchainTestFiller,
    Op,
    Transaction,
)

from .helpers import make_fee_tx
from .spec import (
    BASE_FEE,
    FEE_DISTRIBUTION,
    KEY_PROPOSER_VAL_ID,
    MON,
    STAKING_PRECOMPILE,
    Validator,
    distribution,
    reward_tx,
    staking_storage,
)

pytestmark = [
    pytest.mark.valid_from("MONAD_NEXT"),
    pytest.mark.pre_alloc_mutable,
]


def test_two_blocks(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
) -> None:
    """
    Distribution runs once per block and the pool accumulator grows.

    Each block carries a reward syscall (setting the proposer) and a
    fee-bearing transaction; the distribution account is emptied every
    block and the validator pool reflects both blocks' fees.
    """
    fee = 4 * MON
    validator = Validator(val_id=1, auth=pre.fund_eoa(0), stake=MON)
    pre[STAKING_PRECOMPILE] = Account(
        nonce=1, storage=staking_storage([validator])
    )

    blocks = [
        Block(
            txs=[
                reward_tx(validator.auth, nonce=n),
                make_fee_tx(pre, fee),
            ]
        )
        for n in range(2)
    ]

    # Two equal distributions accumulate linearly into the pool.
    dist = distribution(validator, 2 * fee)
    storage = staking_storage([validator])
    storage.update(dist.storage)
    storage[KEY_PROPOSER_VAL_ID] = validator.val_id << 192

    blockchain_test(
        pre=pre,
        post={
            FEE_DISTRIBUTION: None,
            STAKING_PRECOMPILE: Account(
                nonce=1, balance=dist.staking_balance, storage=storage
            ),
        },
        blocks=blocks,
    )


def _observer_tx(pre: Alloc, reader: object, slot: int) -> Transaction:
    """Record ``BALANCE(fee5)`` at ``slot``; adds no fee (zero tip)."""
    return Transaction(
        gas_limit=100_000,
        max_fee_per_gas=BASE_FEE,
        max_priority_fee_per_gas=0,
        to=reader,
        data=slot.to_bytes(32, "big"),
        sender=pre.fund_eoa(),
    )


def test_fee5_accrues_per_tx_and_resets(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
) -> None:
    """
    fee5 grows by each tx's priority fee and opens the next block empty.

    Zero-tip observer transactions read the running balance: after one
    fee it holds that fee, after a second it holds their sum, and the
    first read of the following block sees zero.
    """
    fee1 = 2 * MON
    fee2 = 3 * MON
    reader = pre.deploy_contract(
        Op.SSTORE(Op.CALLDATALOAD(0), Op.BALANCE(FEE_DISTRIBUTION))
    )

    block1 = Block(
        txs=[
            make_fee_tx(pre, fee1),
            _observer_tx(pre, reader, 1),
            make_fee_tx(pre, fee2),
            _observer_tx(pre, reader, 2),
        ]
    )
    block2 = Block(txs=[_observer_tx(pre, reader, 3)])

    blockchain_test(
        pre=pre,
        post={
            reader: Account(storage={1: fee1, 2: fee1 + fee2, 3: 0}),
            FEE_DISTRIBUTION: None,
        },
        blocks=[block1, block2],
    )
