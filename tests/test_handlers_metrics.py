"""
Tests for metric time window construction.

CheckMK reads a timestamp with no offset as site local time. Measured against
2.4.0p2 CRE, asking for "the last hour" from a host in UTC against a site in
Europe/Berlin returned the window two hours early. Sending UTC with a Z suffix
removes the coupling to the host's timezone.
"""

import time
from datetime import datetime, timedelta, timezone

import pytest

from handlers.metrics import MetricsHandler

TIMEZONES = ("Europe/Berlin", "UTC", "America/New_York")


@pytest.fixture
def handler(mock_checkmk_client):
    return MetricsHandler(mock_checkmk_client)


@pytest.fixture
def restore_timezone(monkeypatch):
    yield
    monkeypatch.delenv("TZ", raising=False)
    time.tzset()


def as_utc(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


@pytest.mark.usefixtures("restore_timezone")
def test_window_is_the_same_instant_in_every_host_timezone(handler, monkeypatch):
    windows = []
    for zone in TIMEZONES:
        monkeypatch.setenv("TZ", zone)
        time.tzset()
        windows.append(handler._parse_time_range("1h"))

    starts = {as_utc(w["start"]).replace(second=0, microsecond=0) for w in windows}
    assert len(starts) == 1, f"host timezone leaked into the window: {windows}"


def test_window_ends_at_the_current_utc_instant(handler):
    before = datetime.now(timezone.utc)
    window = handler._parse_time_range("1h")
    after = datetime.now(timezone.utc)

    assert before - timedelta(seconds=2) <= as_utc(window["end"]) <= after + timedelta(seconds=2)


@pytest.mark.parametrize(
    ("name", "span"),
    [
        ("1h", timedelta(hours=1)),
        ("4h", timedelta(hours=4)),
        ("24h", timedelta(days=1)),
        ("7d", timedelta(days=7)),
        ("30d", timedelta(days=30)),
    ],
)
def test_named_ranges_span_their_duration(handler, name, span):
    window = handler._parse_time_range(name)

    assert as_utc(window["end"]) - as_utc(window["start"]) == span


def test_unknown_range_falls_back_to_one_hour(handler):
    window = handler._parse_time_range("not-a-range")

    assert as_utc(window["end"]) - as_utc(window["start"]) == timedelta(hours=1)
