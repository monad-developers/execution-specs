"""
Tests reserve balance when a transaction's gas fees reach the reserve.
"""

from enum import Enum, auto, unique

import pytest
from execution_testing import (
    Account,
    Alloc,
    Block,
    BlockchainTestFiller,
    Op,
    Transaction,
)
from execution_testing.forks.helpers import Fork

from .spec import Spec, ref_spec_7702

REFERENCE_SPEC_GIT_PATH = ref_spec_7702.git_path
REFERENCE_SPEC_VERSION = ref_spec_7702.version

slot_code_worked = 0x1
value_code_worked = 0x1234

pytestmark = [
    pytest.mark.valid_from("MONAD_EIGHT"),
    pytest.mark.pre_alloc_group(
        "reserve_balance_tests",
        reason="Tests reserve balance",
    ),
]


@unique
class GasFees(Enum):
    """Gas fees of a max-gas transaction, relative to the reserve."""

    HALF_RESERVE = auto()
    JUST_BELOW_RESERVE = auto()
    JUST_ABOVE_RESERVE = auto()
    DOUBLE_RESERVE = auto()

    def __str__(self) -> str:
        """Return string representation."""
        return self.name.lower()

    def gas_price(self, gas_limit: int) -> int:
        """
        Compute the gas price placing the fees at this point.

        The reserve is not an exact multiple of the gas limit, so the
        boundary price is the largest one whose fees still fit within the
        reserve, and the pair straddling it is the tightest available.
        """
        boundary = Spec.RESERVE_BALANCE // gas_limit
        match self:
            case GasFees.HALF_RESERVE:
                return boundary // 2
            case GasFees.JUST_BELOW_RESERVE:
                return boundary
            case GasFees.JUST_ABOVE_RESERVE:
                return boundary + 1
            case GasFees.DOUBLE_RESERVE:
                return 2 * boundary


@pytest.mark.parametrize("gas_fees", list(GasFees))
def test_gas_fees_never_violate_reserve(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
    gas_fees: GasFees,
) -> None:
    """
    Test that a transaction is allowed through however large its gas fees
    are, including at and above the whole reserve.

    The sender's violation threshold is `max(reserve - gas_fees, 0)`: gas
    spend does not count against the reserve, only value spend does. The
    sender is funded to sit exactly at the reserve once the gas is
    debited, and sends no value, so the gas term alone decides.
    """
    gas_limit = fork.transaction_gas_limit_cap()
    assert gas_limit is not None, "fork must cap the transaction gas limit"
    gas_price = gas_fees.gas_price(gas_limit)

    contract_address = pre.deploy_contract(
        Op.SSTORE(slot_code_worked, value_code_worked) + Op.STOP
    )
    sender = pre.fund_eoa(Spec.RESERVE_BALANCE + gas_price * gas_limit)

    tx = Transaction(
        ty=0,
        to=contract_address,
        gas_limit=gas_limit,
        gas_price=gas_price,
        sender=sender,
    )

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={slot_code_worked: value_code_worked}
            ),
            sender: Account(nonce=1),
        },
        blocks=[Block(txs=[tx])],
    )
