"""
Ethereum Virtual Machine (EVM) Interpreter.

.. contents:: Table of Contents
    :backlinks: none
    :local:

Introduction
------------

A straightforward interpreter that executes EVM code.
"""

from dataclasses import dataclass
from typing import Optional, Set, Tuple, final

from ethereum_types.bytes import Bytes, Bytes0
from ethereum_types.numeric import U256, Uint, ulen

from ethereum.exceptions import EthereumException
from ethereum.state import Address
from ethereum.trace import (
    EvmStop,
    OpEnd,
    OpException,
    OpStart,
    PrecompileEnd,
    PrecompileStart,
    TransactionEnd,
    evm_trace,
)

from ..blocks import Log
from ..state_tracker import (
    account_has_code_or_nonce,
    copy_tx_state,
    destroy_storage,
    get_account,
    get_balance_original,
    get_code,
    increment_nonce,
    is_sender_authority,
    iter_all_addresses,
    mark_account_created,
    move_ether,
    restore_tx_state,
    set_code,
)
from ..vm import Message
from ..vm.eoa_delegation import (
    get_delegated_code_address,
    is_valid_delegation,
    set_delegation,
)
from ..vm.gas import GasCosts, charge_gas
from ..vm.precompiled_contracts import MONAD_PRECOMPILE_ADDRESSES
from ..vm.precompiled_contracts.mapping import PRE_COMPILED_CONTRACTS
from . import Evm, EvmMemory
from .exceptions import (
    AddressCollision,
    ExceptionalHalt,
    InvalidContractPrefix,
    InvalidOpcode,
    OutOfGasError,
    Revert,
    RevertInMonadPrecompile,
    RevertOnReserveBalance,
    StackDepthLimitError,
)
from .instructions import Ops, op_implementation
from .runtime import get_valid_jump_destinations

STACK_DEPTH_LIMIT = Uint(1024)
MAX_CODE_SIZE = 128 * 1024
MAX_INIT_CODE_SIZE = 2 * MAX_CODE_SIZE

RESERVE_BALANCE = U256(10 * 10**18)  # 10 MON


def is_reserve_balance_violated(evm: Evm) -> bool:
    """
    Check if any EOA has violated the reserve balance constraint.

    Returns True if a violation is detected, False otherwise.

    Reads "balance at start of EVM execution" from
    ``tx_env.tx_snapshot`` — captured at the top-level message begin
    (after pre-execution gas/nonce deduction). Recreates pre-refactor
    ``state._snapshots[0]`` semantics. Callable from anywhere inside
    the tx (top-level end-of-tx check or the dippedIntoReserve
    precompile).
    """
    message = evm.message
    tx_state = message.tx_env.state
    tx_env = message.tx_env
    snapshot = tx_env.tx_snapshot
    assert snapshot is not None, (
        "tx_snapshot must be set on tx_env before reserve balance check"
    )

    # Collect accounts_to_delete from all ancestor frames. accounts_to_delete
    # only propagates upward on success (incorporate_child_on_success), so a
    # child frame like a precompile call won't see deletions from its parent.
    all_accounts_to_delete: Set[Address] = set()
    current_evm = evm
    while True:
        all_accounts_to_delete.update(current_evm.accounts_to_delete)
        if current_evm.message.parent_evm is not None:
            current_evm = current_evm.message.parent_evm
        else:
            break

    for addr in iter_all_addresses(tx_state):
        # Account SELFDESTRUCTed - skip explicitly.
        if addr in all_accounts_to_delete:
            continue

        acc = get_account(tx_state, addr)
        # For creation txs, code hasn't been set yet on the new contract
        # (set_code runs after process_message returns). Use evm.output which
        # holds the code to be deployed.
        if (
            isinstance(message.target, Bytes0)
            and addr == message.current_target
        ):
            code = evm.output
        else:
            code = get_code(tx_state, acc.code_hash)

        # NOTE: this also matches initcode ending with empty code deployments
        # via `Op.STOP` or `Op.RETURN(0, 0)`, AND check made during initcode
        # execution, but this aligns with Monad EVM implementation.
        if code == b"" or is_valid_delegation(code):
            original_balance = get_balance_original(snapshot, addr)

            is_exception = (
                message.tx_env.origin == addr
                and not is_sender_authority(tx_state.parent, addr)
                and not is_valid_delegation(code)
            )

            if tx_env.origin == addr:
                # gas_fees already deducted, need to re-add if sender
                # to match with spec.
                gas_fees = U256(tx_env.gas_price * tx_env.tx_gas_limit)
                original_balance += gas_fees
                reserve = min(RESERVE_BALANCE, original_balance)
                assert is_exception or gas_fees <= reserve, (
                    "gas fees exceed the reserve for a sender that "
                    "cannot empty; consensus only sequences a "
                    "transaction whose sender's in-flight gas fees "
                    "fit within the reserve"
                )
                # Gas spend does not count against the reserve, so the
                # gas already deducted from the balance is added back by
                # lowering the threshold. Clamped at zero for U256.
                threshold = reserve - min(reserve, gas_fees)
            else:
                threshold = RESERVE_BALANCE

            if (
                acc.balance < original_balance
                and acc.balance < threshold
                and not is_exception
            ):
                return True
    return False


@final
@dataclass
class MessageCallOutput:
    """
    Output of a particular message call.

    Contains the following:

          1. `gas_left`: remaining gas after execution.
          2. `refund_counter`: gas to refund after execution.
          3. `logs`: list of `Log` generated during execution.
          4. `accounts_to_delete`: Contracts which have self-destructed.
          5. `error`: The error from the execution if any.
          6. `return_data`: The output of the execution.
    """

    gas_left: Uint
    refund_counter: U256
    logs: Tuple[Log, ...]
    accounts_to_delete: Set[Address]
    error: Optional[EthereumException]
    return_data: Bytes


def process_message_call(message: Message) -> MessageCallOutput:
    """
    If `message.target` is empty then it creates a smart contract
    else it executes a call from the `message.caller` to the `message.target`.

    Parameters
    ----------
    message :
        Transaction specific items.

    Returns
    -------
    output : `MessageCallOutput`
        Output of the message call

    """
    tx_state = message.tx_env.state
    refund_counter = U256(0)
    if message.target == Bytes0(b""):
        is_collision = account_has_code_or_nonce(
            tx_state, message.current_target
        )
        if is_collision:
            return MessageCallOutput(
                gas_left=Uint(0),
                refund_counter=U256(0),
                logs=tuple(),
                accounts_to_delete=set(),
                error=AddressCollision(),
                return_data=Bytes(b""),
            )
        else:
            evm = process_create_message(message)
    else:
        if message.tx_env.authorizations != ():
            refund_counter += set_delegation(message)

        delegated_address = get_delegated_code_address(message.code)
        if delegated_address is not None:
            message.disable_precompiles = True
            message.accessed_addresses.add(delegated_address)
            message.code = get_code(
                tx_state,
                get_account(tx_state, delegated_address).code_hash,
            )
            message.code_address = delegated_address
            message.disable_create_opcodes = True

        evm = process_message(message)

    if evm.error:
        logs: Tuple[Log, ...] = ()
        accounts_to_delete = set()
    else:
        logs = evm.logs
        accounts_to_delete = evm.accounts_to_delete
        refund_counter += U256(evm.refund_counter)

    tx_end = TransactionEnd(
        int(message.gas) - int(evm.gas_left), evm.output, evm.error
    )
    evm_trace(evm, tx_end)

    return MessageCallOutput(
        gas_left=evm.gas_left,
        refund_counter=refund_counter,
        logs=logs,
        accounts_to_delete=accounts_to_delete,
        error=evm.error,
        return_data=evm.output,
    )


def process_create_message(message: Message) -> Evm:
    """
    Executes a call to create a smart contract.

    Parameters
    ----------
    message :
        Transaction specific items.

    Returns
    -------
    evm: :py:class:`~ethereum.forks.monad_nine.vm.Evm`
        Items containing execution specific objects.

    """
    tx_state = message.tx_env.state
    # take snapshot of state before processing the message
    snapshot = copy_tx_state(tx_state)

    # If the address where the account is being created has storage, it is
    # destroyed. This can only happen in the following highly unlikely
    # circumstances:
    # * The address created by a `CREATE` call collides with a subsequent
    #   `CREATE` or `CREATE2` call.
    # * The first `CREATE` happened before Spurious Dragon and left empty
    #   code.
    destroy_storage(tx_state, message.current_target)

    # In the previously mentioned edge case the preexisting storage is ignored
    # for gas refund purposes. In order to do this we must track created
    # accounts. This tracking is also needed to respect the constraints
    # added to SELFDESTRUCT by EIP-6780.
    mark_account_created(tx_state, message.current_target)

    increment_nonce(tx_state, message.current_target)
    evm = process_message(message)
    if not evm.error:
        contract_code = evm.output
        contract_code_gas = (
            ulen(contract_code) * GasCosts.CODE_DEPOSIT_PER_BYTE
        )
        try:
            if len(contract_code) > 0:
                if contract_code[0] == 0xEF:
                    raise InvalidContractPrefix
            charge_gas(evm, contract_code_gas)
            if len(contract_code) > MAX_CODE_SIZE:
                raise OutOfGasError
        except ExceptionalHalt as error:
            restore_tx_state(tx_state, snapshot)
            evm.gas_left = Uint(0)
            evm.output = b""
            evm.error = error
        else:
            set_code(tx_state, message.current_target, contract_code)
    else:
        restore_tx_state(tx_state, snapshot)
    return evm


def process_message(message: Message) -> Evm:
    """
    Move ether and execute the relevant code.

    Parameters
    ----------
    message :
        Transaction specific items.

    Returns
    -------
    evm: :py:class:`~ethereum.forks.monad_nine.vm.Evm`
        Items containing execution specific objects

    """
    tx_state = message.tx_env.state
    if message.depth > STACK_DEPTH_LIMIT:
        raise StackDepthLimitError("Stack depth limit reached")

    code = message.code
    valid_jump_destinations = get_valid_jump_destinations(code)

    parent_high_watermark = (
        message.parent_evm.memory.high_watermark_bytes
        if message.parent_evm is not None
        else 0
    )

    evm = Evm(
        pc=Uint(0),
        stack=[],
        memory=EvmMemory(
            data=bytearray(), high_watermark_bytes=parent_high_watermark
        ),
        code=code,
        gas_left=message.gas,
        valid_jump_destinations=valid_jump_destinations,
        logs=(),
        refund_counter=0,
        running=True,
        message=message,
        output=b"",
        accounts_to_delete=set(),
        return_data=b"",
        error=None,
        accessed_addresses=message.accessed_addresses,
        accessed_storage_keys=message.accessed_storage_keys,
    )

    # take snapshot of state before processing the message
    snapshot = copy_tx_state(tx_state)
    # Monad: at top-level message begin, also stash the snapshot on
    # tx_env so the reserve-balance check and dippedIntoReserve
    # precompile can read "balance at start of EVM execution" from
    # any frame.
    if message.depth == 0:
        message.tx_env.tx_snapshot = snapshot

    if message.should_transfer_value and message.value != 0:
        move_ether(
            tx_state,
            message.caller,
            message.current_target,
            message.value,
        )

    try:
        if evm.message.code_address in PRE_COMPILED_CONTRACTS:
            if not message.disable_precompiles:
                evm_trace(evm, PrecompileStart(evm.message.code_address))
                PRE_COMPILED_CONTRACTS[evm.message.code_address](evm)
                evm_trace(evm, PrecompileEnd())
            elif evm.message.code_address in MONAD_PRECOMPILE_ADDRESSES:
                # Calling a precompile via delegation and it's a Monad
                # precompile => revert.
                raise RevertInMonadPrecompile
        else:
            while evm.running and evm.pc < ulen(evm.code):
                try:
                    op = Ops(evm.code[evm.pc])
                except ValueError as e:
                    raise InvalidOpcode(evm.code[evm.pc]) from e

                evm_trace(evm, OpStart(op))
                op_implementation[op](evm)
                evm_trace(evm, OpEnd())

            evm_trace(evm, EvmStop(Ops.STOP))

    except RevertInMonadPrecompile as error:
        evm_trace(evm, OpException(error))
        evm.gas_left = Uint(0)
        # evm.output preserved — contains the raw error message
        evm.error = error
    except ExceptionalHalt as error:
        evm_trace(evm, OpException(error))
        evm.gas_left = Uint(0)
        evm.output = b""
        evm.error = error
    except Revert as error:
        evm_trace(evm, OpException(error))
        evm.error = error

    if evm.error:
        restore_tx_state(tx_state, snapshot)
    else:
        # FIXME: index_in_block is a proxy for not being a system tx
        if message.depth == 0 and message.tx_env.index_in_block is not None:
            if is_reserve_balance_violated(evm):
                restore_tx_state(tx_state, snapshot)
                evm.error = RevertOnReserveBalance()
                return evm
    return evm
