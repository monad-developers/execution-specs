"""Tests for the `consume direct` block execution timing report."""

from pathlib import Path
from typing import Any, List, Sequence, cast

import pytest

from execution_testing.cli.pytest_commands.plugins.consume.direct.timing_report import (  # noqa: E501
    BlockTiming,
    BlockTimingReporter,
    _fork_rank,
    _render_csv,
    _rows_from_payload,
    _sorted_rows,
    _split_fixture_id,
    pytest_configure,
    timing_payload,
)
from execution_testing.fixtures import BlockchainFixture
from execution_testing.fixtures.consume import TestCaseIndexFile
from execution_testing.forks import MONAD_NINE, MONAD_TEN

FIXTURE_ID = (
    "tests/monad_ten/mip8_pageified_storage/test_perf_regression.py"
    "::test_page_ops[sstore_fresh-k0-fork_MONAD_NINE-blockchain_test]"
)


class TimingConsumer:
    """A consumer implementing the `BlockTimingReporter` capability."""

    def __init__(self, timings: Sequence[BlockTiming]):  # noqa: D107
        self.timings = timings

    def last_block_timings(self) -> Sequence[BlockTiming]:
        """Return the canned timings."""
        return self.timings


class SilentConsumer:
    """A consumer that does not measure block timing."""


def _test_case(fixture_id: str = FIXTURE_ID) -> TestCaseIndexFile:
    """Build an index test case for `fixture_id` at MONAD_NINE."""
    return TestCaseIndexFile(
        id=fixture_id,
        fork=MONAD_NINE,
        format=BlockchainFixture,
        json_path=Path("dummy.json"),
    )


class _StubPluginManager:
    """Records what the plugin registers."""

    def __init__(self) -> None:  # noqa: D107
        self.registered: List[str] = []

    def register(self, plugin: object, name: str) -> None:  # noqa: ARG002
        """Record a registration."""
        self.registered.append(name)


class _StubConfig:
    """Just enough config surface for `pytest_configure`."""

    def __init__(self, **options: Any) -> None:  # noqa: D107
        self._options = options
        self.pluginmanager = _StubPluginManager()

    def getoption(self, name: str, default: Any = None) -> Any:
        """Return the option, or `default` when it was never registered."""
        return self._options.get(name, default)


def _configure(**options: Any) -> _StubConfig:
    """Run `pytest_configure` against a stub config."""
    config = _StubConfig(**options)
    pytest_configure(cast(pytest.Config, config))
    return config


def test_configure_ignores_a_run_without_the_flag() -> None:
    """Nothing is registered unless --timing-report was passed."""
    assert _configure().pluginmanager.registered == []


def test_configure_registers_for_consume_direct() -> None:
    """A direct run with the flag gets the reporting plugin."""
    config = _configure(timing_report=True, fixture_consumer_bin=[])
    assert config.pluginmanager.registered == ["consume-timing-report"]


def test_configure_rejects_a_non_direct_command() -> None:
    """Without `--bin` registered the command cannot report timing."""
    with pytest.raises(pytest.UsageError, match="consume direct"):
        _configure(timing_report=True)


@pytest.mark.parametrize("numprocesses", [2, "auto", "logical"])
def test_configure_rejects_xdist(numprocesses: Any) -> None:
    """Parallel workers make the measurements meaningless."""
    with pytest.raises(pytest.UsageError, match="xdist"):
        _configure(
            timing_report=True,
            fixture_consumer_bin=[],
            numprocesses=numprocesses,
        )


def test_configure_allows_explicitly_disabled_xdist() -> None:
    """`-n 0` is serial, so it is allowed."""
    config = _configure(
        timing_report=True, fixture_consumer_bin=[], numprocesses=0
    )
    assert config.pluginmanager.registered == ["consume-timing-report"]


def test_split_fixture_id_drops_fork_and_format() -> None:
    """The fork and format tokens are removed, the parameters kept."""
    test, params = _split_fixture_id(
        FIXTURE_ID, "MONAD_NINE", "blockchain_test"
    )
    assert test == "test_perf_regression::test_page_ops"
    assert params == "sstore_fresh-k0"


def test_split_fixture_id_keeps_hyphenated_params() -> None:
    """A parameter that merely looks like a format tag is kept."""
    fixture_id = "a/b/test_mod.py::test_f[blockchain_test_sync-fork_Prague-blockchain_test]"  # noqa: E501
    test, params = _split_fixture_id(fixture_id, "Prague", "blockchain_test")
    assert test == "test_mod::test_f"
    assert params == "blockchain_test_sync"


def test_split_fixture_id_without_params() -> None:
    """An unparametrized fixture id yields empty parameters."""
    test, params = _split_fixture_id(
        "a/test_mod.py::test_f", "Prague", "blockchain_test"
    )
    assert test == "test_mod::test_f"
    assert params == ""


def test_fork_rank_orders_by_release() -> None:
    """Known forks rank chronologically, unknown ones sort last."""
    assert _fork_rank(MONAD_NINE) < _fork_rank(MONAD_TEN)
    assert _fork_rank(None) > _fork_rank(MONAD_TEN)


def test_timing_payload_from_reporting_consumer() -> None:
    """A reporting consumer's timings become one test's payload."""
    consumer = TimingConsumer([{"block": 1, "total_us": 10}])

    assert timing_payload(consumer, _test_case()) == {
        "test": "test_perf_regression::test_page_ops",
        "params": "sstore_fresh-k0",
        "fork": "MONAD_NINE",
        "fork_rank": _fork_rank(MONAD_NINE),
        "blocks": [{"block": 1, "total_us": 10}],
    }


@pytest.mark.parametrize(
    "consumer",
    [SilentConsumer(), TimingConsumer([])],
    ids=["no_capability", "nothing_measured"],
)
def test_timing_payload_none_without_timings(consumer: object) -> None:
    """No payload when the consumer reports no timing."""
    assert timing_payload(consumer, _test_case()) is None


def test_capability_probe_is_structural() -> None:
    """The capability is detected by shape, without inheritance."""
    assert isinstance(TimingConsumer([]), BlockTimingReporter)
    assert not isinstance(SilentConsumer(), BlockTimingReporter)


def test_render_csv_uses_reported_metric_keys() -> None:
    """Metric columns follow the consumer's own keys and order."""
    payload = {
        "test": "test_mod::test_f",
        "params": "-",
        "fork": "MONAD_NINE",
        "fork_rank": 0,
        "blocks": [
            {"block": 1, "gas": 100, "total_us": 10},
            {"block": 2, "gas": 200, "total_us": 20},
        ],
    }
    csv = _render_csv(_rows_from_payload(payload))
    assert csv.splitlines()[0] == "test,params,fork,block,gas,total_us"
    assert csv.splitlines()[1] == "test_mod::test_f,-,MONAD_NINE,1,100,10"


def test_render_csv_blanks_missing_metrics() -> None:
    """A consumer reporting other metrics contributes its own columns."""
    rows = _rows_from_payload(
        {
            "test": "t",
            "params": "-",
            "fork": "MONAD_NINE",
            "fork_rank": 0,
            "blocks": [{"block": 1, "total_us": 10}],
        }
    ) + _rows_from_payload(
        {
            "test": "t",
            "params": "-",
            "fork": "MONAD_NINE",
            "fork_rank": 0,
            "blocks": [{"block": 1, "other_us": 5}],
        }
    )
    lines = _render_csv(rows).splitlines()
    assert lines[0] == "test,params,fork,block,total_us,other_us"
    assert lines[1] == "t,-,MONAD_NINE,1,10,"
    assert lines[2] == "t,-,MONAD_NINE,1,,5"


def test_sorted_rows_groups_forks_in_release_order() -> None:
    """Rows group by case, then fork release order, then block."""
    rows = [
        {
            "test": "t",
            "params": "p",
            "fork": "MONAD_TEN",
            "fork_rank": 1,
            "block": 1,
        },
        {
            "test": "t",
            "params": "p",
            "fork": "MONAD_NINE",
            "fork_rank": 0,
            "block": 2,
        },
        {
            "test": "t",
            "params": "p",
            "fork": "MONAD_NINE",
            "fork_rank": 0,
            "block": 1,
        },
    ]
    assert [(r["fork"], r["block"]) for r in _sorted_rows(rows)] == [
        ("MONAD_NINE", 1),
        ("MONAD_NINE", 2),
        ("MONAD_TEN", 1),
    ]
