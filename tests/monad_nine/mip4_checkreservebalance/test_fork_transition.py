"""
Tests for reserve balance precompile fork transition behavior.

Tests verify that the reserve balance precompile is not available before the
fork and becomes available at and after the fork transition.
"""

import pytest
from execution_testing import (
    Account,
    Alloc,
    Block,
    BlockchainTestFiller,
    Op,
    Transaction,
)
from execution_testing.forks.helpers import Fork

from .helpers import SELECTOR_SETUP, generous_gas
from .spec import Spec, ref_spec_mip4

REFERENCE_SPEC_GIT_PATH = ref_spec_mip4.git_path
REFERENCE_SPEC_VERSION = ref_spec_mip4.version


@pytest.mark.valid_at_transition_to("MONAD_NINE", subsequent_forks=True)
def test_fork_transition(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test reserve balance precompile availability at fork transition.

    Before the MONAD_NINE, the precompile doesn't exist, so CALL returns
    empty output (RETURNDATASIZE == 0). After, the precompile
    returns a 32-byte result (RETURNDATASIZE == 32).
    """
    precompile_before = (
        Spec.RESERVE_BALANCE_PRECOMPILE
        in fork.transitions_from().precompiles()
    )
    sender = pre.fund_eoa()

    callee_code = (
        SELECTOR_SETUP
        + Op.CALL(
            gas=Op.GAS,
            address=Spec.RESERVE_BALANCE_PRECOMPILE,
            args_offset=28,
            args_size=4,
            ret_offset=0,
            ret_size=32,
        )
        + Op.POP
        + Op.SSTORE(Op.TIMESTAMP, Op.EQ(Op.RETURNDATASIZE, 32))
        + Op.STOP
    )
    callee_address = pre.deploy_contract(
        code=callee_code,
        storage={14_999: "0xdeadbeef"},
    )
    caller_address = pre.deploy_contract(
        code=Op.SSTORE(
            Op.TIMESTAMP, Op.CALL(gas=0xFFFF, address=callee_address)
        ),
        storage={14_999: "0xdeadbeef"},
    )
    blocks = [
        Block(
            timestamp=14_999,
            txs=[
                Transaction(
                    to=caller_address,
                    sender=sender,
                    nonce=0,
                    gas_limit=generous_gas(fork),
                )
            ],
        ),
        Block(
            timestamp=15_000,
            txs=[
                Transaction(
                    to=caller_address,
                    sender=sender,
                    nonce=1,
                    gas_limit=generous_gas(fork),
                )
            ],
        ),
        Block(
            timestamp=15_001,
            txs=[
                Transaction(
                    to=caller_address,
                    sender=sender,
                    nonce=2,
                    gas_limit=generous_gas(fork),
                )
            ],
        ),
    ]
    blockchain_test(
        pre=pre,
        blocks=blocks,
        post={
            caller_address: Account(
                storage={
                    14_999: 1,  # Call succeeds
                    15_000: 1,  # Call succeeds on fork transition block
                    15_001: 1,  # Call continues to succeed after transition
                }
            ),
            callee_address: Account(
                storage={
                    14_999: 1 if precompile_before else 0,
                    15_000: 1,  # Precompile available, RETURNDATASIZE==32
                    15_001: 1,  # Precompile continues to work
                }
            ),
        },
    )
