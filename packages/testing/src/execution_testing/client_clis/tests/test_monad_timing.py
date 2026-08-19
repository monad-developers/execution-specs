"""Tests for the monad consumer's `__exec_block` timing parsing."""

import pytest

from execution_testing.client_clis.clis.monad import (
    _duration_us,
    _parse_block_timings,
)


@pytest.mark.parametrize(
    "value, us",
    [
        ("5745us", 5745),
        ("5745µs", 5745),
        ("5745μs", 5745),
        (" 5.745 ms ", 5745),
        ("1200ns", 1),
        ("0.013081s", 13081),
    ],
)
def test_duration_units(value: str, us: int) -> None:
    """Durations convert to integer microseconds whatever the unit."""
    assert _duration_us(value) == us


@pytest.mark.parametrize(
    "value",
    ["", "13081", "5745 sec", "5745usx", "-5us", "5.us", "fast", "us"],
)
def test_duration_unrecognized(value: str) -> None:
    """An unrecognized duration format raises instead of guessing."""
    with pytest.raises(ValueError, match="unrecognized duration"):
        _duration_us(value)


def _line(suffix: str) -> str:
    """Return a realistic runloop `__exec_block` log line."""
    return (
        "2026-07-16 12:45:21.263419673 [7] runloop_monad.cpp:385 LOG_INFO"
        "    __exec_block,bl=       1,id=0x1433,ts=1784205921250,"
        f"tx=    7,rt=   0,rtp= 0.00%,sr= 5745{suffix},"
        f"txe=  6991{suffix},cmt=   169{suffix},tot= 13081{suffix},"
        "tpse=  143,tps=   76,gas= 10000000,gpse=1430,gps=764,ae=   2,"
        "ane=   0,sz=   5,snz=   0,ac=       9,sc=       5 /       1872"
    )


@pytest.mark.parametrize("suffix", ["us", "µs"])
def test_duration_suffix(suffix: str) -> None:
    """The duration unit varies with the runloop's toolchain."""
    rows = _parse_block_timings(f"noise\n{_line(suffix)}\n")
    assert rows == [
        {
            "block": 1,
            "tx_count": 7,
            "gas": 10_000_000,
            "retries": 0,
            "tx_exec_us": 6991,
            "state_root_us": 5745,
            "commit_us": 169,
            "total_us": 13081,
        }
    ]


def test_malformed_line_logged(caplog: pytest.LogCaptureFixture) -> None:
    """A malformed line is skipped and reported, not silently dropped."""
    line = "LOG_INFO    __exec_block,bl=       1,tx=    7,gas=oops"
    with caplog.at_level("ERROR"):
        assert _parse_block_timings(line) == []
    assert "unparsable __exec_block line" in caplog.text
    assert "gas=oops" in caplog.text
