"""Test filling the `runloop_test` fixture format."""

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest

from execution_testing.test_types import MonadRunloopDefaults

test_module = textwrap.dedent(
    """\
    import pytest

    from execution_testing import Transaction

    @pytest.mark.valid_from("MONAD_NINE")
    def test_runloop(state_test, pre) -> None:
        tx = Transaction(sender=pre.fund_eoa(), gas_limit=100_000)
        state_test(pre=pre, post={}, tx=tx)
    """
)


@pytest.fixture()
def fill_args(testdir: pytest.Testdir) -> list[str]:
    """Copy fill ini and return base fill args."""
    testdir.copy_example(
        name=(
            "src/execution_testing/cli/pytest_commands"
            "/pytest_ini_files/pytest-fill.ini"
        )
    )
    testdir.makepyfile(test_module)
    return ["-c", "pytest-fill.ini", "--no-html", "--output", "fixtures"]


def load_single_fixture(testdir: pytest.Testdir, format_dir: str) -> Any:
    """Load the only fixture written under the given format directory."""
    files = list(
        (Path(testdir.tmpdir) / "fixtures" / format_dir).rglob("*.json")
    )
    assert len(files) == 1
    fixtures = json.loads(files[0].read_text())
    assert len(fixtures) == 1
    return next(iter(fixtures.values()))


def test_fill_blockchain_and_runloop_formats(
    testdir: pytest.Testdir, fill_args: list[str]
) -> None:
    """
    One fill with both markers writes `blockchain_tests` and
    `runloop_tests` siblings; the runloop fixture carries EestNet's
    chain id and the runloop-stamped header fields regardless of
    `--chain-id`, while the blockchain fixture is unaffected.
    """
    result = testdir.runpytest(
        *fill_args,
        "-m",
        "blockchain_test or runloop_test",
        "--fork",
        "MONAD_NINE",
        "--chain-id",
        "143",
    )
    result.assert_outcomes(passed=2)

    blockchain = load_single_fixture(testdir, "blockchain_tests")
    assert int(blockchain["config"]["chainid"], 16) == 143
    header = blockchain["blocks"][0]["blockHeader"]
    assert header["extraData"] != "0x" + "00" * 32
    assert int(header["gasLimit"], 16) != MonadRunloopDefaults.gas_limit
    assert int(header["mixHash"], 16) != MonadRunloopDefaults.prev_randao

    runloop = load_single_fixture(testdir, "runloop_tests")
    assert (
        int(runloop["config"]["chainid"], 16) == MonadRunloopDefaults.chain_id
    )
    header = runloop["blocks"][0]["blockHeader"]
    assert header["extraData"] == "0x" + "00" * 32
    assert int(header["gasLimit"], 16) == MonadRunloopDefaults.gas_limit
    assert int(header["mixHash"], 16) == MonadRunloopDefaults.prev_randao


def test_runloop_format_not_generated_for_canonical_forks(
    testdir: pytest.Testdir, fill_args: list[str]
) -> None:
    """
    `runloop_test` items only exist for monad forks, so canonical-fork
    fills (e.g. the mainnet release with `--generate-all-formats`) never
    produce `runloop_tests` fixtures.
    """
    testdir.makepyfile(
        test_module.replace('valid_from("MONAD_NINE")', 'valid_from("Paris")')
    )
    result = testdir.runpytest(
        *fill_args,
        "-m",
        "runloop_test",
        "--fork",
        "Prague",
    )
    assert result.ret == pytest.ExitCode.NO_TESTS_COLLECTED


test_module_chain_config = textwrap.dedent(
    """\
    import pytest

    from execution_testing import Account, Op, Transaction

    @pytest.mark.valid_from("MONAD_NINE")
    def test_chain_config(state_test, pre, chain_config) -> None:
        contract = pre.deploy_contract(Op.SSTORE(1, Op.CHAINID) + Op.STOP)
        tx = Transaction(
            sender=pre.fund_eoa(), to=contract, gas_limit=100_000
        )
        state_test(
            pre=pre,
            post={contract: Account(storage={1: chain_config.chain_id})},
            tx=tx,
        )
    """
)


def test_chain_config_follows_fixture_format(
    testdir: pytest.Testdir, fill_args: list[str]
) -> None:
    """
    The `chain_config` fixture must resolve per item: the blockchain
    item runs first in the session, and a `chain_config` cached from it
    would sign and verify the runloop item with the wrong chain id
    (regression test for the session-scoped `chain_config`).
    """
    testdir.makepyfile(test_module_chain_config)
    result = testdir.runpytest(
        *fill_args,
        "-m",
        "blockchain_test or runloop_test",
        "--fork",
        "MONAD_NINE",
        "--chain-id",
        "143",
    )
    result.assert_outcomes(passed=2)
