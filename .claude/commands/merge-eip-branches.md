# Merge EIP Adoption Branches

Consolidate several single-EIP adoption branches into one fork that adopts
all of them. Run this skill before starting such a merge. Each source
branch was built by `/adopt-upstream-eip`, alone, against its own upstream
EIP branch, so no source branch knows what the combined fork does.

## Inputs

- The source branches, and `<MONAD_FORK>` they all target.
- `<MONAD_BRANCH>`, the branch they merge into. Example: `forks/monad_nine`.
- `<ETH_FORK>` and its upstream integration branch, which already has every
  EIP composed. Example: `upstream/forks/amsterdam`.

## Read First

`/adopt-upstream-eip` defines the fork shape, the shared-file cost rules,
the step-12 cleanup list and the definition of done. This skill only covers
what the merge adds.

## 1. Prepare

```bash
git worktree add -b <merge_branch> <path> origin/<MONAD_BRANCH>
git branch --unset-upstream <merge_branch>
```

Merge into a fresh worktree, and never check out or rewrite a source
branch. Note which commits already landed on `<MONAD_BRANCH>` after the
source branches were cut: a branch carrying its own copy of one of them
conflicts, and `<MONAD_BRANCH>`'s version wins.

```bash
git log --oneline $(git merge-base origin/<MONAD_BRANCH> <branch>)..origin/<MONAD_BRANCH>
```

## 2. Merge

Merge the largest branch first, then the rest, with real merge commits.
Squashing or cherry-picking hides which branch each conflict came from.

```bash
git merge <branch>          # once per source branch
```

## 3. Resolve the Conflicts Git Reports

Expect these, in rising order of thought needed:

- **`.github/configs/feature.yaml`** — every branch appends its own entry
  at the same point. Keep all entries for now; step 5 collapses them.
- **`forks.py`, the `<MONAD_FORK>` declaration** — each branch declares the
  class with one mixin. The merged class lists every mixin ahead of the
  parent fork, in ascending EIP order. Drop any dead `pass` left after
  `follows()`.
- **CI files already on `<MONAD_BRANCH>`** — keep the base branch's version.
- **`src/ethereum/forks/<monad_fork>/`** — two EIPs changing the same file.
  Resolve against the upstream integration branch, not against either
  branch alone.

What must merge silently: the `follows()` trait, the
`BaseForkMeta._is_subclass_of` walk, the `ValidAtTransitionTo` guard and the
exclusion conftests. They are byte-identical across branches by design.
Check afterwards that one copy of each survived.

## 4. Find the Silent Merges

This is the main risk. Two branches editing the same file at different
lines merge cleanly and produce behavior neither branch had. Git reports
nothing.

Find the candidates from the upstream spec changes:

```bash
for n in <eips>; do
  base=$(git merge-base <upstream_integration> upstream/eips/<eth_fork>/eip-$n)
  echo "### EIP-$n"
  git diff --stat $base..upstream/eips/<eth_fork>/eip-$n -- src/ethereum/forks/<eth_fork>
done
```

Any file listed under two EIPs needs reading in full on the merge branch.
Where one EIP removes what another adds, the merged file must match the
upstream integration branch, which already has both.

Two shapes seen in practice:

- One EIP adds a log for a state change a later EIP removes. Git keeps
  both: a log for something that no longer happens.
- Two branches add the same conftest hook at different offsets, one after
  the imports and one appended. Git keeps both hook definitions in one
  module.

Also read every file the merge touched outside `src/ethereum/forks/monad*`
and `tests/monad*`, whether or not git flagged it.

## 5. Clean Up the Scaffolding

Follow `/adopt-upstream-eip` step 12, driven by what the merged fork now
adopts:

- Delete the exclusion conftest for each adopted EIP's suite. Those suites
  must now be selected and pass.
- Collapse the per-branch feature entries into one. When the branch name
  follows `eips/<fork>/eip-<n>+<n>`, the feature name must carry the same
  numbers in the same order: `check_release_matrix.py` matches the sequence
  as a literal string. Verify it:

  ```bash
  python3 .github/scripts/check_release_matrix.py "" <merge_branch_name>
  ```

- Revert expectation scaffolding written for an intermediate fork shape. A
  test edit that only made a fork with EIP-A but without EIP-B green is
  scaffolding once B joins.

To test whether an edit was scaffolding, revert it and diff against the
base branch:

```bash
git revert --no-commit <sha> && git diff origin/<MONAD_BRANCH> -- <file>
```

An empty diff means the edit existed only for the intermediate shape.
Reverting cleanly needs the source branch to have kept scaffolding in its
own commit; a commit mixing scaffolding with permanent change has to be
undone by hand.

Keep gates on a capability (`fork.is_eip_enabled(<n>)`, `fork >=
MONAD_EIGHT`) that stay true on the merged fork. They are permanent.

## 6. Validate

Run `/adopt-upstream-eip`'s step-3 shape check, its step-5 `--collect-only`
selection check, then the full-suite fill for the fork. The full suite
matters: the merge changes which suites select the fork.

Measure any exclusion that survived the cleanup, one suite at a time, by
stripping its hook and filling that suite. State which ones you did not get
to measure.

## Definition of Done

- Every source branch merged, with the merge commits kept.
- Each file two EIPs touched read in full and matched against the upstream
  integration branch.
- One copy of each shared addition; no duplicated hook or trait.
- Adopted suites selected and passing; their exclusions deleted.
- One feature entry, its name matching the branch's EIP sequence.
- Full-suite fill green, with any unmeasured exclusion named.
