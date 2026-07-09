"""Prague-wide test configuration."""


def pytest_ignore_collect(collection_path, config):
    """Skip EIP-2537 BLS precompile tests on execute remote (their conftest
    helpers fail to import on Monad and the precompiles are not deployed)."""
    if config.getoption("--rpc-endpoint", default=None) and (
        "eip2537" in str(collection_path)
    ):
        return True
    return None
