from __future__ import annotations

import pytest

from hoops.leagues import RULES, League, has_capability

NBA = RULES[League.NBA]
NCAA = RULES[League.NCAAM]


@pytest.mark.parametrize(
    ("rules", "period", "clock", "lead", "expected"),
    [
        (NBA, 4, 5 * 60, 25, True),     # 25-point lead, 5:00 left
        (NBA, 4, 6 * 60, 25, True),     # exactly 6:00 left counts
        (NBA, 4, 6 * 60 + 1, 30, False),  # 6:01 left: too early
        (NBA, 4, 5 * 60, 24, False),    # 24 points with 5:00 left: not enough
        (NBA, 4, 3 * 60, 15, True),     # 15-point lead, 3:00 left
        (NBA, 4, 2 * 60, -18, True),    # away team leading counts too
        (NBA, 4, 3 * 60 + 1, 15, False),
        (NBA, 3, 60, 40, False),        # never before the final period
        (NBA, 5, 60, 25, False),        # never in overtime
        (NCAA, 2, 5 * 60, 25, True),    # college: second half
        (NCAA, 1, 60, 30, False),
    ],
)
def test_garbage_time(rules, period, clock, lead, expected):
    assert rules.is_garbage_time(period, clock, lead) is expected


def test_seconds_left_in_regulation():
    assert NBA.seconds_left_in_regulation(1, 720) == 48 * 60
    assert NBA.seconds_left_in_regulation(4, 30) == 30
    assert NBA.seconds_left_in_regulation(5, 200) == 0
    assert NCAA.seconds_left_in_regulation(1, 600) == 30 * 60
    assert NCAA.regulation_seconds == NBA.regulation_seconds - 8 * 60


def test_capabilities():
    assert has_capability(League.NBA, "injury_feed")
    assert not has_capability(League.NCAAM, "injury_feed")
    assert has_capability(League.NCAAM, "manual_absences")
