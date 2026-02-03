"""
Tests reserve balance when doing transfers.
"""

from enum import Enum, auto, unique

import pytest
from execution_testing import (
    AccessList,
    Account,
    Address,
    Alloc,
    AuthorizationTuple,
    Block,
    BlockchainTestFiller,
    Hash,
    Op,
    Transaction,
)
from execution_testing.forks.helpers import Fork
from execution_testing.test_types.helpers import compute_create_address
from execution_testing.tools.tools_code.generators import Initcode

from .helpers import (
    Stage1Balance,
    StageBalance,
    generous_gas,
)
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


def test_smoke_reserve_balance(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Simplest smoke test for checking if reserve balance is enforced.
    """
    initial_balance = 10 * 10**18
    sender = pre.fund_eoa(initial_balance, delegation=Address(0x0111))

    contract = Op.SSTORE(slot_code_worked, value_code_worked) + Op.STOP
    contract_address = pre.deploy_contract(contract)
    gas_limit = generous_gas(fork)
    gas_price = 10

    tx_1 = Transaction(
        gas_limit=gas_limit,
        gas_price=gas_price,
        to=contract_address,
        value=1,
        sender=sender,
    )

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(storage={}),
            sender: Account(balance=initial_balance - gas_price * gas_limit),
        },
        blocks=[Block(txs=[tx_1])],
    )


# Required constants to parametrize an control balances.
GAS_PRICE = 10
GAS_LIMIT = 500_000
TX_FEE = GAS_PRICE * GAS_LIMIT


@unique
class TargetAccountType(Enum):
    """Kinds of target accounts for calls."""

    EMPTY = auto()
    EOA = auto()
    DELEGATED_EOA = auto()
    LEGACY_CONTRACT = auto()
    IDENTITY_PRECOMPILE = auto()

    def __str__(self) -> str:
        """Return string representation of the enum."""
        return f"{self.name}"


@pytest.fixture
def target_address(
    pre: Alloc, target_account_type: TargetAccountType
) -> Address:
    """Target address of the call depending on required type of account."""
    match target_account_type:
        case TargetAccountType.EMPTY:
            return pre.fund_eoa(amount=0)
        case TargetAccountType.EOA:
            return pre.fund_eoa()
        case TargetAccountType.DELEGATED_EOA:
            return pre.fund_eoa(delegation=Address(0x0111))
        case TargetAccountType.LEGACY_CONTRACT:
            return pre.deploy_contract(code=Op.STOP)
        case TargetAccountType.IDENTITY_PRECOMPILE:
            return Address(0x04)


value_balance_violation_param_list = [
    pytest.param(
        0,
        Spec.RESERVE_BALANCE,
        False,
        id="zero_value",
    ),
    pytest.param(
        0,
        Spec.RESERVE_BALANCE - 1,
        False,
        id="zero_value_init_below_reserve",
    ),
    pytest.param(
        0,
        TX_FEE,
        False,
        id="zero_value_spend_all_on_gas",
    ),
    pytest.param(
        1,
        Spec.RESERVE_BALANCE - 1,
        True,
        id="non_zero_value_init_below_reserve",
    ),
    pytest.param(
        1,
        Spec.RESERVE_BALANCE,
        True,
        id="non_zero_value",
    ),
    pytest.param(
        1,
        Spec.RESERVE_BALANCE + 1,
        False,
        id="non_zero_value_good",
    ),
    pytest.param(
        Spec.RESERVE_BALANCE - TX_FEE,
        Spec.RESERVE_BALANCE,
        True,
        id="large_value_leave_zero",
    ),
    pytest.param(
        Spec.RESERVE_BALANCE - 1 - TX_FEE,
        Spec.RESERVE_BALANCE,
        True,
        id="large_value_leave_one",
    ),
    pytest.param(
        Spec.RESERVE_BALANCE + 1,
        2 * Spec.RESERVE_BALANCE,
        True,
        id="large_value_one_below",
    ),
    pytest.param(
        Spec.RESERVE_BALANCE,
        2 * Spec.RESERVE_BALANCE,
        False,
        id="large_value_good",
    ),
    pytest.param(
        Spec.RESERVE_BALANCE - TX_FEE,
        Spec.RESERVE_BALANCE,
        True,
        id="value_equal_to_balance_minus_gas",
    ),
    pytest.param(
        Spec.RESERVE_BALANCE,
        Spec.RESERVE_BALANCE + TX_FEE,
        True,
        id="balance_equal_to_value_plus_gas",
    ),
    pytest.param(
        10 * Spec.RESERVE_BALANCE,
        100 * Spec.RESERVE_BALANCE + TX_FEE,
        False,
        id="well_above_reserve",
    ),
    pytest.param(
        10 * Spec.RESERVE_BALANCE,
        2**256 - 1,
        False,
        id="well_above_reserve_maxed_balance",
    ),
    pytest.param(
        0,
        2**256 - 1,
        False,
        id="zero_maxed_balance",
    ),
    pytest.param(
        1,
        2**256 - 1,
        False,
        id="one_maxed_balance",
    ),
    pytest.param(
        2**256 - 1 - TX_FEE,
        2**256 - 1,
        True,
        id="maxed_out",
    ),
    pytest.param(
        2**256 - 1 - Spec.RESERVE_BALANCE,
        2**256 - 1,
        False,
        id="maxed_out_good",
    ),
    pytest.param(
        2**256 - 1 - Spec.RESERVE_BALANCE + 1,
        2**256 - 1,
        True,
        id="maxed_out_minimal_violation",
    ),
    pytest.param(
        2 * Spec.RESERVE_BALANCE,
        3 * Spec.RESERVE_BALANCE - 1,
        True,
        id="more_than_half_balance",
    ),
    pytest.param(
        2 * Spec.RESERVE_BALANCE,
        3 * Spec.RESERVE_BALANCE,
        False,
        id="more_than_half_balance_good",
    ),
]


@pytest.mark.parametrize(
    ["value", "balance", "violation"],
    value_balance_violation_param_list,
)
@pytest.mark.parametrize("pre_delegated", [True, False])
@pytest.mark.parametrize("delegate", [True, False])
@pytest.mark.parametrize("undelegate", [True, False])
def test_delegated_eoa_send_value(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    violation: bool,
    pre_delegated: bool,
    delegate: bool,
    undelegate: bool,
) -> None:
    """
    Test reserve balance violations for an EOA sending txs with various values.
    """
    target_address = Address(0x1111)
    if pre_delegated:
        sender = pre.fund_eoa(balance, delegation=target_address)
    else:
        sender = pre.fund_eoa(balance)

    authorization_list = []

    if delegate:
        authorization_list += [
            AuthorizationTuple(
                address=target_address,
                nonce=sender.nonce + 1,
                signer=sender,
            )
        ]
    if undelegate:
        authorization_list += [
            AuthorizationTuple(
                address=Address(0),
                nonce=(sender.nonce + 2 if delegate else sender.nonce + 1),
                signer=sender,
            )
        ]

    contract = Op.SSTORE(slot_code_worked, value_code_worked) + Op.STOP
    contract_address = pre.deploy_contract(contract)

    tx_1 = Transaction(
        gas_limit=GAS_LIMIT,
        max_fee_per_gas=GAS_PRICE,
        max_priority_fee_per_gas=GAS_PRICE,
        to=contract_address,
        value=value,
        sender=sender,
        authorization_list=authorization_list or None,
    )
    any_delegation = pre_delegated or delegate or undelegate
    reverted = violation and any_delegation
    storage = {} if reverted else {slot_code_worked: value_code_worked}
    balance = balance - (value if not reverted else 0) - TX_FEE

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage=storage, balance=value if not reverted else 0
            ),
            sender: Account(balance=balance),
        },
        blocks=[Block(txs=[tx_1])],
    )


@pytest.mark.parametrize(
    ["value", "balance", "violation"],
    value_balance_violation_param_list,
)
@pytest.mark.parametrize("pre_delegated", [True, False])
def test_sc_wallet_send_value(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    violation: bool,
    pre_delegated: bool,
    fork: Fork,
) -> None:
    """
    Test reserve balance violations for an EOA sending txs with various values
    using a CALL opcode in a smart contract wallet.
    """
    contract = Op.SSTORE(slot_code_worked, value_code_worked)
    contract_address = pre.deploy_contract(contract)

    wallet_address = pre.deploy_contract(
        code=Op.CALL(address=contract_address, value=value)
    )
    if pre_delegated:
        sender = pre.fund_eoa(balance, delegation=wallet_address)
        authorization_list = []
    else:
        sender = pre.fund_eoa(balance)
        authorization_list = [
            AuthorizationTuple(
                address=wallet_address,
                nonce=0,
                signer=sender,
            )
        ]

    tx_1 = Transaction(
        gas_limit=generous_gas(fork),
        to=sender,
        sender=pre.fund_eoa(),
        authorization_list=authorization_list or None,
    )
    reverted = violation
    storage = {} if reverted else {slot_code_worked: value_code_worked}
    balance = balance - (value if not reverted else 0)

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage=storage, balance=value if not reverted else 0
            ),
            sender: Account(balance=balance),
        },
        blocks=[Block(txs=[tx_1])],
    )


@pytest.mark.parametrize(
    ["value", "balance", "violation"],
    [
        pytest.param(0, 0, False, id="zero_value"),
        pytest.param(1, 1, True, id="one"),
        pytest.param(
            Spec.RESERVE_BALANCE,
            Spec.RESERVE_BALANCE,
            True,
            id="reserve_balance",
        ),
        pytest.param(
            2 * Spec.RESERVE_BALANCE,
            2 * Spec.RESERVE_BALANCE,
            True,
            id="reserve_balance2",
        ),
    ],
)
@pytest.mark.parametrize("pre_delegated", [True, False])
def test_sc_wallet_send_value_with_selfdestruct(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    violation: bool,
    pre_delegated: bool,
    fork: Fork,
) -> None:
    """
    Test reserve balance violations for an EOA sending by
    using a SELFDESTRUCT opcode in a smart contract wallet.
    """
    assert value == balance
    contract_address = pre.deploy_contract(
        Op.SSTORE(slot_code_worked, value_code_worked)
    )

    wallet_address = pre.deploy_contract(
        code=Op.CALL(address=contract_address)
        + Op.SELFDESTRUCT(address=contract_address)
    )
    if pre_delegated:
        sender = pre.fund_eoa(balance, delegation=wallet_address)
        authorization_list = []
    else:
        sender = pre.fund_eoa(balance)
        authorization_list = [
            AuthorizationTuple(
                address=wallet_address,
                nonce=0,
                signer=sender,
            )
        ]

    tx_1 = Transaction(
        gas_limit=generous_gas(fork),
        to=sender,
        sender=pre.fund_eoa(),
        authorization_list=authorization_list or None,
    )
    reverted = violation
    storage = {} if reverted else {slot_code_worked: value_code_worked}

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage=storage, balance=value if not reverted else 0
            ),
            sender: Account(balance=balance if reverted else 0),
        },
        blocks=[Block(txs=[tx_1])],
    )


@pytest.mark.parametrize(
    ["value", "balance", "violation"],
    [
        pytest.param(0, Spec.RESERVE_BALANCE, False, id="zero_value"),
        pytest.param(1, Spec.RESERVE_BALANCE, True, id="non_zero_value"),
        pytest.param(
            1, Spec.RESERVE_BALANCE + 1, False, id="non_zero_value_good"
        ),
    ],
)
@pytest.mark.parametrize("pre_delegated", [True, False])
@pytest.mark.parametrize("delegate", [True, False])
@pytest.mark.parametrize("undelegate", [True, False])
def test_sc_wallet_selfdestruct(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    violation: bool,
    pre_delegated: bool,
    delegate: bool,
    undelegate: bool,
    fork: Fork,
) -> None:
    """
    Test reserve balance violations for a delegated EOA whose wallet
    code SELFDESTRUCTs on behalf of the EOA.

    NOTE: this is different from test_sc_wallet_send_value_with_selfdestruct
    in that SELFDESTRUCT is not used to send value.
    """
    wallet_address = pre.deploy_contract(code=Op.SELFDESTRUCT(Op.ADDRESS))

    if pre_delegated:
        sender = pre.fund_eoa(balance, delegation=wallet_address)
    else:
        sender = pre.fund_eoa(balance)

    authorization_list = []
    if delegate:
        authorization_list.append(
            AuthorizationTuple(
                address=wallet_address,
                nonce=sender.nonce + 1,
                signer=sender,
            )
        )
    if undelegate:
        authorization_list.append(
            AuthorizationTuple(
                address=Address(0),
                nonce=sender.nonce + (2 if delegate else 1),
                signer=sender,
            )
        )

    contract_address = pre.deploy_contract(
        code=Op.SSTORE(slot_code_worked, value_code_worked)
        + Op.CALL(address=sender)
    )

    tx_1 = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        value=value,
        sender=sender,
        authorization_list=authorization_list or None,
    )

    any_delegation = pre_delegated or delegate or undelegate
    reverted = violation and any_delegation
    storage = {} if reverted else {slot_code_worked: value_code_worked}

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage=storage, balance=value if not reverted else 0
            )
        },
        blocks=[Block(txs=[tx_1])],
    )


@pytest.mark.parametrize(
    ["value", "balance", "violation"],
    [
        pytest.param(0, Spec.RESERVE_BALANCE, False, id="zero_value"),
        pytest.param(1, Spec.RESERVE_BALANCE, True, id="non_zero_value"),
        pytest.param(
            1, Spec.RESERVE_BALANCE + 1, False, id="non_zero_value_good"
        ),
    ],
)
@pytest.mark.parametrize("sponsor_pre_delegated", [True, False])
@pytest.mark.parametrize("sponsor_delegated", [True, False])
@pytest.mark.parametrize(
    ["sponsor_value", "sponsor_balance", "sponsor_violation"],
    [
        pytest.param(0, Spec.RESERVE_BALANCE, False, id="sponsor_zero_value"),
        pytest.param(
            1, Spec.RESERVE_BALANCE, True, id="sponsor_non_zero_value"
        ),
        pytest.param(
            1,
            Spec.RESERVE_BALANCE + 1,
            False,
            id="sponsor_non_zero_value_good",
        ),
    ],
)
@pytest.mark.parametrize("pre_delegated", [True, False])
def test_sc_wallet_send_value_various_sponsors(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    violation: bool,
    pre_delegated: bool,
    sponsor_value: int,
    sponsor_balance: int,
    sponsor_violation: bool,
    sponsor_pre_delegated: bool,
    sponsor_delegated: bool,
    fork: Fork,
) -> None:
    """
    Test reserve balance violations for an EOA sending txs with various values
    using a CALL opcode in a smart contract wallet.

    Includes edge cases where the sponsor (i.e. tx signer) is delegated
    or not and has various balances, to ensure it isn't the account
    being checked for reserve balance violation.
    """
    contract = Op.SSTORE(slot_code_worked, value_code_worked)
    contract_address = pre.deploy_contract(contract)

    wallet_address = pre.deploy_contract(
        code=Op.CALL(address=contract_address, value=value)
    )
    if pre_delegated:
        sender = pre.fund_eoa(balance, delegation=wallet_address)
        authorization_list = []
    else:
        sender = pre.fund_eoa(balance)
        authorization_list = [
            AuthorizationTuple(
                address=wallet_address,
                nonce=0,
                signer=sender,
            )
        ]

    sponsor = pre.fund_eoa(
        amount=sponsor_balance,
        delegation=Address(0x0111) if sponsor_pre_delegated else None,
    )
    if sponsor_delegated:
        authorization_list += [
            AuthorizationTuple(
                address=Address(0x0222),
                nonce=2 if sponsor_pre_delegated else 1,
                signer=sponsor,
            )
        ]

    # An intermediate to take sponsor_value over and NOT forward it
    # to the sender under test, in order to not impact its balance.
    caller_address = pre.deploy_contract(code=Op.CALL(address=sender))

    tx_1 = Transaction(
        gas_limit=generous_gas(fork),
        to=caller_address,
        sender=sponsor,
        value=sponsor_value,
        authorization_list=authorization_list or None,
    )
    reverted = violation or (
        sponsor_violation and (sponsor_pre_delegated or sponsor_delegated)
    )
    storage = {} if reverted else {slot_code_worked: value_code_worked}

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage=storage, balance=value if not reverted else 0
            ),
            caller_address: Account(
                balance=sponsor_value if not reverted else 0
            ),
            sender: Account(balance=balance - (value if not reverted else 0)),
        },
        blocks=[Block(txs=[tx_1])],
    )


@pytest.mark.parametrize(
    ["value1", "balance1", "violation1"],
    [
        pytest.param(0, Spec.RESERVE_BALANCE, False, id="zero_value1"),
        pytest.param(1, Spec.RESERVE_BALANCE, True, id="non_zero_value1"),
        pytest.param(
            1, Spec.RESERVE_BALANCE + 1, False, id="non_zero_value_good1"
        ),
    ],
)
@pytest.mark.parametrize(
    ["value2", "balance2", "violation2"],
    [
        pytest.param(0, Spec.RESERVE_BALANCE, False, id="zero_value2"),
        pytest.param(1, Spec.RESERVE_BALANCE, True, id="non_zero_value2"),
        pytest.param(
            1, Spec.RESERVE_BALANCE + 1, False, id="non_zero_value_good2"
        ),
    ],
)
@pytest.mark.parametrize("pre_delegated", [True, False])
def test_multiple_violating_senders(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value1: int,
    balance1: int,
    violation1: bool,
    value2: int,
    balance2: int,
    violation2: bool,
    pre_delegated: bool,
    fork: Fork,
) -> None:
    """
    Test reserve balance violations if there are two delegated EOAs spending in
    the transaction.
    """
    contract = Op.SSTORE(slot_code_worked, value_code_worked)
    contract_address = pre.deploy_contract(contract)

    wallet_address1 = pre.deploy_contract(
        code=Op.CALL(address=contract_address, value=value1)
    )
    wallet_address2 = pre.deploy_contract(
        code=Op.CALL(address=contract_address, value=value2)
    )
    if pre_delegated:
        sender1 = pre.fund_eoa(balance1, delegation=wallet_address1)
        sender2 = pre.fund_eoa(balance2, delegation=wallet_address2)
        authorization_list = []
    else:
        sender1 = pre.fund_eoa(balance1)
        sender2 = pre.fund_eoa(balance2)
        authorization_list = [
            AuthorizationTuple(
                address=wallet_address1,
                nonce=0,
                signer=sender1,
            ),
            AuthorizationTuple(
                address=wallet_address2,
                nonce=0,
                signer=sender2,
            ),
        ]
    dispatcher = pre.deploy_contract(
        Op.CALL(address=sender1) + Op.CALL(address=sender2)
    )

    tx_1 = Transaction(
        gas_limit=generous_gas(fork),
        to=dispatcher,
        sender=pre.fund_eoa(),
        authorization_list=authorization_list or None,
    )
    reverted = violation1 or violation2
    storage = {} if reverted else {slot_code_worked: value_code_worked}
    balance1 = balance1 - (value1 if not reverted else 0)
    balance2 = balance2 - (value2 if not reverted else 0)

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage=storage, balance=value1 + value2 if not reverted else 0
            ),
            sender1: Account(balance=balance1),
            sender2: Account(balance=balance2),
        },
        blocks=[Block(txs=[tx_1])],
    )


@pytest.mark.parametrize(
    ["value", "balance", "violation"],
    [
        pytest.param(0, Spec.RESERVE_BALANCE, False, id="zero_value"),
        pytest.param(1, Spec.RESERVE_BALANCE, True, id="non_zero_value"),
        pytest.param(
            1, Spec.RESERVE_BALANCE + 1, False, id="non_zero_value_good"
        ),
    ],
)
@pytest.mark.parametrize("pre_delegated", [True, False])
@pytest.mark.parametrize("target_account_type", TargetAccountType)
def test_delegated_various_targets(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    violation: bool,
    pre_delegated: bool,
    target_address: Address,
    fork: Fork,
) -> None:
    """
    Test reserve balance violations for an EOA delegated to various kinds of
    targets.
    """
    if pre_delegated:
        sender = pre.fund_eoa(balance, delegation=target_address)
    else:
        sender = pre.fund_eoa(balance)

    contract = Op.SSTORE(slot_code_worked, value_code_worked) + Op.STOP
    contract_address = pre.deploy_contract(contract)

    tx_1 = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        value=value,
        sender=sender,
    )
    reverted = violation and pre_delegated
    storage = {} if reverted else {slot_code_worked: value_code_worked}

    blockchain_test(
        pre=pre,
        post={contract_address: Account(storage=storage)},
        blocks=[Block(txs=[tx_1])],
    )


@pytest.mark.parametrize(
    ["value", "balance", "violation"],
    [
        pytest.param(0, Spec.RESERVE_BALANCE, False, id="zero_value"),
        pytest.param(1, Spec.RESERVE_BALANCE, True, id="non_zero_value"),
        pytest.param(
            1, Spec.RESERVE_BALANCE + 1, False, id="non_zero_value_good"
        ),
        pytest.param(
            2 * Spec.RESERVE_BALANCE,
            3 * Spec.RESERVE_BALANCE - 1,
            True,
            id="more_than_half_balance",
        ),
        pytest.param(
            2 * Spec.RESERVE_BALANCE,
            3 * Spec.RESERVE_BALANCE,
            False,
            id="more_than_half_balance_good",
        ),
    ],
)
@pytest.mark.parametrize("pre_delegated", [True, False])
@pytest.mark.parametrize("pre_funded", [True, False, "half"])
def test_credit_same_tx(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    violation: bool,
    pre_delegated: bool,
    pre_funded: bool | str,
    fork: Fork,
) -> None:
    """
    Test reserve balance violations for an EOA credited during the otherwise
    violating tx.
    """
    contract = Op.SSTORE(slot_code_worked, value_code_worked)
    contract_address = pre.deploy_contract(contract)

    wallet_address = pre.deploy_contract(
        code=Op.CALL(address=contract_address, value=value)
    )
    pre_funded_balance = (
        balance // 2 if pre_funded == "half" else balance if pre_funded else 0
    )
    if pre_delegated:
        sender = pre.fund_eoa(pre_funded_balance, delegation=wallet_address)
        authorization_list = []
    else:
        sender = pre.fund_eoa(pre_funded_balance)
        authorization_list = [
            AuthorizationTuple(
                address=wallet_address,
                nonce=0,
                signer=sender,
            )
        ]
    same_tx_funded_balance = balance - pre_funded_balance
    tx_1 = Transaction(
        gas_limit=generous_gas(fork),
        to=sender,
        sender=pre.fund_eoa(),
        value=same_tx_funded_balance,
        authorization_list=authorization_list or None,
    )
    # If tx_1 net transfer is incoming there is no revert even if balance ends
    # below reserve
    reverted = violation and value > same_tx_funded_balance
    storage = {} if reverted else {slot_code_worked: value_code_worked}

    expected_balance = (
        # Start from fully funded balance if tx went through
        balance
        if not reverted
        # Start from pre funded portion if it reverted but was half pre-funded
        else pre_funded_balance
        if pre_funded == "half"
        # Start from fully funded balance if was fully pre-funded
        else balance
        if pre_funded
        # Start from zero if wasn't pre-funded at all
        else 0
        # Subtract transfer value if it went through
    ) - (value if not reverted else 0)

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage=storage, balance=value if not reverted else 0
            ),
            sender: Account(balance=expected_balance),
        },
        blocks=[Block(txs=[tx_1])],
    )


@pytest.mark.parametrize(
    ["value", "balance", "violation"],
    [
        pytest.param(0, Spec.RESERVE_BALANCE, False, id="zero_value"),
        pytest.param(1, Spec.RESERVE_BALANCE, True, id="non_zero_value"),
        pytest.param(
            1, Spec.RESERVE_BALANCE + 1, False, id="non_zero_value_good"
        ),
    ],
)
@pytest.mark.parametrize("pre_delegated", [True, False])
@pytest.mark.parametrize("credit_value", [None, 0, 1])
def test_credit_in_same_tx_same_call_frame(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    violation: bool,
    pre_delegated: bool,
    credit_value: int | None,
    fork: Fork,
) -> None:
    """
    Test reserve balance violations for an EOA credited during the otherwise
    violating tx, within the frame that debited the EOA.
    """
    if pre_delegated:
        sender = pre.fund_eoa(balance, delegation=Address(0x1111))
    else:
        sender = pre.fund_eoa(balance)

    contract = Op.SSTORE(slot_code_worked, value_code_worked)

    if credit_value is not None:
        creditor = pre.deploy_contract(
            Op.CALL(address=sender, value=credit_value),
            balance=credit_value,
        )
        contract += Op.CALL(address=creditor)
    contract_address = pre.deploy_contract(contract)

    tx_1 = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        value=value,
        sender=sender,
    )
    reverted = violation and pre_delegated and not credit_value
    storage = {} if reverted else {slot_code_worked: value_code_worked}

    blockchain_test(
        pre=pre,
        post={contract_address: Account(storage=storage)},
        blocks=[Block(txs=[tx_1])],
    )


@pytest.mark.parametrize(
    ["value", "balance", "violation"],
    [
        pytest.param(0, Spec.RESERVE_BALANCE, False, id="zero_value"),
        pytest.param(1, Spec.RESERVE_BALANCE, True, id="non_zero_value"),
        pytest.param(
            1, Spec.RESERVE_BALANCE + 1, False, id="non_zero_value_good"
        ),
    ],
)
@pytest.mark.parametrize("credit_value", [None, 0, 1])
def test_credit_after_call_frame(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    violation: bool,
    credit_value: int | None,
    fork: Fork,
) -> None:
    """
    Test reserve balance violations for an EOA credited after the spending
    (violating) call frame exits - ensures the reserve balance check is done
    per tx not per call frame.
    """
    contract = Op.SSTORE(slot_code_worked, value_code_worked)
    contract_address = pre.deploy_contract(contract)
    sender = pre.fund_eoa(balance)

    wallet = Op.CALL(address=contract_address, value=value)
    if credit_value is not None:
        creditor = pre.deploy_contract(
            # Use SELFDESTRUCT in order to avoid CALL and an infinite loop but
            # still send all value
            Op.SELFDESTRUCT(address=sender),
            balance=credit_value,
        )
        wallet += Op.CALL(address=creditor)
    wallet_address = pre.deploy_contract(code=wallet)

    authorization_list = [
        AuthorizationTuple(address=wallet_address, nonce=0, signer=sender)
    ]

    tx_1 = Transaction(
        gas_limit=generous_gas(fork),
        to=sender,
        sender=pre.fund_eoa(),
        authorization_list=authorization_list,
    )
    reverted = violation and not credit_value
    storage = {} if reverted else {slot_code_worked: value_code_worked}

    blockchain_test(
        pre=pre,
        post={contract_address: Account(storage=storage)},
        blocks=[Block(txs=[tx_1])],
    )


@pytest.mark.parametrize(
    ["value", "balance", "violation"],
    [
        pytest.param(0, Spec.RESERVE_BALANCE, False, id="zero_value"),
        pytest.param(1, Spec.RESERVE_BALANCE, True, id="non_zero_value"),
        pytest.param(
            1, Spec.RESERVE_BALANCE + 1, False, id="non_zero_value_good"
        ),
    ],
)
def test_credit_with_transaction_fee(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    violation: bool,
    fork: Fork,
) -> None:
    """
    Test reserve balance violations for an EOA credited by getting a block
    reward (doesn't count as reward comes after check).
    """
    contract = Op.SSTORE(slot_code_worked, value_code_worked)
    contract_address = pre.deploy_contract(contract)
    sender = pre.fund_eoa(balance)

    wallet = Op.CALL(address=contract_address, value=value)
    wallet_address = pre.deploy_contract(code=wallet)

    authorization_list = [
        AuthorizationTuple(address=wallet_address, nonce=0, signer=sender)
    ]
    gas_limit = generous_gas(fork)
    # TODO: assumed - how would I extract it?
    gas_price = 10
    base_fee_per_gas = 7
    priority_gas_price = gas_price - base_fee_per_gas
    reward = gas_limit * priority_gas_price

    tx_1 = Transaction(
        gas_limit=gas_limit,
        max_fee_per_gas=gas_price,
        max_priority_fee_per_gas=gas_price,
        to=sender,
        sender=pre.fund_eoa(),
        authorization_list=authorization_list,
    )
    reverted = violation
    storage = {} if reverted else {slot_code_worked: value_code_worked}

    post = {
        contract_address: Account(
            storage=storage, balance=value if not reverted else 0
        ),
        sender: Account(
            balance=(balance - value + reward)
            if not reverted
            else balance + reward
        ),
    }

    blockchain_test(
        pre=pre,
        post=post,
        blocks=[Block(txs=[tx_1], fee_recipient=sender)],
    )


@pytest.mark.parametrize(
    ["value", "balance", "violation"],
    [
        pytest.param(0, Spec.RESERVE_BALANCE, False, id="zero_value"),
        pytest.param(1, Spec.RESERVE_BALANCE, True, id="non_zero_value"),
        pytest.param(
            1, Spec.RESERVE_BALANCE + 1, False, id="non_zero_value_good"
        ),
    ],
)
@pytest.mark.parametrize("pre_delegated", [True, False])
@pytest.mark.parametrize("access_lists", [True, False])
def test_access_lists(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    violation: bool,
    pre_delegated: bool,
    access_lists: bool,
    fork: Fork,
) -> None:
    """
    Test reserve balance violations for a tx with access lists which should
    never affect reserve balance.
    """
    if pre_delegated:
        sender = pre.fund_eoa(balance, delegation=Address(0x1111))
    else:
        sender = pre.fund_eoa(balance)

    contract = Op.SSTORE(slot_code_worked, value_code_worked) + Op.STOP
    contract_address = pre.deploy_contract(contract)

    access_list_items = None
    if access_lists:
        access_list_items = [
            AccessList(address=sender, storage_keys=[Hash(0)]),
            AccessList(
                address=contract_address, storage_keys=[slot_code_worked]
            ),
        ]

    tx_1 = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        value=value,
        sender=sender,
        access_list=access_list_items,
    )
    reverted = violation and pre_delegated
    storage = {} if reverted else {slot_code_worked: value_code_worked}

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(storage=storage),
        },
        blocks=[Block(txs=[tx_1])],
    )


@pytest.mark.parametrize(
    ["value", "balance", "violation"],
    [
        pytest.param(0, Spec.RESERVE_BALANCE, False, id="zero_value"),
        pytest.param(1, Spec.RESERVE_BALANCE, True, id="non_zero_value"),
        pytest.param(
            1, Spec.RESERVE_BALANCE + 1, False, id="non_zero_value_good"
        ),
    ],
)
@pytest.mark.parametrize("pre_delegated", [True, False])
@pytest.mark.parametrize("new_address_pre_funded", [True, False])
@pytest.mark.parametrize("selfdestruct", [True, False])
@pytest.mark.with_all_contract_creating_tx_types
def test_creation_tx(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    violation: bool,
    pre_delegated: bool,
    new_address_pre_funded: bool,
    selfdestruct: bool,
    tx_type: int,
    fork: Fork,
) -> None:
    """
    Test reserve balance violations for creation txs to: null.
    """
    pre_fund_value = 0
    if pre_delegated:
        sender = pre.fund_eoa(balance, delegation=Address(0x1111))
    else:
        sender = pre.fund_eoa(balance)

    selfdestruct_target = Address(0x5656)
    initcode = (
        Op.SELFDESTRUCT(address=selfdestruct_target)
        if selfdestruct
        else Initcode(deploy_code=Op.STOP)
    )

    tx_1 = Transaction(
        gas_limit=generous_gas(fork),
        to=None,
        ty=tx_type,
        value=value,
        sender=sender,
        data=initcode,
    )
    new_address = tx_1.created_contract

    txs = [tx_1]
    if new_address_pre_funded:
        pre_fund_value = 12345
        txs.insert(
            0,
            Transaction(
                to=new_address, value=pre_fund_value, sender=pre.fund_eoa()
            ),
        )

    reverted = violation and pre_delegated

    blockchain_test(
        pre=pre,
        post={
            new_address: Account(
                code=Op.STOP,
                balance=value + pre_fund_value,
            )
            if not reverted and not selfdestruct
            else Account(balance=pre_fund_value)
            if new_address_pre_funded and reverted
            else None,
            selfdestruct_target: Account(balance=value + pre_fund_value)
            if selfdestruct and not reverted and value + pre_fund_value != 0
            else None,
        },
        blocks=[Block(txs=txs)],
    )


@pytest.mark.parametrize(
    ["value", "balance"],
    [
        pytest.param(0, Spec.RESERVE_BALANCE, id="zero_value"),
        pytest.param(1, Spec.RESERVE_BALANCE, id="non_zero_value"),
        pytest.param(1, Spec.RESERVE_BALANCE + 1, id="non_zero_value_good"),
    ],
)
@pytest.mark.parametrize("pre_delegated", [True, False])
@pytest.mark.parametrize("pre_funded", [True, False])
def test_contract_unrestricted(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    pre_delegated: bool,
    pre_funded: bool,
    fork: Fork,
) -> None:
    """
    Test reserve balance never affects contract spends.
    """
    transfer_destination = Address(0x1121)
    if pre_delegated:
        sender = pre.fund_eoa(
            Spec.RESERVE_BALANCE + balance, delegation=Address(0x1111)
        )
    else:
        sender = pre.fund_eoa(Spec.RESERVE_BALANCE + balance)

    caller = (
        Op.CALL(value=value, address=transfer_destination)
        + Op.SSTORE(slot_code_worked, value_code_worked)
        + Op.STOP
    )
    caller_address = pre.deploy_contract(
        caller, balance=balance if pre_funded else 0
    )

    tx_1 = Transaction(
        gas_limit=generous_gas(fork),
        to=caller_address,
        value=balance if not pre_funded else 0,
        sender=sender,
    )
    storage = {slot_code_worked: value_code_worked}

    blockchain_test(
        pre=pre,
        post={
            caller_address: Account(storage=storage, balance=balance - value),
            transfer_destination: Account(balance=value)
            if value != 0
            else None,
        },
        blocks=[Block(txs=[tx_1])],
    )


@pytest.mark.parametrize(
    ["value", "balance"],
    [
        pytest.param(0, Spec.RESERVE_BALANCE, id="zero_value"),
        pytest.param(1, Spec.RESERVE_BALANCE, id="non_zero_value"),
        pytest.param(1, Spec.RESERVE_BALANCE + 1, id="non_zero_value_good"),
    ],
)
@pytest.mark.parametrize("pre_delegated", [True, False])
@pytest.mark.parametrize("pre_funded", [True, False])
@pytest.mark.parametrize("selfdestruct", [True, False])
@pytest.mark.with_all_create_opcodes
def test_contract_unrestricted_with_create(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    pre_delegated: bool,
    pre_funded: bool,
    selfdestruct: bool,
    create_opcode: Op,
    fork: Fork,
) -> None:
    """
    Test reserve balance never affects contract spends done with a
    create opcode.
    """
    if pre_delegated:
        sender = pre.fund_eoa(
            Spec.RESERVE_BALANCE + balance, delegation=Address(0x1111)
        )
    else:
        sender = pre.fund_eoa(Spec.RESERVE_BALANCE + balance)

    selfdestruct_target = Address(0x5656)

    initcode = (
        Op.SELFDESTRUCT(address=selfdestruct_target)
        if selfdestruct
        else Initcode(deploy_code=Op.STOP)
    )
    initcode_bytes = initcode + b"\x00" * (32 - (len(initcode) % 32))

    factory = (
        Op.MSTORE(0, Op.PUSH32(bytes(initcode_bytes)))
        + create_opcode(value=value, size=len(initcode))
        + Op.SSTORE(slot_code_worked, value_code_worked)
        + Op.STOP
    )
    factory_address = pre.deploy_contract(
        factory, balance=balance if pre_funded else 0
    )

    new_contract_address = compute_create_address(
        address=factory_address,
        nonce=1,
        initcode=initcode,
        opcode=create_opcode,
    )

    tx_1 = Transaction(
        gas_limit=generous_gas(fork),
        to=factory_address,
        value=balance if not pre_funded else 0,
        sender=sender,
    )
    storage = {slot_code_worked: value_code_worked}

    blockchain_test(
        pre=pre,
        post={
            factory_address: Account(storage=storage, balance=balance - value),
            new_contract_address: Account(balance=value, code=Op.STOP)
            if not selfdestruct
            else None,
            selfdestruct_target: Account(balance=value)
            if selfdestruct and value != 0
            else None,
        },
        blocks=[Block(txs=[tx_1])],
    )


@pytest.mark.parametrize(
    ["value", "balance"],
    [
        pytest.param(0, Spec.RESERVE_BALANCE, id="zero_value"),
        pytest.param(1, Spec.RESERVE_BALANCE, id="non_zero_value"),
        pytest.param(1, Spec.RESERVE_BALANCE + 1, id="non_zero_value_good"),
    ],
)
@pytest.mark.parametrize("pre_delegated", [True, False])
@pytest.mark.with_all_create_opcodes
@pytest.mark.parametrize("new_address_pre_funded", [True, False])
@pytest.mark.parametrize("selfdestruct", [True, False])
def test_contract_unrestricted_within_initcode(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    pre_delegated: bool,
    create_opcode: Op,
    new_address_pre_funded: bool,
    selfdestruct: bool,
    fork: Fork,
) -> None:
    """
    Test reserve balance never affects contract spends done from initcode
    context.
    """
    if pre_delegated:
        sender = pre.fund_eoa(
            Spec.RESERVE_BALANCE + balance, delegation=Address(0x1111)
        )
    else:
        sender = pre.fund_eoa(Spec.RESERVE_BALANCE + balance)

    target = Address(0x1231)
    selfdestruct_target = Address(0x5656)

    initcode = (
        (
            Op.CALL(value=value, address=target)
            + Op.SELFDESTRUCT(address=selfdestruct_target)
        )
        if selfdestruct
        else Initcode(
            initcode_prefix=Op.CALL(value=value, address=target),
            deploy_code=Op.STOP,
        )
    )
    initcode_bytes = initcode + b"\x00" * (32 - (len(initcode) % 32))

    factory = (
        Op.MSTORE(0, Op.PUSH32(bytes(initcode_bytes)))
        + create_opcode(
            value=balance if not new_address_pre_funded else 0,
            size=len(initcode),
        )
        + Op.SSTORE(slot_code_worked, value_code_worked)
        + Op.STOP
    )
    factory_address = pre.deploy_contract(
        factory, balance=balance if not new_address_pre_funded else 0
    )

    new_contract_address = compute_create_address(
        address=factory_address,
        nonce=1,
        initcode=initcode,
        opcode=create_opcode,
    )

    tx_1 = Transaction(
        gas_limit=generous_gas(fork),
        to=factory_address,
        sender=sender,
    )
    storage = {slot_code_worked: value_code_worked}

    txs = [tx_1]
    if new_address_pre_funded:
        txs.insert(
            0,
            Transaction(
                to=new_contract_address, value=balance, sender=pre.fund_eoa()
            ),
        )

    blockchain_test(
        pre=pre,
        post={
            factory_address: Account(storage=storage),
            new_contract_address: Account(
                balance=balance - value, code=Op.STOP
            )
            if not selfdestruct
            else None,
            target: Account(balance=value) if value != 0 else None,
            selfdestruct_target: Account(balance=balance - value)
            if selfdestruct
            else None,
        },
        blocks=[Block(txs=txs)],
    )


@pytest.mark.parametrize(
    ["value", "balance"],
    [
        pytest.param(0, Spec.RESERVE_BALANCE, id="zero_value"),
        pytest.param(1, Spec.RESERVE_BALANCE, id="non_zero_value"),
        pytest.param(1, Spec.RESERVE_BALANCE + 1, id="non_zero_value_good"),
    ],
)
@pytest.mark.parametrize("pre_delegated", [True, False])
@pytest.mark.parametrize("new_address_pre_funded", [True, False])
@pytest.mark.parametrize("selfdestruct", [True, False])
@pytest.mark.with_all_contract_creating_tx_types
def test_unrestricted_in_creation_tx_initcode(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    pre_delegated: bool,
    new_address_pre_funded: bool,
    selfdestruct: bool,
    tx_type: int,
    fork: Fork,
) -> None:
    """
    Test reserve balance never affects contract spends done from initcode
    context created via a creation tx with to: null.
    """
    if pre_delegated:
        sender = pre.fund_eoa(
            Spec.RESERVE_BALANCE + balance, delegation=Address(0x1111)
        )
    else:
        sender = pre.fund_eoa(Spec.RESERVE_BALANCE + balance)

    target = Address(0x1231)
    selfdestruct_target = Address(0x5656)

    initcode = (
        (
            Op.CALL(value=value, address=target)
            + Op.SELFDESTRUCT(address=selfdestruct_target)
        )
        if selfdestruct
        else Initcode(
            initcode_prefix=Op.CALL(value=value, address=target),
            deploy_code=Op.STOP,
        )
    )

    tx_1 = Transaction(
        gas_limit=generous_gas(fork),
        to=None,
        ty=tx_type,
        value=balance if not new_address_pre_funded else 0,
        sender=sender,
        data=initcode,
    )
    new_address = tx_1.created_contract

    txs = [tx_1]
    if new_address_pre_funded:
        txs.insert(
            0,
            Transaction(to=new_address, value=balance, sender=pre.fund_eoa()),
        )

    blockchain_test(
        pre=pre,
        post={
            new_address: Account(code=Op.STOP, balance=balance - value)
            if not selfdestruct
            else None,
            target: Account(balance=value) if value != 0 else None,
            selfdestruct_target: Account(balance=balance - value)
            if selfdestruct
            else None,
        },
        blocks=[Block(txs=txs)],
    )


@pytest.mark.parametrize("stage1", Stage1Balance)
@pytest.mark.parametrize("stage2", StageBalance)
@pytest.mark.parametrize("stage3", StageBalance)
def test_two_step_balance_change(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
    stage1: Stage1Balance,
    stage2: StageBalance,
    stage3: StageBalance,
) -> None:
    """
    Test reserve balance rules when a delegated account's balance changes
    in 2 steps.

    The test verifies that a transaction reverts if and only if:
    A) The balance decreased from Stage 1 to Stage 3 (final < initial)
    B) The balance at Stage 3 is below reserve balance

    Both conditions must be true for the transaction to revert.
    """
    balance1 = stage1.compute_balance()
    balance2 = stage2.compute_balance([balance1])
    balance3 = stage3.compute_balance([balance1, balance2])

    delta1 = balance2 - balance1
    delta2 = balance3 - balance2

    sink = Address(0x5111)

    wallet_code = Op.CALL(address=sink, value=Op.CALLDATALOAD(0))
    wallet_address = pre.deploy_contract(code=wallet_code)

    sender = pre.fund_eoa(balance1, delegation=wallet_address)

    contract_code = Op.SSTORE(slot_code_worked, value_code_worked)

    if delta1 <= 0:
        contract_code += Op.MSTORE(0, -delta1)
        contract_code += Op.CALL(address=sender, args_size=32)
    elif delta1 > 0:
        funder1 = pre.deploy_contract(
            code=Op.SELFDESTRUCT(sender),
            balance=delta1,
        )
        contract_code += Op.CALL(address=funder1)

    if delta2 <= 0:
        contract_code += Op.MSTORE(0, -delta2)
        contract_code += Op.CALL(address=sender, args_size=32)
    elif delta2 > 0:
        funder2 = pre.deploy_contract(
            code=Op.SELFDESTRUCT(sender),
            balance=delta2,
        )
        contract_code += Op.CALL(address=funder2)

    contract_address = pre.deploy_contract(contract_code)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=sender,
    )

    balance_decreased = balance3 < balance1
    final_below_reserve = balance3 < Spec.RESERVE_BALANCE

    if balance_decreased and final_below_reserve:
        storage = {}
    else:
        storage = {slot_code_worked: value_code_worked}

    blockchain_test(
        pre=pre,
        post={contract_address: Account(storage=storage)},
        blocks=[Block(txs=[tx])],
    )
