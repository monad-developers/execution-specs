"""
Ethereum Virtual Machine (EVM) STAKING PRECOMPILED CONTRACT.

.. contents:: Table of Contents
    :backlinks: none
    :local:

Introduction
------------

Implementation of the staking precompiled contract.
Getter functions return constant stub values.
Setter functions are stubs that respect interface rules.
"""

from ethereum_types.bytes import Bytes
from ethereum_types.numeric import U256, Uint

from ...vm import Evm
from ...vm.exceptions import InvalidParameter, RevertInMonadPrecompile
from ...vm.gas import charge_gas

# Gas costs per function (from staking spec)
GAS_ADD_VALIDATOR = Uint(505125)
GAS_DELEGATE = Uint(260850)
GAS_UNDELEGATE = Uint(147750)
GAS_WITHDRAW = Uint(68675)
GAS_COMPOUND = Uint(285050)
GAS_CLAIM_REWARDS = Uint(155375)
GAS_CHANGE_COMMISSION = Uint(39475)
GAS_EXTERNAL_REWARD = Uint(62300)
GAS_GET_VALIDATOR = Uint(97200)
GAS_GET_DELEGATOR = Uint(184900)
GAS_GET_WITHDRAWAL_REQUEST = Uint(24300)
GAS_GET_CONSENSUS_VALIDATOR_SET = Uint(814000)
GAS_GET_SNAPSHOT_VALIDATOR_SET = Uint(814000)
GAS_GET_EXECUTION_VALIDATOR_SET = Uint(814000)
GAS_GET_DELEGATIONS = Uint(814000)
GAS_GET_DELEGATORS = Uint(814000)
GAS_GET_EPOCH = Uint(16200)
GAS_GET_PROPOSER_VAL_ID = Uint(100)
GAS_UNKNOWN_SELECTOR = Uint(40000)

# Function selectors
SELECTOR_ADD_VALIDATOR = bytes.fromhex("f145204c")
SELECTOR_DELEGATE = bytes.fromhex("84994fec")
SELECTOR_UNDELEGATE = bytes.fromhex("5cf41514")
SELECTOR_WITHDRAW = bytes.fromhex("aed2ee73")
SELECTOR_COMPOUND = bytes.fromhex("b34fea67")
SELECTOR_CLAIM_REWARDS = bytes.fromhex("a76e2ca5")
SELECTOR_CHANGE_COMMISSION = bytes.fromhex("9bdcc3c8")
SELECTOR_EXTERNAL_REWARD = bytes.fromhex("e4b3303b")
SELECTOR_GET_VALIDATOR = bytes.fromhex("2b6d639a")
SELECTOR_GET_DELEGATOR = bytes.fromhex("573c1ce0")
SELECTOR_GET_WITHDRAWAL_REQUEST = bytes.fromhex("56fa2045")
SELECTOR_GET_CONSENSUS_VALIDATOR_SET = bytes.fromhex("fb29b729")
SELECTOR_GET_SNAPSHOT_VALIDATOR_SET = bytes.fromhex("de66a368")
SELECTOR_GET_EXECUTION_VALIDATOR_SET = bytes.fromhex("7cb074df")
SELECTOR_GET_DELEGATIONS = bytes.fromhex("4fd66050")
SELECTOR_GET_DELEGATORS = bytes.fromhex("a0843a26")
SELECTOR_GET_EPOCH = bytes.fromhex("757991a8")
SELECTOR_GET_PROPOSER_VAL_ID = bytes.fromhex("fbacb0be")

# Syscall selectors (system transactions only)
SELECTOR_SYSCALL_ON_EPOCH_CHANGE = bytes.fromhex("1d4e9f02")
SELECTOR_SYSCALL_REWARD = bytes.fromhex("791bdcf3")
SELECTOR_SYSCALL_SNAPSHOT = bytes.fromhex("157eeb21")

# All known selectors mapped to their gas cost and whether they are
# payable (accept msg.value > 0)
_SELECTOR_INFO: dict[bytes, tuple[Uint, bool, int]] = {
    # (gas_cost, is_payable, expected_data_size)
    # Setters
    # addValidator(bytes,bytes,bytes) - 4+32*3 offset words
    SELECTOR_ADD_VALIDATOR: (GAS_ADD_VALIDATOR, True, 100),
    # delegate(uint64) - 4+32
    SELECTOR_DELEGATE: (GAS_DELEGATE, True, 36),
    # undelegate(uint64,uint256,uint8) - 4+32*3
    SELECTOR_UNDELEGATE: (GAS_UNDELEGATE, False, 100),
    # withdraw(uint64,uint8) - 4+32*2
    SELECTOR_WITHDRAW: (GAS_WITHDRAW, False, 68),
    # compound(uint64) - 4+32
    SELECTOR_COMPOUND: (GAS_COMPOUND, False, 36),
    # claimRewards(uint64) - 4+32
    SELECTOR_CLAIM_REWARDS: (GAS_CLAIM_REWARDS, False, 36),
    # changeCommission(uint64,uint256) - 4+32*2
    SELECTOR_CHANGE_COMMISSION: (GAS_CHANGE_COMMISSION, False, 68),
    # externalReward(uint64) - 4+32
    SELECTOR_EXTERNAL_REWARD: (GAS_EXTERNAL_REWARD, True, 36),
    # Getters
    # getValidator(uint64) - 4+32
    SELECTOR_GET_VALIDATOR: (GAS_GET_VALIDATOR, False, 36),
    # getDelegator(uint64,address) - 4+32*2
    SELECTOR_GET_DELEGATOR: (GAS_GET_DELEGATOR, False, 68),
    # getWithdrawalRequest(uint64,address,uint8) - 4+32*3
    SELECTOR_GET_WITHDRAWAL_REQUEST: (
        GAS_GET_WITHDRAWAL_REQUEST,
        False,
        100,
    ),
    # getConsensusValidatorSet(uint32) - 4+32
    SELECTOR_GET_CONSENSUS_VALIDATOR_SET: (
        GAS_GET_CONSENSUS_VALIDATOR_SET,
        False,
        36,
    ),
    # getSnapshotValidatorSet(uint32) - 4+32
    SELECTOR_GET_SNAPSHOT_VALIDATOR_SET: (
        GAS_GET_SNAPSHOT_VALIDATOR_SET,
        False,
        36,
    ),
    # getExecutionValidatorSet(uint32) - 4+32
    SELECTOR_GET_EXECUTION_VALIDATOR_SET: (
        GAS_GET_EXECUTION_VALIDATOR_SET,
        False,
        36,
    ),
    # getDelegations(address,uint64) - 4+32*2
    SELECTOR_GET_DELEGATIONS: (GAS_GET_DELEGATIONS, False, 68),
    # getDelegators(uint64,address) - 4+32*2
    SELECTOR_GET_DELEGATORS: (GAS_GET_DELEGATORS, False, 68),
    # getEpoch() - 4
    SELECTOR_GET_EPOCH: (GAS_GET_EPOCH, False, 4),
    # getProposerValId() - 4
    SELECTOR_GET_PROPOSER_VAL_ID: (GAS_GET_PROPOSER_VAL_ID, False, 4),
    # Syscalls
    SELECTOR_SYSCALL_ON_EPOCH_CHANGE: (GAS_UNKNOWN_SELECTOR, False, 36),
    SELECTOR_SYSCALL_REWARD: (GAS_UNKNOWN_SELECTOR, False, 36),
    SELECTOR_SYSCALL_SNAPSHOT: (GAS_UNKNOWN_SELECTOR, False, 4),
}

# Sets of selectors by category
_SETTER_SELECTORS = frozenset(
    {
        SELECTOR_ADD_VALIDATOR,
        SELECTOR_DELEGATE,
        SELECTOR_UNDELEGATE,
        SELECTOR_WITHDRAW,
        SELECTOR_COMPOUND,
        SELECTOR_CLAIM_REWARDS,
        SELECTOR_CHANGE_COMMISSION,
        SELECTOR_EXTERNAL_REWARD,
    }
)

_GETTER_SELECTORS = frozenset(
    {
        SELECTOR_GET_VALIDATOR,
        SELECTOR_GET_DELEGATOR,
        SELECTOR_GET_WITHDRAWAL_REQUEST,
        SELECTOR_GET_CONSENSUS_VALIDATOR_SET,
        SELECTOR_GET_SNAPSHOT_VALIDATOR_SET,
        SELECTOR_GET_EXECUTION_VALIDATOR_SET,
        SELECTOR_GET_DELEGATIONS,
        SELECTOR_GET_DELEGATORS,
        SELECTOR_GET_EPOCH,
        SELECTOR_GET_PROPOSER_VAL_ID,
    }
)

_SYSCALL_SELECTORS = frozenset(
    {
        SELECTOR_SYSCALL_ON_EPOCH_CHANGE,
        SELECTOR_SYSCALL_REWARD,
        SELECTOR_SYSCALL_SNAPSHOT,
    }
)


def _validate_call_type(evm: Evm) -> None:
    """
    Validate that the precompile is invoked via CALL only.

    STATICCALL, DELEGATECALL, and CALLCODE are not allowed.
    """
    if evm.message.is_static:
        raise InvalidParameter
    if not evm.message.should_transfer_value:
        raise InvalidParameter
    if evm.message.code_address != evm.message.current_target:
        raise InvalidParameter


def _abi_encode_uint256(value: int) -> bytes:
    """Encode an integer as a 32-byte big-endian uint256."""
    return U256(value).to_be_bytes32()


def _abi_encode_bool(value: bool) -> bytes:
    """Encode a boolean as a 32-byte big-endian uint256."""
    return _abi_encode_uint256(1 if value else 0)


def _handle_get_epoch(evm: Evm) -> None:
    """
    Handle getEpoch() call.

    Return stub: epoch=0, in_boundary_delay=false.
    """
    # Returns (uint64 epoch, bool inBoundaryDelay)
    evm.output = _abi_encode_uint256(0) + _abi_encode_bool(False)


def _handle_get_proposer_val_id(evm: Evm) -> None:
    """
    Handle getProposerValId() call.

    Return stub: validator_id=0 (no validators registered).
    """
    # Returns uint64
    evm.output = _abi_encode_uint256(0)


def _handle_get_validator(evm: Evm) -> None:
    """
    Handle getValidator(uint64) call.

    Return stub: a zeroed-out validator structure.
    """
    # Return 0 for all fields (empty validator)
    # The struct has many fields; return enough zero words
    evm.output = b"\x00" * 32 * 20


def _handle_get_delegator(evm: Evm) -> None:
    """
    Handle getDelegator(uint64,address) call.

    Return stub: a zeroed-out delegator structure.
    """
    evm.output = b"\x00" * 32 * 10


def _handle_get_withdrawal_request(evm: Evm) -> None:
    """
    Handle getWithdrawalRequest(uint64,address,uint8) call.

    Return stub: a zeroed-out withdrawal request.
    """
    # Returns (uint256 amount, uint256 accumulator, uint64 activationEpoch)
    evm.output = (
        _abi_encode_uint256(0)
        + _abi_encode_uint256(0)
        + _abi_encode_uint256(0)
    )


def _handle_get_validator_set(evm: Evm) -> None:
    """
    Handle validator set query (consensus/snapshot/execution).

    Return stub: empty set with done=true, nextCursor=0.
    """
    # Returns (bool done, uint32 nextCursor, bytes data)
    # Use ABI encoding with dynamic bytes
    # offset for bytes field = 96 (3 words)
    evm.output = (
        _abi_encode_bool(True)  # done
        + _abi_encode_uint256(0)  # nextCursor
        + _abi_encode_uint256(96)  # offset to bytes
        + _abi_encode_uint256(0)  # bytes length = 0
    )


def _handle_get_delegations(evm: Evm) -> None:
    """
    Handle getDelegations(address,uint64) call.

    Return stub: empty delegation list.
    """
    # Returns (bool done, uint64 nextCursor, bytes data)
    evm.output = (
        _abi_encode_bool(True)
        + _abi_encode_uint256(0)
        + _abi_encode_uint256(96)
        + _abi_encode_uint256(0)
    )


def _handle_get_delegators(evm: Evm) -> None:
    """
    Handle getDelegators(uint64,address) call.

    Return stub: empty delegator list.
    """
    evm.output = (
        _abi_encode_bool(True)
        + _abi_encode_uint256(0)
        + _abi_encode_uint256(96)
        + _abi_encode_uint256(0)
    )


# Map getter selectors to their handler functions
_GETTER_HANDLERS: dict[bytes, object] = {
    SELECTOR_GET_EPOCH: _handle_get_epoch,
    SELECTOR_GET_PROPOSER_VAL_ID: _handle_get_proposer_val_id,
    SELECTOR_GET_VALIDATOR: _handle_get_validator,
    SELECTOR_GET_DELEGATOR: _handle_get_delegator,
    SELECTOR_GET_WITHDRAWAL_REQUEST: _handle_get_withdrawal_request,
    SELECTOR_GET_CONSENSUS_VALIDATOR_SET: _handle_get_validator_set,
    SELECTOR_GET_SNAPSHOT_VALIDATOR_SET: _handle_get_validator_set,
    SELECTOR_GET_EXECUTION_VALIDATOR_SET: _handle_get_validator_set,
    SELECTOR_GET_DELEGATIONS: _handle_get_delegations,
    SELECTOR_GET_DELEGATORS: _handle_get_delegators,
}


def staking(evm: Evm) -> None:
    """
    Implement the staking precompiled contract.

    The precompile must be invoked via CALL. Invocations via STATICCALL,
    DELEGATECALL, or CALLCODE must revert.

    Calldata must begin with a 4-byte function selector. Unknown selectors
    and malformed calldata cause a revert that consumes all provided gas.

    Parameters
    ----------
    evm :
        The current EVM frame.

    """
    data = evm.message.data

    # Must be invoked via CALL only
    _validate_call_type(evm)

    # Must have at least 4 bytes for the selector
    if len(data) < 4:
        charge_gas(evm, GAS_UNKNOWN_SELECTOR)
        evm.output = b"method not supported"
        raise RevertInMonadPrecompile

    selector = bytes(data[:4])

    # Look up selector info
    info = _SELECTOR_INFO.get(selector)
    if info is None:
        charge_gas(evm, GAS_UNKNOWN_SELECTOR)
        evm.output = b"method not supported"
        raise RevertInMonadPrecompile

    gas_cost, is_payable, expected_size = info

    # GAS
    charge_gas(evm, gas_cost)

    # Syscall selectors are always rejected from regular user calls
    if selector in _SYSCALL_SELECTORS:
        raise InvalidParameter

    # Non-payable functions reject nonzero value
    if not is_payable and evm.message.value != 0:
        evm.output = b"value is nonzero"
        raise RevertInMonadPrecompile

    # Validate calldata size
    if len(data) != expected_size:
        evm.output = b"invalid input"
        raise RevertInMonadPrecompile

    # Dispatch
    if selector in _GETTER_SELECTORS:
        handler = _GETTER_HANDLERS[selector]
        handler(evm)  # type: ignore[operator]
    elif selector in _SETTER_SELECTORS:
        # Setter stubs: validate interface rules but do nothing
        # Return empty output (setters don't return data except
        # addValidator which returns a uint64)
        if selector == SELECTOR_ADD_VALIDATOR:
            # addValidator returns validator ID; 0 = no validator created
            evm.output = _abi_encode_uint256(0)
        else:
            evm.output = Bytes(b"")
    elif selector in _SYSCALL_SELECTORS:
        # Syscall stubs: reject non-system calls
        raise InvalidParameter
    else:
        raise InvalidParameter
