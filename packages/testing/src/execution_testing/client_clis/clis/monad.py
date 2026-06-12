"""
Monad `eest-runner` fixture consumer.

Executes blockchain fixtures against the production monad runloop via
the `eest-runner` harness binary (built from monad-bft + monad
execution, EestNet chain). The fixture is digested into a simple input
document (genesis allocation + per-block timestamp/base fee/beneficiary
and raw signed transactions); the harness drives the consensus ledger
and the runloop, then emits the resulting post-state, which is compared
against the fixture's `postState`.
"""

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from execution_testing.fixtures import BlockchainFixture, FixtureFormat
from execution_testing.test_types import Transaction

from ..fixture_consumer_tool import FixtureConsumerTool

# Monad revision schedule per fixture `network`, as
# (monad_revision, activation timestamp) pairs. Non-transition
# fixtures run a single revision from genesis; transition fixtures
# activate at the EEST transition fork's boundary.
FORK_REVISION_SCHEDULES = {
    "MONAD_EIGHT": [(8, 0)],
    "MONAD_NINE": [(9, 0)],
    "MONAD_EIGHTToMONAD_NINEAtTime15k": [(8, 0), (9, 15_000)],
}


def _hex32(value: str) -> str:
    """Normalize a hex quantity to a 0x-prefixed 64-nibble word."""
    return f"0x{int(value, 16):064x}"


def _digest_genesis_alloc(pre: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a fixture `pre` alloc into the harness genesis alloc."""
    alloc: Dict[str, Any] = {}
    for address, account in pre.items():
        entry: Dict[str, Any] = {
            "wei_balance": account.get("balance", "0x0"),
            "nonce": account.get("nonce", "0x0"),
        }
        code = account.get("code", "0x")
        if code and code != "0x":
            entry["code"] = code
        storage = {
            _hex32(slot): _hex32(value)
            for slot, value in account.get("storage", {}).items()
            if int(value, 16) != 0
        }
        if storage:
            entry["storage"] = storage
        alloc[address] = entry
    return alloc


def _digest_blocks(fixture: Dict[str, Any]) -> list:
    """Convert fixture blocks into the harness block list."""
    blocks = []
    for block in fixture["blocks"]:
        header = block["blockHeader"]
        txs = []
        for tx_json in block.get("transactions", []):
            for auth in tx_json.get("authorizationList", []):
                # Fixtures serialize both `v` and `yParity`; the
                # Transaction model accepts only one of them.
                if "v" in auth:
                    auth.pop("yParity", None)
            tx = Transaction.model_validate(tx_json)
            txs.append(tx.rlp().hex())
        blocks.append(
            {
                "timestamp": int(header["timestamp"], 16),
                "base_fee": int(header["baseFeePerGas"], 16),
                "beneficiary": header["coinbase"],
                "txs": txs,
            }
        )
    return blocks


def _compare_account(
    address: str, expected: Dict[str, Any], actual: Optional[Dict[str, Any]]
) -> list:
    """Compare one expected post-state account; return mismatch strings."""
    mismatches = []
    if actual is None:
        actual = {}
    for field in ("balance", "nonce"):
        expected_value = int(expected.get(field, "0x0"), 16)
        actual_value = int(actual.get(field, "0x0"), 16)
        if expected_value != actual_value:
            mismatches.append(
                f"{address}: {field} expected {hex(expected_value)}, "
                f"got {hex(actual_value)}"
            )
    expected_code = expected.get("code", "0x") or "0x"
    actual_code = actual.get("code", "0x") or "0x"
    if expected_code.lower() != actual_code.lower():
        mismatches.append(
            f"{address}: code expected {expected_code}, got {actual_code}"
        )
    actual_storage = {
        int(slot, 16): int(value, 16)
        for slot, value in actual.get("storage", {}).items()
    }
    expected_storage = {
        int(slot, 16): int(value, 16)
        for slot, value in expected.get("storage", {}).items()
    }
    for slot, expected_value in expected_storage.items():
        actual_value = actual_storage.get(slot, 0)
        if expected_value != actual_value:
            mismatches.append(
                f"{address}: storage[{hex(slot)}] expected "
                f"{hex(expected_value)}, got {hex(actual_value)}"
            )
    for slot, actual_value in actual_storage.items():
        if slot not in expected_storage and actual_value != 0:
            mismatches.append(
                f"{address}: unexpected storage[{hex(slot)}] = "
                f"{hex(actual_value)}"
            )
    return mismatches


class MonadFixtureConsumer(
    FixtureConsumerTool,
    fixture_formats=[BlockchainFixture],
):
    """Monad's `eest-runner` fixture consumer for blockchain tests."""

    default_binary = Path("eest-runner")
    detect_binary_pattern = re.compile(r"^eest-runner\b")
    version_flag: str = "--version"

    def __init__(
        self,
        binary: Optional[Path] = None,
        trace: bool = False,
    ):
        """Initialize the MonadFixtureConsumer."""
        super().__init__(binary=binary)
        self.trace = trace

    def _init_triedb(self, db_path: Path) -> None:
        """Create and format a fresh triedb file via `monad-mpt`."""
        monad_mpt = self.binary.parent / "monad-mpt"
        with open(db_path, "wb") as f:
            f.truncate(8 * 1024**3)
        subprocess.run(
            [str(monad_mpt), "--storage", str(db_path), "--create"],
            capture_output=True,
            text=True,
            check=True,
        )

    def consume_fixture(
        self,
        fixture_format: FixtureFormat,
        fixture_path: Path,
        fixture_name: Optional[str] = None,
        debug_output_path: Optional[Path] = None,
    ) -> None:
        """Execute a blockchain fixture on the monad runloop and verify."""
        assert fixture_format == BlockchainFixture

        with open(fixture_path) as f:
            fixtures = json.load(f)
        if fixture_name is None:
            assert len(fixtures) == 1, "fixture_name required"
            fixture_name = next(iter(fixtures))
        fixture = fixtures[fixture_name]

        network = fixture["network"]
        assert network in FORK_REVISION_SCHEDULES, (
            f"no monad revision schedule for network {network}"
        )
        if any("expectException" in block for block in fixture["blocks"]):
            pytest.skip("invalid blocks cannot be proposed through the ledger")
        input_doc = {
            "genesis_alloc": _digest_genesis_alloc(fixture["pre"]),
            "genesis_rlp": fixture["genesisRLP"],
            "revision_schedule": [
                {"revision": revision, "from_timestamp": from_timestamp}
                for revision, from_timestamp in FORK_REVISION_SCHEDULES[
                    network
                ]
            ],
            "blocks": _digest_blocks(fixture),
        }

        with tempfile.TemporaryDirectory(prefix="eest-runner-") as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "input.json"
            output_path = tmp_path / "output.json"
            ledger_dir = tmp_path / "ledger"
            ledger_dir.mkdir()
            db_path = tmp_path / "triedb"
            self._init_triedb(db_path)

            input_path.write_text(json.dumps(input_doc, indent=2))

            result = subprocess.run(
                [
                    str(self.binary),
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                    "--ledger-dir",
                    str(ledger_dir),
                    "--db",
                    str(db_path),
                ],
                capture_output=True,
                text=True,
            )

            if debug_output_path is not None:
                debug_output_path.mkdir(parents=True, exist_ok=True)
                (debug_output_path / "input.json").write_text(
                    input_path.read_text()
                )
                (debug_output_path / "stdout.txt").write_text(result.stdout)
                (debug_output_path / "stderr.txt").write_text(result.stderr)
                if output_path.exists():
                    (debug_output_path / "output.json").write_text(
                        output_path.read_text()
                    )

            if result.returncode != 0:
                raise Exception(
                    f"eest-runner failed (exit {result.returncode}):\n"
                    f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
                )

            output = json.loads(output_path.read_text())

        actual_post = {
            address.lower(): account
            for address, account in output["post_state"].items()
        }
        post_state = fixture.get("postState")
        assert post_state is not None, (
            "fixture has no postState (hash-only fixtures not supported)"
        )

        mismatches = []
        for address, expected in post_state.items():
            actual = actual_post.get(address.lower())
            mismatches.extend(_compare_account(address, expected, actual))

        if mismatches:
            raise Exception(
                "post-state mismatch on the monad runloop:\n"
                + "\n".join(mismatches)
            )
