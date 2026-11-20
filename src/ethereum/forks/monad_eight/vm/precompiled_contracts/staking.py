"""
Ethereum Virtual Machine (EVM) STAKING PRECOMPILED CONTRACT.

.. contents:: Table of Contents
    :backlinks: none
    :local:

Introduction
------------

Stub of Monad's staking precompiled contract.

Only the fallback path is modelled: the staking methods themselves are
not implemented, so every call that reaches the contract reverts as an
unsupported method would. The address still behaves as a precompile, so
it is warm from the start of every transaction.
"""

from ...vm import Evm
from ...vm.exceptions import InvalidParameter, RevertInMonadPrecompile
from ...vm.gas import GasCosts, charge_gas


def _is_call(evm: Evm) -> bool:
    # STATICCALL: is_static is True
    # DELEGATECALL: should_transfer_value is False
    # CALLCODE: code_address != current_target
    if evm.message.is_static:
        return False
    if not evm.message.should_transfer_value:
        return False
    if evm.message.code_address != evm.message.current_target:
        return False
    return True


def staking(evm: Evm) -> None:
    """
    Revert as the staking precompile does for an unsupported method.

    The precompile must be invoked via CALL. Invocations via STATICCALL,
    DELEGATECALL, or CALLCODE fail.

    An unsupported method costs `PRECOMPILE_STAKING_FALLBACK` gas and
    reverts with the error message "method not supported". Reverts
    consume all gas provided to the call frame.

    Parameters
    ----------
    evm :
        The current EVM frame.

    """
    # Must be invoked via CALL only (not STATICCALL, DELEGATECALL, CALLCODE)
    if not _is_call(evm):
        raise InvalidParameter

    # GAS
    charge_gas(evm, GasCosts.PRECOMPILE_STAKING_FALLBACK)

    # OPERATION
    evm.output = b"method not supported"
    raise RevertInMonadPrecompile
