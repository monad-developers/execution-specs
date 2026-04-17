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

    GAS_BASE_SLOAD = 100
    GAS_COLD_PAGE_READ = 8_100
    GAS_BASE_SSTORE = 100
    GAS_PAGE_WRITE = 5_000
    GAS_NEW_SLOT = 20_000
