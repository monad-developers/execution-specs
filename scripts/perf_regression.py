#!/usr/bin/env python3
"""
Build a NINE-vs-NEXT significance table from perf timing runs.

Reads the `timing_consume.csv` produced by `consume direct
--timing-report` for several identical runs (one dir each) and, per
(test, params) case, reports each measure's mean +/- sd over the runs for
both forks, the two-sided Mann-Whitney U p-value comparing the forks,
and (for significant measures) the NINE->NEXT average change.

Usage: perf_regression.py [--md OUT.md] [--html OUT.html]
       [--now TS --repo SHA --harness SHA --monad-bft SHA --monad SHA]
       [DIR ...]  (default dirs: ../timing_[0-9]*)
Writes GitHub-flavored Markdown to --md (or stdout if omitted); when
--now is given, prefixes a provenance header. With
--html also writes a standalone HTML rendering whose table spans the
full window width.
"""

from __future__ import annotations

import csv
import glob
import html
import sys
from itertools import combinations
from math import comb, erfc, sqrt
from pathlib import Path
from statistics import mean, stdev

METRICS = ["tx_exec_us", "commit_us", "total_us"]
FORKS = ["MONAD_NINE", "MONAD_NEXT"]
ALPHA = 0.01  # a measure is significant at this Mann-Whitney U p-value

UP = "🔴⬆️"  # significant measures all rise NINE->NEXT (NEXT slower)
DOWN = "🟢⬇️"  # significant measures all fall NINE->NEXT (NEXT faster)
MIXED = "⚠️"  # significant measures move both up and down

# Per-case workload descriptions live here; pinned to the execution-specs
# sha of the run when known, else the repo's default branch (HEAD).
DIAGRAMS_URL = (
    "https://github.com/monad-developers/execution-specs/blob/"
    "{ref}/MIP8_PERF_TESTS_DIAGRAMS.md"
)


def _direction(chgs: list[int]) -> str:
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


def parse(report: Path) -> dict:
    """
    Map (test, params, fork) -> {metric: value} from one csv report.

    The csv holds one row per block (raw, unaggregated); each metric is
    reduced to the minimum across a case's blocks.
    """
    rows: dict = {}
    with report.open(newline="") as f:
        for row in csv.DictReader(f):
            if row["fork"] not in FORKS:
                continue
            try:
                mv = {m: int(row[m]) for m in METRICS}
            except (ValueError, TypeError):
                continue
            test = row["test"].split("::")[-1].removeprefix("test_")
            key = (test, row["params"], row["fork"])
            prev = rows.get(key)
            if prev is None:
                rows[key] = mv
            else:
                rows[key] = {m: min(prev[m], mv[m]) for m in METRICS}
    return rows


def _stat(values: list[int]) -> str:
    """Format a run sample as 'mean ± sd' (µs, rounded)."""
    sd = stdev(values) if len(values) > 1 else 0.0
    return f"{round(mean(values))} ± {round(sd)}"


def _pfmt(p: float) -> str:
    """Format a p-value compactly."""
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def _avg_ranks(vals: list[float]) -> list[float]:
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


def _mwu_p(a: list[int], b: list[int]) -> float:
    """
    Two-sided Mann-Whitney U p-value comparing samples `a` and `b`.

    Exact (permutation of ranks) for small samples; a normal
    approximation with continuity correction kicks in past 200k
    combinations. Distribution-free — no normality assumption.
    """
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return 1.0
    ranks = _avg_ranks([*a, *b])
    total = n + m
    mu = n * (total + 1) / 2  # null mean of group-a rank sum
    d = abs(sum(ranks[:n]) - mu)
    if comb(total, n) <= 200_000:
        dist = [sum(c) for c in combinations(ranks, n)]
        extreme = sum(1 for s in dist if abs(s - mu) >= d - 1e-9)
        return extreme / len(dist)
    sigma = sqrt(n * m * (total + 1) / 12)
    if sigma == 0:
        return 1.0
    return erfc(max(0.0, (d - 0.5) / sigma) / sqrt(2))


def build(runs: list[dict]) -> list[str]:
    """Return the markdown lines for the significance table."""
    cases: dict = {}
    for run in runs:
        for (test, params, fork), mv in run.items():
            per_fork = cases.setdefault((test, params), {})
            metrics = per_fork.setdefault(fork, {m: [] for m in METRICS})
            for m in METRICS:
                metrics[m].append(mv[m])

    header = ["test-params"]
    for m in METRICS:
        header += [f"{m} NINE", f"{m} NEXT", f"{m} p"]
    header += ["significant", "Δ avg NINE→NEXT (sig)"]

    lines = [
        f"Mean ± sd over {len(runs)} runs (µs). Bold = measure significant "
        f"(Mann–Whitney U p ≤ {ALPHA}). "
        f"Significant flag: {UP} NEXT slower, {DOWN} NEXT faster, "
        f"{MIXED} mixed.",
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]

    for test, params in sorted(cases):
        forks = cases[(test, params)]
        if not all(f in forks for f in FORKS):
            continue
        cells = [f"{test} {params}"]
        deltas = []
        chgs = []
        for m in METRICS:
            n, x = forks["MONAD_NINE"][m], forks["MONAD_NEXT"][m]
            p = _mwu_p(n, x)
            ncell, xcell = _stat(n), _stat(x)
            if p <= ALPHA:
                ncell, xcell = f"**{ncell}**", f"**{xcell}**"
                navg, xavg = mean(n), mean(x)
                chg = round((xavg - navg) / navg * 100) if navg else 0
                deltas.append(f"{m} {chg:+d}%")
                chgs.append(chg)
            cells += [ncell, xcell, _pfmt(p)]
        cells.append(_direction(chgs) if chgs else "-")
        cells.append(", ".join(deltas) if deltas else "-")
        lines.append("| " + " | ".join(cells) + " |")

    lines += [
        "",
        "p (MWU) is the two-sided Mann–Whitney U p-value comparing the "
        f"NINE and NEXT run samples for that measure; significant at "
        f"p ≤ {ALPHA}.",
    ]
    return lines


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MIP-8 perf: NINE vs NEXT significance</title>
<style>
  html, body { margin: 0; padding: 16px; }
  body {
    font-family: system-ui, -apple-system, sans-serif;
    color: #1a1a1a;
    background: #fff;
  }
  p { font-size: 14px; line-height: 1.4; max-width: 60em; }
  .table-wrap { overflow-x: auto; }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  th, td {
    border: 1px solid #d0d0d0;
    padding: 3px 6px;
    text-align: left;
    overflow-wrap: anywhere;
  }
  th { background: #f2f2f2; position: sticky; top: 0; }
  td:first-child, th:first-child { white-space: nowrap; }
  tbody tr:nth-child(even) { background: #fafafa; }
  strong { font-weight: 700; }
  ul { font-size: 13px; line-height: 1.5; max-width: 80em; }
  @media (prefers-color-scheme: dark) {
    body { color: #e6e6e6; background: #1a1a1a; }
    th { background: #2a2a2a; }
    th, td { border-color: #444; }
    tbody tr:nth-child(even) { background: #222; }
  }
</style>
</head>
<body>
__BODY__
</body>
</html>
"""


def _inline(text: str) -> str:
    """Render inline **bold** markdown to HTML, escaping the rest."""
    out = []
    for i, part in enumerate(text.split("**")):
        esc = html.escape(part)
        out.append(f"<strong>{esc}</strong>" if i % 2 else esc)
    return "".join(out)


def _table_html(block: list[str]) -> str:
    """Render a markdown table (list of `|`-rows) as an HTML table."""
    rows = [
        [c.strip() for c in r.strip().strip("|").split("|")] for r in block
    ]
    head = "".join(f"<th>{_inline(c)}</th>" for c in rows[0])
    body = [
        "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>"
        for row in rows[2:]
    ]
    return (
        '<div class="table-wrap"><table>\n'
        f"<thead><tr>{head}</tr></thead>\n<tbody>\n"
        + "\n".join(body)
        + "\n</tbody></table></div>"
    )


def md_to_html(md: str) -> str:
    """Render the generated markdown report as a standalone HTML page."""
    lines = md.split("\n")
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("|"):
            table = []
            while i < len(lines) and lines[i].startswith("|"):
                table.append(lines[i])
                i += 1
            blocks.append(_table_html(table))
        elif lines[i].startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{_inline(lines[i][2:])}</li>")
                i += 1
            blocks.append("<ul>\n" + "\n".join(items) + "\n</ul>")
        else:
            if lines[i].strip():
                blocks.append(f"<p>{_inline(lines[i])}</p>")
            i += 1
    return HTML_TEMPLATE.replace("__BODY__", "\n".join(blocks))


def _take_opt(argv: list[str], name: str) -> str | None:
    """Pop `--name VALUE` out of argv, returning VALUE (or None)."""
    if name not in argv:
        return None
    idx = argv.index(name)
    if idx + 1 >= len(argv):
        sys.exit(f"{name} requires a value")
    value = argv[idx + 1]
    del argv[idx : idx + 2]
    return value


def _provenance(now: str, shas: list[str | None]) -> str:
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


def main() -> None:
    """Parse the run dirs and write the markdown/HTML report."""
    argv = list(sys.argv[1:])
    md_path = _take_opt(argv, "--md")
    html_path = _take_opt(argv, "--html")
    now = _take_opt(argv, "--now")
    shas = [
        _take_opt(argv, "--repo"),
        _take_opt(argv, "--harness"),
        _take_opt(argv, "--monad-bft"),
        _take_opt(argv, "--monad"),
    ]
    dirs = argv or sorted(
        glob.glob("../timing_[0-9]*"),
        key=lambda p: int(p.rsplit("_", 1)[-1]),
    )
    runs = [
        parse(Path(d) / "timing_consume.csv")
        for d in dirs
        if (Path(d) / "timing_consume.csv").exists()
    ]
    if len(runs) < 2:
        sys.exit("need >=2 runs with timing_consume.csv")
    md = "\n".join(build(runs))
    if now:
        md = f"{_provenance(now, shas)}\n\n{md}"
    if html_path:
        Path(html_path).write_text(md_to_html(md), encoding="utf-8")
    if md_path:
        Path(md_path).write_text(md + "\n", encoding="utf-8")
    else:
        print(md)


if __name__ == "__main__":
    main()
