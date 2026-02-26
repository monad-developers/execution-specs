"""Helper functions for staking precompile tests."""

from execution_testing import Bytecode, Op
from execution_testing.forks.helpers import Fork


def generous_gas(fork: Fork) -> int:
    """Return generous parametrized gas to always be enough."""
    constant = 1_000_000
    gas_costs = fork.gas_costs()
    sstore_cost = gas_costs.G_STORAGE_SET + gas_costs.G_COLD_SLOAD
    deploy_cost = gas_costs.G_CODE_DEPOSIT_BYTE * len(Op.STOP)
    access_cost = gas_costs.G_COLD_ACCOUNT_ACCESS
    return constant + 5 * sstore_cost + deploy_cost + 5 * access_cost


def build_calldata(selector: int, calldata_size: int) -> Bytecode:
    """
    Build bytecode that stores a selector and padding in memory.

    Place the 4-byte selector at mem[28:32] and fill mem[32:..] with
    zero-padded ABI words so that the total args region is calldata_size
    bytes.
    """
    code = Op.PUSH4(selector) + Op.PUSH1(0) + Op.MSTORE
    # If calldata_size > 4, we need additional words in memory
    extra = calldata_size - 4
    if extra > 0:
        words = (extra + 31) // 32
        for i in range(words):
            # Store a dummy uint256 value (1) for each ABI param word
            code += Op.MSTORE(32 + i * 32, 1)
    return code
