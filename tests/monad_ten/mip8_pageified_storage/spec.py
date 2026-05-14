"""Defines MIP-8 pageified storage specification constants."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ReferenceSpec:
    """Defines the reference spec version and git path."""

    git_path: str
    version: str


ref_spec_8 = ReferenceSpec(
    "MIPS/MIP-8.md", "1d3d530310a957528bd7a5c52cec853e56550b1e"
)


@dataclass(frozen=True)
class Spec:
    """
    Parameters from the pageified storage specification as defined
    at MIP-8.
    """

    SLOTS_PER_PAGE = 128
    PAGE_SIZE_BYTES = 4096

    GAS_PAGE_BASE_COST = 100
    GAS_PAGE_LOAD_COST = 8_000
    GAS_PAGE_WRITE_COST = 2_800
    GAS_PAGE_STATE_GROWTH_COST = 17_000
