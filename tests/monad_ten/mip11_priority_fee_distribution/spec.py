"""
Constants and helpers for MIP-11 priority fee distribution tests.

State is modeled with the same namespaced storage layout as the staking
precompile, so fixtures match it.

Spec: https://mips.monad.xyz/MIPs/MIP-11
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from execution_testing import EOA, Account, Address, Transaction

STAKING_PRECOMPILE = Address(0x1000)
FEE_DISTRIBUTION = Address(0xFEE5FEE5FEE5FEE5FEE5FEE5FEE5FEE5FEE5FEE5)

# System sender that signs staking syscall transactions, with a fixed,
# publicly known signing key.
SYSTEM_SENDER = Address(0x6F49A8F621353F12378D0046E7D7E4B9B249DC9E)
SYSTEM_KEY = 0xB0358E6D701A955D9926676F227E40172763296B317FF554E49CDF2C2C35F8A7

REWARD_SELECTOR = bytes.fromhex("791bdcf3")

MON = 10**18
UNIT_BIAS = 10**36
DUST_THRESHOLD = 10**9

# Block base fee the framework uses for the first block; priority fee per
# gas is derived against it so a gas-paid fee is reproduced exactly.
BASE_FEE = 7

# keccak256(b"") — EXTCODEHASH of an existing account with no code.
EMPTY_CODE_HASH = (
    0xC5D2460186F7233C927E7DB2DCC703C0E500B653CA82273B7BFAD8045D85A470
)

# Storage namespaces and offsets (must match the staking precompile).
NS_CONSENSUS_STAKE = 0x04
NS_VAL_ID_SECP = 0x06
NS_VAL_EXECUTION = 0x09
NS_DELEGATOR = 0x0B
KEY_PROPOSER_VAL_ID = 0x04
VE_ACC_REWARD_PER_TOKEN = 1
VE_ADDRESS_FLAGS = 6
VE_UNCLAIMED_REWARDS = 7
CV_STAKE = 0
CV_COMMISSION = 1
DEL_REWARDS = 2


def _base(ns: int, body: bytes) -> int:
    """Return the uint256 base key of a namespaced storage record."""
    return int.from_bytes((bytes([ns]) + body).ljust(32, b"\x00"), "big")


def val_id_slot(address: Address) -> int:
    """Storage slot of the ``val_id`` mapping for a secp address."""
    return _base(NS_VAL_ID_SECP, bytes(address))


def val_exec_slot(val_id: int, offset: int) -> int:
    """Storage slot of a ValExecution record field."""
    return _base(NS_VAL_EXECUTION, val_id.to_bytes(8, "big")) + offset


def consensus_slot(val_id: int, offset: int) -> int:
    """Storage slot of a ConsensusView record field."""
    return _base(NS_CONSENSUS_STAKE, val_id.to_bytes(8, "big")) + offset


def delegator_slot(val_id: int, address: Address, offset: int) -> int:
    """Storage slot of a Delegator record field."""
    body = val_id.to_bytes(8, "big") + bytes(address)
    return _base(NS_DELEGATOR, body) + offset


@dataclass
class Validator:
    """A staking validator for test setup."""

    val_id: int
    auth: Address
    stake: int
    commission: int = 0


def staking_storage(validators: Sequence[Validator]) -> dict[int, int]:
    """Return the staking-account storage seeding the given validators."""
    storage: dict[int, int] = {}
    for v in validators:
        storage[val_id_slot(v.auth)] = v.val_id << 192
        storage[val_exec_slot(v.val_id, VE_ADDRESS_FLAGS)] = (
            int.from_bytes(bytes(v.auth), "big") << 96
        )
        storage[consensus_slot(v.val_id, CV_STAKE)] = v.stake
        if v.commission:
            storage[consensus_slot(v.val_id, CV_COMMISSION)] = v.commission
    return storage


def staking_account(validators: Sequence[Validator]) -> Account:
    """Build the staking account pre-seeded with the given validators."""
    return Account(nonce=1, storage=staking_storage(validators))


def reward_tx(
    author: Address, *, nonce: int = 0, value: int = 0
) -> Transaction:
    """
    Return a reward-syscall system transaction naming ``author``.

    Signed by the public system key so its sender recovers to
    ``SYSTEM_SENDER``. System transactions declare no gas, so both the
    gas limit and price are zero.
    """
    return Transaction(
        ty=0,
        nonce=nonce,
        gas_limit=0,
        gas_price=0,
        to=STAKING_PRECOMPILE,
        value=value,
        data=REWARD_SELECTOR + bytes(12) + bytes(author),
        sender=EOA(key=SYSTEM_KEY),
    )


@dataclass
class Distribution:
    """Storage/balance deltas of distributing ``fee`` to ``validator``."""

    staking_balance: int
    storage: dict[int, int] = field(default_factory=dict)


def distribution(validator: Validator, fee: int) -> Distribution:
    """
    Return the staking-account deltas of distributing ``fee``.

    Mirrors ``distribute_priority_fees``: commission to the auth
    delegator's pool accumulator (via the reward path in these tests the
    auth is the sole recipient of commission), remainder to the pool
    accumulator, sub-dust remainder burned.
    """
    commission = fee * validator.commission // MON
    del_reward = fee - commission
    result = Distribution(staking_balance=commission)
    if commission > 0:
        result.storage[
            delegator_slot(validator.val_id, validator.auth, DEL_REWARDS)
        ] = commission
    if del_reward >= DUST_THRESHOLD:
        result.staking_balance += del_reward
        result.storage[
            val_exec_slot(validator.val_id, VE_ACC_REWARD_PER_TOKEN)
        ] = del_reward * UNIT_BIAS // validator.stake
        result.storage[
            val_exec_slot(validator.val_id, VE_UNCLAIMED_REWARDS)
        ] = del_reward
    return result
