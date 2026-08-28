#!/usr/bin/env bash
# Fill once, consume RUNS times, emit the NINE-vs-TEN perf table.
set -euo pipefail

TAG="${TAG:?set TAG (names all artifacts, e.g. TAG=v4)}"
# The table pairs runs and adjusts for its own test count, so no raw
# p-value can fall below 2/2^RUNS. At the table's current size 13 is the
# fewest that lets an isolated effect clear the 5% threshold; below it
# only measures that move together reach significance (the table says so
# when it applies).
RUNS="${RUNS:-13}"
REPEATS="${REPEATS:-20}"
# Block gas budget in millions, as --gas-benchmark-values takes it. The
# runloop stamps every monad block at 200M.
BLOCK_GAS_M="${BLOCK_GAS_M:-200}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HARNESS="${HARNESS:-$REPO/../monad-eest-rust-harness}"
BIN="${BIN:-$HARNESS/bin/eest-runner}"
TEST="${TEST:-tests/benchmark/stateful/mip8_pageified_storage/test_perf_regression.py}"

cd "$REPO" || exit 1
EPOCH="$(date -u +%s)"
NOW="$(date -u -d "@$EPOCH" '+%Y-%m-%dT%H:%M:%SZ')"
STAMP="$(date -u -d "@$EPOCH" '+%y%m%d_%H%M%S')"
FIX="../fixtures_${TAG}"
PREFIX="../timing_${TAG}_${STAMP}"
TABLE="${PREFIX}_table"
[ -x "$BIN" ] || { echo "harness not executable: $BIN" >&2; exit 1; }

if [ -z "${SKIP_FILL:-}" ]; then
  echo "=== fill $FIX (REPEATS=$REPEATS, ${BLOCK_GAS_M}M blocks) $NOW ==="
  MIP8_PERF_REPEATS="$REPEATS" \
      uv run fill -m blockchain_test "$TEST" \
      --from MONAD_NINE --until MONAD_TEN --chain-id 30143 --monad-runloop \
      --gas-benchmark-values "$BLOCK_GAS_M" \
      --output "$FIX" -n auto || {
    echo "fill failed (a non-empty $FIX aborts fill); rerun with" \
         "SKIP_FILL=1 to reuse it, or delete it to refill" >&2
    exit 1
  }
fi

for i in $(seq 1 "$RUNS"); do
  out="${PREFIX}_${i}"
  rm -rf "$out"
  echo "=== consume $i/$RUNS -> $out $(date -u +%T)Z ==="
  # A failing fixture must not silently drop its case from the table.
  uv run consume direct --input "$FIX" --bin "$BIN" \
      --timing-report --timing-report-dir "$out" || {
    echo "consume run $i reported failures; the table would silently omit" \
         "the affected cases. Fix them or drop them from $TEST." >&2
    exit 1
  }
  [ -f "$out/timing_consume.csv" ] || {
    echo "consume run $i left no $out/timing_consume.csv (eest-runner" \
         "emitted no parseable __exec_block timing lines?)" >&2
    exit 1
  }
done

sha() { git -C "$1" rev-parse --short HEAD 2>/dev/null || echo '?'; }
uv run perf_regression --md "${TABLE}.md" \
    --now "$NOW" \
    --repo "$(sha "$REPO")" \
    --harness "$(sha "$HARNESS")" \
    --monad-bft "$(sha "$HARNESS/monad-bft")" \
    --monad "$(sha "$HARNESS/monad-bft/monad-execution")" \
    "${PREFIX}"_[0-9]* || { echo "perf_regression failed, no table" >&2; exit 1; }
echo "=== table: $(cd .. && pwd)/$(basename "$TABLE").md ==="
