"""
Tests for reserve balance precompile with transfers.

These tests verify that the reserve balance precompile correctly returns
1 when execution is in reserve balance violation and 0 otherwise.
"""

from enum import Enum, auto, unique
from typing import Callable

import pytest
from execution_testing import (
    AccessList,
    Account,
    Address,
    Alloc,
    AuthorizationTuple,
    Block,
    BlockchainTestFiller,
    Op,
    Transaction,
)
from execution_testing.forks.helpers import Fork
from execution_testing.test_types.helpers import compute_create_address
from execution_testing.tools.tools_code.generators import Initcode
from execution_testing.vm.bytecode import Bytecode

from ...monad_eight.reserve_balance.helpers import (
    Stage1Balance,
    StageBalance,
)
from .helpers import RefillFactory, call_dipped_into_reserve, generous_gas
from .spec import Spec, ref_spec_mip4

REFERENCE_SPEC_GIT_PATH = ref_spec_mip4.git_path
REFERENCE_SPEC_VERSION = ref_spec_mip4.version

slot_code_worked = 0x1
value_code_worked = 0x1234
slot_violation_result = 0x2

slot_violation_after_stage2 = 0x12
slot_violation_after_stage3 = 0x13

pytestmark = [
    pytest.mark.valid_from("MONAD_NINE"),
    pytest.mark.pre_alloc_group(
        "mip4_checkreservebalance_tests",
        reason="Tests reserve balance precompile",
    ),
]


GAS_PRICE = 10
GAS_LIMIT = 500_000
TX_FEE = GAS_PRICE * GAS_LIMIT


def test_smoke_checkreservebalance(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    refill_factory: RefillFactory,
    fork: Fork,
) -> None:
    """
    Simplest smoke test for reserve balance precompile.
    """
    refill_call = refill_factory()
    initial_balance = 10 * 10**18
    sender = pre.fund_eoa(initial_balance, delegation=Address(0x0111))

    contract = (
        Op.SSTORE(slot_violation_result, call_dipped_into_reserve())
        + refill_call(Op.ORIGIN)
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    contract_address = pre.deploy_contract(contract)

    tx_1 = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        value=1,
        sender=sender,
    )

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_violation_result: 1,
                    slot_code_worked: value_code_worked,
                }
            ),
        },
        blocks=[Block(txs=[tx_1])],
    )


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
    refill_factory: RefillFactory,
    value: int,
    balance: int,
    violation: bool,
    pre_delegated: bool,
    delegate: bool,
    undelegate: bool,
) -> None:
    """
    Test dippedIntoReserve() returns correct value for an EOA sending txs.
    """
    refill_call = refill_factory()
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

    contract = Op.SSTORE(slot_code_worked, value_code_worked) + Op.SSTORE(
        slot_violation_result, call_dipped_into_reserve()
    )

    # avoid overflow for cases where refill goes above max
    if balance - value + Spec.RESERVE_BALANCE < 2**256:
        contract += refill_call(Op.ORIGIN)
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
    expected_violation = 1 if (violation and any_delegation) else 0

    storage = {
        slot_violation_result: expected_violation,
        slot_code_worked: value_code_worked,
    }

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(storage=storage),
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
    refill_factory: RefillFactory,
    value: int,
    balance: int,
    violation: bool,
    pre_delegated: bool,
    fork: Fork,
) -> None:
    """
    Test dippedIntoReserve() for an EOA sending txs with various values
    using a CALL opcode in a smart contract wallet.
    """
    refill_call = refill_factory()
    contract = Op.SSTORE(slot_code_worked, value_code_worked) + Op.SSTORE(
        slot_violation_result, call_dipped_into_reserve()
    )
    contract_address = pre.deploy_contract(contract)

    wallet_code = Op.CALL(address=contract_address, value=value)

    # avoid overflow for cases where refill goes above max
    if balance - value + Spec.RESERVE_BALANCE < 2**256:
        wallet_code += refill_call(Op.ADDRESS)
    wallet_address = pre.deploy_contract(code=wallet_code)

    if pre_delegated:
        sender = pre.fund_eoa(balance, delegation=wallet_address)
        authorization_list = []
    else:
        sender = pre.fund_eoa(balance)
        authorization_list = [
            AuthorizationTuple(address=wallet_address, nonce=0, signer=sender)
        ]

    tx_1 = Transaction(
        gas_limit=generous_gas(fork),
        to=sender,
        sender=pre.fund_eoa(),
        authorization_list=authorization_list or None,
    )

    expected_violation = 1 if violation else 0
    storage = {
        slot_violation_result: expected_violation,
        slot_code_worked: value_code_worked,
    }

    blockchain_test(
        pre=pre,
        post={contract_address: Account(storage=storage, balance=value)},
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
@pytest.mark.parametrize("selfdestruct_to_self", [True, False])
def test_sc_wallet_selfdestruct(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    refill_factory: RefillFactory,
    value: int,
    balance: int,
    violation: bool,
    pre_delegated: bool,
    delegate: bool,
    undelegate: bool,
    selfdestruct_to_self: bool,
    fork: Fork,
) -> None:
    """
    Test dippedIntoReserve() for a delegated EOA whose wallet code
    SELFDESTRUCTs on behalf of the EOA.

    Naming the EOA itself moves no value, so the reserve balance is only
    at stake when the beneficiary is another account.
    """
    refill_call = refill_factory()
    selfdestruct_target = Address(0x5656)
    wallet_address = pre.deploy_contract(
        code=Op.SELFDESTRUCT(
            Op.ADDRESS if selfdestruct_to_self else selfdestruct_target
        )
    )

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
        + Op.SSTORE(slot_violation_result, call_dipped_into_reserve())
        + refill_call(Op.ORIGIN)
    )

    tx_1 = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        value=value,
        sender=sender,
        authorization_list=authorization_list or None,
    )

    any_delegation = pre_delegated or delegate or undelegate
    # The authorizations apply before execution, so an undelegation
    # leaves nothing for the call to run.
    wallet_runs = (pre_delegated or delegate) and not undelegate
    # Sweeping the sender to another account empties a delegated EOA,
    # which the probe reports whatever the transaction's own value does.
    expected_violation = (
        1
        if (violation and any_delegation)
        or (wallet_runs and not selfdestruct_to_self)
        else 0
    )

    storage = {
        slot_violation_result: expected_violation,
        slot_code_worked: value_code_worked,
    }

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(storage=storage, balance=value),
            # What the sweep sends depends on the gas billed, so only
            # its presence is pinned.
            selfdestruct_target: Account()
            if wallet_runs and not selfdestruct_to_self
            else None,
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
@pytest.mark.parametrize("revert_subcall1", [True, False])
@pytest.mark.parametrize("revert_subcall2", [True, False])
def test_multiple_violating_senders(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    refill_factory: RefillFactory,
    value1: int,
    balance1: int,
    violation1: bool,
    value2: int,
    balance2: int,
    violation2: bool,
    pre_delegated: bool,
    revert_subcall1: bool,
    revert_subcall2: bool,
    fork: Fork,
) -> None:
    """
    Test dippedIntoReserve() when two delegated EOAs are spending in the tx.

    Also check dippedIntoReserve() called by different accounts.

    When a subcall reverts, its reserve balance violation is rolled back,
    allowing following call frames to proceed independently. This covers
    the essential usecase of the dippedIntoReserve() function.
    """
    contract = Op.SSTORE(slot_code_worked, value_code_worked) + Op.SSTORE(
        Op.CALLER, call_dipped_into_reserve()
    )
    contract_address = pre.deploy_contract(contract)

    # Build wallet code for each sender: spend value, check violation,
    # then either revert (rolling back the violation) or refill.
    wallet_code1 = Op.CALL(address=contract_address, value=value1) + Op.SSTORE(
        slot_violation_result, call_dipped_into_reserve()
    )
    if revert_subcall1:
        wallet_code1 += Op.REVERT(0, 0)
    else:
        wallet_code1 += refill_factory()(Op.ADDRESS)

    wallet_code2 = Op.CALL(address=contract_address, value=value2) + Op.SSTORE(
        slot_violation_result, call_dipped_into_reserve()
    )
    if revert_subcall2:
        wallet_code2 += Op.REVERT(0, 0)
    else:
        wallet_code2 += refill_factory()(Op.ADDRESS)

    wallet_address1 = pre.deploy_contract(code=wallet_code1)
    wallet_address2 = pre.deploy_contract(code=wallet_code2)

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

    expected_violation1 = 1 if violation1 else 0
    expected_violation2 = 1 if violation2 else 0

    contract_storage: dict = {}
    if not revert_subcall1 or not revert_subcall2:
        contract_storage[slot_code_worked] = value_code_worked
    if not revert_subcall1:
        contract_storage[sender1] = expected_violation1
    if not revert_subcall2:
        contract_storage[sender2] = expected_violation2

    contract_balance = (value1 if not revert_subcall1 else 0) + (
        value2 if not revert_subcall2 else 0
    )

    blockchain_test(
        pre=pre,
        post={
            sender1: Account(
                storage={slot_violation_result: expected_violation1}
                if not revert_subcall1
                else {}
            ),
            sender2: Account(
                storage={slot_violation_result: expected_violation2}
                if not revert_subcall2
                else {}
            ),
            contract_address: Account(
                storage=contract_storage,
                balance=contract_balance,
            ),
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
@pytest.mark.parametrize(
    "target_account_type",
    [
        TargetAccountType.EMPTY,
        TargetAccountType.EOA,
        TargetAccountType.DELEGATED_EOA,
        TargetAccountType.LEGACY_CONTRACT,
        TargetAccountType.IDENTITY_PRECOMPILE,
    ],
)
def test_delegated_various_targets(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    refill_factory: RefillFactory,
    value: int,
    balance: int,
    violation: bool,
    pre_delegated: bool,
    target_address: Address,
    fork: Fork,
) -> None:
    """
    Test dippedIntoReserve() for an EOA delegated to various kinds of targets.
    """
    refill_call = refill_factory()
    if pre_delegated:
        sender = pre.fund_eoa(balance, delegation=target_address)
    else:
        sender = pre.fund_eoa(balance)

    contract = (
        Op.SSTORE(slot_violation_result, call_dipped_into_reserve())
        + refill_call(Op.ORIGIN)
        + Op.SSTORE(slot_code_worked, value_code_worked)
        + Op.STOP
    )
    contract_address = pre.deploy_contract(contract)

    tx_1 = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        value=value,
        sender=sender,
    )

    expected_violation = 1 if (violation and pre_delegated) else 0

    storage = {
        slot_violation_result: expected_violation,
        slot_code_worked: value_code_worked,
    }

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
    refill_factory: RefillFactory,
    value: int,
    balance: int,
    violation: bool,
    pre_delegated: bool,
    pre_funded: bool | str,
    fork: Fork,
) -> None:
    """
    Test dippedIntoReserve() for an EOA credited during the otherwise
    violating tx.
    """
    refill_call = refill_factory()
    contract = Op.SSTORE(slot_code_worked, value_code_worked) + Op.SSTORE(
        slot_violation_result, call_dipped_into_reserve()
    )
    contract_address = pre.deploy_contract(contract)

    wallet_address = pre.deploy_contract(
        code=Op.CALL(address=contract_address, value=value)
        + refill_call(Op.ADDRESS)
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
    expected_violation = (
        1 if violation and value > same_tx_funded_balance else 0
    )
    storage = {
        slot_violation_result: expected_violation,
        slot_code_worked: value_code_worked,
    }

    blockchain_test(
        pre=pre,
        post={contract_address: Account(storage=storage, balance=value)},
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
    refill_factory: RefillFactory,
    value: int,
    balance: int,
    violation: bool,
    pre_delegated: bool,
    credit_value: int | None,
    fork: Fork,
) -> None:
    """
    Test dippedIntoReserve() for an EOA credited during the otherwise
    violating tx, within the frame that debited the EOA.

    dippedIntoReserve() is called AFTER creditor (if any) so it reflects
    the state after credit has been applied.
    """
    refill_call = refill_factory()
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
    contract += Op.SSTORE(
        slot_violation_result, call_dipped_into_reserve()
    ) + refill_call(Op.ORIGIN)
    contract_address = pre.deploy_contract(contract)

    tx_1 = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        value=value,
        sender=sender,
    )

    expected_violation = (
        1 if (violation and pre_delegated and not credit_value) else 0
    )

    storage = {
        slot_violation_result: expected_violation,
        slot_code_worked: value_code_worked,
    }

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
    refill_factory: RefillFactory,
    value: int,
    balance: int,
    violation: bool,
    credit_value: int | None,
    fork: Fork,
) -> None:
    """
    Test dippedIntoReserve() for an EOA credited after the spending
    (violating) call frame exits.
    """
    refill_call = refill_factory()
    contract = Op.SSTORE(slot_code_worked, value_code_worked) + Op.SSTORE(
        slot_violation_result, call_dipped_into_reserve()
    )
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
    wallet += Op.SSTORE(
        slot_violation_result, call_dipped_into_reserve()
    ) + refill_call(Op.ADDRESS)
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
    first_result = 1 if violation else 0
    # Second result is written after crediting.
    second_result = 1 if violation and not credit_value else 0

    contract_storage = {
        slot_violation_result: first_result,
        slot_code_worked: value_code_worked,
    }
    wallet_storage = {slot_violation_result: second_result}
    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(storage=contract_storage),
            sender: Account(storage=wallet_storage),
        },
        blocks=[Block(txs=[tx_1])],
    )


# NOTE: test_credit_with_transaction_fee from reserve_balance tests does not
# apply for dippedIntoReserve()
# NOTE: test_access_lists from reserve_balance tests does not
# apply for dippedIntoReserve()


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
@pytest.mark.with_all_contract_creating_tx_types
def test_creation_tx(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    refill_factory: RefillFactory,
    value: int,
    balance: int,
    violation: bool,
    pre_delegated: bool,
    tx_type: int,
    fork: Fork,
) -> None:
    """
    Test reserve balance violations for creation txs to: null.
    """
    refill_call = refill_factory()
    pre_fund_value = 0
    if pre_delegated:
        sender = pre.fund_eoa(balance, delegation=Address(0x1111))
    else:
        sender = pre.fund_eoa(balance)

    initcode = Initcode(
        deploy_code=Op.STOP,
        initcode_prefix=Op.SSTORE(
            slot_violation_result, call_dipped_into_reserve()
        )
        + refill_call(Op.ORIGIN),
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

    expected_violation = 1 if (violation and pre_delegated) else 0

    blockchain_test(
        pre=pre,
        post={
            new_address: Account(
                code=Op.STOP,
                balance=value + pre_fund_value,
                storage={slot_violation_result: expected_violation},
            )
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
    Test reserve balance never affects contract spends and
    dippedIntoReserve() always returns 0.
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
        + Op.SSTORE(slot_violation_result, call_dipped_into_reserve())
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
    storage = {slot_code_worked: value_code_worked, slot_violation_result: 0}

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
@pytest.mark.parametrize(
    "selfdestruct,selfdestruct_to_self",
    [(True, False), (True, True), (False, False)],
)
@pytest.mark.with_all_create_opcodes
def test_contract_unrestricted_with_create(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    pre_delegated: bool,
    pre_funded: bool,
    selfdestruct: bool,
    selfdestruct_to_self: bool,
    create_opcode: Op,
    fork: Fork,
) -> None:
    """
    Test reserve balance never affects contract spends done with a
    create opcode, and dippedIntoReserve() respects this.
    """
    if pre_delegated:
        sender = pre.fund_eoa(
            Spec.RESERVE_BALANCE + balance, delegation=Address(0x1111)
        )
    else:
        sender = pre.fund_eoa(Spec.RESERVE_BALANCE + balance)

    selfdestruct_target = Address(0x5656)

    initcode = (
        Op.SELFDESTRUCT(
            address=Op.ADDRESS if selfdestruct_to_self else selfdestruct_target
        )
        if selfdestruct
        else Initcode(deploy_code=Op.STOP)
    )
    initcode_bytes = initcode + b"\x00" * (32 - (len(initcode) % 32))

    factory = (
        Op.MSTORE(0, Op.PUSH32(bytes(initcode_bytes)))
        + create_opcode(value=value, size=len(initcode))
        + Op.SSTORE(slot_code_worked, value_code_worked)
        + Op.SSTORE(slot_violation_result, call_dipped_into_reserve())
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
    storage = {slot_code_worked: value_code_worked, slot_violation_result: 0}

    # A SELFDESTRUCT to the destructing account itself moves nothing, so
    # what it leaves behind is what EIP-8246 changes: before it, the
    # balance is burnt and the account destroyed; after it, the account
    # is cleared and keeps the balance it retained. A zero balance still
    # leaves the account empty, and so pruned.
    selfdestructed_account = (
        Account(nonce=0, balance=value, code=b"", storage={})
        if selfdestruct_to_self and fork.is_eip_enabled(8246) and value != 0
        else None
    )

    blockchain_test(
        pre=pre,
        post={
            factory_address: Account(storage=storage, balance=balance - value),
            new_contract_address: Account(balance=value, code=Op.STOP)
            if not selfdestruct
            else selfdestructed_account,
            selfdestruct_target: Account(balance=value)
            if selfdestruct and value != 0 and not selfdestruct_to_self
            else None,
        },
        blocks=[Block(txs=[tx_1])],
    )


@pytest.mark.parametrize("prefund_balance", [0, Spec.RESERVE_BALANCE // 2])
@pytest.mark.parametrize("create_balance", [0, Spec.RESERVE_BALANCE // 2])
@pytest.mark.parametrize("call_balance", [0, Spec.RESERVE_BALANCE // 2])
@pytest.mark.parametrize("pull_balance", [0, Spec.RESERVE_BALANCE // 2])
@pytest.mark.parametrize(
    "same_tx,through_delegation,selfdestruct_to_self",
    [
        # Only a same-transaction creation reaches EIP-8246's branch, and
        # a delegated frame destructs the delegating account rather than
        # the created one, so the self target varies in one case alone.
        (True, False, False),
        (True, False, True),
        (True, True, False),
        (False, False, False),
        (False, True, False),
    ],
)
@pytest.mark.with_all_create_opcodes
def test_contract_unrestricted_with_selfdestruct(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    refill_factory: RefillFactory,
    # Balance available to spender in previous transaction
    prefund_balance: int,
    # Balance available to spender in create
    create_balance: int,
    # Balance available to spender in selfdestruct call
    call_balance: int,
    # Balance the spender pulls in the selfdestruct call
    pull_balance: int,
    # Whether the selfdestructing call happens in the same tx
    # as the creation (c.f. EIP-6780)
    same_tx: bool,
    # Whether the SELFDESTRUCT should be called on behalf of
    # a delegating account
    through_delegation: bool,
    # Whether SELFDESTRUCT names the destructing account itself
    selfdestruct_to_self: bool,
    create_opcode: Op,
    fork: Fork,
) -> None:
    """
    Test dippedIntoReserve() for contract spends done with a selfdestruct
    opcode, including when called on behalf of a delegating EOA.

    We allow the selfdestructing contract to be funded in various
    stages of the execution.
    """
    refill_call = refill_factory() if through_delegation else None

    value = prefund_balance + call_balance + pull_balance
    delegated_address = pre.fund_eoa(amount=0)

    if through_delegation:
        # If we're delegating to the selfdestructing account,
        # the endowment given at creation will not be included
        # in the SELFDESTRUCT transfer.
        pass
    else:
        value += create_balance

    selfdestruct_target = Address(0x5656)
    pull_funder_address = pre.deploy_contract(
        Op.SELFDESTRUCT(address=Op.CALLER), balance=pull_balance
    )

    deploy_code = Op.CALL(address=pull_funder_address) + Op.SELFDESTRUCT(
        address=Op.ADDRESS if selfdestruct_to_self else selfdestruct_target
    )
    initcode = Initcode(deploy_code=deploy_code)

    new_address_offset = 0
    initcode_offset = 32

    factory = (
        Op.SSTORE(slot_code_worked, value_code_worked)
        + Op.CALLDATACOPY(initcode_offset, 0, len(initcode))
        # create new contract and store its address to later call it
        + Op.MSTORE(
            new_address_offset,
            create_opcode(
                value=create_balance,
                offset=initcode_offset,
                size=len(initcode),
            ),
        )
    )
    if same_tx:
        factory += Op.CALL(
            address=delegated_address
            if through_delegation
            else Op.MLOAD(new_address_offset),
            value=call_balance,
        )
        factory += Op.SSTORE(slot_violation_result, call_dipped_into_reserve())
        if refill_call is not None:
            factory += refill_call(delegated_address)

    factory_address = pre.deploy_contract(
        factory,
        balance=create_balance + (call_balance if same_tx else 0),
    )

    new_contract_address = compute_create_address(
        address=factory_address,
        nonce=1,
        initcode=initcode,
        opcode=create_opcode,
    )

    txs = []
    if prefund_balance > 0:
        txs.append(
            Transaction(
                to=delegated_address
                if through_delegation
                else new_contract_address,
                value=prefund_balance,
                sender=pre.fund_eoa(),
            ),
        )

    # The creating transaction. If same_tx is also the test tx.
    txs.append(
        Transaction(
            gas_limit=generous_gas(fork),
            to=factory_address,
            sender=pre.fund_eoa(),
            data=initcode,
            authorization_list=[
                AuthorizationTuple(
                    address=new_contract_address,
                    nonce=0,
                    signer=delegated_address,
                )
            ]
            if through_delegation
            else None,
        )
    )

    # Intermediate contract which calls into the tested account to trigger
    # its selfdestruct and persist dipped_into_reserve result.
    caller_code = Op.CALL(
        address=delegated_address
        if through_delegation
        else new_contract_address,
        value=call_balance,
    ) + Op.SSTORE(slot_violation_result, call_dipped_into_reserve())
    if refill_call is not None:
        caller_code += refill_call(delegated_address)
    caller_address = pre.deploy_contract(caller_code, balance=call_balance)

    if not same_tx:
        # A separate test tx follows the creating tx.
        txs.append(
            Transaction(
                gas_limit=generous_gas(fork),
                to=caller_address,
                sender=pre.fund_eoa(),
            )
        )

    expected_violation = (
        1 if through_delegation and value > 0 and prefund_balance > 0 else 0
    )

    factory_storage = {slot_code_worked: value_code_worked}
    if same_tx:
        factory_storage[slot_violation_result] = expected_violation

    # A SELFDESTRUCT to the destructing account itself moves nothing, so
    # what it leaves behind is what EIP-8246 changes: before it, the
    # balance is burnt and the account destroyed; after it, the account
    # is cleared and keeps the balance it retained.
    selfdestructed_account = (
        Account(nonce=0, balance=value, code=b"", storage={})
        if selfdestruct_to_self and fork.is_eip_enabled(8246) and value != 0
        else None
    )

    post = {
        # Factory is the caller to store result if same_tx
        # Factory is always left with no balance.
        factory_address: Account(storage=factory_storage, balance=0),
        # caller_address is the caller to store result if not same_tx
        caller_address: Account(
            storage={slot_violation_result: expected_violation}
            if not same_tx
            else {}
        ),
        # Deployed contract will remain if
        #  - destructs not in same tx (EIP-6780)
        #  - it destructs the delegating account
        new_contract_address: Account(
            balance=create_balance if through_delegation else 0,
            code=deploy_code,
        )
        if not same_tx or through_delegation
        else selfdestructed_account,
        # SELFDESTRUCT target is deleted if source was empty
        selfdestruct_target: Account(balance=value)
        if value != 0 and not selfdestruct_to_self
        else None,
    }

    blockchain_test(
        pre=pre,
        post=post,
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
@pytest.mark.with_all_create_opcodes
@pytest.mark.parametrize("new_address_pre_funded", [True, False])
@pytest.mark.parametrize(
    "selfdestruct,selfdestruct_to_self,deploy_code",
    [
        (True, False, None),
        (True, True, None),
        (False, False, Bytecode()),
        (False, False, Op.STOP),
    ],
)
def test_contract_unrestricted_within_initcode(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    refill_factory: RefillFactory,
    value: int,
    balance: int,
    create_opcode: Op,
    new_address_pre_funded: bool,
    selfdestruct: bool,
    selfdestruct_to_self: bool,
    deploy_code: Bytecode | None,
    fork: Fork,
) -> None:
    """
    Test dippedIntoReserve() for contract spends done from initcode
    context via CREATE/CREATE2.

    Checks dippedIntoReserve() twice:
    1. During initcode, right after the spend (via checker)
    2. After initcode exits, in the factory (after CREATE)
    """
    assert selfdestruct == (deploy_code is None)
    refill_call = refill_factory()

    sender = pre.fund_eoa(Spec.RESERVE_BALANCE + balance)

    target = Address(0x1231)
    selfdestruct_target = Address(0x5656)

    # Auxiliary contract: persists dippedIntoReserve() result
    # during initcode (first check).
    checker = pre.deploy_contract(
        Op.SSTORE(slot_violation_result, call_dipped_into_reserve())
    )

    common_initcode = Op.CALL(value=value, address=target) + Op.CALL(
        address=checker
    )
    if selfdestruct:
        initcode = common_initcode + Op.SELFDESTRUCT(
            address=Op.ADDRESS if selfdestruct_to_self else selfdestruct_target
        )
    else:
        initcode = Initcode(
            initcode_prefix=common_initcode,
            deploy_code=deploy_code,
        )
    initcode_size = len(initcode)

    # Factory: copy initcode from calldata into memory, CREATE,
    # then second dippedIntoReserve() check after initcode
    # exits, then refill to prevent end-of-tx revert.
    # Save CREATE result at memory[initcode_size] so
    # refill_call can read it back via MLOAD.
    factory = (
        Op.CALLDATACOPY(0, 0, initcode_size)
        + Op.MSTORE(
            initcode_size,
            create_opcode(
                value=balance if not new_address_pre_funded else 0,
                size=initcode_size,
            ),
        )
        + Op.SSTORE(slot_code_worked, value_code_worked)
        + Op.SSTORE(slot_violation_result, call_dipped_into_reserve())
        + refill_call(Op.MLOAD(initcode_size))
    )
    factory_address = pre.deploy_contract(
        factory,
        balance=balance if not new_address_pre_funded else 0,
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
        data=initcode,
    )

    # First check (checker, during initcode): code=b"" on the new contract, so
    # violation whenever pre-funded balance dips below RESERVE_BALANCE.
    expected_violation_in_initcode = (
        1
        if (new_address_pre_funded and balance - value < Spec.RESERVE_BALANCE)
        else 0
    )

    # Second check (factory, after CREATE): code is now set or explicitly
    # selfdestructed. In case code is set to empty, the reserve balance
    # violation is still expected.
    expected_violation_after_create = (
        1
        if (
            deploy_code is not None
            and len(deploy_code) == 0
            and new_address_pre_funded
            and balance - value < Spec.RESERVE_BALANCE
        )
        else 0
    )

    new_balance = balance - value + Spec.RESERVE_BALANCE

    # A SELFDESTRUCT to the destructing account itself moves nothing, so
    # what it leaves behind is what EIP-8246 changes: before it, the
    # balance is burnt and the account destroyed; after it, the account
    # is cleared and keeps both the retained balance and the refill.
    retained = balance - value if selfdestruct_to_self else 0
    selfdestructed_account = (
        Account(
            nonce=0,
            balance=retained + Spec.RESERVE_BALANCE,
            code=b"",
            storage={},
        )
        if fork.is_eip_enabled(8246)
        else None
    )

    txs = [tx_1]
    if new_address_pre_funded:
        txs.insert(
            0,
            Transaction(
                to=new_contract_address,
                value=balance,
                sender=pre.fund_eoa(),
            ),
        )

    blockchain_test(
        pre=pre,
        post={
            checker: Account(
                storage={
                    slot_violation_result: expected_violation_in_initcode,
                }
            ),
            factory_address: Account(
                storage={
                    slot_code_worked: value_code_worked,
                    slot_violation_result: expected_violation_after_create,
                }
            ),
            new_contract_address: Account(
                balance=new_balance, code=deploy_code
            )
            if not selfdestruct
            else selfdestructed_account,
            target: Account(balance=value) if value != 0 else None,
            # SELFDESTRUCT runs during initcode (before factory
            # refill), so it sends balance - value only.
            selfdestruct_target: Account(balance=balance - value)
            if selfdestruct and not selfdestruct_to_self
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
@pytest.mark.parametrize("new_address_pre_funded", [True, False])
@pytest.mark.parametrize(
    "selfdestruct,selfdestruct_to_self,deploy_code",
    [
        (True, False, None),
        (True, True, None),
        (False, False, Bytecode()),
        (False, False, Op.STOP),
    ],
)
@pytest.mark.with_all_contract_creating_tx_types
def test_unrestricted_in_creation_tx_initcode(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    refill_factory: RefillFactory,
    value: int,
    balance: int,
    new_address_pre_funded: bool,
    selfdestruct: bool,
    selfdestruct_to_self: bool,
    deploy_code: Bytecode | None,
    tx_type: int,
    fork: Fork,
) -> None:
    """
    Test dippedIntoReserve() for contract spends done from initcode
    context created via a creation tx with to: null.

    Checks dippedIntoReserve() once during initcode via checker.
    No second check is possible (no code runs after initcode in
    a creation tx context).
    """
    assert selfdestruct == (deploy_code is None)
    refill_call = refill_factory()

    sender = pre.fund_eoa(Spec.RESERVE_BALANCE + balance)
    target = Address(0x1231)
    selfdestruct_target = Address(0x5656)

    # Auxiliary contract: persists dippedIntoReserve() result
    # during initcode.
    checker = pre.deploy_contract(
        Op.SSTORE(slot_violation_result, call_dipped_into_reserve())
    )

    # code=b"" on the new contract, so violation whenever pre-funded balance
    # dips below RESERVE_BALANCE.
    expected_violation_in_initcode = (
        1
        if (new_address_pre_funded and balance - value < Spec.RESERVE_BALANCE)
        else 0
    )

    common_initcode = (
        Op.CALL(value=value, address=target)
        + Op.CALL(address=checker)
        + refill_call(Op.ADDRESS)
    )
    if selfdestruct:
        initcode = common_initcode + Op.SELFDESTRUCT(
            address=Op.ADDRESS if selfdestruct_to_self else selfdestruct_target
        )
    else:
        initcode = Initcode(
            initcode_prefix=common_initcode,
            deploy_code=deploy_code,
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
            Transaction(
                to=new_address,
                value=balance,
                sender=pre.fund_eoa(),
            ),
        )

    new_balance = balance - value + Spec.RESERVE_BALANCE

    # A SELFDESTRUCT to the destructing account itself moves nothing, so
    # what it leaves behind is what EIP-8246 changes: before it, the
    # balance is burnt and the account destroyed; after it, the account
    # is cleared and keeps the balance it retained.
    selfdestructed_account = (
        Account(nonce=0, balance=new_balance, code=b"", storage={})
        if selfdestruct_to_self and fork.is_eip_enabled(8246)
        else None
    )

    blockchain_test(
        pre=pre,
        post={
            checker: Account(
                storage={
                    slot_violation_result: expected_violation_in_initcode,
                }
            ),
            new_address: Account(code=deploy_code, balance=new_balance)
            if not selfdestruct
            else selfdestructed_account,
            target: Account(balance=value) if value != 0 else None,
            selfdestruct_target: Account(balance=new_balance)
            if selfdestruct and not selfdestruct_to_self
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
    refill_factory: RefillFactory,
    fork: Fork,
    stage1: Stage1Balance,
    stage2: StageBalance,
    stage3: StageBalance,
) -> None:
    """
    Test dippedIntoReserve() when a delegated account's balance changes
    in 2 steps.
    """
    refill_call = refill_factory()

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

    contract_code += Op.SSTORE(
        slot_violation_after_stage2, call_dipped_into_reserve()
    )

    if delta2 <= 0:
        contract_code += Op.MSTORE(0, -delta2)
        contract_code += Op.CALL(address=sender, args_size=32)
    elif delta2 > 0:
        funder2 = pre.deploy_contract(
            code=Op.SELFDESTRUCT(sender),
            balance=delta2,
        )
        contract_code += Op.CALL(address=funder2)

    contract_code += Op.SSTORE(
        slot_violation_after_stage3, call_dipped_into_reserve()
    ) + refill_call(sender)

    contract_address = pre.deploy_contract(contract_code)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=sender,
    )

    violation_after_stage2 = (
        balance2 < balance1 and balance2 < Spec.RESERVE_BALANCE
    )
    violation_after_stage3 = (
        balance3 < balance1 and balance3 < Spec.RESERVE_BALANCE
    )

    storage = {
        slot_code_worked: value_code_worked,
        slot_violation_after_stage2: 1 if violation_after_stage2 else 0,
        slot_violation_after_stage3: 1 if violation_after_stage3 else 0,
    }

    blockchain_test(
        pre=pre,
        post={contract_address: Account(storage=storage)},
        blocks=[Block(txs=[tx])],
    )


@pytest.mark.parametrize(
    "violation_index_fn",
    [
        pytest.param(lambda _n: None, id="no_violation"),
        pytest.param(lambda _n: 0, id="first_violates"),
        pytest.param(lambda n: n // 2, id="middle_violates"),
        pytest.param(lambda n: n - 1, id="last_violates"),
    ],
)
def test_many_accounts_balance_change(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    refill_factory: RefillFactory,
    violation_index_fn: Callable[[int], int | None],
    fork: Fork,
) -> None:
    """
    Test dippedIntoReserve() with many accounts having their balance changed.

    A single wallet is deployed and many EOAs delegate to it. Each EOA sends
    a transfer when the wallet code executes. The violation_index parameter
    determines which account (if any) ends up in violation.

    The number of accounts to involve depends on how cheaply can we call them
    and on the transaction gas limit cap.
    """
    gas_costs = fork.gas_costs()
    gas_per_account = (
        Op.CALL(
            # Warmed using access lists for cheapest call
            address_warm=True,
            value_transfer=True,
            account_new=False,
            delegated_address=True,
            # Warmed using access lists for cheapest call
            delegated_address_warm=True,
        ).gas_cost(fork)
        + gas_costs.TX_ACCESS_LIST_ADDRESS
    )
    gas_limit = fork.transaction_gas_limit_cap()
    assert gas_limit is not None
    # Using generous_gas(fork) as margin for constant gas expenses.
    num_accounts = (gas_limit - generous_gas(fork)) // gas_per_account
    assert (
        num_accounts >= 2550
    )  # 2570 minus margin for refill/precompile overhead
    violation_index = violation_index_fn(num_accounts)

    refill_call = refill_factory()
    value = 1

    initial_sink_balance = 1
    sink_address = pre.fund_eoa(initial_sink_balance)
    wallet_code = Op.CALL(address=sink_address, value=value)
    wallet_address = pre.deploy_contract(code=wallet_code)

    senders = []
    for i in range(num_accounts):
        if i == violation_index:
            balance = Spec.RESERVE_BALANCE
        else:
            balance = Spec.RESERVE_BALANCE + value
        senders.append(pre.fund_eoa(balance, delegation=wallet_address))

    contract_code = Op.SSTORE(slot_code_worked, value_code_worked)
    for sender in senders:
        contract_code += Op.CALL(address=sender) + Op.POP
    contract_code += Op.SSTORE(
        slot_violation_result, call_dipped_into_reserve()
    )
    if violation_index is not None:
        contract_code += refill_call(senders[violation_index])
    contract_address = pre.deploy_contract(contract_code)

    tx = Transaction(
        gas_limit=gas_limit,
        to=contract_address,
        sender=pre.fund_eoa(),
        access_list=[AccessList(address=s, storage_keys=[]) for s in senders]
        + [AccessList(address=wallet_address, storage_keys=[])],
    )

    expected_violation = 1 if violation_index is not None else 0
    total_sent = value * num_accounts

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_code_worked: value_code_worked,
                    slot_violation_result: expected_violation,
                }
            ),
            sink_address: Account(balance=initial_sink_balance + total_sent),
        },
        blocks=[Block(txs=[tx])],
    )
