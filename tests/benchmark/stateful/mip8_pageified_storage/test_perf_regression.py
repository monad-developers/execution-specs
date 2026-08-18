"""
MIP-8 performance-regression tests.

These blockchain tests compare block/transaction execution time on the
monad runloop before MIP-8 (MONAD_NINE, slot-encoded storage) and after
MIP-8 (MONAD_TEN, page-encoded storage). The same Python builds the
workload for both forks; the `fork` fixture is only consulted where the
two forks genuinely differ (storage-op gas, used to size a workload to a
gas budget). We do not assert gas — the oracle is post-state (success
markers, written slots, and read-checksum slots), so the identical
workload can be timed on both forks via the consume timing report.

Each test fills a monad block with one SLOAD/SSTORE pattern, sized to the
`gas_benchmark_value` the framework injects from
`--gas-benchmark-values` (in millions). perf_cycle.sh passes 200, the gas
the runloop stamps every monad block with.

Single-letter test parameters:
- `k`: page occupancy — non-zero slots pre-populated per page (0..128).
- `m`: number of distinct pages a spread test writes in total.
- `n`: number of contracts those `m` pages are spread evenly across.

Every transaction gets its own sender, so no block carries a sender nonce
chain that would serialise it regardless of storage access. The block-shape
sweep parametrizes this to measure what the chain alone costs.

Workloads are sized to MONAD_NINE (its cold storage costs >= MONAD_TEN),
so the same iteration count never out-of-gases on either fork; a
post-fork block may therefore be gas-underfull while doing identical I/O
work, which is exactly the effect being measured.

That is also why these tests pass `skip_gas_used_validation`: the gas a
block actually uses differs between the forks by construction, so no
single `expected_benchmark_gas_used` can match both. The framework still
enforces that a block stays within its budget, `_assert_tx_within_budget`
checks the plan against it, and `_per_iter_gas` bounds its own estimate,
so a block cannot quietly end up sized for less work than the budget.

Benchmark fixtures omit the full post state, which keeps them small and
quick to consume; the monad consumer then verifies each run against the
block's state root, which commits to the whole state. The `post` argument
still drives what the fill asserts, so the markers and checksums below
remain the oracle at fill time.

`MIP8_PERF_REPEATS` (default 1) emits that many copies of each workload
as successive blocks, each offset to a disjoint page range so every block
is a genuine cold execution. One runloop run then yields N independent
per-block timing samples (the first absorbs process/hugepage warmup).

See MONAD_RUNLOOP_TESTING.md.
"""

import os
from enum import StrEnum, auto
from typing import Iterator, List, SupportsBytes, Tuple

import pytest
from execution_testing import (
    EOA,
    Account,
    Address,
    Alloc,
    BenchmarkTestFiller,
    Block,
    Bytecode,
    Conditional,
    Environment,
    Op,
    Transaction,
    While,
    WhileGas,
)
from execution_testing.forks import MONAD_NINE, MONAD_TEN
from execution_testing.forks.helpers import Fork

from tests.monad_ten.mip8_pageified_storage.helpers import fresh_sstore_cold
from tests.monad_ten.mip8_pageified_storage.spec import Spec, ref_spec_8

REFERENCE_SPEC_GIT_PATH = ref_spec_8.git_path
REFERENCE_SPEC_VERSION = ref_spec_8.version

# Key/value type accepted by `deploy_contract`/`Account` storage args;
# pre-built dicts need this annotation (dict is invariant).
StorageDict = dict[
    str | int | bytes | SupportsBytes, str | int | bytes | SupportsBytes
]

SLOTS_PER_PAGE = Spec.SLOTS_PER_PAGE  # 128

# Every test takes its block gas budget from `gas_benchmark_value`, set
# by `--gas-benchmark-values` (in millions). The runloop stamps every
# monad block at 200M gas, so `--gas-benchmark-values 200` is the setting
# that times real blocks; smaller values shrink the same workloads.
TX_GAS_CAP = 30_000_000
# Fixed tx count per block, sized so a full 200M block splits into 7
# equal txs of ~28.57M each (near the 30M per-tx cap). A smaller budget
# keeps the 7 txs and shrinks each one's work.
FULL_BLOCK_TXS = 7
# Workload txs are EIP-1559 with a high max fee and a zero priority tip.
# The high max fee keeps them valid as each full block raises the base
# fee ~12.5%.
MAX_FEE_PER_GAS = 10**6
# `many_small` block shape: many txs, each still large enough to cover the
# per-tx reserve. The count adapts to the block budget (300 at the full
# 200M) so a smaller budget does not starve individual txs.
MANY_SMALL_TXS = 300
MANY_SMALL_MIN_TX_GAS = 200_000

# Emit this many copies of each workload as successive, page-disjoint
# blocks for repeat timing samples (see module docstring). perf_cycle.sh
# raises this; releases fill a single cold copy.
REPEATS = int(os.environ.get("MIP8_PERF_REPEATS", "1"))

# Upper bound on pre-populated storage slots per block (times REPEATS for
# the whole fixture's genesis).
PRE_SLOT_CAP = 65_536

# Gas of one While control step (JUMPDEST + JUMPI + the jump back).
# Deliberately above the real cost so sizing never over-commits a tx into
# an OOG; `_while_control_gas` measures the real cost and
# `_per_iter_gas` fails if this drifts out of MAX_CONTROL_SLACK of it.
# Every gas of slack here is workload the block does not do, so the bound
# is checked rather than trusted.
WHILE_CONTROL_GAS = 40
MAX_CONTROL_SLACK = 24
# Per-tx headroom: intrinsic + calldata + two cold marker SSTOREs + slack.
TX_RESERVE = 120_000

# Page/slot domains, chosen far apart so nothing collides. Each repeat r
# shifts a page domain by r * REPEAT_STRIDE, well above any single
# workload's page extent and far below the next domain.
REPEAT_STRIDE = 1 << 28
READ_DOMAIN = 1 << 40  # page-index base for reused (pre-populated) pages
FRESH_DOMAIN = 1 << 52  # page-index base for fresh-write pages
WARM_BASE = 1 << 70  # per-repeat slot re-read by sload_warm_repeat
MARKER_BASE = 1 << 200  # per-tx success marker slot = MARKER_BASE + global_idx
CKSUM_BASE = 1 << 220  # per-tx read-checksum slot = CKSUM_BASE + global_idx


# Loop condition shared by every workload: iterate while the counter is
# below the requested count. Shared so `_while_control_gas` measures the
# same control overhead the workloads pay.
def _loop_condition() -> Bytecode:
    """Bytecode: counter < count."""
    return Op.LT(Op.MLOAD(M_COUNTER), Op.MLOAD(M_COUNT))


# Memory layout inside the workload contract.
M_COUNTER = 0x00
M_CHECKSUM = 0x20
M_BASE = 0x40
M_COUNT = 0x60
M_GLOBAL = 0x80  # global tx index (marker/checksum slot)
M_PAGE = 0xA0  # sweep page-base scratch
M_LOCAL = 0xC0  # per-block tx index (grow write offset)

# Calldata layout: base | count | global_idx | halt | local_idx.
CD_BASE = 0x00
CD_COUNT = 0x20
CD_GLOBAL = 0x40
CD_HALT = 0x60
CD_LOCAL = 0x80

slot_code_worked = 0x1
value_code_worked = 0x1234


class StorageOp(StrEnum):
    """A storage-access pattern a workload applies to each page."""

    SLOAD_COLD_HIT = auto()
    """Cold SLOAD of an occupied slot (offset 0) on each page."""
    SLOAD_COLD_MISS = auto()
    """Cold SLOAD of an empty slot (offset 127) on an occupied page."""
    SLOAD_SWEEP_K = auto()
    """Cold-read every occupied slot (offsets 0..k-1) of each page."""
    SLOAD_SWEEP_PAGE = auto()
    """Cold-read all 128 slot offsets of each page, hits and misses."""
    SLOAD_WARM_REPEAT = auto()
    """One cold SLOAD of a slot, then repeated warm re-reads of it."""
    SSTORE_FRESH = auto()
    """SSTORE 0->1 into empty pages (fresh state growth)."""
    SSTORE_NOOP = auto()
    """SSTORE 1->1 on occupied pages (rewrite, no value change)."""
    SSTORE_GROW = auto()
    """SSTORE 0->1 on a new slot of an already-occupied page."""
    SSTORE_UPDATE = auto()
    """SSTORE 1->2 on an occupied slot (value change, no growth)."""
    SSTORE_CLEAR_KEEP = auto()
    """SSTORE 1->0 clearing one slot; the page keeps its other slots."""
    SSTORE_CLEAR_EMPTY = auto()
    """SSTORE 1->0 clearing a page's only slot, so the page is removed."""
    SLOAD_EMPTY_PAGE = auto()
    """Cold SLOAD of a slot on a never-populated, empty page."""


def _senders(pre: Alloc, distinct: bool = True) -> Iterator[EOA]:
    """
    Yield one sender per transaction.

    Consecutive nonces from one EOA conflict on that account, so a block
    whose transactions all share a sender carries a dependency chain that
    serialises it in a parallel executor — which would mask whatever the
    storage encoding does. Distinct senders remove that chain, leaving
    storage access as the only cross-transaction dependency. `distinct`
    stays a parameter where the sender chain is itself the subject.
    """
    if distinct:
        while True:
            yield pre.fund_eoa()
    shared = pre.fund_eoa()
    while True:
        yield shared


@pytest.mark.valid_from("MONAD_NINE")
def test_compute_loop(
    benchmark_test: BenchmarkTestFiller,
    pre: Alloc,
    gas_benchmark_value: int,
    fork: Fork,
) -> None:
    """
    Run a gas-bounded arithmetic loop, then write a success marker.

    A stack-neutral arithmetic body is repeated until the compute gas
    budget is nearly spent, reserving just enough for the trailing
    SSTORE. Pure stack arithmetic touches no storage, so the loop costs
    the same on both forks and the marker is the only state written.
    """
    senders = _senders(pre)
    budget = gas_benchmark_value // FULL_BLOCK_TXS

    body = Op.POP(Op.ADD(Op.MUL(Op.NUMBER, Op.GAS), Op.CALLVALUE))
    contract_address = pre.deploy_contract(
        WhileGas(body=body, fork=fork, extra_gas=fresh_sstore_cold(fork))
        + Op.SSTORE(slot_code_worked, value_code_worked)
    )

    blocks = [
        Block(
            txs=[
                Transaction(
                    to=contract_address,
                    sender=next(senders),
                    gas_limit=budget,
                    max_fee_per_gas=MAX_FEE_PER_GAS,
                    max_priority_fee_per_gas=0,
                )
                for _ in range(FULL_BLOCK_TXS)
            ],
        ),
    ]

    benchmark_test(
        pre=pre,
        blocks=blocks,
        post={
            contract_address: Account(
                storage={slot_code_worked: value_code_worked}
            ),
        },
        env=Environment(gas_limit=gas_benchmark_value),
        gas_benchmark_value=gas_benchmark_value,
        skip_gas_used_validation=True,
    )


def _page_index() -> Bytecode:
    """Bytecode: base + i (base at M_BASE, i at M_COUNTER)."""
    return Op.ADD(Op.MLOAD(M_BASE), Op.MLOAD(M_COUNTER))


def _slot(offset: int | Bytecode) -> Bytecode:
    """Bytecode: (page_index << 7) + offset."""
    return Op.ADD(offset, Op.SHL(7, _page_index()))


def _read_accum(slot: Bytecode | int, *, warm: bool) -> Bytecode:
    """Accumulate SLOAD(slot) into the checksum word."""
    load = Op.SLOAD(slot, key_warm=warm, page_load_warm=warm)
    return Op.MSTORE(M_CHECKSUM, Op.ADD(Op.MLOAD(M_CHECKSUM), load))


def _sstore(
    slot: Bytecode,
    value: int | Bytecode,
    *,
    original: int,
    current: int,
    new: int,
    growth: int,
) -> Bytecode:
    """
    Build a cold-page SSTORE(slot, value).

    `value` is what is written at run time; `original`/`current`/`new`/
    `growth` describe the slot transition for gas sizing only. `original`
    (the slot's value at tx start) is required so a nonzero-to-nonzero or
    nonzero-to-zero reset is priced as a reset on MONAD_NINE, not a no-op.
    """
    return Op.SSTORE(
        slot,
        value,
        key_warm=False,
        page_load_warm=False,
        page_write_warm=False,
        original_value=original,
        current_value=current,
        new_value=new,
        current_state_growth=growth,
        net_state_growth=growth,
    )


def _body(op: StorageOp, k: int) -> Bytecode:
    """Assemble the per-iteration loop body for an operation."""
    inc = Op.MSTORE(M_COUNTER, Op.ADD(Op.MLOAD(M_COUNTER), 1))
    if op is StorageOp.SLOAD_COLD_HIT:
        return _read_accum(_slot(0), warm=False) + inc
    if op is StorageOp.SLOAD_COLD_MISS:
        return _read_accum(_slot(SLOTS_PER_PAGE - 1), warm=False) + inc
    if op is StorageOp.SLOAD_WARM_REPEAT:
        # The slot is passed in calldata (M_BASE) so each block re-reads a
        # distinct slot; cold on the first iteration, warm thereafter.
        return _read_accum(Op.MLOAD(M_BASE), warm=True) + inc
    if op is StorageOp.SLOAD_SWEEP_K:
        code = Op.MSTORE(M_PAGE, Op.SHL(7, _page_index()))
        for j in range(k):
            code += _read_accum(Op.ADD(Op.MLOAD(M_PAGE), j), warm=False)
        return code + inc
    if op is StorageOp.SLOAD_SWEEP_PAGE:
        code = Op.MSTORE(M_PAGE, Op.SHL(7, _page_index()))
        for j in range(SLOTS_PER_PAGE):
            code += _read_accum(Op.ADD(Op.MLOAD(M_PAGE), j), warm=False)
        return code + inc
    if op is StorageOp.SSTORE_FRESH:
        return (
            _sstore(_slot(0), 1, original=0, current=0, new=1, growth=0) + inc
        )
    if op is StorageOp.SSTORE_NOOP:
        return (
            _sstore(_slot(0), 1, original=1, current=1, new=1, growth=0) + inc
        )
    if op is StorageOp.SSTORE_GROW:
        offset = Op.ADD(k, Op.MLOAD(M_LOCAL))
        return (
            _sstore(
                _slot(offset),
                1,
                original=0,
                current=0,
                new=1,
                growth=k,
            )
            + inc
        )
    if op is StorageOp.SSTORE_UPDATE:
        # Each tx writes a new nonzero value (2 + its index), so every tx
        # is a genuine value change on the still-occupied slot 0.
        value = Op.ADD(2, Op.MLOAD(M_LOCAL))
        return (
            _sstore(_slot(0), value, original=1, current=1, new=2, growth=0)
            + inc
        )
    if op is StorageOp.SSTORE_CLEAR_KEEP:
        # Tx t clears slot offset t; with k > FULL_BLOCK_TXS the page keeps
        # its higher slots, so each clear is a genuine 1->0 on a live page.
        offset = Op.MLOAD(M_LOCAL)
        return (
            _sstore(
                _slot(offset),
                0,
                original=1,
                current=1,
                new=0,
                growth=0,
            )
            + inc
        )
    if op is StorageOp.SSTORE_CLEAR_EMPTY:
        return (
            _sstore(_slot(0), 0, original=1, current=1, new=0, growth=0) + inc
        )
    if op is StorageOp.SLOAD_EMPTY_PAGE:
        return _read_accum(_slot(0), warm=False) + inc
    raise ValueError(f"unknown op {op}")


def _is_read(op: StorageOp) -> bool:
    """Return whether the operation reads (and writes a checksum)."""
    return op.value.startswith("sload")


def _contract(op: StorageOp, k: int) -> Bytecode:
    """
    Build the workload contract (fixed size, independent of REPEATS).

    Reads calldata (base, count, global_idx, halt, local_idx), runs
    `count` iterations of the op's body, writes a success marker (and, for
    reads, a checksum) keyed by the global index, then STOPs — or, if the
    halt flag is set, executes INVALID so the whole transaction reverts.
    """
    init = (
        Op.MSTORE(M_BASE, Op.CALLDATALOAD(CD_BASE))
        + Op.MSTORE(M_COUNT, Op.CALLDATALOAD(CD_COUNT))
        + Op.MSTORE(M_GLOBAL, Op.CALLDATALOAD(CD_GLOBAL))
        + Op.MSTORE(M_LOCAL, Op.CALLDATALOAD(CD_LOCAL))
        + Op.MSTORE(M_COUNTER, 0)
    )
    loop = While(
        body=_body(op, k),
        condition=_loop_condition(),
    )
    markers = Op.SSTORE(
        Op.ADD(MARKER_BASE, Op.MLOAD(M_GLOBAL)), value_code_worked
    )
    if _is_read(op):
        markers += Op.SSTORE(
            Op.ADD(CKSUM_BASE, Op.MLOAD(M_GLOBAL)), Op.MLOAD(M_CHECKSUM)
        )
    tail = Conditional(
        condition=Op.CALLDATALOAD(CD_HALT),
        if_true=Op.INVALID,
        if_false=Op.STOP,
    )
    return init + loop + markers + tail


def _calldata(
    base: int, count: int, global_idx: int, halt: int, local_idx: int = 0
) -> bytes:
    """Encode the five-word calldata for one transaction."""
    return b"".join(
        v.to_bytes(32, "big")
        for v in (base, count, global_idx, halt, local_idx)
    )


def _while_control_gas(fork: Fork) -> int:
    """
    Gas the `While` wrapper adds around one body iteration.

    Taken from the framework's own accounting rather than counted by hand,
    so a change to how `While` is assembled surfaces as a failed bound in
    `_per_iter_gas` instead of as quietly smaller workloads.
    """
    body = Op.MSTORE(M_COUNTER, Op.ADD(Op.MLOAD(M_COUNTER), 1))
    condition = _loop_condition()
    loop = While(body=body, condition=condition)
    return loop.gas_cost(fork) - body.gas_cost(fork) - condition.gas_cost(fork)


def _per_iter_gas(op: StorageOp, k: int) -> int:
    """Gas for one loop iteration, sized to the costlier of both forks."""
    body = _body(op, k)
    per_op = max(body.gas_cost(MONAD_NINE), body.gas_cost(MONAD_TEN))
    control = max(
        _while_control_gas(MONAD_NINE), _while_control_gas(MONAD_TEN)
    )
    assert control <= WHILE_CONTROL_GAS <= control + MAX_CONTROL_SLACK, (
        f"WHILE_CONTROL_GAS is {WHILE_CONTROL_GAS} but a While control step "
        f"actually costs {control}: below it every workload risks an OOG, "
        f"more than {MAX_CONTROL_SLACK} above it and every block is sized "
        "for measurably less work than its gas budget allows"
    )
    return per_op + WHILE_CONTROL_GAS


def _iterations(op: StorageOp, k: int, budget: int) -> int:
    """Loop iterations that fit `budget` gas, leaving tx headroom."""
    return max(1, (budget - TX_RESERVE) // _per_iter_gas(op, k))


def _assert_tx_within_budget(per_iter: int, count: int, budget: int) -> None:
    """
    Fail if a workload transaction plans more gas than its budget.

    Catches a `count` that was not derived from `budget` and a `TX_RESERVE`
    that outgrew a small budget; either would put more gas in the block
    than the runloop allows.

    How tightly a gas-bound tx *fills* its budget is not checked here — it
    follows from `_per_iter_gas`, whose estimate is bounded against the
    framework's own accounting at the point it is built.
    """
    planned = count * per_iter + TX_RESERVE
    assert planned <= budget, (
        f"workload tx plans {planned} gas but its budget is {budget}; "
        "the block would exceed the gas the runloop allows"
    )


def _occupied_prestate(domain: int, pages: int, k: int) -> StorageDict:
    """Pre-populate `pages` pages (from `domain`) with `k` slots each."""
    storage: StorageDict = {}
    for i in range(pages):
        base_slot = (domain + i) << 7
        for j in range(k):
            storage[base_slot + j] = 1
    return storage


def _repeat_domains(repeat: int) -> Tuple[int, int, int]:
    """Return (read_domain, fresh_domain, warm_slot) for a repeat index."""
    shift = repeat * REPEAT_STRIDE
    return READ_DOMAIN + shift, FRESH_DOMAIN + shift, WARM_BASE + repeat


# (op, page-occupancy k values). cold_miss/grow keep a zero slot free
# (k < SLOTS_PER_PAGE, and grow writes k..k+FULL_BLOCK_TXS-1). clear_keep
# needs k > FULL_BLOCK_TXS so a page still has slots after the block
# clears offsets 0..FULL_BLOCK_TXS-1. clear_empty uses single-slot pages
# (k=1) that vanish when cleared; empty_page reads never-set pages
# (k=0). sweep_k reads only the k occupied slots; sweep_page reads all
# 128 offsets — k hits + (128-k) misses, never-set pages at k=0.
_PAGE_OP_KS = [
    (StorageOp.SLOAD_COLD_HIT, [1, 16, 128]),
    (StorageOp.SLOAD_COLD_MISS, [1, 16, 64]),
    (StorageOp.SLOAD_SWEEP_K, [2, 16, 128]),
    (StorageOp.SLOAD_SWEEP_PAGE, [0, 1, 64]),
    (StorageOp.SSTORE_NOOP, [1, 16, 128]),
    (StorageOp.SSTORE_GROW, [1, 16, 64]),
    (StorageOp.SSTORE_UPDATE, [1, 16, 128]),
    (StorageOp.SSTORE_CLEAR_KEEP, [8, 16, 128]),
    (StorageOp.SSTORE_CLEAR_EMPTY, [1]),
    (StorageOp.SLOAD_EMPTY_PAGE, [0]),
    (StorageOp.SLOAD_WARM_REPEAT, [1]),
    (StorageOp.SSTORE_FRESH, [0]),
]

_PAGE_OP_PARAMS = [
    pytest.param(op, k, id=f"{op.value}-k{k}")
    for op, ks in _PAGE_OP_KS
    for k in ks
]


@pytest.mark.parametrize("op, k", _PAGE_OP_PARAMS)
@pytest.mark.valid_from("MONAD_NINE")
def test_page_ops(
    benchmark_test: BenchmarkTestFiller,
    pre: Alloc,
    gas_benchmark_value: int,
    op: StorageOp,
    k: int,
) -> None:
    """
    Fill a block with one storage-op pattern over `pages` pages.

    Occupied-page ops (reads, no-op, growing/updating writes, and the
    keep-a-slot clear) reuse a per-block pre-populated page pool: every
    transaction re-touches the same pages, paying cold access each time
    (per-tx warmth resets). Fresh writes and the page-emptying clear give
    each transaction its own disjoint page range; the emptying clear
    pre-populates one slot per page so each tx removes whole pages. The
    page count is bounded by the tx gas budget and, for pre-populated ops,
    by PRE_SLOT_CAP // k — so high-occupancy cold-read blocks are pre-state
    bound and run below 200M by design. With REPEATS > 1 each repeat block
    is offset to a fresh page range.
    """
    budget = gas_benchmark_value // FULL_BLOCK_TXS
    pages = _iterations(op, k, budget)

    per_tx_pages = op is StorageOp.SSTORE_CLEAR_EMPTY
    occupied = op not in (
        StorageOp.SSTORE_FRESH,
        StorageOp.SLOAD_WARM_REPEAT,
        StorageOp.SSTORE_CLEAR_EMPTY,
    )
    if occupied:
        pages = min(pages, PRE_SLOT_CAP // max(k, 1))
    elif per_tx_pages:
        pages = min(pages, PRE_SLOT_CAP // FULL_BLOCK_TXS)
    _assert_tx_within_budget(_per_iter_gas(op, k), pages, budget)

    senders = _senders(pre)

    prestate: StorageDict = {}
    for r in range(REPEATS):
        read_dom, fresh_dom, warm_slot = _repeat_domains(r)
        if op is StorageOp.SLOAD_WARM_REPEAT:
            prestate[warm_slot] = 1
        elif per_tx_pages:
            for t in range(FULL_BLOCK_TXS):
                dom = fresh_dom + t * pages
                prestate.update(_occupied_prestate(dom, pages, 1))
        elif occupied:
            prestate.update(_occupied_prestate(read_dom, pages, k))
    contract = pre.deploy_contract(_contract(op, k), storage=prestate)

    blocks = []
    expected: StorageDict = dict(prestate)
    global_idx = 0
    for r in range(REPEATS):
        read_dom, fresh_dom, warm_slot = _repeat_domains(r)
        txs = []
        for t in range(FULL_BLOCK_TXS):
            if op in (StorageOp.SSTORE_FRESH, StorageOp.SSTORE_CLEAR_EMPTY):
                base = fresh_dom + t * pages
            elif op is StorageOp.SLOAD_WARM_REPEAT:
                base = warm_slot
            else:
                base = read_dom
            txs.append(
                Transaction(
                    to=contract,
                    sender=next(senders),
                    gas_limit=budget,
                    max_fee_per_gas=MAX_FEE_PER_GAS,
                    max_priority_fee_per_gas=0,
                    data=_calldata(base, pages, global_idx, 0, t),
                )
            )
            expected[MARKER_BASE + global_idx] = value_code_worked
            if _is_read(op):
                if op in (
                    StorageOp.SLOAD_SWEEP_K,
                    StorageOp.SLOAD_SWEEP_PAGE,
                ):
                    checksum = pages * k
                elif op in (
                    StorageOp.SLOAD_COLD_MISS,
                    StorageOp.SLOAD_EMPTY_PAGE,
                ):
                    checksum = 0
                else:  # cold_hit, warm_repeat: each read returns 1
                    checksum = pages
                if checksum:
                    expected[CKSUM_BASE + global_idx] = checksum
            global_idx += 1

        if op is StorageOp.SSTORE_GROW:
            for i in range(pages):
                base_slot = (read_dom + i) << 7
                for t in range(FULL_BLOCK_TXS):
                    expected[base_slot + (k + t)] = 1
        elif op is StorageOp.SSTORE_FRESH:
            for j in range(FULL_BLOCK_TXS * pages):
                expected[(fresh_dom + j) << 7] = 1
        elif op is StorageOp.SSTORE_UPDATE:
            # Slot 0 is written once per tx (1->2->...); it ends at
            # 1 + FULL_BLOCK_TXS. Other occupied slots stay 1.
            for i in range(pages):
                expected[(read_dom + i) << 7] = 1 + FULL_BLOCK_TXS
        elif op is StorageOp.SSTORE_CLEAR_KEEP:
            for i in range(pages):
                base_slot = (read_dom + i) << 7
                for t in range(FULL_BLOCK_TXS):
                    expected[base_slot + t] = 0
        elif op is StorageOp.SSTORE_CLEAR_EMPTY:
            for t in range(FULL_BLOCK_TXS):
                for i in range(pages):
                    expected[(fresh_dom + (t * pages + i)) << 7] = 0

        blocks.append(Block(txs=txs))

    benchmark_test(
        pre=pre,
        blocks=blocks,
        post={contract: Account(storage=expected)},
        env=Environment(gas_limit=gas_benchmark_value),
        gas_benchmark_value=gas_benchmark_value,
        skip_gas_used_validation=True,
    )


_SPREAD_PARAMS = [
    pytest.param(m, n, id=f"m{m}_n{n}")
    for m, n in [
        (1, 1),
        (4, 1),
        (16, 1),
        (64, 1),
        (256, 1),
        (1024, 1),
        (4096, 1),
        (4096, 8),
        (4096, 64),
        (4096, 512),
    ]
]


@pytest.mark.parametrize("m, n", _SPREAD_PARAMS)
@pytest.mark.valid_from("MONAD_NINE")
def test_page_spread(
    benchmark_test: BenchmarkTestFiller,
    pre: Alloc,
    gas_benchmark_value: int,
    m: int,
    n: int,
) -> None:
    """
    Fresh-write `m` distinct pages spread evenly over `n` contracts.

    Each contract writes `m // n` fresh pages, split into as many
    transactions as the per-tx gas cap requires (so large per-contract
    shares become several txs, and many contracts become many small
    txs). Isolates the effect of write distribution across accounts. Each
    repeat block writes a fresh page range.

    Work is sized by `m` and `n`, not by the gas budget, so a high fan-out
    (n=512) stays large however small the budget is. The budget only has
    to be large enough to hold the resulting block, which the sizing
    check below enforces.
    """
    per_iter = _per_iter_gas(StorageOp.SSTORE_FRESH, 0)
    max_per_tx = max(1, (TX_GAS_CAP - TX_RESERVE) // per_iter)
    pages_per_contract = m // n

    txs_per_contract = -(-pages_per_contract // max_per_tx)
    planned = n * (
        pages_per_contract * per_iter + txs_per_contract * TX_RESERVE
    )
    assert planned <= gas_benchmark_value, (
        f"m={m} n={n} needs {planned} gas but the budget is "
        f"{gas_benchmark_value}; raise --gas-benchmark-values or drop the case"
    )

    senders = _senders(pre)
    contracts: List[Address] = [
        pre.deploy_contract(_contract(StorageOp.SSTORE_FRESH, 0))
        for _ in range(n)
    ]

    blocks = []
    contract_storage: dict[Address, StorageDict] = {c: {} for c in contracts}
    global_idx = 0
    for r in range(REPEATS):
        _, fresh_dom, _ = _repeat_domains(r)
        txs = []
        for contract in contracts:
            done = 0
            while done < pages_per_contract:
                count = min(max_per_tx, pages_per_contract - done)
                txs.append(
                    Transaction(
                        to=contract,
                        sender=next(senders),
                        gas_limit=count * per_iter + TX_RESERVE,
                        max_fee_per_gas=MAX_FEE_PER_GAS,
                        max_priority_fee_per_gas=0,
                        data=_calldata(fresh_dom + done, count, global_idx, 0),
                    )
                )
                contract_storage[contract][MARKER_BASE + global_idx] = (
                    value_code_worked
                )
                global_idx += 1
                done += count
            for j in range(pages_per_contract):
                slot = (fresh_dom + j) << 7
                contract_storage[contract][slot] = 1
        blocks.append(Block(txs=txs))

    benchmark_test(
        pre=pre,
        blocks=blocks,
        post={
            contract: Account(storage=storage)
            for contract, storage in contract_storage.items()
        },
        env=Environment(gas_limit=gas_benchmark_value),
        gas_benchmark_value=gas_benchmark_value,
        skip_gas_used_validation=True,
    )


_SHAPE_PARAMS = [
    pytest.param(
        op,
        k,
        shape,
        distinct,
        id=f"{op.value}-k{k}-{shape}-"
        f"{'distinct' if distinct else 'shared'}_senders",
    )
    for op, k in [(StorageOp.SSTORE_FRESH, 0), (StorageOp.SLOAD_COLD_HIT, 8)]
    for shape in ("few_big", "many_small")
    for distinct in (True, False)
]


@pytest.mark.parametrize("op, k, shape, distinct_senders", _SHAPE_PARAMS)
@pytest.mark.valid_from("MONAD_NINE")
def test_block_shape(
    benchmark_test: BenchmarkTestFiller,
    pre: Alloc,
    gas_benchmark_value: int,
    op: StorageOp,
    k: int,
    shape: str,
    distinct_senders: bool,
) -> None:
    """
    Same total work packed as a few big txs vs many small txs.

    `few_big` uses 7 gas-cap-sized transactions; `many_small` uses many
    (300 at the full block budget) smaller ones. Reads reuse one
    pre-populated page pool (each tx a fresh cold pass); fresh writes give
    each tx a disjoint range. Each repeat block is offset to a fresh range.

    `distinct_senders` decides whether the block also carries a sender
    nonce chain: shared senders serialise the transactions independently
    of their storage access, which bounds how much the packing can matter.
    """
    if shape == "few_big":
        num_txs = FULL_BLOCK_TXS
    else:
        num_txs = min(
            MANY_SMALL_TXS,
            max(1, gas_benchmark_value // MANY_SMALL_MIN_TX_GAS),
        )
    budget = min(TX_GAS_CAP, gas_benchmark_value // num_txs)
    count = _iterations(op, k, budget)

    occupied = op is not StorageOp.SSTORE_FRESH
    if occupied:
        count = min(count, PRE_SLOT_CAP // max(k, 1))
    _assert_tx_within_budget(_per_iter_gas(op, k), count, budget)

    senders = _senders(pre, distinct_senders)
    prestate: StorageDict = {}
    for r in range(REPEATS):
        read_dom, _, _ = _repeat_domains(r)
        if occupied:
            prestate.update(_occupied_prestate(read_dom, count, k))
    contract = pre.deploy_contract(_contract(op, k), storage=prestate)

    blocks = []
    expected: StorageDict = dict(prestate)
    global_idx = 0
    for r in range(REPEATS):
        read_dom, fresh_dom, _ = _repeat_domains(r)
        txs = []
        for t in range(num_txs):
            base = read_dom if occupied else fresh_dom + t * count
            txs.append(
                Transaction(
                    to=contract,
                    sender=next(senders),
                    gas_limit=budget,
                    max_fee_per_gas=MAX_FEE_PER_GAS,
                    max_priority_fee_per_gas=0,
                    data=_calldata(base, count, global_idx, 0, t),
                )
            )
            expected[MARKER_BASE + global_idx] = value_code_worked
            if occupied:  # cold_hit reads each return 1
                expected[CKSUM_BASE + global_idx] = count
            global_idx += 1
        if op is StorageOp.SSTORE_FRESH:
            for j in range(num_txs * count):
                expected[(fresh_dom + j) << 7] = 1
        blocks.append(Block(txs=txs))

    benchmark_test(
        pre=pre,
        blocks=blocks,
        post={contract: Account(storage=expected)},
        env=Environment(gas_limit=gas_benchmark_value),
        gas_benchmark_value=gas_benchmark_value,
        skip_gas_used_validation=True,
    )


@pytest.mark.parametrize("mode", ["success", "halt", "mix"])
@pytest.mark.valid_from("MONAD_NINE")
def test_tx_halt(
    benchmark_test: BenchmarkTestFiller,
    pre: Alloc,
    gas_benchmark_value: int,
    mode: str,
) -> None:
    """
    Fresh-write block whose transactions succeed, all halt, or alternate.

    A halting tx runs INVALID after its writes and marker, so all of its
    state reverts (and it consumes its full gas limit — a perfectly full
    block). Only successful txs leave pages and markers behind, giving a
    strong post-state oracle for the mixed case. Each repeat block is
    offset to a fresh range.
    """
    budget = gas_benchmark_value // FULL_BLOCK_TXS
    count = _iterations(StorageOp.SSTORE_FRESH, 0, budget)
    _assert_tx_within_budget(
        _per_iter_gas(StorageOp.SSTORE_FRESH, 0), count, budget
    )

    senders = _senders(pre)
    contract = pre.deploy_contract(_contract(StorageOp.SSTORE_FRESH, 0))

    blocks = []
    expected: StorageDict = {}
    global_idx = 0
    for r in range(REPEATS):
        _, fresh_dom, _ = _repeat_domains(r)
        txs = []
        for t in range(FULL_BLOCK_TXS):
            halt = mode == "halt" or (mode == "mix" and t % 2 == 1)
            txs.append(
                Transaction(
                    to=contract,
                    sender=next(senders),
                    gas_limit=budget,
                    max_fee_per_gas=MAX_FEE_PER_GAS,
                    max_priority_fee_per_gas=0,
                    data=_calldata(
                        fresh_dom + t * count,
                        count,
                        global_idx,
                        int(halt),
                        t,
                    ),
                )
            )
            if not halt:
                for i in range(count):
                    page = fresh_dom + (t * count + i)
                    expected[page << 7] = 1
                expected[MARKER_BASE + global_idx] = value_code_worked
            global_idx += 1
        blocks.append(Block(txs=txs))

    benchmark_test(
        pre=pre,
        blocks=blocks,
        post={contract: Account(storage=expected)},
        env=Environment(gas_limit=gas_benchmark_value),
        gas_benchmark_value=gas_benchmark_value,
        skip_gas_used_validation=True,
    )


RAND_CONTRACTS = 8  # pool of contracts, spread across the block's txs
RAND_BASE = 1  # first page key of the read set
RAND_STRIDE = 1 << 10  # > max slots, so per-tx page sets are disjoint
M_CHAIN = 0xE0  # chained-sload current slot (memory scratch)
SERIAL_BASE = 1 << 40
SERIAL_REPEAT_STRIDE = 1 << 24  # >> per-tx slot count
CHAIN_BASE = 1 << 30
CHAIN_REPEAT_STRIDE = 1 << 20  # >> ring length


def _size_count(body: Bytecode, budget: int) -> int:
    """Loop iterations of `body` that fit `budget`, sized to both forks."""
    per_iter = (
        max(body.gas_cost(MONAD_NINE), body.gas_cost(MONAD_TEN))
        + WHILE_CONTROL_GAS
    )
    count = max(1, (budget - TX_RESERVE) // per_iter)
    _assert_tx_within_budget(per_iter, count, budget)
    return count


def _rand_sload_body(slots: int) -> Bytecode:
    """One iteration: cold-SLOAD one distinct page from the read set."""
    idx = Op.AND(Op.MLOAD(M_COUNTER), slots - 1)
    slot = Op.SHL(7, Op.ADD(Op.MLOAD(M_BASE), idx))
    read = Op.SLOAD(slot, key_warm=False, page_load_warm=False)
    return Op.MSTORE(
        M_CHECKSUM, Op.ADD(Op.MLOAD(M_CHECKSUM), read)
    ) + Op.MSTORE(M_COUNTER, Op.ADD(Op.MLOAD(M_COUNTER), 1))


def _rand_sload_contract(slots: int) -> Bytecode:
    """
    SLOAD `slots` distinct pages in a cycle, `count` times, then write a
    success marker and the read checksum.
    """
    init = (
        Op.MSTORE(M_BASE, Op.CALLDATALOAD(CD_BASE))  # base page key
        + Op.MSTORE(M_COUNT, Op.CALLDATALOAD(CD_COUNT))
        + Op.MSTORE(M_GLOBAL, Op.CALLDATALOAD(CD_GLOBAL))
        + Op.MSTORE(M_COUNTER, 0)
        + Op.MSTORE(M_CHECKSUM, 0)
    )
    loop = While(
        body=_rand_sload_body(slots),
        condition=_loop_condition(),
    )
    tail = (
        Op.SSTORE(Op.ADD(MARKER_BASE, Op.MLOAD(M_GLOBAL)), value_code_worked)
        + Op.SSTORE(
            Op.ADD(CKSUM_BASE, Op.MLOAD(M_GLOBAL)), Op.MLOAD(M_CHECKSUM)
        )
        + Op.STOP
    )
    return init + loop + tail


_RAND_PARAMS = [
    pytest.param(slots, k, id=f"slots{slots}-k{k}")
    for slots in (1, 16, 128)
    for k in (0, 1)
]


@pytest.mark.parametrize("slots, k", _RAND_PARAMS)
@pytest.mark.valid_from("MONAD_NINE")
def test_random_sload(
    benchmark_test: BenchmarkTestFiller,
    pre: Alloc,
    gas_benchmark_value: int,
    slots: int,
    k: int,
) -> None:
    """
    Cold-SLOAD a set of `slots` distinct pages, from a pool of contracts
    spread across the block's txs. `k` is the page occupancy of each read
    page: 1 (slot present, reads 1) or 0 (empty page, reads 0). Cycling a
    small set means only the first pass is cold, so blocks are
    gas-underfull by design; each repeat uses a fresh page range.
    """
    budget = gas_benchmark_value // FULL_BLOCK_TXS
    count = _size_count(_rand_sload_body(slots), budget)
    code = _rand_sload_contract(slots)
    senders = _senders(pre)

    # Plan tx -> (contract index, base page key) and genesis slots.
    plan: List[Tuple[int, int]] = []
    genesis: List[StorageDict] = [{} for _ in range(RAND_CONTRACTS)]
    g = 0
    for _r in range(REPEATS):
        for _t in range(FULL_BLOCK_TXS):
            ci = g % RAND_CONTRACTS
            base = RAND_BASE + g * RAND_STRIDE
            if k == 1:
                for idx in range(slots):
                    genesis[ci][(base + idx) << 7] = 1
            plan.append((ci, base))
            g += 1

    contracts: List[Address] = [
        pre.deploy_contract(code, storage=genesis[i])
        for i in range(RAND_CONTRACTS)
    ]

    post: dict[Address, StorageDict] = {
        contracts[i]: dict(genesis[i]) for i in range(RAND_CONTRACTS)
    }
    blocks = []
    g = 0
    for _r in range(REPEATS):
        txs = []
        for _t in range(FULL_BLOCK_TXS):
            ci, base = plan[g]
            contract = contracts[ci]
            txs.append(
                Transaction(
                    to=contract,
                    sender=next(senders),
                    gas_limit=budget,
                    max_fee_per_gas=MAX_FEE_PER_GAS,
                    max_priority_fee_per_gas=0,
                    data=_calldata(base, count, g, 0),
                )
            )
            post[contract][MARKER_BASE + g] = value_code_worked
            if k == 1:
                post[contract][CKSUM_BASE + g] = count
            g += 1
        blocks.append(Block(txs=txs))

    benchmark_test(
        pre=pre,
        blocks=blocks,
        post={c: Account(storage=s) for c, s in post.items()},
        env=Environment(gas_limit=gas_benchmark_value),
        gas_benchmark_value=gas_benchmark_value,
        skip_gas_used_validation=True,
    )


@pytest.mark.valid_from("MONAD_NINE")
def test_bad_block_serial(
    benchmark_test: BenchmarkTestFiller,
    pre: Alloc,
    gas_benchmark_value: int,
) -> None:
    """
    Adversarial block forcing serial execution: every tx SLOADs and
    SSTOREs (increments) the same contiguous slot sequence, so the txs
    conflict and cannot run in parallel; each shared slot ends at
    FULL_BLOCK_TXS. Each repeat uses a fresh slot range.

    Senders are distinct, so the storage conflict is the only thing
    serialising the block.
    """
    budget = gas_benchmark_value // FULL_BLOCK_TXS
    senders = _senders(pre)

    # Per-iteration body: increment the counter-th shared slot.
    sl = Op.ADD(Op.MLOAD(M_BASE), Op.MLOAD(M_COUNTER))
    read = Op.SLOAD(sl, key_warm=False, page_load_warm=False)
    body = _sstore(
        sl, Op.ADD(read, 1), original=0, current=0, new=1, growth=0
    ) + Op.MSTORE(M_COUNTER, Op.ADD(Op.MLOAD(M_COUNTER), 1))
    count = _size_count(body, budget)

    contract = pre.deploy_contract(
        Op.MSTORE(M_BASE, Op.CALLDATALOAD(CD_BASE))
        + Op.MSTORE(M_COUNT, Op.CALLDATALOAD(CD_COUNT))
        + Op.MSTORE(M_GLOBAL, Op.CALLDATALOAD(CD_GLOBAL))
        + Op.MSTORE(M_COUNTER, 0)
        + While(
            body=body,
            condition=_loop_condition(),
        )
        + Op.SSTORE(Op.ADD(MARKER_BASE, Op.MLOAD(M_GLOBAL)), value_code_worked)
        + Op.STOP
    )

    blocks = []
    expected: StorageDict = {}
    g = 0
    for r in range(REPEATS):
        base = SERIAL_BASE + r * SERIAL_REPEAT_STRIDE
        txs = []
        for _t in range(FULL_BLOCK_TXS):
            txs.append(
                Transaction(
                    to=contract,
                    sender=next(senders),
                    gas_limit=budget,
                    max_fee_per_gas=MAX_FEE_PER_GAS,
                    max_priority_fee_per_gas=0,
                    data=_calldata(base, count, g, 0),
                )
            )
            expected[MARKER_BASE + g] = value_code_worked
            g += 1
        for i in range(count):
            expected[base + i] = FULL_BLOCK_TXS
        blocks.append(Block(txs=txs))

    benchmark_test(
        pre=pre,
        blocks=blocks,
        post={contract: Account(storage=expected)},
        env=Environment(gas_limit=gas_benchmark_value),
        gas_benchmark_value=gas_benchmark_value,
        skip_gas_used_validation=True,
    )


@pytest.mark.valid_from("MONAD_NINE")
def test_bad_block_chained(
    benchmark_test: BenchmarkTestFiller,
    pre: Alloc,
    gas_benchmark_value: int,
) -> None:
    """
    Adversarial block with a data-dependent SLOAD chain: each SLOAD
    returns the next SLOAD's slot (SLOAD(SLOAD(...(base)))), following a
    pre-built ring of distinct pages, so the reads serialise within a tx
    and each hop is a random disk position (pointer chase). Each repeat
    uses a fresh ring.
    """
    budget = gas_benchmark_value // FULL_BLOCK_TXS
    senders = _senders(pre)

    # Per-iteration body: SLOAD current slot; its value is the next slot.
    nxt = Op.SLOAD(Op.MLOAD(M_CHAIN), key_warm=False, page_load_warm=False)
    body = Op.MSTORE(M_CHAIN, nxt) + Op.MSTORE(
        M_COUNTER, Op.ADD(Op.MLOAD(M_COUNTER), 1)
    )
    count = _size_count(body, budget)

    code = (
        Op.MSTORE(M_BASE, Op.CALLDATALOAD(CD_BASE))  # base slot
        + Op.MSTORE(M_COUNT, Op.CALLDATALOAD(CD_COUNT))
        + Op.MSTORE(M_GLOBAL, Op.CALLDATALOAD(CD_GLOBAL))
        + Op.MSTORE(M_COUNTER, 0)
        + Op.MSTORE(M_CHAIN, Op.MLOAD(M_BASE))
        + While(
            body=body,
            condition=_loop_condition(),
        )
        + Op.SSTORE(Op.ADD(MARKER_BASE, Op.MLOAD(M_GLOBAL)), value_code_worked)
        + Op.STOP
    )

    genesis: StorageDict = {}
    rings: List[List[int]] = []
    for r in range(REPEATS):
        base = CHAIN_BASE + r * CHAIN_REPEAT_STRIDE
        ring = [(base + j) << 7 for j in range(count)]
        for j in range(count):
            genesis[ring[j]] = ring[(j + 1) % count]
        rings.append(ring)
    contract = pre.deploy_contract(code, storage=genesis)

    blocks = []
    expected: StorageDict = dict(genesis)
    g = 0
    for r in range(REPEATS):
        ring = rings[r]
        txs = []
        for t in range(FULL_BLOCK_TXS):
            txs.append(
                Transaction(
                    to=contract,
                    sender=next(senders),
                    gas_limit=budget,
                    max_fee_per_gas=MAX_FEE_PER_GAS,
                    max_priority_fee_per_gas=0,
                    data=_calldata(ring[t % count], count, g, 0),
                )
            )
            expected[MARKER_BASE + g] = value_code_worked
            g += 1
        blocks.append(Block(txs=txs))

    benchmark_test(
        pre=pre,
        blocks=blocks,
        post={contract: Account(storage=expected)},
        env=Environment(gas_limit=gas_benchmark_value),
        gas_benchmark_value=gas_benchmark_value,
        skip_gas_used_validation=True,
    )
