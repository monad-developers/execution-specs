"""Helper functions for staking precompile tests."""

from execution_testing import Bytecode, Op
from execution_testing.forks.helpers import Fork

# Memory layout for precompile call tests.
# MSTORE writes a 32-byte word; the 4-byte selector sits at the
# rightmost bytes of that word.
WRONG_SEL_MSTORE_OFFSET = 0
CORRECT_SEL_MSTORE_OFFSET = 32

# Where the 4-byte selectors actually start in memory:
WRONG_SEL_ARGS_OFFSET = WRONG_SEL_MSTORE_OFFSET + 28
CORRECT_SEL_ARGS_OFFSET = CORRECT_SEL_MSTORE_OFFSET + 28


def generous_gas(fork: Fork) -> int:
    """Return generous parametrized gas to always be enough."""
    constant = 1_000_000
    # Transition forks don't expose gas_costs directly; resolve to the
    # post-transition fork (no-op for regular forks).
    gas_costs = fork.transitions_to().gas_costs()
    sstore_cost = gas_costs.STORAGE_SET + gas_costs.COLD_STORAGE_ACCESS
    deploy_cost = gas_costs.CODE_DEPOSIT_PER_BYTE * len(Op.STOP)
    access_cost = gas_costs.COLD_ACCOUNT_ACCESS
    selfdestruct_cost = gas_costs.OPCODE_SELFDESTRUCT_BASE
    return (
        constant
        + 5 * sstore_cost
        + deploy_cost
        + 6 * access_cost
        + 3 * selfdestruct_cost
    )


def tx_calldata(selector: int, calldata_size: int) -> bytes:
    """Build raw calldata bytes for a direct transaction."""
    sel_bytes = selector.to_bytes(4, "big")
    return sel_bytes + b"\x00" * max(0, calldata_size - 4)


def calldata_mem_end(calldata_size: int) -> int:
    """
    Return safe first memory offset past all written regions.

    Accounts for EXTRA_CALLDATA (+1 byte beyond calldata_size)
    and both MSTORE regions.
    """
    return (
        max(
            CORRECT_SEL_ARGS_OFFSET + calldata_size,
            CORRECT_SEL_MSTORE_OFFSET + 32,
        )
        + 1
    )


def build_calldata(selector: int, calldata_size: int) -> Bytecode:
    """
    Build bytecode that stores a selector in memory.

    Place the 4-byte selector at
    mem[CORRECT_SEL_ARGS_OFFSET:CORRECT_SEL_ARGS_OFFSET+4]
    and leave the rest zero-initialized for calldata args.
    """
    # Not used b/c input is zero beyond the selector;
    # left here only for clarity
    del calldata_size
    code = Op.MSTORE(CORRECT_SEL_MSTORE_OFFSET, selector)
    return code
