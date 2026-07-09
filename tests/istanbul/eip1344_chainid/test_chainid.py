"""
Tests [EIP-1344: CHAINID opcode](https://eips.ethereum.org/EIPS/eip-1344).
"""

import pytest
from execution_testing import (
    Account,
    Alloc,
    ChainConfig,
    Op,
    StateTestFiller,
    Transaction,
)

REFERENCE_SPEC_GIT_PATH = "EIPS/eip-1344.md"
REFERENCE_SPEC_VERSION = "02e46aebc80e6e5006ab4d2daa41876139f9a9e2"


@pytest.mark.with_all_typed_transactions(
    marks=lambda tx_type: pytest.mark.skip(
        reason="type 3 transactions aren't supported in Monad"
    )
    if tx_type == 3
    else None
)
@pytest.mark.ported_from(
    [
        "https://github.com/ethereum/tests/blob/v13.3/src/GeneralStateTestsFiller/stChainId/chainIdFiller.json",
    ],
)
@pytest.mark.valid_from("Istanbul")
def test_chainid(
    state_test: StateTestFiller,
    pre: Alloc,
    chain_config: ChainConfig,
    typed_transaction: Transaction,
) -> None:
    """Test CHAINID opcode."""
    chain_id = chain_config.chain_id
    contract_address = pre.deploy_contract(Op.SSTORE(1, Op.CHAINID) + Op.STOP)

    tx = typed_transaction.copy(
        chain_id=chain_id,
        to=contract_address,
    )
    # Reset gas-price fields so the execute-remote framework can set them
    # based on the live network's base fee (fixture defaults are too low
    # for Monad).
    tx.model_fields_set.discard("max_fee_per_gas")
    tx.model_fields_set.discard("max_priority_fee_per_gas")
    tx.model_fields_set.discard("gas_price")

    post = {
        contract_address: Account(storage={1: chain_id}),
    }

    state_test(pre=pre, post=post, tx=tx)
