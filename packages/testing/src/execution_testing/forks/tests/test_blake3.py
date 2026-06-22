"""
Test BLAKE3 implementation against official test vectors.

Test vectors from:
https://github.com/BLAKE3-team/BLAKE3/blob/master/test_vectors/test_vectors.json

The input for each test case is generated as bytes(i % 251 for i in range(N)).
Only the ``hash`` field (unkeyed hash mode) is tested here since that is the
mode used by MIP-8.
"""

import json
from pathlib import Path

import pytest
from ethereum.crypto.blake3 import blake3_hash

VECTORS_PATH = Path(__file__).parent / "blake3_test_vectors.json"

with open(VECTORS_PATH) as f:
    _TEST_DATA = json.load(f)

_CASES = [(c["input_len"], c["hash"][:64]) for c in _TEST_DATA["cases"]]


def _make_input(length: int) -> bytes:
    return bytes([i % 251 for i in range(length)])


@pytest.mark.parametrize(
    "input_len, expected_hex",
    _CASES,
    ids=[str(c[0]) for c in _CASES],
)
def test_blake3_hash(input_len: int, expected_hex: str) -> None:
    """
    Verify unkeyed BLAKE3 hash matches official test vector.
    """
    data = _make_input(input_len)
    result = blake3_hash(data).hex()
    assert result == expected_hex, (
        f"input_len={input_len}: expected {expected_hex}, got {result}"
    )
