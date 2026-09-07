"""
Tests for the JSON-RPC dispatch layer.

The dispatcher is exercised through a stub registry, so no CheckMK client and
no mock detection is involved. Production code must never behave differently
because it is under test.
"""

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from config import MCPConfig
from mcp.dispatch import Dispatcher


class RecordingHandler:
    """A handler that records the call it received."""

    def __init__(self, result: Optional[List[Dict[str, Any]]] = None, raises: Optional[Exception] = None):
        self.result = result if result is not None else [{"type": "text", "text": "ok"}]
        self.raises = raises
        self.calls: List[Any] = []

    async def handle(self, tool_name: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        self.calls.append((tool_name, arguments))
        if self.raises is not None:
            raise self.raises
        return self.result


class StubRegistry:
    def __init__(self, handlers: Optional[Dict[str, Any]] = None):
        self._handlers = handlers or {}

    def handler_for(self, tool_name):
        return self._handlers.get(tool_name)

    def tool_names(self):
        return frozenset(self._handlers)


def make_dispatcher(handlers=None):
    registry = StubRegistry(handlers)
    return Dispatcher(lambda: registry, MCPConfig())


def request(method, params=None, request_id=1):
    body = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


class TestProtocol:
    @pytest.mark.asyncio
    async def test_tools_list_returns_the_catalogue(self):
        response = await make_dispatcher().handle(request("tools/list"))

        assert len(response["result"]["tools"]) == 117

    @pytest.mark.asyncio
    async def test_initialize_answers_a_supported_version(self):
        response = await make_dispatcher().handle(request("initialize", {"protocolVersion": "1999-01-01-BOGUS"}))

        assert response["result"]["protocolVersion"] in MCPConfig().supported_protocol_versions

    @pytest.mark.asyncio
    async def test_initialized_notification_produces_no_response(self):
        assert await make_dispatcher().handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    @pytest.mark.asyncio
    async def test_unknown_method_is_method_not_found(self):
        response = await make_dispatcher().handle(request("no/such/method"))

        assert response["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_request_without_jsonrpc_field_is_invalid(self):
        response = await make_dispatcher().handle({"id": 1, "method": "tools/list"})

        assert response["error"]["code"] == -32600

    @pytest.mark.asyncio
    async def test_request_that_is_not_an_object_is_invalid(self):
        response = await make_dispatcher().handle("not a dict")

        assert response["error"]["code"] == -32600


class TestToolCalls:
    @pytest.mark.asyncio
    async def test_a_registered_tool_is_invoked(self):
        handler = RecordingHandler()
        dispatcher = make_dispatcher({"vibemk_demo": handler})

        response = await dispatcher.handle(request("tools/call", {"name": "vibemk_demo", "arguments": {}}))

        assert response["result"]["content"] == [{"type": "text", "text": "ok"}]

    @pytest.mark.asyncio
    async def test_arguments_reach_the_handler(self):
        handler = RecordingHandler()
        dispatcher = make_dispatcher({"vibemk_demo": handler})

        await dispatcher.handle(
            request("tools/call", {"name": "vibemk_demo", "arguments": {"host_name": "example.com"}})
        )

        assert handler.calls == [("vibemk_demo", {"host_name": "example.com"})]

    @pytest.mark.asyncio
    async def test_an_unregistered_tool_is_method_not_found(self):
        response = await make_dispatcher().handle(request("tools/call", {"name": "vibemk_nope", "arguments": {}}))

        assert response["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_a_raising_handler_becomes_an_internal_error(self):
        dispatcher = make_dispatcher({"vibemk_demo": RecordingHandler(raises=RuntimeError("boom"))})

        response = await dispatcher.handle(request("tools/call", {"name": "vibemk_demo", "arguments": {}}))

        assert response["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_requests_are_served_concurrently(self):
        dispatcher = make_dispatcher({"vibemk_demo": RecordingHandler()})

        responses = await asyncio.gather(
            *(dispatcher.handle(request("tools/call", {"name": "vibemk_demo", "arguments": {}}, i)) for i in range(5))
        )

        assert [r["id"] for r in responses] == [0, 1, 2, 3, 4]


class TestConfigurationErrors:
    @pytest.mark.asyncio
    async def test_a_failing_registry_becomes_readable_tool_output(self):
        def explode():
            raise ValueError("CHECKMK_SERVER_URL is required")

        dispatcher = Dispatcher(explode, MCPConfig())

        response = await dispatcher.handle(request("tools/call", {"name": "vibemk_demo", "arguments": {}}))

        assert "CHECKMK_SERVER_URL is required" in response["result"]["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_tools_list_works_without_a_usable_registry(self):
        def explode():
            raise ValueError("CHECKMK_SERVER_URL is required")

        response = await Dispatcher(explode, MCPConfig()).handle(request("tools/list"))

        assert len(response["result"]["tools"]) == 117
