"""
Executes a JSON test fixture directly against a client using a dedicated client
interface similar to geth's EVM 'blocktest' command.
"""

from pathlib import Path
from typing import Callable

from execution_testing.cli.pytest_commands.plugins.consume.direct.timing_report import (  # noqa: E501
    TIMING_PROPERTY,
)
from execution_testing.fixtures import FixtureConsumer
from execution_testing.fixtures.consume import (
    TestCaseIndexFile,
    TestCaseStream,
)


def test_fixture(
    test_case: TestCaseIndexFile | TestCaseStream,
    fixture_consumer: FixtureConsumer,
    fixture_path: Path,
    test_dump_dir: Path | None,
    record_property: Callable[[str, object], None],
) -> None:
    """
    Generic test function used to call the fixture consumer with a given
    fixture file path and a fixture name (for a single test run).
    """
    block_timings = fixture_consumer.consume_fixture(
        test_case.format,
        fixture_path,
        fixture_name=test_case.id,
        debug_output_path=test_dump_dir,
    )
    if block_timings:
        record_property(
            TIMING_PROPERTY,
            {"id": test_case.id, "blocks": [dict(t) for t in block_timings]},
        )
