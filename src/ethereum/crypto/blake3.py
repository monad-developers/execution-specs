"""
BLAKE3 Cryptographic Hash Function.

.. contents:: Table of Contents
    :backlinks: none
    :local:

Introduction
------------

Implementation of BLAKE3 compression function and hash, used for
page commitments and storage trie hashing in MIP-8.
"""

import struct
from typing import List

from ethereum_types.bytes import Bytes

IV = [
    0x6A09E667,
    0xBB67AE85,
    0x3C6EF372,
    0xA54FF53A,
    0x510E527F,
    0x9B05688C,
    0x1F83D9AB,
    0x5BE0CD19,
]

MSG_PERMUTATION = [
    2,
    6,
    3,
    10,
    7,
    0,
    4,
    13,
    1,
    11,
    12,
    5,
    9,
    14,
    15,
    8,
]

CHUNK_START = 1
CHUNK_END = 2
PARENT = 4
ROOT = 8
DERIVE_KEY_MATERIAL = 64

BLOCK_LEN = 64
CHUNK_LEN = 1024
MASK_32 = 0xFFFFFFFF


def _rotate_right(x: int, n: int) -> int:
    return ((x >> n) | (x << (32 - n))) & MASK_32


def _g(
    state: List[int],
    a: int,
    b: int,
    c: int,
    d: int,
    mx: int,
    my: int,
) -> None:
    state[a] = (state[a] + state[b] + mx) & MASK_32
    state[d] = _rotate_right(state[d] ^ state[a], 16)
    state[c] = (state[c] + state[d]) & MASK_32
    state[b] = _rotate_right(state[b] ^ state[c], 12)
    state[a] = (state[a] + state[b] + my) & MASK_32
    state[d] = _rotate_right(state[d] ^ state[a], 8)
    state[c] = (state[c] + state[d]) & MASK_32
    state[b] = _rotate_right(state[b] ^ state[c], 7)


def _round(state: List[int], m: List[int]) -> None:
    _g(state, 0, 4, 8, 12, m[0], m[1])
    _g(state, 1, 5, 9, 13, m[2], m[3])
    _g(state, 2, 6, 10, 14, m[4], m[5])
    _g(state, 3, 7, 11, 15, m[6], m[7])
    _g(state, 0, 5, 10, 15, m[8], m[9])
    _g(state, 1, 6, 11, 12, m[10], m[11])
    _g(state, 2, 7, 8, 13, m[12], m[13])
    _g(state, 3, 4, 9, 14, m[14], m[15])


def _permute(m: List[int]) -> List[int]:
    return [m[i] for i in MSG_PERMUTATION]


def compress(
    chaining_value: List[int],
    block_bytes: bytes,
    block_len: int,
    counter: int,
    flags: int,
) -> List[int]:
    """
    Run the BLAKE3 compression function.

    Parameters
    ----------
    chaining_value :
        8 x 32-bit words of chaining value.
    block_bytes :
        64 bytes of message block data.
    block_len :
        Number of valid bytes in the block (0-64).
    counter :
        64-bit block counter.
    flags :
        Flag bits for this compression.

    Returns
    -------
    output : List[int]
        16 x 32-bit words.

    """
    assert len(block_bytes) == BLOCK_LEN
    m = list(struct.unpack("<16I", block_bytes))

    state = [
        chaining_value[0],
        chaining_value[1],
        chaining_value[2],
        chaining_value[3],
        chaining_value[4],
        chaining_value[5],
        chaining_value[6],
        chaining_value[7],
        IV[0],
        IV[1],
        IV[2],
        IV[3],
        counter & MASK_32,
        (counter >> 32) & MASK_32,
        block_len & MASK_32,
        flags & MASK_32,
    ]

    for _ in range(7):
        _round(state, m)
        m = _permute(m)

    for i in range(8):
        state[i] ^= state[i + 8]
        state[i + 8] ^= chaining_value[i]

    return state


def words_to_bytes(words: List[int]) -> bytes:
    """Serialize 32-bit words to little-endian bytes."""
    return struct.pack("<%dI" % len(words), *words)


def _compress_chunk(chunk: bytes, counter: int, extra_flags: int) -> List[int]:
    """
    Compress a single chunk (up to 1024 bytes) into 8 chaining
    value words.
    """
    cv = list(IV)
    num_blocks = max(1, (len(chunk) + BLOCK_LEN - 1) // BLOCK_LEN)

    for i in range(num_blocks):
        start = i * BLOCK_LEN
        block = chunk[start : start + BLOCK_LEN]
        blen = len(block)
        if blen < BLOCK_LEN:
            block = block + b"\x00" * (BLOCK_LEN - blen)

        flags = 0
        if i == 0:
            flags |= CHUNK_START
        if i == num_blocks - 1:
            flags |= CHUNK_END | extra_flags

        output = compress(cv, block, blen, counter, flags)
        cv = output[:8]

    return cv


def _parent_cv(
    left: List[int], right: List[int], extra_flags: int
) -> List[int]:
    """Merge two child chaining values into a parent."""
    block = words_to_bytes(left) + words_to_bytes(right)
    output = compress(IV, block, BLOCK_LEN, 0, PARENT | extra_flags)
    return output[:8]


def blake3_hash(data: Bytes | bytes) -> bytes:
    """
    Compute the BLAKE3 hash of input data.

    Parameters
    ----------
    data :
        Input bytes to hash.

    Returns
    -------
    digest : bytes
        32-byte BLAKE3 digest.

    """
    data = bytes(data)

    if len(data) == 0:
        output = compress(
            IV,
            b"\x00" * BLOCK_LEN,
            0,
            0,
            CHUNK_START | CHUNK_END | ROOT,
        )
        return words_to_bytes(output[:8])

    if len(data) <= CHUNK_LEN:
        cv = _compress_chunk(data, 0, ROOT)
        return words_to_bytes(cv)

    cvs: List[List[int]] = []
    offset = 0
    counter = 0
    while offset < len(data):
        chunk = data[offset : offset + CHUNK_LEN]
        offset += CHUNK_LEN
        cvs.append(_compress_chunk(chunk, counter, 0))
        counter += 1

    while len(cvs) > 2:
        next_level: List[List[int]] = []
        for i in range(0, len(cvs), 2):
            if i + 1 < len(cvs):
                next_level.append(_parent_cv(cvs[i], cvs[i + 1], 0))
            else:
                next_level.append(cvs[i])
        cvs = next_level

    root = _parent_cv(cvs[0], cvs[1], ROOT)
    return words_to_bytes(root)
