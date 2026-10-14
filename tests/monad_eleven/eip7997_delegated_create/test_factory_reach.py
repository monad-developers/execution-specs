"""
Tests for reaching the EIP-7997 factory from a delegated frame.

Monad forbids the create opcodes in a frame running as a 7702-delegated
EOA.
"""

import pytest
from execution_testing import (
    Account,
    Address,
    Alloc,
    Block,
    BlockchainTestFiller,
    Hash,
    Op,
    Transaction,
)
from execution_testing.test_types.helpers import compute_create2_address
from execution_testing.tools.tools_code.generators import Initcode

from ...amsterdam.eip7997_deterministic_factory_contract.spec import (
    Spec,
    ref_spec_7997,
)

REFERENCE_SPEC_GIT_PATH = ref_spec_7997.git_path
REFERENCE_SPEC_VERSION = ref_spec_7997.version

SALT = 0x42

slot_code_worked = 0x1
value_code_worked = 0x1234
slot_call_result = 0x2

pytestmark = [
    pytest.mark.valid_from("MONAD_ELEVEN"),
    pytest.mark.pre_alloc_group(
        "eip7997_delegated_create_tests",
        reason="Tests the EIP-7997 factory reached from a delegated frame",
    ),
]


@pytest.mark.parametrize("create_op", [Op.CREATE, Op.CREATE2])
def test_create_halts_in_delegated_frame(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    create_op: Op,
) -> None:
    """
    A create opcode executed as an EOA's delegated code halts the frame.
    """
    delegate = pre.deploy_contract(
        create_op(value=0, size=1)
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    sender = pre.fund_eoa()
    delegated = pre.fund_eoa(delegation=delegate)

    tx = Transaction(
        to=delegated,
        sender=sender,
    )

    blockchain_test(
        pre=pre,
        post={delegated: Account(storage={})},
        blocks=[Block(txs=[tx])],
    )


@pytest.mark.with_all_call_opcodes
def test_factory_deploys_from_delegated_frame(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    call_opcode: Op,
) -> None:
    """
    A delegated frame reaches CREATE2 through the EIP-7997 factory only
    when the factory runs at its own address.
    """
    initcode = Initcode(deploy_code=Op.STOP)

    delegate = pre.deploy_contract(
        Op.CALLDATACOPY(0, 0, Op.CALLDATASIZE)
        + Op.SSTORE(
            slot_call_result,
            Op.ADD(
                1,
                call_opcode(
                    gas=Op.GAS,
                    address=Spec.FACTORY_ADDRESS,
                    args_offset=0,
                    args_size=Op.CALLDATASIZE,
                ),
            ),
        )
    )
    sender = pre.fund_eoa()
    delegated = pre.fund_eoa(delegation=delegate)

    tx = Transaction(
        to=delegated,
        data=Hash(SALT) + bytes(initcode),
        sender=sender,
    )

    post: dict[Address, Account | None]
    if call_opcode == Op.CALL:
        post = {
            delegated: Account(storage={slot_call_result: 2}),
            compute_create2_address(
                Spec.FACTORY_ADDRESS, SALT, initcode
            ): Account(code=Op.STOP),
        }
    elif call_opcode in (Op.DELEGATECALL, Op.CALLCODE, Op.STATICCALL):
        # STATICCALL halts the factory on the static context instead.
        post = {
            delegated: Account(storage={slot_call_result: 1}),
            compute_create2_address(
                delegated, SALT, initcode
            ): Account.NONEXISTENT,
        }
    else:
        raise ValueError(f"unexpected call opcode {call_opcode}")

    blockchain_test(
        pre=pre,
        post=post,
        blocks=[Block(txs=[tx])],
    )
