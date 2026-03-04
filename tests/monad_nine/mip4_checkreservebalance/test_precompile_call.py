"""
Tests for reserve balance precompile call behavior.

Tests cover:
- Input validation (selector, size)
- Gas consumption and out-of-gas behavior
- Different call opcodes (CALL, DELEGATECALL, CALLCODE, STATICCALL)
- Value transfer with calls
"""

from enum import Enum, auto, unique

import pytest
from execution_testing import (
    Account,
    Alloc,
    Block,
    BlockchainTestFiller,
    Bytecode,
    Op,
    StateTestFiller,
    Transaction,
)
from execution_testing.forks.helpers import Fork

from ..mip3_linear_memory.spec import Spec as SpecMIP3
from .helpers import (
    SELECTOR_SETUP,
    generous_gas,
)
from .spec import (
    Spec,
    ref_spec_mip4,
)

REFERENCE_SPEC_GIT_PATH = ref_spec_mip4.git_path
REFERENCE_SPEC_VERSION = ref_spec_mip4.version

slot_code_worked = 0x1
value_code_worked = 0x1234
slot_call_success = 0x2
slot_return_size = 0x3
slot_return_value = 0x4
slot_ret_buffer_value = 0x5
slot_all_gas_consumed = 0x6


@unique
class CallScenario(Enum):
    """Precompile call scenarios for parametrized tests."""

    SUCCESS = auto()
    WRONG_SELECTOR = auto()
    SHORT_CALLDATA = auto()
    EXTRA_CALLDATA = auto()
    NOT_CALL = auto()
    NONZERO_VALUE = auto()
    LOW_GAS = auto()

    @property
    def should_succeed(self) -> bool:
        """Return whether this scenario results in a successful call."""
        return self == CallScenario.SUCCESS

    def error_message(self, gas: int = Spec.GAS_COST) -> bytes | None:
        """Return raw ASCII error bytes for this scenario, or None."""
        match self:
            case (
                CallScenario.SUCCESS
                | CallScenario.NOT_CALL
                | CallScenario.LOW_GAS
            ):
                return None
            case CallScenario.WRONG_SELECTOR | CallScenario.SHORT_CALLDATA:
                if gas >= Spec.GAS_ERROR_THRESHOLD:
                    return Spec.ERROR_METHOD_NOT_SUPPORTED.encode()
                return None
            case CallScenario.EXTRA_CALLDATA:
                return Spec.ERROR_INPUT_INVALID.encode()
            case CallScenario.NONZERO_VALUE:
                return Spec.ERROR_VALUE_NONZERO.encode()

    @property
    def check_priority(self) -> int:
        """Return precompile check priority."""
        if self == CallScenario.SUCCESS:
            raise AssertionError("SUCCESS has no check priority")
        order = [
            CallScenario.NOT_CALL,
            CallScenario.LOW_GAS,
            CallScenario.SHORT_CALLDATA,
            CallScenario.WRONG_SELECTOR,
            CallScenario.NONZERO_VALUE,
            CallScenario.EXTRA_CALLDATA,
        ]

        return order.index(self)


def call_code(
    *scenarios: CallScenario,
    gas: int | Bytecode = Spec.GAS_COST,
    ret_offset: int = 0,
    ret_size: int = 0,
) -> Bytecode:
    """
    Generate setup + call bytecode for one or more combined scenarios.

    Both the correct and wrong selectors are always written to memory
    at separate offsets. The args_offset is chosen based on whether
    WRONG_SELECTOR is among the scenarios.
    """
    scenario_set = set(scenarios)

    # Memory layout: non-overlapping buffers
    #   MSTORE(0, selector)    -> correct selector at mem[28:32]
    #   MSTORE(32, 0xDEADBEEF) -> wrong selector at mem[60:64]
    correct_sel_args_offset = 28
    wrong_sel_args_offset = 60

    setup: Bytecode = SELECTOR_SETUP + Op.MSTORE(32, 0xDEADBEEF)

    if CallScenario.WRONG_SELECTOR in scenario_set:
        args_offset = wrong_sel_args_offset
    else:
        args_offset = correct_sel_args_offset

    if CallScenario.SHORT_CALLDATA in scenario_set:
        args_size = 3
    elif CallScenario.EXTRA_CALLDATA in scenario_set:
        args_size = 5
    else:
        args_size = 4
    if ret_size > 0:
        assert ret_offset >= args_offset + args_size, (
            "ret buffer must come after args buffer"
        )

    if CallScenario.LOW_GAS in scenario_set:
        gas = 99

    if CallScenario.NONZERO_VALUE in scenario_set:
        value = 1
    else:
        value = 0

    if CallScenario.NOT_CALL in scenario_set:
        opcode = Op.CALLCODE
    else:
        opcode = Op.CALL

    return setup + opcode(
        gas=gas,
        address=Spec.RESERVE_BALANCE_PRECOMPILE,
        value=value,
        args_offset=args_offset,
        args_size=args_size,
        ret_offset=ret_offset,
        ret_size=ret_size,
    )


pytestmark = [
    pytest.mark.valid_from("MONAD_NINE"),
    pytest.mark.pre_alloc_group(
        "mip4_checkreservebalance_tests",
        reason="Tests reserve balance precompile",
    ),
]


def _mload_of(msg: bytes) -> int:
    """
    Compute the MLOAD uint256 value after a raw error message is written
    to a zero-initialised memory slot. CALL copies msg to mem[offset],
    MLOAD then reads 32 bytes big-endian with trailing zeros.
    """
    return int.from_bytes((msg + b"\x00" * 32)[:32], "big")


@pytest.mark.parametrize(
    "input_size",
    [
        pytest.param(0, id="empty"),
        pytest.param(1, id="one_byte"),
        pytest.param(2, id="two_bytes"),
        pytest.param(3, id="three_bytes"),
        pytest.param(4, id="exact"),
        pytest.param(5, id="one_extra"),
        pytest.param(8, id="four_extra"),
        pytest.param(32, id="one_word"),
        pytest.param(1028, id="large"),
        pytest.param(SpecMIP3.MAX_TX_MEMORY_USAGE, id="max"),
    ],
)
@pytest.mark.parametrize("gas", [Spec.GAS_COST, Spec.GAS_ERROR_THRESHOLD])
def test_input_size(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    input_size: int,
    gas: int,
    fork: Fork,
) -> None:
    """
    Test precompile behavior with various input sizes.

    Calldata must be exactly 4 bytes (the selector). Any other size reverts.
    """
    # Store selector at mem[0:4], extra bytes at mem[4:]
    contract = (
        Op.MSTORE(0, Spec.DIPPED_INTO_RESERVE_SELECTOR + b"\xff" * 28)
        + Op.SSTORE(
            slot_call_success,
            Op.CALL(
                gas=gas,
                address=Spec.RESERVE_BALANCE_PRECOMPILE,
                args_offset=0,
                args_size=input_size,
                ret_offset=0,
                ret_size=32,
            ),
        )
        + Op.SSTORE(slot_return_size, Op.RETURNDATASIZE)
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    contract_address = pre.deploy_contract(contract)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    should_succeed = input_size == 4

    if should_succeed:
        expected_return_size = 32
    elif input_size > 4:
        expected_return_size = len(Spec.ERROR_INPUT_INVALID)
    elif gas >= Spec.GAS_ERROR_THRESHOLD:
        expected_return_size = len(Spec.ERROR_METHOD_NOT_SUPPORTED)
    else:
        expected_return_size = 0

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_call_success: 1 if should_succeed else 0,
                    slot_return_size: expected_return_size,
                    slot_code_worked: value_code_worked,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param(Spec.DIPPED_INTO_RESERVE_SELECTOR, id="correct_selector"),
        pytest.param(0x00000000, id="zero_selector"),
        pytest.param(0xFFFFFFFF, id="max_selector"),
        pytest.param(0x3A61584F, id="off_by_one"),
    ],
)
@pytest.mark.parametrize("gas", [Spec.GAS_COST, Spec.GAS_ERROR_THRESHOLD])
def test_selector(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    selector: int,
    gas: int,
    fork: Fork,
) -> None:
    """
    Test precompile behavior with various function selectors.

    Correct selector succeeds, wrong selectors cause revert.
    """
    contract = (
        Op.PUSH4(selector)
        + Op.PUSH1(0)
        + Op.MSTORE  # Selector at mem[28:32]
        + Op.SSTORE(
            slot_call_success,
            Op.CALL(
                gas=gas,
                address=Spec.RESERVE_BALANCE_PRECOMPILE,
                args_offset=28,
                args_size=4,
                ret_offset=0,
                ret_size=32,
            ),
        )
        + Op.SSTORE(slot_return_size, Op.RETURNDATASIZE)
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    contract_address = pre.deploy_contract(contract)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    should_succeed = selector == Spec.DIPPED_INTO_RESERVE_SELECTOR

    if should_succeed:
        expected_return_size = 32
    elif gas >= Spec.GAS_ERROR_THRESHOLD:
        expected_return_size = len(Spec.ERROR_METHOD_NOT_SUPPORTED)
    else:
        expected_return_size = 0

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_call_success: 1 if should_succeed else 0,
                    slot_return_size: expected_return_size,
                    slot_code_worked: value_code_worked,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )


@pytest.mark.parametrize("enough_gas", [True, False])
def test_gas(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    enough_gas: bool,
) -> None:
    """
    Test that precompile consumes expected gas.
    """
    gas_warm_sload = fork.gas_costs().G_WARM_SLOAD
    gas = gas_warm_sload if enough_gas else gas_warm_sload - 1

    contract = (
        SELECTOR_SETUP
        + Op.SSTORE(
            slot_call_success,
            Op.CALL(
                gas=gas,
                address=Spec.RESERVE_BALANCE_PRECOMPILE,
                args_offset=28,
                args_size=4,
                ret_offset=0,
                ret_size=32,
            ),
        )
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    contract_address = pre.deploy_contract(contract)

    tx = Transaction(
        to=contract_address,
        sender=pre.fund_eoa(),
        gas_limit=generous_gas(fork),
    )

    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_call_success: 1 if enough_gas else 0,
                    slot_code_worked: value_code_worked,
                }
            )
        },
        tx=tx,
    )


@pytest.mark.with_all_call_opcodes()
def test_call_opcodes(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    call_opcode: Op,
    fork: Fork,
) -> None:
    """
    Test that precompile must be invoked via CALL only.

    STATICCALL, DELEGATECALL, and CALLCODE must revert.
    """
    contract = (
        SELECTOR_SETUP
        + Op.SSTORE(
            slot_call_success,
            call_opcode(
                gas=10000,
                address=Spec.RESERVE_BALANCE_PRECOMPILE,
                args_offset=28,
                args_size=4,
                ret_offset=0,
                ret_size=32,
            ),
        )
        + Op.SSTORE(slot_return_size, Op.RETURNDATASIZE)
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    contract_address = pre.deploy_contract(contract)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    should_succeed = call_opcode == Op.CALL

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_call_success: 1 if should_succeed else 0,
                    slot_return_size: 32 if should_succeed else 0,
                    slot_code_worked: value_code_worked,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )


@pytest.mark.parametrize(
    "scenario",
    [s for s in CallScenario if s != CallScenario.LOW_GAS],
)
# Ensures that the reverts are independent of gas sent.
@pytest.mark.parametrize("gas", [Spec.GAS_COST, 10000, 40000])
def test_revert_returns(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
    scenario: CallScenario,
    gas: int,
) -> None:
    """
    Test return data from the precompile on success and on each revert reason.
    """
    # Memory layout: non-overlapping buffers; args are at mem[0:64].
    ret_offset = 96
    rdc_offset = 128

    contract = (
        Op.SSTORE(
            slot_call_success,
            call_code(scenario, gas=gas, ret_offset=ret_offset, ret_size=32),
        )
        + Op.SSTORE(slot_return_size, Op.RETURNDATASIZE)
        + Op.SSTORE(slot_ret_buffer_value, Op.MLOAD(ret_offset))
        + Op.RETURNDATACOPY(rdc_offset, 0, Op.RETURNDATASIZE)
        + Op.SSTORE(slot_return_value, Op.MLOAD(rdc_offset))
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    contract_address = pre.deploy_contract(contract, balance=1)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    err = scenario.error_message(gas)
    expected_return_size = (
        32 if scenario.should_succeed else (len(err) if err else 0)
    )
    # On success the precompile returns U256(false)=0; on failure with a
    # message, CALL copies err to mem[ret_offset] (zero-initialised).
    expected_mload = 0 if err is None else _mload_of(err)

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_call_success: 1 if scenario.should_succeed else 0,
                    slot_return_size: expected_return_size,
                    slot_ret_buffer_value: expected_mload,
                    slot_return_value: expected_mload,
                    slot_code_worked: value_code_worked,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )


@pytest.mark.parametrize(
    "scenario",
    [s for s in CallScenario if s != CallScenario.LOW_GAS],
)
def test_revert_consumes_all_gas(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
    scenario: CallScenario,
) -> None:
    """
    Test that precompile reverts consume all gas provided to the call frame.
    """
    gas_limit = generous_gas(fork)
    gas_threshold = gas_limit // 64

    contract = (
        Op.SSTORE(slot_code_worked, value_code_worked)
        + Op.SSTORE(slot_call_success, 1)
        + Op.SSTORE(slot_all_gas_consumed, 1)
        + Op.SSTORE(slot_call_success, call_code(scenario, gas=Op.GAS))
        + Op.SSTORE(slot_all_gas_consumed, Op.LT(Op.GAS, gas_threshold))
    )
    contract_address = pre.deploy_contract(contract, balance=1)

    tx = Transaction(
        gas_limit=gas_limit,
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_code_worked: value_code_worked,
                    slot_call_success: 1 if scenario.should_succeed else 0,
                    slot_all_gas_consumed: 0 if scenario.should_succeed else 1,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )


@pytest.mark.parametrize("value", [0, 1])
def test_call_with_value(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    value: int,
    fork: Fork,
) -> None:
    """
    Test that sending value with CALL causes the call to revert.
    """
    contract = (
        SELECTOR_SETUP
        + Op.SSTORE(
            slot_call_success,
            Op.CALL(
                gas=10000,
                address=Spec.RESERVE_BALANCE_PRECOMPILE,
                value=value,
                args_offset=28,
                args_size=4,
                ret_offset=0,
                ret_size=32,
            ),
        )
        + Op.SSTORE(slot_return_size, Op.RETURNDATASIZE)
        + Op.SSTORE(slot_return_value, Op.MLOAD(0))
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    contract_address = pre.deploy_contract(contract, balance=value)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    should_succeed = value == 0

    if should_succeed:
        # Precompile returns U256(false)=0; ret_offset=0 overwrites the
        # selector that SELECTOR_SETUP wrote.
        expected_return_value = 0
    else:
        # CALL copies error msg (16 bytes) to mem[0:16]; mem[28:32] still
        # holds the selector written by SELECTOR_SETUP before the call.
        err = Spec.ERROR_VALUE_NONZERO.encode()
        sel = Spec.DIPPED_INTO_RESERVE_SELECTOR
        expected_return_value = int.from_bytes(err + b"\x00" * 12 + sel, "big")

    storage: dict[int, int] = {
        slot_call_success: 1 if should_succeed else 0,
        slot_return_size: 32
        if should_succeed
        else len(Spec.ERROR_VALUE_NONZERO),
        slot_return_value: expected_return_value,
        slot_code_worked: value_code_worked,
    }

    post = {
        contract_address: Account(storage=storage),
    }

    blockchain_test(
        pre=pre,
        post=post,
        blocks=[Block(txs=[tx])],
    )


# --- Check-order tests and their helpers ---

_INCOMPATIBLE_SCENARIOS = {
    frozenset({CallScenario.SHORT_CALLDATA, CallScenario.EXTRA_CALLDATA}),
    frozenset({CallScenario.LOW_GAS, CallScenario.NONZERO_VALUE}),
}

_CHECK_ORDER_PAIRS = [
    pytest.param(
        s1,
        s2,
        id=f"{s1.name.lower()}__{s2.name.lower()}",
    )
    for s1 in CallScenario
    for s2 in CallScenario
    if s1 != CallScenario.SUCCESS
    and s2 != CallScenario.SUCCESS
    and s1.check_priority < s2.check_priority
    and frozenset({s1, s2}) not in _INCOMPATIBLE_SCENARIOS
]


@pytest.mark.parametrize("gas", [Spec.GAS_COST, Spec.GAS_ERROR_THRESHOLD])
@pytest.mark.parametrize("scenario1,scenario2", _CHECK_ORDER_PAIRS)
def test_check_order(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
    scenario1: CallScenario,
    scenario2: CallScenario,
    gas: int,
) -> None:
    """
    Test precompile check priority with enum-driven failure pairs.

    Each combination triggers exactly two failure causes. The test
    derives the expected outcome from the higher-priority failure.
    """
    prevailing = min(scenario1, scenario2, key=lambda s: s.check_priority)
    expected_msg = prevailing.error_message(gas) or b""

    # Memory layout: non-overlapping buffers; args are at mem[0:64].
    ret_offset = 96
    rdc_offset = 128

    contract = (
        Op.SSTORE(
            slot_call_success,
            call_code(
                scenario1,
                scenario2,
                gas=gas,
                ret_offset=ret_offset,
                ret_size=32,
            ),
        )
        + Op.SSTORE(slot_return_size, Op.RETURNDATASIZE)
        + Op.RETURNDATACOPY(rdc_offset, 0, Op.RETURNDATASIZE)
        + Op.SSTORE(slot_return_value, Op.MLOAD(rdc_offset))
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    contract_address = pre.deploy_contract(contract, balance=1)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    expected_return_size = len(expected_msg)
    expected_mload = _mload_of(expected_msg) if expected_msg else 0

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_call_success: 0,
                    slot_return_size: expected_return_size,
                    slot_return_value: expected_mload,
                    slot_code_worked: value_code_worked,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )
