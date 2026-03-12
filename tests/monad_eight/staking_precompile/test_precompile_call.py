"""
Tests for staking precompile call behavior.

Tests cover:
- Input validation (selector, size)
- Gas consumption and out-of-gas behavior
- Different call opcodes (CALL, DELEGATECALL, CALLCODE, STATICCALL)
- Value transfer with calls (payable vs non-payable)
- Return data on success and revert
"""

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

from .helpers import build_calldata, generous_gas, tx_calldata
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

slot_code_worked = 0x1
value_code_worked = 0x1234
slot_call_success = 0x2
slot_return_size = 0x3
slot_return_value = 0x4
slot_ret_buffer_value = 0x5
slot_all_gas_consumed = 0x6


def _calldata_mem_end(calldata_size: int) -> int:
    """Return the first memory offset after build_calldata's writes."""
    return 60 + calldata_size + 1


def _mload_of(msg: bytes) -> int:
    """
    Compute the MLOAD uint256 value after a raw error message is
    written to a zero-initialised memory slot.

    CALL copies msg to mem[offset], MLOAD then reads 32 bytes
    big-endian with trailing zeros.
    """
    return int.from_bytes((msg + b"\x00" * 32)[:32], "big")


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

    def should_succeed(self, func: FunctionInfo) -> bool:
        """Return whether this scenario succeeds for the given function."""
        if func.empty_state_error:
            return False
        if self == CallScenario.SUCCESS:
            return True
        if self == CallScenario.NONZERO_VALUE:
            return func.is_payable
        if self == CallScenario.EXTRA_CALLDATA:
            return func.calldata_size == 4
        return False

    def error_message(self, func: "FunctionInfo | None" = None) -> bytes:
        """Return raw ASCII error bytes for this scenario."""
        match self:
            case CallScenario.SUCCESS:
                if func and func.empty_state_error:
                    return func.empty_state_error.encode()
                return b""
            case (
                CallScenario.NOT_CALL
                | CallScenario.DELEGATE_TO_PRECOMPILE
                | CallScenario.LOW_GAS
            ):
                return b""
            case CallScenario.WRONG_SELECTOR | CallScenario.TRUNCATED_SELECTOR:
                return ERROR_METHOD_NOT_SUPPORTED.encode()
            case CallScenario.SHORT_CALLDATA:
                return ERROR_INPUT_TOO_SHORT.encode()
            case CallScenario.EXTRA_CALLDATA:
                if func and func.calldata_size == 4:
                    return b""
                return ERROR_INVALID_INPUT.encode()
            case CallScenario.NONZERO_VALUE:
                if func and func.is_payable and func.empty_state_error:
                    return func.empty_state_error.encode()
                return ERROR_VALUE_NONZERO.encode()
        return b""

    @property
    def check_priority(self) -> int:
        """Return precompile check priority."""
        if self == CallScenario.SUCCESS:
            raise AssertionError("SUCCESS has no check priority")
        order = [
            CallScenario.NOT_CALL,
            CallScenario.DELEGATE_TO_PRECOMPILE,
            CallScenario.TRUNCATED_SELECTOR,
            CallScenario.WRONG_SELECTOR,
            CallScenario.LOW_GAS,
            CallScenario.NONZERO_VALUE,
            CallScenario.SHORT_CALLDATA,
            CallScenario.EXTRA_CALLDATA,
        ]
        return order.index(self)


def call_code(
    *scenarios: CallScenario,
    func: FunctionInfo,
    gas: int | Bytecode = 0,
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

    # Memory layout: non-overlapping buffers
    #   MSTORE(0, 0xDEADBEEF) -> wrong selector at mem[28:32]
    #   build_calldata(selector, size) -> selector at mem[60:64]
    wrong_sel_args_offset = 28
    correct_sel_args_offset = 60

    # NOTE: in case of wrong selector scenario, we do not include any
    # extra calldata for (non-existent) function arguments
    setup: Bytecode = build_calldata(
        func.selector, func.calldata_size
    ) + Op.MSTORE(0, 0xDEADBEEF)

    if CallScenario.WRONG_SELECTOR in scenario_set:
        args_offset = wrong_sel_args_offset
        args_size = 4
    elif CallScenario.TRUNCATED_SELECTOR in scenario_set:
        args_offset = correct_sel_args_offset
        args_size = 3
    elif CallScenario.SHORT_CALLDATA in scenario_set:
        args_offset = correct_sel_args_offset
        args_size = func.calldata_size - 1
    elif CallScenario.EXTRA_CALLDATA in scenario_set:
        args_offset = correct_sel_args_offset
        args_size = func.calldata_size + 1
    else:
        args_offset = correct_sel_args_offset
        args_size = func.calldata_size

    if ret_size > 0:
        assert ret_offset >= args_offset + args_size, (
            "ret buffer must come after args buffer"
        )

    if CallScenario.LOW_GAS in scenario_set:
        if (
            CallScenario.WRONG_SELECTOR in scenario_set
            or CallScenario.TRUNCATED_SELECTOR in scenario_set
        ):
            gas = GAS_UNKNOWN_SELECTOR - 1
        else:
            gas = func.gas_cost - 1

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
                args_offset=60,
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

    is_short = input_size < func.calldata_size
    is_long = input_size > func.calldata_size
    parameterless = func.calldata_size == 4
    size_ok = input_size == func.calldata_size or (is_long and parameterless)
    should_succeed = size_ok and not func.empty_state_error

    if size_ok and func.empty_state_error:
        expected_return_size = len(func.empty_state_error)
    elif should_succeed:
        expected_return_size = func.return_size
    elif input_size < 4:
        expected_return_size = len(ERROR_METHOD_NOT_SUPPORTED)
    elif is_short:
        expected_return_size = len(ERROR_INPUT_TOO_SHORT)
    else:
        expected_return_size = len(ERROR_INVALID_INPUT)

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
        should_succeed = not func.empty_state_error
        expected_return_size = (
            func.return_size if should_succeed else len(func.empty_state_error)
        )
    else:
        calldata_setup = Op.MSTORE(32, selector)
        args_size = 4
        gas = GAS_UNKNOWN_SELECTOR + 10000
        should_succeed = False
        expected_return_size = len(ERROR_METHOD_NOT_SUPPORTED)

    contract = (
        calldata_setup
        + Op.SSTORE(
            slot_call_success,
            Op.CALL(
                gas=gas,
                address=STAKING_PRECOMPILE,
                args_offset=60,
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
                args_offset=60,
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

    should_succeed = enough_gas and not func.empty_state_error

    state_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_call_success: 1 if should_succeed else 0,
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
                args_offset=60,
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

    is_call = call_opcode == Op.CALL
    should_succeed = is_call and not func.empty_state_error

    if should_succeed:
        expected_return_size = func.return_size
    elif is_call and func.empty_state_error:
        expected_return_size = len(func.empty_state_error)
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
    "func",
    [pytest.param(f, id=f.name) for f in ALL_FUNCTIONS],
)
@pytest.mark.parametrize(
    "scenario",
    [s for s in CallScenario if s != CallScenario.LOW_GAS],
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
    # Always provide enough gas regardless of scenario:
    # TRUNCATED_SELECTOR/WRONG_SELECTOR charge GAS_UNKNOWN_SELECTOR
    # before the function's own gas cost is known.
    gas = max(func.gas_cost, GAS_UNKNOWN_SELECTOR) + 10000

    mem_end = _calldata_mem_end(func.calldata_size)
    ret_offset = max(mem_end, 96)
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
            call_code(
                scenario,
                func=func,
                gas=gas,
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

    ok = scenario.should_succeed(func)

    if scenario == CallScenario.SHORT_CALLDATA and func.calldata_size == 4:
        # SHORT_CALLDATA on a 4-byte function sends 3 bytes, hitting the
        # truncated-selector path rather than the size-mismatch path.
        err = ERROR_METHOD_NOT_SUPPORTED.encode()
    else:
        err = scenario.error_message(func)
    expected_return_size = func.return_size if ok else (len(err) if err else 0)
    expected_mload = (
        func.first_return_word if ok else _mload_of(err) if err else 0
    )

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_call_success: 1 if ok else 0,
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
            call_code(
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

    ok = scenario.should_succeed(func)

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_code_worked: value_code_worked,
                    slot_call_success: 1 if ok else 0,
                    slot_all_gas_consumed: 0 if ok else 1,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )


@pytest.mark.parametrize(
    "func",
    [pytest.param(f, id=f.name) for f in ALL_FUNCTIONS],
)
@pytest.mark.parametrize("value", [0, 1])
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
    mem_end = _calldata_mem_end(func.calldata_size)
    ret_offset = max(mem_end, 96)
    rdc_offset = ret_offset + 32

    contract = (
        build_calldata(func.selector, func.calldata_size)
        + Op.SSTORE(
            slot_call_success,
            Op.CALL(
                gas=func.gas_cost + 10000,
                address=STAKING_PRECOMPILE,
                value=value,
                args_offset=60,
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

    value_ok = value == 0 or func.is_payable
    should_succeed = value_ok and not func.empty_state_error

    if should_succeed:
        expected_return_size = func.return_size
        expected_mload = func.first_return_word
    elif not value_ok:
        err = ERROR_VALUE_NONZERO.encode()
        expected_return_size = len(err)
        expected_mload = _mload_of(err)
    else:
        err = func.empty_state_error.encode()
        expected_return_size = len(err)
        expected_mload = _mload_of(err)

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_call_success: 1 if should_succeed else 0,
                    slot_return_size: expected_return_size,
                    slot_return_value: expected_mload,
                    slot_code_worked: value_code_worked,
                }
            ),
        },
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
    scenarios_set = frozenset({scenario1, scenario2})
    call_succeeds = False

    if scenarios_set == frozenset(
        {CallScenario.LOW_GAS, CallScenario.NONZERO_VALUE}
    ):
        if func.is_payable:
            # EVM adds 2300 stipend for value>0, overcoming LOW_GAS;
            # payable func accepts value -> call succeeds
            expected_msg = b""
            call_succeeds = True
        else:
            expected_msg = CallScenario.NONZERO_VALUE.error_message(func)
    elif CallScenario.LOW_GAS in scenarios_set and (
        CallScenario.TRUNCATED_SELECTOR in scenarios_set
        or CallScenario.WRONG_SELECTOR in scenarios_set
    ):
        # gas = GAS_UNKNOWN_SELECTOR-1 < GAS_UNKNOWN_SELECTOR -> OOG
        expected_msg = b""
    else:
        prevailing = min(scenario1, scenario2, key=lambda s: s.check_priority)
        expected_msg = prevailing.error_message(func) or b""
        if prevailing == CallScenario.NONZERO_VALUE:
            if (
                CallScenario.SHORT_CALLDATA in scenarios_set
                and func.calldata_size == 4
            ):
                # SHORT on a 4-byte func sends 3 bytes -> truncated
                # selector path -> "method not supported"
                expected_msg = ERROR_METHOD_NOT_SUPPORTED.encode()
            elif func.is_payable:
                # Payable accepts value; the other scenario fires
                other = (
                    scenario1
                    if scenario1 != CallScenario.NONZERO_VALUE
                    else scenario2
                )
                expected_msg = other.error_message(func) or b""

    mem_end = _calldata_mem_end(func.calldata_size)
    ret_offset = max(mem_end, 96)
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
            call_code(
                scenario1,
                scenario2,
                func=func,
                gas=max(func.gas_cost, GAS_UNKNOWN_SELECTOR) + 10000,
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

    if call_succeeds:
        expected_return_size = func.return_size
        expected_mload = func.first_return_word
    else:
        expected_return_size = len(expected_msg)
        expected_mload = _mload_of(expected_msg) if expected_msg else 0

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_call_success: 1 if call_succeeds else 0,
                    slot_return_size: expected_return_size,
                    slot_return_value: expected_mload,
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

    tx = Transaction(
        gas_limit=gas_limit,
        max_fee_per_gas=gas_price,
        max_priority_fee_per_gas=gas_price,
        to=to,
        sender=sender,
        data=calldata,
        value=value,
        expected_receipt=TransactionReceipt(
            status=1 if scenario.should_succeed(func) else 0,
        ),
    )

    if scenario.should_succeed(func):
        post: dict = {sender: Account(balance=0)}
        # FIXME: which is correct? does the precompile hide the balance?
        # Value was transferred to precompile
        # post = {
        #     sender: Account(balance=0),
        #     STAKING_PRECOMPILE:
        # Account(balance=value) if value > 0 else None,
        # }
    else:
        post = {sender: Account(balance=value)}

    state_test(
        pre=pre,
        post=post,
        tx=tx,
    )


_TX_INCOMPATIBLE_SCENARIOS = _INCOMPATIBLE_SCENARIOS | {
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
            if scenario1.should_succeed(func)
            and scenario2.should_succeed(func)
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
                args_offset=60,
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
