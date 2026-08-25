# Adopt Upstream EIP

Adopt a single upstream EIP into a work-in-progress Monad fork and release
its fixtures, while the rest of the Ethereum fork's EIPs stay out. Run this
skill before starting such work.

## Inputs

- **`<MONAD_FORK>`** — the WIP Monad fork that adopts the EIP, and
  **`<MONAD_PARENT>`**, the Monad fork it succeeds. Example: `MONAD_NEXT`
  succeeding `MONAD_TEN`.
- **`<EIP>`** — the EIP number, or the numbers joined with `+` for a
  bundle. Examples: `7997`, `2345+3456`. Bundle several in one branch
  only when they are interdependent or tightly coupled; otherwise one
  EIP per branch, so each releases and reviews on its own.

Derived from those, and used as placeholders throughout:

| Placeholder | Meaning | Example |
| --- | --- | --- |
| `<ETH_FORK>` | Ethereum fork that owns the EIP | `Amsterdam` |
| `<eth_fork>` | its lowercase path form | `amsterdam` |
| `<monad_fork>` | lowercase path form of `<MONAD_FORK>` | `monad_next` |
| `<monad_parent>` | lowercase path form of `<MONAD_PARENT>` | `monad_ten` |
| `<MONAD_BRANCH>` | branch the Monad forks live on | `forks/monad_nine` |

`MONAD_EIGHT` appears literally where a marker or comparison must mean
"every Monad fork": it is the earliest one, so `subsequent_forks=True` and
`>=` cover the rest. Replace it only if an earlier Monad fork appears.

## Goal and Invariants

The end state is a set of independent branches, each releasing fixtures for
its own EIP set, that eventually merge into `<MONAD_BRANCH>` with **minimal
drift from upstream**. They are built in parallel and merged one after
another, so where two of them touch the same file they should try to avoid conflicts
there.

A change to a file the Monad forks do not own is legitimate in two ways:

- **Permanent** — still correct after the merge: the fork class, the ported
  spec change, the `follows=` hook, gating for a divergence that is
  genuinely Monad's.
- **Scaffolding** — needed only while the branch stands alone, because the
  branch has to fill green by itself: an exclusion for a suite a sibling
  branch adopts, the per-EIP release feature, expectations that a later
  adoption deletes.

Scaffolding is allowed and often unavoidable. What is not allowed is a
change that is neither — one that does nothing on its own branch and is
kept only for symmetry with a sibling. Keep each piece of scaffolding
load-bearing on its branch (measured, not assumed), gate it on a capability
so it neutralises itself where possible (`fork.is_eip_enabled(<n>)`), and
list it in step 12 so the merge removes it.

1. `<MONAD_FORK>` adopts EIP mixins. It never inherits `<ETH_FORK>`.
2. Fork order comes from `follows=`, never from inheritance.
3. Shared test files change only where Monad genuinely diverges, or where
   the branch cannot fill green without it.
4. What the sibling branches genuinely share is byte-identical between
   them: the same `follows=` hook, the same marker guard, the same
   exclusion wording, the same test adaptation. Identical additions merge
   silently; wording that drifts conflicts. Apply the alignment to what a
   branch needs anyway — it is never itself a reason to carry a change,
   which is the distinction above.

## Keeping the Shared Code Shallow

Everything under `src/ethereum/forks/monad*` and `tests/monad*` is the
Monad forks' own, so an edit there never collides with an upstream change
to the same file. It still has a price: those directories are copies of an
upstream fork, and every refactor or bug fix upstream makes to the original
gets carried across by hand, which is harder the further the copy has
drifted. A shared file costs more again — each line of the edit is a
conflict the next upstream merge resolves by hand. Argue for either cost
rather than reaching for it, and keep the Monad copies close enough to
their originals that carrying a fix across stays mechanical.

Before touching a shared file, measure how contested it is, and note how
much of the tree imports it:

```bash
git log --oneline --since="4 months ago" <upstream_ref> -- <file> | wc -l
```

A file upstream rewrites every other week will conflict on every merge; one
it has not touched in a year is nearly free. Let that number decide how
hard to look for an alternative.

When a shared edit is unavoidable, choose by where the file sits, not by
how few lines the change takes. Two things set the price: how often
upstream rewrites the file, and how much of the tree depends on it. Prefer,
in this order:

1. **Leaf files, repeated if need be.** A change written out several times
   in files nothing imports — tests, fixtures, per-fork definitions —
   costs less than one line in code everything imports. Each copy
   conflicts only where it stands, is greppable, and can be fixed or
   dropped on its own, while the line in shared plumbing is exposed to
   every future upstream change and can alter behavior far from where it
   was written. Repetition is the right answer often enough to try it
   first.
2. **A new trait with a default.** Where a single decision point is
   genuinely needed, add a classmethod returning the existing behavior and
   override it on the Monad fork. Additions merge silently; edits to
   existing logic do not.
3. **Derivation over stored state.** If the answer follows from something
   the fork already knows, compute it there rather than recording it in
   another field, keyword or registry — one expression instead of a
   declaration site, an assignment site and a reader.
4. **The fork's own class body.** Override in the fork rather than changing
   the base class or a shared helper.

Deduplication is not the goal; bounded cost is. Repeated edits earn a
single decision point once they start drifting out of step with each other,
and then they collapse into the least contested file that can hold the
decision — not the most abstract one.

And avoid, however tempting:

- **Central plumbing.** Class-construction hooks, registries and anything
  every fork flows through are the busiest code upstream owns; a change
  there conflicts repeatedly and, worse, can merge cleanly while behaving
  differently.
- **New abstract methods.** They force every existing fork to answer. A
  concrete default leaves the rest of the hierarchy untouched.
- **Editing upstream EIP definitions.** They belong to the fork that
  introduced them; express the difference on the Monad fork instead.
- **Restructuring.** Reflowing a block to insert one line turns a silent
  merge into a conflict. Append; do not reshape.

The reviewable test is the diff itself: a shared file should show added
lines and no moved ones. If it shows a rewritten function, look again for a
trait that would have done the job.

## 1. Scope the EIP

```bash
git log --oneline --all --grep="<EIP>" | head          # upstream commits
git show <sha> --stat -- 'src/*'                       # spec-side change
cat packages/testing/src/execution_testing/forks/forks/eips/<eth_fork>/eip_<EIP>.py
grep -rhoE 'valid_[a-z_]+\("[A-Za-z0-9_]+"' tests/<eth_fork>/eip<EIP>_*/
```

Read the EIP text at `https://eips.ethereum.org/EIPS/eip-<EIP>`, then
establish three things:

- **Is there a spec change at all?** Some EIPs are framework-only. EIP-7997
  is a pre-allocation: its upstream commit touched one docstring in `src/`.
  EIP-8246 changed `state_tracker.py`, `fork.py` and `vm/instructions/system.py`.
- **Which validity markers the EIP's own suite uses.** `valid_from("EIP…")`
  expands forward by fork comparison, so `<MONAD_FORK>` is selected once it
  is ordered after `<ETH_FORK>`. `valid_at("EIP…")` resolves to the
  enabling forks only, so `<MONAD_FORK>` is selected only by adopting the
  mixin. `valid_at_transition_to("EIP…")` needs the marker guard in step 6.
- **Which gas constants or fork methods the EIP touches**, to predict what
  the mixin composes with.

## 2. Branch

```bash
git fetch origin
git checkout -b eips/<monad_fork>/eip-<EIP> origin/<MONAD_BRANCH>
```

Branch from `<MONAD_BRANCH>`, not from a sibling EIP branch. The branches
stay independent until a deliberate merge.

## 3. Adopt the EIP in the framework

In `packages/testing/src/execution_testing/forks/forks/forks.py`, declare
`<MONAD_FORK>` **after** `<ETH_FORK>` (so the name resolves) with the EIP
mixins first:

```python
class <MONAD_FORK>(  # noqa: N801
    eips.EIP<EIP>,
    <MONAD_PARENT>,
    follows=<ETH_FORK>,
):
    """
    <MONAD_FORK> fork.

    <ETH_FORK>-based successor to <MONAD_PARENT>, adopting the EIP-<EIP>
    changes. The <ETH_FORK> changes it does not adopt stay out of the
    fork by not being inherited at all; the fork order still places
    <MONAD_FORK> after <ETH_FORK> through `follows`.
    """

    pass
```

Concretely: `class MONAD_NEXT(eips.EIP7997, MONAD_TEN, follows=Amsterdam)`.

Why this shape, and not `class <MONAD_FORK>(<MONAD_PARENT>, <ETH_FORK>)`:

- Monad's own overrides use cooperative `super()`, so inheriting
  `<ETH_FORK>` threads its mixins underneath them. Hand-written pin-backs
  cannot fix the `super()` chain.
- Mixins first means each mixin's `super()` lands on `<MONAD_PARENT>`, so a
  wrapper EIP composes onto Monad's schedule. EIP-7981 on `MONAD_NEXT`
  yields an intrinsic of 27380 against the parent's 25300 — exactly
  (80 + 128) floor tokens at Monad's rate of 10, with EIP-2780 absent.
- Listing the mixins ahead of an inherited `<ETH_FORK>` is impossible:
  `TypeError: Cannot create a consistent method resolution order`, because
  that fork already inherits them.

`follows=` is a `BaseFork.__init_subclass__` keyword that records
succession without inheritance; `BaseForkMeta._is_subclass_of` walks the
chain so comparisons see it. A successor fork inherits the value, so
`<MONAD_FORK>`'s own successors stay ordered after `<ETH_FORK>` without
restating it. If a branch predates the hook, add it with the
same wording so sibling branches stay textually identical.

Verify the shape before going further:

```bash
uv run python -c "
from execution_testing.forks.forks import forks as F
n = F.<MONAD_FORK>; p = F.<MONAD_PARENT>; g = n.gas_costs()
print('MRO:', [c.__name__ for c in n.__mro__[:5]])
print('> <ETH_FORK>:', n > F.<ETH_FORK>, '| eips:', len(n._enabled_eips))
print('adopted:', n.is_eip_enabled(<EIP>), '| parent:', p.is_eip_enabled(<EIP>))
print(f'gas COLD={g.COLD_ACCOUNT_ACCESS} STORAGE_SET={g.STORAGE_SET} FLOOR={g.TX_DATA_TOKEN_FLOOR} TX_BASE={g.TX_BASE}')
"
```

The MRO must read mixins then `<MONAD_PARENT>`, with `<ETH_FORK>` absent.
The gas constants must match `<MONAD_PARENT>` **apart from the ones the
adopted EIP changes**, and those must have changed. Anything
the fork does not adopt must report as disabled — spot-check the methods
the EIPs left out would have changed.

## 4. Port the spec change

Apply the upstream change to `src/ethereum/forks/<monad_fork>/` verbatim,
resolving conflicts against what the Monad fork already has. Read the
upstream hunks and reproduce them; most spec changes are a handful of lines
(EIP-7976 is two). For a larger change, rewrite the diff's **path headers
only** and let the rejects show where the copies have diverged:

```bash
git diff <upstream_base>..<upstream_head> -- src/ethereum/forks/<eth_fork> \
  > /tmp/eip.patch
# rewrite <eth_fork> in the diff/---/+++ header lines only, then:
git apply --reject /tmp/eip.patch      # resolve each .rej hunk by hand
```

Do not rewrite the diff body. A blanket `sed` over the patch also renames
the fork inside docstrings, cross-references and any identifier that
contains the name. Either way, finish by reading `git diff` against the
upstream hunks: the same lines must have moved, and nothing else.

Recurring conflicts: Monad imports `ethereum.state_paged` where upstream
imports `ethereum.state_mpt`; Monad's memory is `EvmMemory` with a
watermark (MIP-3); Monad has no `gas_meter`; Monad's exceptions module
carries its own additions. Drop imports the change no longer needs, and
keep a placeholder successor fork in sync when it mirrors `<monad_parent>`.

Skip this step entirely when the EIP is framework-only.

## 5. Keep the unadopted suites off the fork

`follows=` orders the fork after `<ETH_FORK>`, so every suite marked
`valid_from("<ETH_FORK>")` or `valid_from("EIP…")` now selects it — for
EIPs the fork does not implement. For each such suite, add:

```python
"""Pytest (plugin) definitions local to EIP-<n> tests."""

import pytest


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Mark all tests in this subdir as not valid for Monad forks."""
    metafunc.definition.add_marker(
        pytest.mark.not_valid_for("MONAD_EIGHT", subsequent_forks=True)
    )
```

Append the hook when the suite already owns a `conftest.py`; create the
file when it does not. Keep the text identical across branches.

Add exclusions only where they are load-bearing. Measure, one suite at a
time, by stripping the hook and filling that suite for the fork:

```bash
cp tests/<eth_fork>/eip<n>_*/conftest.py /tmp/conftest.bak
# strip the pytest_generate_tests hook, then:
uv run fill --clean --no-html -m blockchain_test \
  tests/<eth_fork>/eip<n>_* --fork <MONAD_FORK> --chain-id 143
cp /tmp/conftest.bak tests/<eth_fork>/eip<n>_*/conftest.py
```

Restore from a copy, not with `git checkout HEAD --`: while the branch is
being built these conftests are uncommitted, so checking out HEAD deletes
the hook instead of putting it back.

Confirm the final selection:

```bash
uv run fill --collect-only -q -m blockchain_test --fork <MONAD_FORK> \
  --chain-id 143 -k "not invalid_header" 2>/dev/null \
  | grep -oE "^tests/<eth_fork>/[a-z0-9_]+/" | sort -u
```

The adopted EIP's directory must appear. Others may too, as long as they
pass for the fork: the rule is that no unadopted suite **fails**, not that
none is selected. Excluding a suite that passes only adds drift.

## 6. Marker guard, only when needed

If the adopted EIP's suite uses `valid_at_transition_to("EIP…")`, the fork
becomes a second enabling fork and the marker rejects the expansion. In
`packages/testing/src/execution_testing/cli/pytest_commands/plugins/forks/forks.py`,
`ValidAtTransitionTo._process_with_marker_args` must bound the **arguments**
rather than the resolved forks:

```python
        # A single EIP argument expands to one fork per enabling fork, so
        # the limit is on the arguments rather than on the resolved forks.
        if len(fork_args) > 1:
```

Leave that file untouched when the suite has no EIP-named transition
marker: EIP-8246 has none, and EIP-7997 uses a fork-named one.

## 7. Adapt the tests where Monad diverges

Before adapting a failing test, establish what Monad actually does. A
failure is only understood once the Monad behavior behind it is named, and
the expectation must then encode that behavior rather than a guess that
makes the assertion pass. The sources are `https://docs.monad.xyz`, the
adopted MIPs (MIP-3 linear memory, MIP-4 reserve balance, MIP-8 paged
storage, and whatever else the fork carries) and the fork's own
`src/ethereum/forks/<monad_fork>/` code, which is the final word.

Read as context, they explain failures that look unrelated to the EIP. For
example `fork.py` sums `tx.gas` into `block_gas_used` and passes that to
`make_receipt`, so Monad bills the whole gas limit: no refund, no floor
comparison, no credit for `gas_left`. Every `cumulative_gas_used`
expectation on a Monad fork is therefore the gas limit, whatever the EIP
under test does to floors or refunds.

Two bodies of tests need this, and the adopted EIP's **own suite** is
usually the larger one. It asserts against `<ETH_FORK>`'s fork methods and
constants, several of which Monad does not share, and step 6's guard adds
transition cases for the `<MONAD_PARENT>`-to-`<MONAD_FORK>` boundary that
have no upstream counterpart. Failures come in four shapes, among others:

- a constant that arrived with an unadopted EIP
- a capability Monad kept and `<ETH_FORK>` dropped 
- the newly generated transition cases, whose expectations assume the
  `<ETH_FORK>` side of the boundary;
- a mechanism Monad removed

Adopt such a test rather than dropping it: gate on `fork >= MONAD_EIGHT`
and assert Monad's value — a refund of zero — so the fork keeps the
coverage instead of losing the case. Deselect only when the test's subject
does not exist on the fork at all. Where the framework advertises a
capability the spec does not have (`refund_types()` listing refunds Monad
never pays), fix the trait instead of every test it parametrises.

Budget for this: a framework-only EIP may need one test touched, while an
EIP that reprices gas needs twenty. The shared suites elsewhere in `tests/`
need the same treatment for the same reasons.

Gate on capability, not on fork identity:

- `fork.is_eip_enabled(<n>)` for anything an EIP changes. Precedent exists
  throughout `tests/`.
- `fork >= MONAD_EIGHT` for Monad-specific behavior. **Always the positive
  form.** `fork < MONAD_EIGHT` reads False for `<ETH_FORK>` too, because
  Monad forks and Ethereum forks are incomparable — neither is a subclass
  of the other.

Prefer a helper over repeated conditionals when several sites share the
rule, and keep the change out of branches where the gate is never true.
However, avoid modifying abstract framework code, which could be conflict-prone
on subsequent merges from the upstream.

## 8. Declare the fixture release

In `.github/configs/feature.yaml`:

```yaml
monad_eip<EIP>:
  evm-type: eels
  fill-params: -m blockchain_test --fork=<MONAD_FORK> --chain-id=143 -k "not invalid_header"
```

The name mirrors the branch, bundles included, so a branch
`eips/monad_next/eip-2345+3456` declares `monad_eip2345+3456`. The
`check_release.yaml` rehearsal deduces the feature from the branch name,
and rehearses every feature the branch declares when it finds none.
`monad_amsterdam` predates the convention.

## 9. Validate with a full-suite fill

Fill the whole suite for the fork, with the release feature's parameters:

```bash
uv run fill --clean --no-html -m blockchain_test \
  --fork <MONAD_FORK> --chain-id 143 -k "not invalid_header" \
  --output=<scratch>/fixtures
```

The whole suite matters, not just the EIP's own directory: ordering the
fork after `<ETH_FORK>` changes which suites select it, so a regression can
surface anywhere.

## 10. Open the PR

The PR targets `<MONAD_BRANCH>`:

```
https://github.com/monad-developers/execution-specs/compare/<MONAD_BRANCH>...<branch>?expand=1
```

Record in the description any consequence a client must match — for example
fixtures that newly assert a rejection reason the Monad client has to map.

## 11. Release the fixtures

```bash
gh workflow run release_fixtures.yaml --ref <branch> \
  -f feature=monad_eip<EIP> -f version=v0.1.0
```

`generate_build_matrix.py` reads `.github/configs/feature.yaml` from the
dispatched ref, so `--ref` must be the branch that declares the feature.
First release is `v0.1.0`. `release_fixtures.yaml` must exist on the
default branch for the dispatch to be accepted.

## 12. Record the merge-time cleanup

List the branch's scaffolding in the PR, so the eventual merge removes it:

- The exclusion conftest for every EIP the merged fork adopts.
- The per-EIP `monad_eip<EIP>` feature entry, if the merged fork releases
  under one name.
- Any expectation scaffolding that only made an intermediate fork shape
  green — for instance burn-log expectations that disappear once EIP-8246
  joins EIP-7708 on the same fork.

## Definition of Done

- The fork's MRO holds mixins then `<MONAD_PARENT>`, no `<ETH_FORK>`.
- Gas constants match `<MONAD_PARENT>` apart from those the EIP changes,
  which have; unadopted EIPs report disabled.
- `<MONAD_FORK> > <ETH_FORK>` holds through `follows`.
- The adopted EIP's `<eth_fork>` suite is selected for the fork, and no
  unadopted `<eth_fork>` suite fails.
- Full-suite fill green for the fork.
- Every touched file outside `src/ethereum/forks/monad*` and
  `tests/monad*` is either permanent or scaffolding that is load-bearing
  here and listed for cleanup — nothing kept for symmetry alone.
- Each shared file the branch touches shows added lines rather than
  reshaped ones, and no central plumbing, abstract method or upstream EIP
  definition among them.
