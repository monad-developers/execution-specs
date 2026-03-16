"""Pytest configuration for MIP-4 reserve balance introspection tests."""

import pytest
from execution_testing import Address, Alloc, Bytecode, Op

from .helpers import RefillCall, RefillFactory
from .spec import Spec


@pytest.fixture
def refill_factory(pre: Alloc) -> RefillFactory:
    """
    Fixture that provides a factory for creating refill contracts.

    Returns a function that, when called, deploys a new refill contract and
    returns a callable to generate bytecode that calls the refill contract.
    """

    def factory_function() -> RefillCall:
        """
        Deploy a refill contract and return call helper.

        The refill contract uses SELFDESTRUCT to transfer RESERVE_BALANCE to
        the address provided in calldata. SELFDESTRUCT sends ETH without
        triggering target's code, avoiding recursion with delegated EOAs.
        """
        code = bytes(Op.SELFDESTRUCT(Op.CALLDATALOAD(0)))
        refill_address = pre.deploy_contract(
            code=code, balance=Spec.RESERVE_BALANCE
        )

        def make_refill_call(target: Address | Bytecode) -> Bytecode:
            """
            Generate bytecode to call the refill contract with target address.
            """
            return Op.MSTORE(0, target) + Op.CALL(
                gas=Op.GAS,
                address=refill_address,
                args_offset=0,
                args_size=32,
            )

        return make_refill_call

    return factory_function
