"""
MIP-11 distribution account behaves as an ordinary account.

The fee5 address is not a precompile: it can be called, sent value,
introspected, selfdestructed to, and used as a 7702 delegation target
like any other account. The end-of-block distribution empties whatever
balance it holds (burned here, since no validator is registered).
"""

import pytest
from execution_testing import (
    Account,
    Alloc,
    Block,
    BlockchainTestFiller,
    Op,
    Transaction,
)
from execution_testing.test_types.receipt_types import TransactionReceipt

from .spec import EMPTY_CODE_HASH, FEE_DISTRIBUTION, MON

pytestmark = [
    pytest.mark.valid_from("MONAD_NEXT"),
    pytest.mark.pre_alloc_mutable,
]

slot_size = 0x1
slot_hash = 0x2
slot_balance = 0x3
slot_copy = 0x4
slot_success = 0x5


@pytest.mark.parametrize("funded", [False, True])
def test_fee5_introspection(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    funded: bool,
) -> None:
    """EXTCODESIZE/HASH/COPY and BALANCE see fee5 as a plain account."""
    balance = 3 * MON if funded else 0
    if funded:
        pre[FEE_DISTRIBUTION] = Account(balance=balance)

    code = (
        Op.SSTORE(slot_size, Op.EXTCODESIZE(FEE_DISTRIBUTION))
        + Op.SSTORE(slot_hash, Op.EXTCODEHASH(FEE_DISTRIBUTION))
        + Op.SSTORE(slot_balance, Op.BALANCE(FEE_DISTRIBUTION))
        + Op.EXTCODECOPY(FEE_DISTRIBUTION, 0, 0, 32)
        + Op.SSTORE(slot_copy, Op.MLOAD(0))
    )
    contract = pre.deploy_contract(code)

    tx = Transaction(gas_limit=200_000, to=contract, sender=pre.fund_eoa())

    blockchain_test(
        pre=pre,
        post={
            contract: Account(
                storage={
                    slot_size: 0,
                    slot_hash: EMPTY_CODE_HASH if funded else 0,
                    slot_balance: balance,
                    slot_copy: 0,
                }
            ),
            # Any balance read above is burned at end of block.
            FEE_DISTRIBUTION: None,
        },
        blocks=[Block(txs=[tx])],
    )


def test_fee5_call_with_value(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
) -> None:
    """A CALL with value to fee5 succeeds and transfers value."""
    value = 5 * MON
    code = Op.SSTORE(
        slot_success, Op.CALL(address=FEE_DISTRIBUTION, value=value)
    )
    contract = pre.deploy_contract(code, balance=value)

    tx = Transaction(gas_limit=200_000, to=contract, sender=pre.fund_eoa())

    blockchain_test(
        pre=pre,
        post={
            contract: Account(balance=0, storage={slot_success: 1}),
            FEE_DISTRIBUTION: None,
        },
        blocks=[Block(txs=[tx])],
    )


def test_fee5_top_level_tx(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
) -> None:
    """A top-level transaction to fee5 with value succeeds."""
    tx = Transaction(
        gas_limit=100_000,
        to=FEE_DISTRIBUTION,
        value=5 * MON,
        sender=pre.fund_eoa(),
        expected_receipt=TransactionReceipt(status=1),
    )

    blockchain_test(
        pre=pre,
        post={FEE_DISTRIBUTION: None},
        blocks=[Block(txs=[tx])],
    )


@pytest.mark.parametrize("value", [0, 5 * MON])
def test_fee5_selfdestruct_beneficiary(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
) -> None:
    """A contract may name fee5 as its SELFDESTRUCT beneficiary."""
    contract = pre.deploy_contract(
        Op.SELFDESTRUCT(FEE_DISTRIBUTION), balance=value
    )

    tx = Transaction(gas_limit=200_000, to=contract, sender=pre.fund_eoa())

    blockchain_test(
        pre=pre,
        post={
            # Predeployed contract: EIP-6780 keeps it, drained to 0.
            contract: Account(balance=0),
            FEE_DISTRIBUTION: None,
        },
        blocks=[Block(txs=[tx])],
    )


def test_fee5_as_delegation_target(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
) -> None:
    """An EOA delegating to fee5 executes empty code (a no-op)."""
    delegator = pre.fund_eoa(0, delegation=FEE_DISTRIBUTION)
    code = Op.SSTORE(slot_success, Op.CALL(address=delegator))
    contract = pre.deploy_contract(code)

    tx = Transaction(gas_limit=200_000, to=contract, sender=pre.fund_eoa())

    blockchain_test(
        pre=pre,
        post={contract: Account(storage={slot_success: 1})},
        blocks=[Block(txs=[tx])],
    )
