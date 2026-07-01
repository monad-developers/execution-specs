"""MIP-11 distribution across multiple blocks."""

import pytest
from execution_testing import (
    Account,
    Alloc,
    Block,
    BlockchainTestFiller,
)

from .helpers import make_fee_tx
from .spec import (
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
