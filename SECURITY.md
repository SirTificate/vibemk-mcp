# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.5.x   | ✅ |
| < 0.5   | ❌ |

Only the latest release receives fixes. The project is pre-1.0 and developed by one maintainer;
please do not read the table as a support contract.

## Reporting a vulnerability

**Use GitHub's private vulnerability reporting:**
<https://github.com/SirTificate/vibemk-mcp/security/advisories/new>

That opens a report only the maintainer can see. Please do not open a public issue for a security
problem.

Useful in a report: what an attacker can do, how to reproduce it, and which version you tested.
A suggested fix is welcome but not required.

Expect an acknowledgement within a few days. This is a spare-time project, so a fix arrives when it
arrives — you will be told either way, and credited unless you prefer otherwise.

## Scope

This server hands a language model write access to a CheckMK site, so a few things are worth
separating from vulnerabilities in the code:

**In scope:** credential handling, the HTTP client's TLS behaviour, anything that lets a crafted
CheckMK response or tool argument reach a shell, the filesystem, or an unintended endpoint.

**Not a vulnerability, but by design:** an LLM doing something destructive it was asked to do. The
server executes the tools it is told to. Scope the automation user to what you are willing to lose,
and read the section on security considerations in the [README](README.md).

**Known and accepted:** credentials come from environment variables or a `.env` file, so anyone who
can read the process environment or that file has the CheckMK account. There is no secret store.
