"""Tests for the `perf_regression` NINE-vs-TEN significance table."""

from pathlib import Path
from typing import Dict, Sequence

import pytest
from click import ClickException
from click.testing import CliRunner

from execution_testing.cli.perf_regression import (
    ALPHA,
    DOWN,
    MIXED,
    UP,
    _avg_ranks,
    _mwu_p,
    main,
    parse,
    report,
)

HEADER = (
    "test,params,fork,block,tx_count,gas,"
    "tx_exec_us,state_root_us,commit_us,total_us"
)


def _csv(rows: Sequence[Dict[str, object]]) -> str:
    """Render timing rows the way the consume timing report does."""
    lines = [HEADER]
    for row in rows:
        lines.append(
            ",".join(str(row.get(column, "")) for column in HEADER.split(","))
        )
    return "\n".join(lines) + "\n"


def _row(fork: str, total: int, block: int = 1) -> Dict[str, object]:
    """One per-block row for the `page_ops` case at `fork`."""
    return {
        "test": "test_perf_regression::test_page_ops",
        "params": "sstore_fresh-k0",
        "fork": fork,
        "block": block,
        "tx_count": 7,
        "gas": 200_000_000,
        "tx_exec_us": total // 2,
        "state_root_us": total // 4,
        "commit_us": total // 4,
        "total_us": total,
    }


def _run_dir(tmp_path: Path, name: str, nine: int, ten: int) -> Path:
    """Write a one-case run directory with the given per-fork totals."""
    path = tmp_path / name
    path.mkdir()
    (path / "timing_consume.csv").write_text(
        _csv([_row("MONAD_NINE", nine), _row("MONAD_TEN", ten)])
    )
    return path


def test_parse_reduces_blocks_to_minimum(tmp_path: Path) -> None:
    """Each metric is reduced to its minimum across a case's blocks."""
    path = tmp_path / "timing_consume.csv"
    path.write_text(
        _csv(
            [
                _row("MONAD_NINE", 300, block=1),
                _row("MONAD_NINE", 100, block=2),
                _row("MONAD_NINE", 200, block=3),
            ]
        )
    )
    parsed = parse(path)
    assert (
        parsed[("page_ops", "sstore_fresh-k0", "MONAD_NINE")]["total_us"]
        == 100
    )


def test_parse_ignores_other_forks(tmp_path: Path) -> None:
    """Rows for forks outside the comparison are dropped."""
    path = tmp_path / "timing_consume.csv"
    path.write_text(_csv([_row("Prague", 100), _row("MONAD_TEN", 200)]))
    assert list(parse(path)) == [("page_ops", "sstore_fresh-k0", "MONAD_TEN")]


def test_parse_skips_unparsable_metrics(tmp_path: Path) -> None:
    """A row with a non-numeric metric is skipped, not fatal."""
    bad = _row("MONAD_NINE", 100)
    bad["total_us"] = "oops"
    path = tmp_path / "timing_consume.csv"
    path.write_text(_csv([bad, _row("MONAD_TEN", 200)]))
    assert list(parse(path)) == [("page_ops", "sstore_fresh-k0", "MONAD_TEN")]


def test_avg_ranks_averages_ties() -> None:
    """Tied values share the average of their ranks."""
    assert _avg_ranks([10.0, 20.0, 20.0, 30.0]) == [1.0, 2.5, 2.5, 4.0]


def test_mwu_p_identical_samples_is_one() -> None:
    """Identical samples are not distinguishable."""
    assert _mwu_p([1, 1, 1], [1, 1, 1]) == 1.0


def test_mwu_p_empty_sample_is_one() -> None:
    """A missing sample yields no evidence."""
    assert _mwu_p([], [1, 2]) == 1.0


def test_mwu_p_fully_separated_samples() -> None:
    """Fully separated samples give the smallest attainable p-value."""
    nine = [10, 11, 12, 13, 14, 15, 16]
    ten = [20, 21, 22, 23, 24, 25, 26]
    # Exact two-sided permutation p: 2 of comb(14, 7) rank splits are
    # at least as extreme.
    assert _mwu_p(nine, ten) == pytest.approx(2 / 3432)


def test_mwu_p_normal_approximation_for_large_samples() -> None:
    """Past the exact-enumeration cap the normal fallback still ranks."""
    nine = list(range(30))
    ten = list(range(100, 130))
    assert _mwu_p(nine, ten) < ALPHA


def _case_row(table: str) -> str:
    """The table's data row for the single `page_ops` case."""
    rows = [
        line for line in table.splitlines() if line.startswith("| page_ops")
    ]
    assert len(rows) == 1, table
    return rows[0]


def test_build_flags_significant_slowdown(tmp_path: Path) -> None:
    """A clean slowdown is flagged, bolded and quantified."""
    dirs = [
        _run_dir(tmp_path, f"r{i}", nine=100 + i, ten=200 + i)
        for i in range(7)
    ]
    row = _case_row(report(tuple(dirs)))
    assert UP in row
    assert "**" in row
    assert "total_us +97%" in row


def test_build_flags_significant_speedup(tmp_path: Path) -> None:
    """A clean speedup is flagged in the other direction."""
    dirs = [
        _run_dir(tmp_path, f"r{i}", nine=200 + i, ten=100 + i)
        for i in range(7)
    ]
    row = _case_row(report(tuple(dirs)))
    assert DOWN in row
    assert "total_us -49%" in row


def test_build_marks_noise_insignificant(tmp_path: Path) -> None:
    """Interleaved samples are reported without a significance flag."""
    totals = [
        (100, 101),
        (102, 99),
        (98, 103),
        (101, 100),
        (99, 102),
        (103, 98),
        (97, 104),
    ]
    dirs = [
        _run_dir(tmp_path, f"r{i}", nine=n, ten=t)
        for i, (n, t) in enumerate(totals)
    ]
    row = _case_row(report(tuple(dirs)))
    assert UP not in row
    assert DOWN not in row
    assert MIXED not in row
    assert row.endswith("| - | - |")
    assert "**" not in row


def test_report_needs_two_runs(tmp_path: Path) -> None:
    """A single run cannot support a comparison."""
    with pytest.raises(ClickException, match="need >=2 runs"):
        report((_run_dir(tmp_path, "only", nine=100, ten=200),))


def test_report_warns_about_dirs_without_csv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A run dir without a CSV is named rather than silently dropped."""
    dirs = [_run_dir(tmp_path, f"r{i}", nine=100, ten=200) for i in range(2)]
    empty = tmp_path / "empty"
    empty.mkdir()

    report((*dirs, empty))

    assert "skipping 1 run dir(s)" in capsys.readouterr().err


def test_cli_writes_markdown_and_html(tmp_path: Path) -> None:
    """The CLI writes both renderings and the provenance header."""
    dirs = [
        _run_dir(tmp_path, f"r{i}", nine=100 + i, ten=200 + i)
        for i in range(7)
    ]
    md_path = tmp_path / "table.md"
    html_path = tmp_path / "table.html"

    result = CliRunner().invoke(
        main,
        [
            "--md",
            str(md_path),
            "--html",
            str(html_path),
            "--now",
            "2026-08-18T00:00:00Z",
            "--repo",
            "deadbeef",
            *[str(d) for d in dirs],
        ],
    )

    assert result.exit_code == 0, result.output
    md = md_path.read_text()
    assert "Cycle 2026-08-18T00:00:00Z" in md
    assert "| execution-specs | deadbeef |" in md
    assert "blob/deadbeef/MIP8_PERF_TESTS_DIAGRAMS.md" in md
    html = html_path.read_text()
    assert "<table>" in html
    assert "<strong>" in html


def test_cli_requires_run_dirs() -> None:
    """Invoking without run directories is a usage error."""
    result = CliRunner().invoke(main, [])
    assert result.exit_code != 0
