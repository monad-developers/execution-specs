"""Fixtures for the EIP-8282 builder execution request tests."""

import pytest

from ...common.system_contract_request_fixtures import (
    blocks,  # noqa: F401
    included_requests,  # noqa: F401
    system_contract_interactions_per_block_copy,  # noqa: F401
    timestamp,  # noqa: F401
)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Mark all tests in this subdir as not valid for Monad forks."""
    metafunc.definition.add_marker(
        pytest.mark.not_valid_for("MONAD_EIGHT", subsequent_forks=True)
    )
