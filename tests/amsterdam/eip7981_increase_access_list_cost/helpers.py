"""Helpers for testing EIP-7981."""

from typing import List

from execution_testing import AccessList, Fork
from execution_testing.forks import MONAD_EIGHT


def calculate_access_list_data_cost(
    access_list: List[AccessList], fork: Fork
) -> int:
    """
    Calculate the flat data surcharge for the access list.

    Each address or storage key byte contributes four floor tokens,
    priced at the supplied fork's floor token gas rate.
    """
    total_bytes = 0

    for access in access_list:
        # Count bytes in address (20 bytes)
        total_bytes += len(access.address)

        # Count bytes in each storage key (32 bytes each)
        for slot in access.storage_keys:
            total_bytes += len(slot)

    return total_bytes * 4 * fork.gas_costs().TX_DATA_TOKEN_FLOOR


def billed_gas(fork: Fork, gas_limit: int, gas_used: int) -> int:
    """
    Return the gas a successful transaction's receipt reports.

    Monad bills the whole limit, with no refund and no floor comparison.
    """
    return gas_limit if fork >= MONAD_EIGHT else gas_used
