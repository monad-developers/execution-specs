#!/usr/bin/env bash
# Build the eest-runner harness inside the monad-builder container and
# sync ALL artifacts (binary + shared libraries + monad-mpt) into
# install/, which the bin/ wrappers run from. Always use this script to
# rebuild — copying only the binary leaves a stale libmonad_execution.so.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "$ROOT/monad-bft/Cargo.toml" ] ||
   [ ! -f "$ROOT/monad-bft/monad-execution/CMakeLists.txt" ]; then
    echo "error: the monad stack is not checked out. Run:" >&2
    echo "  ./monad-runloop/init-stack.sh" >&2
    exit 1
fi

# Advisory only: a submodule at a commit other than its gitlink is a
# normal way to work on monad locally, so say so and carry on.
REPO="$(git -C "$ROOT" rev-parse --show-toplevel 2>/dev/null || true)"
if [ -n "$REPO" ] &&
   git -C "$REPO" submodule status --recursive monad-runloop/monad-bft \
       2>/dev/null | grep -q '^+'; then
    echo "note: the monad stack is not at its pinned gitlink" >&2
    git -C "$REPO" submodule status --recursive monad-runloop/monad-bft \
        2>/dev/null | grep '^+' >&2 || true
fi

docker run --rm -v "$ROOT":/work -w /work/rust-harness \
    -e CC=gcc-15 -e CXX=g++-15 \
    -e ASMFLAGS=-march=haswell -e CFLAGS=-march=haswell \
    -e CXXFLAGS=-march=haswell \
    -e TRIEDB_TARGET=triedb_driver -e RUSTFLAGS="-A warnings" \
    monad-builder:latest bash -c '
set -e
git config --global --add safe.directory "*"
cargo build --locked
mkdir -p /work/install/bin /work/install/lib
cp target/debug/eest-runner /work/install/bin/eest-runner.real
BUILD_DIR=$(ls -d target/debug/build/monad-cxx-*/out/build | head -1)
cmake --build $BUILD_DIR --target monad-mpt -j8 >/dev/null
cp $(find $BUILD_DIR -name monad-mpt -type f | head -1) \
    /work/install/bin/monad-mpt.real
find target/debug/build -name "*.so*" -exec cp -P {} /work/install/lib/ \;
echo "build + install sync done"
'
