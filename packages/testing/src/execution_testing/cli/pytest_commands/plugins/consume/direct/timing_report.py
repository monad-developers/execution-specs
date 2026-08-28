"""
Emit per-block execution timing collected during `consume direct` as a
CSV artifact alongside the HTML report.

A consumer that implements the `BlockTimingReporter` capability (e.g. the
monad runloop) exposes per-block timing for the fixture it just ran. This
plugin reads it off the finished test and writes every block it collected
as one raw per-block CSV at session end.

The metric columns are whatever keys the consumer reported, so the plugin
stays agnostic of which execution phases a given client can measure.

Everything the feature needs lives here — the capability protocol, the
command-line options and the reporting — so the generic `consume` modules
stay byte-identical to upstream. `pytest-consume.ini` registers it with a
single `-p` line.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import (
    Any,
    Dict,
    Generator,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    runtime_checkable,
)

import pytest

from execution_testing.fixtures.consume import (
    TestCaseIndexFile,
    TestCaseStream,
)
from execution_testing.forks import Fork, TransitionFork
from execution_testing.forks.helpers import get_forks

BlockTiming = Mapping[str, int]
"""
One block's measurements, keyed by metric name.

The metric names are the reporting consumer's own: it decides which
phases it can measure and how they are labelled. Consumers must use the
same keys, in the same order, for every block they report.
"""


@runtime_checkable
class BlockTimingReporter(Protocol):
    """
    Optional consumer capability: per-block timing of the last fixture.

    A consumer that can measure block processing implements this so
    `consume direct --timing-report` can tabulate it.
    """

    def last_block_timings(self) -> Sequence[BlockTiming]:
        """
        Return per-block measurements from the most recent
        `consume_fixture` call, empty if it measured none.
        """
        ...


# Fixtures the report hook reads off the finished test. Both are
# parametrized by the consume plugins, so they are present in
# `item.funcargs` for the call phase.
_CONSUMER_FIXTURE = "fixture_consumer"
_TEST_CASE_FIXTURE = "test_case"

# Columns identifying a row; the reported metric keys follow them.
_ID_COLUMNS = ["test", "params", "fork"]


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add the timing report options to the consume command group."""
    group = parser.getgroup(
        "consume_direct",
        "Arguments related to consuming fixtures via a client",
    )
    group.addoption(
        "--timing-report",
        action="store_true",
        dest="timing_report",
        default=False,
        help=(
            "Emit per-block execution timing (from consumers that report "
            "it) as a raw `timing_consume.csv`."
        ),
    )
    group.addoption(
        "--timing-report-dir",
        action="store",
        dest="timing_report_dir",
        type=Path,
        default=None,
        help=(
            "Directory for the timing report. Defaults to the HTML report's "
            "directory (the fixtures `.meta` directory)."
        ),
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register the report plugin, or refuse a run that cannot time."""
    if not config.getoption("timing_report", False):
        return
    # `--bin` is registered by the `consume direct` conftest only, so its
    # absence means a hive simulator, where no consumer reports timing.
    # A hive command usually fails its own setup check before reaching
    # here; this keeps the flag from being silently accepted if it does.
    if config.getoption("fixture_consumer_bin", None) is None:
        raise pytest.UsageError(
            "--timing-report is only available for `consume direct`."
        )
    if (config.getoption("numprocesses", None) or 0) != 0:
        raise pytest.UsageError(
            "--timing-report cannot be combined with xdist."
        )
    config.pluginmanager.register(
        TimingReportPlugin(config), "consume-timing-report"
    )


def _fork_rank(fork: Fork | TransitionFork | None) -> int:
    """
    Position of `fork` in the framework's chronological fork list.

    Used to group CSV rows in release order without hardcoding a fork
    list. Transition and unknown forks sort after all plain forks.
    """
    forks = get_forks()
    try:
        return forks.index(fork)  # type: ignore[arg-type]
    except ValueError:
        return len(forks)


def _sorted_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Order rows by (test, params, fork) then block for readability."""
    rows.sort(
        key=lambda r: (
            r["test"],
            r["params"],
            r["fork_rank"],
            r["fork"],
            r.get("block", 0),
        )
    )
    return rows


def _split_fixture_id(
    fixture_id: str, fork: str, fixture_format: str
) -> Tuple[str, str]:
    """
    Split a fixture id into (test, params).

    ``tests/.../test_perf_regression.py::test_page_ops[
    sstore_fresh-k0-fork_MONAD_NINE-blockchain_test]`` becomes
    ``("test_perf_regression::test_page_ops", "sstore_fresh-k0")``. The
    fork and fixture-format tokens are dropped by value (both are known
    from the test case), and the remaining parameters are kept as one
    ``-``-joined column since their arity varies per test.
    """
    module, _, rest = fixture_id.partition("::")
    func = rest.split("[", 1)[0]
    test = f"{Path(module).stem}::{func}" if module else func
    params: List[str] = []
    if "[" in rest and rest.rstrip().endswith("]"):
        inner = rest[rest.index("[") + 1 : rest.rindex("]")]
        params = [token for token in inner.split("-") if token]
        for known in (f"fork_{fork}", fixture_format):
            if known in params:
                params.remove(known)
    return test, "-".join(params)


def timing_payload(
    consumer: object,
    test_case: TestCaseIndexFile | TestCaseStream,
) -> Optional[Dict[str, Any]]:
    """
    Build one test's timing payload from the consumer that ran it.

    Returns None for a consumer without the `BlockTimingReporter`
    capability and for a fixture the consumer measured nothing for.
    """
    if not isinstance(consumer, BlockTimingReporter):
        return None
    timings = consumer.last_block_timings()
    if not timings:
        return None
    fork = test_case.fork
    fork_name = fork.name() if fork is not None else ""
    test, params = _split_fixture_id(
        test_case.id, fork_name, test_case.format.format_name
    )
    return {
        "test": test,
        "params": params or "-",
        "fork": fork_name,
        "fork_rank": _fork_rank(fork),
        "blocks": [dict(timing) for timing in timings],
    }


def _rows_from_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten one recorded timing payload into per-block table rows."""
    identity = {key: payload[key] for key in (*_ID_COLUMNS, "fork_rank")}
    return [{**identity, **block} for block in payload["blocks"]]


def _metric_columns(rows: List[Dict[str, Any]]) -> List[str]:
    """
    Metric columns, in the order the consumer first reported them.

    Consumers reporting different metrics in one session contribute their
    own columns; a row missing a column is written blank.
    """
    columns: List[str] = []
    for row in rows:
        for key in row:
            if key not in columns and key not in (*_ID_COLUMNS, "fork_rank"):
                columns.append(key)
    return columns


def _render_csv(rows: List[Dict[str, Any]]) -> str:
    """Render rows as CSV."""
    columns = [*_ID_COLUMNS, *_metric_columns(rows)]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(column, "") for column in columns])
    return buffer.getvalue()


class TimingReportPlugin:
    """Collect each test's timing and write the raw per-block CSV."""

    def __init__(self, config: pytest.Config):  # noqa: D107
        self.config = config
        self.rows: List[Dict[str, Any]] = []
        self.written: List[Path] = []

    def _output_dir(self) -> Path:
        """
        Directory the timing artifacts are written to.

        Prefers an explicit ``--timing-report-dir``; otherwise follows the
        HTML report's directory (so it lands next to ``report_consume.html``,
        which defaults to the fixtures `.meta`).
        """
        explicit = self.config.getoption("timing_report_dir", None)
        if explicit is not None:
            return Path(explicit)
        htmlpath = getattr(self.config.option, "htmlpath", None)
        if htmlpath:
            return Path(htmlpath).parent
        source = self.config.fixtures_source  # type: ignore[attr-defined]
        return Path(source.path) / ".meta"

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(
        self,
        item: pytest.Item,
        call: pytest.CallInfo[None],
    ) -> Generator[None, Any, None]:
        """
        Keep this test's timing for the report.

        The consumer and test case are read straight off the finished
        item, so the generic test function stays untouched. `--timing-report`
        rejects xdist, so one process sees every test and the rows can be
        held in memory until session end.
        """
        outcome = yield
        if call.when != "call":
            return
        report = outcome.get_result()
        if report.outcome != "passed":
            return
        funcargs = getattr(item, "funcargs", None) or {}
        consumer = funcargs.get(_CONSUMER_FIXTURE)
        test_case = funcargs.get(_TEST_CASE_FIXTURE)
        if consumer is None or test_case is None:
            return
        payload = timing_payload(consumer, test_case)
        if payload:
            self.rows.extend(_rows_from_payload(payload))

    def pytest_sessionfinish(
        self,
        session: pytest.Session,  # noqa: ARG002
        exitstatus: int,  # noqa: ARG002
    ) -> None:
        """Write the collected rows as the CSV report."""
        if not self.rows:
            return
        output_dir = self._output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "timing_consume.csv"
        path.write_text(_render_csv(_sorted_rows(self.rows)))
        self.written.append(path)

    def pytest_terminal_summary(
        self,
        terminalreporter: Any,
        exitstatus: int,  # noqa: ARG002
        config: pytest.Config,  # noqa: ARG002
    ) -> None:
        """Point the user at the generated timing artifacts."""
        if not self.written:
            return
        terminalreporter.write_sep("=", "block execution timing report")
        terminalreporter.write_line(
            "raw per-block timings; aggregate across blocks downstream"
        )
        for path in self.written:
            terminalreporter.write_line(f"timing report written to: {path}")
