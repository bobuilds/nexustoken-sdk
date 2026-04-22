# Changelog

## 0.6.0 — 2026-04-22

**Day 2 expansion pack — Public Artifacts marketplace + physical-world task_type.**

### Added

- `NexusWorker(publish_artifacts=True, artifact_license=..., artifact_tags=...)`
  constructor flags. Every successful submission is offered as a reusable
  public artifact; future requesters whose task hashes to the same content
  get your cached result and you earn 70% of the call price as royalty.
  Default remains `False` — opt-in for IP / privacy reasons.
- `NexusClient.list_public_artifacts(...)` / `get_public_artifact(id)` /
  `my_public_artifacts(...)` / `my_artifact_earnings()` /
  `retract_public_artifact(id)` — five helpers for the new
  `/api/v1/public-artifacts` endpoints. Consumption stays implicit via
  `create_task()` — no new billing surface.
- `NexusClient.create_physical_proof_task(brief, budget=..., ...)` helper
  for the new `physical_proof` task_type. Workers submit
  `{description, proof: {type: "file_ref", file_id: ...}}` to prove a
  real-world action (photo, video, sensor dump). Quality judgment rides
  the existing dispute / jury flow — no new attestation stack.
- MCP tools from the V2 Open Capability Marketplace
  (`nexus_discover_capabilities`, `nexus_create_job`, etc.) — these were
  intended for 0.5.0 but the wheel published to PyPI didn't include
  them. 0.6.0 fixes that.

### Why 0.6.0, not 0.5.1

PyPI already has `nexustoken-sdk==0.5.0`, published before the Day 2
features were scoped. Rather than yank + re-release the same version
number (which would break any downstream pin that happened to grab 0.5.0),
we minor-bump so Day 2 features ship cleanly and 0.5.0 stays immutable.

### Unchanged

- Python import surface: `from nexus_sdk import NexusClient, NexusWorker`.
- Wire protocol, REST API, MCP tool schema for pre-existing tools.
- API keys, billing, the 1 NC = ¥0.1 price anchor.

## 0.5.0 — 2026-04-21

Released to PyPI on 2026-04-21. **Not recommended** — the V2 marketplace
MCP tools referenced in the release notes did not make it into the
published wheel, and the SDK changes planned for this version landed in
0.6.0 instead. Use 0.6.0 or newer.

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
