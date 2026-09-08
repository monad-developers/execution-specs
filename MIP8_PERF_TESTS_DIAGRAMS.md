# MIP-8 perf-regression tests: block diagrams

What each test case in
`tests/benchmark/stateful/mip8_pageified_storage/test_perf_regression.py`
actually does at the SLOAD/SSTORE level inside one block. Counts stay
symbolic — they follow from `--gas-benchmark-values` and the module's
sizing helpers. Diagrams show the `REPEATS = 1` block.

## Legend and shared machinery

Notation used in all diagrams:

```
R(o)      SLOAD  of offset o within a page (result added to a checksum)
W(o)=v    SSTORE of value v at offset o within a page
P         pages (loop iterations) per transaction
D         READ_DOMAIN  = 2^40   base page index of pre-populated pools
F         FRESH_DOMAIN = 2^52   base page index of fresh-write ranges
g         global tx index (unique across all blocks of a fixture)
t         local tx index within the block (0..6)
```

Storage addressing (MIP-8 page = 128 slots):

```
slot = (page_index << 7) + offset

page D + i:
  offset:  0     1    ...   k-1    k    ...   127
          ┌─────┬─────┬─────┬─────┬─────┬─────┬─────┐
          │  1  │  1  │ ... │  1  │  0  │ ... │  0  │
          └─────┴─────┴─────┴─────┴─────┴─────┴─────┘
           \__ k pre-populated slots __/ \__ empty __/
```

Block anatomy shared by `test_page_ops`, `test_block_shape` (few_big),
`test_tx_halt`, `test_random_sload` and the two `test_bad_block_*` tests
— one block, 7 equal txs, each from its own sender, to the same
workload contract (`test_random_sload` cycles a pool of 8 contracts
instead):

```
Block (gas limit = the benchmark budget)
┌──────────┬──────────┬──────────┬──────────┬──────────┬──────────┬──────────┐
│   tx 0   │   tx 1   │   tx 2   │   tx 3   │   tx 4   │   tx 5   │   tx 6   │
│ budget/7 │ budget/7 │ budget/7 │ budget/7 │ budget/7 │ budget/7 │ budget/7 │
└──────────┴──────────┴──────────┴──────────┴──────────┴──────────┴──────────┘
```

Per-tx warm/access sets reset between transactions, so every tx of a
block pays **cold** access again even when it re-touches the exact pages
tx 0 touched. This is what lets one pre-populated pool serve all 7 txs
as 7 independent cold passes.

Each tx also has its own sender. Consecutive nonces from one EOA are a
write-write conflict on that account, which would serialise the block
whatever its storage access looks like; distinct senders leave storage as
the only cross-tx dependency. `test_block_shape` parametrizes
`distinct_senders` so the cost of that chain stays measurable.

Every transaction runs the same loop contract: read calldata
`(base, count, g, halt, t)`, execute `count` iterations of the op body,
then finish with one or two bookkeeping SSTOREs on far-away pages:

```
tail of every tx:
  SSTORE(2^200 + g, 0x1234)      success marker          (cold, fresh)
  SSTORE(2^220 + g, checksum)    read ops only; a checksum of 0 still
                                 executes but writes 0 → no state trace
```

With `MIP8_PERF_REPEATS > 1` the whole block is emitted again with all
page domains shifted by `r * 2^28`, so every repeat block is a fully
cold copy. Diagrams below show the `r = 0` block.

---

## `test_compute_loop`

No parameters. The storage-free baseline: the same 7-tx full block as
above, each tx running a stack-arithmetic `WhileGas` loop.

```
Block (gas limit = the benchmark budget)
┌──────────────────────────────────────────────────┬─── ... ───┬──────────┐
│ tx 0 (budget/7 gas)                              │           │   tx 6   │
│   loop: POP(ADD(MUL(NUMBER, GAS), CALLVALUE))    │           │  (same)  │
│   ... repeated until gas nearly spent ...        │           │          │
│   SSTORE(slot 1, 0x1234)                         │           │          │
└──────────────────────────────────────────────────┴─── ... ───┴──────────┘
```

All 7 txs write the same marker slot, so only one slot is touched however
many run. As the suite's control, the loop is sized against a fixed fork,
which keeps the deployed bytecode byte-identical on both sides.

Only the final marker touches storage, so the block isolates pure
execution overhead from any MIP-8 effect.

---

## `test_page_ops`

One section per storage operation (`StorageOp`); within each, `k` (page
occupancy) varies. All variants use the 7-tx full block above. Per-tx
page counts:

| op                 | k values     | P bounded by                |
|--------------------|--------------|-----------------------------|
| sload_cold_hit     | {1,16,128}   | gas; pre-state at high k¹   |
| sload_cold_miss    | {1,16,64}    | gas; pre-state at high k¹   |
| sload_sweep_k      | {2,16,128}   | gas (k reads per iteration) |
| sload_sweep_page   | {0,1,64}     | gas (128 reads per iter)    |
| sload_warm_repeat  | {1}          | gas (one slot, re-read)     |
| sload_empty_page   | {0}          | gas                         |
| sstore_fresh       | {0}          | gas                         |
| sstore_noop        | {1,16,128}   | gas; pre-state at high k¹   |
| sstore_grow        | {1,16,64}    | gas                         |
| sstore_update      | {1,16,128}   | gas; pre-state at high k¹   |
| sstore_clear_keep  | {8,16,128}   | gas; pre-state at high k¹   |
| sstore_clear_empty | {1}          | pre-state                   |

¹ `PRE_SLOT_CAP` bounds the pre-populated slots per block, so at high k
the page count falls to `PRE_SLOT_CAP / k` and the block does less work
than its gas budget allows by design.

### Storage operation `sload_cold_hit`

Block-filling transactions cold-SLOAD one occupied slot on many pages.

Pre-state: pool of P pages at domain D, each with slots `0..k-1 = 1`.
Every tx makes one cold pass over the whole pool, reading offset 0
(always occupied).

```
pre-state pool (shared by all 7 txs)
             page D    page D+1   page D+2         page D+(P-1)
tx 0:        R(0)      R(0)       R(0)      ...    R(0)     all cold
tx 1:        R(0)      R(0)       R(0)      ...    R(0)     cold again
 ...                                                        (warmth reset)
tx 6:        R(0)      R(0)       R(0)      ...    R(0)

each read returns 1  →  checksum = P per tx
block total: 7 × P cold page reads of an existing, occupied slot
```

### Storage operation `sload_cold_miss`

Block-filling transactions cold-SLOAD an empty slot on many occupied pages.

Same shared pool as `cold_hit` (k ≤ 64 keeps the last slot empty), but
each read targets offset 127 — the page exists, the slot is zero.

```
page D + i:
  offset:  0 .. k-1        k .. 126   127
          [ 1 .. 1 ]      [ 0 .. 0 ] [ 0 ] ◄── R(127) per iteration
           occupied         empty      read misses (returns 0)

tx 0..6:  R(127) on every pool page, all cold
checksum = 0  →  the tail checksum SSTORE writes 0 (no state trace)
```

Measures "page found, slot not found" lookups, P cold reads per tx.

### Storage operation `sload_sweep_k`

Block-filling transactions cold-read all occupied slots of each page.

Each loop iteration cold-reads **every occupied slot** of one page,
offsets `0..k-1` in ascending order.

```
tx 0..6, iteration i (page D + i):
  offset:  0    1    2   ...  k-1 | k .. 127
          R(0) R(1) R(2) ... R(k-1)| untouched     k cold reads

per tx: P pages × k reads
checksum = P × k per tx
```

Isolates intra-page locality: same number-ish of cold reads as
`cold_hit`, but bunched k-per-page instead of 1-per-page.

### Storage operation `sload_sweep_page`

Block-filling transactions cold-read all 128 slot offsets of each page, hits and misses.

Each loop iteration reads offsets 0..127 of one page whose first k
slots are pre-populated: k hits plus 128−k misses. At k=0 the pages
were never populated and every read is a whole-page miss.

```
tx 0..6, iteration i (page D + i):
  offset:  0   ...  k-1 |  k   ...  127
          R(0) ... R(k-1)| R(k) ... R(127)     128 cold reads
           \_ k hits ___/ \_ 128-k misses _/

per tx: P pages × 128 reads;  checksum = P × k
(k=0: checksum 0 → the tail SSTORE writes 0, no state trace)
```

Fixed whole-page read volume with occupancy as the only variable —
`sload_sweep_k` scales the read count with k instead.

### Storage operation `sload_warm_repeat`

Block-filling transactions cold-SLOAD a slot of each page then warm-re-read it repeatedly.

No page pool — a single pre-populated slot `W = 2^70` (value 1). The
slot arrives via calldata and is read directly (not page-shifted).

```
pre-state:  slot W = 1

tx 0:  SLOAD(W) ×P           1st read cold, the rest warm
tx 1:  SLOAD(W) ×P           cold again once (warm set reset), then warm
 ...
tx 6:  SLOAD(W) ×P

checksum = P per tx;  the block re-reads one hot slot 7 × P times
```

The warm-path baseline: page/slot caching should make fork choice
irrelevant here.

### Storage operation `sload_empty_page`

Block-filling transactions cold-SLOAD a slot on never-populated, empty pages.

Like `cold_hit` but with `k = 0`: the domain-D pages were **never
populated**. Every read is a whole-page miss.

```
             page D    page D+1        page D+(P-1)
tx 0..6:     R(0)      R(0)      ...   R(0)        page does not exist

checksum = 0 (zero write in the tail, no state trace)
```

Measures lookups that fall off the page index entirely.

### Storage operation `sstore_fresh`

Block-filling transactions SSTORE 0->1 into previously-unoccupied slots on many pages.

No pre-state. Each tx gets its own disjoint range of never-touched
pages and creates one slot on each: `W(0)=1`, a 0→1 write that brings
a whole new page into existence.

```
page-index axis (base F), tiled per tx:

F         F+P          F+2P                 F+6P         F+7P
╠═ tx0: P ═╬═ tx1: P ══╬═ tx2: P ══╬═  ...  ══╬═ tx6: P ══╣
   pages       pages       pages                  pages

tx t, iteration i:  W(0)=1  on page F + t·P + i         state growth

the block creates 7 × P new pages
```

### Storage operation `sstore_noop`

Block-filling transactions SSTORE 1->1 (value unchanged) on occupied pages.

Shared occupied pool (like `cold_hit`); each tx rewrites slot 0 with
its current value — 1→1, no state change ever.

```
tx 0..6, iteration i (page D + i):
  offset 0: [ 1 ]  ◄── W(0)=1     cold page access, value unchanged

post-state == pre-state (plus markers)
```

Pays the write path without any page mutation.

### Storage operation `sstore_grow`

Block-filling transactions SSTORE 0->1 into a new empty slot of occupied pages.

Shared pool with offsets `0..k-1` occupied. Tx `t` writes offset
`k + t` — a 0→1 on an **already-occupied** page (growth within a page,
no page creation). All 7 txs touch the same P pages, each at its own
offset.

```
page D + i, one column per tx:
  offset:  0 .. k-1 |  k   k+1  k+2  k+3  k+4  k+5  k+6 | k+7 .. 127
  before: [ 1 .. 1 ]|  0    0    0    0    0    0    0  |  0
  writer:           | tx0  tx1  tx2  tx3  tx4  tx5  tx6 |
  after:  [ 1 .. 1 ]|  1    1    1    1    1    1    1  |  0

each tx: P cold W(k+t)=1 writes, one per pool page
```

### Storage operation `sstore_update`

Block-filling transactions SSTORE 1->2 (nonzero value change) on occupied pages.

Shared pool; every tx overwrites the occupied slot 0 with a fresh
nonzero value `2 + t`, so each write is a genuine value change with no
occupancy change.

```
page D + i, offset 0 over the block:
  pre    tx0     tx1     tx2     tx3     tx4     tx5     tx6
   1  ─► W(0)=2 ─► =3  ─► =4  ─► =5  ─► =6  ─► =7  ─► =8

each tx: P cold writes; slot 0 ends at 1 + 7
```

### Storage operation `sstore_clear_keep`

Block-filling transactions SSTORE 1->0 clearing one slot of occupied pages, leaving the page populated.

Shared pool with `k > 7` occupied slots. Tx `t` clears offset `t`
(1→0). Offsets `7..k-1` stay populated, so no page ever empties.

```
page D + i:
  offset:   0    1    2    3    4    5    6  |  7 .. k-1 | k .. 127
  before:   1    1    1    1    1    1    1  |  1 .. 1   |  0
  clearer: tx0  tx1  tx2  tx3  tx4  tx5  tx6 | untouched |
  after:    0    0    0    0    0    0    0  |  1 .. 1   |  0

each tx: P cold W(t)=0 writes
```

### Storage operation `sstore_clear_empty`

Block-filling transactions SSTORE 1->0 clearing the only slot of single-slot pages, removing the page.

The page-removal case. Pre-state gives **each tx its own** range of
single-slot pages (offset 0 = 1). Clearing that slot leaves the page
empty, so every write deletes a page.

```
pre-state, tiled like sstore_fresh but pre-populated with k=1:

F         F+P          F+2P                F+6P         F+7P
╠═ tx0: P ═╬═ tx1: P ══╬═   ...   ══════════╬═ tx6: P ══╣
  1-slot      1-slot                            1-slot
  pages       pages                             pages

tx t, iteration i:  W(0)=0  on page F + t·P + i
  before: [1][0..0]  →  after: [0][0..0]  →  page removed

the block removes 7 × P pages
```

---

## `test_page_spread`

Transactions SSTORE 0->1 into `m` fresh slots spread across `n` contracts.

Op is fixed (`sstore_fresh`, the 0→1 page-creating write); the sweep is
over **where** the writes land: `m` total pages spread evenly across
`n` contracts.

Each contract runs the same loop contract with empty storage. Every
contract writes page indices `F + 0 .. F + (m/n − 1)` — the *same*
indices for all contracts, but in n distinct account storages, so
nothing collides. A contract's share becomes one tx while it fits the
per-tx gas cap, and splits into several txs when it does not.

```
Block (one per fixture; txs sized to the work, not to the budget)

n = 1, large m (one contract, share split across txs):
┌ contract C0 ──────────────────────────────────────────────────────┐
│ tx0: W(0)=1 on pages F+0    .. F+c-1    (a gas-cap-sized share)    │
│ tx1: W(0)=1 on pages F+c    .. F+2c-1                              │
│  ...                                                               │
│ txN: W(0)=1 on pages F+Nc   .. F+m-1    (the remainder)            │
└───────────────────────────────────────────────────────────────────┘

n = 8 (8 txs, m/8 pages each):
┌ C0 ┐┌ C1 ┐┌ C2 ┐┌ C3 ┐┌ C4 ┐┌ C5 ┐┌ C6 ┐┌ C7 ┐
│tx0 ││tx1 ││tx2 ││tx3 ││tx4 ││tx5 ││tx6 ││tx7 │   each: m/8 × W(0)=1
└────┘└────┘└────┘└────┘└────┘└────┘└────┘└────┘

n = 512 (512 txs, m/512 pages each):
┌C0┐┌C1┐┌C2┐ ... ┌C511┐          each: m/512 × W(0)=1
└──┘└──┘└──┘     └────┘
```

Variants: `m ∈ {1,4,16,64,256,1024,4096} × n=1` (total-size sweep),
`m=4096 × n ∈ {8,64,512}` (distribution sweep). Same total I/O at
a given `m` regardless of `n` — only the account fan-out changes.

---

## `test_block_shape`

A few big vs many small transactions, each cold-SLOAD one occupied slot or SSTORE 0->1 into fresh slots.

Same total work packed as a few big txs vs many small ones. Two
workloads, each filled twice — once with a sender per tx and once with
one sender for the whole block (`distinct_senders`), giving 8 cases. The
sender mode changes no count below, only whether the block also carries
a nonce chain:

| op, k             | shape      | txs  | tx gas    | block work        |
|-------------------|------------|------|-----------|-------------------|
| sstore_fresh, k=0 | few_big    | 7    | budget/7  | txs × P new pages |
| sstore_fresh, k=0 | many_small | many | budget/N  | txs × P new pages |
| sload_cold_hit, 8 | few_big    | 7    | budget/7  | txs × P cold reads|
| sload_cold_hit, 8 | many_small | many | budget/N  | txs × P cold reads|

```
few_big:     ┌──────────┬──────────┬──────────┬──── ... ───┬──────────┐
             │   tx 0   │   tx 1   │   tx 2   │            │   tx 6   │
             └──────────┴──────────┴──────────┴──── ... ───┴──────────┘

many_small:  ┌──┬──┬──┬──┬──┬──┬──┬──┬──┬── ... ──┬──┬──┬──┬──┬──┬──┐
             │t0│t1│t2│t3│t4│t5│t6│t7│t8│         │  │  │  │  │tN-1│
             └──┴──┴──┴──┴──┴──┴──┴──┴──┴── ... ──┴──┴──┴──┴──┴──┴──┘
```

The reads reuse one shared pre-populated pool sized to a single tx
(P pages per tx, k = 8 slots each);
every tx is a fresh cold pass over it. The fresh writes tile disjoint
per-tx ranges exactly like `sstore_fresh` above. What varies is per-tx
fixed overhead (intrinsic gas, markers, cold pool re-touch) relative
to loop work.

---

## `test_tx_halt`

Seven transactions SSTORE 0->1 into fresh slots and either succeed, hit INVALID reverting all writes, or alternate.

The `sstore_fresh` full block (7 txs × P fresh pages, disjoint
ranges), with a `halt` calldata flag per tx. A halting tx performs all
of its writes **and** its marker, then executes `INVALID`: everything
reverts and the tx consumes its entire gas limit.

```
mode=success:  ┌ tx0 ✓ ┬ tx1 ✓ ┬ tx2 ✓ ┬ tx3 ✓ ┬ tx4 ✓ ┬ tx5 ✓ ┬ tx6 ✓ ┐
               all writes land: 7 × P pages + 7 markers

mode=halt:     ┌ tx0 ✗ ┬ tx1 ✗ ┬ tx2 ✗ ┬ tx3 ✗ ┬ tx4 ✗ ┬ tx5 ✗ ┬ tx6 ✗ ┐
               every tx: P × W(0)=1, marker, then INVALID
               → post-state empty, block still burns its full budget

mode=mix:      ┌ tx0 ✓ ┬ tx1 ✗ ┬ tx2 ✓ ┬ tx3 ✗ ┬ tx4 ✓ ┬ tx5 ✗ ┬ tx6 ✓ ┐
               only even txs' pages + markers survive (4 × P pages)
```

Measures the cost of executing (and then discarding) storage writes —
the revert path does the same page work as success but must be rolled
back.

---

## `test_random_sload`

Cold-SLOAD a set of distinct 1-element or empty pages (random access), from a pool of contracts spread across the block.

Random file access: each read targets a **distinct page** (`slot =
page_key << 7`), and the MPT hashes every page key (`keccak256`) to an
unpredictable trie/disk position, so consecutive page keys land at
uncorrelated disk locations. Params: `slots ∈ {1, 16, 128}` (size of
the per-tx page set) × `k ∈ {0, 1}` (each read page is 1-slot occupied,
or never populated).

A pool of 8 identical contracts is deployed; tx `g` calls contract
`g mod 8` and carries a base page key strided by `g`, giving every tx a
disjoint set of pages inside "its" contract:

```
Block:   tx0→C0   tx1→C1   tx2→C2   tx3→C3   tx4→C4   tx5→C5   tx6→C6

tx g:  page set = { (base_g + i) << 7 : i < slots },  base_g strided by g
       every page key hashed by the MPT → random disk position

       iteration j (of P) reads page (base_g + (j mod slots)) << 7:
       j:     0        1       ...  slots−1 │ slots ...        P-1
             base+0   base+1   ...          │ (set cycles again)
             cold     cold     ...  cold    │ warm ...         warm
```

Each tx makes P reads, but only the first pass over the set
(`slots` reads) is genuinely cold — sizing charges every iteration as
cold, so these blocks do far less work than the budget by design;
the subject is random-locality I/O, not volume. With `k = 1` the set's
pages are pre-populated (one slot = 1) in that contract's genesis and
the checksum is P; with `k = 0` every read is a whole-page miss and
the tail checksum SSTORE writes 0 (no state trace). Markers/checksums
land in the storage of whichever contract the tx called.

---

## `test_bad_block_serial`

Every tx SLOAD+SSTORE-increments the same slot sequence, forcing serial execution across the block.

No parameters. The write-conflict adversarial block: all 7 txs
read-then-increment the **same** contiguous slot range, so every tx
depends on the previous tx's writes and the block cannot be executed
in parallel.

```
shared slot range: base = 2^40, slots base+0 .. base+782
(contiguous keys → the whole range is just 7 dense pages)

per iteration i:  SLOAD(base+i), then SSTORE(base+i, read+1)

           slot: base+0   base+1   base+2  ...  base+P-1
tx 0:             0→1      0→1      0→1          0→1     fresh writes
tx 1:             1→2      1→2      1→2          1→2   ▲ must see the
 ...                                                   │ previous tx's
tx 6:             6→7      6→7      6→7          6→7   │ writes: serial
```

P read+write pairs per tx (sized to the fresh 0→1
cost — the later increment txs are nonzero→nonzero updates and run
cheaper, leaving the block somewhat gas-underfull). Post-state: every
shared slot ends at 7, plus the 7 markers (no checksum tail here).

---

## `test_bad_block_chained`

Each SLOAD returns the next SLOAD's slot (SLOAD(SLOAD(...))), a data-dependent pointer chase over distinct pages that serialises the reads within a tx.

No parameters. The data-dependency adversarial block: the genesis
storage holds a pre-built ring of P **distinct pages** (keys
`(base+j) << 7`) where each page's stored value is the key of the next
page. A tx starts from its calldata base and hops the ring with
`slot := SLOAD(slot)` — every read's address comes from the previous
read, so the reads serialize within the tx, and because the MPT hashes
each page key, every hop is an unpredictable disk position.

```
genesis ring (P distinct pages, keys (base+j) << 7):

   ring[0] ──► ring[1] ──► ring[2] ──► ... ──► ring[P-1] ──┐
      ▲                                                     │
      └─────────────────────────────────────────────────────┘
             SLOAD(ring[j]) returns ring[j+1]

tx t (t = 0..6):  start at ring[t], then P hops
                  = one full lap, staggered one step per tx
```

All 7 txs traverse the same ring; per-tx warmth reset makes each lap
fully cold, so the block performs 7 × P cold, address-dependent
reads. Nothing is written except the 7
markers — post-state is the untouched ring plus markers.
