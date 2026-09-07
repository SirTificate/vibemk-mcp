"""
Tests for downtime time parsing in handlers/downtimes.py

These cover the contract between user-supplied time expressions and the
UTC timestamps that get sent to the CheckMK REST API.
"""

import os
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from handlers.downtimes import DowntimeHandler

# A fixed non-UTC zone with a stable offset makes the UTC conversion observable.
BERLIN = "Europe/Berlin"


@pytest.fixture
def handler(mock_checkmk_client):
    return DowntimeHandler(mock_checkmk_client)


@pytest.fixture
def berlin_tz(monkeypatch):
    """Run the test body with the process in Europe/Berlin local time."""
    monkeypatch.setenv("TZ", BERLIN)
    time.tzset()
    yield
    monkeypatch.delenv("TZ", raising=False)
    time.tzset()


def as_utc(iso_z: str) -> datetime:
    """Parse the '...Z' string the handler produces back into an aware datetime."""
    return datetime.strptime(iso_z, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class TestExplicitDatesArePreserved:
    """An explicit calendar date must survive parsing."""

    def test_iso_8601_start_keeps_its_date(self, handler):
        result = handler._parse_downtime_times("2026-12-24T22:00:00Z", "2026-12-24T23:00:00Z", 0)

        assert result["start_time"] == "2026-12-24T22:00:00Z"
        assert result["end_time"] == "2026-12-24T23:00:00Z"

    def test_iso_8601_date_far_in_the_future_is_not_clamped_to_today(self, handler):
        result = handler._parse_downtime_times("2027-01-01T00:00:00Z", "2027-01-01T02:00:00Z", 0)

        assert result["start_time"].startswith("2027-01-01")

    def test_natural_language_date_with_time_keeps_its_date(self, handler):
        result = handler._parse_downtime_times("2026-12-24 at 22:00", "", 60)

        assert result["start_time"].startswith("2026-12-24")

    def test_iso_8601_with_explicit_offset_is_converted_to_utc(self, handler):
        # 22:00 at +02:00 is 20:00 UTC.
        result = handler._parse_downtime_times("2026-12-24T22:00:00+02:00", "", 60)

        assert result["start_time"] == "2026-12-24T20:00:00Z"


class TestLocalTimesAreConvertedToUtc:
    """Times the user names without an offset are local, and must be sent as UTC."""

    def test_time_tomorrow_is_converted_from_local_to_utc(self, handler, berlin_tz):
        result = handler._parse_downtime_times("22:00 tomorrow", "", 120)

        local_tomorrow = (datetime.now().astimezone() + timedelta(days=1)).replace(
            hour=22, minute=0, second=0, microsecond=0
        )
        assert as_utc(result["start_time"]) == local_tomorrow.astimezone(timezone.utc)

    def test_bare_clock_time_is_converted_from_local_to_utc(self, handler, berlin_tz):
        result = handler._parse_downtime_times("03:00", "", 60)

        start = as_utc(result["start_time"])
        # 03:00 Berlin is 01:00 or 02:00 UTC depending on DST — never 03:00 UTC.
        assert start.astimezone().hour == 3
        assert start.hour != 3

    def test_now_is_current_utc(self, handler):
        before = datetime.now(timezone.utc).replace(microsecond=0)
        result = handler._parse_downtime_times("now", "", 30)
        after = datetime.now(timezone.utc)

        assert before - timedelta(seconds=2) <= as_utc(result["start_time"]) <= after + timedelta(seconds=2)


class TestDurationAndOrdering:
    def test_empty_end_time_uses_duration_minutes(self, handler):
        result = handler._parse_downtime_times("2026-12-24T22:00:00Z", "", 90)

        delta = as_utc(result["end_time"]) - as_utc(result["start_time"])
        assert delta == timedelta(minutes=90)

    def test_relative_start_offsets_from_now(self, handler):
        result = handler._parse_downtime_times("+2h", "", 30)

        start = as_utc(result["start_time"])
        expected = datetime.now(timezone.utc) + timedelta(hours=2)
        assert abs((start - expected).total_seconds()) < 5

    def test_relative_end_offsets_from_start(self, handler):
        result = handler._parse_downtime_times("2026-12-24T22:00:00Z", "+90m", 0)

        assert result["end_time"] == "2026-12-24T23:30:00Z"

    def test_end_before_start_is_corrected_to_start_plus_duration(self, handler):
        result = handler._parse_downtime_times("2026-12-24T22:00:00Z", "2026-12-24T20:00:00Z", 45)

        assert as_utc(result["end_time"]) - as_utc(result["start_time"]) == timedelta(minutes=45)


class TestMixedNaiveAndAwareInputs:
    """Comparing a naive against an aware datetime raises TypeError — it must not happen."""

    def test_naive_start_with_aware_end_does_not_raise(self, handler):
        result = handler._parse_downtime_times("now", "2030-01-01T00:00:00Z", 60)

        assert result["end_time"] == "2030-01-01T00:00:00Z"

    def test_aware_start_with_naive_end_does_not_raise(self, handler):
        result = handler._parse_downtime_times("2026-12-24T22:00:00Z", "23:30", 60)

        assert result["start_time"] == "2026-12-24T22:00:00Z"


class TestOutputContract:
    def test_output_is_always_utc_z_format(self, handler):
        for start in ["now", "+1h", "22:00 tomorrow", "2026-12-24T22:00:00Z", "in 2 hours", "garbage"]:
            result = handler._parse_downtime_times(start, "", 30)
            for key in ("start_time", "end_time"):
                # Raises ValueError if the format ever drifts.
                datetime.strptime(result[key], "%Y-%m-%dT%H:%M:%SZ")


class TestParseNaturalTime:
    def test_returns_none_for_unparseable_input(self, handler):
        assert handler._parse_natural_time("not a time at all") is None

    def test_returns_none_for_empty_input(self, handler):
        assert handler._parse_natural_time("") is None

    def test_returns_timezone_aware_datetime(self, handler):
        result = handler._parse_natural_time("22:00 tomorrow")

        assert result is not None
        assert result.tzinfo is not None, "naive datetimes silently become wrong once formatted as UTC"
