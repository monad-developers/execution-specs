"""
Ethereum Virtual Machine (EVM) Storage Instructions.

.. contents:: Table of Contents
    :backlinks: none
    :local:

Introduction
------------

Implementations of the EVM storage related instructions.
"""

from ethereum_types.numeric import Uint

from ...state_tracker import (
    get_storage,
    get_transient_storage,
    set_storage,
    set_transient_storage,
)
from .. import Evm
from ..exceptions import OutOfGasError, WriteInStaticContext
from ..gas import (
    GasCosts,
    charge_gas,
    page_index,
)
from ..stack import pop, push


def sload(evm: Evm) -> None:
    """
    Load a value from storage to the stack, with page-level access
    tracking per MIP-8.

    Parameters
    ----------
    evm :
        The current EVM frame.

    """
    # STACK
    slot = pop(evm.stack)
    key = slot.to_be_bytes32()

    # GAS
    page_key = (evm.message.current_target, page_index(slot))
    if page_key in evm.read_accessed_pages:
        charge_gas(evm, GasCosts.PAGE_BASE_COST)
    else:
        evm.read_accessed_pages.add(page_key)
        charge_gas(evm, GasCosts.PAGE_LOAD_COST + GasCosts.PAGE_BASE_COST)

    # OPERATION
    tx_state = evm.message.tx_env.state
    value = get_storage(tx_state, evm.message.current_target, key)

    push(evm.stack, value)

    # PROGRAM COUNTER
    evm.pc += Uint(1)


def sstore(evm: Evm) -> None:
    """
    Store a value in storage, with page-level I/O cost and per-page
    state growth tracking per MIP-8.

    Parameters
    ----------
    evm :
        The current EVM frame.

    """
    # STACK
    slot = pop(evm.stack)
    key = slot.to_be_bytes32()
    new_value = pop(evm.stack)
    if evm.gas_left <= GasCosts.CALL_STIPEND:
        raise OutOfGasError

    tx_state = evm.message.tx_env.state
    target = evm.message.current_target
    current_value = get_storage(tx_state, target, key)

    page_key = (target, page_index(slot))

    # Page I/O cost
    gas_cost = GasCosts.PAGE_BASE_COST

    if page_key not in evm.read_accessed_pages:
        gas_cost += GasCosts.PAGE_LOAD_COST
        evm.read_accessed_pages.add(page_key)

    if current_value != new_value:
        if page_key not in evm.write_accessed_pages:
            gas_cost += GasCosts.PAGE_WRITE_COST
            evm.write_accessed_pages.add(page_key)
            evm.current_state_growth.setdefault(page_key, 0)
            evm.net_state_growth.setdefault(page_key, 0)

    # State growth cost
    if current_value == 0 and new_value != 0:
        evm.current_state_growth[page_key] = (
            evm.current_state_growth.get(page_key, 0) + 1
        )
    elif current_value != 0 and new_value == 0:
        evm.current_state_growth[page_key] = (
            evm.current_state_growth.get(page_key, 0) - 1
        )

    current = evm.current_state_growth.get(page_key, 0)
    peak = evm.net_state_growth.get(page_key, 0)
    if current > peak:
        gas_cost += GasCosts.PAGE_STATE_GROWTH_COST
        evm.net_state_growth[page_key] = current

    charge_gas(evm, gas_cost)
    if evm.message.is_static:
        raise WriteInStaticContext
    set_storage(tx_state, target, key, new_value)

    # PROGRAM COUNTER
    evm.pc += Uint(1)


def tload(evm: Evm) -> None:
    """
    Loads to the stack, the value corresponding to a certain key from the
    transient storage of the current account.

    Parameters
    ----------
    evm :
        The current EVM frame.

    """
    # STACK
    key = pop(evm.stack).to_be_bytes32()

    # GAS
    charge_gas(evm, GasCosts.WARM_ACCESS)

    # OPERATION
    value = get_transient_storage(
        evm.message.tx_env.state, evm.message.current_target, key
    )
    push(evm.stack, value)

    # PROGRAM COUNTER
    evm.pc += Uint(1)


def tstore(evm: Evm) -> None:
    """
    Stores a value at a certain key in the current context's transient storage.

    Parameters
    ----------
    evm :
        The current EVM frame.

    """
    # STACK
    key = pop(evm.stack).to_be_bytes32()
    new_value = pop(evm.stack)

    # GAS
    charge_gas(evm, GasCosts.WARM_ACCESS)
    if evm.message.is_static:
        raise WriteInStaticContext
    set_transient_storage(
        evm.message.tx_env.state,
        evm.message.current_target,
        key,
        new_value,
    )

    # PROGRAM COUNTER
    evm.pc += Uint(1)
