"""
MIP-11 priority fee distribution to a validator pool.

A reward-syscall system transaction sets the block proposer; the
end-of-block distribution then credits the proposer pool's accumulator
(commission to the auth delegator), matching the staking precompile's
storage layout and accumulator math.

Spec: https://mips.monad.xyz/MIPs/MIP-11
"""

import pytest
from execution_testing import (
    Account,
    Address,
    Alloc,
    Block,
    BlockchainTestFiller,
    Bytecode,
    Op,
    Transaction,
)

from .helpers import FEE_TX_GAS, make_fee_txs
from .spec import (
    BASE_FEE,
    DUST_THRESHOLD,
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


def _staking_post_account(
    validator: Validator, fee: int, *, proposer: int
) -> Account:
    """Return the expected staking account after distributing ``fee``."""
    storage = staking_storage([validator])
    dist = distribution(validator, fee)
    storage.update(dist.storage)
    if proposer:
        storage[KEY_PROPOSER_VAL_ID] = proposer << 192
    return Account(nonce=1, balance=dist.staking_balance, storage=storage)


@pytest.mark.parametrize(
    "fee",
    [
        pytest.param(DUST_THRESHOLD - 1, id="below_dust"),
        pytest.param(DUST_THRESHOLD, id="dust_threshold"),
        pytest.param(MON, id="one_mon"),
        pytest.param(1_000_000 * MON, id="million_mon"),
        pytest.param(1_000_001 * MON, id="over_million_mon"),
        pytest.param(10**30, id="huge"),
    ],
)
def test_fee_amounts(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fee: int,
) -> None:
    """
    Distribute accumulated fees of varying size to the proposer pool.

    Sub-dust fees are burned; everything at or above the dust threshold
    is distributed (there is no upper cap). The distribution account is
    emptied.
    """
    validator = Validator(val_id=1, auth=pre.fund_eoa(0), stake=MON)
    pre[STAKING_PRECOMPILE] = Account(
        nonce=1, storage=staking_storage([validator])
    )
    pre[FEE_DISTRIBUTION] = Account(balance=fee)

    blockchain_test(
        pre=pre,
        post={
            FEE_DISTRIBUTION: None,
            STAKING_PRECOMPILE: _staking_post_account(
                validator, fee, proposer=1
            ),
        },
        blocks=[Block(txs=[reward_tx(validator.auth)])],
    )


def test_commission_credited_to_auth(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
) -> None:
    """A nonzero commission is credited to the auth delegator's rewards."""
    fee = 10 * MON
    validator = Validator(
        val_id=1, auth=pre.fund_eoa(0), stake=MON, commission=10**17
    )
    pre[STAKING_PRECOMPILE] = Account(
        nonce=1, storage=staking_storage([validator])
    )
    pre[FEE_DISTRIBUTION] = Account(balance=fee)

    blockchain_test(
        pre=pre,
        post={
            FEE_DISTRIBUTION: None,
            STAKING_PRECOMPILE: _staking_post_account(
                validator, fee, proposer=1
            ),
        },
        blocks=[Block(txs=[reward_tx(validator.auth)])],
    )


def test_no_proposer_burns(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
) -> None:
    """
    Without a reward syscall the proposer is cleared, so accumulated fees
    are burned and the validator pool is untouched.
    """
    fee = 5 * MON
    validator = Validator(val_id=1, auth=pre.fund_eoa(0), stake=MON)
    seeded = staking_storage([validator])
    pre[STAKING_PRECOMPILE] = Account(nonce=1, storage=seeded)
    pre[FEE_DISTRIBUTION] = Account(balance=fee)

    blockchain_test(
        pre=pre,
        post={
            FEE_DISTRIBUTION: None,
            # Prelude clears the proposer; storage otherwise unchanged.
            STAKING_PRECOMPILE: Account(nonce=1, balance=0, storage=seeded),
        },
        blocks=[Block(txs=[])],
    )


@pytest.mark.parametrize("n_txs", [1, 3])
def test_priority_fee_from_gas(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    n_txs: int,
) -> None:
    """
    Priority fees paid by real transactions accrue to the distribution
    account and are distributed to the proposer pool.
    """
    total_fee = 6 * MON
    validator = Validator(val_id=1, auth=pre.fund_eoa(0), stake=MON)
    pre[STAKING_PRECOMPILE] = Account(
        nonce=1, storage=staking_storage([validator])
    )

    txs = [reward_tx(validator.auth)] + make_fee_txs(pre, total_fee, n_txs)

    blockchain_test(
        pre=pre,
        post={
            FEE_DISTRIBUTION: None,
            STAKING_PRECOMPILE: _staking_post_account(
                validator, total_fee, proposer=1
            ),
        },
        blocks=[Block(txs=txs)],
    )


@pytest.mark.parametrize(
    "halt",
    [Op.STOP, pytest.param(Op.REVERT(0, 0), id="revert"), Op.INVALID],
)
def test_reverted_fee_distributed(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    halt: Bytecode,
) -> None:
    """
    A transaction's priority fee is distributed regardless of outcome.

    The full gas limit is charged whether the call halts, reverts, or
    hits an invalid opcode, so the same fee accrues and the pool receives
    the same reward.
    """
    fee = 6 * MON
    validator = Validator(val_id=1, auth=pre.fund_eoa(0), stake=MON)
    pre[STAKING_PRECOMPILE] = Account(
        nonce=1, storage=staking_storage([validator])
    )

    target = pre.deploy_contract(halt)
    priority = fee // FEE_TX_GAS
    fee_tx = Transaction(
        gas_limit=FEE_TX_GAS,
        max_fee_per_gas=BASE_FEE + priority,
        max_priority_fee_per_gas=priority,
        to=target,
        sender=pre.fund_eoa(FEE_TX_GAS * (BASE_FEE + priority) + 11 * MON),
    )

    blockchain_test(
        pre=pre,
        post={
            FEE_DISTRIBUTION: None,
            STAKING_PRECOMPILE: _staking_post_account(
                validator, fee, proposer=1
            ),
        },
        blocks=[Block(txs=[reward_tx(validator.auth), fee_tx])],
    )


# MIP-4 reserve balance: an account's ending balance may not drop below
# this except via a qualifying emptying transaction.
RESERVE_BALANCE = 10 * MON


@pytest.mark.parametrize("violated", [False, True])
def test_reserve_violation_fee_distributed(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    violated: bool,
) -> None:
    """
    A transaction reverting on a reserve-balance violation still pays its
    priority fee, so the pool receives the same reward as on success.

    The sender is delegated, so it never qualifies for the
    emptying-transaction exception. It sends value on top of gas: at the
    reserve the spend dips below it and the transaction reverts; funded
    above the reserve it succeeds. The priority fee accrues either way.
    """
    fee = 4 * MON
    value = MON
    validator = Validator(val_id=1, auth=pre.fund_eoa(0), stake=MON)
    pre[STAKING_PRECOMPILE] = Account(
        nonce=1, storage=staking_storage([validator])
    )

    slot = 0x1
    target = pre.deploy_contract(Op.SSTORE(slot, 1))
    priority = fee // FEE_TX_GAS
    gas_cost = FEE_TX_GAS * (BASE_FEE + priority)
    balance = (
        RESERVE_BALANCE
        if violated
        else RESERVE_BALANCE + gas_cost + value + MON
    )
    fee_tx = Transaction(
        gas_limit=FEE_TX_GAS,
        max_fee_per_gas=BASE_FEE + priority,
        max_priority_fee_per_gas=priority,
        to=target,
        value=value,
        sender=pre.fund_eoa(balance, delegation=Address(0x0111)),
    )

    blockchain_test(
        pre=pre,
        post={
            FEE_DISTRIBUTION: None,
            STAKING_PRECOMPILE: _staking_post_account(
                validator, fee, proposer=1
            ),
            # The value transfers and marker persist only on success.
            target: (
                Account(balance=0, storage={})
                if violated
                else Account(balance=value, storage={slot: 1})
            ),
        },
        blocks=[Block(txs=[reward_tx(validator.auth), fee_tx])],
    )


def test_in_block_transfer_distributed(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
) -> None:
    """
    A plain value transfer into fee5 during the block is distributed to
    the proposer pool: distribution forwards the account's whole balance,
    not only accumulated priority fees.
    """
    transfer = 5 * MON
    validator = Validator(val_id=1, auth=pre.fund_eoa(0), stake=MON)
    pre[STAKING_PRECOMPILE] = Account(
        nonce=1, storage=staking_storage([validator])
    )

    transfer_tx = Transaction(
        gas_limit=100_000,
        max_fee_per_gas=BASE_FEE,
        max_priority_fee_per_gas=0,
        to=FEE_DISTRIBUTION,
        value=transfer,
        sender=pre.fund_eoa(transfer + 11 * MON),
    )

    blockchain_test(
        pre=pre,
        post={
            FEE_DISTRIBUTION: None,
            STAKING_PRECOMPILE: _staking_post_account(
                validator, transfer, proposer=1
            ),
        },
        blocks=[Block(txs=[reward_tx(validator.auth), transfer_tx])],
    )
