"""
Helper types, functions and classes for testing MIP-8 pageified storage.
"""

from execution_testing.forks.helpers import Fork

from .spec import Spec


def page_index(slot: int) -> int:
    """Return the page index for a given storage slot."""
    return slot >> 7


def generous_gas(fork: Fork) -> int:
    """
    Return generous gas to always be enough for MIP-8 tests.

    Must be large enough that after a child CREATE/CREATE2 burns
    63/64 of forwarded gas, 1/64 remainder covers parent's
    post-call SSTORE + SLOAD (cold page read + new slot write).
    """
    gas_costs = fork.gas_costs()
    return (
        5_000_000
        + Spec.GAS_COLD_PAGE_READ * 10
        + Spec.GAS_PAGE_WRITE * 10
        + Spec.GAS_NEW_SLOT * 10
        + gas_costs.G_COLD_ACCOUNT_ACCESS * 5
    )
