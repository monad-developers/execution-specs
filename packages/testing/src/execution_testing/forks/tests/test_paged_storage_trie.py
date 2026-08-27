"""
Tests for the MIP-8 page-based storage commitment.

Two kinds of checks live here:

1. Property / consistency tests that need no precomputed constants. They
   validate structural guarantees of ``page_commit`` and
   ``storage_root_paged`` (length, determinism, the empty-trie root, the
   zero-page rejection, and that the high-level root matches a manual
   reconstruction from the documented primitives). These are independently
   valid regardless of the exact hash output.

2. Known-answer vectors for ``page_commit`` (``_PAGE_COMMIT_VECTORS``).

   .. warning::

      These expected values were generated from this very specification's
      ``page_commit`` implementation, not from an independent source. They
      pin the commitment so accidental changes to the ISMC logic are caught,
      but they do NOT by themselves prove the implementation matches the
      canonical MIP-8 definition. Before relying on them as ground truth,
      cross-check against the Monad reference client. If the reference client
      disagrees, update these vectors (and fix ``page_commit``).
"""

from typing import Dict, Mapping

import pytest
from ethereum.crypto.hash import Hash32, keccak256
from ethereum.merkle_patricia_trie import (
    EMPTY_TRIE_ROOT,
    Trie,
    bytes_to_nibble_list,
    encode_internal_node,
    patricialize,
    root,
    trie_set,
)
from ethereum.paged_storage_trie import (
    PAGE_SIZE,
    WORDS_PER_PAGE,
    page_commit,
    storage_root_paged,
)
from ethereum_rlp import rlp
from ethereum_types.bytes import Bytes32
from ethereum_types.numeric import U256, Uint


def _page(slot_values: Mapping[int, int]) -> bytes:
    """Pack ``{offset: value}`` into a single 4096-byte page image."""
    page = bytearray(PAGE_SIZE)
    for offset, value in slot_values.items():
        page[offset * 32 : (offset + 1) * 32] = value.to_bytes(32, "big")
    return bytes(page)


def _storage(slot_values: Mapping[int, int]) -> Dict[Bytes32, U256]:
    """Build a ``{slot_key: value}`` storage mapping from ``{slot: value}``."""
    return {
        U256(slot).to_be_bytes32(): U256(value)
        for slot, value in slot_values.items()
    }


# --- page_commit known-answer vectors -------------------------------------
#
# Generated from the spec ``page_commit`` implementation. See the module
# docstring warning: confirm against the Monad reference client before
# treating these as canonical. Each value maps {slot_offset: slot_value}.
_PAGE_COMMIT_VECTORS = [
    pytest.param(
        {0: 1},
        "80218c63919cd8c68aa9a5c0117bb8b46eb02099a7ce0b47a36e7b21658cc9f9",
        id="single_slot_0",
    ),
    pytest.param(
        {127: 1},
        "39a2175f8fac8fbf447383b46ff40e03673b388c05c87e50ed7b3f1a810c98d8",
        id="single_slot_127",
    ),
    pytest.param(
        {0: 1, 1: 2},
        "46906319c63bef972eab21b85ebaadda0b3d1648c8cd333be15f61b7dbc96e4e",
        id="pair_0_1",
    ),
    pytest.param(
        {0: 1, 1: 2, 64: 3, 127: 4},
        "49d161b8515a5264dc34b7f00effc8fd06587e099192a5e06def7935280e721e",
        id="multilevel_0_1_64_127",
    ),
    pytest.param(
        dict.fromkeys(range(WORDS_PER_PAGE), 1),
        "a3e39c072e2f951b586e5261f7f19303dc9b95bdba8df7617508d9c10bd49ea2",
        id="full_page_all_one",
    ),
    pytest.param(
        {i: i + 1 for i in range(0, WORDS_PER_PAGE, 2)},
        "1499b50116676c8f01dae124cb2ee14eba8c0f8b8f658fd655d8091edbbfec3e",
        id="sparse_even_slots",
    ),
]


@pytest.mark.parametrize("slot_values, expected_hex", _PAGE_COMMIT_VECTORS)
def test_page_commit_known_vectors(
    slot_values: Dict[int, int], expected_hex: str
) -> None:
    """Verify ``page_commit`` matches the pinned (characterization) vector."""
    assert page_commit(_page(slot_values)).hex() == expected_hex


# --- page_commit properties ------------------------------------------------


@pytest.mark.parametrize(
    "slot_values",
    [{0: 1}, {127: 1}, {0: 1, 1: 2}, dict.fromkeys(range(WORDS_PER_PAGE), 1)],
)
def test_page_commit_length_is_32(slot_values: Dict[int, int]) -> None:
    """A page commitment is always a 32-byte digest."""
    assert len(page_commit(_page(slot_values))) == 32


def test_page_commit_rejects_all_zero_page() -> None:
    """An all-zero page is never committed (omitted at a higher level)."""
    with pytest.raises(AssertionError):
        page_commit(bytes(PAGE_SIZE))


def test_page_commit_deterministic() -> None:
    """The same page image always commits to the same digest."""
    page = _page({0: 1, 1: 2, 64: 3})
    assert page_commit(page) == page_commit(page)


def test_page_commit_sensitive_to_value() -> None:
    """Changing a slot's value changes the commitment."""
    assert page_commit(_page({0: 1})) != page_commit(_page({0: 2}))


def test_page_commit_sensitive_to_offset() -> None:
    """The same value at a different slot offset commits differently."""
    assert page_commit(_page({0: 1})) != page_commit(_page({1: 1}))


# --- storage_root_paged properties -----------------------------------------


def test_empty_storage_is_empty_trie_root() -> None:
    """Empty storage commits to the canonical empty-trie root."""
    assert storage_root_paged({}) == EMPTY_TRIE_ROOT


def test_storage_root_length_is_32() -> None:
    """A storage root is always a 32-byte hash."""
    assert len(storage_root_paged(_storage({0: 1, 128: 2}))) == 32


def test_storage_root_order_independent() -> None:
    """Insertion order of slots does not affect the storage root."""
    forward = _storage({0: 1, 1: 2, 128: 3, 300: 4})
    reverse = dict(reversed(list(forward.items())))
    assert storage_root_paged(forward) == storage_root_paged(reverse)


def test_storage_root_distinguishes_pages() -> None:
    """Placing a value on a different page yields a different root."""
    same_page = storage_root_paged(_storage({0: 1, 1: 2}))
    other_page = storage_root_paged(_storage({0: 1, 128: 2}))
    assert same_page != other_page


def test_storage_root_clears_to_empty_when_slots_omitted() -> None:
    """
    Storage with every nonzero slot removed returns to the empty root.

    Cleared slots are dropped from the state trie (never stored as zero), so
    a fully cleared account is indistinguishable from a never-written one.
    """
    assert storage_root_paged(_storage({})) == EMPTY_TRIE_ROOT


@pytest.mark.parametrize(
    "slot_values",
    [{0: 1}, {0: 1, 1: 2}, {0: 1, 5: 9, 127: 3}],
)
def test_single_page_root_matches_manual_reconstruction(
    slot_values: Dict[int, int],
) -> None:
    """
    ``storage_root_paged`` matches a keccak-MPT built by hand.

    Cross-checks the trie-assembly layer for a single page (page 0): slot
    grouping, the ``keccak256(page_index)`` trie key, and the
    ``rlp.encode(commitment)`` leaf framing — independently of the BLAKE3
    internals of ``page_commit``.
    """
    commitment = page_commit(_page(slot_values))
    key = keccak256(U256(0).to_be_bytes32())
    obj = {bytes_to_nibble_list(key): rlp.encode(commitment)}

    root_node = encode_internal_node(patricialize(obj, Uint(0)))
    encoded = rlp.encode(root_node)
    expected = keccak256(encoded) if len(encoded) < 32 else Hash32(root_node)

    assert storage_root_paged(_storage(slot_values)) == expected


def _storage_trie(slot_values: Mapping[int, int]) -> Trie[Bytes32, U256]:
    """Build a secured storage trie holding `{slot: value}`."""
    trie: Trie[Bytes32, U256] = Trie(secured=True, default=U256(0))
    for slot, value in slot_values.items():
        trie_set(trie, U256(slot).to_be_bytes32(), U256(value))
    return trie


@pytest.mark.parametrize(
    "slot_values",
    [{0: 1}, {0: 1, 1: 2}, {0: 1, 128: 2}, {2**256 - 1: 1}],
)
def test_paged_root_differs_from_keccak_mpt_root(
    slot_values: Dict[int, int],
) -> None:
    """The page commitment does not coincide with a keccak MPT over slots."""
    assert storage_root_paged(_storage(slot_values)) != root(
        _storage_trie(slot_values)
    )


def test_fully_cleared_page_root_matches_never_written() -> None:
    """
    Clearing every slot of a page restores the root the storage had
    before that page was written.

    Zeroing a slot drops it from the trie, so the emptied page has no
    commitment entry at all — `page_commit` rejects an all-zero image.
    """
    trie = _storage_trie({0: 1})
    baseline = storage_root_paged(trie._data)

    for offset in range(WORDS_PER_PAGE):
        slot = U256(WORDS_PER_PAGE + offset).to_be_bytes32()
        trie_set(trie, slot, U256(offset + 1))
    assert storage_root_paged(trie._data) != baseline

    for offset in range(WORDS_PER_PAGE):
        slot = U256(WORDS_PER_PAGE + offset).to_be_bytes32()
        trie_set(trie, slot, U256(0))
    assert storage_root_paged(trie._data) == baseline
