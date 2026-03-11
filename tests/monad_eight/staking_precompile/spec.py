"""Define staking precompile specification constants."""

from dataclasses import dataclass

from execution_testing import Address


@dataclass(frozen=True)
class ReferenceSpec:
    """Define the reference spec version and git path."""

    git_path: str
    version: str


ref_spec_staking = ReferenceSpec("staking/staking-precompile", "main")

# Error messages returned as raw ASCII revert data
ERROR_METHOD_NOT_SUPPORTED = "method not supported"
ERROR_INVALID_INPUT = "invalid input"
ERROR_VALUE_NONZERO = "value is nonzero"

# Precompile address for staking
STAKING_PRECOMPILE = Address(0x1000)

# Function selectors - Setters
SELECTOR_ADD_VALIDATOR = 0xF145204C
SELECTOR_DELEGATE = 0x84994FEC
SELECTOR_UNDELEGATE = 0x5CF41514
SELECTOR_WITHDRAW = 0xAED2EE73
SELECTOR_COMPOUND = 0xB34FEA67
SELECTOR_CLAIM_REWARDS = 0xA76E2CA5
SELECTOR_CHANGE_COMMISSION = 0x9BDCC3C8
SELECTOR_EXTERNAL_REWARD = 0xE4B3303B

# Function selectors - Getters
SELECTOR_GET_VALIDATOR = 0x2B6D639A
SELECTOR_GET_DELEGATOR = 0x573C1CE0
SELECTOR_GET_WITHDRAWAL_REQUEST = 0x56FA2045
SELECTOR_GET_CONSENSUS_VALIDATOR_SET = 0xFB29B729
SELECTOR_GET_SNAPSHOT_VALIDATOR_SET = 0xDE66A368
SELECTOR_GET_EXECUTION_VALIDATOR_SET = 0x7CB074DF
SELECTOR_GET_DELEGATIONS = 0x4FD66050
SELECTOR_GET_DELEGATORS = 0xA0843A26
SELECTOR_GET_EPOCH = 0x757991A8
SELECTOR_GET_PROPOSER_VAL_ID = 0xFBACB0BE

# Function selectors - Syscalls
SELECTOR_SYSCALL_ON_EPOCH_CHANGE = 0x1D4E9F02
SELECTOR_SYSCALL_REWARD = 0x791BDCF3
SELECTOR_SYSCALL_SNAPSHOT = 0x157EEB21

# Gas costs per function
GAS_ADD_VALIDATOR = 505125
GAS_DELEGATE = 260850
GAS_UNDELEGATE = 147750
GAS_WITHDRAW = 68675
GAS_COMPOUND = 285050
GAS_CLAIM_REWARDS = 155375
GAS_CHANGE_COMMISSION = 39475
GAS_EXTERNAL_REWARD = 62300
GAS_GET_VALIDATOR = 97200
GAS_GET_DELEGATOR = 184900
GAS_GET_WITHDRAWAL_REQUEST = 24300
GAS_GET_CONSENSUS_VALIDATOR_SET = 814000
GAS_GET_SNAPSHOT_VALIDATOR_SET = 814000
GAS_GET_EXECUTION_VALIDATOR_SET = 814000
GAS_GET_DELEGATIONS = 814000
GAS_GET_DELEGATORS = 814000
GAS_GET_EPOCH = 16200
GAS_GET_PROPOSER_VAL_ID = 100
GAS_UNKNOWN_SELECTOR = 40000

# Expected calldata sizes (selector + ABI-encoded params)
CALLDATA_SIZE_ADD_VALIDATOR = 100  # 4 + 32*3 (three offset words)
CALLDATA_SIZE_DELEGATE = 36  # 4 + 32 (uint64)
CALLDATA_SIZE_UNDELEGATE = 100  # 4 + 32*3
CALLDATA_SIZE_WITHDRAW = 68  # 4 + 32*2
CALLDATA_SIZE_COMPOUND = 36
CALLDATA_SIZE_CLAIM_REWARDS = 36
CALLDATA_SIZE_CHANGE_COMMISSION = 68
CALLDATA_SIZE_EXTERNAL_REWARD = 36
CALLDATA_SIZE_GET_VALIDATOR = 36
CALLDATA_SIZE_GET_DELEGATOR = 68
CALLDATA_SIZE_GET_WITHDRAWAL_REQUEST = 100
CALLDATA_SIZE_GET_CONSENSUS_VALIDATOR_SET = 36
CALLDATA_SIZE_GET_SNAPSHOT_VALIDATOR_SET = 36
CALLDATA_SIZE_GET_EXECUTION_VALIDATOR_SET = 36
CALLDATA_SIZE_GET_DELEGATIONS = 68
CALLDATA_SIZE_GET_DELEGATORS = 68
CALLDATA_SIZE_GET_EPOCH = 4
CALLDATA_SIZE_GET_PROPOSER_VAL_ID = 4

# Payable functions (accept msg.value > 0)
PAYABLE_SELECTORS = frozenset(
    {
        SELECTOR_ADD_VALIDATOR,
        SELECTOR_DELEGATE,
        SELECTOR_EXTERNAL_REWARD,
    }
)


@dataclass(frozen=True)
class FunctionInfo:
    """Metadata about a staking precompile function."""

    selector: int
    gas_cost: int
    calldata_size: int
    is_payable: bool
    name: str
    return_size: int
    first_return_word: int


# All functions with their metadata
ALL_FUNCTIONS = [
    # Setters
    FunctionInfo(
        SELECTOR_ADD_VALIDATOR,
        GAS_ADD_VALIDATOR,
        CALLDATA_SIZE_ADD_VALIDATOR,
        True,
        "addValidator",
        32,  # returns uint64
        0,  # validator id = 0 (no validator created)
    ),
    FunctionInfo(
        SELECTOR_DELEGATE,
        GAS_DELEGATE,
        CALLDATA_SIZE_DELEGATE,
        True,
        "delegate",
        0,
        0,
    ),
    FunctionInfo(
        SELECTOR_UNDELEGATE,
        GAS_UNDELEGATE,
        CALLDATA_SIZE_UNDELEGATE,
        False,
        "undelegate",
        0,
        0,
    ),
    FunctionInfo(
        SELECTOR_WITHDRAW,
        GAS_WITHDRAW,
        CALLDATA_SIZE_WITHDRAW,
        False,
        "withdraw",
        0,
        0,
    ),
    FunctionInfo(
        SELECTOR_COMPOUND,
        GAS_COMPOUND,
        CALLDATA_SIZE_COMPOUND,
        False,
        "compound",
        0,
        0,
    ),
    FunctionInfo(
        SELECTOR_CLAIM_REWARDS,
        GAS_CLAIM_REWARDS,
        CALLDATA_SIZE_CLAIM_REWARDS,
        False,
        "claimRewards",
        0,
        0,
    ),
    FunctionInfo(
        SELECTOR_CHANGE_COMMISSION,
        GAS_CHANGE_COMMISSION,
        CALLDATA_SIZE_CHANGE_COMMISSION,
        False,
        "changeCommission",
        0,
        0,
    ),
    FunctionInfo(
        SELECTOR_EXTERNAL_REWARD,
        GAS_EXTERNAL_REWARD,
        CALLDATA_SIZE_EXTERNAL_REWARD,
        True,
        "externalReward",
        0,
        0,
    ),
    # Getters
    FunctionInfo(
        SELECTOR_GET_VALIDATOR,
        GAS_GET_VALIDATOR,
        CALLDATA_SIZE_GET_VALIDATOR,
        False,
        "getValidator",
        32 * 20,  # 20 zero words
        0,
    ),
    FunctionInfo(
        SELECTOR_GET_DELEGATOR,
        GAS_GET_DELEGATOR,
        CALLDATA_SIZE_GET_DELEGATOR,
        False,
        "getDelegator",
        32 * 10,  # 10 zero words
        0,
    ),
    FunctionInfo(
        SELECTOR_GET_WITHDRAWAL_REQUEST,
        GAS_GET_WITHDRAWAL_REQUEST,
        CALLDATA_SIZE_GET_WITHDRAWAL_REQUEST,
        False,
        "getWithdrawalRequest",
        32 * 3,  # 3 zero words
        0,
    ),
    FunctionInfo(
        SELECTOR_GET_CONSENSUS_VALIDATOR_SET,
        GAS_GET_CONSENSUS_VALIDATOR_SET,
        CALLDATA_SIZE_GET_CONSENSUS_VALIDATOR_SET,
        False,
        "getConsensusValidatorSet",
        32 * 4,  # done + cursor + offset + length
        1,  # done=true
    ),
    FunctionInfo(
        SELECTOR_GET_SNAPSHOT_VALIDATOR_SET,
        GAS_GET_SNAPSHOT_VALIDATOR_SET,
        CALLDATA_SIZE_GET_SNAPSHOT_VALIDATOR_SET,
        False,
        "getSnapshotValidatorSet",
        32 * 4,
        1,
    ),
    FunctionInfo(
        SELECTOR_GET_EXECUTION_VALIDATOR_SET,
        GAS_GET_EXECUTION_VALIDATOR_SET,
        CALLDATA_SIZE_GET_EXECUTION_VALIDATOR_SET,
        False,
        "getExecutionValidatorSet",
        32 * 4,
        1,
    ),
    FunctionInfo(
        SELECTOR_GET_DELEGATIONS,
        GAS_GET_DELEGATIONS,
        CALLDATA_SIZE_GET_DELEGATIONS,
        False,
        "getDelegations",
        32 * 4,
        1,  # done=true
    ),
    FunctionInfo(
        SELECTOR_GET_DELEGATORS,
        GAS_GET_DELEGATORS,
        CALLDATA_SIZE_GET_DELEGATORS,
        False,
        "getDelegators",
        32 * 4,
        1,  # done=true
    ),
    FunctionInfo(
        SELECTOR_GET_EPOCH,
        GAS_GET_EPOCH,
        CALLDATA_SIZE_GET_EPOCH,
        False,
        "getEpoch",
        32 * 2,  # epoch + inBoundaryDelay
        0,  # epoch=0 (no epochs occurred)
    ),
    FunctionInfo(
        SELECTOR_GET_PROPOSER_VAL_ID,
        GAS_GET_PROPOSER_VAL_ID,
        CALLDATA_SIZE_GET_PROPOSER_VAL_ID,
        False,
        "getProposerValId",
        32,  # uint64
        0,  # validator_id=0 (no validators registered)
    ),
]

GETTER_FUNCTIONS = [f for f in ALL_FUNCTIONS if f.name.startswith("get")]
SETTER_FUNCTIONS = [f for f in ALL_FUNCTIONS if not f.name.startswith("get")]
PAYABLE_FUNCTIONS = [f for f in ALL_FUNCTIONS if f.is_payable]
NON_PAYABLE_FUNCTIONS = [f for f in ALL_FUNCTIONS if not f.is_payable]

# Representative subset to limit parametrization explosion
REPRESENTATIVE_FUNCTIONS = [
    f
    for f in ALL_FUNCTIONS
    if f.name in ("delegate", "undelegate", "getEpoch", "getValidator")
]

# Lookup table: selector -> FunctionInfo
FUNC_BY_SELECTOR = {f.selector: f for f in ALL_FUNCTIONS}
