"""Defines the MIP-18 call stack introspection specification reference."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ReferenceSpec:
    """Defines the reference spec version and git path."""

    git_path: str
    version: str


ref_spec_18 = ReferenceSpec(
    "MIPS/MIP-18.md", "628ffa40505cde048fba859919cc14e87b8dda54"
)
