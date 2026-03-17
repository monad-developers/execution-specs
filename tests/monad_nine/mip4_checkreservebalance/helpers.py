"""Helper functions and fixtures for MIP-4 reserve balance precompile tests."""

from typing import Callable

from execution_testing import Address, Bytecode, Op
from execution_testing.forks.helpers import Fork

from .spec import Spec

RefillCall = Callable[[Address | Bytecode], Bytecode]
RefillFactory = Callable[[], RefillCall]

# Bytecode to store the selector at mem[28:32]
SELECTOR_SETUP = Op.MSTORE(0, Spec.DIPPED_INTO_RESERVE_SELECTOR)


def call_dipped_into_reserve() -> Bytecode:
    """
    Generate bytecode to call dippedIntoReserve() precompile.

    Returns bytecode that leaves the result (0 or 1) on the stack.
    The precompile must be invoked via CALL (not STATICCALL/DELEGATECALL/
    CALLCODE).
    """
    return (
        SELECTOR_SETUP
        + Op.CALL(
            gas=Op.GAS,
            address=Spec.RESERVE_BALANCE_PRECOMPILE,
            args_offset=28,
            args_size=4,
            ret_offset=0,
            ret_size=32,
        )
        + Op.POP
        + Op.MLOAD(0)
    )


def generous_gas(fork: Fork) -> int:
    """Return generous parametrized gas to always be enough."""
    constant = 100_000
    gas_costs = fork.gas_costs()
    sstore_cost = gas_costs.GAS_STORAGE_SET + gas_costs.GAS_COLD_SLOAD
    deploy_cost = gas_costs.GAS_CODE_DEPOSIT_PER_BYTE * len(Op.STOP)
    access_cost = gas_costs.GAS_COLD_ACCOUNT_ACCESS
    selfdestruct_cost = gas_costs.GAS_SELF_DESTRUCT
    return (
        constant
        + 5 * sstore_cost
        + deploy_cost
        + 6 * access_cost
        + 3 * selfdestruct_cost
    )
