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

GAS_PRICE = 100 * 10**9


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
@pytest.mark.execute(
    pytest.mark.skip(
        reason="Requires strict block numbering AND strict cross-sender "
        "tx ordering within a block; Monad reorders cross-sender txs."
    )
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
    Test reserve balance violations for an EOA sending txs with various values,
    where the exception rules are enforced based on txs in various block
    positions.
    """
    # gas spend by transactions send in setup blocks
    prepare_tx_gas = (
        fork.gas_costs().GAS_TX_BASE
        + fork.gas_costs().GAS_AUTH_PER_EMPTY_ACCOUNT * 2
    )
    # If a setup-block tx is sent by the main sender, credit it to keep the
    # sender's balance at test_tx start equal to `balance`. We do NOT add
    # the test_tx's gas budget on top: the reserve check uses
    # min(RESERVE, original_balance) - gas_fees as the threshold, and
    # over-funding inflates original_balance so the threshold becomes
    # RESERVE - gas_fees, hiding violations when value < gas_fees.
    prepare_tx_fee = GAS_PRICE * prepare_tx_gas if send_pos else 0
    balance += prepare_tx_fee

    target_address = pre.nonexistent_account()
    if pre_delegated:
        test_sender = pre.fund_eoa(balance, delegation=target_address)
    else:
        test_sender = pre.fund_eoa(balance)

    contract = Op.SSTORE(slot_code_worked, value_code_worked) + Op.STOP
    contract_address = pre.deploy_contract(contract)

    nblocks = 4
    blocks = []
    test_sender_nonce = int(test_sender.nonce)
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
                to=pre.nonexistent_account(),
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
    Test reserve balance violations for an EOA sending txs with various values,
    where the exception rules are not enforced based on txs in invalid block.
    """
    # gas spend by transactions send in setup blocks
    prepare_tx_gas = (
        fork.gas_costs().GAS_TX_BASE
        + fork.gas_costs().GAS_AUTH_PER_EMPTY_ACCOUNT * 2
    )
    # if any of the transactions in setup blocks are sent by main sender we
    # need to credit them extra
    prepare_tx_fee = (
        GAS_PRICE * prepare_tx_gas if send_pos and not invalid_block else 0
    )
    balance += prepare_tx_fee

    target_address = pre.nonexistent_account()
    if pre_delegated:
        test_sender = pre.fund_eoa(balance, delegation=target_address)
    else:
        test_sender = pre.fund_eoa(balance)

    contract = Op.SSTORE(slot_code_worked, value_code_worked) + Op.STOP
    contract_address = pre.deploy_contract(contract)

    nblocks = 2
    blocks = []
    test_sender_nonce = int(test_sender.nonce)
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
                to=pre.nonexistent_account(),
                nonce=nonce,
                sender=sender,
                authorization_list=authorization_list or None,
            )
            txs.append(prepare_tx)
        if nblock == 0 and invalid_block:
            txs.append(
                Transaction(
                    gas_limit=fork.gas_costs().GAS_TX_BASE - 123,
                    to=pre.nonexistent_account(),
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
@pytest.mark.execute(
    pytest.mark.skip(
        reason="Requires strict block numbering AND strict cross-sender "
        "tx ordering within a block; Monad reorders cross-sender txs."
    )
)
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
    Test reserve balance violations for an EOA sending txs with various values,
    but also receiving a refill of entire reserve balance in the meantime.
    """
    # gas spend by transactions send in setup blocks
    prepare_tx_gas = (
        fork.gas_costs().GAS_TX_BASE
        + fork.gas_costs().GAS_AUTH_PER_EMPTY_ACCOUNT * 2
    )
    # if any of the transactions in setup blocks are sent by main sender we
    # need to credit them extra
    prepare_tx_fee = GAS_PRICE * prepare_tx_gas if send_pos else 0

    balance += prepare_tx_fee

    target_address = pre.nonexistent_account()
    if pre_delegated:
        test_sender = pre.fund_eoa(balance, delegation=target_address)
    else:
        test_sender = pre.fund_eoa(balance)

    contract = Op.SSTORE(slot_code_worked, value_code_worked) + Op.STOP
    contract_address = pre.deploy_contract(contract)

    nblocks = 3
    blocks = []
    test_sender_nonce = int(test_sender.nonce)
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
                to=pre.nonexistent_account(),
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


@pytest.mark.parametrize("pre_delegated", [True, False])
@pytest.mark.parametrize("send_pos", [(0, 0), (2, 0)])
@pytest.mark.parametrize("credit_pos", [(0, 0), (0, 1), (1, 0), (2, 1)])
@pytest.mark.parametrize(
    "send_value",
    [
        pytest.param(0, id="send_zero"),
        pytest.param(1, id="send_one"),
        pytest.param(Spec.RESERVE_BALANCE, id="send_reserve"),
    ],
)
@pytest.mark.parametrize(
    "credit_value",
    [
        pytest.param(0, id="credit_zero"),
        pytest.param(1, id="credit_one"),
        pytest.param(Spec.RESERVE_BALANCE, id="credit_reserve"),
    ],
)
@pytest.mark.parametrize("credit_statically_visible", [True, False])
@pytest.mark.execute(
    pytest.mark.skip(
        reason="Requires strict block numbering AND strict cross-sender "
        "tx ordering within a block; Monad reorders cross-sender txs."
    )
)
def test_credit_with_value(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    pre_delegated: bool,
    send_pos: Tuple[int, int],
    credit_pos: Tuple[int, int],
    send_value: int,
    credit_value: int,
    credit_statically_visible: bool,
    fork: Fork,
) -> None:
    """
    Test reserve balance where sender transfers value in a setup tx
    and receives credit of varying amounts via direct transfer or
    SELFDESTRUCT contract.

    Uses 4 blocks so send in block 0 falls outside the k=3 window,
    making the emptying exception reachable for undelegated senders.
    """
    prepare_tx_gas = fork.gas_costs().GAS_TX_BASE
    prepare_tx_fee = GAS_PRICE * prepare_tx_gas
    initial_balance = Spec.RESERVE_BALANCE + send_value + prepare_tx_fee

    target_address = pre.nonexistent_account()
    if pre_delegated:
        test_sender = pre.fund_eoa(initial_balance, delegation=target_address)
    else:
        test_sender = pre.fund_eoa(initial_balance)

    contract = Op.SSTORE(slot_code_worked, value_code_worked) + Op.STOP
    contract_address = pre.deploy_contract(contract)

    nblocks = 4
    blocks = []
    test_sender_nonce = int(test_sender.nonce)
    for nblock in range(nblocks):
        txs = []
        for ntx in range(2):
            pos = (nblock, ntx)

            if send_pos == pos:
                sender = test_sender
                nonce = test_sender_nonce
                test_sender_nonce += 1
            else:
                sender = pre.fund_eoa()
                nonce = 0

            prepare_tx = Transaction(
                gas_limit=prepare_tx_gas,
                max_fee_per_gas=GAS_PRICE,
                max_priority_fee_per_gas=GAS_PRICE,
                to=pre.nonexistent_account(),
                nonce=nonce,
                sender=sender,
                value=send_value,
            )
            txs.append(prepare_tx)

            if credit_pos == pos:
                if credit_statically_visible:
                    credit_tx = Transaction(
                        gas_limit=prepare_tx_gas,
                        max_fee_per_gas=GAS_PRICE,
                        max_priority_fee_per_gas=GAS_PRICE,
                        to=test_sender,
                        value=credit_value,
                        sender=pre.fund_eoa(),
                    )
                else:
                    credit_contract = pre.deploy_contract(
                        Op.SELFDESTRUCT(address=test_sender),
                        balance=credit_value,
                    )
                    credit_tx = Transaction(
                        gas_limit=generous_gas(fork),
                        max_fee_per_gas=GAS_PRICE,
                        max_priority_fee_per_gas=GAS_PRICE,
                        to=credit_contract,
                        sender=pre.fund_eoa(),
                    )
                txs.append(credit_tx)
        if nblock < nblocks - 1:
            blocks.append(Block(txs=txs))
            del txs

    value = 1
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

    # Sender balance at test time: RESERVE_BALANCE + credit_value.
    violation = credit_value < value
    recent_send = send_pos[0] > 0
    is_exception = not pre_delegated and not recent_send
    reverted = violation and not is_exception
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
        # Use deterministic deploy (deferred to end of pending tx list) so
        # the delegation target has no code at fund_eoa time; otherwise the
        # funding tx's CALL to the (delegated) sender would invoke the
        # target's code and leak an extra 1 wei out of sender during setup.
        target_address = pre.deterministic_deploy_contract(
            deploy_code=Op.CALL(address=pre.nonexistent_account(), value=1),
            salt=int(bytes(pre.nonexistent_account()).hex(), 16),
        )
    else:
        target_address = pre.nonexistent_account()
    test_sender = pre.fund_eoa(balance, delegation=target_address)

    contract = Op.SSTORE(slot_code_worked, value_code_worked) + Op.STOP
    contract_address = pre.deploy_contract(contract)

    blocks = []
    test_sender_nonce = int(test_sender.nonce)
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
@pytest.mark.execute(
    pytest.mark.skip(
        reason="Requires strict block numbering AND strict cross-sender "
        "tx ordering within a block; Monad reorders cross-sender txs."
    )
)
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
    prepare_tx_gas = fork.gas_costs().GAS_TX_BASE
    prepare_tx_fee = GAS_PRICE * prepare_tx_gas
    balance += prepare_tx_fee
    test_sender = pre.fund_eoa(balance, delegation=pre.nonexistent_account())

    contract = Op.SSTORE(slot_code_worked, value_code_worked) + Op.STOP
    contract_address = pre.deploy_contract(contract)

    prepare_tx_receiver = pre.fund_eoa(0)

    nblocks = 2
    blocks = []
    test_sender_nonce = int(test_sender.nonce)
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
