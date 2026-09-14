# vibeMK-MCP 🚀

**CheckMK Monitoring via LLM - Professional MCP Server**

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![CheckMK 2.4](https://img.shields.io/badge/CheckMK-2.4-green.svg)](https://checkmk.com/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-purple.svg)](https://spec.modelcontextprotocol.io/)
[![No Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](https://github.com/SirTificate/vibemk-mcp)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

> **A maintained continuation of [chexma/vibeMK](https://github.com/chexma/vibeMK)** by Andre, whose
> last release was in August 2025. This fork keeps the project going: it carries the original work
> forward under the same GPL-3.0 licence, preserves its history and authorship, and adds fixes
> verified against the OpenAPI document a CheckMK site publishes for itself. Tool names are
> unchanged (`vibemk_*`), so existing client configurations keep working, with the exception of the
> three tools listed under [Verification](#-how-this-is-verified). If the original author resumes,
> everything here is offered back.

## 🎯 Overview

vibeMK enables complete management of your CheckMK monitoring environment directly through LLM interfaces using natural language.
This project is in the alpha stage and under development. I accept no liability for any damage resulting from the use of this software.

## vibeMK - Current Features

### Live Monitoring ✅
- **Host Status**: Real-time host state (UP/DOWN/UNREACHABLE) with hard state detection
- **Service Status**: Live service monitoring (OK/WARNING/CRITICAL/UNKNOWN)
- **Performance Metrics**: Retrieve metrics data with automatic discovery
- **Current Problems**: Auto-detect all active monitoring issues

### Downtime Management ✅
- **Schedule Downtimes**: Create host/service downtimes with flexible duration parsing ("2h", "1h30m")
- **List & Filter**: View all downtimes or filter for active ones only

### Problem Management ✅
- **Acknowledge Problems**: Set acknowledgements for host/service issues (sticky/persistent options)
- **List Acknowledgements**: View all current problem acknowledgements
- **Remove Acknowledgements**: Delete by pattern or individual removal

### Configuration Management ✅
- **Folders**: Create/delete monitoring folder structures
- **Rules**: Create rules for 2000+ CheckMK ruleset types with proper format handling
- **Time Periods**: Create custom notification schedules (business hours, 24/7, etc.)
- **Host Groups**: Organize hosts into logical groups

### User & Security ✅
- **User Accounts**: Create/manage user accounts with role assignment
- **Password Management**: Set passwords with policy enforcement
- **Contact Groups**: Manage notification groups
- **Host/Service Tags**: Comprehensive tagging system

## 🚀 Quick Start

```bash
1. git clone https://github.com/SirTificate/vibemk-mcp.git
2. Edit the configuration file of your LLM Client, e.g. Claude Desktop - claude_desktop_config.json (See examples)
3. Start your LLM Client
4. CheckMK automation user setup (Administrator permissions or a customized role if changes are to be made, read-only if only analyses are to be performed.)
5. voila - configure checkmk using natural language
```
**Complete Installation Guide**: See [INSTALL.md](INSTALL.md) for detailed step-by-step instructions.
**More Examples**: See `examples/llm_configs/` and [INSTALL.md](INSTALL.md)  
**Visual Examples**: See `examples/Screenshots/` for example prompts and usage patterns  


## 💡 Practical Prompt Examples

```bash
# Add new server
"Create a new host 'web-server-05' in folder 'Servers' with IP 192.168.1.105 and discover all services"

# Schedule maintenance
"Schedule a 2-hour downtime for the service Check_MK on 'cephnode01' starting at 22:00 tomorrow for 'Debian Updates'"

# Downtimes 
"show me all current scheduled downtimes."

# Metric analysis
"Compare the “response_time” metric of the “HTTPS Webservice” service of the two hosts www.google.de and www.heise.de for the last ten minutes."

# Ruleset analysis
"analyze and compare the rulesets "Filesystems (used space and growth)" and see, if there are duplicates or if rules can be combined."
```
**Advanced Usage**: See `examples/ExamplePrompts.md` for complex scenarios and tips

## 📚 Checkmk version compatibility

| CheckMK Version | Status | Notes |
|-----------------|--------|-------|
| **2.4.x** | ✅ Verified | All 104 endpoint calls checked against the API document of a 2.4.0p36 Raw site; read-only tools additionally exercised against it |
| **2.3.x** | ⚠️ Expected to work | Same REST API version (1.0), not re-verified since the fork |
| **2.2.x and older** | 🔴 Unsupported | |

The REST API is served at version `1.0` up to and including CheckMK 2.4. CheckMK 2.5 introduces a
versioned `v1` path that is compatible with `1.0`; support for it is not implemented yet.

## 🔍 How this is verified

A test suite that mocks the HTTP client cannot tell whether an endpoint exists — a call to a path
CheckMK does not serve looks exactly like one that works. Seven calls in this codebase turned out to
have nothing behind them, and no test had ever noticed.

Every endpoint call is now checked against the OpenAPI document the site publishes for itself:

```
{server_url}/check_mk/api/1.0/openapi-doc.yaml
```

That document carries the exact version *and* edition, so it cannot describe an endpoint a given
site does not have. All 104 calls match a path and a verb that 2.4.0p36 Raw serves.

Three tools were removed rather than left to fail quietly, because CheckMK's REST API offers no
equivalent: `vibemk_reschedule_check`, `vibemk_get_custom_graph` and `vibemk_search_metrics`. 114
tools remain.

There is also a read-only smoke test against a live instance, behind `LIVE_SMOKE_TEST=true` and
deliberately kept out of CI:

```bash
LIVE_SMOKE_TEST=true python -m pytest tests/test_live_smoke.py -v
```

## Checkmk Edition Support

All features in this server use REST API endpoints that CheckMK registers for **every edition,
including Raw (CRE)**. That includes Business Intelligence and the Event Console, which the original
README listed as Enterprise-only — a mistake this fork corrects.

The one genuine edition restriction is the **Agent Bakery** (`agent` endpoints), which exists only in
the Enterprise and Cloud editions. It is not currently implemented here.

## Security considerations

This lets a language model create and delete hosts, rules and users in your monitoring, and
"activate changes" is a real button.

- **Start with a read-only automation user.** Widen the role later, once you have seen what the
  model actually does with it. A custom role scoped to what you need beats an Administrator account.
- **Keep the log.** Every tool call is recorded at INFO with its name — that log is the only record
  of what the model did. Arguments are deliberately not logged, since they carry host names, comment
  text and, for the password tools, secrets.
- **Check the CheckMK audit log** after the first few sessions. It sees the changes from the other
  side.
- Use at your own risk. No liability is accepted for actions performed by an AI.

To report a security issue, see [SECURITY.md](SECURITY.md).

# 📄 License

This project is licensed under the [GNU General Public License v3.0](LICENSE).

---

**Happy Monitoring with CheckMK and LLMs!** 🎉