"""
Emit per-block execution timing collected during `consume direct` as a
CSV artifact alongside the HTML report.

The consumer (e.g. the monad runloop) returns per-block timing from
`consume_fixture`; `test_via_direct.test_fixture` stashes it on the test's
`user_properties` under `TIMING_PROPERTY`. This plugin writes one part file
per test *in the process that ran it* (so it never depends on xdist
forwarding `user_properties` to the controller), then the controller
collects every part into a single raw per-block CSV at session end.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from pathlib import Path
from typing import Any, Dict, Generator, List, Tuple

import pytest

# Key under which `test_fixture` records the timing payload
# ``{"id": <fixture id>, "blocks": [BlockExecutionTiming, ...]}``.
TIMING_PROPERTY = "consume_block_timing"

# Column key -> header. One row per block carrying the raw per-block
# measurements.
_COLUMNS: List[Tuple[str, str]] = [
    ("test", "test"),
    ("params", "params"),
    ("fork", "fork"),
    ("block", "block"),
    ("tx_count", "tx"),
    ("gas", "gas"),
    ("tx_exec_us", "tx_exec_us"),
    ("state_root_us", "state_root_us"),
    ("commit_us", "commit_us"),
    ("total_us", "total_us"),
]

# Fork ordering (oldest first) so CSV rows group forks in release order;
# unknown forks sort after, alphabetically.
_FORK_ORDER = ["MONAD_EIGHT", "MONAD_NINE", "MONAD_TEN", "MONAD_NEXT"]

# Fixture-id suffixes identifying the fixture format, not a real parameter.
_FORMAT_TAGS = {
    "blockchain_test",
    "blockchain_test_engine",
    "blockchain_test_sync",
    "state_test",
}


def _fork_rank(fork: str) -> Tuple[int, str]:
    """Sort key placing known forks in release order, others after."""
    if fork in _FORK_ORDER:
        return _FORK_ORDER.index(fork), ""
    return len(_FORK_ORDER), fork


def _sorted_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Order rows by (test, params, fork) then block for readability."""
    rows.sort(
        key=lambda r: (
            r["test"],
            r["params"],
            _fork_rank(r["fork"]),
            r["block"],
        )
    )
    return rows


def _split_fixture_id(fixture_id: str) -> Tuple[str, str, str]:
    """
    Split a fixture id into (test, params, fork).

    ``tests/.../test_perf_regression.py::test_compute_loop[
    scheme_a-fork_MONAD_NINE-blockchain_test]`` becomes
    ``("test_perf_regression::test_compute_loop", "scheme_a", "MONAD_NINE")``.
    Parameters are kept as one ``-``-joined column since their arity varies
    per test; the fork token and the trailing format tag are pulled out.
    """
    module, _, rest = fixture_id.partition("::")
    func = rest.split("[", 1)[0]
    test = f"{Path(module).stem}::{func}" if module else func
    fork = ""
    params: List[str] = []
    if "[" in rest and rest.rstrip().endswith("]"):
        inner = rest[rest.index("[") + 1 : rest.rindex("]")]
        for token in inner.split("-"):
            if token.startswith("fork_"):
                fork = token[len("fork_") :]
            elif token in _FORMAT_TAGS:
                continue
            else:
                params.append(token)
    return test, "-".join(params), fork


def _rows_from_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten one recorded timing payload into per-block table rows."""
    test, params, fork = _split_fixture_id(payload["id"])
    rows = []
    for block in payload["blocks"]:
        rows.append(
            {"test": test, "params": params or "-", "fork": fork, **block}
        )
    return rows


def _render_csv(rows: List[Dict[str, Any]]) -> str:
    """Render rows as CSV."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([header for _, header in _COLUMNS])
    for row in rows:
        writer.writerow([row.get(key, "") for key, _ in _COLUMNS])
    return buffer.getvalue()


class TimingReportPlugin:
    """Collect per-test timing parts and write the raw per-block CSV."""

    def __init__(self, config: pytest.Config):  # noqa: D107
        self.config = config
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

    def _parts_dir(self) -> Path:
        return self._output_dir() / ".timing_parts"

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(
        self,
        item: pytest.Item,  # noqa: ARG002
        call: pytest.CallInfo[None],
    ) -> Generator[None, Any, None]:
        """
        Persist this test's timing to a part file, in the running process.

        Reading ``user_properties`` here (rather than on the controller)
        avoids relying on xdist to marshal them back; each test writes its
        own uniquely-named file, so parallel workers never race.
        """
        outcome = yield
        if call.when != "call":
            return
        report = outcome.get_result()
        if report.outcome != "passed":
            return
        payload = dict(report.user_properties).get(TIMING_PROPERTY)
        if not payload:
            return
        parts_dir = self._parts_dir()
        parts_dir.mkdir(parents=True, exist_ok=True)
        (parts_dir / f"{uuid.uuid4().hex}.json").write_text(
            json.dumps(payload)
        )

    def pytest_sessionfinish(
        self,
        session: pytest.Session,
        exitstatus: int,  # noqa: ARG002
    ) -> None:
        """Collect all part files into the CSV report (controller only)."""
        if hasattr(session.config, "workerinput"):
            return  # xdist worker: parts already written by makereport
        parts_dir = self._parts_dir()
        if not parts_dir.is_dir():
            return
        rows: List[Dict[str, Any]] = []
        for part in parts_dir.glob("*.json"):
            rows.extend(_rows_from_payload(json.loads(part.read_text())))
        if not rows:
            return
        rows = _sorted_rows(rows)

        output_dir = self._output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "timing_consume.csv"
        path.write_text(_render_csv(rows))
        self.written.append(path)

        for part in parts_dir.glob("*.json"):
            part.unlink()
        parts_dir.rmdir()

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
