"""Bytecode for the delegated create tests."""

from execution_testing import Address, Bytecode, Op

slot_created = 0x1
slot_call_result = 0x2


def factory_code(create_opcode: Op) -> Bytecode:
    """Deploy the calldata as initcode and record the created address."""
    return Op.CALLDATACOPY(0, 0, Op.CALLDATASIZE) + Op.SSTORE(
        slot_created, create_opcode(value=0, offset=0, size=Op.CALLDATASIZE)
    )


def forwarding_code(call_opcode: Op, address: Address) -> Bytecode:
    """Forward the calldata to `address` and record the call result."""
    return Op.CALLDATACOPY(0, 0, Op.CALLDATASIZE) + Op.SSTORE(
        slot_call_result,
        call_opcode(
            gas=Op.GAS,
            address=address,
            args_offset=0,
            args_size=Op.CALLDATASIZE,
        ),
    )
