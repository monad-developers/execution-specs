"""
Tests of the MIP-18 call stack introspection opcodes.

CALLSTACKDEPTH pushes the depth of the current frame and CALLERN(n)
pushes what CALLER returns n frames above it, so CALLERN(0) is CALLER
and CALLERN(CALLSTACKDEPTH) is ORIGIN.
"""

import pytest
from execution_testing import (
    Account,
    Address,
    Alloc,
    Bytecode,
    CodeGasMeasure,
    Fork,
    Op,
    StateTestFiller,
    Storage,
    Transaction,
)

from .spec import ref_spec_18

REFERENCE_SPEC_GIT_PATH = ref_spec_18.git_path
REFERENCE_SPEC_VERSION = ref_spec_18.version

pytestmark = pytest.mark.valid_from("MONAD_NEXT")

slot_gas_measured = 0


def test_top_level_frame(
    state_test: StateTestFiller,
    pre: Alloc,
) -> None:
    """
    Introspect the call stack from a transaction's top-level frame.

    The depth is 0, CALLERN(0) is the origin and any deeper index is
    out of range.
    """
    sender = pre.fund_eoa()
    storage = Storage()
    code = (
        Op.SSTORE(storage.store_next(0), Op.CALLSTACKDEPTH)
        + Op.SSTORE(storage.store_next(sender), Op.CALLERN(0))
        + Op.SSTORE(storage.store_next(0), Op.CALLERN(1))
        + Op.SSTORE(storage.store_next(0), Op.CALLERN(2**256 - 1))
    )
    contract_address = pre.deploy_contract(code, storage=storage.canary())

    tx = Transaction(to=contract_address, sender=sender)

    state_test(
        pre=pre,
        post={contract_address: Account(storage=storage)},
        tx=tx,
    )


@pytest.mark.parametrize_by_fork(
    "top_call_opcode", lambda fork: fork.call_opcodes()
)
@pytest.mark.parametrize_by_fork(
    "middle_call_opcode", lambda fork: fork.call_opcodes()
)
def test_call_chain(
    state_test: StateTestFiller,
    pre: Alloc,
    top_call_opcode: Op,
    middle_call_opcode: Op,
) -> None:
    """
    Introspect the call stack from the leaf of a two-hop chain, each hop
    made with its own call opcode, and relay the answers to the top-level
    frame's storage.
    """
    sender = pre.fund_eoa()
    depth = 2
    queries = [Op.CALLSTACKDEPTH] + [Op.CALLERN(n) for n in range(depth + 2)]
    sentinel = 0xBA5E

    leaf_address = pre.deploy_contract(
        sum(
            (Op.MSTORE(32 * i, query) for i, query in enumerate(queries)),
            Bytecode(),
        )
        + Op.RETURN(0, 32 * len(queries))
    )
    middle_address = pre.deploy_contract(
        middle_call_opcode(gas=Op.GAS, address=leaf_address)
        + Op.RETURNDATACOPY(0, 0, Op.RETURNDATASIZE)
        + Op.RETURN(0, Op.RETURNDATASIZE)
    )
    top_address = pre.deploy_contract(
        top_call_opcode(gas=Op.GAS, address=middle_address)
        + Op.RETURNDATACOPY(0, 0, Op.RETURNDATASIZE)
        + sum(
            (Op.SSTORE(i, Op.MLOAD(32 * i)) for i in range(len(queries))),
            Bytecode(),
        ),
        storage=dict.fromkeys(range(len(queries)), sentinel),
    )

    # Replay the chain as (running account, CALLER) per frame.
    frames: list[tuple[Address, Address]] = [(top_address, sender)]
    for call_opcode, callee in (
        (top_call_opcode, middle_address),
        (middle_call_opcode, leaf_address),
    ):
        address, caller = frames[-1]
        if call_opcode == Op.DELEGATECALL:
            frames.append((address, caller))
        elif call_opcode == Op.CALLCODE:
            frames.append((address, address))
        else:
            frames.append((callee, address))
    assert len(frames) == depth + 1
    answers: list[int | Address] = [
        depth,
        *(frames[depth - n][1] if n <= depth else 0 for n in range(depth + 2)),
    ]

    tx = Transaction(to=top_address, sender=sender)

    state_test(
        pre=pre,
        post={top_address: Account(storage=dict(enumerate(answers)))},
        tx=tx,
    )


@pytest.mark.parametrize(
    "code,setup",
    [
        pytest.param(Op.CALLSTACKDEPTH, Bytecode(), id="CALLSTACKDEPTH"),
        pytest.param(Op.CALLERN(0), Op.PUSH1(0), id="CALLERN"),
        pytest.param(
            Op.CALLERN(2**256 - 1),
            Op.PUSH32(2**256 - 1),
            id="CALLERN_out_of_range",
        ),
    ],
)
def test_gas_cost(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    code: Bytecode,
    setup: Bytecode,
) -> None:
    """
    Measure the gas of an extended opcode net of the `setup` of its
    stack input: the EXTENSION prefix adds nothing and CALLERN costs the
    same whatever its index.
    """
    contract_address = pre.deploy_contract(
        CodeGasMeasure(
            code=code,
            overhead_cost=setup.gas_cost(fork),
            extra_stack_items=1,
            sstore_key=slot_gas_measured,
        )
    )

    tx = Transaction(to=contract_address, sender=pre.fund_eoa())

    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_gas_measured: code.gas_cost(fork)
                    - setup.gas_cost(fork)
                }
            )
        },
        tx=tx,
    )
