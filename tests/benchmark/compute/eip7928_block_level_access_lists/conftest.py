"""Pytest (plugin) definitions local to EIP-7928 benchmark tests."""

import pytest


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Mark all tests in this subdir as not valid for Monad forks."""
    metafunc.definition.add_marker(
        pytest.mark.not_valid_for("MONAD_EIGHT", subsequent_forks=True)
    )
