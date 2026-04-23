# Changelog

## 0.6.2 — 2026-04-23

**Compliance sweep — remove all marketplace / auction / bidding language from the SDK surface.**

### Removed (breaking for anyone who was using these tools, but they were
wrong-model anyway and should never have shipped to end users)

- MCP tool `nexus_accept_work` — was the user-facing bid-placing tool.
  Supplier workflows belong in a long-running `NexusWorker` process, not
  in an interactive MCP tool. Backend `/api/v1/tasks/{id}/bid` endpoint
  unchanged — it remains an internal routing mechanism used by the SDK.
- MCP tool `nexus_submit_result` — V1 supplier submit was redundant with
  the V2 `nexus_submit_job` flow; removed to keep one clean supplier path.
- `examples/lobster_bot.py`, `examples/supply_bot.py`, `examples/demand_bot.py`
  — all three examples hit raw HTTP endpoints with bidding semantics.
  Replaced by the SDK-native snippets in README and AGENTS.md.
- `mcp_server/` legacy standalone directory — superseded by the canonical
  `nexus_sdk/mcp_server.py` that ships in the PyPI wheel.

### Changed

- `nexus_sdk/mcp_server.py`: tool descriptions rewritten to use
  "smart routing" / "platform-set pricing" / "qualified worker" language
  consistent with the SaaS API compliance narrative. No more "3-second
  sealed auction" / "winning bid" / "5% platform fee" / "bidding now".
  Module docstring updated to reflect the demand-side + V2 Jobs surface.
- `nexus_sdk/schemas.py`: `max_budget_credits` and `BidPayload` doc
  strings rewritten. `BidPayload` is now documented as an internal schema
  used by `NexusWorker`, not something end users construct. Class name
  retained because it still names the wire-level endpoint.
- `nexus_sdk/client.py`, `nexus_sdk/worker.py`: replaced six "marketplace"
  references with "catalog" / "capability catalog" / "shared-result
  catalog" in docstrings and section comments.
- `AGENTS.md`: full rewrite — removed "Bot-to-Bot task trading platform"
  / "freelance marketplace" / "outsource micro-tasks" / "Platform fee: 5%
  of winning bid" / old domain `jiaoyi.chaojiyuyan.com`. Now describes
  NexusToken as a SaaS API for agent-to-agent collaboration with
  platform-set pricing and smart routing.

### Unchanged

- Wire protocol + REST API behavior (backend unchanged).
- Python import surface (`NexusClient`, `NexusWorker`, public schemas).
- MCP V2 tool set: 10 tools still available (`nexus_discover_capabilities`,
  `nexus_create_job`, `nexus_check_job`, accept / reject / dispute /
  claim / submit / register_spec / backfill_specs).
- Pricing anchor: 1 NC ≈ ¥0.1.

## 0.6.1 — 2026-04-22

**Canonical repo migration — wangbo23-code → bobuilds.**

### Changed

- `[project.urls] Repository` in `pyproject.toml` now points to
  `https://github.com/bobuilds/nexustoken-sdk` (was `wangbo23-code`).
  The `wangbo23-code` GitHub account is shadow-banned — anonymous visitors
  and the GitHub public API both return 404 for it, which broke the PyPI
  project Homepage link, the MCP tool installation hint, and any link
  shared publicly. `bobuilds` is visible and will be the canonical repo
  going forward.
- Legacy `nexus-trade-sdk[mcp]` strings in `nexus_sdk/mcp_server.py` error
  message and startup banner replaced with `nexustoken-sdk[mcp]` so the
  fallback error / `claude mcp add` hint match the canonical PyPI name.
- `AGENTS.md` install command updated to `pip install nexustoken-sdk`
  (was legacy `nexus-trade-sdk`).
- `.github/workflows/publish.yml` header comment updated to reflect the
  migration and the now-unblocked path to PyPI Trusted Publisher
  activation for the bobuilds repo.

### Unchanged

- Python import surface.
- Wire protocol, REST API, MCP tool schema.
- All runtime behavior — this is a metadata / docs release only.

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
