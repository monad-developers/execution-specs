"""Tests for the monad consumer's fixture selection."""

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from execution_testing.client_clis.clis.monad import _iter_fixtures


def _write(path: Path, fixtures: Dict[str, Any]) -> Path:
    """Write a fixture file holding the given fixtures."""
    path.write_text(json.dumps(fixtures))
    return path


def test_named_fixture_is_the_only_one_yielded(tmp_path: Path) -> None:
    """A name selects one fixture out of a file holding several."""
    path = _write(
        tmp_path / "f.json",
        {"first": {"network": "A"}, "second": {"network": "B"}},
    )

    assert list(_iter_fixtures(path, "second")) == [{"network": "B"}]


def test_no_name_yields_every_fixture(tmp_path: Path) -> None:
    """
    Without a name the whole file is run.

    Fixture verification hands over a merged file and expects all of it
    checked, so yielding only the first would leave the rest unverified.
    """
    path = _write(
        tmp_path / "f.json",
        {"a": {"network": "A"}, "b": {"network": "B"}, "c": {"network": "C"}},
    )

    assert list(_iter_fixtures(path, None)) == [
        {"network": "A"},
        {"network": "B"},
        {"network": "C"},
    ]


def test_no_name_on_a_sole_fixture(tmp_path: Path) -> None:
    """A single-fixture file yields its one fixture."""
    path = _write(tmp_path / "f.json", {"only": {"network": "A"}})

    assert list(_iter_fixtures(path, None)) == [{"network": "A"}]


def test_missing_name_raises(tmp_path: Path) -> None:
    """A name absent from the file is an error, not an empty result."""
    path = _write(tmp_path / "f.json", {"only": {"network": "A"}})

    with pytest.raises(KeyError, match="absent"):
        list(_iter_fixtures(path, "absent"))


def test_named_lookup_stops_at_the_match(tmp_path: Path) -> None:
    """The stream is not read past the fixture that was asked for."""
    path = _write(
        tmp_path / "f.json",
        {"first": {"network": "A"}, "second": {"network": "B"}},
    )

    fixtures = _iter_fixtures(path, "first")

    assert next(fixtures) == {"network": "A"}
    with pytest.raises(StopIteration):
        next(fixtures)
