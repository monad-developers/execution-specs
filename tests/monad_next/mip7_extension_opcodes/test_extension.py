"""
Tests of the EXTENSION opcode prefix under MIP-7.

MIP-7 gives EXTENSION a selector byte and leaves every selector that no
MIP assigns behaving like INVALID. The EIP-8163 suite, which the fork
is valid for, covers the selector-less EXTENSION, the selectors MIP-7
excludes and the neutrality of the prefix to JUMPDEST analysis.
"""

from typing import Generator

import pytest
from _pytest.mark.structures import ParameterSet
from execution_testing import (
    Account,
    Alloc,
    CodeGasMeasure,
    Fork,
    Op,
    StateTestFiller,
    Transaction,
)

from .spec import ref_spec_7

REFERENCE_SPEC_GIT_PATH = ref_spec_7.git_path
REFERENCE_SPEC_VERSION = ref_spec_7.version

pytestmark = pytest.mark.valid_from("MONAD_NEXT")

slot_code_worked = 1
slot_gas_measured = 2
value_code_worked = 0x1234
child_gas = 100_000


def undefined_selectors(fork: Fork) -> Generator[ParameterSet, None, None]:
    """Yield every selector byte the fork assigns no extended opcode to."""
    defined = {bytes(opcode)[1] for opcode in fork.extension_opcodes()}
    for selector in range(256):
        if selector not in defined:
            yield pytest.param(selector, id=f"0x{selector:02x}")


@pytest.mark.parametrize_by_fork("selector", undefined_selectors)
@pytest.mark.parametrize("stack_item", [0, 1])
def test_undefined_selector(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    selector: int,
    stack_item: int,
) -> None:
    """
    Execute EXTENSION with a selector no extended opcode is defined for.

    The frame halts like INVALID: its storage write is reverted and the
    caller measures the whole forwarded gas as consumed.
    """
    push = Op.PUSH0 if stack_item == 0 else Op.PUSH1(stack_item)
    child_code = (
        Op.SSTORE(slot_code_worked, value_code_worked)
        + push * 256
        + Op.EXTENSION
        + bytes([selector])
    )
    child_address = pre.deploy_contract(code=child_code)

    call = Op.CALL(child_gas, child_address, 0, 0, 0, 0, 0, address_warm=False)
    parent_address = pre.deploy_contract(
        code=CodeGasMeasure(
            code=call,
            extra_stack_items=1,
            sstore_key=slot_gas_measured,
        )
    )

    tx = Transaction(to=parent_address, sender=pre.fund_eoa())

    state_test(
        pre=pre,
        post={
            child_address: Account(storage={}),
            parent_address: Account(
                storage={slot_gas_measured: call.gas_cost(fork) + child_gas}
            ),
        },
        tx=tx,
    )
