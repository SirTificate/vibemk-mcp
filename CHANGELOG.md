# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

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

### Added
- Notification rule management: list, show, create, update and delete
  (`vibemk_*_notification_rule`)

### Fixed
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