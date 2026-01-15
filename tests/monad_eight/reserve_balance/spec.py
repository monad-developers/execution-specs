"""Defines reserve balance specification constants and functions."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ReferenceSpec:
    """Defines the reference spec version and git path."""

    git_path: str
    version: str


# FIXME
ref_spec_7702 = ReferenceSpec(
    "EIPS/eip-7702.md", "99f1be49f37c034bdd5c082946f5968710dbfc87"
)


@dataclass(frozen=True)
class Spec:
    """
    # FIXME
    Parameters from the reserve balance specifications as defined at
    ........
    """

    RESERVE_BALANCE = 10 * 10**18
