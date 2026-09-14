"""
Tests for downtime time parsing in handlers/downtimes.py

These cover the contract between user-supplied time expressions and the
UTC timestamps that get sent to the CheckMK REST API.
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from handlers.downtimes import DowntimeHandler
from mcp.registry import ToolRegistry
from mcp.tools import get_all_tools

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

    @pytest.mark.usefixtures("berlin_tz")
    def test_time_tomorrow_is_converted_from_local_to_utc(self, handler):
        result = handler._parse_downtime_times("22:00 tomorrow", "", 120)

        local_tomorrow = (datetime.now().astimezone() + timedelta(days=1)).replace(
            hour=22, minute=0, second=0, microsecond=0
        )
        assert as_utc(result["start_time"]) == local_tomorrow.astimezone(timezone.utc)

    @pytest.mark.usefixtures("berlin_tz")
    def test_bare_clock_time_is_converted_from_local_to_utc(self, handler):
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
                # Raises ValueError if the format ever drifts. The parsed value
                # itself is never used, only the fact that parsing succeeded, so
                # the naive-datetime result this produces is fine here.
                datetime.strptime(result[key], "%Y-%m-%dT%H:%M:%SZ")  # noqa: DTZ007


class TestParseNaturalTime:
    def test_returns_none_for_unparseable_input(self, handler):
        assert handler._parse_natural_time("not a time at all") is None

    def test_returns_none_for_empty_input(self, handler):
        assert handler._parse_natural_time("") is None

    def test_returns_timezone_aware_datetime(self, handler):
        result = handler._parse_natural_time("22:00 tomorrow")

        assert result is not None
        assert result.tzinfo is not None, "naive datetimes silently become wrong once formatted as UTC"


class TestIsDowntimeActiveIsTotal:
    """_is_downtime_active promises 'False otherwise' -- it must never raise.

    A malformed downtime record (start_time present but null) must not blow
    up the comprehensions in _get_host_downtime_status / has_host_level_downtime;
    it should just be treated as inactive.
    """

    def test_null_start_time_returns_false_instead_of_raising(self, handler):
        # extensions.get("start_time", 0) does NOT fall back to 0 here: the key
        # is present with a None value, so this is exactly the shape a
        # malformed CheckMK downtime record can take.
        downtime = {"extensions": {"start_time": None, "end_time": 9999999999.0}}

        assert handler._is_downtime_active(downtime, 1700000000.0) is False

    def test_null_end_time_returns_false_instead_of_raising(self, handler):
        downtime = {"extensions": {"start_time": 0.0, "end_time": None}}

        assert handler._is_downtime_active(downtime, 1700000000.0) is False

    def test_active_downtime_returns_true(self, handler):
        downtime = {"extensions": {"start_time": 1000.0, "end_time": 2000.0}}

        assert handler._is_downtime_active(downtime, 1500.0) is True

    def test_expired_downtime_returns_false(self, handler):
        downtime = {"extensions": {"start_time": 1000.0, "end_time": 2000.0}}

        assert handler._is_downtime_active(downtime, 2500.0) is False


class TestHasHostLevelDowntimeIsTotal:
    """has_host_level_downtime must also never raise -- same TRY300 defect."""

    @pytest.mark.asyncio
    async def test_one_malformed_record_does_not_raise(self, handler, mock_checkmk_client):
        mock_checkmk_client.get.return_value = {
            "success": True,
            "data": {
                "value": [
                    {"extensions": {"start_time": None, "end_time": 9999999999.0}},
                ]
            },
        }

        result = await handler.has_host_level_downtime("test-host")

        assert result is False

    @pytest.mark.asyncio
    async def test_malformed_record_does_not_hide_a_real_active_downtime(self, handler, mock_checkmk_client):
        now = datetime.now(timezone.utc).timestamp()
        mock_checkmk_client.get.return_value = {
            "success": True,
            "data": {
                "value": [
                    {"extensions": {"start_time": None, "end_time": 9999999999.0}},
                    {"extensions": {"start_time": now - 100, "end_time": now + 100}},
                ]
            },
        }

        result = await handler.has_host_level_downtime("test-host")

        assert result is True


class TestGetHostDowntimeStatusSkipsMalformedRecords:
    """One malformed downtime must not turn the whole status lookup into an error."""

    @pytest.mark.asyncio
    async def test_malformed_host_downtime_is_skipped_not_fatal(self, handler, mock_checkmk_client):
        now = datetime.now(timezone.utc).timestamp()
        good_downtime = {"id": 1, "extensions": {"start_time": now - 100, "end_time": now + 100}}
        bad_downtime = {"id": 2, "extensions": {"start_time": None, "end_time": now + 100}}

        def fake_get(_path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            query = params.get("query", "") if params else ""
            if '"is_service", "right": "0"' in query:
                return {"success": True, "data": {"value": [good_downtime, bad_downtime]}}
            return {"success": True, "data": {"value": []}}

        mock_checkmk_client.get.side_effect = fake_get

        status = await handler._get_host_downtime_status("test-host")

        assert "error" not in status
        assert status["has_host_downtime"] is True
        assert status["host_downtime_count"] == 1
        assert status["active_host_downtimes"] == [good_downtime]


class TestRecurringDowntimes:
    """`recur` is advertised by both scheduling tools and must reach CheckMK.

    Valid values come from cmk/gui/openapi/endpoints/downtime/request_schemas.py
    at v2.4.0p2. CheckMK's own docstring notes recurring downtimes work only on
    the Enterprise editions; on Raw the request is accepted and the downtime is
    created as a one-off.
    """

    @pytest.fixture
    def handler(self, mock_checkmk_client):
        return DowntimeHandler(mock_checkmk_client)

    @pytest.fixture(autouse=True)
    def no_verification_backoff(self, monkeypatch):
        """Skip the post-creation verification sleeps.

        After scheduling, the handler polls CheckMK five times with a five
        second pause to confirm the downtime appeared. These tests assert on
        the request that goes out, not on that polling, and the real sleeps
        would add a minute to the suite.
        """

        async def instant(_seconds: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", instant)

    @pytest.mark.asyncio
    async def test_host_downtime_forwards_recur(self, handler):
        handler.client.post.return_value = {"success": True, "status": 204, "headers": {}, "data": {}}

        await handler.handle(
            "vibemk_schedule_host_downtime",
            {"host_name": "example.com", "comment": "patching", "recur": "week"},
        )

        assert handler.client.post.call_args.kwargs["data"]["recur"] == "week"

    @pytest.mark.asyncio
    async def test_service_downtime_forwards_recur(self, handler):
        handler.client.post.return_value = {"success": True, "status": 204, "headers": {}, "data": {}}

        await handler.handle(
            "vibemk_schedule_service_downtime",
            {
                "host_name": "example.com",
                "service_descriptions": ["CPU utilization"],
                "comment": "patching",
                "recur": "day",
            },
        )

        assert handler.client.post.call_args.kwargs["data"]["recur"] == "day"

    @pytest.mark.asyncio
    async def test_omitting_recur_sends_no_recur_key(self, handler):
        # CheckMK defaults the field to "fixed"; sending nothing keeps the
        # payload honest about what the caller actually asked for.
        handler.client.post.return_value = {"success": True, "status": 204, "headers": {}, "data": {}}

        await handler.handle("vibemk_schedule_host_downtime", {"host_name": "example.com"})

        assert "recur" not in handler.client.post.call_args.kwargs["data"]

    @pytest.mark.asyncio
    async def test_a_value_checkmk_does_not_accept_is_rejected_locally(self, handler):
        # "month" was advertised for a year and is not a CheckMK value at all;
        # forwarding it would earn a 400. Catch it here with a usable message.
        result = await handler.handle("vibemk_schedule_host_downtime", {"host_name": "example.com", "recur": "month"})

        assert not handler.client.post.called
        assert "month" in result[0]["text"]
        assert "day_of_month" in result[0]["text"]

    def test_advertised_values_match_what_checkmk_accepts(self):
        checkmk = {
            "fixed",
            "hour",
            "day",
            "week",
            "second_week",
            "fourth_week",
            "weekday_start",
            "weekday_end",
            "day_of_month",
        }
        for name in ("vibemk_schedule_host_downtime", "vibemk_schedule_service_downtime"):
            tool = next(t for t in get_all_tools() if t["name"] == name)
            advertised = set(tool["inputSchema"]["properties"]["recur"]["enum"])
            assert advertised <= checkmk, f"{name} advertises {advertised - checkmk}"


class TestTheGenericSchedulingToolReachesAWorkingPath:
    """vibemk_schedule_downtime used to live in MonitoringHandler.

    It posted to `domain-types/downtime/collections/all`, which CheckMK serves
    for GET only, so it answered 405 for every call it ever made. Meanwhile
    DowntimeHandler carried a correct implementation, split into a host and a
    service path, with recurrence validation and a proper
    `service_descriptions` array. The tool now dispatches to those instead of
    being a second, broken copy.
    """

    @pytest.fixture
    def scheduling(self, mock_checkmk_client: Any) -> Any:
        mock_checkmk_client.post.return_value = {"success": True, "status": 200, "headers": {}, "data": {}}
        # The handler reads the downtime collection twice: once before
        # scheduling, to refuse a duplicate, and again afterwards to verify the
        # downtime exists. Answering "none yet, then one" models that sequence.
        # It also keeps the tests quick — a verification that never succeeds
        # retries five times, five seconds apart.
        answers = iter(
            [
                {"success": True, "status": 200, "headers": {}, "data": {"value": []}},
            ]
        )
        created = {
            "success": True,
            "status": 200,
            "headers": {},
            "data": {"value": [{"id": "1", "extensions": {"host_name": "web01"}}]},
        }
        mock_checkmk_client.get.side_effect = lambda *_a, **_k: next(answers, created)
        return mock_checkmk_client

    def endpoints(self, client: Any) -> List[str]:
        return [c.args[0] for c in client.post.call_args_list]

    def test_the_registry_routes_it_to_this_handler(self) -> None:
        handler = ToolRegistry.from_client(MagicMock()).handler_for("vibemk_schedule_downtime")

        assert type(handler).__name__ == "DowntimeHandler"

    @pytest.mark.asyncio
    async def test_a_host_downtime_uses_the_host_collection(self, handler: Any, scheduling: Any) -> None:
        await handler.handle(
            "vibemk_schedule_downtime",
            {"downtime_type": "host", "host_name": "web01", "comment": "patching", "duration": "30m"},
        )

        assert "domain-types/downtime/collections/host" in self.endpoints(scheduling)

    @pytest.mark.asyncio
    async def test_a_service_downtime_uses_the_service_collection(self, handler: Any, scheduling: Any) -> None:
        await handler.handle(
            "vibemk_schedule_downtime",
            {
                "downtime_type": "service",
                "host_name": "web01",
                "service_description": "CPU load",
                "comment": "patching",
                "duration": "30m",
            },
        )

        assert "domain-types/downtime/collections/service" in self.endpoints(scheduling)

    @pytest.mark.asyncio
    async def test_the_service_is_sent_as_the_array_the_api_expects(self, handler: Any, scheduling: Any) -> None:
        await handler.handle(
            "vibemk_schedule_downtime",
            {
                "downtime_type": "service",
                "host_name": "web01",
                "service_description": "CPU load",
                "comment": "patching",
                "duration": "30m",
            },
        )

        body = next(c for c in scheduling.post.call_args_list if "collections/service" in c.args[0]).kwargs["data"]
        assert body["service_descriptions"] == ["CPU load"]

    @pytest.mark.asyncio
    async def test_an_unsupported_type_never_reaches_checkmk(self, handler: Any, scheduling: Any) -> None:
        result = await handler.handle(
            "vibemk_schedule_downtime", {"downtime_type": "hostgroup", "host_name": "web01", "comment": "x"}
        )

        assert self.endpoints(scheduling) == []
        assert "hostgroup" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_nothing_posts_to_the_read_only_collection(self, handler: Any, scheduling: Any) -> None:
        await handler.handle(
            "vibemk_schedule_downtime",
            {"downtime_type": "host", "host_name": "web01", "comment": "patching", "duration": "30m"},
        )

        assert "domain-types/downtime/collections/all" not in self.endpoints(scheduling)
