"""
Tests for (stubbed and empty state-ed) staking precompile call behavior.

Tests cover:
- Input validation (selector, size)
- Gas consumption and out-of-gas behavior
- Different call opcodes (CALL, DELEGATECALL, CALLCODE, STATICCALL)
- Value transfer with calls (payable vs non-payable)
- Return data on success and revert
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto, unique
from typing import Any

import pytest
from execution_testing import (
    Account,
    Address,
    Alloc,
    AuthorizationTuple,
    Block,
    BlockchainTestFiller,
    Bytecode,
    Op,
    StateTestFiller,
    Transaction,
)
from execution_testing.forks.forks.forks import MONAD_NINE
from execution_testing.forks.helpers import Fork
from execution_testing.test_types.receipt_types import TransactionReceipt

from .helpers import (
    CORRECT_SEL_ARGS_OFFSET,
    WRONG_SEL_ARGS_OFFSET,
    WRONG_SEL_MSTORE_OFFSET,
    build_calldata,
    calldata_mem_end,
    generous_gas,
    tx_calldata,
)
from .spec import (
    ALL_FUNCTIONS,
    ERROR_INPUT_TOO_SHORT,
    ERROR_INVALID_INPUT,
    ERROR_METHOD_NOT_SUPPORTED,
    ERROR_VALUE_NONZERO,
    FUNC_BY_SELECTOR,
    GAS_UNKNOWN_SELECTOR,
    REPRESENTATIVE_FUNCTIONS,
    STAKING_PRECOMPILE,
    FunctionInfo,
    ref_spec_staking,
)

REFERENCE_SPEC_GIT_PATH = ref_spec_staking.git_path
REFERENCE_SPEC_VERSION = ref_spec_staking.version

GAS_STIPEND = 2300

slot_code_worked = 0x1
value_code_worked = 0x1234
slot_call_success = 0x2
slot_return_size = 0x3
slot_return_value = 0x4
slot_ret_buffer_value = 0x5
slot_all_gas_consumed = 0x6


def _mload_of(msg: bytes) -> int:
    """
    Compute the MLOAD uint256 value after a raw error message is
    written to a zero-initialised memory slot.

    CALL copies msg to mem[offset], MLOAD then reads 32 bytes
    big-endian with trailing zeros.
    """
    return int.from_bytes((msg + b"\x00" * 32)[:32], "big")


@dataclass(frozen=True)
class ExpectedOutcome:
    """Fully resolved expected result of a precompile call."""

    call_success: int
    return_size: int
    return_word: int = 0

    @staticmethod
    def success(func: FunctionInfo) -> ExpectedOutcome:
        """
        Build outcome for a successful precompile call, against
        a stubbed implementation.
        """
        return ExpectedOutcome(
            call_success=1,
            return_size=func.return_size,
            return_word=func.first_return_word,
        )

    @staticmethod
    def revert(error: str) -> ExpectedOutcome:
        """Build outcome for a precompile revert with error data."""
        raw = error.encode()
        return ExpectedOutcome(
            call_success=0,
            return_size=len(raw),
            return_word=_mload_of(raw) if raw else 0,
        )

    @staticmethod
    def by_function(func: FunctionInfo) -> ExpectedOutcome:
        """
        Outcome when all checks pass — may still revert on empty stubbed
        state.
        """
        if func.empty_state_error:
            return ExpectedOutcome.revert(func.empty_state_error)
        return ExpectedOutcome.success(func)

    @staticmethod
    def by_input_size_delta(
        func: FunctionInfo, input_size_delta: int
    ) -> ExpectedOutcome:
        """Resolve outcome based on calldata size delta."""
        input_size = func.calldata_size + input_size_delta
        if input_size < 4:
            return ExpectedOutcome.revert(ERROR_METHOD_NOT_SUPPORTED)
        if func.overrides_size_errors:
            return ExpectedOutcome.revert(func.empty_state_error)
        parameterless = func.calldata_size == 4
        is_short = input_size < func.calldata_size
        is_long = input_size > func.calldata_size
        size_ok = not is_short and (not is_long or parameterless)
        if size_ok:
            return ExpectedOutcome.by_function(func)
        if is_short:
            return ExpectedOutcome.revert(ERROR_INPUT_TOO_SHORT)
        return ExpectedOutcome.revert(ERROR_INVALID_INPUT)

    @staticmethod
    def by_selector(selector: int) -> ExpectedOutcome:
        """Resolve outcome based on function selector."""
        func = FUNC_BY_SELECTOR.get(selector)
        if func is None:
            return ExpectedOutcome.revert(ERROR_METHOD_NOT_SUPPORTED)
        return ExpectedOutcome.by_function(func)

    @staticmethod
    def by_call_opcode(func: FunctionInfo, call_opcode: Op) -> ExpectedOutcome:
        """Resolve outcome based on call opcode type."""
        if call_opcode != Op.CALL:
            return ExpectedOutcome.revert("")
        return ExpectedOutcome.by_function(func)

    @staticmethod
    def by_value(func: FunctionInfo, value: int) -> ExpectedOutcome:
        """Resolve outcome based on value transfer amount."""
        if value == 0:
            return ExpectedOutcome.by_function(func)
        if not func.is_payable:
            return ExpectedOutcome.revert(ERROR_VALUE_NONZERO)
        if func.nonzero_value_error:
            return ExpectedOutcome.revert(func.nonzero_value_error)
        return ExpectedOutcome.by_function(func)


@unique
class CallScenario(Enum):
    """Precompile call scenarios for parametrized tests."""

    SUCCESS = auto()
    TRUNCATED_SELECTOR = auto()
    WRONG_SELECTOR = auto()
    SHORT_CALLDATA = auto()
    EXTRA_CALLDATA = auto()
    NOT_CALL = auto()
    DELEGATE_TO_PRECOMPILE = auto()
    NONZERO_VALUE = auto()
    LOW_GAS = auto()

    @property
    def check_priority(self) -> int:
        """Return precompile check priority."""
        order = [
            CallScenario.NOT_CALL,
            CallScenario.DELEGATE_TO_PRECOMPILE,
            CallScenario.TRUNCATED_SELECTOR,
            CallScenario.WRONG_SELECTOR,
            CallScenario.LOW_GAS,
            CallScenario.NONZERO_VALUE,
            CallScenario.SHORT_CALLDATA,
            CallScenario.EXTRA_CALLDATA,
            CallScenario.SUCCESS,
        ]
        return order.index(self)


def _normalize(scenario: CallScenario, func: FunctionInfo) -> CallScenario:
    """Map scenarios to their effective form for a given function."""
    if func.calldata_size == 4:
        if scenario == CallScenario.SHORT_CALLDATA:
            return CallScenario.TRUNCATED_SELECTOR
        if scenario == CallScenario.EXTRA_CALLDATA:
            return CallScenario.SUCCESS
    return scenario


def resolve_outcome(
    func: FunctionInfo, scenario: CallScenario
) -> ExpectedOutcome:
    """
    Resolve expected outcome for a single scenario.
    """
    scenario = _normalize(scenario, func)
    match scenario:
        case CallScenario.SUCCESS:
            return ExpectedOutcome.by_function(func)
        case (
            CallScenario.NOT_CALL
            | CallScenario.DELEGATE_TO_PRECOMPILE
            | CallScenario.LOW_GAS
        ):
            return ExpectedOutcome.revert("")
        case CallScenario.TRUNCATED_SELECTOR | CallScenario.WRONG_SELECTOR:
            return ExpectedOutcome.revert(ERROR_METHOD_NOT_SUPPORTED)
        case CallScenario.SHORT_CALLDATA:
            if func.overrides_size_errors:
                return ExpectedOutcome.revert(func.empty_state_error)
            return ExpectedOutcome.revert(ERROR_INPUT_TOO_SHORT)
        case CallScenario.EXTRA_CALLDATA:
            if func.overrides_size_errors:
                return ExpectedOutcome.revert(func.empty_state_error)
            return ExpectedOutcome.revert(ERROR_INVALID_INPUT)
        case CallScenario.NONZERO_VALUE:
            if not func.is_payable:
                return ExpectedOutcome.revert(ERROR_VALUE_NONZERO)
            if func.nonzero_value_error:
                return ExpectedOutcome.revert(func.nonzero_value_error)
            return ExpectedOutcome.by_function(func)
    raise ValueError(f"Unknown scenario: {scenario}")


def _stipend_neutralizes_low_gas(
    func: FunctionInfo,
    scenario_set: set[CallScenario],
) -> bool:
    """
    Check if the EVM value-transfer stipend overcomes how little
    gas is provided in LOW_GAS scenarios.
    """
    if not (
        {CallScenario.LOW_GAS, CallScenario.NONZERO_VALUE} <= scenario_set
    ):
        return False

    # Given how LOW_GAS gas is calculated, unknown selector
    # scenarios can never have stipend compensate for gas
    # shortage.
    assert GAS_UNKNOWN_SELECTOR > GAS_STIPEND
    known_selector = scenario_set.isdisjoint(
        {
            CallScenario.TRUNCATED_SELECTOR,
            CallScenario.WRONG_SELECTOR,
        }
    )
    return known_selector and func.gas_cost <= GAS_STIPEND


def resolve_outcome_pair(
    func: FunctionInfo,
    scenario1: CallScenario,
    scenario2: CallScenario,
) -> ExpectedOutcome:
    """
    Resolve expected outcome when two failure scenarios combine.

    The higher-priority scenario prevails, with special handling
    for LOW_GAS interactions and NONZERO_VALUE pass-through on
    payable functions.
    """
    scenario1 = _normalize(scenario1, func)
    scenario2 = _normalize(scenario2, func)

    # Sort so prevailing (higher priority) is first
    if scenario1.check_priority > scenario2.check_priority:
        scenario1, scenario2 = scenario2, scenario1

    if _stipend_neutralizes_low_gas(
        # True iff `scenario1 == LOW_GAS and scenario2 == NONZERO_VALUE`
        func,
        {scenario1, scenario2},
    ):
        return resolve_outcome(func, scenario2)

    # The additional gas check for fallback function special gas cost.
    if (
        scenario1
        in (
            CallScenario.TRUNCATED_SELECTOR,
            CallScenario.WRONG_SELECTOR,
        )
        and scenario2 == CallScenario.LOW_GAS
    ):
        return ExpectedOutcome.revert("")

    # Payable functions pass the NONZERO_VALUE check;
    # the other (lower-priority) scenario fires instead
    if scenario1 == CallScenario.NONZERO_VALUE and func.is_payable:
        return resolve_outcome(func, scenario2)

    return resolve_outcome(func, scenario1)


def resolve_outcome_triple(
    func: FunctionInfo,
    s1: CallScenario,
    s2: CallScenario,
    s3: CallScenario,
) -> ExpectedOutcome:
    """Resolve expected outcome for three combined scenarios."""
    scenarios = sorted(
        [_normalize(s, func) for s in (s1, s2, s3)],
        key=lambda s: s.check_priority,
    )
    if _stipend_neutralizes_low_gas(func, set(scenarios)):
        scenarios.remove(CallScenario.LOW_GAS)

    if scenarios[0] == CallScenario.NONZERO_VALUE and func.is_payable:
        scenarios = scenarios[1:]

    if len(scenarios) == 1:
        return resolve_outcome(func, scenarios[0])
    return resolve_outcome_pair(func, scenarios[0], scenarios[1])


def scenario_call_code(
    *scenarios: CallScenario,
    func: FunctionInfo,
    gas: int | Bytecode | None = None,
    ret_offset: int = 0,
    ret_size: int = 0,
    delegating_eoa: Address | None = None,
) -> Bytecode:
    """
    Generate setup + call bytecode for one or more combined scenarios.

    Both the correct and wrong selectors are always written to memory
    at separate offsets. The args_offset is chosen based on whether
    WRONG_SELECTOR is among the scenarios.
    """
    scenario_set = set(scenarios)

    setup: Bytecode = build_calldata(
        func.selector, func.calldata_size
    ) + Op.MSTORE(WRONG_SEL_MSTORE_OFFSET, 0xDEADBEEF)

    if CallScenario.WRONG_SELECTOR in scenario_set:
        args_offset = WRONG_SEL_ARGS_OFFSET
        args_size = 4
    elif CallScenario.TRUNCATED_SELECTOR in scenario_set:
        args_offset = CORRECT_SEL_ARGS_OFFSET
        args_size = 3
    elif CallScenario.SHORT_CALLDATA in scenario_set:
        args_offset = CORRECT_SEL_ARGS_OFFSET
        args_size = func.calldata_size - 1
    elif CallScenario.EXTRA_CALLDATA in scenario_set:
        args_offset = CORRECT_SEL_ARGS_OFFSET
        args_size = func.calldata_size + 1
    else:
        args_offset = CORRECT_SEL_ARGS_OFFSET
        args_size = func.calldata_size

    if ret_size > 0:
        assert ret_offset >= args_offset + args_size, (
            "ret buffer must come after args buffer"
        )

    if gas is None:
        if CallScenario.LOW_GAS in scenario_set:
            gas = max(
                0,
                # Subtracting also the stipend in case the
                # value sent would cause stipend to be added.
                min(func.gas_cost, GAS_UNKNOWN_SELECTOR) - GAS_STIPEND - 1,
            )
        else:
            gas = max(func.gas_cost, GAS_UNKNOWN_SELECTOR)

    if CallScenario.NONZERO_VALUE in scenario_set:
        value = 1
    else:
        value = 0

    if CallScenario.NOT_CALL in scenario_set:
        opcode = Op.CALLCODE
    else:
        opcode = Op.CALL

    if CallScenario.DELEGATE_TO_PRECOMPILE in scenario_set:
        assert delegating_eoa is not None, (
            "delegating_eoa required for DELEGATE_TO_PRECOMPILE"
        )
        call_address = delegating_eoa
    else:
        call_address = STAKING_PRECOMPILE

    return setup + opcode(
        gas=gas,
        address=call_address,
        value=value,
        args_offset=args_offset,
        args_size=args_size,
        ret_offset=ret_offset,
        ret_size=ret_size,
    )


pytestmark = [
    pytest.mark.valid_from("MONAD_EIGHT"),
    pytest.mark.pre_alloc_group(
        "staking_precompile_tests",
        reason="Tests staking precompile",
    ),
]


def input_size_delta(fork: Fork) -> Any:
    """Input size delta, large input allowed for linear memory forks."""
    yield pytest.param(-1, id="short")
    yield pytest.param(0, id="correct")
    yield pytest.param(1, id="byte_extra")
    yield pytest.param(32, id="word_extra")
    if fork >= MONAD_NINE:
        yield pytest.param(7 * 1024 * 1024, id="7MB_extra")


@pytest.mark.parametrize(
    "func",
    [pytest.param(f, id=f.name) for f in ALL_FUNCTIONS],
)
@pytest.mark.parametrize_by_fork("input_size_delta", input_size_delta)
def test_input_size(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    func: FunctionInfo,
    input_size_delta: int,
    fork: Fork,
) -> None:
    """
    Test precompile behavior with various input sizes.

    Calldata must be exactly the expected size for the function.
    """
    input_size = func.calldata_size + input_size_delta
    if input_size < 4:
        gas = GAS_UNKNOWN_SELECTOR + 10000
    else:
        gas = func.gas_cost + 10000
    calldata_setup = build_calldata(
        func.selector, func.calldata_size + input_size_delta
    )

    contract = (
        calldata_setup
        + Op.SSTORE(
            slot_call_success,
            Op.CALL(
                gas=gas,
                address=STAKING_PRECOMPILE,
                args_offset=CORRECT_SEL_ARGS_OFFSET,
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

    outcome = ExpectedOutcome.by_input_size_delta(func, input_size_delta)

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_call_success: outcome.call_success,
                    slot_return_size: outcome.return_size,
                    slot_code_worked: value_code_worked,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )


@pytest.mark.parametrize(
    "selector",
    [pytest.param(f.selector, id=f.name) for f in ALL_FUNCTIONS]
    + [
        pytest.param(0x00000000, id="zero_selector"),
        pytest.param(0xFFFFFFFF, id="max_selector"),
        pytest.param(0xDEADBEEF, id="deadbeef"),
    ],
)
def test_selector(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    selector: int,
    fork: Fork,
) -> None:
    """
    Test precompile behavior with various function selectors.

    Known selectors succeed with correct calldata, unknown selectors
    revert.
    """
    func = FUNC_BY_SELECTOR.get(selector)

    if func is not None:
        calldata_setup = build_calldata(func.selector, func.calldata_size)
        args_size = func.calldata_size
        gas = func.gas_cost + 10000
    else:
        calldata_setup = Op.MSTORE(32, selector)
        args_size = 4
        gas = GAS_UNKNOWN_SELECTOR + 10000

    contract = (
        calldata_setup
        + Op.SSTORE(
            slot_call_success,
            Op.CALL(
                gas=gas,
                address=STAKING_PRECOMPILE,
                args_offset=CORRECT_SEL_ARGS_OFFSET,
                args_size=args_size,
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

    outcome = ExpectedOutcome.by_selector(selector)

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_call_success: outcome.call_success,
                    slot_return_size: outcome.return_size,
                    slot_code_worked: value_code_worked,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )


@pytest.mark.parametrize(
    "func",
    [pytest.param(f, id=f.name) for f in ALL_FUNCTIONS],
)
@pytest.mark.parametrize("enough_gas", [True, False])
def test_gas(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    func: FunctionInfo,
    enough_gas: bool,
) -> None:
    """
    Test that the precompile consumes the expected gas.
    """
    gas = func.gas_cost if enough_gas else func.gas_cost - 1

    contract = (
        build_calldata(func.selector, func.calldata_size)
        + Op.SSTORE(
            slot_call_success,
            Op.CALL(
                gas=gas,
                address=STAKING_PRECOMPILE,
                args_offset=CORRECT_SEL_ARGS_OFFSET,
                args_size=func.calldata_size,
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

    outcome = (
        ExpectedOutcome.by_function(func)
        if enough_gas
        else ExpectedOutcome.revert("")
    )

    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_call_success: outcome.call_success,
                    slot_code_worked: value_code_worked,
                }
            )
        },
        tx=tx,
    )


@pytest.mark.parametrize(
    "func",
    [pytest.param(f, id=f.name) for f in ALL_FUNCTIONS],
)
@pytest.mark.with_all_call_opcodes()
def test_call_opcodes(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    call_opcode: Op,
    func: FunctionInfo,
    fork: Fork,
) -> None:
    """
    Test that the precompile must be invoked via CALL only.

    STATICCALL, DELEGATECALL, and CALLCODE must revert.
    """
    contract = (
        build_calldata(func.selector, func.calldata_size)
        + Op.SSTORE(
            slot_call_success,
            call_opcode(
                gas=func.gas_cost + 10000,
                address=STAKING_PRECOMPILE,
                args_offset=CORRECT_SEL_ARGS_OFFSET,
                args_size=func.calldata_size,
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

    outcome = ExpectedOutcome.by_call_opcode(func, call_opcode)

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_call_success: outcome.call_success,
                    slot_return_size: outcome.return_size,
                    slot_code_worked: value_code_worked,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )


@pytest.mark.parametrize(
    "func",
    [pytest.param(f, id=f.name) for f in ALL_FUNCTIONS],
)
@pytest.mark.parametrize(
    "scenario",
    list(CallScenario),
)
def test_revert_returns(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
    func: FunctionInfo,
    scenario: CallScenario,
) -> None:
    """
    Test return data on success and on each revert reason.
    """
    ret_offset = calldata_mem_end(func.calldata_size)
    rdc_offset = ret_offset + 32

    delegating_eoa: Address | None = None
    authorization_list = None
    if scenario == CallScenario.DELEGATE_TO_PRECOMPILE:
        delegating_eoa = pre.fund_eoa()
        authorization_list = [
            AuthorizationTuple(
                address=STAKING_PRECOMPILE,
                nonce=0,
                signer=delegating_eoa,
            )
        ]

    contract = (
        Op.SSTORE(
            slot_call_success,
            scenario_call_code(
                scenario,
                func=func,
                ret_offset=ret_offset,
                ret_size=32,
                delegating_eoa=delegating_eoa,
            ),
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
        authorization_list=authorization_list,
    )

    outcome = resolve_outcome(func, scenario)

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_call_success: outcome.call_success,
                    slot_return_size: outcome.return_size,
                    slot_ret_buffer_value: outcome.return_word,
                    slot_return_value: outcome.return_word,
                    slot_code_worked: value_code_worked,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )


@pytest.mark.parametrize(
    "func",
    [pytest.param(f, id=f.name) for f in ALL_FUNCTIONS],
)
@pytest.mark.parametrize(
    "scenario",
    [s for s in CallScenario if s != CallScenario.LOW_GAS],
)
def test_revert_consumes_all_gas(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
    func: FunctionInfo,
    scenario: CallScenario,
) -> None:
    """
    Test that precompile reverts consume all gas
    provided to the call frame.
    """
    gas_limit = generous_gas(fork)
    gas_threshold = gas_limit // 64

    delegating_eoa: Address | None = None
    authorization_list = None
    if scenario == CallScenario.DELEGATE_TO_PRECOMPILE:
        delegating_eoa = pre.fund_eoa()
        authorization_list = [
            AuthorizationTuple(
                address=STAKING_PRECOMPILE,
                nonce=0,
                signer=delegating_eoa,
            )
        ]

    contract = (
        Op.SSTORE(slot_code_worked, value_code_worked)
        + Op.SSTORE(slot_call_success, 1)
        + Op.SSTORE(slot_all_gas_consumed, 1)
        + Op.SSTORE(
            slot_call_success,
            scenario_call_code(
                scenario,
                func=func,
                gas=Op.GAS,
                delegating_eoa=delegating_eoa,
            ),
        )
        + Op.SSTORE(
            slot_all_gas_consumed,
            Op.LT(Op.GAS, gas_threshold),
        )
    )
    contract_address = pre.deploy_contract(contract, balance=1)

    tx = Transaction(
        gas_limit=gas_limit,
        to=contract_address,
        sender=pre.fund_eoa(),
        authorization_list=authorization_list,
    )

    outcome = resolve_outcome(func, scenario)

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_code_worked: value_code_worked,
                    slot_call_success: outcome.call_success,
                    slot_all_gas_consumed: 1 - outcome.call_success,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )


@pytest.mark.parametrize(
    "func",
    [pytest.param(f, id=f.name) for f in ALL_FUNCTIONS],
)
@pytest.mark.parametrize("value", [0, 1, 2**128])
def test_call_with_value(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    func: FunctionInfo,
    value: int,
    fork: Fork,
) -> None:
    """
    Test value transfer behavior for payable and non-payable functions.

    Payable functions accept value; non-payable functions revert with
    "value is nonzero".
    """
    ret_offset = calldata_mem_end(func.calldata_size)
    rdc_offset = ret_offset + 32

    contract = (
        build_calldata(func.selector, func.calldata_size)
        + Op.SSTORE(
            slot_call_success,
            Op.CALL(
                gas=func.gas_cost + 10000,
                address=STAKING_PRECOMPILE,
                value=value,
                args_offset=CORRECT_SEL_ARGS_OFFSET,
                args_size=func.calldata_size,
                ret_offset=ret_offset,
                ret_size=32,
            ),
        )
        + Op.SSTORE(slot_return_size, Op.RETURNDATASIZE)
        + Op.RETURNDATACOPY(rdc_offset, 0, Op.RETURNDATASIZE)
        + Op.SSTORE(slot_return_value, Op.MLOAD(rdc_offset))
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )
    contract_address = pre.deploy_contract(contract, balance=value)

    tx = Transaction(
        gas_limit=generous_gas(fork),
        to=contract_address,
        sender=pre.fund_eoa(),
    )

    outcome = ExpectedOutcome.by_value(func, value)

    post: dict = {
        contract_address: Account(
            storage={
                slot_call_success: outcome.call_success,
                slot_return_size: outcome.return_size,
                slot_return_value: outcome.return_word,
                slot_code_worked: value_code_worked,
            },
            balance=0 if outcome.call_success else value,
        ),
        STAKING_PRECOMPILE: Account(balance=value)
        if outcome.call_success and value > 0
        else None,
    }

    blockchain_test(
        pre=pre,
        post=post,
        blocks=[Block(txs=[tx])],
    )


# --- Check-order tests and their helpers ---

_INCOMPATIBLE_SCENARIOS = {
    frozenset({CallScenario.SHORT_CALLDATA, CallScenario.EXTRA_CALLDATA}),
    frozenset({CallScenario.TRUNCATED_SELECTOR, CallScenario.SHORT_CALLDATA}),
    frozenset({CallScenario.TRUNCATED_SELECTOR, CallScenario.EXTRA_CALLDATA}),
    # Both produce the same error message
    frozenset({CallScenario.TRUNCATED_SELECTOR, CallScenario.WRONG_SELECTOR}),
}

_CHECK_ORDER_PAIRS = [
    pytest.param(s1, s2, id=f"{s1.name.lower()}__{s2.name.lower()}")
    for s1 in CallScenario
    for s2 in CallScenario
    if CallScenario.SUCCESS not in {s1, s2}
    and s1.check_priority < s2.check_priority
    and frozenset({s1, s2}) not in _INCOMPATIBLE_SCENARIOS
]


@pytest.mark.parametrize(
    "func",
    [pytest.param(f, id=f.name) for f in REPRESENTATIVE_FUNCTIONS],
)
@pytest.mark.parametrize("scenario1,scenario2", _CHECK_ORDER_PAIRS)
def test_check_order(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
    func: FunctionInfo,
    scenario1: CallScenario,
    scenario2: CallScenario,
) -> None:
    """
    Test precompile check priority with enum-driven failure pairs.

    Each combination triggers exactly two failure causes. The test
    derives the expected outcome from the higher-priority failure.
    """
    ret_offset = calldata_mem_end(func.calldata_size)
    rdc_offset = ret_offset + 32

    delegating_eoa: Address | None = None
    authorization_list = None
    if CallScenario.DELEGATE_TO_PRECOMPILE in {scenario1, scenario2}:
        delegating_eoa = pre.fund_eoa()
        authorization_list = [
            AuthorizationTuple(
                address=STAKING_PRECOMPILE,
                nonce=0,
                signer=delegating_eoa,
            )
        ]

    contract = (
        Op.SSTORE(
            slot_call_success,
            scenario_call_code(
                scenario1,
                scenario2,
                func=func,
                ret_offset=ret_offset,
                ret_size=32,
                delegating_eoa=delegating_eoa,
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
        authorization_list=authorization_list,
    )

    outcome = resolve_outcome_pair(func, scenario1, scenario2)

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_call_success: outcome.call_success,
                    slot_return_size: outcome.return_size,
                    slot_return_value: outcome.return_word,
                    slot_code_worked: value_code_worked,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )


def _pairwise_compatible(
    scenarios: tuple[CallScenario, ...],
    incompatible: set[frozenset[CallScenario]] = _INCOMPATIBLE_SCENARIOS,
) -> bool:
    """Check that no pair in the tuple is incompatible."""
    return all(
        frozenset({a, b}) not in incompatible
        for i, a in enumerate(scenarios)
        for b in scenarios[i + 1 :]
    )


_CHECK_ORDER_TRIPLES = [
    pytest.param(
        s1,
        s2,
        s3,
        id=f"{s1.name.lower()}__{s2.name.lower()}__{s3.name.lower()}",
    )
    for s1 in CallScenario
    for s2 in CallScenario
    for s3 in CallScenario
    if CallScenario.SUCCESS not in {s1, s2, s3}
    and s1.check_priority < s2.check_priority < s3.check_priority
    and _pairwise_compatible((s1, s2, s3))
]


@pytest.mark.parametrize(
    "func",
    [pytest.param(f, id=f.name) for f in REPRESENTATIVE_FUNCTIONS],
)
@pytest.mark.parametrize("scenario1,scenario2,scenario3", _CHECK_ORDER_TRIPLES)
def test_check_order_triple(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
    func: FunctionInfo,
    scenario1: CallScenario,
    scenario2: CallScenario,
    scenario3: CallScenario,
) -> None:
    """
    Test precompile check priority with three combined failures.

    Each combination triggers exactly three failure causes. The test
    derives the expected outcome from the priority interactions.
    """
    normalized = {
        _normalize(s, func) for s in (scenario1, scenario2, scenario3)
    }
    if not _pairwise_compatible(tuple(normalized)):
        pytest.skip("normalized scenarios are incompatible")

    ret_offset = calldata_mem_end(func.calldata_size)
    rdc_offset = ret_offset + 32

    scenarios = {scenario1, scenario2, scenario3}

    delegating_eoa: Address | None = None
    authorization_list = None
    if CallScenario.DELEGATE_TO_PRECOMPILE in scenarios:
        delegating_eoa = pre.fund_eoa()
        authorization_list = [
            AuthorizationTuple(
                address=STAKING_PRECOMPILE,
                nonce=0,
                signer=delegating_eoa,
            )
        ]

    contract = (
        Op.SSTORE(
            slot_call_success,
            scenario_call_code(
                scenario1,
                scenario2,
                scenario3,
                func=func,
                ret_offset=ret_offset,
                ret_size=32,
                delegating_eoa=delegating_eoa,
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
        authorization_list=authorization_list,
    )

    outcome = resolve_outcome_triple(func, scenario1, scenario2, scenario3)

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_call_success: outcome.call_success,
                    slot_return_size: outcome.return_size,
                    slot_return_value: outcome.return_word,
                    slot_code_worked: value_code_worked,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )


# --- Direct-transaction tests ---


def _tx_params(
    *scenarios: CallScenario,
    func: FunctionInfo,
    pre: Alloc,
    fork: Fork,
) -> tuple[bytes, int, Address, int]:
    """
    Return (calldata, value, to, gas_limit) for tx-level scenarios.
    """
    scenario_set = set(scenarios)

    if CallScenario.WRONG_SELECTOR in scenario_set:
        calldata = bytes.fromhex("DEADBEEF")
    elif CallScenario.TRUNCATED_SELECTOR in scenario_set:
        calldata = func.selector.to_bytes(4, "big")[:3]
    else:
        calldata = tx_calldata(func.selector, func.calldata_size)

    if CallScenario.SHORT_CALLDATA in scenario_set:
        calldata = calldata[: func.calldata_size - 1]
    elif CallScenario.EXTRA_CALLDATA in scenario_set:
        calldata = calldata + b"\xff"

    if CallScenario.NONZERO_VALUE in scenario_set:
        value = 1
    else:
        value = 0

    if CallScenario.DELEGATE_TO_PRECOMPILE in scenario_set:
        to: Address = pre.fund_eoa(0, delegation=STAKING_PRECOMPILE)
    else:
        to = STAKING_PRECOMPILE

    if CallScenario.LOW_GAS in scenario_set:
        intrinsic_gas = fork.transaction_intrinsic_cost_calculator()(
            calldata=calldata,
            return_cost_deducted_prior_execution=True,
        )
        gas_limit = intrinsic_gas + func.gas_cost - 1
    else:
        gas_limit = generous_gas(fork)

    return calldata, value, to, gas_limit


@pytest.mark.parametrize(
    "func",
    [pytest.param(f, id=f.name) for f in REPRESENTATIVE_FUNCTIONS],
)
@pytest.mark.parametrize(
    "scenario",
    [s for s in CallScenario if s is not CallScenario.NOT_CALL],
)
def test_tx_revert_scenarios(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    func: FunctionInfo,
    scenario: CallScenario,
) -> None:
    """
    Test precompile behavior when called directly as transaction `to`.
    """
    gas_price = 10

    calldata, value, to, gas_limit = _tx_params(
        scenario, func=func, pre=pre, fork=fork
    )
    gas_cost = gas_limit * gas_price
    sender = pre.fund_eoa(gas_cost + value)

    outcome = resolve_outcome(func, scenario)

    tx = Transaction(
        gas_limit=gas_limit,
        max_fee_per_gas=gas_price,
        max_priority_fee_per_gas=gas_price,
        to=to,
        sender=sender,
        data=calldata,
        value=value,
        expected_receipt=TransactionReceipt(
            status=outcome.call_success,
        ),
    )

    if outcome.call_success:
        post: dict = {
            sender: Account(balance=0),
            STAKING_PRECOMPILE: Account(balance=value) if value > 0 else None,
        }
    else:
        post = {sender: Account(balance=value)}

    state_test(
        pre=pre,
        post=post,
        tx=tx,
    )


_TX_INCOMPATIBLE_SCENARIOS = _INCOMPATIBLE_SCENARIOS | {
    # EIP-7623 floor makes it impossible to create a valid tx with
    # insufficient execution gas when extra calldata is appended.
    # For extra calldata (5 bytes), the floor is high enough that
    # we can't create a valid tx with less than 100 execution gas
    # EIP-7623 floor (21200) vs (21179) - impossible
    # For correct calldata (4 bytes), in the test just above it's
    # EIP-7623 floor (21160) vs (21163) - possible
    frozenset({CallScenario.LOW_GAS, CallScenario.EXTRA_CALLDATA}),
}

_TX_SCENARIO_PAIRS = [
    pytest.param(s1, s2, id=f"{s1.name.lower()}__{s2.name.lower()}")
    for s1 in CallScenario
    for s2 in CallScenario
    if CallScenario.SUCCESS not in {s1, s2}
    and CallScenario.NOT_CALL not in {s1, s2}
    and s1.check_priority < s2.check_priority
    and frozenset({s1, s2}) not in _TX_INCOMPATIBLE_SCENARIOS
]


@pytest.mark.parametrize(
    "func",
    [pytest.param(f, id=f.name) for f in REPRESENTATIVE_FUNCTIONS],
)
@pytest.mark.parametrize("scenario1,scenario2", _TX_SCENARIO_PAIRS)
def test_tx_revert_scenario_pairs(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    func: FunctionInfo,
    scenario1: CallScenario,
    scenario2: CallScenario,
) -> None:
    """
    Test when the precompile is called directly as transaction
    `to` with 2 reasons to revert.
    """
    gas_price = 10

    calldata, value, to, gas_limit = _tx_params(
        scenario1, scenario2, func=func, pre=pre, fork=fork
    )
    gas_cost = gas_limit * gas_price
    sender = pre.fund_eoa(gas_cost + value)

    outcome1 = resolve_outcome(func, scenario1)
    outcome2 = resolve_outcome(func, scenario2)

    tx = Transaction(
        gas_limit=gas_limit,
        max_fee_per_gas=gas_price,
        max_priority_fee_per_gas=gas_price,
        to=to,
        sender=sender,
        data=calldata,
        value=value,
        expected_receipt=TransactionReceipt(
            status=0x1
            if outcome1.call_success and outcome2.call_success
            else 0x0
        ),
    )

    post: dict = {
        sender: Account(balance=value),
    }

    state_test(
        pre=pre,
        post=post,
        tx=tx,
    )


_TX_SCENARIO_TRIPLES = [
    pytest.param(
        s1,
        s2,
        s3,
        id=f"{s1.name.lower()}__{s2.name.lower()}__{s3.name.lower()}",
    )
    for s1 in CallScenario
    for s2 in CallScenario
    for s3 in CallScenario
    if CallScenario.SUCCESS not in {s1, s2, s3}
    and CallScenario.NOT_CALL not in {s1, s2, s3}
    and s1.check_priority < s2.check_priority < s3.check_priority
    and _pairwise_compatible((s1, s2, s3), _TX_INCOMPATIBLE_SCENARIOS)
]


@pytest.mark.parametrize(
    "func",
    [pytest.param(f, id=f.name) for f in REPRESENTATIVE_FUNCTIONS],
)
@pytest.mark.parametrize("scenario1,scenario2,scenario3", _TX_SCENARIO_TRIPLES)
def test_tx_revert_scenario_triples(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    func: FunctionInfo,
    scenario1: CallScenario,
    scenario2: CallScenario,
    scenario3: CallScenario,
) -> None:
    """
    Test when the precompile is called directly as transaction
    `to` with 3 reasons to revert.
    """
    normalized = {
        _normalize(s, func) for s in (scenario1, scenario2, scenario3)
    }
    if not _pairwise_compatible(tuple(normalized), _TX_INCOMPATIBLE_SCENARIOS):
        pytest.skip("normalized scenarios are incompatible")

    gas_price = 10

    calldata, value, to, gas_limit = _tx_params(
        scenario1,
        scenario2,
        scenario3,
        func=func,
        pre=pre,
        fork=fork,
    )
    gas_cost = gas_limit * gas_price
    sender = pre.fund_eoa(gas_cost + value)

    outcome1 = resolve_outcome(func, scenario1)
    outcome2 = resolve_outcome(func, scenario2)
    outcome3 = resolve_outcome(func, scenario3)

    tx = Transaction(
        gas_limit=gas_limit,
        max_fee_per_gas=gas_price,
        max_priority_fee_per_gas=gas_price,
        to=to,
        sender=sender,
        data=calldata,
        value=value,
        expected_receipt=TransactionReceipt(
            status=0x1
            if outcome1.call_success
            and outcome2.call_success
            and outcome3.call_success
            else 0x0
        ),
    )

    post: dict = {
        sender: Account(balance=value),
    }

    state_test(
        pre=pre,
        post=post,
        tx=tx,
    )


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param(0x1D4E9F02, id="syscallOnEpochChange"),
        pytest.param(0x791BDCF3, id="syscallReward"),
        pytest.param(0x157EEB21, id="syscallSnapshot"),
    ],
)
def test_syscall_rejected(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    selector: int,
    fork: Fork,
) -> None:
    """
    Test that syscall selectors are rejected.

    Syscalls are reserved for system transactions and must
    revert when called by regular contracts.
    """
    contract = (
        build_calldata(selector, 36)
        + Op.SSTORE(
            slot_call_success,
            Op.CALL(
                gas=100000,
                address=STAKING_PRECOMPILE,
                args_offset=CORRECT_SEL_ARGS_OFFSET,
                args_size=36,
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

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_call_success: 0,
                    slot_return_size: len(ERROR_METHOD_NOT_SUPPORTED),
                    slot_code_worked: value_code_worked,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )
