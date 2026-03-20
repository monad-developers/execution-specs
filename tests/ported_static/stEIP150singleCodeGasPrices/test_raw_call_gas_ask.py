"""
Test ported from static filler.

Ported from:
tests/static/state_tests/stEIP150singleCodeGasPrices/RawCallGasAskFiller.json
"""

import pytest
from execution_testing import (
    EOA,
    Account,
    Address,
    Alloc,
    Environment,
    StateTestFiller,
    Transaction,
)
from execution_testing.forks import MONAD_NINE
from execution_testing.forks.helpers import Fork
from execution_testing.vm import Op

REFERENCE_SPEC_GIT_PATH = "N/A"
REFERENCE_SPEC_VERSION = "N/A"


@pytest.mark.ported_from(
    [
        "tests/static/state_tests/stEIP150singleCodeGasPrices/RawCallGasAskFiller.json",  # noqa: E501
    ],
)
@pytest.mark.valid_from("Cancun")
@pytest.mark.pre_alloc_mutable
def test_raw_call_gas_ask(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """Test ported from static filler."""
    coinbase = Address("0x2adc25665018aa1fe0e6bc666dac8fc2697ff9ba")
    sender = EOA(
        key=0x4F31B3206FBF0E0E598B9B1A7D8AC86302A0FF1D8930738F1BEBAE9B67173E52
    )

    env = Environment(
        fee_recipient=coinbase,
        number=1,
        timestamp=1000,
        prev_randao=0x20000,
        base_fee_per_gas=10,
        gas_limit=10000000,
    )

    # Source: LLL
    # { [0] (GAS) (CALL 3000000 <contract:0x094f5374fce5edbc8e2a8697c15331677e6ebf0b> 0 0 0 0 0) [[1]] (SUB @0 (GAS)) }  # noqa: E501
    contract = pre.deploy_contract(
        code=(
            Op.MSTORE(offset=0x0, value=Op.GAS)
            + Op.POP(
                Op.CALL(
                    gas=0x2DC6C0,
                    address=0xE497CD0909C3691E0B6D2A42E26F36696FC27BA5,
                    value=0x0,
                    args_offset=0x0,
                    args_size=0x0,
                    ret_offset=0x0,
                    ret_size=0x0,
                ),
            )
            + Op.SSTORE(key=0x1, value=Op.SUB(Op.MLOAD(offset=0x0), Op.GAS))
            + Op.STOP
        ),
        nonce=0,
        address=Address("0x18817869e5f5b3f55f57bb7791ea8ee6f62604c8"),  # noqa: E501
    )
    callee = pre.deploy_contract(
        code=Op.SSTORE(key=0x2, value=Op.GAS) + Op.STOP,
        nonce=0,
        address=Address("0xe497cd0909c3691e0b6d2a42e26f36696fc27ba5"),  # noqa: E501
    )
    pre[sender] = Account(balance=0xE8D4A51000)

    tx = Transaction(
        sender=sender,
        to=contract,
        gas_limit=500000,
    )

    # Slot 1 measures gas consumed by CALL (cold) + callee's cold SLOAD.
    # MIP-3 (MONAD_NINE) saves 3 gas on MSTORE/MLOAD.
    gas_costs = fork.gas_costs()
    gas_adj = (
        (gas_costs.GAS_COLD_ACCOUNT_ACCESS - 2600)
        + (gas_costs.GAS_COLD_SLOAD - 2100)
    )
    if fork >= MONAD_NINE:
        gas_adj -= 3

    # For _ask tests, the callee gets proportional gas via gas=GAS.
    # On M9, the callee receives 7380 less gas due to cold access changes.
    callee_adj = 7380 if fork >= MONAD_NINE else 0

    post = {
        contract: Account(storage={1: 24739 + gas_adj}),
        callee: Account(storage={2: 0x727BB - callee_adj}),
    }

    state_test(env=env, pre=pre, post=post, tx=tx)
