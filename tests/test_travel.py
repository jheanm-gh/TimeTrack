"""Kilometres and odometer readings. Acceptance checklist 11 and 12."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.calc import ValidationError, km_from_odometer, resolve_travel_km, sum_km
from app.core.models import TravelDetail


class TestOdometer:
    def test_kilometres_are_derived_from_the_readings(self):
        assert km_from_odometer(Decimal("104200"), Decimal("104320")) == Decimal("120.0")

    def test_a_lower_closing_reading_is_rejected_with_a_readable_message(self):
        """Acceptance checklist 12."""
        with pytest.raises(ValidationError) as caught:
            km_from_odometer(Decimal("104400"), Decimal("104300"))
        message = str(caught.value)
        assert "lower than the opening" in message
        assert "104300" in message and "104400" in message

    def test_an_identical_pair_is_zero_kilometres_not_an_error(self):
        assert km_from_odometer(Decimal("104200"), Decimal("104200")) == Decimal("0.0")

    def test_half_kilometres_survive(self):
        assert km_from_odometer(Decimal("1000"), Decimal("1120.5")) == Decimal("120.5")

    def test_a_missing_reading_is_reported_clearly(self):
        with pytest.raises(ValidationError) as caught:
            km_from_odometer(Decimal("100"), None)
        assert "opening and closing" in str(caught.value)


class TestResolveTravelKm:
    def test_odometer_pair_wins_over_a_typed_figure(self):
        """The readings are the evidence; the typed number is a guess."""
        travel = TravelDetail(
            km_travelled=Decimal("999"),
            odo_start=Decimal("104200"),
            odo_end=Decimal("104320"),
        )
        assert resolve_travel_km(travel) == Decimal("120.0")

    def test_a_typed_figure_is_used_when_there_are_no_readings(self):
        assert resolve_travel_km(TravelDetail(km_travelled=Decimal("87.4"))) == Decimal("87.4")

    def test_no_travel_at_all_is_none(self):
        assert resolve_travel_km(TravelDetail()) is None

    def test_negative_kilometres_are_rejected(self):
        with pytest.raises(ValidationError):
            resolve_travel_km(TravelDetail(km_travelled=Decimal("-5")))

    def test_one_reading_alone_falls_back_to_the_typed_figure(self):
        travel = TravelDetail(km_travelled=Decimal("60"), odo_start=Decimal("104200"))
        assert resolve_travel_km(travel) == Decimal("60.0")

    def test_has_any_detects_a_trip_with_only_a_destination(self):
        assert TravelDetail(trip_to="Kloof TSF").has_any
        assert not TravelDetail().has_any


class TestTotals:
    def test_totals_ignore_missing_values(self):
        assert sum_km([Decimal("120"), None, Decimal("87.5")]) == Decimal("207.5")

    def test_an_empty_list_is_zero(self):
        assert sum_km([]) == Decimal("0.0")

    def test_totals_stay_decimal(self):
        total = sum_km([Decimal("0.1")] * 10)
        assert total == Decimal("1.0")
        assert isinstance(total, Decimal)
