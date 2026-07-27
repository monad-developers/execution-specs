#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pyyaml",
# ]
# ///
"""
Generate the build matrix for release fixture workflows.

Read `.github/configs/feature.yaml` and emit a flat JSON build matrix
suitable for ``strategy.matrix`` in GitHub Actions.

Features whose ``fill-params`` contain ``--until`` are split across the
shared fork ranges defined in `.github/configs/fork-ranges.yaml`.
Features using ``--fork`` (single fork) produce a single unsplit entry.
"""

import json
import re
import sys
from pathlib import Path

import yaml

FEATURE_CONFIG = Path(".github/configs/feature.yaml")
FORK_RANGES_CONFIG = Path(".github/configs/fork-ranges.yaml")

# Canonical fork ordering used to filter fork ranges per feature.
FORK_ORDER = [
    "Frontier",
    "Homestead",
    "DAOFork",
    "TangerineWhistle",
    "SpuriousDragon",
    "Byzantium",
    "Constantinople",
    "Istanbul",
    "MuirGlacier",
    "Berlin",
    "London",
    "ArrowGlacier",
    "GrayGlacier",
    "Paris",
    "Shanghai",
    "Cancun",
    "Prague",
    "Osaka",
    "BPO1",
    "BPO2",
    "Amsterdam",
    # Monad forks are layered above the canonical forks. They never
    # share a shared fork range, so their absolute position only needs
    # to sort after the canonical forks.
    "MONAD_EIGHT",
    "MONAD_NINE",
    "MONAD_TEN",
]

FORK_INDEX = {name: i for i, name in enumerate(FORK_ORDER)}


def load_config(path: Path) -> dict:
    """Load and return the feature configuration."""
    with open(path) as f:
        return yaml.safe_load(f)


def parse_fork_bounds(fill_params: str) -> tuple[str | None, str | None]:
    """
    Extract the ``--from``/``--until`` bounds from fill-params.

    Return ``(None, None)`` when ``--fork`` is used instead (single-fork
    feature that should not be split). ``--from`` may be absent, in
    which case the feature fills from the first canonical fork.
    """
    if re.search(r"--fork\b", fill_params):
        return None, None
    from_m = re.search(r"--from[=\s]+(\S+)", fill_params)
    until_m = re.search(r"--until[=\s]+(\S+)", fill_params)
    return (
        from_m.group(1) if from_m else None,
        until_m.group(1) if until_m else None,
    )


def applicable_ranges(
    fork_ranges: list[dict], from_fork: str | None, until_fork: str
) -> list[dict]:
    """
    Return fork ranges overlapping the feature's ``[from, until]``.

    Clamp each returned range to the feature's bounds so we never fill
    outside its declared window. A feature whose bounds fall outside
    every shared range (e.g. the Monad forks) yields an empty list,
    which signals an unsplit build.
    """
    limit = FORK_INDEX[until_fork]
    start = FORK_INDEX[from_fork] if from_fork else 0
    result = []
    for r in fork_ranges:
        r_from = FORK_INDEX[r["from"]]
        r_until = FORK_INDEX[r["until"]]
        if r_until < start or r_from > limit:
            continue
        entry = dict(r)
        if r_from < start:
            entry["from"] = from_fork
        if r_until > limit:
            entry["until"] = until_fork
        result.append(entry)
    return result


def build_matrix(
    feature: dict, name: str, fork_ranges: list[dict]
) -> tuple[list[dict], str]:
    """
    Build the matrix for a single feature.

    Return (build_entries, combine_labels).  Split features produce
    one entry per fork range and a space-separated label string for
    the combine step.  Unsplit features produce a single entry with
    empty labels.
    """
    from_fork, until = parse_fork_bounds(feature["fill-params"])
    if until and fork_ranges:
        ranges = applicable_ranges(fork_ranges, from_fork, until)
        if len(ranges) > 1:
            build = [
                {
                    "feature": name,
                    "label": r["label"],
                    "from_fork": r["from"],
                    "until_fork": r["until"],
                }
                for r in ranges
            ]
            labels = " ".join(r["label"] for r in ranges)
            return build, labels

    return [
        {
            "feature": name,
            "label": "",
            "from_fork": "",
            "until_fork": "",
        }
    ], ""


def main() -> None:
    """Entry point."""
    if len(sys.argv) != 2:
        print(
            "Usage: generate_build_matrix.py <feature>",
            file=sys.stderr,
        )
        sys.exit(1)

    config = load_config(FEATURE_CONFIG)
    fork_ranges = load_config(FORK_RANGES_CONFIG) or []
    name = sys.argv[1]

    if name not in config or not isinstance(config[name], dict):
        print(
            f"Error: feature '{name}' not found in {FEATURE_CONFIG}.",
            file=sys.stderr,
        )
        sys.exit(1)

    build, labels = build_matrix(config[name], name, fork_ranges)

    print(f"build_matrix={json.dumps(build)}")
    print(f"feature_name={name}")
    print(f"combine_labels={labels}")


if __name__ == "__main__":
    main()
