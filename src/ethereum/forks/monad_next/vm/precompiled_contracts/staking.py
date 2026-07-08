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

from ethereum.crypto.hash import Hash32
from ethereum.state import Address

from ...blocks import Log
from ...state_tracker import (
    TransactionState,
    create_ether,
    get_account,
    get_storage,
    set_account_balance,
    set_storage,
)
from ...utils.hexadecimal import hex_to_address
from ...vm import Evm
from ...vm.exceptions import InvalidParameter, RevertInMonadPrecompile
from ...vm.gas import charge_gas
from . import STAKING_ADDRESS

# Sender of staking syscall transactions (reward/snapshot/epoch), signed
# with a fixed, publicly known key.
SYSTEM_SENDER = hex_to_address("0x6f49a8f621353f12378d0046e7d7e4b9b249dc9e")

# Account that accumulates priority fees during a block; the end-of-block
# distribution empties it. A plain account, not a precompile.
FEE_DISTRIBUTION_ADDRESS = hex_to_address(
    "0xfee5fee5fee5fee5fee5fee5fee5fee5fee5fee5"
)

# --- Staking state storage layout (account 0x1000) ---
#
# State is stored in the staking account's own storage using the same
# namespaced key scheme as the monad client, so produced fixtures are
# byte-compatible with it. A record's base key is a 32-byte value
# ``ns || key-fields || zero-pad``; field word ``k`` of a record lives
# at ``base_as_uint256 + k``. Small integers are left-aligned in their
# word (raw big-endian struct bytes); u256 amounts occupy the whole word.
#
# Namespaces (byte 0 of a record key):
NS_CONSENSUS_STAKE = 0x04
NS_VAL_ID_SECP = 0x06
NS_VAL_EXECUTION = 0x09
NS_DELEGATOR = 0x0B

# Singleton slots (small keys under the implicit 0x00 namespace):
KEY_EPOCH = 0x01
KEY_LAST_VAL_ID = 0x03
KEY_PROPOSER_VAL_ID = 0x04

# ValExecution record field word offsets.
VE_STAKE = 0
VE_ACC_REWARD_PER_TOKEN = 1
VE_COMMISSION = 2
VE_KEYS = 3  # occupies 3 words
VE_ADDRESS_FLAGS = 6
VE_UNCLAIMED_REWARDS = 7

# Delegator record field word offsets.
DEL_STAKE = 0
DEL_ACC_REWARD_PER_TOKEN = 1
DEL_REWARDS = 2

# ConsensusView record field word offsets.
CV_STAKE = 0
CV_COMMISSION = 1

# Distribution constants.
MON = 10**18
UNIT_BIAS = 10**36
DUST_THRESHOLD = 10**9

_ZERO_ADDRESS = Address(b"\x00" * 20)


def _record_base(ns: int, body: bytes) -> int:
    """Return the uint256 base key for a namespaced storage record."""
    return int.from_bytes((bytes([ns]) + body).ljust(32, b"\x00"), "big")


def _slot(tx_state: TransactionState, key: int) -> int:
    """Read the storage word at integer key ``key`` as an int."""
    return int(
        get_storage(tx_state, STAKING_ADDRESS, U256(key).to_be_bytes32())
    )


def _set_slot(tx_state: TransactionState, key: int, value: int) -> None:
    """Write ``value`` to the storage word at integer key ``key``."""
    set_storage(
        tx_state, STAKING_ADDRESS, U256(key).to_be_bytes32(), U256(value)
    )


def _val_execution_base(val_id: int) -> int:
    return _record_base(NS_VAL_EXECUTION, val_id.to_bytes(8, "big"))


def _consensus_base(val_id: int) -> int:
    return _record_base(NS_CONSENSUS_STAKE, val_id.to_bytes(8, "big"))


def _delegator_base(val_id: int, address: Address) -> int:
    return _record_base(
        NS_DELEGATOR, val_id.to_bytes(8, "big") + bytes(address)
    )


def read_proposer_val_id(tx_state: TransactionState) -> int:
    """Return the proposer validator id set this block (0 if unset)."""
    return _slot(tx_state, KEY_PROPOSER_VAL_ID) >> 192


def write_proposer_val_id(tx_state: TransactionState, val_id: int) -> None:
    """Store the proposer validator id for this block (left-aligned u64)."""
    _set_slot(tx_state, KEY_PROPOSER_VAL_ID, val_id << 192)


def clear_proposer_val_id(tx_state: TransactionState) -> None:
    """Clear the proposer validator id (block prelude)."""
    _set_slot(tx_state, KEY_PROPOSER_VAL_ID, 0)


def read_val_id(tx_state: TransactionState, address: Address) -> int:
    """Resolve a secp address to its validator id (0 if none)."""
    return _slot(tx_state, _record_base(NS_VAL_ID_SECP, bytes(address))) >> 192


def read_consensus_stake(tx_state: TransactionState, val_id: int) -> int:
    """Return the validator's active (consensus) stake for this epoch."""
    return _slot(tx_state, _consensus_base(val_id) + CV_STAKE)


def read_consensus_commission(tx_state: TransactionState, val_id: int) -> int:
    """Return the validator's commission rate for this epoch."""
    return _slot(tx_state, _consensus_base(val_id) + CV_COMMISSION)


def read_auth_address(tx_state: TransactionState, val_id: int) -> Address:
    """Return the validator's auth address (top 20 bytes of address_flags)."""
    word = _slot(tx_state, _val_execution_base(val_id) + VE_ADDRESS_FLAGS)
    return Address((word >> 96).to_bytes(20, "big"))


def validator_exists(tx_state: TransactionState, val_id: int) -> bool:
    """Return whether the validator has a nonzero auth address."""
    return read_auth_address(tx_state, val_id) != _ZERO_ADDRESS


def _add_slot(tx_state: TransactionState, key: int, delta: int) -> None:
    """Add ``delta`` to the storage word at integer key ``key``."""
    _set_slot(tx_state, key, _slot(tx_state, key) + delta)


def read_epoch(tx_state: TransactionState) -> int:
    """Return the current staking epoch."""
    return _slot(tx_state, KEY_EPOCH) >> 192


# keccak256("ValidatorRewarded(uint64,address,uint256,uint64)").
#
# The staking API documents this event only for ``syscallReward``.
# Emitting it on the MIP-11 distribution path (with the distribution
# account as ``from``) is documented in neither MIP-11 nor the staking
# API; it is reproduced here because distribution and the reward syscall
# share the pool-crediting path, which the client instruments with this
# event.
VALIDATOR_REWARDED_TOPIC = Hash32(
    bytes.fromhex(
        "3a420a01486b6b28d6ae89c51f5c3bde3e0e74eecbb646a0c481ccba3aae3754"
    )
)


def _validator_rewarded_log(
    tx_state: TransactionState, val_id: int, from_addr: Address, amount: int
) -> Log:
    """Build the ValidatorRewarded log for a pool reward."""
    return Log(
        address=STAKING_ADDRESS,
        topics=(
            VALIDATOR_REWARDED_TOPIC,
            Hash32(val_id.to_bytes(32, "big")),
            Hash32(bytes(12) + bytes(from_addr)),
        ),
        data=Bytes(
            amount.to_bytes(32, "big")
            + read_epoch(tx_state).to_bytes(32, "big")
        ),
    )


def staking_distribute(
    tx_state: TransactionState,
    val_id: int,
    from_addr: Address,
    amount: int,
    active_stake: int,
) -> Log:
    """
    Distribute ``amount`` to a validator pool (MIP-11
    ``staking_contract.distribute(val_id)``).

    Bump ``accumulated_reward_per_token`` by ``amount * UNIT_BIAS //
    active_stake`` and ``unclaimed_rewards`` by ``amount``, and return
    the ValidatorRewarded log. The matching balance transfer is done by
    the caller (the spec's ``{msg.value}``).
    """
    base = _val_execution_base(val_id)
    _add_slot(
        tx_state,
        base + VE_ACC_REWARD_PER_TOKEN,
        amount * UNIT_BIAS // active_stake,
    )
    _add_slot(tx_state, base + VE_UNCLAIMED_REWARDS, amount)
    return _validator_rewarded_log(tx_state, val_id, from_addr, amount)


def apply_commission_to_auth_account(
    tx_state: TransactionState,
    val_id: int,
    auth: Address,
    amount: int,
) -> None:
    """
    Credit ``amount`` commission to the auth delegator's rewards (MIP-11
    ``staking_contract.apply_commission_to_auth_account(auth)``).

    Storage-only; the balance transfer is done by the caller.
    """
    _add_slot(tx_state, _delegator_base(val_id, auth) + DEL_REWARDS, amount)


# Gas costs per function (from staking spec)
GAS_ADD_VALIDATOR = Uint(505125)
GAS_DELEGATE = Uint(260850)
GAS_UNDELEGATE = Uint(147750)
GAS_WITHDRAW = Uint(68675)
GAS_COMPOUND = Uint(289325)
GAS_CLAIM_REWARDS = Uint(155375)
GAS_CHANGE_COMMISSION = Uint(39475)
GAS_EXTERNAL_REWARD = Uint(66575)
GAS_GET_VALIDATOR = Uint(97200)
GAS_GET_DELEGATOR = Uint(184900)
GAS_GET_WITHDRAWAL_REQUEST = Uint(24300)
GAS_GET_CONSENSUS_VALIDATOR_SET = Uint(814000)
GAS_GET_SNAPSHOT_VALIDATOR_SET = Uint(814000)
GAS_GET_EXECUTION_VALIDATOR_SET = Uint(814000)
GAS_GET_DELEGATIONS = Uint(814000)
GAS_GET_DELEGATORS = Uint(814000)
GAS_GET_EPOCH = Uint(200)
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


def distribute_priority_fees(tx_state: TransactionState) -> None:
    """
    Distribute the distribution account's balance (MIP-11
    ``distribution_account.distribute(block_leader)``).

    The spec models this as a method on the fee5 account called by the
    system at end of block ("no transaction can call it"); it is a direct
    end-of-block call, and the logic lives here because fee5 has no code.
    The client emits a ValidatorRewarded event here to its event stream
    but never into a receipt, so it has no effect on state or the header
    bloom and is dropped. Step numbers follow the spec pseudocode.
    """
    # 1. Load balance and clear it for the block.
    total_balance = int(
        get_account(tx_state, FEE_DISTRIBUTION_ADDRESS).balance
    )
    if total_balance == 0:
        return
    set_account_balance(tx_state, FEE_DISTRIBUTION_ADDRESS, U256(0))

    # 2. Resolve the proposer's validator id.
    # Divergence from the spec: it resolves ``val_id(block_leader)`` from
    # ``block.coinbase``; here the cached ``proposer_val_id`` (set by the
    # reward syscall and cleared each block) is read instead, so a block
    # with no reward syscall burns the balance. Equal for a valid block
    # whose reward names the block leader.
    val_id = read_proposer_val_id(tx_state)

    # Divergence from the spec: it omits these guards; the validator
    # existence and active-set checks are enforced here, both burning the
    # balance.
    if val_id == 0 or not validator_exists(tx_state, val_id):
        return
    active_stake = read_consensus_stake(tx_state, val_id)
    if active_stake == 0:
        return

    # 3-4. Commission on the pool's commission rate.
    # Divergence from the spec's ``val_consensus``: the epoch-boundary
    # snapshot view is not modeled here (equal off epoch boundaries).
    commission_rate = read_consensus_commission(tx_state, val_id)
    commission_amount = total_balance * commission_rate // MON
    distribute_amount = total_balance - commission_amount

    # 5. Credit commission to the auth delegator (always, before the dust
    #    check). The drained balance moves into the staking contract.
    create_ether(tx_state, STAKING_ADDRESS, U256(commission_amount))
    apply_commission_to_auth_account(
        tx_state,
        val_id,
        read_auth_address(tx_state, val_id),
        commission_amount,
    )
    if distribute_amount < DUST_THRESHOLD:
        return

    # 6. Distribute the remainder to the validator pool. The returned log
    #    is dropped (see the docstring); only the pool credit matters.
    create_ether(tx_state, STAKING_ADDRESS, U256(distribute_amount))
    staking_distribute(
        tx_state,
        val_id,
        FEE_DISTRIBUTION_ADDRESS,
        distribute_amount,
        active_stake,
    )


def _syscall_reward(evm: Evm) -> None:
    """
    Handle the reward syscall.

    Mint the reward into the pool, credit commission to the auth
    delegator, and credit the remainder to the proposer pool the same way
    ``distribute_priority_fees`` does, then set ``proposer_val_id``. The
    reward may be zero (proposer set with no block reward); the credits
    are then no-ops, but ValidatorRewarded is emitted unconditionally to
    match the client. This log lands in the syscall tx's receipt and thus
    the block bloom, since the reward syscall is a transaction (unlike the
    end-of-block ``distribute_priority_fees``).
    """
    tx_state = evm.message.tx_env.state
    data = evm.message.data
    # calldata: selector(4) + abi address (32, right-aligned 20 bytes)
    author = Address(bytes(data[16:36]))
    val_id = read_val_id(tx_state, author)
    if val_id == 0:
        evm.output = b"not in validator set"
        raise RevertInMonadPrecompile
    active_stake = read_consensus_stake(tx_state, val_id)
    if active_stake == 0:
        evm.output = b"not in validator set"
        raise RevertInMonadPrecompile

    raw_reward = int(evm.message.value)
    create_ether(tx_state, STAKING_ADDRESS, U256(raw_reward))
    commission_rate = read_consensus_commission(tx_state, val_id)
    commission_amount = raw_reward * commission_rate // MON
    apply_commission_to_auth_account(
        tx_state,
        val_id,
        read_auth_address(tx_state, val_id),
        commission_amount,
    )
    log = staking_distribute(
        tx_state,
        val_id,
        SYSTEM_SENDER,
        raw_reward - commission_amount,
        active_stake,
    )
    evm.logs = evm.logs + (log,)

    write_proposer_val_id(tx_state, val_id)


# All known selectors mapped to their gas cost and whether they are
# payable (accept msg.value > 0)
_SELECTOR_INFO: dict[bytes, tuple[Uint, bool, int]] = {
    # (gas_cost, is_payable, expected_data_size)
    # Setters
    # addValidator(bytes,bytes,bytes) - 4+32*3 offsets+32*3 lengths
    SELECTOR_ADD_VALIDATOR: (GAS_ADD_VALIDATOR, True, 196),
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
    evm.output = b"\x00" * 32 * 18


def _handle_get_delegator(evm: Evm) -> None:
    """
    Handle getDelegator(uint64,address) call.

    Return stub: a zeroed-out delegator structure.
    """
    evm.output = b"\x00" * 32 * 7


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
    selector = bytes(data[:4]) if len(data) >= 4 else b""

    # Staking syscalls arrive as transactions from the system sender.
    if selector == SELECTOR_SYSCALL_REWARD and (
        evm.message.caller == SYSTEM_SENDER
    ):
        charge_gas(evm, GAS_UNKNOWN_SELECTOR)
        _syscall_reward(evm)
        return

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
        evm.output = b"method not supported"
        raise RevertInMonadPrecompile

    # Non-payable functions reject nonzero value
    if not is_payable and evm.message.value != 0:
        evm.output = b"value is nonzero"
        raise RevertInMonadPrecompile

    # Validate calldata size (addValidator defers to its handler)
    if selector != SELECTOR_ADD_VALIDATOR:
        if len(data) < expected_size:
            evm.output = b"input too short"
            raise RevertInMonadPrecompile
        # Extra calldata bytes only affect selectors accepting data.
        if expected_size > 4 and len(data) > expected_size:
            evm.output = b"invalid input"
            raise RevertInMonadPrecompile

    # Dispatch
    if selector in _GETTER_SELECTORS:
        handler = _GETTER_HANDLERS[selector]
        handler(evm)  # type: ignore[operator]
    elif selector in _SETTER_SELECTORS:
        if selector == SELECTOR_ADD_VALIDATOR:
            evm.output = b"length mismatch"
            raise RevertInMonadPrecompile
        elif selector == SELECTOR_DELEGATE and evm.message.value != U256(0):
            evm.output = b"unknown validator"
            raise RevertInMonadPrecompile
        elif selector in (
            SELECTOR_DELEGATE,
            SELECTOR_UNDELEGATE,
            SELECTOR_COMPOUND,
            SELECTOR_CLAIM_REWARDS,
        ):
            evm.output = _abi_encode_bool(True)
        elif selector in (
            SELECTOR_CHANGE_COMMISSION,
            SELECTOR_EXTERNAL_REWARD,
        ):
            evm.output = b"unknown validator"
            raise RevertInMonadPrecompile
        elif selector == SELECTOR_WITHDRAW:
            evm.output = b"unknown withdrawal id"
            raise RevertInMonadPrecompile
        else:
            raise AssertionError(f"unhandled setter: {selector.hex()}")
    else:
        raise InvalidParameter
