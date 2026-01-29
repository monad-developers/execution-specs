"""Defines reserve balance specification constants and functions."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ReferenceSpec:
    """Defines the reference spec version and git path."""

    git_path: str
    version: str


ref_spec_3 = ReferenceSpec(
    "MIPS/MIP-3.md", "a70093b2549d935e31accd62e2e784114253fdc2"
)


@dataclass(frozen=True)
class Spec:
    """
    Parameters from the linear memory specifications as defined at MIP-3
    ........
    """

    MAX_TX_MEMORY_USAGE = 8 * 1024 * 1024  # 8 MiB
