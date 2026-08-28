"""Tests for the `perf_regression` NINE-vs-TEN significance table."""

from pathlib import Path
from typing import Dict, List, Sequence

import pytest
from click import ClickException
from click.testing import CliRunner

from execution_testing.cli.perf_regression import (
    ALPHA,
    DOWN,
    MIXED,
    UP,
    _avg_ranks,
    _bh_adjust,
    _wilcoxon_p,
    main,
    parse,
    report,
)

HEADER = (
    "test,params,fork,block,tx_count,gas,retries,"
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
        "retries": 0,
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


def test_wilcoxon_p_no_differences_is_one() -> None:
    """Identical paired samples carry no sign information."""
    assert _wilcoxon_p([0, 0, 0]) == 1.0
    assert _wilcoxon_p([]) == 1.0


def test_wilcoxon_p_drops_zero_differences() -> None:
    """Zero differences are excluded from the rank count."""
    assert _wilcoxon_p([0, 0, 1, 2, 3]) == _wilcoxon_p([1, 2, 3])


def test_wilcoxon_p_consistent_sign_is_the_floor() -> None:
    """All-positive differences hit the smallest attainable p, 2/2**n."""
    assert _wilcoxon_p([1, 2, 3, 4, 5, 6, 7]) == pytest.approx(2 / 2**7)
    assert _wilcoxon_p([5] * 7) == pytest.approx(2 / 2**7)


def test_wilcoxon_p_mixed_signs_is_not_significant() -> None:
    """Differences that change sign give no evidence of a shift."""
    assert _wilcoxon_p([1, -2, 3, -4, 2, -1, 1]) > ALPHA


def test_wilcoxon_p_is_paired_not_pooled() -> None:
    """A consistent shift is detected even when the levels overlap."""
    # Both forks drift upward across runs, but TEN is always the slower
    # of the pair; an unpaired test would drown this in the drift.
    nine = [100, 200, 300, 400, 500, 600, 700, 800]
    ten = [110, 210, 310, 410, 510, 610, 710, 810]
    diffs = [t - n for n, t in zip(nine, ten, strict=True)]
    assert _wilcoxon_p(diffs) == pytest.approx(2 / 2**8)


# Two-sided critical values of the signed-rank statistic at alpha = 0.05,
# from the published table: reject when W+ <= W_crit. Reproducing the whole
# table pins the exact branch against a source outside this repo.
WILCOXON_CRITICAL_05 = {6: 0, 7: 2, 8: 3, 9: 5, 10: 8, 11: 10, 12: 13, 13: 17}


def _diffs_with_positive_rank_sum(n: int, target: int) -> List[int]:
    """Signed differences over ranks 1..n whose positive ranks sum to it."""
    positive = set()
    remaining = target
    for rank in range(n, 0, -1):
        if rank <= remaining:
            positive.add(rank)
            remaining -= rank
    assert remaining == 0, f"cannot hit W+={target} with ranks 1..{n}"
    return [r if r in positive else -r for r in range(1, n + 1)]


@pytest.mark.parametrize("n, w_crit", sorted(WILCOXON_CRITICAL_05.items()))
def test_wilcoxon_p_matches_published_critical_values(
    n: int, w_crit: int
) -> None:
    """The published rejection boundary falls where the table says."""
    at = _wilcoxon_p(_diffs_with_positive_rank_sum(n, w_crit))
    above = _wilcoxon_p(_diffs_with_positive_rank_sum(n, w_crit + 1))

    assert at <= 0.05 < above


@pytest.mark.parametrize(
    "diffs, expected",
    [
        ([1, 2, 3, 4, 5, 6, 7], 2 / 2**7),
        ([-3, 8, -1, 12, 5, -20, 2, 30], 0.3828125),
        ([10, -20, 30, -40, 50, -60, 70, -80, 90, -100], 0.845703125),
    ],
    ids=["all_positive_n7", "mixed_n8", "mixed_n10"],
)
def test_wilcoxon_p_tie_free_reference_values(
    diffs: List[int], expected: float
) -> None:
    """Tie-free samples agree with `scipy.stats.wilcoxon` (1.18.1)."""
    assert _wilcoxon_p(diffs) == pytest.approx(expected)


def test_wilcoxon_p_averages_tied_ranks() -> None:
    """
    Pin the tie convention on a published worked example.

    The differences are the Wikipedia signed-rank example: one zero, which
    drops to n=9, and |5| twice. With ties the exact null distribution is
    not defined, so implementations disagree — this enumerates signs over
    the averaged ranks, giving 324 of 2**9 sign assignments at least as
    extreme. scipy's exact branch ranks 1..n instead and reports 0.6523;
    the article reports 0.6113.
    """
    diffs = [15, -7, 5, 20, 0, -9, 17, -12, 5, -10]

    assert _wilcoxon_p(diffs) == pytest.approx(324 / 2**9)


def test_wilcoxon_p_normal_approximation_for_large_samples() -> None:
    """Past the exact-enumeration cap the normal fallback still ranks."""
    assert _wilcoxon_p(list(range(1, 25))) < ALPHA


def test_bh_adjust_matches_step_up_procedure() -> None:
    """Adjusted values match the Benjamini-Hochberg step-up example."""
    ps = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205]
    adjusted = _bh_adjust(ps)
    assert adjusted[0] == pytest.approx(0.008)
    assert adjusted[1] == pytest.approx(0.032)
    assert adjusted[-1] == pytest.approx(0.205)


def test_bh_adjust_is_monotone_and_bounded() -> None:
    """Adjusted values never decrease with p, and never exceed 1."""
    ps = [0.2, 0.9, 0.01, 0.5, 0.99]
    adjusted = _bh_adjust(ps)
    assert all(q <= 1.0 for q in adjusted)
    by_p = [q for _, q in sorted(zip(ps, adjusted, strict=True))]
    assert by_p == sorted(by_p)


def test_bh_adjust_empty() -> None:
    """An empty table adjusts to nothing."""
    assert _bh_adjust([]) == []


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
        for i in range(9)
    ]
    row = _case_row(report(tuple(dirs)))
    assert UP in row
    assert "**" in row
    assert "total_us +96%" in row


def test_build_flags_significant_speedup(tmp_path: Path) -> None:
    """A clean speedup is flagged in the other direction."""
    dirs = [
        _run_dir(tmp_path, f"r{i}", nine=200 + i, ten=100 + i)
        for i in range(9)
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
        (104, 97),
        (96, 105),
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


def _one_fork_dir(tmp_path: Path, name: str, fork: str, total: int) -> Path:
    """Write a run directory that measured only one of the two forks."""
    path = tmp_path / name
    path.mkdir()
    (path / "timing_consume.csv").write_text(_csv([_row(fork, total)]))
    return path


def test_report_names_cases_never_compared(tmp_path: Path) -> None:
    """A case no run measured at both forks is named, not dropped."""
    dirs = [
        _one_fork_dir(tmp_path, f"r{i}", "MONAD_NINE", 100 + i)
        for i in range(3)
    ]
    table = report(tuple(dirs))
    assert "1 case(s) not compared" in table
    assert "- page_ops sstore_fresh-k0" in table


def test_report_names_partially_compared_cases(tmp_path: Path) -> None:
    """A case missing from some runs reports its reduced run count."""
    dirs = [
        _run_dir(tmp_path, f"r{i}", nine=100 + i, ten=200 + i)
        for i in range(3)
    ]
    dirs.append(_one_fork_dir(tmp_path, "partial", "MONAD_NINE", 100))
    table = report(tuple(dirs))
    assert "compared on fewer than 4 runs" in table
    assert "3 of 4 runs" in table


def test_report_counts_hypothesis_tests(tmp_path: Path) -> None:
    """The report states how many tests the adjustment covers."""
    dirs = [_run_dir(tmp_path, f"r{i}", nine=100, ten=200) for i in range(3)]
    assert "3 hypothesis tests in this table" in report(tuple(dirs))


def test_report_warns_when_underpowered(tmp_path: Path) -> None:
    """Too few pairs to clear the floor is called out, not hidden."""
    dirs = [
        _run_dir(tmp_path, f"r{i}", nine=100 + i, ten=200 + i)
        for i in range(4)
    ]
    table = report(tuple(dirs))
    assert "Underpowered for isolated effects" in table
    assert _case_row(table).endswith("| - | - |")


def test_report_no_power_warning_when_resolvable(tmp_path: Path) -> None:
    """Enough pairs to clear the floor leaves the warning out."""
    # 3 measures at 2/2**10 clears q <= 0.05; 6 runs would not.
    dirs = [
        _run_dir(tmp_path, f"r{i}", nine=100 + i, ten=200 + i)
        for i in range(10)
    ]
    assert "Underpowered" not in report(tuple(dirs))


def _retry_dir(tmp_path: Path, name: str, nine: int, ten: int) -> Path:
    """Write a run directory whose blocks report differing retry counts."""
    path = tmp_path / name
    path.mkdir()
    rows = []
    for fork, retries in (("MONAD_NINE", nine), ("MONAD_TEN", ten)):
        for block, r in enumerate((0, retries), start=1):
            row = _row(fork, 100, block=block)
            row["retries"] = r
            rows.append(row)
    (path / "timing_consume.csv").write_text(_csv(rows))
    return path


def test_report_shows_retries_of_the_reported_block(tmp_path: Path) -> None:
    """Retries come from the block whose timing the row reports."""
    dirs = [_retry_dir(tmp_path, f"r{i}", nine=0, ten=4) for i in range(3)]
    table = report(tuple(dirs))
    assert "retries NINE" in table
    assert "retries TEN" in table
    cells = [c.strip() for c in _case_row(table).split("|")]
    # Both blocks tie on total_us, so the earlier one (retries 0) stands.
    assert cells.count("0 ± 0") >= 2


def test_parse_takes_the_counter_from_the_fastest_block(
    tmp_path: Path,
) -> None:
    """The counter follows argmin(REFERENCE_METRIC), not its own extremum."""
    rows = []
    for block, (total, retries) in enumerate(
        [(300, 9), (100, 2), (200, 5)], start=1
    ):
        row = _row("MONAD_NINE", total, block=block)
        row["retries"] = retries
        rows.append(row)
    path = tmp_path / "timing_consume.csv"
    path.write_text(_csv(rows))

    parsed = parse(path)[("page_ops", "sstore_fresh-k0", "MONAD_NINE")]

    assert parsed["total_us"] == 100
    assert parsed["retries"] == 2  # not 9 (max) and not 5


def test_parse_counter_ignores_a_slower_block_with_more_retries(
    tmp_path: Path,
) -> None:
    """A later, slower block does not drag its counter into the row."""
    rows = []
    for block, (total, retries) in enumerate([(100, 1), (500, 99)], start=1):
        row = _row("MONAD_NINE", total, block=block)
        row["retries"] = retries
        rows.append(row)
    path = tmp_path / "timing_consume.csv"
    path.write_text(_csv(rows))

    parsed = parse(path)[("page_ops", "sstore_fresh-k0", "MONAD_NINE")]

    assert parsed["total_us"] == 100
    assert parsed["retries"] == 1


def test_parse_tolerates_csv_without_retries(tmp_path: Path) -> None:
    """A csv written before the counter existed still parses."""
    legacy = HEADER.replace(",retries", "")
    row = _row("MONAD_NINE", 100)
    path = tmp_path / "timing_consume.csv"
    path.write_text(
        legacy
        + "\n"
        + ",".join(str(row.get(c, "")) for c in legacy.split(","))
        + "\n"
    )
    parsed = parse(path)
    assert (
        parsed[("page_ops", "sstore_fresh-k0", "MONAD_NINE")]["retries"] == 0
    )


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
        for i in range(9)
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
