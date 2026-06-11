"""Fixtures for the EIP-7934 RLP block size limit tests."""

import pytest
from execution_testing import (
    Address,
    Alloc,
    Environment,
    Op,
)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Mark all tests in this subdir as not valid for Monad forks."""
    metafunc.definition.add_marker(
        pytest.mark.not_valid_for("MONAD_EIGHT", subsequent_forks=True)
    )


@pytest.fixture
def post() -> Alloc:
    """Post state allocation fixture."""
    return Alloc()


@pytest.fixture
def env() -> Environment:
    """Environment fixture with a specified gas limit."""
    return Environment(gas_limit=100_000_000)


@pytest.fixture
def contract_recipient(pre: Alloc) -> Address:
    """Deploy a simple contract that can receive large calldata."""
    contract_code = Op.SSTORE(0, Op.CALLDATASIZE) + Op.STOP
    return pre.deploy_contract(contract_code)
