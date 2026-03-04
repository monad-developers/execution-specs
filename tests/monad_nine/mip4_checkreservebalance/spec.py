"""Defines MIP-4 reserve balance precompile specification constants."""

from dataclasses import dataclass

from execution_testing import Address


@dataclass(frozen=True)
class ReferenceSpec:
    """Defines the reference spec version and git path."""

    git_path: str
    version: str


ref_spec_mip4 = ReferenceSpec("MIPS/MIP-4.md", "main")


@dataclass(frozen=True)
class Spec:
    """Parameters from the MIP-4 specification."""

    RESERVE_BALANCE = 10 * 10**18

    RESERVE_BALANCE_PRECOMPILE = Address(0x1001)

    # Aligns with G_WARM_ACCOUNT_ACCESS at time of MIP-4.
    GAS_COST = 100

    # keccak256("dippedIntoReserve()")[:4].hex() == "3a61584e"
    DIPPED_INTO_RESERVE_SELECTOR = bytes.fromhex("3A61584E")

    ERROR_METHOD_NOT_SUPPORTED = "method not supported"
    ERROR_INPUT_INVALID = "input is invalid"
    ERROR_VALUE_NONZERO = "value is nonzero"
