"""
Ethereum Virtual Machine (EVM) RESERVE BALANCE PRECOMPILED CONTRACT.

.. contents:: Table of Contents
    :backlinks: none
    :local:

Introduction
------------

Implementation of the RESERVE BALANCE precompiled contract for MIP-4.
"""

from ethereum_types.numeric import U256

from ...vm import Evm
from ...vm.exceptions import InvalidParameter, RevertInMonadPrecompile
from ...vm.gas import GAS_WARM_ACCESS, charge_gas

# Function selector for dippedIntoReserve()
# keccak256("dippedIntoReserve()")[:4].hex() == "3a61584e"
DIPPED_INTO_RESERVE_SELECTOR = bytes.fromhex("3a61584e")


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


def reserve_balance(evm: Evm) -> None:
    """
    Return whether execution is in reserve balance violation.

    The precompile must be invoked via CALL. Invocations via STATICCALL,
    DELEGATECALL, or CALLCODE must revert.

    The method is not payable and must revert with the error message
    "value is nonzero" when called with a nonzero value.

    Calldata must be exactly the 4-byte function selector (0x3a61584e).
    If the selector does not match, the precompile reverts with "method
    not supported". If extra calldata is appended beyond the selector,
    the precompile reverts with "input is invalid".

    Reverts consume all gas provided to the call frame.

    Parameters
    ----------
    evm :
        The current EVM frame.

    """
    from ..interpreter import is_reserve_balance_violated

    data = evm.message.data

    # Must be invoked via CALL only (not STATICCALL, DELEGATECALL, CALLCODE)
    if not _is_call(evm):
        raise InvalidParameter

    # GAS
    charge_gas(evm, GAS_WARM_ACCESS)

    if len(data) < 4:
        evm.output = b"method not supported"
        raise RevertInMonadPrecompile

    if data[:4] != DIPPED_INTO_RESERVE_SELECTOR:
        evm.output = b"method not supported"
        raise RevertInMonadPrecompile

    if evm.message.value != 0:
        evm.output = b"value is nonzero"
        raise RevertInMonadPrecompile

    if len(data) > 4:
        evm.output = b"input is invalid"
        raise RevertInMonadPrecompile

    # OPERATION
    violation = is_reserve_balance_violated(
        evm.message.block_env.state,
        evm.message.tx_env,
    )
    # Return bool encoded as uint256 (32 bytes)
    evm.output = U256(1 if violation else 0).to_be_bytes32()
