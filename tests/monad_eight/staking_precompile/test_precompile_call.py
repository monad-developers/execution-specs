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
    Alloc,
    Block,
    BlockchainTestFiller,
    Bytecode,
    Op,
    StateTestFiller,
    Transaction,
)
from execution_testing.forks.helpers import Fork

from .helpers import build_calldata, generous_gas
from .spec import (
    ALL_FUNCTIONS,
    FUNC_BY_SELECTOR,
    GAS_UNKNOWN_SELECTOR,
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
    extra_words = max(0, (calldata_size - 4 + 31) // 32)
    return 32 + extra_words * 32


@unique
class CallScenario(Enum):
    """Precompile call scenarios for parametrized tests."""

    SUCCESS = auto()
    TRUNCATED_SELECTOR = auto()
    WRONG_SELECTOR = auto()
    SHORT_CALLDATA = auto()
    EXTRA_CALLDATA = auto()
    STATICCALL = auto()
    NONZERO_VALUE = auto()

    def should_succeed(self, func: FunctionInfo) -> bool:
        """Return whether this scenario succeeds for the given function."""
        if self == CallScenario.SUCCESS:
            return True
        if self == CallScenario.NONZERO_VALUE:
            return func.is_payable
        return False

    def call_code(
        self,
        func: FunctionInfo,
        gas: int | Bytecode,
        args_offset: int = 28,
        ret_offset: int = 0,
        ret_size: int = 0,
    ) -> Bytecode:
        """Generate bytecode for this call scenario."""
        common: dict[str, Any] = dict(
            gas=gas,
            address=STAKING_PRECOMPILE,
            args_offset=args_offset,
            ret_offset=ret_offset,
            ret_size=ret_size,
        )
        match self:
            case CallScenario.SUCCESS:
                return build_calldata(
                    func.selector, func.calldata_size
                ) + Op.CALL(args_size=func.calldata_size, **common)
            case CallScenario.WRONG_SELECTOR:
                return Op.MSTORE(0, 0xDEADBEEF) + Op.CALL(
                    args_size=4, **common
                )
            case CallScenario.TRUNCATED_SELECTOR:
                return Op.MSTORE(0, func.selector) + Op.CALL(
                    args_size=3, **common
                )
            case CallScenario.SHORT_CALLDATA:
                return build_calldata(
                    func.selector, func.calldata_size
                ) + Op.CALL(args_size=func.calldata_size - 1, **common)
            case CallScenario.EXTRA_CALLDATA:
                return build_calldata(
                    func.selector, func.calldata_size
                ) + Op.CALL(args_size=func.calldata_size + 1, **common)
            case CallScenario.NONZERO_VALUE:
                return build_calldata(
                    func.selector, func.calldata_size
                ) + Op.CALL(args_size=func.calldata_size, value=1, **common)
            case CallScenario.STATICCALL:
                return build_calldata(
                    func.selector, func.calldata_size
                ) + Op.STATICCALL(args_size=func.calldata_size, **common)


pytestmark = [
    pytest.mark.valid_from("MONAD_EIGHT"),
    pytest.mark.pre_alloc_group(
        "staking_precompile_tests",
        reason="Tests staking precompile",
    ),
]


@pytest.mark.parametrize(
    "func",
    [pytest.param(f, id=f.name) for f in ALL_FUNCTIONS],
)
@pytest.mark.parametrize(
    "input_size",
    [
        pytest.param(0, id="empty"),
        pytest.param(3, id="three_bytes"),
        pytest.param(4, id="selector_only"),
        pytest.param(5, id="five_bytes"),
        pytest.param(36, id="one_param"),
        pytest.param(37, id="one_param_plus"),
        pytest.param(68, id="two_params"),
        pytest.param(69, id="two_params_plus"),
        pytest.param(100, id="three_params"),
        pytest.param(101, id="three_params_plus"),
    ],
)
def test_input_size(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    func: FunctionInfo,
    input_size: int,
    fork: Fork,
) -> None:
    """
    Test precompile behavior with various input sizes.

    Calldata must be exactly the expected size for the function.
    """
    if input_size < 4:
        calldata_setup = Op.PUSH32(b"\xff" * 32) + Op.PUSH1(0) + Op.MSTORE
        args_offset = 32 - input_size
        gas = GAS_UNKNOWN_SELECTOR + 10000
    else:
        mem_size = max(input_size, func.calldata_size)
        calldata_setup = build_calldata(func.selector, mem_size)
        args_offset = 28
        gas = func.gas_cost + 10000

    contract = (
        calldata_setup
        + Op.SSTORE(
            slot_call_success,
            Op.CALL(
                gas=gas,
                address=STAKING_PRECOMPILE,
                args_offset=args_offset,
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

    should_succeed = input_size == func.calldata_size

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_call_success: 1 if should_succeed else 0,
                    slot_return_size: (
                        func.return_size if should_succeed else 0
                    ),
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

    Known selectors succeed with correct calldata, unknown selectors revert.
    """
    func = FUNC_BY_SELECTOR.get(selector)

    if func is not None:
        calldata_setup = build_calldata(func.selector, func.calldata_size)
        args_size = func.calldata_size
        gas = func.gas_cost + 10000
        should_succeed = True
        expected_return_size = func.return_size
    else:
        calldata_setup = Op.PUSH4(selector) + Op.PUSH1(0) + Op.MSTORE
        args_size = 4
        gas = GAS_UNKNOWN_SELECTOR + 10000
        should_succeed = False
        expected_return_size = 0

    contract = (
        calldata_setup
        + Op.SSTORE(
            slot_call_success,
            Op.CALL(
                gas=gas,
                address=STAKING_PRECOMPILE,
                args_offset=28,
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
                args_offset=28,
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
                args_offset=28,
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

    should_succeed = call_opcode == Op.CALL

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_call_success: 1 if should_succeed else 0,
                    slot_return_size: (
                        func.return_size if should_succeed else 0
                    ),
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
@pytest.mark.parametrize("scenario", CallScenario)
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
    ret_offset = _calldata_mem_end(func.calldata_size)
    rdc_offset = ret_offset + 32

    contract = (
        Op.SSTORE(
            slot_call_success,
            scenario.call_code(
                func,
                gas=func.gas_cost + 10000,
                ret_offset=ret_offset,
                ret_size=32,
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
    )

    ok = scenario.should_succeed(func)

    blockchain_test(
        pre=pre,
        post={
            contract_address: Account(
                storage={
                    slot_call_success: 1 if ok else 0,
                    slot_return_size: (func.return_size if ok else 0),
                    slot_ret_buffer_value: (
                        func.first_return_word if ok else 0
                    ),
                    slot_return_value: (func.first_return_word if ok else 0),
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
@pytest.mark.parametrize("scenario", CallScenario)
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

    contract = (
        Op.SSTORE(slot_code_worked, value_code_worked)
        + Op.SSTORE(slot_call_success, 1)
        + Op.SSTORE(slot_all_gas_consumed, 1)
        + Op.SSTORE(
            slot_call_success,
            scenario.call_code(func, gas=Op.GAS),
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
                args_offset=28,
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
                    slot_return_size: 0,
                    slot_code_worked: value_code_worked,
                }
            ),
        },
        blocks=[Block(txs=[tx])],
    )
