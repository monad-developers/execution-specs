"""Defines the MIP-7 extension opcodes specification reference."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ReferenceSpec:
    """Defines the reference spec version and git path."""

    git_path: str
    version: str


ref_spec_7 = ReferenceSpec(
    "MIPS/MIP-7.md", "4504a0dc1637eb4b4b8cbab155cbf509a5f0c259"
)
