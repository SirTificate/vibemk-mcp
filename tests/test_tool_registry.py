"""
Structural guards for the MCP tool registry.

The catalogue in mcp/tools.py and the dispatch table in mcp/server.py are
maintained by hand in two different files. These tests keep them in agreement:
a tool the client can see must be callable, and a handler that exists must be
reachable.
"""

import os
from unittest.mock import patch

import pytest

from mcp.tools import get_all_tools


@pytest.fixture
def handler_names():
    """Tool names wired to a handler in the real (non-test) dispatch table."""
    env = {
        "CHECKMK_SERVER_URL": "http://checkmk.invalid",
        "CHECKMK_SITE": "test",
        "CHECKMK_USERNAME": "automation",
        "CHECKMK_PASSWORD": "secret",
    }
    with patch.dict(os.environ, env, clear=True), patch(
        "api.client.CheckMKClient._detect_api_url",
        return_value="http://checkmk.invalid/test/check_mk/api/1.0",
    ):
        from mcp.server import CheckMKMCPServer

        server = CheckMKMCPServer()
        server._ensure_initialized()
        return server.handlers


def test_no_tool_is_declared_twice():
    names = [tool["name"] for tool in get_all_tools()]

    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert duplicates == [], f"declared more than once: {duplicates}"


def test_every_declared_tool_has_a_handler(handler_names):
    declared = {tool["name"] for tool in get_all_tools()}

    unroutable = sorted(name for name in declared if handler_names.get(name) is None)
    assert unroutable == [], f"advertised to the client but not callable: {unroutable}"


def test_every_handler_is_declared_as_a_tool(handler_names):
    declared = {tool["name"] for tool in get_all_tools()}

    unreachable = sorted(name for name in handler_names if name not in declared)
    assert unreachable == [], f"wired to a handler but never advertised: {unreachable}"


def test_every_tool_has_a_usable_schema():
    for tool in get_all_tools():
        name = tool["name"]
        assert tool.get("description"), f"{name} has no description"

        schema = tool.get("inputSchema")
        assert schema, f"{name} has no inputSchema"
        assert schema.get("type") == "object", f"{name} schema is not an object"

        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            assert required in properties, f"{name} requires '{required}' but never defines it"
