# Codebase Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the remaining structural defects impossible to reintroduce — production code that branches on being tested, a disabled type checker, a dishonest protocol handshake, and metric windows that depend on the host's timezone.

**Architecture:** `mcp/server.py` splits into four modules with one job each — `transport.py` (stdio I/O), `dispatch.py` (JSON-RPC), `registry.py` (tool-to-handler table), and a thin `server.py` that wires them. mypy and ruff run in CI at their configured strictness from the first commit, with a one-way exception list for modules not yet clean.

**Tech Stack:** Python 3.8+, standard library only for the server itself. pytest + pytest-asyncio, black, isort, mypy (strict), ruff. No runtime dependencies may be added.

**Spec:** `docs/superpowers/specs/2026-09-07-codebase-hardening-design.md`

## Global Constraints

- **No runtime dependencies.** `dependencies = []` in `pyproject.toml` stays empty. The server uses only the standard library.
- **Python 3.8 floor.** `requires-python = ">=3.8"`; CI runs 3.8 through 3.12. No `match`, no `X | Y` annotations at runtime, no `tomllib`. Use `typing.Optional`, `typing.Dict`, `typing.Tuple`.
- **Tool names are frozen.** All 117 tool names keep the `vibemk_` prefix and their exact spelling. They are the compatibility surface for every existing client configuration.
- **TDD throughout.** The failing test is written first and watched failing for the expected reason before any implementation.
- **Formatting gates.** `black --check .` and `isort --check-only .` must pass before every commit. Line length 120.
- **New modules are born clean.** Any file created by this plan must pass `mypy` and `ruff` immediately and must never be added to an exception list.
- **Commit style.** Conventional commits (`fix:`, `feat:`, `refactor:`, `test:`, `ci:`, `docs:`) per `CONTRIBUTING.md`.
- **Verification command.** The full suite is `python -m pytest -q`. It must be green at the end of every task.

---

### Task 1: Metric time windows in UTC

**Files:**
- Modify: `handlers/metrics.py:38-59` (`_parse_time_range`)
- Test: `tests/test_handlers_metrics.py` (create)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `MetricsHandler._parse_time_range(self, time_range: str) -> Dict[str, str]`, returning keys `start` and `end` formatted `%Y-%m-%dT%H:%M:%SZ`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_handlers_metrics.py`:

```python
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


def test_window_is_the_same_instant_in_every_host_timezone(handler, monkeypatch, restore_timezone):
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
    "name,span",
    [("1h", timedelta(hours=1)), ("4h", timedelta(hours=4)), ("24h", timedelta(days=1)),
     ("7d", timedelta(days=7)), ("30d", timedelta(days=30))],
)
def test_named_ranges_span_their_duration(handler, name, span):
    window = handler._parse_time_range(name)

    assert as_utc(window["end"]) - as_utc(window["start"]) == span


def test_unknown_range_falls_back_to_one_hour(handler):
    window = handler._parse_time_range("not-a-range")

    assert as_utc(window["end"]) - as_utc(window["start"]) == timedelta(hours=1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_handlers_metrics.py -q`
Expected: FAIL. `test_window_is_the_same_instant_in_every_host_timezone` fails with three distinct starts; the format tests fail with `ValueError: time data '2026-09-07 15:56:16' does not match format '%Y-%m-%dT%H:%M:%SZ'`.

- [ ] **Step 3: Write minimal implementation**

Replace `handlers/metrics.py:38-59` with:

```python
    def _parse_time_range(self, time_range: str) -> Dict[str, str]:
        """Build the UTC window CheckMK expects for a named range.

        A timestamp without an offset is read as site local time, so a server
        whose host runs in a different zone than the site silently asks for the
        wrong window. UTC with a Z suffix — the form CheckMK's own
        reorganize_time_range docstring uses — removes that coupling.
        """
        from datetime import datetime, timedelta, timezone

        spans = {
            "1h": timedelta(hours=1),
            "4h": timedelta(hours=4),
            "24h": timedelta(days=1),
            "7d": timedelta(days=7),
            "30d": timedelta(days=30),
        }
        now = datetime.now(timezone.utc)
        start = now - spans.get(time_range, timedelta(hours=1))
        return {
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_handlers_metrics.py -q` → PASS (8 tests)
Run: `python -m pytest -q` → PASS, no regressions.

- [ ] **Step 5: Format and commit**

```bash
black . && isort .
git add handlers/metrics.py tests/test_handlers_metrics.py
git commit -m "fix: build metric time windows in UTC

CheckMK reads a timestamp with no offset as site local time. _parse_time_range
built naive local time, so the window was correct only while the server host
and the site shared a timezone. Measured against 2.4.0p2 CRE, a host in UTC
against a Europe/Berlin site got a window two hours early; America/New_York
got six. A container defaults to UTC, so the documented Docker deployment was
affected.

Now built from an aware UTC datetime and formatted with a Z suffix, the form
CheckMK's own reorganize_time_range docstring uses."
```

---

### Task 2: Honest protocol version negotiation

**Files:**
- Modify: `config/settings.py` (`MCPConfig`)
- Modify: `mcp/server.py:410-435` (`_handle_initialize`)
- Test: `tests/test_config.py` (append), `tests/test_mcp_server.py` (append)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `MCPConfig.supported_protocol_versions: Tuple[str, ...]` and `MCPConfig.negotiate_protocol_version(self, requested: Optional[str]) -> str`. Task 4 calls this method from `Dispatcher`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
class TestProtocolNegotiation:
    """The MCP spec requires answering with a version the server supports."""

    def test_a_supported_version_is_accepted(self):
        config = MCPConfig()

        assert config.negotiate_protocol_version("2024-11-05") == "2024-11-05"

    def test_an_unsupported_version_falls_back_to_the_preferred_one(self):
        config = MCPConfig()

        assert config.negotiate_protocol_version("1999-01-01-BOGUS") == config.protocol_version

    def test_a_missing_version_falls_back_to_the_preferred_one(self):
        config = MCPConfig()

        assert config.negotiate_protocol_version(None) == config.protocol_version

    def test_the_preferred_version_is_supported(self):
        config = MCPConfig()

        assert config.protocol_version in config.supported_protocol_versions
```

Append to `tests/test_mcp_server.py`:

```python
    @pytest.mark.asyncio
    async def test_initialize_never_echoes_an_unsupported_version(self, mcp_server):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "1999-01-01-BOGUS", "capabilities": {}},
        }

        response = await mcp_server.handle_request(request)

        answered = response["result"]["protocolVersion"]
        assert answered != "1999-01-01-BOGUS"
        assert answered in mcp_server.mcp_config.supported_protocol_versions
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py::TestProtocolNegotiation tests/test_mcp_server.py -q`
Expected: FAIL with `AttributeError: 'MCPConfig' object has no attribute 'negotiate_protocol_version'`, and the server test asserting `'1999-01-01-BOGUS' != '1999-01-01-BOGUS'`.

- [ ] **Step 3: Write minimal implementation**

In `config/settings.py`, add the field and method to `MCPConfig` (keep `protocol_version` as it is — it is the preferred version):

```python
    supported_protocol_versions: Tuple[str, ...] = ("2024-11-05",)

    def negotiate_protocol_version(self, requested: Optional[str]) -> str:
        """Return a protocol version this server actually speaks.

        The MCP specification requires the server to answer initialize with a
        version it supports. Echoing the client's string instead claims support
        for anything a client cares to name.
        """
        if requested in self.supported_protocol_versions:
            return requested
        return self.protocol_version
```

Add `Tuple` to the `typing` import at the top of the file.

In `mcp/server.py`, replace the body of `_handle_initialize` between `params = request.get("params", {})` and the `response = {` literal, and the `protocolVersion` line:

```python
        negotiated = self.mcp_config.negotiate_protocol_version(params.get("protocolVersion"))
        logger.info(
            "Protocol version negotiation: client=%s, answered=%s",
            params.get("protocolVersion"),
            negotiated,
        )

        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": self.mcp_config.server_name, "version": self.mcp_config.server_version},
            },
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py tests/test_mcp_server.py -q` → PASS
Run: `python -m pytest -q` → PASS

- [ ] **Step 5: Format and commit**

```bash
black . && isort .
git add config/settings.py mcp/server.py tests/test_config.py tests/test_mcp_server.py
git commit -m "fix: answer initialize with a protocol version the server supports

_handle_initialize echoed back whatever protocolVersion the client sent, so a
request naming 1999-01-01-BOGUS was answered with 1999-01-01-BOGUS. The MCP
specification requires the server to answer with a version it supports.

MCPConfig now holds the supported versions explicitly and negotiates against
them. Which versions to support is a separate decision; this makes the answer
honest at the one version supported today."
```

---

### Task 3: Extract `ToolRegistry`

**Files:**
- Create: `mcp/registry.py`
- Modify: `mcp/server.py` (`_setup_handlers` at 204-376 moves out; `_ensure_initialized` uses the registry)
- Test: `tests/test_tool_registry.py` (modify to target the registry)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `ToolRegistry.from_client(client: CheckMKClient) -> "ToolRegistry"`
  - `ToolRegistry.handler_for(self, tool_name: str) -> Optional[BaseHandler]`
  - `ToolRegistry.tool_names(self) -> FrozenSet[str]`
  Task 4 consumes all three.

- [ ] **Step 1: Write the failing test**

Replace the `handler_names` fixture in `tests/test_tool_registry.py` with one built on the registry, and add registry unit tests. The four existing structural assertions keep their names and meaning:

```python
import os
from unittest.mock import MagicMock, patch

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
```

Then change the four existing tests to take `registry` instead of `handler_names`, replacing `handler_names.get(name) is None` with `registry.handler_for(name) is None` and `name not in handler_names` with `name not in registry.tool_names()`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tool_registry.py -q`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'mcp.registry'`.

- [ ] **Step 3: Write minimal implementation**

Create `mcp/registry.py`. Move the 22 handler instantiations and the tool-to-handler dict literal from `mcp/server.py:204-376` **verbatim** — the mapping is correct and guarded by tests; only its location changes.

```python
"""
Tool-to-handler registry for vibeMK

Owns the mapping from an MCP tool name to the handler that serves it. Knows
nothing about the JSON-RPC protocol or about transport.
"""

from typing import Dict, FrozenSet, Optional

from api import CheckMKClient
from handlers.acknowledgements import AcknowledgementHandler
# ... the remaining 21 handler imports, moved from mcp/server.py
from handlers.base import BaseHandler


class ToolRegistry:
    """Maps MCP tool names to the handler instances that serve them."""

    def __init__(self, handlers: Dict[str, BaseHandler]) -> None:
        self._handlers = handlers

    @classmethod
    def from_client(cls, client: CheckMKClient) -> "ToolRegistry":
        """Build every handler against one CheckMK client."""
        connection_handler = ConnectionHandler(client)
        # ... the remaining 21 instantiations, moved verbatim
        return cls(
            {
                # the tool-to-handler dict literal, moved verbatim
            }
        )

    def handler_for(self, tool_name: str) -> Optional[BaseHandler]:
        """Return the handler for a tool, or None when it is not registered."""
        return self._handlers.get(tool_name)

    def tool_names(self) -> FrozenSet[str]:
        """Every tool name this registry can route."""
        return frozenset(self._handlers)
```

Note: the moved instantiations become local variables rather than `self.*` attributes. `AcknowledgementHandler` and `DiscoveryHandler` do not inherit `BaseHandler`; annotate the dict as `Dict[str, BaseHandler]` anyway and leave those two as they are — Task 8 addresses the inheritance inconsistency if it proves to matter for mypy. If mypy objects at that point, widen the annotation to `Dict[str, Any]` with a comment naming the two classes.

In `mcp/server.py`, replace `_setup_handlers` with registry construction inside `_ensure_initialized`:

```python
            self.registry = ToolRegistry.from_client(self.client)
            logger.info("Registry initialized: %d tools available", len(self.registry.tool_names()))
```

Replace the lookup in `_handle_tools_call`:

```python
        handler = self.registry.handler_for(tool_name)
```

Keep `self.handlers` as a property returning the registry's internal dict **only if** other code still reads it; grep first with `grep -rn "\.handlers" --include='*.py' .` and delete the attribute if nothing does.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tool_registry.py -q` → PASS
Run: `python -m pytest -q` → PASS
Run the end-to-end check:

```bash
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{}}}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
 | python main.py 2>/dev/null | python -c "import sys,json; print(len(json.loads(sys.stdin.readlines()[1])['result']['tools']), 'tools')"
```
Expected: `117 tools`

- [ ] **Step 5: Verify the new module is born clean**

Run: `mypy mcp/registry.py --ignore-missing-imports` → no errors
Run: `ruff check mcp/registry.py` → no findings

Fix anything reported before committing. This module must never enter an exception list.

- [ ] **Step 6: Format and commit**

```bash
black . && isort .
git add mcp/registry.py mcp/server.py tests/test_tool_registry.py
git commit -m "refactor: move the tool-to-handler table into ToolRegistry

The mapping is unchanged and still guarded by the structural tests; only its
location moves. Separating it from mcp/server.py is the first of three steps
that let the dispatch layer be tested without the server constructing a
CheckMK client."
```

---

### Task 4: Extract `Dispatcher` and delete the test scaffolding

**Files:**
- Create: `mcp/dispatch.py`
- Modify: `mcp/server.py` (remove lines 74-164 and the JSON-RPC methods; delegate to the dispatcher)
- Test: `tests/test_mcp_server.py` (rewrite to target `Dispatcher`)

**Interfaces:**
- Consumes: `ToolRegistry.handler_for`, `ToolRegistry.tool_names` (Task 3); `MCPConfig.negotiate_protocol_version` (Task 2).
- Produces: `Dispatcher(registry_provider: Callable[[], ToolRegistry], config: MCPConfig)` with `async def handle(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]`. Task 5 passes `Dispatcher.handle` to the transport.

**Why a provider rather than a registry:** the CheckMK connection is established on the first tool call, not at startup, so a misconfigured server still answers `initialize` and `tools/list` and reports the configuration error as tool output. The dispatcher calls the provider only when it needs a handler, and turns a raised error into tool content.

- [ ] **Step 1: Write the failing test**

Rewrite `tests/test_mcp_server.py` entirely. A stub registry replaces every use of `unittest.mock` introspection:

```python
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
        response = await make_dispatcher().handle(
            request("initialize", {"protocolVersion": "1999-01-01-BOGUS"})
        )

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mcp_server.py -q`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'mcp.dispatch'`.

- [ ] **Step 3: Write minimal implementation**

Create `mcp/dispatch.py`, moving the JSON-RPC logic from `mcp/server.py:377-496` and adapting the registry lookup:

```python
"""
JSON-RPC dispatch for vibeMK

Implements the MCP methods over plain dictionaries. Performs no I/O and knows
nothing about how a request arrived.
"""

from typing import Any, Callable, Dict, Optional

from config import MCPConfig
from mcp.registry import ToolRegistry
from mcp.tools import get_all_tools
from utils import get_logger

logger = get_logger(__name__)

CONFIGURATION_HELP = (
    "Please set the required environment variables:\n"
    "- CHECKMK_SERVER_URL\n- CHECKMK_SITE\n- CHECKMK_USERNAME\n- CHECKMK_PASSWORD"
)


class Dispatcher:
    """Routes MCP requests to handlers and shapes JSON-RPC responses."""

    def __init__(self, registry_provider: Callable[[], ToolRegistry], config: MCPConfig) -> None:
        self._registry_provider = registry_provider
        self._config = config

    async def handle(self, request: Any) -> Optional[Dict[str, Any]]:
        """Handle one MCP request; returns None for notifications."""
        if not isinstance(request, dict):
            return self._error(None, -32600, "Invalid Request: must be an object")

        request_id = request.get("id")
        if "method" not in request:
            return self._error(request_id, -32600, "Invalid Request: missing required field 'method'")
        if request.get("jsonrpc") != "2.0":
            return self._error(request_id, -32600, "Invalid Request: missing or invalid 'jsonrpc' field")

        method = request["method"]
        try:
            if method == "initialize":
                return self._initialize(request)
            if method == "notifications/initialized":
                return None
            if method == "tools/list":
                return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": get_all_tools()}}
            if method == "tools/call":
                return await self._call_tool(request)
            return self._error(request_id, -32601, "Method not found: {}".format(method))
        except Exception as error:  # noqa: BLE001 - the loop must survive any handler
            logger.exception("Error handling request %s", method)
            return self._error(request_id, -32603, "Internal error: {}".format(error))

    def _initialize(self, request: Dict[str, Any]) -> Dict[str, Any]:
        params = request.get("params", {})
        negotiated = self._config.negotiate_protocol_version(params.get("protocolVersion"))
        logger.info("Protocol negotiation: client=%s, answered=%s", params.get("protocolVersion"), negotiated)
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": self._config.server_name, "version": self._config.server_version},
            },
        }

    async def _call_tool(self, request: Dict[str, Any]) -> Dict[str, Any]:
        request_id = request.get("id")
        params = request.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        try:
            registry = self._registry_provider()
        except Exception as error:  # noqa: BLE001 - reported as tool content, not a crash
            logger.exception("CheckMK configuration is unusable")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "❌ **CheckMK Configuration Error**\n\n{}\n\n{}".format(
                                error, CONFIGURATION_HELP
                            ),
                        }
                    ]
                },
            }

        handler = registry.handler_for(tool_name)
        if handler is None:
            return self._error(request_id, -32601, "Unknown tool: {}".format(tool_name))

        try:
            content = await handler.handle(tool_name, arguments)
        except Exception as error:  # noqa: BLE001 - one bad tool must not end the session
            logger.exception("Error in tool call %s", tool_name)
            return self._error(request_id, -32603, "Internal error in {}: {}".format(tool_name, error))
        return {"jsonrpc": "2.0", "id": request_id, "result": {"content": content}}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
```

In `mcp/server.py`, **delete** lines 74-164 in their entirety — `_init_for_tests`, `_create_test_handlers`, `_setup_test_handlers`, `_detect_test_mode` — along with `self._test_mode`, and delete the JSON-RPC methods now living in the dispatcher. `CheckMKMCPServer` keeps `handle_request` as a thin delegation to `self._dispatcher.handle` until Task 5 removes it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mcp_server.py -q` → PASS (14 tests)
Run: `python -m pytest -q` → PASS

- [ ] **Step 5: Verify the scaffolding is gone**

```bash
grep -rn "unittest.mock" mcp/ ; echo "exit=$?"
grep -rn "TestHandler\|_test_mode\|_detect_test_mode" mcp/ ; echo "exit=$?"
```
Expected: no output from either grep (exit 1).

Run: `mypy mcp/dispatch.py --ignore-missing-imports` → no errors
Run: `ruff check mcp/dispatch.py` → no findings

- [ ] **Step 6: Format and commit**

```bash
black . && isort .
git add mcp/dispatch.py mcp/server.py tests/test_mcp_server.py
git commit -m "refactor: move JSON-RPC dispatch out of the server, drop the test scaffolding

_detect_test_mode asked unittest.mock whether the object had been mocked and
branched production behaviour on the answer. Around 100 lines existed only to
serve nine tests, while the other 116 already built real handlers around a
mocked client.

The dispatcher takes a registry provider rather than a registry so the CheckMK
connection stays lazy: a misconfigured server still answers initialize and
tools/list and reports the configuration error as tool content."
```

---

### Task 5: Extract `StdioTransport` and reduce `server.py` to wiring

**Files:**
- Create: `mcp/transport.py`
- Modify: `mcp/server.py` (becomes wiring only)
- Test: `tests/test_transport.py` (create)

**Interfaces:**
- Consumes: `Dispatcher.handle` (Task 4) as the `handle` callable.
- Produces: `StdioTransport(handle, stdin=sys.stdin, stdout=sys.stdout)` with `async def run(self) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_transport.py`:

```python
"""
Tests for the stdio transport.

The streams are injected, so the loop is exercised without a subprocess.
"""

import io
import json

import pytest

from mcp.transport import StdioTransport


def responder(response=None):
    async def handle(request):
        if response is None:
            return {"jsonrpc": "2.0", "id": request.get("id"), "result": {"echo": request.get("method")}}
        return response

    return handle


def run_with(lines, handle):
    stdin = io.StringIO("".join(line + "\n" for line in lines))
    stdout = io.StringIO()
    import asyncio

    asyncio.run(StdioTransport(handle, stdin=stdin, stdout=stdout).run())
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]


def test_a_request_produces_one_response_line():
    written = run_with(['{"jsonrpc":"2.0","id":1,"method":"tools/list"}'], responder())

    assert written == [{"jsonrpc": "2.0", "id": 1, "result": {"echo": "tools/list"}}]


def test_a_malformed_line_is_skipped_and_the_loop_continues():
    written = run_with(["not json at all", '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'], responder())

    assert [r["id"] for r in written] == [2]


def test_a_blank_line_is_skipped():
    written = run_with(["", '{"jsonrpc":"2.0","id":3,"method":"tools/list"}'], responder())

    assert [r["id"] for r in written] == [3]


def test_a_notification_writes_nothing():
    async def handle(request):
        return None

    assert run_with(['{"jsonrpc":"2.0","method":"notifications/initialized"}'], handle) == []


def test_the_loop_ends_at_end_of_input():
    # Completing at all is the assertion: a loop that does not stop hangs here.
    assert run_with([], responder()) == []


def test_non_ascii_content_survives_the_round_trip():
    written = run_with(
        ['{"jsonrpc":"2.0","id":4,"method":"tools/list"}'],
        responder({"jsonrpc": "2.0", "id": 4, "result": {"text": "Grüße 🚀"}}),
    )

    assert written[0]["result"]["text"] == "Grüße 🚀"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_transport.py -q`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'mcp.transport'`.

- [ ] **Step 3: Write minimal implementation**

Create `mcp/transport.py`, moving the loop from `mcp/server.py:497-547`:

```python
"""
Stdio transport for vibeMK

Reads newline-delimited JSON requests from a stream and writes responses back.
Knows nothing about CheckMK, about tools, or about JSON-RPC semantics beyond
"a request may produce a response, or none".
"""

import json
import sys
from typing import Any, Awaitable, Callable, Dict, Optional, TextIO

from utils import get_logger

logger = get_logger(__name__)

RequestHandler = Callable[[Any], Awaitable[Optional[Dict[str, Any]]]]


class StdioTransport:
    """Serves MCP requests over newline-delimited JSON on a pair of streams."""

    def __init__(self, handle: RequestHandler, stdin: TextIO = None, stdout: TextIO = None) -> None:
        self._handle = handle
        self._stdin = stdin if stdin is not None else sys.stdin
        self._stdout = stdout if stdout is not None else sys.stdout

    async def run(self) -> None:
        """Read requests until the input ends."""
        logger.info("Transport ready, waiting for requests on stdin")
        while True:
            try:
                line = self._stdin.readline()
                if not line:
                    logger.info("Input closed, shutting down")
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    request = json.loads(line)
                except json.JSONDecodeError as error:
                    logger.error("Skipping unparseable line: %s", error)
                    continue

                response = await self._handle(request)
                if response is not None:
                    self._write(response)

            except KeyboardInterrupt:
                logger.info("Stopped by user")
                break
            except EOFError:
                logger.info("EOF reached, exiting")
                break
            except Exception:  # noqa: BLE001 - one bad request must not end the session
                logger.exception("Unexpected error in the transport loop, continuing")
                continue

        logger.info("Transport shutdown complete")

    def _write(self, response: Dict[str, Any]) -> None:
        self._stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        self._stdout.flush()
```

Rewrite `mcp/server.py` as wiring only:

```python
"""
vibeMK MCP Server

Wires the transport, the dispatcher and the tool registry together. The
CheckMK connection is established on the first tool call, not at startup, so a
misconfigured server still answers initialize and tools/list.
"""

from typing import Optional

from api import CheckMKClient
from config import CheckMKConfig, MCPConfig
from mcp.dispatch import Dispatcher
from mcp.registry import ToolRegistry
from mcp.transport import StdioTransport
from utils import get_logger

logger = get_logger(__name__)


class CheckMKMCPServer:
    """vibeMK MCP server for CheckMK integration."""

    def __init__(self) -> None:
        self.mcp_config = MCPConfig()
        self._registry: Optional[ToolRegistry] = None
        self._dispatcher = Dispatcher(self._registry_provider, self.mcp_config)
        self._transport = StdioTransport(self._dispatcher.handle)

    def _registry_provider(self) -> ToolRegistry:
        """Build the registry on first use; raises when configuration is unusable."""
        if self._registry is None:
            logger.info("Initializing CheckMK connection for the first tool call")
            config = CheckMKConfig.from_env()
            logger.info("CheckMK config loaded: %s site=%s user=%s", config.server_url, config.site, config.username)
            self._registry = ToolRegistry.from_client(CheckMKClient(config))
            logger.info("Registry initialized: %d tools", len(self._registry.tool_names()))
        return self._registry

    async def run(self) -> None:
        """Serve MCP requests on stdio until the input ends."""
        logger.info("Starting vibeMK %s", self.mcp_config.server_version)
        await self._transport.run()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_transport.py -q` → PASS (6 tests)
Run: `python -m pytest -q` → PASS

- [ ] **Step 5: Verify the acceptance criteria from the spec**

```bash
wc -l mcp/server.py            # expected: under 120
grep -rn "unittest.mock" mcp/  # expected: no output
mypy mcp/transport.py mcp/server.py --ignore-missing-imports
ruff check mcp/transport.py mcp/server.py
```

End-to-end, including the misconfigured case:

```bash
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{}}}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
 '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"vibemk_get_checkmk_version","arguments":{}}}' \
 | env -u CHECKMK_SERVER_URL -u CHECKMK_SITE -u CHECKMK_USERNAME -u CHECKMK_PASSWORD python main.py 2>/dev/null
```
Expected: three response lines. The third contains "CheckMK Configuration Error" as tool content, **not** a JSON-RPC error — this is the lazy-initialisation guarantee the spec calls out as the likeliest thing to break.

- [ ] **Step 6: Format and commit**

```bash
black . && isort .
git add mcp/transport.py mcp/server.py tests/test_transport.py
git commit -m "refactor: split the stdio loop out; server.py is wiring only

mcp/server.py did three jobs in 547 lines. It now builds the four parts and
starts the transport. Injecting the streams lets the loop be tested without a
subprocess, which is how the malformed-line and end-of-input paths get their
first tests.

A second transport for streamable HTTP (upstream issue #2) becomes an
alternative against the same dispatcher rather than a parallel implementation."
```

---

### Task 6: Ratchet infrastructure for mypy and ruff

**Files:**
- Modify: `.github/workflows/ci.yml:43-47`
- Modify: `pyproject.toml` (exception lists)
- Test: `tests/test_lint_ratchet.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: the exception lists that Tasks 7-10 shrink.

**Note:** run this task *after* Tasks 3-5, as the spec sequences it. `mcp/server.py` carries 77 of the 272 mypy errors and is rewritten by those tasks, so building the list first would list modules that no longer exist in that form.

- [ ] **Step 1: Write the failing test**

Create `tests/test_lint_ratchet.py`:

```python
"""
Keeps the type-checking and linting exception lists one-way.

Both tools run in CI at their configured strictness. Modules that do not pass
yet are listed explicitly in pyproject.toml. This test fails when an entry is
no longer needed, so the list has to shrink rather than quietly persist.
"""

import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def read_mypy_exceptions():
    """Modules listed with ignore_errors = true in pyproject.toml."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(
        r'\[\[tool\.mypy\.overrides\]\]\s*\nmodule\s*=\s*\[(.*?)\]\s*\nignore_errors\s*=\s*true',
        text,
        re.S,
    )
    if not block:
        return []
    return re.findall(r'"([^"]+)"', block.group(1))


def module_to_path(module: str) -> pathlib.Path:
    return ROOT / (module.replace(".", "/") + ".py")


@pytest.mark.skipif(sys.platform == "win32", reason="mypy invocation differs on Windows")
def test_every_mypy_exception_is_still_needed():
    stale = []
    for module in read_mypy_exceptions():
        path = module_to_path(module)
        if not path.exists():
            stale.append("{} (file is gone)".format(module))
            continue
        result = subprocess.run(
            [sys.executable, "-m", "mypy", str(path), "--ignore-missing-imports", "--no-incremental"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            stale.append("{} (now clean)".format(module))

    assert stale == [], (
        "these entries are no longer needed and must be removed from the "
        "mypy overrides in pyproject.toml: {}".format(stale)
    )


def test_every_ruff_exception_is_still_needed():
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", ".", "--output-format=concise"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    reported = set(re.findall(r"^(\S+?):\d+:\d+: ([A-Z]+\d+)", result.stdout, re.M))

    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r'\[tool\.ruff\.lint\.per-file-ignores\]\n(.*?)(?=\n\[|\Z)', text, re.S)
    stale = []
    if block:
        for line in block.group(1).splitlines():
            entry = re.match(r'"([^"]+)"\s*=\s*\[(.*)\]', line.strip())
            if not entry:
                continue
            path, codes = entry.group(1), re.findall(r'"([^"]+)"', entry.group(2))
            for code in codes:
                if (path, code) not in reported:
                    stale.append("{}: {}".format(path, code))

    assert stale == [], (
        "these per-file-ignores no longer suppress anything and must be "
        "removed from pyproject.toml: {}".format(stale)
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_lint_ratchet.py -q`
Expected: PASS trivially, because no exception lists exist yet. This is the one place in this plan where the guard cannot fail first — it guards a list that Step 3 creates. Proceed to Step 3, then return here: after the lists exist, deliberately add a bogus entry (`"handlers.base"` to the mypy list, which is already clean), re-run, and confirm the test fails with "now clean". Remove the bogus entry before continuing.

- [ ] **Step 3: Build the exception lists**

Generate the current mypy offenders:

```bash
python -m mypy . --ignore-missing-imports 2>&1 \
  | grep -oE '^[^:]+\.py' | sort -u \
  | sed 's|/|.|g; s|\.py$||; s|^|    "|; s|$|",|'
```

Add to `pyproject.toml`, after the existing `[[tool.mypy.overrides]]` block for tests:

```toml
# Modules not yet type-clean. This list may only shrink: tests/test_lint_ratchet.py
# fails when an entry is no longer needed. Do not add to it — a module you
# change must be brought up to the configured strictness.
[[tool.mypy.overrides]]
module = [
    # ... generated list
]
ignore_errors = true
```

Generate the ruff per-file ignores:

```bash
python -m ruff check . --output-format=concise \
  | awk -F: '{print $1}' | sort | uniq -c | sort -rn
```

For each offending file, add its rule codes under:

```toml
[tool.ruff.lint.per-file-ignores]
# Same rule as the mypy list above: this may only shrink.
"handlers/downtimes.py" = ["ANN001", "ANN201", "..."]
```

Also move `select` and `ignore` from `[tool.ruff]` to `[tool.ruff.lint]` — modern ruff warns that the top-level keys are deprecated, and `per-file-ignores` belongs under `lint` regardless.

- [ ] **Step 4: Enable both tools in CI**

Replace `.github/workflows/ci.yml:43-47` with:

```yaml
    - name: Type check with mypy
      run: |
        mypy .
      env:
        PYTHONPATH: ${{ github.workspace }}

    - name: Lint with ruff
      run: |
        ruff check .
      env:
        PYTHONPATH: ${{ github.workspace }}
```

Add `ruff>=0.1.0` to the `dev` extra in `pyproject.toml` if it is not already listed.

- [ ] **Step 5: Verify locally, then verify the guard bites**

```bash
mypy .          # expected: Success
ruff check .    # expected: All checks passed
python -m pytest -q
```

Then the deliberate check from Step 2: add `"handlers.base"` to the mypy override list, run `python -m pytest tests/test_lint_ratchet.py -q`, confirm it fails naming that module, and remove it.

- [ ] **Step 6: Commit**

```bash
black . && isort .
git add pyproject.toml .github/workflows/ci.yml tests/test_lint_ratchet.py
git commit -m "ci: run mypy and ruff, with a one-way exception list

Both were configured and neither ran: the mypy step was an echo and ruff was
never invoked, so 272 type errors and 1141 lint findings accumulated behind a
green build.

Both now run at their configured strictness. Modules that do not pass yet are
listed explicitly, and a test fails when an entry stops being necessary — the
list can only shrink, and a module that is clean cannot silently regress."
```

---

### Task 7: Clear `api/`, `config/`, `utils/` and `main.py`

**Files:**
- Modify: `api/client.py`, `config/settings.py`, `config/dotenv.py`, `main.py`
- Modify: `pyproject.toml` (remove the cleared entries from both lists)

**Interfaces:**
- Consumes: the exception lists from Task 6.
- Produces: those four modules removed from both lists.

**Scope:** 16 mypy errors (`api/client.py` 2, `config/settings.py` 8, `config/dotenv.py` 1, `main.py` 6) and roughly 70 ruff findings, dominated by `api/client.py` at 60.

- [ ] **Step 1: See exactly what is reported**

```bash
mypy api/client.py config/settings.py config/dotenv.py main.py --ignore-missing-imports
ruff check api/ config/ main.py utils/
```

Record the output. There is no test to write first here: the tools' output *is* the failing state, and the existing suite is the regression net.

- [ ] **Step 2: Fix one module at a time**

For each of the four, in this order — `config/dotenv.py`, `main.py`, `config/settings.py`, `api/client.py`:

1. Add the missing parameter and return annotations (ANN001/ANN201, `no-untyped-def`).
2. Replace f-strings in logging calls with `%s` placeholders (G004): `logger.info(f"Detected API URL: {base_url}")` becomes `logger.info("Detected API URL: %s", base_url)`.
3. Replace the bare `except:` at `api/client.py:219` with `except (ValueError, AttributeError):` — it guards a `json.loads` of an error body.
4. Move function-level imports to the top of the module (PLC0415) unless the import is there to break a cycle; if it is, leave it and add the code to that file's ignore list with a comment naming the cycle.
5. Run that module's tests immediately: `python -m pytest tests/test_api_client.py -q`, `tests/test_config.py`, `tests/test_dotenv.py`.

- [ ] **Step 3: Remove the cleared entries from both lists**

Delete `api.client`, `config.settings`, `config.dotenv` and `main` from the mypy override list, and their entries from `per-file-ignores`.

- [ ] **Step 4: Verify**

```bash
mypy .          # Success
ruff check .    # All checks passed
python -m pytest -q
```

`tests/test_lint_ratchet.py` passing is the proof the entries were actually removable.

- [ ] **Step 5: Format and commit**

```bash
black . && isort .
git add api/ config/ main.py utils/ pyproject.toml
git commit -m "refactor: bring api, config, utils and main up to the configured strictness

Annotations, %s-style logging arguments, and the bare except in the client's
error path. Removes these modules from the mypy and ruff exception lists."
```

---

### Task 8: Clear the smaller handlers

**Files:**
- Modify: `handlers/acknowledgements.py`, `handlers/rulesets.py`, `handlers/user_roles.py`, `handlers/timeperiods.py`, `handlers/services.py`, `handlers/debug.py`, `handlers/connection.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: the exception lists.
- Produces: these seven modules removed from both lists.

**Scope:** 21 mypy errors and roughly 150 ruff findings.

- [ ] **Step 1: See what is reported**

```bash
mypy handlers/acknowledgements.py handlers/rulesets.py handlers/user_roles.py \
     handlers/timeperiods.py handlers/services.py handlers/debug.py handlers/connection.py \
     --ignore-missing-imports
ruff check handlers/acknowledgements.py handlers/rulesets.py handlers/user_roles.py \
           handlers/timeperiods.py handlers/services.py handlers/debug.py handlers/connection.py
```

- [ ] **Step 2: Fix one module at a time, running its tests after each**

Same four moves as Task 7, plus two specific to these files:

- `handlers/acknowledgements.py` and `handlers/discovery.py` do not inherit `BaseHandler`, unlike the other 20 handlers. Make them inherit it. `AcknowledgementHandler.__init__` sets `_checkMK_version` and `_endpoints_tested` — keep those, call `super().__init__(client)`, and drop its own `self.client = client`. This removes the `Dict[str, BaseHandler]` friction noted in Task 3 and gives both classes `error_response`, `success_response` and `_if_match_header`.
- Bare `except:` at `handlers/acknowledgements.py:63` and `handlers/connection.py:117`: narrow to the exception actually expected, or `except Exception:` with a `# noqa: BLE001` and a comment saying why the broad catch is correct there.

After each module: `python -m pytest -q`.

- [ ] **Step 3: Remove the cleared entries and verify**

```bash
mypy .          # Success
ruff check .    # All checks passed
python -m pytest -q
```

- [ ] **Step 4: Format and commit**

```bash
black . && isort .
git add handlers/ pyproject.toml
git commit -m "refactor: bring the smaller handlers up to the configured strictness

Also makes AcknowledgementHandler and DiscoveryHandler inherit BaseHandler
like the other twenty, so the registry's handler type is honest and both gain
the shared response helpers."
```

---

### Task 9: Clear the large handlers

**Files:**
- Modify: `handlers/downtimes.py`, `handlers/hosts.py`, `handlers/metrics.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: the exception lists.
- Produces: these three modules removed from the lists, **except** the complexity rules, which may remain with a written justification.

**Scope:** 57 mypy errors and roughly 230 ruff findings — the largest single task in this plan.

- [ ] **Step 1: Fix `handlers/metrics.py` (12 mypy, 41 ruff)**

Annotations, `%s` logging, function-level imports. Run `python -m pytest tests/test_handlers_metrics.py -q` after.

- [ ] **Step 2: Fix `handlers/hosts.py` (10 mypy, 82 ruff)**

Same moves. Note the bare `except:` at three sites (161, 175, 991) — narrow each. Run `python -m pytest tests/test_handlers_hosts.py -q` after.

- [ ] **Step 3: Fix `handlers/downtimes.py` (35 mypy, 110 ruff)**

Same moves. This file is 1247 lines with 20 methods; work method by method and run `python -m pytest tests/test_handlers_downtimes.py -q` after each group of related methods rather than at the end.

- [ ] **Step 4: Decide on the complexity rules**

`PLR0911` (too many return statements) and `PLR0912` (too many branches) are the only rules left. They describe real structural problems — `handlers/services.py::_get_service_status` with its four-step fallback chain is the clearest example — and fixing them means restructuring, not annotating.

Choose one, and record the choice in the commit message:

- **Restructure now** if the method is small enough to hold in your head. `_get_service_status` is the candidate: each fallback is independent and could become a small function returning `Optional[dict]`, with the caller trying them in order.
- **Defer** otherwise. Keep the code in `per-file-ignores` for `PLR0911`/`PLR0912` only, with a comment naming the method and stating that restructuring it is separate work. This is explicitly allowed by the spec.

- [ ] **Step 5: Verify**

```bash
mypy .          # Success
ruff check .    # All checks passed
python -m pytest -q
```

- [ ] **Step 6: Format and commit**

```bash
black . && isort .
git add handlers/ pyproject.toml
git commit -m "refactor: bring the large handlers up to the configured strictness

downtimes, hosts and metrics carried 57 of the type errors and 230 of the lint
findings between them. Annotations, %s-style logging, narrowed bare excepts."
```

---

### Task 10: Clear the test modules and close the ratchet

**Files:**
- Modify: `tests/*.py` as listed by the tools
- Modify: `pyproject.toml` (the lists should end empty or near-empty)

**Interfaces:**
- Consumes: the exception lists.
- Produces: empty or minimal lists.

**Scope:** roughly 100 mypy errors across nine test modules, most of them `no-untyped-def` on test functions.

- [ ] **Step 1: Decide the strictness for tests**

`pyproject.toml` already has `[[tool.mypy.overrides]] module = "tests.*"` with `disallow_untyped_defs = false`, added during an earlier migration. Two coherent positions:

- **Annotate the tests too.** Consistent, and catches real mistakes in fixtures.
- **Keep tests exempt from `disallow_untyped_defs` only**, and require everything else. Test functions returning `None` gain little from `-> None` on every one.

Take the second: keep the existing override, and remove the test modules from the *blanket* `ignore_errors` list so their other errors — wrong argument types, bad attribute access — are caught. Record this choice in the commit message.

- [ ] **Step 2: Fix the remaining test errors**

```bash
mypy tests/ --ignore-missing-imports
```

Expect mostly `arg-type` and `attr-defined` on mock objects. Where a mock genuinely has no static type, annotate the fixture's return as `Any` rather than contorting the test.

`tests/conftest.py:43-44` reports `method-assign` for assigning `MagicMock()` to `client.get`. That is the fixture's whole purpose; add `# type: ignore[method-assign]` with a comment on those two lines rather than restructuring the fixture.

- [ ] **Step 3: Empty the lists**

Remove every remaining entry from the mypy `ignore_errors` block and from `per-file-ignores`, except any `PLR0911`/`PLR0912` deferral recorded in Task 9. If the `ignore_errors` block ends empty, delete the block.

- [ ] **Step 4: Verify the end state**

```bash
mypy .          # Success: no issues found
ruff check .    # All checks passed
python -m pytest -q
black --check . && isort --check-only .
wc -l mcp/server.py                      # under 120
grep -rn "unittest.mock" mcp/            # no output
```

- [ ] **Step 5: Update the changelog and commit**

Add to `CHANGELOG.md` under a new `## [Unreleased]` heading:

```markdown
## [Unreleased]

### Changed
- `mcp/server.py` split into `transport.py` (stdio I/O), `dispatch.py` (JSON-RPC) and
  `registry.py` (tool-to-handler table); the server is now wiring only. The test
  scaffolding that made production behaviour depend on whether the code was being
  tested is gone
- `initialize` answers with a protocol version the server supports instead of echoing
  the client's
- Metric time windows are built in UTC, so they no longer depend on the server host's
  timezone matching the CheckMK site's

### Added
- mypy and ruff run in CI at their configured strictness, behind an exception list that
  a test keeps one-way
```

```bash
black . && isort .
git add tests/ pyproject.toml CHANGELOG.md
git commit -m "refactor: bring the test modules under type checking

Tests keep their exemption from mandatory return annotations — a test function
returning None gains nothing from being told so — but no longer sit behind a
blanket ignore_errors, so wrong argument types and bad attribute access in
fixtures are caught."
```

---

## Self-Review

**Spec coverage:**

| Spec section | Tasks |
|---|---|
| 1. Splitting `mcp/server.py` | 3, 4, 5 |
| 1. Migrating the nine tests | 4 (rewritten as 14 dispatcher tests) |
| 1. Acceptance (no `unittest.mock` in `mcp/`, server under 120 lines) | 4 Step 5, 5 Step 5, 10 Step 4 |
| 2. Ratchet mechanism | 6 |
| 2. Order of work (annotations, autofix, handlers, complexity last) | 7, 8, 9, 10 |
| 2. Acceptance (CI steps, guard test, lists smaller) | 6 Step 4-5, 10 Step 4 |
| 3. Protocol negotiation | 2 |
| 4. `metrics.py` UTC windows | 1 |
| Sequence (metrics → protocol → split → ratchet) | Task order 1, 2, 3-5, 6-10 |
| Risk: lazy initialisation preserved | 4 (registry provider), 5 Step 5 (end-to-end check) |
| Risk: annotations per module, tests run immediately | 7 Step 2, 8 Step 2, 9 Steps 1-3 |

No gaps.

**Placeholder scan:** no "TBD", "TODO", "implement later", or "similar to Task N". The one place code is not reproduced inline is Task 3's move of the 170-line handler table, which cites exact source lines (`mcp/server.py:204-376`) and states the move is verbatim — the code exists and is under test.

**Type consistency:** `ToolRegistry.handler_for` / `tool_names` / `from_client` are named identically in Tasks 3, 4 and 5. `Dispatcher(registry_provider, config)` and `Dispatcher.handle` match between Tasks 4 and 5. `MCPConfig.negotiate_protocol_version` and `supported_protocol_versions` match between Tasks 2 and 4. `StdioTransport(handle, stdin, stdout)` and `run()` match between Task 5's implementation and its tests.

**Known deviation:** Task 6 Step 2 cannot watch its guard fail before the thing it guards exists. The step says so and prescribes a deliberate failure check immediately after the lists are built, rather than pretending the normal cycle applies.
