"""
Tests dippedIntoReserve() observes the reserve balance rules spanning multiple
blocks.
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
from execution_testing.forks.helpers import Fork

from .helpers import RefillFactory, call_dipped_into_reserve, generous_gas
from .spec import Spec, ref_spec_mip4

REFERENCE_SPEC_GIT_PATH = ref_spec_mip4.git_path
REFERENCE_SPEC_VERSION = ref_spec_mip4.version

slot_code_worked = 0x1
value_code_worked = 0x1234
slot_violation_result = 0x2

pytestmark = [
    pytest.mark.valid_from("MONAD_NINE"),
    pytest.mark.pre_alloc_group(
        "mip4_reserve_balance_introspection_tests",
        reason="Tests reserve balance introspection",
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
    refill_factory: RefillFactory,
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
    refill_call = refill_factory()
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
    prepare_tx_to = pre.nonexistent_account()
    if pre_delegated:
        test_sender = pre.fund_eoa(balance, delegation=target_address)
    else:
        test_sender = pre.fund_eoa(balance)

    contract = (
        Op.SSTORE(slot_violation_result, call_dipped_into_reserve())
        + refill_call(Op.ORIGIN)
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
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
                to=prepare_tx_to,
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
    expected_violation = (
        1
        if (violation and (any_delegation or (send_pos and send_pos[0] > 0)))
        else 0
    )

    storage = {
        slot_violation_result: expected_violation,
        slot_code_worked: value_code_worked,
    }
    blockchain_test(
        pre=pre,
        post={contract_address: Account(storage=storage)},
        blocks=blocks,
    )
