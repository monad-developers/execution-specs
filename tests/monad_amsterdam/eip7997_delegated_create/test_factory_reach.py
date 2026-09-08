"""
Tests for reaching the EIP-7997 factory from a delegated frame.

Monad forbids the create opcodes inside a frame executing an EOA's
delegated code. The ban is callee-scoped: `access_delegation` sets it on
the frame it builds, and a child frame the delegated code calls does not
inherit it. EIP-7997 makes a CREATE2 factory reachable at a fixed
address on every chain, so these tests pin both halves of that
boundary.
"""

import pytest
from execution_testing import (
    Account,
    Alloc,
    Block,
    BlockchainTestFiller,
    Hash,
    Op,
    Transaction,
)
from execution_testing.test_types.helpers import compute_create2_address
from execution_testing.tools.tools_code.generators import Initcode

from ...amsterdam.eip7997_deterministic_factory_predeploy.spec import (
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
    pytest.mark.valid_from("MONAD_NEXT"),
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

    The halt is the only coverage of `CreateIn7702Context`; nothing is
    deployed and the storage write that follows never lands.
    """
    # The create halts before its initcode is read, so the empty
    # memory the size refers to is immaterial.
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


def test_factory_deploys_from_delegated_frame(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
) -> None:
    """
    A delegated frame reaches CREATE2 through the EIP-7997 factory.

    The ban applies to the frame running the delegated code, not to the
    factory frame it calls, so the deployment succeeds.
    """
    initcode = Initcode(deploy_code=Op.STOP)
    deployed = compute_create2_address(Spec.FACTORY_ADDRESS, SALT, initcode)

    delegate = pre.deploy_contract(
        Op.CALLDATACOPY(0, 0, Op.CALLDATASIZE)
        + Op.SSTORE(
            slot_call_result,
            Op.CALL(
                gas=Op.GAS,
                address=Spec.FACTORY_ADDRESS,
                args_offset=0,
                args_size=Op.CALLDATASIZE,
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

    blockchain_test(
        pre=pre,
        post={
            delegated: Account(storage={slot_call_result: 1}),
            deployed: Account(code=Op.STOP),
        },
        blocks=[Block(txs=[tx])],
    )
