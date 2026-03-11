"""
Tests for staking precompile fork transition behavior.

Verify that the staking precompile is not available before the
MONAD_EIGHT fork and becomes available at and after the transition.
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
from execution_testing.forks import get_transition_fork_predecessor
from execution_testing.forks.forks.forks import MONAD_EIGHT
from execution_testing.forks.helpers import Fork

from .helpers import build_calldata, generous_gas
from .spec import (
    GAS_GET_PROPOSER_VAL_ID,
    SELECTOR_GET_PROPOSER_VAL_ID,
    STAKING_PRECOMPILE,
    ref_spec_staking,
)

REFERENCE_SPEC_GIT_PATH = ref_spec_staking.git_path
REFERENCE_SPEC_VERSION = ref_spec_staking.version


@pytest.mark.valid_at_transition_to("MONAD_EIGHT", subsequent_forks=True)
def test_fork_transition(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Test staking precompile availability at fork transition.

    Before the fork, the precompile doesn't exist, so CALL returns
    empty output (RETURNDATASIZE == 0). After the fork, the
    precompile returns a 32-byte result (RETURNDATASIZE == 32).
    """
    sender = pre.fund_eoa()

    # True when the fork before the transition already has the staking
    # precompile (e.g. MONAD_EIGHT → MONAD_NINE transition).
    staking_pre = get_transition_fork_predecessor(fork) >= MONAD_EIGHT

    # Use getProposerValId() — minimal calldata (4 bytes), low gas
    callee_code = (
        build_calldata(SELECTOR_GET_PROPOSER_VAL_ID, 4)
        + Op.CALL(
            gas=GAS_GET_PROPOSER_VAL_ID + 10000,
            address=STAKING_PRECOMPILE,
            args_offset=60,
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
            Op.TIMESTAMP,
            Op.CALL(gas=0xFFFF, address=callee_address),
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
                    # Call succeeds (precompile just returns empty)
                    14_999: 1,
                    # Call succeeds on fork transition block
                    15_000: 1,
                    # Call continues to succeed after transition
                    15_001: 1,
                }
            ),
            callee_address: Account(
                storage={
                    # Pre-transition: available iff predecessor >= MONAD_EIGHT
                    14_999: 1 if staking_pre else 0,
                    # Precompile available, RETURNDATASIZE==32
                    15_000: 1,
                    # Precompile continues to work
                    15_001: 1,
                }
            ),
        },
    )
