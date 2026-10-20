"""Helpers for testing EIP-7954."""

from execution_testing import Fork
from execution_testing.forks import MONAD_EIGHT


def billed_gas(fork: Fork, gas_limit: int, gas_used: int) -> int:
    """
    Return the gas a successful transaction's receipt reports.

    Monad bills the whole limit, with no refund and no floor comparison.
    """
    return gas_limit if fork >= MONAD_EIGHT else gas_used
