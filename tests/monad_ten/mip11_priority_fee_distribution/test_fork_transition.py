"""
MIP-11 priority fee routing across the fork boundary.

Before the fork a transaction's priority fee is paid to the block
coinbase; at and after the fork it accrues to the distribution account
and is distributed to the proposer pool. The same fee-bearing
transaction routes differently on each side of the boundary.
"""

import pytest
from execution_testing import (
    Account,
    Address,
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

# Default block coinbase used by the test framework.
COINBASE = Address(0x2ADC25665018AA1FE0E6BC666DAC8FC2697FF9BA)

pytestmark = [pytest.mark.pre_alloc_mutable]


@pytest.mark.valid_at_transition_to("MONAD_NEXT", subsequent_forks=True)
def test_priority_fee_routing_across_fork(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
) -> None:
    """
    A fee-bearing transaction pays the coinbase before the fork and the
    proposer pool after it.

    The pre-fork block credits the coinbase with the whole priority fee
    and leaves fee5 empty; the post-fork block routes the same fee to
    fee5 and distributes it to the pool, crediting the coinbase nothing.
    """
    fee = 2 * MON
    validator = Validator(val_id=1, auth=pre.fund_eoa(0), stake=MON)
    pre[STAKING_PRECOMPILE] = Account(
        nonce=1, storage=staking_storage([validator])
    )

    pre_fork = Block(timestamp=14_999, txs=[make_fee_tx(pre, fee)])
    post_fork = Block(
        timestamp=15_000,
        txs=[reward_tx(validator.auth), make_fee_tx(pre, fee)],
    )

    dist = distribution(validator, fee)
    storage = staking_storage([validator])
    storage.update(dist.storage)
    storage[KEY_PROPOSER_VAL_ID] = validator.val_id << 192

    blockchain_test(
        pre=pre,
        post={
            # Only the pre-fork block paid the coinbase.
            COINBASE: Account(balance=fee),
            FEE_DISTRIBUTION: None,
            STAKING_PRECOMPILE: Account(
                nonce=1, balance=dist.staking_balance, storage=storage
            ),
        },
        blocks=[pre_fork, post_fork],
    )
