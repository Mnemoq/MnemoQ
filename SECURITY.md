# Security Policy

## Reporting a Vulnerability

Do **not** open a public issue for security vulnerabilities.

Report vulnerabilities via [GitHub Security Advisories](https://github.com/Mnemoq/MnemoQ/security/advisories/new) (private vulnerability reporting).

Include:
- Description of the vulnerability and its impact
- Steps to reproduce
- Affected versions
- Suggested fix (if any)

You will receive a response within 7 days. Valid reports will be credited.

## Scope

- The MCP server (`mnemoq-mcp`)
- The HTTP API server (`agent_memory.engine.server`)
- The SDK transport layer (`agent_memory.sdk.client`)
- CLI input handling

Out of scope: self-hosted misconfiguration, issues in third-party dependencies (report upstream).
