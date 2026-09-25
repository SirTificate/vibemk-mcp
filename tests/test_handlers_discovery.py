"""
Tests for discovery defaults and mode mapping.

Discovery is the one area where a default decides whether monitoring is
added or removed. CheckMK's own BulkDiscoveryOptions default every flag to
False; this server overrode four of them with True, so a call carrying
nothing but hostnames removed vanished services. Nobody asking "discover
services on these hosts" is asking for that.

The single-host fallback had a second problem: it maps a discovery mode onto
bulk options by membership in four lists, and `tabula_rasa` — the most
destructive mode there is — appeared in none of them. It therefore became a
request with every option False and no full scan: a no-op, reported as a
success.
"""

from typing import Any, Dict

import pytest

from api.exceptions import CheckMKError
from handlers.discovery import _BULK_EQUIVALENT, DiscoveryHandler
from mcp.tools import get_all_tools

BULK = "domain-types/discovery_run/actions/bulk-discovery-start/invoke"


@pytest.fixture
def handler(mock_checkmk_client: Any) -> DiscoveryHandler:
    mock_checkmk_client.post.return_value = {"success": True, "status": 200, "headers": {}, "data": {}}
    mock_checkmk_client.get.return_value = {"success": True, "status": 200, "headers": {}, "data": {}}
    return DiscoveryHandler(mock_checkmk_client)


def bulk_body(client: Any) -> Dict[str, Any]:
    call = next(c for c in client.post.call_args_list if c.args[0] == BULK)
    return dict(call.kwargs["data"])


class TestRemovalIsOptIn:
    @pytest.mark.asyncio
    async def test_a_bare_call_does_not_remove_vanished_services(
        self, handler: DiscoveryHandler, mock_checkmk_client: Any
    ) -> None:
        await handler.handle("vibemk_start_bulk_discovery", {"hostnames": ["web01"]})

        assert bulk_body(mock_checkmk_client)["options"]["remove_vanished_services"] is False

    @pytest.mark.asyncio
    async def test_a_bare_call_still_monitors_what_it_finds(
        self, handler: DiscoveryHandler, mock_checkmk_client: Any
    ) -> None:
        # Otherwise the tool's default behaviour would be to do nothing at all.
        await handler.handle("vibemk_start_bulk_discovery", {"hostnames": ["web01"]})

        assert bulk_body(mock_checkmk_client)["options"]["monitor_undecided_services"] is True

    @pytest.mark.asyncio
    async def test_removal_happens_when_it_is_asked_for(
        self, handler: DiscoveryHandler, mock_checkmk_client: Any
    ) -> None:
        await handler.handle(
            "vibemk_start_bulk_discovery",
            {"hostnames": ["web01"], "options": {"remove_vanished_services": True}},
        )

        assert bulk_body(mock_checkmk_client)["options"]["remove_vanished_services"] is True

    def test_the_schema_does_not_advertise_removal_as_the_default(self) -> None:
        tool = next(t for t in get_all_tools() if t["name"] == "vibemk_start_bulk_discovery")
        options = tool["inputSchema"]["properties"]["options"]["properties"]

        assert options["remove_vanished_services"]["default"] is False


class TestEveryModeReachesTheFallbackIntact:
    """The fallback runs when single-host discovery fails, so it is reached the
    way a real call reaches it: by making that first request fail."""

    @pytest.fixture
    def falls_back(self, mock_checkmk_client: Any) -> Any:
        ok = {"success": True, "status": 200, "headers": {}, "data": {}}

        def answer(endpoint: str, **_: Any) -> Dict[str, Any]:
            if endpoint == BULK:
                return ok
            msg = "single-host discovery unavailable"
            raise CheckMKError(msg)

        mock_checkmk_client.post.side_effect = answer
        return mock_checkmk_client

    @pytest.mark.asyncio
    async def test_tabula_rasa_is_not_a_silent_no_op(self, handler: DiscoveryHandler, falls_back: Any) -> None:
        # tabula_rasa means "forget everything and rediscover". Mapped to all
        # options False it asks CheckMK to do nothing, and says it worked.
        await handler.handle("vibemk_start_service_discovery", {"host_name": "web01", "mode": "tabula_rasa"})

        body = bulk_body(falls_back)
        assert any(body["options"].values()), "tabula_rasa mapped to a request that changes nothing"
        assert body["options"]["remove_vanished_services"] is True
        assert body["do_full_scan"] is True

    def test_every_offered_mode_has_a_bulk_equivalent(self) -> None:
        tool = next(t for t in get_all_tools() if t["name"] == "vibemk_start_service_discovery")
        offered = set(tool["inputSchema"]["properties"]["mode"]["enum"])

        assert offered <= set(
            _BULK_EQUIVALENT
        ), f"modes the fallback cannot express: {sorted(offered - set(_BULK_EQUIVALENT))}"
