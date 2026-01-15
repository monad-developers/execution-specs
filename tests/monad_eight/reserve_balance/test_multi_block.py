"""
Tests reserve balance rules spanning blocks.
"""

from typing import Tuple

import pytest
from execution_testing import (
    Account,
    Address,
    Alloc,
    AuthorizationTuple,
    Block,
    BlockchainTestFiller,
    Op,
    Transaction,
)
from execution_testing.exceptions.exceptions import TransactionException
from execution_testing.forks.helpers import Fork

from .helpers import generous_gas
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

GAS_PRICE = 10


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
    "delegate_pos", [None, (0, 0), (0, 1), (1, 0), (2, 1), (3, 0), (3, 1)]
)
@pytest.mark.parametrize(
    "undelegate_pos", [None, (0, 0), (0, 1), (1, 0), (2, 1), (3, 0), (3, 1)]
)
@pytest.mark.parametrize(
    "send_pos", [None, (0, 0), (0, 1), (1, 0), (2, 1), (3, 0), (3, 1)]
)
def test_exception_rule(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    violation: bool,
    pre_delegated: bool,
    delegate_pos: Tuple[int, int] | None,
    undelegate_pos: Tuple[int, int] | None,
    send_pos: Tuple[int, int] | None,
    fork: Fork,
) -> None:
    """
    Test reserve balance violations for an EOA sending txs with vaious values,
    where the exception rules are enforced based on txs in various block
    positions.
    """
    # gas spend by transactions send in setup blocks
    prepare_tx_gas = (
        fork.gas_costs().G_TRANSACTION + fork.gas_costs().G_AUTHORIZATION * 2
    )
    # if any of the transactions in setup blocks are sent by main sender we
    # need to credit them extra
    prepare_tx_fee = GAS_PRICE * prepare_tx_gas if send_pos else 0
    balance += prepare_tx_fee

    target_address = Address(0x1111)
    if pre_delegated:
        test_sender = pre.fund_eoa(balance, delegation=target_address)
    else:
        test_sender = pre.fund_eoa(balance)

    contract = Op.SSTORE(slot_code_worked, value_code_worked) + Op.STOP
    contract_address = pre.deploy_contract(contract)

    nblocks = 4
    blocks = []
    test_sender_nonce = test_sender.nonce
    latest_delegated_block = nblocks if pre_delegated else -1
    for nblock in range(nblocks):
        txs = []
        for ntx in range(2):
            authorization_list = []
            pos = (nblock, ntx)

            if send_pos == pos:
                nonce = test_sender_nonce
                sender = test_sender
                test_sender_nonce += 1
            else:
                nonce = 0
                sender = pre.fund_eoa()

            if delegate_pos == pos:
                authorization_list += [
                    AuthorizationTuple(
                        address=target_address,
                        nonce=test_sender_nonce,
                        signer=test_sender,
                    )
                ]
                test_sender_nonce += 1
                latest_delegated_block = nblocks
            if undelegate_pos == pos:
                authorization_list += [
                    AuthorizationTuple(
                        address=Address(0),
                        nonce=test_sender_nonce,
                        signer=test_sender,
                    )
                ]
                test_sender_nonce += 1
                latest_delegated_block = nblock
            prepare_tx = Transaction(
                gas_limit=prepare_tx_gas,
                max_fee_per_gas=GAS_PRICE,
                max_priority_fee_per_gas=GAS_PRICE,
                to=Address(0x7873),
                nonce=nonce,
                sender=sender,
                authorization_list=authorization_list or None,
            )
            txs.append(prepare_tx)
        # If this isn't the last block, we're done.
        # If it is, we'll append the test tx below.
        if nblock < nblocks - 1:
            blocks.append(Block(txs=txs))
            del txs

    test_tx = Transaction(
        gas_limit=generous_gas(fork),
        max_fee_per_gas=GAS_PRICE,
        max_priority_fee_per_gas=GAS_PRICE,
        to=contract_address,
        nonce=test_sender_nonce,
        value=value,
        sender=test_sender,
        authorization_list=authorization_list or None,
    )
    txs.append(test_tx)
    blocks.append(Block(txs=txs))

    any_delegation = latest_delegated_block > 0
    reverted = violation and (any_delegation or (send_pos and send_pos[0] > 0))
    storage = {} if reverted else {slot_code_worked: value_code_worked}

    blockchain_test(
        pre=pre,
        post={contract_address: Account(storage=storage)},
        blocks=blocks,
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
@pytest.mark.parametrize("delegate_pos", [None, (0, 0)])
@pytest.mark.parametrize("undelegate_pos", [None, (0, 0)])
@pytest.mark.parametrize("send_pos", [None, (0, 0)])
@pytest.mark.parametrize(
    "invalid_block",
    [pytest.param(True, marks=[pytest.mark.exception_test]), False],
)
@pytest.mark.skip(reason="Invalid blocks not supported in Monad")
def test_exception_rule_invalid_block(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    violation: bool,
    pre_delegated: bool,
    delegate_pos: Tuple[int, int] | None,
    undelegate_pos: Tuple[int, int] | None,
    send_pos: Tuple[int, int] | None,
    invalid_block: bool,
    fork: Fork,
) -> None:
    """
    Test reserve balance violations for an EOA sending txs with vaious values,
    where the exception rules are not enforced based on txs in invalid block.
    """
    # gas spend by transactions send in setup blocks
    prepare_tx_gas = (
        fork.gas_costs().G_TRANSACTION + fork.gas_costs().G_AUTHORIZATION * 2
    )
    # if any of the transactions in setup blocks are sent by main sender we
    # need to credit them extra
    prepare_tx_fee = (
        GAS_PRICE * prepare_tx_gas if send_pos and not invalid_block else 0
    )
    balance += prepare_tx_fee

    target_address = Address(0x1111)
    if pre_delegated:
        test_sender = pre.fund_eoa(balance, delegation=target_address)
    else:
        test_sender = pre.fund_eoa(balance)

    contract = Op.SSTORE(slot_code_worked, value_code_worked) + Op.STOP
    contract_address = pre.deploy_contract(contract)

    nblocks = 2
    blocks = []
    test_sender_nonce = test_sender.nonce
    for nblock in range(nblocks):
        txs = []
        for ntx in range(2):
            authorization_list = []
            pos = (nblock, ntx)

            if send_pos == pos:
                nonce = test_sender_nonce
                sender = test_sender
                if not invalid_block:
                    test_sender_nonce += 1
            else:
                nonce = 0
                sender = pre.fund_eoa()

            if delegate_pos == pos:
                authorization_list += [
                    AuthorizationTuple(
                        address=target_address,
                        nonce=test_sender_nonce,
                        signer=test_sender,
                    )
                ]
                if not invalid_block:
                    test_sender_nonce += 1
            if undelegate_pos == pos:
                authorization_list += [
                    AuthorizationTuple(
                        address=Address(0),
                        nonce=test_sender_nonce,
                        signer=test_sender,
                    )
                ]
                if not invalid_block:
                    test_sender_nonce += 1
            prepare_tx = Transaction(
                gas_limit=prepare_tx_gas,
                max_fee_per_gas=GAS_PRICE,
                max_priority_fee_per_gas=GAS_PRICE,
                to=Address(0x7873),
                nonce=nonce,
                sender=sender,
                authorization_list=authorization_list or None,
            )
            txs.append(prepare_tx)
        if nblock == 0 and invalid_block:
            txs.append(
                Transaction(
                    gas_limit=fork.gas_costs().G_TRANSACTION - 123,
                    to=Address(0x7676),
                    sender=pre.fund_eoa(),
                    error=TransactionException.INTRINSIC_GAS_TOO_LOW
                    if invalid_block
                    else None,
                )
            )
        # If this isn't the last block, we're done.
        # If it is, we'll append the test tx below.
        if nblock < nblocks - 1:
            blocks.append(
                Block(
                    txs=txs,
                    exception=TransactionException.INTRINSIC_GAS_TOO_LOW
                    if invalid_block
                    else None,
                )
            )
            del txs

    test_tx = Transaction(
        gas_limit=generous_gas(fork),
        max_fee_per_gas=GAS_PRICE,
        max_priority_fee_per_gas=GAS_PRICE,
        to=contract_address,
        nonce=test_sender_nonce,
        value=value,
        sender=test_sender,
        authorization_list=authorization_list or None,
    )
    txs.append(test_tx)
    blocks.append(Block(txs=txs))

    any_delegation = pre_delegated or (
        (delegate_pos or undelegate_pos) and not invalid_block
    )
    reverted = violation and (any_delegation or send_pos and not invalid_block)
    storage = {} if reverted else {slot_code_worked: value_code_worked}

    blockchain_test(
        pre=pre,
        post={contract_address: Account(storage=storage)},
        blocks=blocks,
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
@pytest.mark.parametrize("delegate_pos", [None, (0, 0), (2, 0)])
@pytest.mark.parametrize("undelegate_pos", [None, (0, 0), (2, 1)])
@pytest.mark.parametrize("send_pos", [None, (0, 0), (0, 1)])
@pytest.mark.parametrize("credit_pos", [None, (0, 0), (0, 1), (1, 0), (2, 1)])
def test_credit(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    violation: bool,
    pre_delegated: bool,
    delegate_pos: Tuple[int, int] | None,
    undelegate_pos: Tuple[int, int] | None,
    send_pos: Tuple[int, int] | None,
    credit_pos: Tuple[int, int] | None,
    fork: Fork,
) -> None:
    """
    Test reserve balance violations for an EOA sending txs with vaious values,
    where the exception rules are not enforced based on txs in invalid block.
    """
    # gas spend by transactions send in setup blocks
    prepare_tx_gas = (
        fork.gas_costs().G_TRANSACTION + fork.gas_costs().G_AUTHORIZATION * 2
    )
    # if any of the transactions in setup blocks are sent by main sender we
    # need to credit them extra
    prepare_tx_fee = GAS_PRICE * prepare_tx_gas if send_pos else 0

    balance += prepare_tx_fee

    target_address = Address(0x1111)
    if pre_delegated:
        test_sender = pre.fund_eoa(balance, delegation=target_address)
    else:
        test_sender = pre.fund_eoa(balance)

    contract = Op.SSTORE(slot_code_worked, value_code_worked) + Op.STOP
    contract_address = pre.deploy_contract(contract)

    nblocks = 3
    blocks = []
    test_sender_nonce = test_sender.nonce
    for nblock in range(nblocks):
        txs = []
        for ntx in range(2):
            authorization_list = []
            pos = (nblock, ntx)

            if send_pos == pos:
                nonce = test_sender_nonce
                sender = test_sender
                test_sender_nonce += 1
            else:
                nonce = 0
                sender = pre.fund_eoa()

            if delegate_pos == pos:
                authorization_list += [
                    AuthorizationTuple(
                        address=target_address,
                        nonce=test_sender_nonce,
                        signer=test_sender,
                    )
                ]
                test_sender_nonce += 1
            if undelegate_pos == pos:
                authorization_list += [
                    AuthorizationTuple(
                        address=Address(0),
                        nonce=test_sender_nonce,
                        signer=test_sender,
                    )
                ]
                test_sender_nonce += 1
            prepare_tx = Transaction(
                gas_limit=prepare_tx_gas,
                max_fee_per_gas=GAS_PRICE,
                max_priority_fee_per_gas=GAS_PRICE,
                to=Address(0x7873),
                nonce=nonce,
                sender=sender,
                authorization_list=authorization_list or None,
            )
            txs.append(prepare_tx)
            if credit_pos == pos:
                credit_tx = Transaction(
                    gas_limit=prepare_tx_gas,  # generous but always enough
                    max_fee_per_gas=GAS_PRICE,
                    max_priority_fee_per_gas=GAS_PRICE,
                    to=test_sender,
                    value=Spec.RESERVE_BALANCE,
                    sender=pre.fund_eoa(),
                )
                txs.append(credit_tx)
        # If this isn't the last block, we're done.
        # If it is, we'll append the test tx below.
        if nblock < nblocks - 1:
            blocks.append(Block(txs=txs))
            del txs

    test_tx = Transaction(
        gas_limit=generous_gas(fork),
        max_fee_per_gas=GAS_PRICE,
        max_priority_fee_per_gas=GAS_PRICE,
        to=contract_address,
        nonce=test_sender_nonce,
        value=value,
        sender=test_sender,
        authorization_list=authorization_list or None,
    )
    txs.append(test_tx)
    blocks.append(Block(txs=txs))

    any_delegation = pre_delegated or delegate_pos or undelegate_pos
    reverted = violation and (any_delegation or send_pos) and not credit_pos
    storage = {} if reverted else {slot_code_worked: value_code_worked}

    blockchain_test(
        pre=pre,
        post={contract_address: Account(storage=storage)},
        blocks=blocks,
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
@pytest.mark.parametrize("do_7702_send", [True, False])
def test_7702_caller_is_no_sender(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    violation: bool,
    do_7702_send: bool,
    fork: Fork,
) -> None:
    """
    Test that making an 7702 call from an EOA doesn't count interfere.
    """
    if do_7702_send:
        # Extra balance for the 7702 send
        balance += 1
        # The test_sender will send value with this call, but this doesn't
        # interfere with reserve balance rules.
        target_address = pre.deploy_contract(
            Op.CALL(address=Address(0x5656), value=1)
        )
    else:
        target_address = Address(0x1111)
    test_sender = pre.fund_eoa(balance, delegation=target_address)

    contract = Op.SSTORE(slot_code_worked, value_code_worked) + Op.STOP
    contract_address = pre.deploy_contract(contract)

    blocks = []
    test_sender_nonce = test_sender.nonce
    prepare_tx = Transaction(
        gas_limit=generous_gas(fork),
        to=test_sender,
        sender=pre.fund_eoa(),
    )
    blocks.append(Block(txs=[prepare_tx]))

    test_tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        nonce=test_sender_nonce,
        value=value,
        sender=test_sender,
    )
    blocks.append(Block(txs=[test_tx]))

    reverted = violation
    storage = {} if reverted else {slot_code_worked: value_code_worked}

    blockchain_test(
        pre=pre,
        post={contract_address: Account(storage=storage)},
        blocks=blocks,
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
@pytest.mark.parametrize("invalid_send_pos", [(0, 0), (1, 0), (1, 1)])
def test_valid_tx_after_invalid(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    balance: int,
    violation: bool,
    invalid_send_pos: Tuple[int, int],
    fork: Fork,
) -> None:
    """
    Test where a tx follows one which violated reserve balance.
    """
    # gas spend by transactions send in setup blocks
    prepare_tx_gas = fork.gas_costs().G_TRANSACTION
    prepare_tx_fee = GAS_PRICE * prepare_tx_gas
    balance += prepare_tx_fee
    test_sender = pre.fund_eoa(balance, delegation=Address(0x1111))

    contract = Op.SSTORE(slot_code_worked, value_code_worked) + Op.STOP
    contract_address = pre.deploy_contract(contract)

    prepare_tx_receiver = pre.fund_eoa(0)

    nblocks = 2
    blocks = []
    test_sender_nonce = test_sender.nonce
    for nblock in range(nblocks):
        txs = []
        for ntx in range(2):
            pos = (nblock, ntx)

            if invalid_send_pos == pos:
                nonce = test_sender_nonce
                sender = test_sender
                test_sender_nonce += 1
                receiver = prepare_tx_receiver
            else:
                nonce = 0
                sender = pre.fund_eoa()
                receiver = pre.fund_eoa()
            prepare_tx = Transaction(
                gas_limit=prepare_tx_gas,
                max_fee_per_gas=GAS_PRICE,
                max_priority_fee_per_gas=GAS_PRICE,
                to=receiver,
                nonce=nonce,
                sender=sender,
                value=balance - prepare_tx_fee,
            )
            txs.append(prepare_tx)
        # If this isn't the last block, we're done.
        # If it is, we'll append the test tx below.
        if nblock < nblocks - 1:
            blocks.append(Block(txs=txs))
            del txs

    test_tx = Transaction(
        gas_limit=generous_gas(fork),
        max_fee_per_gas=GAS_PRICE,
        max_priority_fee_per_gas=GAS_PRICE,
        to=contract_address,
        nonce=test_sender_nonce,
        value=value,
        sender=test_sender,
    )
    txs.append(test_tx)
    blocks.append(Block(txs=txs))

    reverted = violation
    storage = {} if reverted else {slot_code_worked: value_code_worked}

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(storage=storage),
            # Ensure the invalid prepare tx was invalid
            prepare_tx_receiver: None,
        },
        blocks=blocks,
    )
