"""
MIP-11 distribution account access warmth.

The fee5 address is a plain account: it is cold on first access (not
pre-warmed like a precompile or the coinbase), and it warms the normal
ways — via an access-list entry or a prior access.

Two consecutive ``BALANCE(fee5)`` costs are measured. Their difference
cancels the fixed measurement overhead, leaving exactly the cold/warm
gap (when the first access is cold) or zero (when already warm).
"""

import pytest
from execution_testing import (
    AccessList,
    Account,
    Alloc,
    Block,
    BlockchainTestFiller,
    Bytecode,
    Op,
    Transaction,
)
from execution_testing.forks.helpers import Fork

from .spec import FEE_DISTRIBUTION

pytestmark = [
    pytest.mark.valid_from("MONAD_NEXT"),
    pytest.mark.pre_alloc_mutable,
]

slot_diff = 0x1


def _measure_balance() -> Bytecode:
    """Leave on the stack the gas consumed by one ``BALANCE(fee5)``."""
    return (
        Op.GAS
        + Op.BALANCE(FEE_DISTRIBUTION)
        + Op.POP
        + Op.GAS
        + Op.SWAP1
        + Op.SUB
    )


@pytest.mark.parametrize("access_listed", [False, True])
def test_fee5_access_warmth(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
    access_listed: bool,
) -> None:
    """
    fee5 is cold by default and warms via access list or prior access.

    ``slot_diff`` holds (first access cost - second access cost): the
    cold/warm gap when fee5 starts cold, or zero when pre-warmed by an
    access-list entry.
    """
    # Measure two accesses, store first - second.
    code = (
        _measure_balance()
        + _measure_balance()
        + Op.SWAP1
        + Op.SUB
        + Op.PUSH1(slot_diff)
        + Op.SSTORE
    )
    contract = pre.deploy_contract(code)

    cold = Op.BALANCE(address_warm=False).gas_cost(fork)
    warm = Op.BALANCE(address_warm=True).gas_cost(fork)
    expected_diff = 0 if access_listed else cold - warm

    access_list = (
        [AccessList(address=FEE_DISTRIBUTION, storage_keys=[])]
        if access_listed
        else []
    )

    tx = Transaction(
        ty=1,
        gas_limit=200_000,
        to=contract,
        sender=pre.fund_eoa(),
        access_list=access_list,
    )

    blockchain_test(
        pre=pre,
        post={contract: Account(storage={slot_diff: expected_diff})},
        blocks=[Block(txs=[tx])],
    )
