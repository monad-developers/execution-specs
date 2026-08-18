#!/usr/bin/env bash
# Fill once, consume RUNS times, emit the NINE-vs-TEN perf table.
set -uo pipefail

TAG="${TAG:?set TAG (names all artifacts, e.g. TAG=v4)}"
RUNS="${RUNS:-7}"
REPEATS="${REPEATS:-20}"
# Perf runs time full 200M blocks; the test default is a small block.
BLOCK_GAS="${MIP8_PERF_BLOCK_GAS:-200000000}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HARNESS="${HARNESS:-$REPO/../monad-eest-rust-harness}"
BIN="${BIN:-$HARNESS/bin/eest-runner}"
TEST="${TEST:-tests/monad_ten/mip8_pageified_storage/test_perf_regression.py}"

cd "$REPO"
EPOCH="$(date -u +%s)"
NOW="$(date -u -d "@$EPOCH" '+%Y-%m-%dT%H:%M:%SZ')"
STAMP="$(date -u -d "@$EPOCH" '+%y%m%d_%H%M%S')"
FIX="../fixtures_${TAG}"
PREFIX="../timing_${TAG}_${STAMP}"
TABLE="${PREFIX}_table"
[ -x "$BIN" ] || { echo "harness not executable: $BIN" >&2; exit 1; }

if [ -z "${SKIP_FILL:-}" ]; then
  echo "=== fill $FIX (REPEATS=$REPEATS, BLOCK_GAS=$BLOCK_GAS) $NOW ==="
  MIP8_PERF_REPEATS="$REPEATS" MIP8_PERF_BLOCK_GAS="$BLOCK_GAS" \
      uv run fill -m blockchain_test "$TEST" \
      --from MONAD_NINE --until MONAD_TEN --chain-id 30143 --monad-runloop \
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
  uv run consume direct --input "$FIX" --bin "$BIN" \
      --timing-report --timing-report-dir "$out"
  [ -f "$out/timing_consume.csv" ] || {
    echo "consume run $i left no $out/timing_consume.csv (eest-runner" \
         "emitted no parseable __exec_block timing lines?)" >&2
    exit 1
  }
done

sha() { git -C "$1" rev-parse --short HEAD 2>/dev/null || echo '?'; }
uv run perf_regression --md "${TABLE}.md" --html "${TABLE}.html" \
    --now "$NOW" \
    --repo "$(sha "$REPO")" \
    --harness "$(sha "$HARNESS")" \
    --monad-bft "$(sha "$HARNESS/monad-bft")" \
    --monad "$(sha "$HARNESS/monad-bft/monad-execution")" \
    "${PREFIX}"_[0-9]* || { echo "perf_regression failed, no table" >&2; exit 1; }
echo "=== table: $(cd .. && pwd)/$(basename "$TABLE").html ==="
