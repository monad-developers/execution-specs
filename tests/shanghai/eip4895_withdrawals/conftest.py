"""Fixtures for the EIP-4895 withdrawals tests."""

import pytest
from execution_testing import Alloc, Environment


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Mark all tests in this subdir as not valid for Monad forks."""
    metafunc.definition.add_marker(
        pytest.mark.not_valid_for("MONAD_EIGHT", subsequent_forks=True)
    )


@pytest.fixture
def env() -> Environment:
    """Environment fixture."""
    return Environment()


@pytest.fixture
def post() -> Alloc:
    """Post state fixture."""
    return Alloc()
