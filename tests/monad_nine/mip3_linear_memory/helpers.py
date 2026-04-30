"""
Helper types, functions and classes for testing reserve balance.
"""

from execution_testing import (
    Bytecode,
    Op,
)
from execution_testing.forks.helpers import Fork
from execution_testing.vm import Opcode

from .spec import Spec

COLD_ACCESS_TARGET_ADDRESS = 0x1234567890ABCDEF1234567890ABCDEF12345678


def prepare_stack_memory_opcode(opcode: Opcode, size: int) -> Bytecode:
    """Prepare valid stack for memory-allocating opcode."""
    if opcode == Op.CALLDATACOPY:
        # stack: destOffset, offset, size
        return Op.PUSH32(size) + Op.PUSH0 + Op.PUSH0
    elif opcode == Op.CODECOPY:
        # stack: destOffset, offset, size
        return Op.PUSH32(size) + Op.PUSH0 + Op.PUSH0
    elif opcode == Op.EXTCODECOPY:
        # stack: address, destOffset, offset, size
        return (
            Op.PUSH32(size)
            + Op.PUSH0
            + Op.PUSH0
            + Op.PUSH20(COLD_ACCESS_TARGET_ADDRESS)
        )
    elif opcode == Op.MCOPY:
        # stack: srcOffset, destOffset, size
        return Op.PUSH32(size) + Op.PUSH0 + Op.PUSH0
    elif opcode == Op.SHA3:
        # stack: offset, size
        return Op.PUSH32(size) + Op.PUSH0
    elif opcode in (Op.LOG0, Op.LOG1, Op.LOG2, Op.LOG3, Op.LOG4):
        # stack: offset, size, [topics...]
        # Prepare stack with topics based on opcode
        num_topics = opcode.int() - Op.LOG0.int()  # Extract N from LOGn
        topics_code = Bytecode()
        for i in range(num_topics):
            topics_code += Op.PUSH32(i + 1)
        return topics_code + Op.PUSH32(size) + Op.PUSH0
    elif opcode in (Op.RETURN, Op.REVERT):
        # stack: offset, size
        return Op.PUSH32(size) + Op.PUSH0
    elif opcode == Op.CREATE:
        # stack: value, offset, size
        return Op.PUSH32(size) + Op.PUSH0 + Op.PUSH0
    elif opcode == Op.CREATE2:
        # stack: value, offset, size, salt
        # Use counter-based salt to avoid address conflicts
        slot_counter = 0x0
        code_increment_counter = (
            Op.TLOAD(slot_counter)
            + Op.DUP1
            + Op.TSTORE(slot_counter, Op.PUSH1(1) + Op.ADD)
        )
        return code_increment_counter + Op.PUSH32(size) + Op.PUSH0 + Op.PUSH0
    elif opcode == Op.MSTORE:
        # stack: offset, value
        offset = max(0, size - 32) if size > 0 else 0
        return Op.PUSH1(0xFF) + Op.PUSH32(offset)
    elif opcode == Op.MSTORE8:
        # stack: offset, value
        offset = max(0, size - 1) if size > 0 else 0
        return Op.PUSH1(0xFF) + Op.PUSH32(offset)
    elif opcode == Op.MLOAD:
        # stack: offset
        offset = max(0, size - 32) if size > 0 else 0
        return Op.PUSH32(offset)
    elif opcode == Op.CALL:
        # stack: gas, address, value, argsOffset, argsSize, retOffset, retSize
        return (
            Op.PUSH32(size)
            + Op.PUSH0  # retSize, retOffset
            + Op.PUSH0
            + Op.PUSH0  # argsSize, argsOffset
            + Op.PUSH0  # value
            + Op.PUSH20(COLD_ACCESS_TARGET_ADDRESS)
            + Op.GAS  # use all available gas
        )
    elif opcode == Op.CALLCODE:
        # stack: gas, address, value, argsOffset, argsSize, retOffset, retSize
        return (
            Op.PUSH32(size)
            + Op.PUSH0  # retSize, retOffset
            + Op.PUSH0
            + Op.PUSH0  # argsSize, argsOffset
            + Op.PUSH0  # value
            + Op.PUSH20(COLD_ACCESS_TARGET_ADDRESS)
            + Op.GAS  # use all available gas
        )
    elif opcode == Op.DELEGATECALL:
        # stack: gas, address, argsOffset, argsSize, retOffset, retSize
        return (
            Op.PUSH32(size)
            + Op.PUSH0  # retSize, retOffset
            + Op.PUSH0
            + Op.PUSH0  # argsSize, argsOffset
            + Op.PUSH20(COLD_ACCESS_TARGET_ADDRESS)
            + Op.GAS  # use all available gas
        )
    elif opcode == Op.STATICCALL:
        # stack: gas, address, argsOffset, argsSize, retOffset, retSize
        return (
            Op.PUSH32(size)
            + Op.PUSH0  # retSize, retOffset
            + Op.PUSH0
            + Op.PUSH0  # argsSize, argsOffset
            + Op.PUSH20(COLD_ACCESS_TARGET_ADDRESS)
            + Op.GAS  # use all available gas
        )
    else:
        raise ValueError(f"Unknown memory opcode: {opcode}")


def generous_gas(fork: Fork) -> int:
    """
    Return generous parametrized gas to always be enough.
    """
    constant = 100_000
    gas_costs = fork.gas_costs()
    sstore_cost = gas_costs.GAS_STORAGE_SET + gas_costs.GAS_COLD_SLOAD
    deploy_cost = gas_costs.GAS_CODE_DEPOSIT_PER_BYTE * len(Op.STOP)
    access_cost = gas_costs.GAS_COLD_ACCOUNT_ACCESS
    # Assume up to 5 memory expansions to the max size
    linear_memory_expansion_cost = 5 * fork.memory_expansion_gas_calculator()(
        new_bytes=Spec.MAX_TX_MEMORY_USAGE
    )
    # Account for per-word operation costs, assume 5 times up to max
    max_words = 5 * Spec.MAX_TX_MEMORY_USAGE // 32
    per_word_op_cost = (
        max(gas_costs.GAS_COPY, gas_costs.GAS_KECCAK256_PER_WORD) * max_words
    )
    return (
        constant
        + sstore_cost
        + deploy_cost
        + 5 * access_cost
        + linear_memory_expansion_cost
        + per_word_op_cost
    )
