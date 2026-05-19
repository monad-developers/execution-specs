"""
Helper types, functions and classes for testing MIP-8 pageified storage.
"""

import pytest
from execution_testing import Op
from execution_testing.forks.helpers import Fork

# Tuples (v_original, v_current, v_new) covering all zero-ness and
# same-ness combinations of the slot value at tx start (v_original),
# right before the measured SSTORE (v_current), and after (v_new).
# Letters X, Y, Z represent distinct nonzero values.
STATE_TRANSITIONS = [
    pytest.param(0, 0, 0, id="0_0_0"),
    pytest.param(0, 0, 1, id="0_0_X"),
    pytest.param(0, 1, 0, id="0_X_0"),
    pytest.param(0, 1, 1, id="0_X_X"),
    pytest.param(0, 1, 2, id="0_X_Y"),
    pytest.param(5, 0, 0, id="X_0_0"),
    pytest.param(5, 0, 5, id="X_0_X"),
    pytest.param(5, 0, 6, id="X_0_Y"),
    pytest.param(5, 5, 0, id="X_X_0"),
    pytest.param(5, 5, 5, id="X_X_X"),
    pytest.param(5, 5, 6, id="X_X_Y"),
    pytest.param(5, 6, 0, id="X_Y_0"),
    pytest.param(5, 6, 5, id="X_Y_X"),
    pytest.param(5, 6, 6, id="X_Y_Y"),
    pytest.param(5, 6, 7, id="X_Y_Z"),
]


def expected_setup_growth(orig: int, curr: int) -> tuple[int, int]:
    """Return (current_state_growth, net_state_growth) after orig→curr."""
    if orig == 0 and curr != 0:
        return (1, 1)
    if orig != 0 and curr == 0:
        return (-1, 0)
    return (0, 0)


def page_index(slot: int) -> int:
    """Return the page index for a given storage slot."""
    return slot >> 7


def fresh_sstore_cold(fork: Fork) -> int:
    """Gas for a fresh-slot SSTORE on a cold page."""
    return Op.SSTORE(
        key_warm=False,
        page_load_warm=False,
        page_write_warm=False,
        current_value=0,
        new_value=1,
        current_state_growth=0,
        net_state_growth=0,
    ).gas_cost(fork)


def fresh_sstore_warm(fork: Fork) -> int:
    """Gas for a fresh-slot SSTORE on a warm page."""
    return Op.SSTORE(
        key_warm=True,
        page_load_warm=True,
        page_write_warm=True,
        current_value=0,
        new_value=1,
        current_state_growth=0,
        net_state_growth=0,
    ).gas_cost(fork)


def generous_gas(fork: Fork) -> int:
    """
    Return gas enough for typical MIP-8 tests.

    Covers a handful of fresh SSTOREs plus measurement chain.
    Tests doing full-page sweeps add `full_page_sweep_gas(fork)`.
    Tests with child CREATE/CREATE2 use `generous_gas_with_create()`.
    """
    return fork.gas_costs().TX_BASE + 8 * fresh_sstore_cold(fork)


def generous_gas_with_create(fork: Fork) -> int:
    """
    Return gas sized so CREATE/CREATE2 children leave the parent
    (1/64 of forwarded gas) enough for a post-call cold SSTORE
    plus measurement overhead.
    """
    return fork.gas_costs().TX_BASE + 64 * (fresh_sstore_cold(fork) + 5_000)


def full_page_sweep_gas(fork: Fork) -> int:
    """
    Gas for SSTORE on every slot of a single page.

    First slot pays cold I/O + state growth; remaining 127 pay
    only BASE + state growth.
    """
    return fresh_sstore_cold(fork) + 127 * fresh_sstore_warm(fork)
