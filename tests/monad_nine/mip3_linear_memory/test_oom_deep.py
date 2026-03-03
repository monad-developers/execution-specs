"""
Tests deep nested call frames with memory allocation.

Tests that run on both MONAD_EIGHT and MONAD_NINE to compare behavior:
- MONAD_NINE: OOM when cumulative memory exceeds 8MB limit
- MONAD_EIGHT: can go above this limit if allocations spread across frames
"""

from typing import List

import pytest
from execution_testing import (
    Account,
    Alloc,
    Op,
    StateTestFiller,
    Transaction,
)
from execution_testing.base_types.base_types import Address
from execution_testing.forks.forks.forks import MONAD_NINE
from execution_testing.forks.helpers import Fork

from .spec import Spec, ref_spec_3

REFERENCE_SPEC_GIT_PATH = ref_spec_3.git_path
REFERENCE_SPEC_VERSION = ref_spec_3.version

slot_depth = 0x100

pytestmark = [
    pytest.mark.valid_from("MONAD_EIGHT"),
    pytest.mark.pre_alloc_group(
        "mip3_tests",
        reason="Tests linear memory MIP-3",
    ),
]


def test_nested_frames_deep(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test memory allocation across many nested call frames.

    Uses small chunk sizes to maximize depth. In MONAD_NINE, reverts when
    cumulative memory exceeds MAX_TX_MEMORY_USAGE. This test contrasts that
    in MONAD_EIGHT one tx can allocate more memory in total and serves as
    a sanity check.
    """
    gas_limit = 30_000_000
    chunk_size = 128 * 1024  # 128KB per frame

    # Calculate expected max depth for each fork
    # MONAD_NINE: limited by 8MB cumulative memory = 64 frames
    # MONAD_EIGHT: limited by gas (quadratic cost + 63/64 forwarding)
    #   ~150 frames
    max_depth = (
        100  # should successfully allocate at all depts for MONAD_EIGHT
    )

    # Deploy contracts from deepest to shallowest
    addresses: List[Address] = []
    for depth in range(max_depth - 1, -1, -1):
        if depth == max_depth - 1:
            # Deepest level: allocate memory and store success
            contract = Op.MLOAD(chunk_size - 32) + Op.SSTORE(
                slot_depth + depth, 1
            )
        else:
            callee = addresses[-1]
            contract = (
                Op.SSTORE(slot_depth + depth, 1)
                + Op.MLOAD(chunk_size - 32)
                + Op.DELEGATECALL(address=callee)
                + Op.POP
            )
        addresses.append(pre.deploy_contract(contract))

    entry_address = addresses[-1]

    tx = Transaction(
        gas_limit=gas_limit,
        to=entry_address,
        sender=pre.fund_eoa(),
    )

    # Calculate expected successful depth based on fork
    if fork >= MONAD_NINE:
        # OOM at cumulative memory > 8MB
        expected_max_success_depth = Spec.MAX_TX_MEMORY_USAGE // chunk_size
    else:
        # MONAD_EIGHT: All frames succeed with sufficient gas
        expected_max_success_depth = max_depth

    storage = {slot_depth + d: 1 for d in range(expected_max_success_depth)}

    state_test(
        pre=pre,
        post={entry_address: Account(storage=storage)},
        tx=tx,
    )
