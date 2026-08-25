#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pyyaml",
# ]
# ///
"""
Build the job matrix for a fixture release rehearsal.

Usage: `check_release_matrix.py [features] [branch]`, where `features`
is an optional comma- or space-separated subset of the feature names in
`.github/configs/feature.yaml`.

With no `features`, an EIP branch rehearses the feature it releases for
its own EIP and nothing else, and every other branch rehearses every
feature. Either way a branch that adds a feature is covered without
touching the workflow.

Reuse `generate_build_matrix.py` so a rehearsal fills exactly what the
release fills, then flatten the per-feature matrices into the single
`fill_matrix` a `strategy.matrix` consumes.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from generate_build_matrix import (  # noqa: E402
    FEATURE_CONFIG,
    FORK_RANGES_CONFIG,
    build_matrix,
    fail,
    load_config,
)

# EIP branches follow `eips/<fork>/eip-<n>`, with `+` joining the EIP
# numbers a combined branch carries, e.g. `eips/amsterdam/eip-2345+3456`.
EIP_BRANCH_RE = re.compile(r"^eips/[^/]+/eip-([0-9]+(?:\+[0-9]+)*)$")


def eip_features(defined: list[str], branch: str) -> list[str]:
    """
    Return the features *branch* releases for its own EIPs.

    An EIP branch names its feature after the EIPs it carries, so
    `eips/monad_next/eip-7997` releases `monad_eip7997` and
    `eips/amsterdam/eip-2345+3456` releases `monad_eip2345+3456`. The
    numbers must match in full: a combined branch does not claim the
    feature of either EIP on its own, and neither claims the combined
    one. Return an empty list for any other branch, and for an EIP
    branch that has not declared a feature of its own yet.
    """
    match = EIP_BRANCH_RE.match(branch)
    if not match:
        return []
    numbers = re.compile(rf"eip{re.escape(match.group(1))}(?![0-9+])")
    return [name for name in defined if numbers.search(name)]


def defined_features(config: dict) -> list[str]:
    """Return every feature name in `feature.yaml`, in config order."""
    return [
        name for name, feature in config.items() if isinstance(feature, dict)
    ]


def select_features(config: dict, requested: str, branch: str) -> list[str]:
    """
    Narrow the rehearsal to the requested features.

    An empty request falls back to the features *branch* releases for
    its own EIPs, then to every feature: this fork releases all of
    them. An unknown name fails the run rather than silently
    rehearsing less than was asked for.
    """
    defined = defined_features(config)
    names = [name for name in requested.replace(",", " ").split() if name]
    if names:
        unknown = [name for name in names if name not in defined]
        if unknown:
            fail(
                f"unknown feature(s) {', '.join(unknown)}; "
                f"{FEATURE_CONFIG} defines {', '.join(defined)}"
            )
        return names
    from_branch = eip_features(defined, branch)
    if from_branch:
        print(
            f"Branch '{branch}' releases {', '.join(from_branch)}; "
            "rehearsing only that.",
            file=sys.stderr,
        )
        return from_branch
    return defined


def main() -> None:
    """Print the rehearsal's feature list and fill matrix to stdout."""
    requested = sys.argv[1] if len(sys.argv) > 1 else ""
    branch = sys.argv[2] if len(sys.argv) > 2 else ""

    config = load_config(FEATURE_CONFIG)
    fork_ranges = load_config(FORK_RANGES_CONFIG) or []

    features = select_features(config, requested, branch)
    if not features:
        fail(f"{FEATURE_CONFIG} defines no feature")

    matrix: list[dict] = []
    for name in features:
        entries, _ = build_matrix(config[name], name, fork_ranges)
        matrix.extend(entries)

    print(f"features={json.dumps(features)}")
    print(f"fill_matrix={json.dumps(matrix)}")


if __name__ == "__main__":
    main()
