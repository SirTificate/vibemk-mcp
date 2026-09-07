# Codebase hardening

Design for the four remaining items from the September 2026 code review and
the CheckMK 2.4 endpoint audit.

Status: approved 2026-09-07. Supersedes nothing.

## Why

The functional defects the review found are fixed. What remains are structural
problems that let those defects survive for a year unnoticed:

- Production code branches on whether it is being tested, so the tests cannot
  be trusted to describe production behaviour.
- The type checker is disabled in CI and the linter never runs, although both
  are configured.
- The server claims to speak any MCP protocol version a client names.
- Metric time windows are correct only while the server host and the CheckMK
  site share a timezone.

The goal is a codebase where a regression is caught by CI rather than by a
user, and where the next contributor can change one part without reading all
of it.

## Non-goals

- Adopting pull request #3 (agent bakery, audit log, aux tags, sites, event
  console). It targets endpoints that exist, but it is +3314 lines with an
  unchecked test plan, and it is far easier to judge against a codebase that
  already type-checks. Separate work, after this.
- Changing the advertised MCP protocol version. Correcting the negotiation is
  in scope; choosing a newer version changes externally visible behaviour and
  is decided separately once negotiation is correct.
- Making `CheckMKClient` asynchronous. It blocks the event loop, but with a
  serial stdio transport nothing else is waiting. Revisit if an HTTP transport
  lands.
- Reducing the configured strictness of mypy or ruff to make CI pass. The
  configured level is the target; the ratchet is how we get there.

## 1. Splitting `mcp/server.py`

### Problem

`mcp/server.py` is 547 lines doing three jobs: reading stdin and writing
stdout, routing JSON-RPC methods, and building the tool-to-handler table.
Around 100 of those lines exist only to serve tests:

| Lines | Member | Purpose |
|-------|--------|---------|
| 74–83 | `_init_for_tests` | Build config in the constructor, swallowing failure |
| 84–115 | `_create_test_handlers` | Assign 22 `TestHandler` stubs as attributes |
| 116–153 | `_setup_test_handlers` | A second, partial copy of the dispatch table |
| 154–164 | `_detect_test_mode` | Ask `unittest.mock` whether we are under test |
| 454–459 | (call site) | Branch every tool call on that answer |

`_detect_test_mode` is the root of it: production behaviour depends on whether
the object has been mocked. Nine tests in `tests/test_mcp_server.py` rely on
this, while the other 116 tests already construct real handlers around a mocked
client and need none of it.

### Target structure

Four modules, each with one job:

**`mcp/transport.py`** — `StdioTransport`
Reads newline-delimited JSON from a stream, hands each parsed request to a
callable, writes each returned response back. Knows nothing about CheckMK,
tools, or JSON-RPC semantics beyond "a request may produce a response, or
none". Owns the loop, the EOF and keyboard-interrupt handling, and the
decision to skip a malformed line rather than die.

```python
class StdioTransport:
    def __init__(self, handle: Callable[[dict], Awaitable[dict | None]],
                 stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None: ...
    async def run(self) -> None: ...
```

Injecting the streams is what makes the transport testable without a
subprocess.

**`mcp/registry.py`** — `ToolRegistry`
Owns the mapping from tool name to handler. Built from a `CheckMKClient`;
exposes lookup and the set of registered names. The invariants that
`tests/test_tool_registry.py` already guards move here as the registry's own
contract.

```python
class ToolRegistry:
    @classmethod
    def from_client(cls, client: CheckMKClient) -> "ToolRegistry": ...
    def handler_for(self, tool_name: str) -> BaseHandler | None: ...
    def tool_names(self) -> frozenset[str]: ...
```

**`mcp/dispatch.py`** — `Dispatcher`
Implements the JSON-RPC layer: request validation, `initialize`,
`tools/list`, `tools/call`, `notifications/initialized`, and error responses.
Takes a registry and an `MCPConfig`. Contains no I/O.

```python
class Dispatcher:
    def __init__(self, registry: ToolRegistry, config: MCPConfig) -> None: ...
    async def handle(self, request: dict) -> dict | None: ...
```

**`mcp/server.py`** — `CheckMKMCPServer`
Thin wiring only: load config, build client, build registry, build dispatcher,
run transport. Keeps lazy initialisation — the CheckMK connection is still
established on the first tool call, not at startup, so a misconfigured server
still answers `initialize` and `tools/list` and reports the configuration
error as tool output.

The registry is built lazily for that reason. `Dispatcher` therefore receives
a registry *provider* (a zero-argument callable) rather than a registry, so it
can surface a configuration error as tool content instead of crashing:

```python
Dispatcher(registry_provider: Callable[[], ToolRegistry], config: MCPConfig)
```

### Migrating the nine tests

Each becomes a `Dispatcher` test with a stub registry — an object with
`handler_for` and `tool_names`, no `unittest.mock` introspection anywhere:

| Current test | Becomes |
|---|---|
| `test_tools_list_request` | `Dispatcher.handle` on a `tools/list` request |
| `test_tool_call_success` | stub registry returning a handler that records its call |
| `test_tool_call_invalid_tool` | stub registry returning `None` → `-32601` |
| `test_invalid_jsonrpc_method` | unchanged semantics, `Dispatcher` target |
| `test_malformed_request` | unchanged semantics, `Dispatcher` target |
| `test_tool_call_with_arguments` | stub handler asserts the arguments it received |
| `test_handler_exception` | stub handler raises → `-32603` |
| `test_server_initialization` | `CheckMKMCPServer` wires the four parts together |
| `test_concurrent_requests` | `Dispatcher` under `asyncio.gather` |

Two tests are added that the old structure could not express: the transport
skips a malformed line and continues, and it stops on EOF.

### Acceptance

- `grep -r "unittest.mock" mcp/` finds nothing.
- No module under `mcp/` references `TestHandler`, `_test_mode`, or
  `_detect_test_mode`.
- `mcp/server.py` is under 120 lines.
- The full suite passes, and the end-to-end stdio check (`initialize`,
  `tools/list`, `tools/call`) behaves as it does today.

## 2. Type checking and linting as a ratchet

### Problem

`pyproject.toml` sets `strict = true` for mypy and enables 30 ruff rule
groups. CI replaces the mypy step with an `echo` and never runs ruff. Current
state: 272 mypy errors across 26 files, 1141 ruff findings.

27 of 53 modules are already mypy-clean, so the starting position is better
than the totals suggest.

### Mechanism

Both tools run in CI at their configured strictness from the first commit of
this work. Modules that do not pass yet are listed explicitly:

- mypy: a `[[tool.mypy.overrides]]` block with `ignore_errors = true` and the
  module list.
- ruff: `[tool.ruff.lint.per-file-ignores]` naming file and rule codes.

Both lists live in `pyproject.toml`, and a test asserts that every listed
entry still has a finding — so an entry that becomes unnecessary fails the
build and must be deleted. The lists can only shrink.

This is the point of the ratchet: a module that is clean can never silently
regress, and the exception list cannot quietly grow.

### Order of work

1. Missing annotations first — ANN001 and ANN201 are 302 of the 1141 ruff
   findings and the same defect mypy reports as `no-untyped-def` (20) and
   `no-untyped-call` (41). One pass reduces both tools substantially.
2. `ruff check --fix` for the 280 mechanically fixable findings, reviewed as a
   diff rather than trusted blindly.
3. `handlers/` module by module, largest first (`downtimes.py`, `hosts.py`,
   `metrics.py`, `services.py`).
4. Complexity rules last (PLR0911 "too many returns", PLR0912 "too many
   branches"). These describe real structural problems — `_get_service_status`
   with its five-step fallback chain is the clearest — and are refactors, not
   annotations. They may be deferred past this work with a written note.

`G004` (110 findings, f-strings in logging calls) is fixed as part of each
module's pass rather than in a separate sweep, since it touches the same
lines.

### Acceptance

- CI runs `mypy .` and `ruff check .` as failing steps.
- The `echo` placeholder is gone from `.github/workflows/ci.yml`.
- A test fails when an exception-list entry is no longer needed.
- The exception lists are strictly smaller at the end of this work than at the
  start.

## 3. MCP protocol negotiation

### Problem

`_handle_initialize` echoes back whatever `protocolVersion` the client sent.
A request naming `1999-01-01-BOGUS` is answered with `1999-01-01-BOGUS`,
claiming support for a version that does not exist. The MCP specification
requires the server to answer with a version it actually supports.

### Change

`MCPConfig` gains an explicit tuple of supported versions, currently holding
only `2024-11-05`. Negotiation becomes:

- Client names a supported version → answer with that version.
- Client names anything else, or omits the field → answer with the server's
  preferred version, which is the newest supported one.

Deciding *which* versions to support is separate work, as stated in the
non-goals. This change makes the answer honest at the version we support
today.

### Acceptance

- An `initialize` naming an unsupported version is answered with a supported
  one, never the client's string.
- An `initialize` naming a supported version is answered with that version.
- An `initialize` with no `protocolVersion` is answered with the preferred
  version.

## 4. `metrics.py` time windows

### Problem, with evidence

`MetricsHandler._parse_time_range` builds naive local time and formats it as
`"%Y-%m-%d %H:%M:%S"` with no timezone marker. CheckMK reads that as site
local time. Measured against CheckMK 2.4.0p2 CRE, requesting "the last hour"
with the host in three timezones:

| Host timezone | Sent | Window returned |
|---|---|---|
| Europe/Berlin (matches the site) | `2026-09-07 15:56:16` | 13:56–14:56 UTC — correct |
| UTC | `2026-09-07 13:56:17` | 11:56–12:56 UTC — two hours early |
| America/New_York | `2026-09-07 09:56:19` | 07:56–08:56 UTC — six hours early |

The current behaviour is correct only because this deployment's host and site
share a timezone. A container defaults to UTC, so the Docker deployment
discussed in upstream issue #2 would silently return the wrong hour.

The same probe confirmed that ISO-8601 with a `Z` suffix built from an aware
UTC datetime yields the correct window regardless of host timezone, matching
the canonical form in CheckMK's own `reorganize_time_range` docstring.

### Change

`_parse_time_range` returns `datetime.now(timezone.utc)`-based values
formatted as `%Y-%m-%dT%H:%M:%SZ`.

### Acceptance

- A test parametrised over Europe/Berlin, UTC and America/New_York asserts the
  same UTC instant is produced in all three.
- Output matches `%Y-%m-%dT%H:%M:%SZ`.

## Sequence

1. **`metrics.py`** — smallest, fully specified, independent.
2. **Protocol negotiation** — small, independent, no structural impact.
3. **Server split** — the substantial change; benefits from a stable suite.
4. **Linting ratchet** — last, so it does not fight code being restructured.

Each step lands as its own branch with its own tests, merged to `main` when
CI is green, in this order.

## Testing

Test-driven throughout, as for the work already done: the failing test comes
first and is watched failing for the expected reason.

Two guards outlive this work:

- The exception-list test from section 2, which keeps the ratchet one-way.
- The existing `tests/test_tool_registry.py` structural guards, extended to
  the new `ToolRegistry` boundary.

## Risks

**The server split touches the process entry point.** A mistake breaks every
tool call rather than one. Mitigated by keeping the end-to-end stdio check as
an explicit acceptance criterion, run before the branch merges, and by the
split being mechanical: no behaviour changes, only relocation.

**Lazy initialisation is easy to lose in the split.** A server that builds its
client eagerly would fail at startup on a misconfigured system instead of
reporting a readable error on the first tool call. The registry-provider
indirection exists specifically to preserve this, and a test asserts that
`initialize` and `tools/list` still answer with no CheckMK configuration
present.

**The annotation pass is large and mechanical**, which is where wrong
annotations hide. Annotations are added per module with that module's tests
run immediately, not in one sweep across the tree.
