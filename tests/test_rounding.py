"""The billing rounding rule.

This is the single most important calculation in the application, and the
one the user cannot check by reading code. Every case he described in his
own words appears here verbatim as a test.
"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from app.core.calc import (
    round_hours,
    round_seconds_to_hours,
    seconds_to_hours,
)
from app.core.models import RoundingDirection, RoundingRule

UP = RoundingRule()  # 0.25, up - the defaults from the brief


def minutes(count: int) -> int:
    return count * 60


class TestExactBoundaries:
    """A total landing exactly on a quarter must stay put.

    "60 minutes is 1.00, not 1.25" - this is the failure mode that would
    quietly over-bill every single day.
    """

    @pytest.mark.parametrize(
        ("mins", "expected"),
        [
            (0, "0.00"),
            (15, "0.25"),
            (30, "0.50"),
            (45, "0.75"),
            (60, "1.00"),
            (75, "1.25"),
            (90, "1.50"),
            (180, "3.00"),
            (480, "8.00"),
            (1440, "24.00"),
        ],
    )
    def test_exact_quarters_do_not_move(self, mins, expected):
        assert round_seconds_to_hours(minutes(mins), UP) == Decimal(expected)

    def test_a_day_of_exactly_three_hours_bills_three(self):
        """Acceptance checklist 13: 3h 00m -> 3.00, not 3.25."""
        assert round_seconds_to_hours(minutes(180), UP) == Decimal("3.00")

    def test_a_day_of_three_hours_one_minute_bills_three_and_a_quarter(self):
        """Acceptance checklist 13: 3h 01m -> 3.25."""
        assert round_seconds_to_hours(minutes(181), UP) == Decimal("3.25")


class TestOneSecondOverTheLine:
    """A ceiling must react to a single second, or it is not a ceiling."""

    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (3600, "1.00"),
            (3601, "1.25"),
            (900, "0.25"),
            (901, "0.50"),
            (899, "0.25"),
            (1, "0.25"),
            (10800, "3.00"),
            (10801, "3.25"),
        ],
    )
    def test_single_second_crossings(self, seconds, expected):
        assert round_seconds_to_hours(seconds, UP) == Decimal(expected)


class TestTheUsersWorkedExample:
    """The example the user wrote out himself, tested exactly as stated."""

    def test_six_ten_minute_calls_bill_one_hour(self):
        """Six 10-minute calls total 60 minutes, so he bills 1.00 - not 1.50.

        Rounding each call separately would give 6 x 0.25 = 1.50. That is
        the mistake this test exists to prevent.
        """
        total = sum(minutes(10) for _ in range(6))
        assert round_seconds_to_hours(total, UP) == Decimal("1.00")

    def test_rounding_each_entry_separately_would_inflate_the_day(self):
        """Demonstrates the wrong answer, so the right one is not a fluke."""
        per_entry = sum(
            (round_seconds_to_hours(minutes(10), UP) for _ in range(6)), Decimal("0")
        )
        assert per_entry == Decimal("1.50")  # the inflated figure
        assert round_seconds_to_hours(minutes(60), UP) == Decimal("1.00")  # correct

    def test_one_call_of_eleven_minutes_pushes_the_day_to_a_quarter_more(self):
        """Five 10-minute calls plus one 11-minute call = 61 min -> 1.25."""
        total = sum(minutes(10) for _ in range(5)) + minutes(11)
        assert total == minutes(61)
        assert round_seconds_to_hours(total, UP) == Decimal("1.25")


class TestDecimalNotFloat:
    """Why the whole module refuses to touch binary floating point.

    These are not invented numbers: they were found by searching for sets of
    whole-second durations that total an exact quarter hour but whose float
    sum overshoots it. With floats the user would be billing an extra
    quarter hour on those days and would have no way of noticing.
    """

    #: (durations in seconds, exact total hours)
    FLOAT_TRAPS = [
        ([3794, 5046, 1319, 4482, 3359], "5.00"),
        ([3329, 3325, 1228, 3678, 3672, 3668], "5.25"),
        ([1244, 700, 1213, 892, 3647, 444, 4432, 928], "3.75"),
        ([5079, 3274, 1021, 4999, 3990, 2337], "5.75"),
    ]

    @staticmethod
    def _float_billed(durations):
        """The naive implementation, for contrast only."""
        total_hours = 0.0
        for seconds in durations:
            total_hours += seconds / 3600.0
        return math.ceil(total_hours * 4) / 4

    @pytest.mark.parametrize(("durations", "expected"), FLOAT_TRAPS)
    def test_decimal_gets_these_right(self, durations, expected):
        assert round_seconds_to_hours(sum(durations), UP) == Decimal(expected)

    @pytest.mark.parametrize(("durations", "expected"), FLOAT_TRAPS)
    def test_float_would_get_these_wrong(self, durations, expected):
        """Guards the guard: if this ever stops failing, the trap is stale."""
        assert self._float_billed(durations) != float(Decimal(expected))

    def test_seconds_to_hours_is_exact_on_quarter_boundaries(self):
        for quarters in range(0, 4 * 24 + 1):
            hours = seconds_to_hours(quarters * 900)
            assert hours * 4 == hours.to_integral_value() * 4 or hours * 4 == quarters
            assert hours == Decimal(quarters) / 4

    def test_result_is_always_a_decimal(self):
        assert isinstance(round_seconds_to_hours(1234, UP), Decimal)


class TestIncrementsAndDirections:
    """The increment and direction are configurable; the default is 0.25 up."""

    @pytest.mark.parametrize(
        ("increment", "seconds", "expected"),
        [
            ("0.25", minutes(61), "1.25"),
            ("0.1", minutes(61), "1.10"),
            ("0.1", minutes(67), "1.20"),
            ("0.5", minutes(61), "1.50"),
            ("0.5", minutes(30), "0.50"),
        ],
    )
    def test_increments(self, increment, seconds, expected):
        rule = RoundingRule(increment=Decimal(increment))
        assert round_seconds_to_hours(seconds, rule) == Decimal(expected)

    @pytest.mark.parametrize(
        ("direction", "expected"),
        [
            (RoundingDirection.UP, "1.25"),
            (RoundingDirection.NEAREST, "1.00"),
            (RoundingDirection.DOWN, "1.00"),
        ],
    )
    def test_directions(self, direction, expected):
        rule = RoundingRule(direction=direction)
        assert round_seconds_to_hours(minutes(61), rule) == Decimal(expected)

    def test_nearest_rounds_a_half_upwards(self):
        rule = RoundingRule(direction=RoundingDirection.NEAREST)
        # 7.5 minutes is exactly half of a quarter hour.
        assert round_seconds_to_hours(450, rule) == Decimal("0.25")

    def test_zero_increment_means_no_rounding(self):
        rule = RoundingRule(increment=Decimal("0"))
        assert round_seconds_to_hours(minutes(61), rule) == Decimal("1.02")

    def test_software_rounding_can_be_switched_off_independently(self):
        from app.core.models import EntryKind

        rule = RoundingRule(apply_to_software=False)
        work_rule = rule.for_kind(EntryKind.WORK)
        software_rule = rule.for_kind(EntryKind.SOFTWARE)
        assert round_seconds_to_hours(minutes(61), work_rule) == Decimal("1.25")
        assert round_seconds_to_hours(minutes(61), software_rule) == Decimal("1.02")


class TestRoundHoursDirectly:
    def test_accepts_a_decimal_total(self):
        assert round_hours(Decimal("1.01"), UP) == Decimal("1.25")

    def test_large_totals_do_not_lose_precision(self):
        # A full year of billable hours, one second over a boundary.
        assert round_hours(Decimal("1999.9999"), UP) == Decimal("2000.00")
        assert round_hours(Decimal("2000.0001"), UP) == Decimal("2000.25")
