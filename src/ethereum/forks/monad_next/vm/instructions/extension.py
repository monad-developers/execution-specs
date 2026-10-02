"""
Ethereum Virtual Machine (EVM) Extension Instructions.

.. contents:: Table of Contents
    :backlinks: none
    :local:

Introduction
------------

Dispatch of the extended opcodes behind the `EXTENSION` prefix, as
defined by MIP-7 (https://github.com/monad-crypto/MIPs/blob/main/MIPs/MIP-7.md).

An extended opcode is the two-byte sequence of `EXTENSION` followed by a
selector byte. Selectors `0x5B` (`JUMPDEST`) and `0x60`-`0x7F`
(`PUSH1`-`PUSH32`) are never assigned, which keeps jump destination
analysis unaware of extended opcodes: it reads the selector as a plain
opcode and finds the same destinations as an analysis that knows
nothing of `EXTENSION`.
"""

import enum
from typing import Callable, Dict

from ethereum_types.numeric import Uint, ulen

from .. import Evm
from ..exceptions import InvalidExtension
from . import environment as environment_instructions


class ExtensionOps(enum.Enum):
    """
    Enum for the selectors of the extended opcodes.

    A selector is never `0x5B` nor in the range `0x60`-`0x7F`.
    """

    # Call Stack Introspection Ops
    CALLSTACKDEPTH = 0x00
    CALLERN = 0x01


extension_implementation: Dict[ExtensionOps, Callable] = {
    ExtensionOps.CALLSTACKDEPTH: environment_instructions.callstackdepth,
    ExtensionOps.CALLERN: environment_instructions.callern,
}


def extension(evm: Evm) -> None:
    """
    Execute the extended opcode selected by the byte following
    `EXTENSION`.

    `EXTENSION` with no selector byte, or with a selector no extended
    opcode is defined for, behaves like `INVALID`. The extended opcode
    charges its own gas and advances the program counter past both bytes
    and any immediates of its own.

    Parameters
    ----------
    evm :
        The current EVM frame.

    """
    selector_pc = evm.pc + Uint(1)
    if selector_pc >= ulen(evm.code):
        raise InvalidExtension
    selector = evm.code[selector_pc]
    for extension_op, implementation in extension_implementation.items():
        if extension_op.value == selector:
            implementation(evm)
            return
    raise InvalidExtension
