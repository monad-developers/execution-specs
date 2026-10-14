"""
Tests for the create opcodes around a 7702-delegated EOA.

Monad forbids the create opcodes in a frame running as a 7702-delegated
EOA, whichever code the frame executes.
"""

import pytest
from execution_testing import (
    Account,
    Address,
    Alloc,
    Initcode,
    Op,
    StateTestFiller,
    Storage,
    Transaction,
    compute_create_address,
)

from .helpers import (
    factory_code,
    forwarding_code,
    slot_call_result,
    slot_created,
)

pytestmark = pytest.mark.valid_from("MONAD_EIGHT")


@pytest.mark.with_all_create_opcodes
def test_create_in_delegated_code(
    state_test: StateTestFiller,
    pre: Alloc,
    create_opcode: Op,
) -> None:
    """A create opcode run as a delegated EOA's code halts the frame."""
    initcode = Initcode(deploy_code=Op.STOP)
    delegate = pre.deploy_contract(factory_code(create_opcode))
    delegated = pre.fund_eoa(delegation=delegate)

    tx = Transaction(to=delegated, data=initcode, sender=pre.fund_eoa())

    state_test(
        pre=pre,
        tx=tx,
        post={
            delegated: Account(nonce=delegated.nonce, storage={}),
            compute_create_address(
                address=delegated,
                nonce=delegated.nonce,
                initcode=initcode,
                opcode=create_opcode,
            ): Account.NONEXISTENT,
        },
    )


@pytest.mark.with_all_call_opcodes
@pytest.mark.with_all_create_opcodes
def test_contract_calls_delegated_factory(
    state_test: StateTestFiller,
    pre: Alloc,
    call_opcode: Op,
    create_opcode: Op,
) -> None:
    """
    A contract reaching a delegated EOA's creating code deploys only when
    that code runs at the contract's own address.
    """
    initcode = Initcode(deploy_code=Op.STOP)
    delegate = pre.deploy_contract(factory_code(create_opcode))
    delegated = pre.fund_eoa(delegation=delegate)
    deploys = call_opcode in (Op.DELEGATECALL, Op.CALLCODE)
    storage = Storage()
    storage[slot_call_result] = 1 if deploys else 0
    caller = pre.deploy_contract(
        forwarding_code(call_opcode, delegated), storage=storage.canary()
    )

    tx = Transaction(to=caller, data=initcode, sender=pre.fund_eoa())

    post: dict[Address, Account | None] = {
        delegated: Account(nonce=delegated.nonce, storage={}),
    }
    if deploys:
        created = compute_create_address(
            address=caller, nonce=1, initcode=initcode, opcode=create_opcode
        )
        post[caller] = Account(
            nonce=2, storage={slot_call_result: 1, slot_created: created}
        )
        post[created] = Account(code=Op.STOP)
    else:
        post[caller] = Account(nonce=1, storage={slot_call_result: 0})
        post[
            compute_create_address(
                address=delegated,
                nonce=delegated.nonce,
                initcode=initcode,
                opcode=create_opcode,
            )
        ] = Account.NONEXISTENT

    state_test(pre=pre, tx=tx, post=post)


@pytest.mark.with_all_call_opcodes
@pytest.mark.with_all_create_opcodes
def test_delegated_frame_calls_factory(
    state_test: StateTestFiller,
    pre: Alloc,
    call_opcode: Op,
    create_opcode: Op,
) -> None:
    """
    A delegated frame reaching a factory contract deploys only when the
    factory runs at its own address.
    """
    initcode = Initcode(deploy_code=Op.STOP)
    factory = pre.deploy_contract(factory_code(create_opcode))
    delegate = pre.deploy_contract(forwarding_code(call_opcode, factory))
    deploys = call_opcode == Op.CALL
    storage = Storage()
    storage[slot_call_result] = 1 if deploys else 0
    delegated = pre.fund_eoa(delegation=delegate, storage=storage.canary())

    tx = Transaction(to=delegated, data=initcode, sender=pre.fund_eoa())

    post: dict[Address, Account | None]
    if deploys:
        created = compute_create_address(
            address=factory, nonce=1, initcode=initcode, opcode=create_opcode
        )
        post = {
            delegated: Account(storage={slot_call_result: 1}),
            factory: Account(nonce=2, storage={slot_created: created}),
            created: Account(code=Op.STOP),
        }
    else:
        # STATICCALL halts the factory on the static context instead.
        post = {
            delegated: Account(
                nonce=delegated.nonce, storage={slot_call_result: 0}
            ),
            factory: Account(nonce=1, storage={}),
            compute_create_address(
                address=delegated,
                nonce=delegated.nonce,
                initcode=initcode,
                opcode=create_opcode,
            ): Account.NONEXISTENT,
        }

    state_test(pre=pre, tx=tx, post=post)
