"""
Structural guards for the MCP tool registry.

The catalogue in mcp/tools.py and the dispatch table in mcp/registry.py are
maintained by hand in two different files. These tests keep them in agreement:
a tool the client can see must be callable, and a handler that exists must be
reachable.
"""

from unittest.mock import MagicMock

import pytest

from mcp.registry import ToolRegistry
from mcp.tools import get_all_tools


@pytest.fixture
def registry():
    """A registry over a client that is never actually called."""
    return ToolRegistry.from_client(MagicMock())


def test_registry_exposes_every_wired_name(registry):
    assert "vibemk_get_checkmk_hosts" in registry.tool_names()


def test_registry_returns_none_for_an_unknown_tool(registry):
    assert registry.handler_for("vibemk_not_a_tool") is None


def test_registry_returns_a_handler_with_a_handle_method(registry):
    handler = registry.handler_for("vibemk_get_checkmk_hosts")

    assert hasattr(handler, "handle")


def test_no_tool_is_declared_twice():
    names = [tool["name"] for tool in get_all_tools()]

    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert duplicates == [], f"declared more than once: {duplicates}"


def test_every_declared_tool_has_a_handler(registry):
    declared = {tool["name"] for tool in get_all_tools()}

    unroutable = sorted(name for name in declared if registry.handler_for(name) is None)
    assert unroutable == [], f"advertised to the client but not callable: {unroutable}"


def test_every_handler_is_declared_as_a_tool(registry):
    declared = {tool["name"] for tool in get_all_tools()}

    unreachable = sorted(name for name in registry.tool_names() if name not in declared)
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


def test_repository_root_is_not_a_python_package():
    """The checkout directory must not be importable as a package.

    An __init__.py at the repository root makes pytest treat the checkout
    directory itself as the root package, which only works while that
    directory's name happens to be a valid Python identifier. Renaming the
    repository to anything containing a hyphen then breaks collection of the
    whole suite with "attempted relative import with no known parent package".
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent

    assert not (root / "__init__.py").exists(), (
        "__init__.py at the repository root couples the test suite to the "
        "checkout directory's name; the importable packages are api, config, "
        "handlers, mcp, utils and checkmk_types"
    )
