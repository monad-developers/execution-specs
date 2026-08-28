"""
Build a NINE-vs-TEN significance table from perf timing runs.

Reads the `timing_consume.csv` produced by `consume direct
--timing-report` for several identical runs (one dir each) and, per
(test, params) case, reports each measure's mean +/- sd over the runs for
both forks, the two-sided Mann-Whitney U p-value comparing the forks,
and (for significant measures) the NINE->TEN average change.

Writes GitHub-flavored Markdown to `--md`, or stdout if omitted; when
`--now` is given, prefixes a provenance header.
"""

from __future__ import annotations

import csv
from itertools import product
from math import erfc, sqrt
from pathlib import Path
from statistics import mean, stdev
from typing import Dict, List, Optional, Tuple

import click

# One run's measurements: (test, params, fork) -> {metric: value}.
CaseMetrics = Dict[str, int]
Run = Dict[Tuple[str, str, str], CaseMetrics]

# A case, identified by (test, params).
CaseKey = Tuple[str, str]

# One metric's paired samples: [(NINE, TEN) per run measuring both forks].
PairedSamples = List[Tuple[int, int]]

METRICS = ["tx_exec_us", "commit_us", "total_us"]
# Work counters, reported alongside the durations but not tested: they say
# whether a slower block did the same work or redid some of it. Taken from
# the block the reduction picked, not reduced themselves — a retry count
# is only meaningful next to the timing of the same block.
COUNTS = ["retries"]
# The measure whose minimum picks a case's representative block. Block
# wall time is what a retry count belongs to: retries are a property of
# executing the whole block, not of one of its phases.
REFERENCE_METRIC = "total_us"
FORKS = ["MONAD_NINE", "MONAD_TEN"]
# A measure is significant at this Benjamini-Hochberg adjusted p-value.
# The table runs one test per (case, measure), so the unadjusted rate
# would produce a steady trickle of false positives across a cycle. The
# adjustment bounds the false discovery rate across the whole table, so
# 5% here caps the expected share of false flags among the measures the
# table does flag. Tightening it costs paired runs, since the floor a
# signed-rank test can reach scales with 2^RUNS.
ALPHA = 0.05

UP = "🔴⬆️"  # significant measures all rise NINE->TEN (TEN slower)
DOWN = "🟢⬇️"  # significant measures all fall NINE->TEN (TEN faster)
MIXED = "⚠️"  # significant measures move both up and down

# Per-case workload descriptions live here; pinned to the execution-specs
# sha of the run when known, else the repo's default branch (HEAD).
DIAGRAMS_URL = (
    "https://github.com/monad-developers/execution-specs/blob/"
    "{ref}/MIP8_PERF_TESTS_DIAGRAMS.md"
)


def _direction(chgs: List[int]) -> str:
    """Pick the direction emoji from the significant measures' changes."""
    ups = any(c > 0 for c in chgs)
    downs = any(c < 0 for c in chgs)
    if ups and downs:
        return MIXED
    if ups:
        return UP
    if downs:
        return DOWN
    return MIXED


def parse(report: Path) -> Run:
    """
    Map (test, params, fork) -> {metric: value} from one csv report.

    The csv holds one row per block (raw, unaggregated); each duration is
    reduced to the minimum across a case's blocks. Each counter in
    `COUNTS` is carried over from the block with the lowest
    `REFERENCE_METRIC`, so it describes the block whose timing is
    reported rather than a reduction of its own; ties keep the earlier
    block. A counter the report predates is treated as zero so older csv
    files still parse.
    """
    rows: Run = {}
    with report.open(newline="") as f:
        for row in csv.DictReader(f):
            if row["fork"] not in FORKS:
                continue
            try:
                mv = {m: int(row[m]) for m in METRICS}
                mv.update({c: int(row.get(c) or 0) for c in COUNTS})
            except (ValueError, TypeError):
                continue
            test = row["test"].split("::")[-1].removeprefix("test_")
            key = (test, row["params"], row["fork"])
            prev = rows.get(key)
            if prev is None:
                rows[key] = mv
            else:
                # `prev` already holds the running minimum, so its
                # counters belong to the block that set it.
                fastest = (
                    mv
                    if mv[REFERENCE_METRIC] < prev[REFERENCE_METRIC]
                    else prev
                )
                rows[key] = {
                    **{m: min(prev[m], mv[m]) for m in METRICS},
                    **{c: fastest[c] for c in COUNTS},
                }
    return rows


def _stat(values: List[int]) -> str:
    """Format a run sample as 'mean ± sd' (µs, rounded)."""
    sd = stdev(values) if len(values) > 1 else 0.0
    return f"{round(mean(values))} ± {round(sd)}"


def _pfmt(p: float) -> str:
    """Format a p-value compactly."""
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def _avg_ranks(vals: List[float]) -> List[float]:
    """Return 1-based ranks (ties averaged), aligned to `vals`."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _wilcoxon_p(diffs: List[int]) -> float:
    """
    Two-sided Wilcoxon signed-rank p-value for paired differences.

    Both forks are measured inside the same consume pass, so run `i`
    yields a matched (NINE, TEN) pair. Testing the differences cancels
    whatever drifted between runs — host thermals, page cache, other load
    — which an unpaired test would instead charge to both samples'
    variance.

    Zero differences carry no sign information and are dropped, as in the
    standard procedure. Exact (enumerating the sign assignments,
    conditional on the observed ranks, so averaged ties are handled) up to
    200k of them; past that a normal approximation with a continuity and
    tie correction. Distribution-free — no normality assumption.

    Follows Wilcoxon, F. (1945), "Individual Comparisons by Ranking
    Methods", Biometrics Bulletin 1(6), 80-83. On tie-free samples both
    branches reproduce `scipy.stats.wilcoxon` exactly, and the exact
    branch reproduces the published two-sided critical values; see
    `test_perf_regression.py`.

    With ties in |difference| there is no single standard answer, because
    the exact null distribution assumes distinct ranks. This enumerates
    signs over the observed averaged ranks (the conditional exact test);
    scipy's exact branch instead ranks 1..n and ignores ties. The two
    differ by a few percent on tied samples.
    """
    nonzero = [d for d in diffs if d != 0]
    n = len(nonzero)
    if n == 0:
        return 1.0
    ranks = _avg_ranks([abs(d) for d in nonzero])
    positive = sum(r for r, d in zip(ranks, nonzero, strict=True) if d > 0)
    mu = sum(ranks) / 2  # null mean of the positive-rank sum
    d = abs(positive - mu)
    if 2**n <= 200_000:
        extreme = 0
        for signs in product((0, 1), repeat=n):
            total = sum(r for r, s in zip(ranks, signs, strict=True) if s)
            if abs(total - mu) >= d - 1e-9:
                extreme += 1
        return extreme / 2**n
    tie_correction = sum(t**3 - t for t in _tie_sizes(ranks)) / 48
    variance = n * (n + 1) * (2 * n + 1) / 24 - tie_correction
    if variance <= 0:
        return 1.0
    return erfc(max(0.0, (d - 0.5) / sqrt(variance)) / sqrt(2))


def _tie_sizes(ranks: List[float]) -> List[int]:
    """Group sizes of tied ranks, for the signed-rank tie correction."""
    counts: Dict[float, int] = {}
    for rank in ranks:
        counts[rank] = counts.get(rank, 0) + 1
    return [count for count in counts.values() if count > 1]


def _bh_adjust(ps: List[float]) -> List[float]:
    """
    Benjamini-Hochberg adjusted p-values, aligned to `ps`.

    The table tests every (case, measure) pair, so raw p-values would let
    a handful of false positives through every cycle. Comparing the
    adjusted value against ALPHA controls the false discovery rate across
    the whole table instead.
    """
    m = len(ps)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: ps[i])
    adjusted = [1.0] * m
    running = 1.0
    for rank, i in reversed(list(enumerate(order, start=1))):
        running = min(running, ps[i] * m / rank)
        adjusted[i] = min(1.0, running)
    return adjusted


def _paired_samples(
    runs: List[Run],
) -> Tuple[Dict[CaseKey, Dict[str, PairedSamples]], Dict[CaseKey, int]]:
    """
    Pair each case's per-run measurements across the two forks.

    Returns the per-case, per-metric pairs and, per case, how many runs
    were dropped because they measured only one of the two forks.
    """
    cases = sorted({(test, params) for run in runs for test, params, _ in run})
    samples: Dict[CaseKey, Dict[str, PairedSamples]] = {}
    dropped: Dict[CaseKey, int] = {}
    for case in cases:
        per_metric: Dict[str, PairedSamples] = {
            m: [] for m in (*METRICS, *COUNTS)
        }
        missing = 0
        for run in runs:
            nine = run.get((*case, FORKS[0]))
            ten = run.get((*case, FORKS[1]))
            if nine is None or ten is None:
                missing += 1
                continue
            for m in (*METRICS, *COUNTS):
                per_metric[m].append((nine[m], ten[m]))
        samples[case] = per_metric
        if missing:
            dropped[case] = missing
    return samples, dropped


def build(runs: List[Run]) -> List[str]:
    """Return the markdown lines for the significance table."""
    samples, dropped = _paired_samples(runs)

    # Test every (case, measure) first, so the significance threshold can
    # be adjusted across the whole table before anything is rendered.
    tested = [
        (case, metric)
        for case in sorted(samples)
        for metric in METRICS
        if samples[case][metric]
    ]
    ps = [_wilcoxon_p([t - n for n, t in samples[c][m]]) for c, m in tested]
    adjusted = dict(zip(tested, _bh_adjust(ps), strict=True))
    raw = dict(zip(tested, ps, strict=True))

    header = ["test-params"]
    for m in METRICS:
        header += [f"{m} NINE", f"{m} TEN", f"{m} q"]
    for c in COUNTS:
        header += [f"{c} NINE", f"{c} TEN"]
    header += ["significant", "Δ avg NINE→TEN (sig)"]

    lines = [
        f"Mean ± sd over up to {len(runs)} runs; durations in µs, "
        f"{'/'.join(COUNTS)} a count. Bold = measure significant (paired "
        f"Wilcoxon signed-rank, Benjamini–Hochberg adjusted q ≤ {ALPHA}). "
        f"Significant flag: {UP} TEN slower, {DOWN} TEN faster, "
        f"{MIXED} mixed.",
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]

    unpaired = []
    for case in sorted(samples):
        test, params = case
        if not samples[case][METRICS[0]]:
            unpaired.append(f"{test} {params}")
            continue
        cells = [f"{test} {params}"]
        deltas = []
        chgs = []
        for m in METRICS:
            pairs = samples[case][m]
            n = [nine for nine, _ in pairs]
            x = [ten for _, ten in pairs]
            q = adjusted[(case, m)]
            ncell, xcell = _stat(n), _stat(x)
            if q <= ALPHA:
                ncell, xcell = f"**{ncell}**", f"**{xcell}**"
                navg, xavg = mean(n), mean(x)
                chg = round((xavg - navg) / navg * 100) if navg else 0
                deltas.append(f"{m} {chg:+d}%")
                chgs.append(chg)
            cells += [ncell, xcell, _pfmt(q)]
        for c in COUNTS:
            pairs = samples[case][c]
            cells += [
                _stat([nine for nine, _ in pairs]),
                _stat([ten for _, ten in pairs]),
            ]
        cells.append(_direction(chgs) if chgs else "-")
        cells.append(", ".join(deltas) if deltas else "-")
        lines.append("| " + " | ".join(cells) + " |")

    lines += [
        "",
        "q is the Benjamini–Hochberg adjusted p-value of a two-sided "
        "paired Wilcoxon signed-rank test on the per-run NINE→TEN "
        f"differences for that measure; significant at q ≤ {ALPHA}. "
        f"Smallest raw p in this table: {_pfmt(min(ps)) if ps else 'n/a'}. "
        f"{'/'.join(COUNTS)} is reported, not tested: it tells a block "
        "that did the same work more slowly from one that redid work.",
    ]

    lines += _power_note(samples, len(raw))
    lines += _coverage_notes(runs, unpaired, dropped, raw)
    return lines


def _power_note(
    samples: Dict[CaseKey, Dict[str, PairedSamples]], tests: int
) -> List[str]:
    """
    Warn when the run count cannot resolve an isolated effect.

    A signed-rank test on `n` pairs has 2^n sign assignments, so no raw p
    can fall below 2/2^n whatever the effect size; times the table's test
    count that is a floor on q.
    """
    paired = [len(m[METRICS[0]]) for m in samples.values() if m[METRICS[0]]]
    if not paired or not tests:
        return []
    n = max(paired)
    if 2 / 2**n * tests <= ALPHA:
        return []
    needed = 1
    while 2 / 2**needed * tests > ALPHA:
        needed += 1
    return [
        "",
        f"⚠️ {n} paired runs cannot reach q ≤ {ALPHA} for an effect in one "
        f"measure alone across {tests} tests; that needs ≥ {needed} runs.",
    ]


def _coverage_notes(
    runs: List[Run],
    unpaired: List[str],
    dropped: Dict[CaseKey, int],
    raw: Dict[Tuple[CaseKey, str], float],
) -> List[str]:
    """
    Report what the table could not cover.

    A case only appears above when a run measured both forks; a case or
    run that failed or was skipped would otherwise vanish from the report
    without a trace.
    """
    notes: List[str] = []
    if unpaired:
        notes += [
            "",
            f"**{len(unpaired)} case(s) not compared** — no run measured "
            "both forks (fixture failed, was skipped, or was not filled):",
            "",
        ]
        notes += [f"- {case}" for case in sorted(unpaired)]
    partial = {
        c: n for c, n in dropped.items() if f"{c[0]} {c[1]}" not in unpaired
    }
    if partial:
        notes += [
            "",
            f"**{len(partial)} case(s) compared on fewer than {len(runs)} "
            "runs** — the remaining runs measured only one fork:",
            "",
        ]
        notes += [
            f"- {test} {params}: {len(runs) - missing} of {len(runs)} runs"
            for (test, params), missing in sorted(partial.items())
        ]
    if raw:
        notes += [
            "",
            f"{len(raw)} hypothesis tests in this table "
            f"({len({c for c, _ in raw})} cases × {len(METRICS)} measures).",
        ]
    return notes


def _provenance(now: str, shas: List[Optional[str]]) -> str:
    """Markdown header: descriptions link, cycle time, and repo shas."""
    repos = [
        "execution-specs",
        "monad-eest-rust-harness",
        "monad-bft",
        "monad",
    ]
    ref = DIAGRAMS_URL.format(ref=shas[0] or "HEAD")
    lines = [
        f"Test-case descriptions: {ref}",
        "",
        f"Cycle {now}",
        "",
        "| repo | sha |",
        "| --- | --- |",
    ]
    lines += [
        f"| {r} | {s or '?'} |" for r, s in zip(repos, shas, strict=True)
    ]
    return "\n".join(lines)


def report(
    run_dirs: Tuple[Path, ...],
    now: Optional[str] = None,
    shas: Optional[List[Optional[str]]] = None,
) -> str:
    """
    Render the significance table for the given run directories.

    Raises `click.ClickException` if fewer than two of them hold a
    `timing_consume.csv`, since a comparison needs at least two samples
    per fork.
    """
    reports = [Path(d) / "timing_consume.csv" for d in run_dirs]
    missing = [str(p.parent) for p in reports if not p.exists()]
    runs = [parse(p) for p in reports if p.exists()]
    if missing:
        click.echo(
            f"skipping {len(missing)} run dir(s) without a "
            f"timing_consume.csv: {', '.join(missing)}",
            err=True,
        )
    if len(runs) < 2:
        raise click.ClickException(
            f"need >=2 runs with timing_consume.csv, found {len(runs)}"
        )
    md = "\n".join(build(runs))
    if now:
        md = f"{_provenance(now, shas or [None] * 4)}\n\n{md}"
    return md


@click.command()
@click.argument(
    "run_dirs",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--md",
    "md_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the Markdown report here instead of stdout.",
)
@click.option(
    "--now",
    default=None,
    help="Cycle timestamp; prefixes the report with a provenance header.",
)
@click.option("--repo", default=None, help="execution-specs sha.")
@click.option("--harness", default=None, help="monad-eest-rust-harness sha.")
@click.option("--monad-bft", default=None, help="monad-bft sha.")
@click.option("--monad", default=None, help="monad-execution sha.")
def main(
    run_dirs: Tuple[Path, ...],
    md_path: Optional[Path],
    now: Optional[str],
    repo: Optional[str],
    harness: Optional[str],
    monad_bft: Optional[str],
    monad: Optional[str],
) -> None:
    """
    Build a NINE-vs-TEN significance table from perf timing runs.

    Each RUN_DIRS argument is one `consume direct --timing-report` output
    directory holding a `timing_consume.csv`.
    """
    md = report(run_dirs, now, [repo, harness, monad_bft, monad])
    if md_path:
        md_path.write_text(md + "\n", encoding="utf-8")
    else:
        click.echo(md)


if __name__ == "__main__":
    main()
