"""
Shared primitives for the MIP-8 page-based storage commitment.

The storage root commits to ``{page_index: page_commit(page)}`` pairs in a
keccak256 MPT, where each page groups 128 storage slots and is committed with
a BLAKE3 "Induced Subtree Merkle Commit" (ISMC). These primitives are imported
by both the execution spec fork and the testing framework so the two compute
identical storage roots.
"""

from types import SimpleNamespace
from typing import Dict, List, Mapping, MutableMapping

from ethereum_rlp import rlp
from ethereum_types.bytes import Bytes, Bytes32
from ethereum_types.numeric import U256, Uint

from ethereum.crypto.blake3 import (
    CHUNK_END,
    CHUNK_START,
    DERIVE_KEY_MATERIAL,
    IV,
    blake3_hash,
    compress,
    words_to_bytes,
)
from ethereum.crypto.hash import Hash32, keccak256
from ethereum.merkle_patricia_trie import (
    bytes_to_nibble_list,
    encode_internal_node,
    patricialize,
)

PAGE_SIZE = 4096
WORDS_PER_PAGE = 128


def page_commit(page: bytes) -> Bytes:
    """
    ISMC_Commit per MIP-8.

    Direct port of the pseudocode at MIP-8 § Page Commitment Function.
    """
    assert len(page) == PAGE_SIZE

    slot_bitmap = 0
    for i in range(WORDS_PER_PAGE):
        if page[i * 32 : (i + 1) * 32] != bytes(32):
            slot_bitmap |= 1 << i

    # Zero pages should be omitted at a higher level.
    assert slot_bitmap != 0

    pair_bitmap = 0
    for i in range(64):
        if (slot_bitmap >> (2 * i)) & 0b11:
            pair_bitmap |= 1 << i

    pair_leaf_domain = b"ultra_merkle_pair_leaf_domain___"
    leaf_iv = compress(
        IV, pair_leaf_domain + bytes(32), 64, 0, DERIVE_KEY_MATERIAL
    )[:8]

    active_nodes: List[SimpleNamespace] = []
    for i in range(64):
        if (pair_bitmap >> i) & 1:
            pair_data = page[i * 64 : (i + 1) * 64]
            leaf_hash = words_to_bytes(
                compress(leaf_iv, pair_data, 64, 0, DERIVE_KEY_MATERIAL)[:8]
            )
            active_nodes.append(SimpleNamespace(index=i, value=leaf_hash))

    for level in range(6):
        next_level_nodes: List[SimpleNamespace] = []
        i = 0
        while i < len(active_nodes):
            current_node = active_nodes[i]
            if i + 1 < len(active_nodes):
                next_node = active_nodes[i + 1]
                if (current_node.index >> (level + 1)) == (
                    next_node.index >> (level + 1)
                ):
                    parent_value = words_to_bytes(
                        compress(
                            IV,
                            current_node.value + next_node.value,
                            64,
                            0,
                            CHUNK_START | CHUNK_END,
                        )[:8]
                    )
                    next_level_nodes.append(
                        SimpleNamespace(
                            index=current_node.index, value=parent_value
                        )
                    )
                    i += 2
                    continue
            next_level_nodes.append(current_node)
            i += 1
        active_nodes = next_level_nodes
        if len(active_nodes) == 1:
            break

    subtree_root = active_nodes[0].value
    seal_payload = slot_bitmap.to_bytes(16, "little") + subtree_root
    return Bytes(blake3_hash(seal_payload))


def _prepare_storage_trie(
    storage: Mapping[Bytes32, U256],
) -> Mapping[Bytes, Bytes]:
    """
    Group slots into pages, compute BLAKE3 page commitments, and return a
    keccak256-secured mapping suitable for standard MPT construction.
    """
    pages: Dict[U256, bytearray] = {}

    for preimage, value in storage.items():
        slot = U256.from_be_bytes(preimage)
        page_idx = slot >> U256(7)
        offset = int(slot & U256(0x7F))

        if page_idx not in pages:
            pages[page_idx] = bytearray(PAGE_SIZE)

        value_bytes = value.to_be_bytes32()
        start = offset * 32
        pages[page_idx][start : start + 32] = value_bytes

    mapped: MutableMapping[Bytes, Bytes] = {}
    for page_idx, page_data in pages.items():
        commitment = page_commit(bytes(page_data))
        key = keccak256(page_idx.to_be_bytes32())
        # Difference (8) — Storage MPT leaf value framing: the leaf holds
        # the RLP-string framing (`0xa0 || commitment`) of the commitment.
        mapped[bytes_to_nibble_list(key)] = rlp.encode(commitment)

    return mapped


def storage_root_paged(storage: Mapping[Bytes32, U256]) -> Hash32:
    """
    Compute the storage root over a keccak256 MPT whose leaves are BLAKE3
    page commitments (MIP-8).

    ``storage`` maps each 32-byte slot key to its `U256` value.
    """
    obj = _prepare_storage_trie(storage)

    root_node = encode_internal_node(patricialize(obj, Uint(0)))
    if len(rlp.encode(root_node)) < 32:
        return keccak256(rlp.encode(root_node))
    else:
        assert isinstance(root_node, Bytes)
        return Hash32(root_node)
