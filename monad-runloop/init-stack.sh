#!/usr/bin/env bash
# Check out the monad stack the harness builds against: monad-bft, its
# monad-execution submodule, and the third_party submodules the CMake
# build needs.
#
# monad declares 23 third_party submodules, 3.3 GB, not all needed.
#
# SKIP is a denylist on purpose. A submodule the build turns out to need
# fails as a confusing CMake error deep into the build, while an extra
# one only costs disk, so anything monad adds later is checked out by
# default and only entries verified unreferenced are listed here.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
REPO="$(git -C "$ROOT" rev-parse --show-toplevel)"
EXEC="$ROOT/monad-bft/monad-execution"

SKIP=(
    third_party/ethereum-tests
    third_party/yellowpaper
    third_party/zkevm-standards
)

git -C "$REPO" submodule update --init monad-runloop/monad-bft
git -C "$ROOT/monad-bft" submodule update --init monad-execution

# Guard the denylist against upstream drift. The scope mirrors what the
# top-level CMakeLists descends into, which excludes zkvm/ — that is why
# zkevm-standards can be skipped even though zkvm/guest references it.
guard_scope=("$EXEC/CMakeLists.txt" "$EXEC/category" "$EXEC/cmd")
for path in "${SKIP[@]}"; do
    if grep -rlq --include=CMakeLists.txt --include='*.cmake' \
        -- "$(basename "$path")" "${guard_scope[@]}" 2>/dev/null; then
        echo "error: $path is on the skip list but the build references" >&2
        echo "       it. Remove it from SKIP in $(basename "$0")." >&2
        exit 1
    fi
done

mapfile -t wanted < <(
    git -C "$EXEC" config --file .gitmodules --get-regexp '\.path$' |
        awk '{print $2}' |
        grep -vxF -f <(printf '%s\n' "${SKIP[@]}")
)

git -C "$EXEC" submodule update --init --recursive -- "${wanted[@]}"

echo "monad stack ready: $(git -C "$ROOT/monad-bft" rev-parse --short HEAD)" \
     "+ monad-execution $(git -C "$EXEC" rev-parse --short HEAD)," \
     "${#wanted[@]} of $((${#wanted[@]} + ${#SKIP[@]})) third_party submodules"
