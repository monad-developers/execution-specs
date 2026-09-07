"""
Tests that Monad forks reject blob transactions.

Monad advertises no blob schedule and carries the blob header fields
fixed at zero, so the type byte EIP-4844 assigns is unknown to the fork
and a transaction carrying it is invalid rather than merely unused.
"""

import pytest
from execution_testing import (
    Account,
    Alloc,
    Block,
    BlockchainTestFiller,
    Hash,
    Transaction,
    TransactionException,
    add_kzg_version,
)
from execution_testing.forks.helpers import Fork

BLOB_COMMITMENT_VERSION_KZG = 1

pytestmark = [
    pytest.mark.valid_from("MONAD_EIGHT"),
    pytest.mark.exception_test,
]


@pytest.mark.parametrize(
    "gas_limit",
    [
        pytest.param(100_000, id="sufficient_gas"),
        # Below the intrinsic cost, so the transaction would fail generic
        # validation too; the unknown type is reported ahead of it.
        pytest.param(1_000, id="below_intrinsic_gas"),
    ],
)
def test_blob_transaction_is_rejected(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
    gas_limit: int,
) -> None:
    """A blob transaction is not a transaction type the fork knows."""
    assert 3 not in fork.tx_types()

    sender = pre.fund_eoa()
    tx = Transaction(
        ty=3,
        to=pre.fund_eoa(amount=0),
        gas_limit=gas_limit,
        max_fee_per_blob_gas=1,
        blob_versioned_hashes=add_kzg_version(
            [Hash(1)], BLOB_COMMITMENT_VERSION_KZG
        ),
        sender=sender,
        error=TransactionException.TYPE_NOT_SUPPORTED,
    )

    blockchain_test(
        pre=pre,
        post={sender: Account(nonce=0)},
        blocks=[Block(txs=[tx], exception=tx.error)],
    )
