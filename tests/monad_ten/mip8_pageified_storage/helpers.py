"""
Helper types, functions and classes for testing MIP-8 pageified storage.
"""

from execution_testing.forks.helpers import Fork

from .spec import Spec


def page_index(slot: int) -> int:
    """Return the page index for a given storage slot."""
    return slot >> 7


def generous_gas(fork: Fork) -> int:
    """Return generous gas to always be enough for MIP-8 tests."""
    fresh_sstore_cold = (
        Spec.GAS_PAGE_LOAD_COST
        + Spec.GAS_PAGE_WRITE_COST
        + Spec.GAS_PAGE_BASE_COST
        + Spec.GAS_PAGE_STATE_GROWTH_COST
    )
    full_page_sweep = 128 * fresh_sstore_cold
    return fork.gas_costs().TX_BASE + 2 * full_page_sweep + 100_000
