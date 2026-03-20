"""
Test ported from static filler.

Ported from:
tests/static/state_tests/stStaticCall
static_callcodecall_10_SuicideEnd2Filler.json
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
        "tests/static/state_tests/stStaticCall/static_callcodecall_10_SuicideEnd2Filler.json",  # noqa: E501
    ],
)
@pytest.mark.valid_from("Cancun")
@pytest.mark.parametrize(
    "tx_value, expected_post",
    [
        (
            0,
            {
                Address("0x44d09ddf088dd88c0e91fa7ef74973ff94ad7414"): Account(
                    storage={0: 1, 1: 0x2CF641}
                )
            },
        ),
        (
            1,
            {
                Address("0x44d09ddf088dd88c0e91fa7ef74973ff94ad7414"): Account(
                    storage={0: 1, 1: 0x2CDC15}
                )
            },
        ),
    ],
    ids=["case0", "case1"],
)
@pytest.mark.pre_alloc_mutable
@pytest.mark.slow
def test_static_callcodecall_10_suicide_end2(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    tx_value: int,
    expected_post: dict,
) -> None:
    """Test ported from static filler."""
    coinbase = Address("0x2adc25665018aa1fe0e6bc666dac8fc2697ff9ba")
    sender = EOA(
        key=0xE04D1AC7DDDA0C98397D56A0B501E960D4CD325A39286919AC23C1A07009A869
    )

    env = Environment(
        fee_recipient=coinbase,
        number=1,
        timestamp=1000,
        prev_randao=0x20000,
        base_fee_per_gas=10,
        gas_limit=30000000,
    )

    # Source: LLL
    # {  [[ 0 ]] (CALLCODE 150000 <contract:0x1000000000000000000000000000000000000001> (CALLVALUE) 0 64 0 64 ) [[ 1 ]] (GAS) }  # noqa: E501
    contract = pre.deploy_contract(
        code=(
            Op.SSTORE(
                key=0x0,
                value=Op.CALLCODE(
                    gas=0x249F0,
                    address=0xB60789F240AC9F12FCDE1E4BBD5042A7F30932D4,
                    value=Op.CALLVALUE,
                    args_offset=0x0,
                    args_size=0x40,
                    ret_offset=0x0,
                    ret_size=0x40,
                ),
            )
            + Op.SSTORE(key=0x1, value=Op.GAS)
            + Op.STOP
        ),
        balance=0xDE0B6B3A7640000,
        nonce=0,
        address=Address("0x44d09ddf088dd88c0e91fa7ef74973ff94ad7414"),  # noqa: E501
    )
    pre.deploy_contract(
        code=(
            Op.POP(
                Op.STATICCALL(
                    gas=0xC350,
                    address=0xCFB5784A5E49924BECC2D5C5D2EE0A9B141E6216,
                    args_offset=0x0,
                    args_size=0x40,
                    ret_offset=0x0,
                    ret_size=0x40,
                ),
            )
            + Op.SELFDESTRUCT(
                address=0x44D09DDF088DD88C0E91FA7EF74973FF94AD7414
            )
            + Op.STOP
        ),
        balance=0x2540BE400,
        nonce=0,
        address=Address("0xb60789f240ac9f12fcde1e4bbd5042a7f30932d4"),  # noqa: E501
    )
    pre.deploy_contract(
        code=Op.MSTORE(offset=0x2, value=0x1) + Op.STOP,
        balance=0x2540BE400,
        nonce=0,
        address=Address("0xcfb5784a5e49924becc2d5c5d2ee0a9b141e6216"),  # noqa: E501
    )
    pre[sender] = Account(balance=0xDE0B6B3A7640000)

    tx = Transaction(
        sender=sender,
        to=contract,
        gas_limit=3000000,
        value=tx_value,
    )

    # Slot 1 stores gas remaining (GAS opcode). On Monad, higher cold
    # access costs consume more gas, reducing the stored value.
    # MIP-3 (MONAD_NINE) linear memory pricing saves 15 gas.
    gas_costs = fork.gas_costs()
    gas_adj = (
        (gas_costs.GAS_COLD_ACCOUNT_ACCESS - 2600) * 2
        + (gas_costs.GAS_COLD_SLOAD - 2100)
    )
    if fork >= MONAD_NINE:
        gas_adj -= 15
    post = {}
    for addr, acct in expected_post.items():
        storage = dict(acct.storage) if acct.storage else {}
        if 1 in storage:
            storage[1] -= gas_adj
        post[addr] = Account(storage=storage)

    state_test(env=env, pre=pre, post=post, tx=tx)
