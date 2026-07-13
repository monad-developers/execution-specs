# MIP-8 perf-regression tests: per-family block diagrams

What each test-case family in
`tests/monad_ten/mip8_pageified_storage/test_perf_regression.py` actually
does at the SLOAD/SSTORE level inside one block. Numbers (pages per tx,
per-iteration gas) are computed with the module's own sizing helpers at
`BLOCK_GAS_TARGET = 200M`, `REPEATS = 1`.

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
— one 200M-gas block, 7 equal txs, all from the same sender to the same
workload contract (`test_random_sload` cycles a pool of 8 contracts
instead):

```
Block (gas limit 200,000,000)
┌──────────┬──────────┬──────────┬──────────┬──────────┬──────────┬──────────┐
│   tx 0   │   tx 1   │   tx 2   │   tx 3   │   tx 4   │   tx 5   │   tx 6   │
│  28.57M  │  28.57M  │  28.57M  │  28.57M  │  28.57M  │  28.57M  │  28.57M  │
└──────────┴──────────┴──────────┴──────────┴──────────┴──────────┴──────────┘
```

Per-tx warm/access sets reset between transactions, so every tx of a
block pays **cold** access again even when it re-touches the exact pages
tx 0 touched. This is what lets one pre-populated pool serve all 7 txs
as 7 independent cold passes.

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

No parameters, one family. The storage-free baseline: one block, one
10M-gas tx running a stack-arithmetic `WhileGas` loop.

```
Block
┌───────────────────────────────────────────────┐
│ tx 0 (10M gas)                                │
│   loop: POP(ADD(MUL(NUMBER, GAS), CALLVALUE)) │   no storage access
│   ... repeated until gas nearly spent ...     │
│   SSTORE(slot 1, 0x1234)                      │   the only write
└───────────────────────────────────────────────┘
```

Only the final marker touches storage, so the block isolates pure
execution overhead from any MIP-8 effect.

---

## `test_page_ops` — one family per storage op

Family = one `StorageOp`; within a family, `k` (page occupancy) varies.
All variants use the 7-tx full block above. Per-tx page counts:

| op                 | k values     | P pages/tx                 |
|--------------------|--------------|----------------------------|
| sload_cold_hit     | {1,16,128}   | 3469; k=128: 512¹          |
| sload_cold_miss    | {1,16,64}    | 3469; k=64: 1024¹          |
| sload_sweep        | {2,16,128}   | k=2:1741 k=16:218 k=128:27 |
| sload_warm_repeat  | {1}          | 158,946 (iterations)       |
| sload_empty_page   | {0}          | 3469                       |
| sstore_fresh       | {0}          | 1009                       |
| sstore_noop        | {1,16,128}   | 3432; k=128: 512¹          |
| sstore_grow        | {1,16,64}    | 1009                       |
| sstore_update      | {1,16,128}   | 2563; k=128: 512¹          |
| sstore_clear_keep  | {8,16,128}   | 2565; k=128: 512¹          |
| sstore_clear_empty | {1}          | 2565                       |

¹ capped by `PRE_SLOT_CAP = 65,536` pre-state slots per block
(`P = 65,536 / k`); those blocks do less than 200M of real work by
design — the pre-state, not gas, is the bound.

### Family `sload_cold_hit`

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

### Family `sload_cold_miss`

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

### Family `sload_sweep`

Each loop iteration cold-reads **every occupied slot** of one page,
offsets `0..k-1` in ascending order.

```
tx 0..6, iteration i (page D + i):
  offset:  0    1    2   ...  k-1 | k .. 127
          R(0) R(1) R(2) ... R(k-1)| untouched     k cold reads

per tx: P pages × k reads   (k=2: 1741×2, k=16: 218×16, k=128: 27×128)
checksum = P × k per tx
```

Isolates intra-page locality: same number-ish of cold reads as
`cold_hit`, but bunched k-per-page instead of 1-per-page.

### Family `sload_warm_repeat`

No page pool — a single pre-populated slot `W = 2^70` (value 1). The
slot arrives via calldata and is read directly (not page-shifted).

```
pre-state:  slot W = 1

tx 0:  SLOAD(W) ×158,946      1st read cold, remaining 158,945 warm
tx 1:  SLOAD(W) ×158,946      cold again once (warm set reset), then warm
 ...
tx 6:  SLOAD(W) ×158,946

checksum = 158,946 per tx;  block total ≈ 1.11M reads of one hot slot
```

The warm-path baseline: page/slot caching should make fork choice
irrelevant here.

### Family `sload_empty_page`

Like `cold_hit` but with `k = 0`: the domain-D pages were **never
populated**. Every read is a whole-page miss.

```
             page D    page D+1        page D+(P-1)
tx 0..6:     R(0)      R(0)      ...   R(0)        page does not exist

P = 3469 per tx; checksum = 0 (zero write in the tail, no state trace)
```

Measures lookups that fall off the page index entirely.

### Family `sstore_fresh`

No pre-state. Each tx gets its own disjoint range of never-touched
pages and creates one slot on each: `W(0)=1`, a 0→1 write that brings
a whole new page into existence.

```
page-index axis (base F), tiled per tx:

F         F+P          F+2P                 F+6P         F+7P
╠═ tx0: P ═╬═ tx1: P ══╬═ tx2: P ══╬═  ...  ══╬═ tx6: P ══╣
   pages       pages       pages                  pages

tx t, iteration i:  W(0)=1  on page F + t·P + i         state growth

P = 1009  →  block creates 7 × 1009 = 7063 new pages
```

### Family `sstore_noop`

Shared occupied pool (like `cold_hit`); each tx rewrites slot 0 with
its current value — 1→1, no state change ever.

```
tx 0..6, iteration i (page D + i):
  offset 0: [ 1 ]  ◄── W(0)=1     cold page access, value unchanged

post-state == pre-state (plus markers); P = 3432 (512 at k=128)
```

Pays the write path without any page mutation.

### Family `sstore_grow`

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

each tx: P = 1009 cold W(k+t)=1 writes, one per pool page
```

### Family `sstore_update`

Shared pool; every tx overwrites the occupied slot 0 with a fresh
nonzero value `2 + t`, so each write is a genuine value change with no
occupancy change.

```
page D + i, offset 0 over the block:
  pre    tx0     tx1     tx2     tx3     tx4     tx5     tx6
   1  ─► W(0)=2 ─► =3  ─► =4  ─► =5  ─► =6  ─► =7  ─► =8

each tx: P = 2563 cold writes (512 at k=128); slot 0 ends at 8
```

### Family `sstore_clear_keep`

Shared pool with `k > 7` occupied slots. Tx `t` clears offset `t`
(1→0). Offsets `7..k-1` stay populated, so no page ever empties.

```
page D + i:
  offset:   0    1    2    3    4    5    6  |  7 .. k-1 | k .. 127
  before:   1    1    1    1    1    1    1  |  1 .. 1   |  0
  clearer: tx0  tx1  tx2  tx3  tx4  tx5  tx6 | untouched |
  after:    0    0    0    0    0    0    0  |  1 .. 1   |  0

each tx: P = 2565 cold W(t)=0 writes (512 at k=128)
```

### Family `sstore_clear_empty`

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

P = 2565  →  block removes 7 × 2565 = 17,955 pages
```

---

## `test_page_spread` — one family

Op is fixed (`sstore_fresh`, the 0→1 page-creating write); the sweep is
over **where** the writes land: `m` total pages spread evenly across
`n` contracts.

Each contract runs the same loop contract with empty storage. Every
contract writes page indices `F + 0 .. F + (m/n − 1)` — the *same*
indices for all contracts, but in n distinct account storages, so
nothing collides. A contract's share becomes one tx while it fits the
30M tx cap (≤ 1060 pages); `m/n = 4096` splits into 4 txs.

```
Block (one per fixture; txs sized to the work, not to fill 200M)

n = 1, m = 4096 (4 txs, one contract):
┌ contract C0 ──────────────────────────────────────────────────────┐
│ tx0: W(0)=1 on pages F+0     .. F+1059     (1060 pages, 30.0M)     │
│ tx1: W(0)=1 on pages F+1060  .. F+2119     (1060 pages, 30.0M)     │
│ tx2: W(0)=1 on pages F+2120  .. F+3179     (1060 pages, 30.0M)     │
│ tx3: W(0)=1 on pages F+3180  .. F+4095     ( 916 pages, 25.9M)     │
└───────────────────────────────────────────────────────────────────┘

n = 8, m = 4096 (8 txs, 512 pages each):
┌ C0 ┐┌ C1 ┐┌ C2 ┐┌ C3 ┐┌ C4 ┐┌ C5 ┐┌ C6 ┐┌ C7 ┐
│tx0 ││tx1 ││tx2 ││tx3 ││tx4 ││tx5 ││tx6 ││tx7 │   each: 512 × W(0)=1
└────┘└────┘└────┘└────┘└────┘└────┘└────┘└────┘   14.55M gas each

n = 512, m = 4096 (512 txs, 8 pages each):
┌C0┐┌C1┐┌C2┐ ... ┌C511┐          each: 8 × W(0)=1, 345,504 gas
└──┘└──┘└──┘     └────┘          block ≈ 177M gas
```

Variants: `m ∈ {1,4,16,64,256,1024,4096} × n=1` (total-size sweep),
`m=4096 × n ∈ {8,64,512}` (distribution sweep). Same total I/O at
`m = 4096` regardless of `n` — only the account fan-out changes.

---

## `test_block_shape` — one family

Same total work packed as **7 big** txs vs **300 small** txs. Two
workloads:

| op, k             | shape      | txs | tx gas    | iters/tx | block work         |
|-------------------|------------|-----|-----------|----------|--------------------|
| sstore_fresh, k=0 | few_big    | 7   | 28.57M    | 1009     | 7063 new pages     |
| sstore_fresh, k=0 | many_small | 300 | 666,666   | 19       | 5700 new pages     |
| sload_cold_hit, 8 | few_big    | 7   | 28.57M    | 3469     | 7×3469 cold reads  |
| sload_cold_hit, 8 | many_small | 300 | 666,666   | 66       | 300×66 cold reads  |

```
few_big:     ┌──────────┬──────────┬──────────┬──── ... ───┬──────────┐
             │   tx 0   │   tx 1   │   tx 2   │            │   tx 6   │
             └──────────┴──────────┴──────────┴──── ... ───┴──────────┘

many_small:  ┌──┬──┬──┬──┬──┬──┬──┬──┬──┬── ... ──┬──┬──┬──┬──┬──┬──┐
             │t0│t1│t2│t3│t4│t5│t6│t7│t8│         │  │  │  │  │t299│
             └──┴──┴──┴──┴──┴──┴──┴──┴──┴── ... ──┴──┴──┴──┴──┴──┴──┘
```

The reads reuse one shared pre-populated pool sized to a single tx
(3469 pages for few_big, 66 pages for many_small, k = 8 slots each);
every tx is a fresh cold pass over it. The fresh writes tile disjoint
per-tx ranges exactly like `sstore_fresh` above. What varies is per-tx
fixed overhead (intrinsic gas, markers, cold pool re-touch) relative
to loop work.

---

## `test_tx_halt` — one family

The `sstore_fresh` full block (7 txs × 1009 fresh pages, disjoint
ranges), with a `halt` calldata flag per tx. A halting tx performs all
of its writes **and** its marker, then executes `INVALID`: everything
reverts and the tx consumes its entire 28.57M gas limit.

```
mode=success:  ┌ tx0 ✓ ┬ tx1 ✓ ┬ tx2 ✓ ┬ tx3 ✓ ┬ tx4 ✓ ┬ tx5 ✓ ┬ tx6 ✓ ┐
               all writes land: 7063 pages + 7 markers

mode=halt:     ┌ tx0 ✗ ┬ tx1 ✗ ┬ tx2 ✗ ┬ tx3 ✗ ┬ tx4 ✗ ┬ tx5 ✗ ┬ tx6 ✗ ┐
               every tx: 1009 × W(0)=1, marker, then INVALID
               → post-state empty, block still burns the full 200M

mode=mix:      ┌ tx0 ✓ ┬ tx1 ✗ ┬ tx2 ✓ ┬ tx3 ✗ ┬ tx4 ✓ ┬ tx5 ✗ ┬ tx6 ✓ ┐
               only even txs' pages + markers survive (4 × 1009 pages)
```

Measures the cost of executing (and then discarding) storage writes —
the revert path does the same page work as success but must be rolled
back.

---

## `test_random_sload` — one family

Random file access: each read targets a **distinct page** (`slot =
page_key << 7`), and the MPT hashes every page key (`keccak256`) to an
unpredictable trie/disk position, so consecutive page keys land at
uncorrelated disk locations. Params: `slots ∈ {1, 16, 128}` (size of
the per-tx page set) × `k ∈ {0, 1}` (each read page is 1-slot occupied,
or never populated).

A pool of 8 identical contracts is deployed; tx `g` calls contract
`g mod 8` and carries a base page key `1 + 1024·g`, giving every tx a
disjoint set of pages inside "its" contract:

```
Block:   tx0→C0   tx1→C1   tx2→C2   tx3→C3   tx4→C4   tx5→C5   tx6→C6

tx g:  page set = { (base_g + i) << 7 : i < slots },  base_g = 1 + 1024·g
       every page key hashed by the MPT → random disk position

       iteration j (of 3469) reads page (base_g + (j mod slots)) << 7:
       j:     0        1       ...  slots−1 │ slots ...       3468
             base+0   base+1   ...          │ (set cycles again)
             cold     cold     ...  cold    │ warm ...         warm
```

Each tx makes 3469 reads, but only the first pass over the set
(`slots` reads) is genuinely cold — sizing charges every iteration as
cold (8160 gas), so these blocks are heavily gas-underfull by design;
the subject is random-locality I/O, not volume. With `k = 1` the set's
pages are pre-populated (one slot = 1) in that contract's genesis and
the checksum is 3469; with `k = 0` every read is a whole-page miss and
the tail checksum SSTORE writes 0 (no state trace). Markers/checksums
land in the storage of whichever contract the tx called.

---

## `test_bad_block_serial` — one family

No parameters. The write-conflict adversarial block: all 7 txs
read-then-increment the **same** contiguous slot range, so every tx
depends on the previous tx's writes and the block cannot be executed
in parallel.

```
shared slot range: base = 2^40, slots base+0 .. base+782
(contiguous keys → the whole range is just 7 dense pages)

per iteration i:  SLOAD(base+i), then SSTORE(base+i, read+1)

           slot: base+0   base+1   base+2  ...  base+782
tx 0:             0→1      0→1      0→1          0→1     fresh writes
tx 1:             1→2      1→2      1→2          1→2   ▲ must see the
 ...                                                   │ previous tx's
tx 6:             6→7      6→7      6→7          6→7   │ writes: serial
```

783 read+write pairs per tx (36,254 gas each, sized to the fresh 0→1
cost — the later increment txs are nonzero→nonzero updates and run
cheaper, leaving the block somewhat gas-underfull). Post-state: every
shared slot ends at 7, plus the 7 markers (no checksum tail here).

---

## `test_bad_block_chained` — one family

No parameters. The data-dependency adversarial block: the genesis
storage holds a pre-built ring of 3482 **distinct pages** (keys
`(base+j) << 7`) where each page's stored value is the key of the next
page. A tx starts from its calldata base and hops the ring with
`slot := SLOAD(slot)` — every read's address comes from the previous
read, so the reads serialize within the tx, and because the MPT hashes
each page key, every hop is an unpredictable disk position.

```
genesis ring (3482 distinct pages, keys (base+j) << 7):

   ring[0] ──► ring[1] ──► ring[2] ──► ... ──► ring[3481] ──┐
      ▲                                                     │
      └─────────────────────────────────────────────────────┘
             SLOAD(ring[j]) returns ring[j+1]

tx t (t = 0..6):  start at ring[t], then 3482 hops
                  = one full lap, staggered one step per tx
```

All 7 txs traverse the same ring; per-tx warmth reset makes each lap
fully cold, so the block performs 7 × 3482 cold, address-dependent
reads. Nothing is written except the 7
markers — post-state is the untouched ring plus markers.
