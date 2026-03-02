"""Test Calling Precompile Range (close to zero)."""

import pytest
from execution_testing import (
    Account,
    Address,
    Alloc,
    Bytecode,
    Fork,
    Op,
    StateTestFiller,
    Storage,
    Transaction,
)

UPPER_BOUND = 0x101
RETURNDATASIZE_OFFSET = 0x10000000000000000  # Must be greater than UPPER_BOUND


@pytest.mark.parametrize(
    "calldata_size",
    [
        pytest.param(0, id="empty_calldata"),
        pytest.param(31, id="31_bytes"),
        pytest.param(32, id="32_bytes"),
    ],
)
# Should we shift the tested address range upwards to the
# range where Monad precompiles are put
@pytest.mark.parametrize("monad_range", [True, False])
@pytest.mark.valid_from("Byzantium")
def test_precompile_absence(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    calldata_size: int,
    monad_range: bool,
) -> None:
    """
    Test that addresses close to zero are not precompiles unless active in the
    fork.
    """
    active_precompiles = fork.precompiles()
    storage = Storage()
    call_code = Bytecode()
    offset = 0x1000 - UPPER_BOUND // 2 if monad_range else 1
    for address in range(offset, UPPER_BOUND + offset):
        if Address(address) in active_precompiles:
            continue
        if address == 0x1000:
            # Monad Staking Precompile not implemented yet
            continue
        call_code += Op.SSTORE(
            address,
            Op.CALL(gas=0, address=address, args_size=calldata_size),
        )
        storage[address] = 1
        if Op.RETURNDATASIZE in fork.valid_opcodes():
            call_code += Op.SSTORE(
                address + RETURNDATASIZE_OFFSET,
                Op.RETURNDATASIZE,
            )
            storage[address + RETURNDATASIZE_OFFSET] = 0

    call_code += Op.STOP

    entry_point_address = pre.deploy_contract(
        call_code, storage=storage.canary()
    )

    gas_costs = fork.gas_costs()
    sstore_cost = (
        2
        * (UPPER_BOUND - len(active_precompiles))
        * (gas_costs.G_STORAGE_SET + gas_costs.G_COLD_SLOAD)
    )
    access_cost = (
        UPPER_BOUND - len(active_precompiles)
    ) * gas_costs.G_COLD_ACCOUNT_ACCESS

    tx = Transaction(
        to=entry_point_address,
        gas_limit=50_000 + sstore_cost + access_cost,
        sender=pre.fund_eoa(),
        protected=True,
    )

    state_test(
        pre=pre,
        tx=tx,
        post={
            entry_point_address: Account(
                storage=storage,
            )
        },
    )
