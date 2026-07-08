"""Transaction helpers for MIP-11 tests."""

from execution_testing import Alloc, Transaction

from .spec import BASE_FEE, MON

# Gas limit used by fee-bearing transactions. The accumulated priority
# fee is exactly ``gas_limit * priority_fee_per_gas`` (Monad charges the
# full gas limit), so a fee divisible by this value is reproduced
# exactly.
FEE_TX_GAS = 10**6


def make_fee_tx(
    pre: Alloc,
    fee: int,
    *,
    gas_limit: int = FEE_TX_GAS,
) -> Transaction:
    """
    Return a transaction whose priority fee equals ``fee`` exactly.

    ``fee`` must be divisible by ``gas_limit``.
    """
    assert fee % gas_limit == 0, "fee must be divisible by gas_limit"
    priority = fee // gas_limit
    max_fee = BASE_FEE + priority
    gas_cost = gas_limit * max_fee
    return Transaction(
        gas_limit=gas_limit,
        max_fee_per_gas=max_fee,
        max_priority_fee_per_gas=priority,
        to=pre.fund_eoa(0),
        sender=pre.fund_eoa(gas_cost + 11 * MON),
    )


def make_fee_txs(
    pre: Alloc,
    total_fee: int,
    n_txs: int,
    *,
    gas_limit: int = FEE_TX_GAS,
) -> list[Transaction]:
    """Return ``n_txs`` transactions whose priority fees sum to ``fee``."""
    assert total_fee % n_txs == 0, "total_fee must be divisible by n_txs"
    return [
        make_fee_tx(pre, total_fee // n_txs, gas_limit=gas_limit)
        for _ in range(n_txs)
    ]
