"""
Emit per-block execution timing collected during `consume direct` as
Markdown and/or CSV artifacts alongside the HTML report.

The consumer (e.g. the monad runloop) returns per-block timing from
`consume_fixture`; `test_via_direct.test_fixture` stashes it on the test's
`user_properties` under `TIMING_PROPERTY`. This plugin writes one part file
per test *in the process that ran it* (so it never depends on xdist
forwarding `user_properties` to the controller), then the controller
aggregates every part into a single table at session end.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from itertools import groupby
from pathlib import Path
from typing import Any, Dict, Generator, List, Tuple

import pytest

# Key under which `test_fixture` records the timing payload
# ``{"id": <fixture id>, "blocks": [BlockExecutionTiming, ...]}``.
TIMING_PROPERTY = "consume_block_timing"

# Column key -> header. Metrics are the per-fixture minimum across blocks
# (repeat samples); the warmup block is naturally discarded by the min.
_COLUMNS: List[Tuple[str, str]] = [
    ("test", "test"),
    ("params", "params"),
    ("fork", "fork"),
    ("tx_count", "tx"),
    ("gas", "gas"),
    ("tx_exec_us", "tx_exec_us"),
    ("state_root_us", "state_root_us"),
    ("commit_us", "commit_us"),
    ("total_us", "total_us"),
]

# Numeric metric columns a Δ% row is computed for.
_METRIC_KEYS = ("tx_exec_us", "state_root_us", "commit_us", "total_us")

# Fork ordering (oldest first) so a Δ% row compares the newer fork against
# the older baseline; unknown forks sort after, alphabetically.
_FORK_ORDER = ["MONAD_EIGHT", "MONAD_NINE", "MONAD_NEXT", "MONAD_TEN"]

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


def _delta_row(base: Dict[str, Any], comp: Dict[str, Any]) -> Dict[str, Any]:
    """Build a percent-change row comparing `comp` against `base`."""

    def pct(b: Any, c: Any) -> str:
        if not isinstance(b, (int, float)) or b == 0:
            return "n/a"
        return f"{(c - b) / b * 100:+.1f}%"

    row: Dict[str, Any] = {
        "test": "",
        "params": "",
        "fork": f"Δ% {comp['fork']}/{base['fork']}",
        "tx_count": "",
        "gas": "",
    }
    for key in _METRIC_KEYS:
        row[key] = pct(base[key], comp[key])
    return row


def _aggregate(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """
    Reduce per-block rows to one row per (test, params, fork).

    Each metric becomes the minimum across the fixture's blocks — repeat
    samples on disjoint pages — which discards the warmup block. Returns
    the aggregated rows and the largest block count seen (repeat count).
    """
    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    order: List[Tuple[str, str, str]] = []
    for row in rows:
        key = (row["test"], row["params"], row["fork"])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    aggregated: List[Dict[str, Any]] = []
    max_blocks = 1
    for key in order:
        members = groups[key]
        max_blocks = max(max_blocks, len(members))
        row = dict(members[0])
        for metric in _METRIC_KEYS:
            row[metric] = min(m[metric] for m in members)
        aggregated.append(row)
    return aggregated, max_blocks


def _ordered_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Order rows by (test, params) then fork, and append a Δ% row after each
    pair of forks sharing a (test, params) group.
    """

    def group_key(row: Dict[str, Any]) -> Tuple[str, str]:
        return row["test"], row["params"]

    rows.sort(key=lambda r: (*group_key(r), _fork_rank(r["fork"])))
    ordered: List[Dict[str, Any]] = []
    for _, group in groupby(rows, key=group_key):
        members = list(group)
        ordered.extend(members)
        if len(members) == 2:
            ordered.append(_delta_row(members[0], members[1]))
    return ordered


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


def _render_markdown(rows: List[Dict[str, Any]]) -> str:
    """Render rows as a GitHub-flavored Markdown table."""
    headers = [header for _, header in _COLUMNS]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(str(row.get(key, "")) for key, _ in _COLUMNS)
            + " |"
        )
    return "\n".join(lines) + "\n"


def _render_csv(rows: List[Dict[str, Any]]) -> str:
    """Render rows as CSV."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([header for _, header in _COLUMNS])
    for row in rows:
        writer.writerow([row.get(key, "") for key, _ in _COLUMNS])
    return buffer.getvalue()


class TimingReportPlugin:
    """Collect per-test timing parts and write the aggregated report."""

    def __init__(self, config: pytest.Config, fmt: str):  # noqa: D107
        self.config = config
        self.fmt = fmt
        self.written: List[Path] = []
        self.repeats = 1

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
        """Aggregate all part files into the report (controller only)."""
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
        aggregated, self.repeats = _aggregate(rows)
        rows = _ordered_rows(aggregated)

        output_dir = self._output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        if self.fmt in ("both", "md"):
            path = output_dir / "timing_consume.md"
            path.write_text(_render_markdown(rows))
            self.written.append(path)
        if self.fmt in ("both", "csv"):
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
        if self.repeats > 1:
            terminalreporter.write_line(
                f"metrics are the min over {self.repeats} repeat block(s) "
                "per fixture"
            )
        for path in self.written:
            terminalreporter.write_line(f"timing report written to: {path}")
