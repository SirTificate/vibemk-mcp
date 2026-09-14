# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Fixed
- Rule positioning never worked. `vibemk_move_rule` sent `top`, `bottom`, `before` and
  `after` and named its target `target_rule`; CheckMK's move endpoint discriminates on
  `position` over exactly `top_of_folder`, `bottom_of_folder`, `after_specific_rule` and
  `before_specific_rule`, expects the target under `rule_id`, and requires a `folder` for
  the two folder positions that the code never sent, so every move was rejected. The
  folder now comes from the rule itself. `vibemk_create_rule` advertised `position` and
  never read it — the create endpoint takes no position at all, so it is honoured by
  moving afterwards, and a failed move is reported with the rule id instead of being
  swallowed. The short forms stay accepted and are translated
- `vibemk_get_host_effective_attributes` reported a host's own attributes as its effective
  ones. It fetched the folder separately to merge inherited values, addressing it as
  `/servers/linux` where CheckMK expects `~servers~linux`, so the lookup answered 404 for
  every host outside the root folder. CheckMK resolves the inheritance itself when asked
- Three handler modules printed a literal `\n` instead of a line break — 55 occurrences in
  `host_group_rules.py`, 14 in `debug.py`, 13 in `users.py` — so every affected answer
  arrived as one unbroken run of text
- The metrics 400 handler told users timestamps had to be `YYYY-MM-DD HH:MM:SS` long after
  the code began sending ISO-8601 UTC, advising callers straight back into the timezone
  bug that change had removed
- Both downtime scheduling tools advertised a `recur` parameter the handler never read,
  so a request for a recurring weekly downtime silently produced a one-off and reported
  success. The advertised values were wrong too: CheckMK accepts `fixed`, `hour`, `day`,
  `week`, `second_week`, `fourth_week`, `weekday_start`, `weekday_end` and `day_of_month`,
  while vibeMK offered `month`, which CheckMK has never had. The tool descriptions now
  also state that recurring downtimes are an Enterprise and Cloud feature — a Raw site
  accepts the request and creates a one-off
- `_is_downtime_active` and `has_host_level_downtime` could raise instead of returning
  `False`: a `try/except/else` rewrite left their final comparison in the `else`, which
  the `except` does not cover. A downtime record whose `start_time` is null then turned
  `vibemk_check_host_downtime_status` into an error for the whole host rather than
  skipping that record
- Reading an HTTPError's body is treated as best effort again. It had been narrowed to
  named exception types twice; both were wrong, because the type varies by Python version
  (`KeyError` on 3.9, no exception at all on 3.13) and a failure there skipped the retry
  for transient status codes
- Four dispatch branches referenced tool names declared nowhere — three in
  `handlers/debug.py` and one leftover alias in `handlers/discovery.py`

### Removed
- `_format_metric_data` and the three display limits only it used; nothing called it

### Changed
- `mcp/server.py` split into `transport.py` (stdio I/O), `dispatch.py` (JSON-RPC) and
  `registry.py` (tool-to-handler table); the server is now wiring only, 62 lines from 547.
  The scaffolding that made production behaviour depend on whether the code was under
  test — it asked `unittest.mock` whether it had been mocked — is gone
- `initialize` answers with a protocol version the server supports instead of echoing the
  client's, which claimed support for anything a client cared to name
- Metric time windows are built in UTC, so they no longer depend on the server host's
  timezone matching the CheckMK site's. Measured against 2.4.0p2: a host in UTC against a
  Europe/Berlin site received a window two hours early
- `AcknowledgementHandler` and `DiscoveryHandler` inherit `BaseHandler` like the other
  twenty, so the registry's handler type is honest and both gain the shared helpers

### Added
- A read-only smoke test against a real CheckMK instance, behind `LIVE_SMOKE_TEST=true`
  and kept out of CI. The rest of the suite mocks the HTTP client and so cannot tell
  whether CheckMK serves a path the code calls; three endpoints turned out to be dead and
  no test noticed
- A guard against escaped newlines in response text, checking the class rather than the
  instances
- mypy and ruff run in CI at their configured strictness. Both were configured and neither
  ran: the mypy step was an `echo` and ruff was never invoked, so 272 type errors and 1141
  lint findings had accumulated behind a green build. Modules not yet clean are listed
  explicitly, and a test fails when an entry stops being necessary, so the list can only
  shrink
- Structural guards for the tool catalogue: no duplicate names, no advertised tool without
  a handler, no handler without a declaration, and no dispatch branch referencing an
  undeclared tool
- The suite grew from 43 tests to 171

### Infrastructure
- Static analysis runs once on a single interpreter with mypy and ruff pinned. Running it
  across the test matrix meant pip resolved a different mypy per Python version — 1.14 on
  3.8, 1.19 on 3.9, 2.3 on 3.10 and above — and those versions disagree, so the same tree
  passed on three matrix entries and failed on a fourth
- The test matrix installs a `test` extra rather than `dev`, so a pin chosen for a checker
  cannot constrain which interpreters the suite runs on

## [0.4.0] - 2026-09-07

First release of the maintained continuation at
[SirTificate/vibemk-mcp](https://github.com/SirTificate/vibemk-mcp). Distributed under the same
GPL-3.0 licence as the original, with its history and authorship preserved. Tool names are
unchanged, so existing client configurations keep working.


### Fixed
- Service discovery called `domain-types/service_discovery/actions/start`, which CheckMK
  registers under `service_discovery_run` — every `vibemk_discover_services` call was a 404,
  and its dispatch passed a string to a method expecting a dict, raising AttributeError
  before that. The tool duplicated two working ones and was removed
- Bulk discovery status queried `objects/discovery_run/{job_id}`, which is not registered;
  it now reads `objects/background_job/{job_id}`, the endpoint bulk discovery redirects to
- Removed a service-status fallback posting to `domain-types/bi_rule/actions/livestatus_query`
  — no such endpoint exists in any CheckMK release
- Discovery modes `tabula_rasa` and `only_service_labels` were rejected client-side although
  CheckMK 2.4 accepts them
- Eleven call sites sent `If-Match: "*"`, which CheckMK accepts but which disables the
  concurrency check entirely; they now send the object's real ETag
- `PUT`/move on folders, move on hosts and rules, and `DELETE` on time periods sent no
  `If-Match` at all although CheckMK 2.4 requires one
- Change activation no longer hand-rolls a urllib request to work around the client's
  missing header support, and takes its ETag from the pending-changes response

- Downtime scheduling no longer discards an explicit date: any expression containing a
  time of day was reduced to that time *today*, so a downtime requested for
  `2026-12-24T22:00:00Z` was scheduled for the current day
- Downtime times named without an offset (e.g. "22:00 tomorrow") are now converted from
  local time to UTC instead of being labelled `Z` while holding local time — previously
  off by the UTC offset on every non-UTC site
- `vibemk_get_notification_rules` and `vibemk_test_notification` were advertised in
  `tools/list` but mapped to no handler, so calling either returned `Unknown tool`; both
  declarations are removed
- Four acknowledgement tools (`acknowledge_host_problem`, `acknowledge_service_problem`,
  `list_acknowledgements`, `remove_acknowledgement`) were implemented and wired but never
  declared, so no MCP client could reach them
- A scheme-less `CHECKMK_SERVER_URL` defaulted to `http://`, putting the Basic auth
  credentials on the wire in the clear; bare hosts now default to `https://`
- `CheckMKClient.post()` and `.delete()` accept custom headers, so `If-Match` can be sent
  on the endpoints that require it
- `User-Agent` reported `vibeMK/config.settings` instead of the version, and the
  advertised version (0.3.9) disagreed with `pyproject.toml` (0.3.10)

### Added
- Notification rule management: list, show, create, update and delete
  (`vibemk_*_notification_rule`)
- Optional `.env` support, read once at startup before logging is configured; real
  environment variables always take precedence
- Structural tests covering the tool registry, downtime time parsing, client transport
  and the `.env` parser

### Merged from upstream pull requests
- CheckMK 2.4 CRE compatibility (#4 by @RafaelFerreiraAralab): API URL detection no longer
  accepts an SSO login redirect as a valid endpoint, host and service listings use the
  monitoring collections instead of the Setup/WATO endpoints that return empty for
  non-administrative accounts, `columns` is requested explicitly so `state` is populated,
  and stdio is forced to UTF-8 on Windows

## [0.3.10] - 2025-08-23
### Added
- Enhanced Host Attribute Updates for ipaddress, alias and tags

## [0.3.9] - 2025-08-23
### Changed  
- Version increment

## [0.3.6] - 2025-08-23
### Fixed
- User roles management now works correctly across CheckMK 2.3 and 2.4

## [0.3.5] - 2025-08-23  
### Fixed
- Problem acknowledgements now work correctly across CheckMK 2.3 and 2.4
- Automatic version detection for better compatibility

## [0.3.3] - 2025-08-22
### Added
- Service group management - create, list, update, and delete service groups
- Bulk operations for managing multiple service groups at once

## [0.3.2] - 2025-08-22
### Added
- Advanced host management - validate configs, compare states, create cluster hosts
- Enhanced host updates with merge, overwrite, and remove modes

## [0.3.1] - 2025-08-21
### Added  
- Time period management - create and manage notification schedules
- Support for complex weekly schedules (e.g., Monday-Friday 8-17)

## [0.3.0] - 2025-08-21
### Added
- Complete downtime management - schedule, list, and delete maintenance windows
- Flexible duration parsing (e.g., "2h", "1h30m")

## [0.2.0] - 2025-08-21
### Added
- Performance metrics retrieval and analysis
- Live host and service status monitoring  
- Folder management for organizing hosts
- Rule creation and management

## [0.1.0] - 2025-08-20
### Added
- Initial CheckMK integration with MCP server
- Basic monitoring tools and authentication