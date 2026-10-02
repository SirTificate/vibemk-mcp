"""
Tests for metric time window construction.

CheckMK reads a timestamp with no offset as site local time. Measured against
2.4.0p2 CRE, asking for "the last hour" from a host in UTC against a site in
Europe/Berlin returned the window two hours early. Sending UTC with a Z suffix
removes the coupling to the host's timezone.
"""

import re
import time
from datetime import datetime, timedelta, timezone

import pytest

from api.exceptions import CheckMKAPIError, CheckMKNotFoundError
from handlers.metrics import MetricsHandler
from mcp.tools import get_all_tools

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


# The 400 handler told users to send "YYYY-MM-DD HH:MM:SS" long after the code
# started sending "%Y-%m-%dT%H:%M:%SZ" for the UTC fix above. A caller who did
# as told reintroduced exactly the timezone bug that change removed. Deriving
# the expectation from _parse_time_range keeps the advice and the wire format
# from drifting apart again.
ISO_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def test_a_time_range_error_shows_the_format_actually_sent(handler):
    transmitted = handler._parse_time_range("1h")["start"]
    assert ISO_UTC.fullmatch(transmitted), "precondition: _parse_time_range emits ISO-8601 UTC"

    message = handler._handle_400_error({"detail": "time_range is invalid"}, "web01", "Check_MK", "util")

    assert ISO_UTC.search(message), f"the advice should show the shape the code transmits, got: {message}"


# get_custom_graph and search_metrics call endpoints that only the commercial
# editions serve. A Raw site's API document lacks them, which looks exactly like
# a path that was never there -- they were removed for that reason once, and
# wrongly. On Raw they answer 404, which reads to a model like a wrong path
# worth retrying, so both the catalogue and the answer have to name the edition.
COMMERCIAL_ONLY = [
    ("vibemk_get_custom_graph", {"custom_graph_id": "my_graph"}, "domain-types/metric/actions/get_custom_graph/invoke"),
    (
        "vibemk_search_metrics",
        {"host_filter": "web01", "graph_id": "cpu_load"},
        "domain-types/metric/actions/filter/invoke",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "arguments", "endpoint"), COMMERCIAL_ONLY)
async def test_commercial_metric_tools_call_their_endpoint(handler, mock_checkmk_client, tool, arguments, endpoint):
    mock_checkmk_client.post.return_value = {"success": True, "data": {"metrics": []}}

    await handler.handle(tool, arguments)

    assert mock_checkmk_client.post.call_args.args[0] == endpoint


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "arguments"), [(tool, arguments) for tool, arguments, _ in COMMERCIAL_ONLY])
async def test_a_404_names_the_edition(handler, mock_checkmk_client, tool, arguments):
    mock_checkmk_client.post.side_effect = CheckMKNotFoundError("HTTP 404: Not Found", 404, {"title": "Not Found"})

    answer = await handler.handle(tool, arguments)

    assert "commercial edition" in answer[0]["text"]


@pytest.mark.parametrize("tool", [tool for tool, _, _ in COMMERCIAL_ONLY])
def test_the_catalogue_says_which_tools_need_a_commercial_edition(tool):
    descriptions = {entry["name"]: entry["description"] for entry in get_all_tools()}

    assert "commercial edition" in descriptions[tool]


# The diagnostics read CheckMK's error body from `error_data`, an attribute
# CheckMKError does not have -- the body is `response_data`. CheckMK's detail
# never arrived, and against the empty detail every `name in detail` test with
# an empty name matched, so a host metric's 400 was blamed on a service.
WITH_DIAGNOSTICS = [
    ("vibemk_get_host_metrics", {"host_name": "web01", "metric_name": "load1"}),
    ("vibemk_get_service_metrics", {"host_name": "web01", "service_description": "CPU load", "metric_name": "load1"}),
    ("vibemk_get_custom_graph", {"custom_graph_id": "my_graph"}),
]


def rejected(status: int, reason: str, detail: str) -> CheckMKAPIError:
    return CheckMKAPIError(f"HTTP {status}: {reason}", status, {"title": reason, "detail": detail})


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "arguments"), WITH_DIAGNOSTICS)
async def test_a_400_carries_checkmks_detail(handler, mock_checkmk_client, tool, arguments):
    mock_checkmk_client.post.side_effect = rejected(400, "Bad Request", "These fields have problems: reduce")
    mock_checkmk_client.get.side_effect = CheckMKAPIError("HTTP 500: Internal Server Error", 500)

    answer = await handler.handle(tool, arguments)

    assert "These fields have problems: reduce" in answer[0]["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "arguments"), WITH_DIAGNOSTICS)
async def test_a_400_blames_nothing_the_detail_does_not_name(handler, mock_checkmk_client, tool, arguments):
    mock_checkmk_client.post.side_effect = rejected(400, "Bad Request", "These fields have problems: reduce")
    mock_checkmk_client.get.side_effect = CheckMKAPIError("HTTP 500: Internal Server Error", 500)

    answer = await handler.handle(tool, arguments)

    assert "may not exist" not in answer[0]["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "arguments"), WITH_DIAGNOSTICS)
async def test_another_error_keeps_the_detail_and_names_its_status_once(handler, mock_checkmk_client, tool, arguments):
    mock_checkmk_client.post.side_effect = rejected(500, "Internal Server Error", "Livestatus is not running")
    mock_checkmk_client.get.side_effect = CheckMKAPIError("HTTP 500: Internal Server Error", 500)

    answer = await handler.handle(tool, arguments)

    assert "Livestatus is not running" in answer[0]["text"]
    assert "HTTP 500: HTTP 500" not in answer[0]["text"]


# The filter endpoint reads one named graph or one named metric across every
# host the filter matches; it has no mode that searches without naming one.
# The original never sent graph_id, so every call on a commercial site answered
# 400 -- found upstream against an Ultimate site (chexma/vibeMK@539a5a7).
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("given", "sent"),
    [
        ({"graph_id": "cpu_load"}, {"type": "predefined_graph", "graph_id": "cpu_load"}),
        ({"metric_id": "load1"}, {"type": "single_metric", "metric_id": "load1"}),
    ],
)
async def test_a_metric_search_names_what_it_reads(handler, mock_checkmk_client, given, sent):
    mock_checkmk_client.post.return_value = {"success": True, "data": {"metrics": []}}

    await handler.handle("vibemk_search_metrics", {"host_filter": "web01", **given})

    body = mock_checkmk_client.post.call_args.kwargs["data"]
    assert {key: body[key] for key in ("type", "graph_id", "metric_id") if key in body} == sent


@pytest.mark.asyncio
@pytest.mark.parametrize("given", [{}, {"graph_id": "cpu_load", "metric_id": "load1"}])
async def test_a_metric_search_needs_exactly_one_id(handler, mock_checkmk_client, given):
    answer = await handler.handle("vibemk_search_metrics", {"host_filter": "web01", **given})

    mock_checkmk_client.post.assert_not_called()
    assert "graph_id" in answer[0]["text"]


def test_the_metric_search_declares_both_ids():
    tool = next(entry for entry in get_all_tools() if entry["name"] == "vibemk_search_metrics")

    assert {"graph_id", "metric_id"} <= set(tool["inputSchema"]["properties"])
