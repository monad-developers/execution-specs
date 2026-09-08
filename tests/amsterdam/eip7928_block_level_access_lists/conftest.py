"""Pytest (plugin) definitions local to EIP-7928 tests."""

import pytest

NO_BAL_DEPENDENCY_MARKER = "no_bal_dependency"


def pytest_configure(config: pytest.Config) -> None:
    """Register the marker that keeps a test valid on Monad forks."""
    config.addinivalue_line(
        "markers",
        f"{NO_BAL_DEPENDENCY_MARKER}: the test's post-state assertions hold "
        "without a block access list, so it also runs on forks that do not "
        "implement EIP-7928",
    )


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """
    Mark the tests in this subdir as not valid for Monad forks.

    Monad forks carry the block access list hash header field fixed at
    zero and build no list, so the transition tool returns none and every
    `expected_block_access_list` is dropped unverified. Tests marked
    `no_bal_dependency` assert intra-block state visibility through the
    post-state instead, and stay valid.
    """
    if metafunc.definition.get_closest_marker(NO_BAL_DEPENDENCY_MARKER):
        return
    metafunc.definition.add_marker(
        pytest.mark.not_valid_for("MONAD_EIGHT", subsequent_forks=True)
    )
