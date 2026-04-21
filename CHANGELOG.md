# Changelog

## 0.4.0 — 2026-04-21

**Package renamed: `nexus-trade-sdk` → `nexustoken-sdk`.**

### Migration

- New install: `pip install nexustoken-sdk`
- Python imports unchanged: `from nexus_sdk import NexusClient, NexusWorker`
- MCP: `uvx --from 'nexustoken-sdk[mcp]' nexus-mcp`
- The legacy `nexus-trade-sdk` package continues to publish as a
  deprecation shim that re-exports the same symbols with a
  `DeprecationWarning` until 2026-10-01, after which it will be
  yanked from PyPI.

### Why

The platform's positioning is a SaaS API for structured data
processing. Legacy `trade`/`marketplace` terminology in the PyPI
distribution name and keywords created payment-processor compliance
friction. The rename aligns the package metadata with current
product positioning. Internally the platform still calls the metering
unit "compute units" (NC) — that is unchanged.

### Unchanged

- Python import surface: `from nexus_sdk import ...`
- REST API endpoints
- API keys and billing
- Wire protocol and MCP tool schema

## 0.3.1 — 2026-04-17

Previous stable release under the `nexus-trade-sdk` name. See git
history for details.
