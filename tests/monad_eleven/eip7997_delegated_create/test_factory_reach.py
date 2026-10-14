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
    Initcode,
    Op,
    Storage,
    Transaction,
    compute_create2_address,
)

from ...amsterdam.eip7997_deterministic_factory_contract.spec import (
    Spec,
    ref_spec_7997,
)
from ...monad_eight.delegated_creates.helpers import (
    forwarding_code,
    slot_call_result,
)

REFERENCE_SPEC_GIT_PATH = ref_spec_7997.git_path
REFERENCE_SPEC_VERSION = ref_spec_7997.version

SALT = 0x42
FACTORY = Address(Spec.FACTORY_ADDRESS)

pytestmark = [
    pytest.mark.valid_from("MONAD_ELEVEN"),
    pytest.mark.pre_alloc_group(
        "eip7997_delegated_create_tests",
        reason="Tests the EIP-7997 factory reached from a delegated frame",
    ),
]


def test_factory_as_delegated_code(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
) -> None:
    """The factory run as a delegated EOA's code halts the frame."""
    initcode = Initcode(deploy_code=Op.STOP)
    delegated = pre.fund_eoa(delegation=FACTORY)

    tx = Transaction(
        to=delegated,
        data=Hash(SALT) + bytes(initcode),
        sender=pre.fund_eoa(),
    )

    blockchain_test(
        pre=pre,
        post={
            delegated: Account(nonce=delegated.nonce),
            compute_create2_address(
                delegated, SALT, initcode
            ): Account.NONEXISTENT,
        },
        blocks=[Block(txs=[tx])],
    )


@pytest.mark.with_all_call_opcodes
def test_contract_calls_delegated_factory(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    call_opcode: Op,
) -> None:
    """
    A contract reaching the factory as a delegated EOA's code deploys only
    when the factory runs at the contract's own address.
    """
    initcode = Initcode(deploy_code=Op.STOP)
    delegated = pre.fund_eoa(delegation=FACTORY)
    deploys = call_opcode in (Op.DELEGATECALL, Op.CALLCODE)
    storage = Storage()
    storage[slot_call_result] = 1 if deploys else 0
    caller = pre.deploy_contract(
        forwarding_code(call_opcode, delegated), storage=storage.canary()
    )

    tx = Transaction(
        to=caller,
        data=Hash(SALT) + bytes(initcode),
        sender=pre.fund_eoa(),
    )

    post: dict[Address, Account | None] = {
        delegated: Account(nonce=delegated.nonce),
    }
    if deploys:
        post[caller] = Account(storage={slot_call_result: 1})
        post[compute_create2_address(caller, SALT, initcode)] = Account(
            code=Op.STOP
        )
    else:
        post[caller] = Account(storage={slot_call_result: 0})
        post[compute_create2_address(delegated, SALT, initcode)] = (
            Account.NONEXISTENT
        )

    blockchain_test(pre=pre, post=post, blocks=[Block(txs=[tx])])


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
    delegate = pre.deploy_contract(forwarding_code(call_opcode, FACTORY))
    deploys = call_opcode == Op.CALL
    storage = Storage()
    storage[slot_call_result] = 1 if deploys else 0
    delegated = pre.fund_eoa(delegation=delegate, storage=storage.canary())

    tx = Transaction(
        to=delegated,
        data=Hash(SALT) + bytes(initcode),
        sender=pre.fund_eoa(),
    )

    post: dict[Address, Account | None]
    if deploys:
        post = {
            delegated: Account(storage={slot_call_result: 1}),
            compute_create2_address(FACTORY, SALT, initcode): Account(
                code=Op.STOP
            ),
        }
    else:
        # STATICCALL halts the factory on the static context instead.
        post = {
            delegated: Account(storage={slot_call_result: 0}),
            compute_create2_address(
                delegated, SALT, initcode
            ): Account.NONEXISTENT,
        }

    blockchain_test(pre=pre, post=post, blocks=[Block(txs=[tx])])
