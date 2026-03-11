"""Helper functions for staking precompile tests."""

from execution_testing import Bytecode, Op
from execution_testing.forks.helpers import Fork


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


def build_calldata(selector: int, calldata_size: int) -> Bytecode:
    """
    Build bytecode that stores a selector and padding in memory.

    Place the 4-byte selector at mem[60:64] and assume mem[64:..] has
    rest of calldata
    """
    selector_calldata_offset = 32
    code = Op.MSTORE(selector_calldata_offset, selector)
    return code
