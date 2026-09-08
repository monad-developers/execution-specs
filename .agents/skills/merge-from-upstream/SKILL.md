---
name: merge-from-upstream
description: Merge upstream execution-specs into the fork's from-upstream branch.
---

# Merge From Upstream

Merge the current default branch of `ethereum/execution-specs` into the
`from-upstream` branch of `monad-developers/execution-specs`, carry the
applicable changes into the Monad forks, and verify the result. Run this
skill before starting such work.

The commit sequence is the deliverable as much as the merged tree is: a
reviewer reads the conflict resolution and the Monad propagation as
separate diffs, so never fold them into the merge commit or into each
other.

## 1. Prepare the branch

- Check out `from-upstream`.
- Verify it is not the head branch of a currently open PR.
- Reset it to the `monad-developers/execution-specs` default branch.

## 2. Record the fixture set baseline

Collect the tests the `tests-monad` release job fills, with
`--collect-only`, and save the result for the closing comparison. The
fill parameters live under the `monad` key in
`.github/configs/feature.yaml`; `.github/actions/build-fixtures` and the
`fill-release` recipe in the `Justfile` add the rest.

The `N/M tests collected` summary counts items before the filler drops
the ones that generate no fixture for the fork range, so it overstates
the release set. Take the node IDs, not that number.

## 3. Merge

- Fetch the upstream remote.
- Merge upstream's default branch as a merge commit, **leaving the
  conflicts unresolved in the merge commit**.
- Stage the conflicted paths explicitly. Staging everything would also
  commit untracked directories left behind by other branches.

## 4. Resolve the conflicts

Put the resolution in a separate commit after the merge commit.

## 5. Propagate into the Monad forks

Analyze the changes upstream made to its own forks (Amsterdam, Osaka,
Prague and the rest). Filter them to those that apply to the `MONAD_*`
forks inheriting those upstream forks, and apply those to the WET
implementations in `src/ethereum/forks`.

These files do not conflict, so nothing flags them. They still have to
accommodate the upstream change whenever it applies — for instance
because the Monad fork adopted the same EIP.

Do these changes in a further commit.

## 6. Stop for review

If anything about the changes so far is doubtful, stop and request human
review before verifying.

## 7. Verify

Lint, then fill. Base the fill on the `tests-monad` release command and
pass `--maxfail 1` so breakage surfaces early. Fill fine-grained first,
over the tests related to the touched files and features, then sweep the
full set.

A full sweep runs for hours. Split it into chunks that partition the
collected set, so no single run is long enough to be interrupted, and
check the chunk totals add up to the collected count.

## 8. Fix what the verification finds

Use follow-up commits: fold related changes together, keep unrelated
changes separate.

## 9. Report the fixture set change

Collect the release tests again with `--collect-only`, compare against
the step 2 baseline, and report the change in the resulting fixture set.

## 10. Stop for review

Once verification succeeds, stop and request human review.
