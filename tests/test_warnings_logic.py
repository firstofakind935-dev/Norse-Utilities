import pytest
from cogs.warnings import _get_strike_level, _threshold_crossed, _parse_expires_at
from datetime import datetime, timezone


# --- _get_strike_level ---

def test_strike_level_zero_at_zero_warns():
    assert _get_strike_level(0) == 0

def test_strike_level_zero_below_first_threshold():
    assert _get_strike_level(2) == 0

def test_strike_level_one_at_threshold():
    assert _get_strike_level(3) == 1

def test_strike_level_one_between_thresholds():
    assert _get_strike_level(5) == 1

def test_strike_level_two_at_threshold():
    assert _get_strike_level(6) == 2

def test_strike_level_two_between_thresholds():
    assert _get_strike_level(7) == 2

def test_strike_level_three_at_threshold():
    assert _get_strike_level(8) == 3

def test_strike_level_three_above_threshold():
    assert _get_strike_level(10) == 3


# --- _threshold_crossed ---

def test_no_threshold_crossed_below_first():
    assert _threshold_crossed(0, 2) is None

def test_no_threshold_crossed_between_thresholds():
    assert _threshold_crossed(3, 5) is None

def test_threshold_crossed_strike_1():
    assert _threshold_crossed(2, 3) == 1

def test_threshold_crossed_strike_2():
    assert _threshold_crossed(5, 6) == 2

def test_threshold_crossed_strike_3():
    assert _threshold_crossed(7, 8) == 3

def test_threshold_crossed_when_count_jumps():
    # warn count going from 2 to 4 still crosses the 3-warn threshold
    assert _threshold_crossed(2, 4) == 1

def test_no_threshold_when_already_past():
    # already at 3, adding one more (4) does not cross a new threshold
    assert _threshold_crossed(3, 4) is None


# --- _parse_expires_at ---

def test_parse_expires_at_days():
    result = _parse_expires_at(3, "days")
    assert result is not None
    dt = datetime.fromisoformat(result)
    assert dt > datetime.now(timezone.utc)

def test_parse_expires_at_hours():
    result = _parse_expires_at(1, "hours")
    assert result is not None
    dt = datetime.fromisoformat(result)
    assert dt > datetime.now(timezone.utc)

def test_parse_expires_at_weeks():
    result = _parse_expires_at(2, "weeks")
    assert result is not None
    dt = datetime.fromisoformat(result)
    assert dt > datetime.now(timezone.utc)

def test_parse_expires_at_unknown_unit_returns_none():
    assert _parse_expires_at(1, "minutes") is None

def test_parse_expires_at_unknown_unit_2():
    assert _parse_expires_at(5, "years") is None
