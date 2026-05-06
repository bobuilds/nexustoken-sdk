#!/usr/bin/env python3
"""
NexusToken MCP Server — Model Context Protocol integration.

This MCP Server exposes NexusToken as native AI tools so any MCP-compatible
AI host (Claude Desktop, Claude Code, Cursor, etc.) can post tasks, discover
agent capabilities, and run the V2 Job protocol against the platform.

  1. nexus_create_task      — Delegate a JSON extraction task to the network
  2. nexus_check_status     — Check task status or account balance
  3. nexus_discover_* / nexus_create_job / nexus_check_job / …  — V2 Jobs

Supplier-side workflows (accepting and returning work) run as a separate
long-running process using the ``NexusWorker`` SDK class, not through MCP.

Usage with Claude Desktop:
  Add to ~/.claude/claude_desktop_config.json:
  {
    "mcpServers": {
      "nexus": {
        "command": "python3",
        "args": ["/path/to/nexus_mcp.py"],
        "env": {
          "NEXUS_API_KEY": "your-api-key",
          "NEXUS_BASE_URL": "http://localhost:8000"
        }
      }
    }
  }

Requires: pip install mcp httpx
"""

import json
import logging
import os
import sys
import time

import httpx

try:
    from mcp.server import Server
    from mcp.types import Tool, TextContent
    # New MCP API (>= 1.0): use `stdio_server` + `server.run(...)`.
    # Old MCP API (< 0.2): a top-level `run_server(server)` coroutine existed.
    try:
        from mcp.server.stdio import stdio_server as _stdio_ctx
        _MCP_LEGACY_API = False
    except ImportError:
        from mcp.server.stdio import run_server as _legacy_run_server  # type: ignore
        _MCP_LEGACY_API = True
        _stdio_ctx = None  # type: ignore
except ImportError:
    print("ERROR: pip install 'nexustoken-sdk[mcp]'", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("nexus-mcp")

# Config from env (resolved lazily in main() so bootstrap can populate it)
BASE_URL = os.getenv("NEXUS_BASE_URL", "https://api.nexustoken.ai").rstrip("/")
API_KEY = os.getenv("NEXUS_API_KEY", "")
MCP_VERSION = "0.6.6"

server = Server("nexus-task-network")
# Build the HTTP client lazily — after _bootstrap_credentials() has a chance
# to run the device flow on first launch.
http: "httpx.Client | None" = None


def _build_http_client() -> "httpx.Client":
    """(Re)build the HTTP client with the current BASE_URL + API_KEY."""
    return httpx.Client(
        base_url=BASE_URL,
        headers={
            "X-API-Key": API_KEY,
            "User-Agent": f"nexus-mcp/{MCP_VERSION} (python/{sys.version_info.major}.{sys.version_info.minor})",
            "X-SDK-Version": MCP_VERSION,
            "X-SDK-Source": "mcp",
        },
        timeout=30,
    )


def check_version():
    """Check MCP server version compatibility with platform."""
    try:
        resp = http.get("/api/v1/heartbeat")
        if resp.status_code == 200:
            data = resp.json()
            min_ver = data.get("min_sdk_version", "0.0.0")
            latest_ver = data.get("latest_sdk_version", MCP_VERSION)

            def ver_lt(a, b):
                return tuple(int(x) for x in a.split(".")[:3]) < tuple(int(x) for x in b.split(".")[:3])

            if ver_lt(MCP_VERSION, min_ver):
                logger.error(
                    f"MCP Server version {MCP_VERSION} is too old. "
                    f"Minimum required: {min_ver}. Please update."
                )
                sys.exit(1)
            if ver_lt(MCP_VERSION, latest_ver):
                logger.warning(f"NexusToken MCP Server {latest_ver} available (you have {MCP_VERSION})")
            announcement = data.get("announcement", "")
            if announcement:
                logger.info(f"[NexusToken] {announcement}")
    except Exception as e:
        logger.warning(f"Version check failed (will continue): {e}")


# ─── Tool definitions ───────────────────────────────────────────

TOOLS = [
    Tool(
        name="nexus_create_task",
        description=(
            "Delegate a JSON extraction task to the NexusToken network. "
            "Use this when you need structured data from text and want the work "
            "handled by a qualified worker on the platform. "
            "You provide: the raw text, the desired output JSON Schema, an example "
            "output, and a NC budget cap (NC = internal, non-redeemable service credit). "
            "The platform picks a price and routes the task to a capable worker "
            "based on capability match and reliability. The worker's output is "
            "auto-validated against your schema before settlement. "
            "Typical cost: 5-50 NC per task. "
            "Typical latency: 5-15 seconds end-to-end. "
            "Returns: task_id and status. Use nexus_check_status to poll for results."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "input_data": {
                    "type": "string",
                    "description": (
                        "The raw text to extract structured data from. "
                        "Can be any unstructured text: product descriptions, resumes, "
                        "news articles, log entries, etc. Max 10,000 characters."
                    ),
                },
                "validation_schema": {
                    "type": "object",
                    "description": (
                        "A standard JSON Schema that defines the EXACT structure of the "
                        "output you expect. The platform's validator will reject any "
                        "submission that doesn't match this schema. Example: "
                        '{"type":"object","properties":{"name":{"type":"string"},'
                        '"age":{"type":"integer"}},"required":["name","age"]}'
                    ),
                },
                "example_output": {
                    "type": "object",
                    "description": (
                        "A concrete example of what the correct output looks like. "
                        "This MUST pass the validation_schema you provided. "
                        "Workers use this to understand your intent. "
                        'Example: {"name": "John Doe", "age": 30}'
                    ),
                },
                "max_budget_credits": {
                    "type": "integer",
                    "description": (
                        "Maximum NC budget cap for this task (NC = internal, "
                        "non-redeemable service credit). Minimum 5, typical range 5-50. "
                        "The platform sets the final price at or below this cap; any "
                        "excess is refunded on settlement."
                    ),
                    "minimum": 5,
                },
                "validation_rules": {
                    "type": "array",
                    "description": (
                        "Optional hard rules for extra validation beyond the schema. "
                        "Supported rule types: required_fields, min_length, max_length, "
                        "regex, enum, field_type. Example: "
                        '[{"type":"enum","field":"status","values":["active","inactive"]}]'
                    ),
                    "default": [],
                },
            },
            "required": ["input_data", "validation_schema", "example_output", "max_budget_credits"],
        },
    ),
    Tool(
        name="nexus_check_status",
        description=(
            "Check the status of a NexusToken task or your account balance. "
            "Use this after nexus_create_task to poll for results. "
            "Common task statuses: PENDING (queued for routing), AWARDED "
            "(assigned to a worker), SETTLED (done, result available), "
            "EXPIRED (no capable worker available within the window), "
            "CANCELLED (you cancelled it). "
            "If mode='balance', returns your current NC balance and frozen NC."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["task", "balance"],
                    "description": "'task' to check a specific task, 'balance' to check your credits.",
                },
                "task_id": {
                    "type": "string",
                    "description": "The task_id returned by nexus_create_task. Required when mode='task'.",
                },
            },
            "required": ["mode"],
        },
    ),

    # ─── V2: Capability catalog + Jobs ─────────────────────────────
    # V2 tools operate on CapabilitySpecs (provider contracts) and Jobs
    # (direct-assignment executions with multimodal I/O and multiple
    # validation modes). V1 supplier-side tools were removed in 0.6.2 —
    # supplier workflows run as a long-running ``NexusWorker`` process,
    # not through MCP.
    Tool(
        name="nexus_discover_capabilities",
        description=(
            "Browse the NexusToken capability catalog — a live directory of "
            "agent-to-agent contracts that any AI can invoke. Use this BEFORE "
            "nexus_create_job to find a capability whose input/output shape "
            "matches what you need. Filters compose with AND semantics. "
            "Returns a list of {id, name, category, input_mode, output_mode, "
            "price_nc, validation_mode, tags, description} dicts — pass the "
            "`id` as `capability_spec_id` to create_job."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["data", "text", "code", "web", "multimodal"],
                    "description": "High-level capability bucket.",
                },
                "input_mode": {
                    "type": "string",
                    "enum": ["text", "json", "file", "url"],
                    "description": "Filter by the shape of input the capability accepts.",
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["text", "json", "file", "bundle"],
                    "description": "Filter by the shape of output the capability produces.",
                },
                "max_price": {
                    "type": "integer",
                    "description": "Cap on price_nc — cheap-first browse.",
                },
                "tag": {
                    "type": "string",
                    "description": "Require one of the provider's tags to match (exact).",
                },
                "page": {"type": "integer", "default": 1, "minimum": 1},
                "per_page": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            },
        },
    ),
    Tool(
        name="nexus_create_job",
        description=(
            "Dispatch a V2 Job to a specific CapabilitySpec in the catalog. "
            "You must first have a capability_spec_id (from nexus_discover_capabilities). "
            "Pack your inputs as a typed Envelope — a list of items where each is one "
            "of {text, json, file, url}. For file inputs, upload via the /api/v1/files/upload "
            "endpoint first (outside MCP) and reference the file_id here. "
            "Budget ceiling must be >= spec.price_nc; any excess is refunded on settlement. "
            "After creation, poll with nexus_check_job; depending on the spec's "
            "validation_mode the job settles inline (deterministic / evaluator_llm) or "
            "parks in HELD pending async resolution (evaluator_jury / acceptance / reputation)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "capability_spec_id": {
                    "type": "string",
                    "description": "UUID of the target CapabilitySpec (from discover).",
                },
                "input_envelope": {
                    "type": "object",
                    "description": (
                        "Payload envelope. Must contain an `items` list (1-100). "
                        "Each item has a `type` and the matching payload key: "
                        '{"type":"text","text":"..."} or {"type":"json","json":{...}} '
                        'or {"type":"file","file_id":"<uuid>"} or {"type":"url","url":"..."}'
                    ),
                },
                "budget_ceiling_nc": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Max NC you will spend. Must be >= spec.price_nc.",
                },
                "claim_window_hours": {
                    "type": "integer",
                    "default": 24,
                    "minimum": 1,
                    "maximum": 168,
                    "description": "How long to wait for the provider to claim (else EXPIRED + refund).",
                },
            },
            "required": ["capability_spec_id", "input_envelope", "budget_ceiling_nc"],
        },
    ),
    Tool(
        name="nexus_check_job",
        description=(
            "Fetch current state of a V2 Job by id. Useful for polling after "
            "nexus_create_job. Returns status + output_envelope (if available) + "
            "deadlines + error_code. Common status values: "
            "QUEUED (awaiting claim), RUNNING (provider working), "
            "HELD (output submitted, awaiting async resolution), "
            "COMPLETED (settled — success), FAILED (refunded — validation / jury / "
            "reject), CANCELLED (you cancelled), EXPIRED (no claim in window)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job UUID."},
            },
            "required": ["job_id"],
        },
    ),
    Tool(
        name="nexus_accept_job",
        description=(
            "Approve a HELD job (acceptance mode — or reputation mode, to short-circuit "
            "the dispute window). Releases funds to the provider and transitions the "
            "job to COMPLETED. Only the caller may accept."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
            },
            "required": ["job_id"],
        },
    ),
    Tool(
        name="nexus_reject_job",
        description=(
            "Reject a HELD acceptance-mode job. Caller gets a full refund and the "
            "job transitions to FAILED. Not valid for reputation mode — use "
            "nexus_dispute_job instead."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "reason": {"type": "string", "default": ""},
            },
            "required": ["job_id"],
        },
    ),
    Tool(
        name="nexus_dispute_job",
        description=(
            "Escalate a HELD reputation-mode job to an AI arbiter. If the arbiter "
            "sides with you, the provider gets nothing and you get a full refund. "
            "If the arbiter sides with the provider, the job settles normally. "
            "Reason must be at least 10 characters."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "reason": {"type": "string", "minLength": 10},
            },
            "required": ["job_id", "reason"],
        },
    ),
    Tool(
        name="nexus_claim_job",
        description=(
            "Provider tool: pick up a QUEUED job on a spec you own. Transitions "
            "the job to RUNNING and starts the execution window (2h by default). "
            "For MVP, only the spec's owner account may claim. After claiming, "
            "execute the work and submit with nexus_submit_job."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
            },
            "required": ["job_id"],
        },
    ),
    Tool(
        name="nexus_submit_job",
        description=(
            "Provider tool: return the output envelope for a RUNNING job you own. "
            "Envelope shape mirrors input: a list of items of types text/json/file/url "
            "consistent with the spec's output_mode. Under deterministic / "
            "evaluator_llm modes the job settles inline; under the async modes it "
            "moves to HELD awaiting caller/jury/timeout resolution."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "output_envelope": {
                    "type": "object",
                    "description": "Same shape as input_envelope — a dict with an `items` list.",
                },
            },
            "required": ["job_id", "output_envelope"],
        },
    ),
    Tool(
        name="nexus_register_spec",
        description=(
            "Provider tool: publish a new CapabilitySpec to the catalog so "
            "callers can dispatch jobs to you. Only the 5 core fields are "
            "required; tags + schemas + validation_mode default sensibly. To "
            "migrate an existing V1 capability, use nexus_backfill_specs instead."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "maxLength": 100},
                "category": {"type": "string", "enum": ["data", "text", "code", "web", "multimodal"]},
                "input_mode": {"type": "string", "enum": ["text", "json", "file", "url"]},
                "output_mode": {"type": "string", "enum": ["text", "json", "file", "bundle"]},
                "price_nc": {"type": "integer", "minimum": 1},
                "description": {"type": "string", "maxLength": 1000},
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "file_types": {"type": "array", "items": {"type": "string"}},
                "validation_mode": {
                    "type": "string",
                    "enum": ["deterministic", "evaluator_llm", "evaluator_jury", "acceptance", "reputation"],
                    "default": "deterministic",
                },
                "tags": {"type": "array", "items": {"type": "string"}},
                "version": {"type": "string", "default": "1.0"},
            },
            "required": ["name", "category", "input_mode", "output_mode", "price_nc"],
        },
    ),
    Tool(
        name="nexus_backfill_specs",
        description=(
            "Provider tool: one-click auto-generate a V2 CapabilitySpec for every "
            "V1 capability you already have. Idempotent. Returns the count of "
            "newly-created specs and their serialized form."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
]


# ─── Tool handlers ───────────────────────────────────────────────

@server.list_tools()
async def list_tools():
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        # V1 tools (JSON extraction — demand side only)
        if name == "nexus_create_task":
            return await _handle_create_task(arguments)
        elif name == "nexus_check_status":
            return await _handle_check_status(arguments)
        # V2 tools (capability catalog + Jobs)
        elif name == "nexus_discover_capabilities":
            return await _handle_discover_capabilities(arguments)
        elif name == "nexus_create_job":
            return await _handle_create_job(arguments)
        elif name == "nexus_check_job":
            return await _handle_check_job(arguments)
        elif name == "nexus_accept_job":
            return await _handle_accept_job(arguments)
        elif name == "nexus_reject_job":
            return await _handle_reject_job(arguments)
        elif name == "nexus_dispute_job":
            return await _handle_dispute_job(arguments)
        elif name == "nexus_claim_job":
            return await _handle_claim_job(arguments)
        elif name == "nexus_submit_job":
            return await _handle_submit_job(arguments)
        elif name == "nexus_register_spec":
            return await _handle_register_spec(arguments)
        elif name == "nexus_backfill_specs":
            return await _handle_backfill_specs(arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except httpx.HTTPStatusError as e:
        return [TextContent(type="text", text=f"API Error {e.response.status_code}: {e.response.text}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def _handle_create_task(args: dict):
    payload = {
        "task_type": "json_extraction",
        "input_data": args["input_data"],
        "validation_schema": args["validation_schema"],
        "validation_rules": args.get("validation_rules", []),
        "example_output": args["example_output"],
        "max_budget_credits": args["max_budget_credits"],
        "max_execution_seconds": 120,
    }
    resp = http.post("/api/v1/tasks", json=payload)
    resp.raise_for_status()
    data = resp.json()
    return [TextContent(
        type="text",
        text=json.dumps({
            "task_id": data["id"],
            "status": data["status"],
            "budget": data["max_budget_credits"],
            "message": (
                f"Task created! ID: {data['id']}. "
                f"Status: {data['status']}. "
                "The platform is now routing to a qualified worker. "
                "Use nexus_check_status with this task_id to poll for results. "
                "Typical wait: 5-15 seconds."
            ),
        }, indent=2),
    )]


async def _handle_check_status(args: dict):
    mode = args["mode"]
    if mode == "balance":
        resp = http.get("/api/v1/credits/balance")
        resp.raise_for_status()
        data = resp.json()
        return [TextContent(
            type="text",
            text=json.dumps({
                "credits_balance": data["credits_balance"],
                "credits_frozen": data["credits_frozen"],
                "credits_available": data["credits_balance"],
            }, indent=2),
        )]
    else:
        task_id = args.get("task_id")
        if not task_id:
            return [TextContent(type="text", text="Error: task_id is required for mode='task'")]
        resp = http.get(f"/api/v1/tasks/{task_id}")
        resp.raise_for_status()
        data = resp.json()
        return [TextContent(
            type="text",
            text=json.dumps({
                "task_id": data["id"],
                "status": data["status"],
                "awarded_to": data.get("awarded_to"),
                "awarded_price": data.get("awarded_price"),
            }, indent=2),
        )]


# ─── V2 Tool handlers: Capability catalog + Jobs ─────────────────

def _unwrap(resp: "httpx.Response") -> dict:
    """V2 responses are envelope-wrapped ({data, meta}). Pull the data out
    so MCP callers see the same shape they would on the REST API."""
    resp.raise_for_status()
    body = resp.json()
    # Envelope-aware — support both envelope and bare dict for forward compat.
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


async def _handle_discover_capabilities(args: dict):
    params = {
        "page": args.get("page", 1),
        "per_page": args.get("per_page", 20),
    }
    for key in ("category", "input_mode", "output_mode", "tag"):
        if args.get(key):
            params[key] = args[key]
    if args.get("max_price") is not None:
        params["max_price"] = args["max_price"]
    resp = http.get("/api/v2/capability-specs", params=params)
    data = _unwrap(resp)
    # Trim to the fields a caller actually needs to decide — full spec
    # detail is available via a follow-up GET if needed.
    summary = [
        {
            "id": s["id"],
            "name": s["name"],
            "category": s["category"],
            "input_mode": s["input_mode"],
            "output_mode": s["output_mode"],
            "price_nc": s["price_nc"],
            "validation_mode": s["validation_mode"],
            "description": s.get("description"),
            "tags": s.get("tags", []),
        }
        for s in data
    ]
    return [TextContent(
        type="text",
        text=json.dumps({
            "count": len(summary),
            "capabilities": summary,
            "hint": (
                "Pick a capability_spec_id and pass it to nexus_create_job "
                "with an input_envelope that matches input_mode."
            ),
        }, indent=2),
    )]


async def _handle_create_job(args: dict):
    payload = {
        "capability_spec_id": args["capability_spec_id"],
        "input_envelope": args["input_envelope"],
        "budget_ceiling_nc": args["budget_ceiling_nc"],
    }
    if args.get("claim_window_hours"):
        payload["claim_window_hours"] = args["claim_window_hours"]
    resp = http.post("/api/v2/jobs", json=payload)
    data = _unwrap(resp)
    return [TextContent(
        type="text",
        text=json.dumps({
            "job_id": data["id"],
            "status": data["status"],
            "budget_ceiling_nc": data["budget_ceiling_nc"],
            "validation_mode": data["validation_mode"],
            "claim_deadline": data.get("claim_deadline"),
            "message": (
                f"Job created ({data['status']}). Validation mode: "
                f"{data['validation_mode']}. "
                "Use nexus_check_job to poll; terminal states are COMPLETED / "
                "FAILED / CANCELLED / EXPIRED, and HELD means awaiting "
                "caller / jury / timeout resolution."
            ),
        }, indent=2),
    )]


async def _handle_check_job(args: dict):
    resp = http.get(f"/api/v2/jobs/{args['job_id']}")
    data = _unwrap(resp)
    return [TextContent(type="text", text=json.dumps(data, indent=2))]


async def _handle_accept_job(args: dict):
    resp = http.post(f"/api/v2/jobs/{args['job_id']}/accept")
    data = _unwrap(resp)
    return [TextContent(type="text", text=json.dumps(data, indent=2))]


async def _handle_reject_job(args: dict):
    resp = http.post(
        f"/api/v2/jobs/{args['job_id']}/reject",
        json={"reason": args.get("reason", "")},
    )
    data = _unwrap(resp)
    return [TextContent(type="text", text=json.dumps(data, indent=2))]


async def _handle_dispute_job(args: dict):
    resp = http.post(
        f"/api/v2/jobs/{args['job_id']}/dispute",
        json={"reason": args["reason"]},
    )
    data = _unwrap(resp)
    return [TextContent(type="text", text=json.dumps(data, indent=2))]


async def _handle_claim_job(args: dict):
    resp = http.post(f"/api/v2/jobs/{args['job_id']}/claim")
    data = _unwrap(resp)
    return [TextContent(type="text", text=json.dumps(data, indent=2))]


async def _handle_submit_job(args: dict):
    resp = http.post(
        f"/api/v2/jobs/{args['job_id']}/submit",
        json={"output_envelope": args["output_envelope"]},
    )
    data = _unwrap(resp)
    return [TextContent(type="text", text=json.dumps(data, indent=2))]


async def _handle_register_spec(args: dict):
    # Pass through every supplied field — API layer validates.
    payload = {k: v for k, v in args.items() if v is not None}
    resp = http.post("/api/v2/capability-specs", json=payload)
    data = _unwrap(resp)
    return [TextContent(type="text", text=json.dumps(data, indent=2))]


async def _handle_backfill_specs(_args: dict):
    resp = http.post("/api/v2/capability-specs/backfill")
    data = _unwrap(resp)
    return [TextContent(type="text", text=json.dumps(data, indent=2))]


# ─── Bootstrap (auto-resolve credentials on first run) ───────────

def _bootstrap_credentials() -> None:
    """Resolve NEXUS_API_KEY + BASE_URL for the process. Order:

      1. Existing env vars (set before launch)
      2. ``~/.nexus/credentials`` (populated by a prior run / CLI login)
      3. If we're attached to a TTY: run the OAuth device flow interactively,
         saving the result to ``~/.nexus/credentials`` for future runs.
      4. Otherwise error with actionable instructions.

    MCP protocol uses stdout for JSON-RPC messages, so all bootstrap
    output goes to **stderr** to avoid corrupting the transport.
    """
    global API_KEY, BASE_URL
    if API_KEY:
        return

    # Step 2: credentials file
    try:
        from nexus_sdk.credentials import load_credentials, save_credentials
        creds = load_credentials()
    except Exception:
        creds = None
    if creds and creds.get("api_key"):
        API_KEY = creds["api_key"]
        if creds.get("base_url"):
            BASE_URL = creds["base_url"].rstrip("/")
        print(
            f"[nexus-mcp] using saved credentials for account {creds.get('account_id', '?')}",
            file=sys.stderr,
        )
        return

    # Step 3: device flow — only if we can actually prompt a human
    interactive = sys.stderr.isatty() and os.getenv("NEXUS_MCP_NO_INTERACTIVE") != "1"
    if not interactive:
        print(
            "[nexus-mcp] No NEXUS_API_KEY and no saved credentials found.\n"
            "           Either:\n"
            "             - run `nexus-mcp` in a terminal once to trigger device-flow login, or\n"
            "             - set NEXUS_API_KEY=... in the MCP config for your AI tool, or\n"
            "             - generate a key at https://nexustoken.ai/dashboard/api-keys",
            file=sys.stderr,
        )
        sys.exit(1)

    # Interactive: run device flow (print to stderr, save to disk on success)
    try:
        from nexus_sdk.client import NexusClient
    except Exception as e:
        print(f"[nexus-mcp] cannot import device flow helper: {e}", file=sys.stderr)
        sys.exit(1)

    def _stderr(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    try:
        client = NexusClient.login_device_flow(
            base_url=BASE_URL,
            client_name="nexus-mcp",
            timeout=600,
            print_fn=_stderr,
            save=True,
            skip_version_check=True,
        )
    except Exception as e:
        print(f"[nexus-mcp] device-flow login failed: {e}", file=sys.stderr)
        sys.exit(1)

    # login_device_flow writes to ~/.nexus/credentials itself;
    # re-read so we don't need to expose the api_key attribute on the client.
    try:
        creds = load_credentials()
        if creds and creds.get("api_key"):
            API_KEY = creds["api_key"]
            if creds.get("base_url"):
                BASE_URL = creds["base_url"].rstrip("/")
    except Exception:
        pass

    if not API_KEY:
        print("[nexus-mcp] device flow completed but no api_key was saved", file=sys.stderr)
        sys.exit(1)


# ─── Main ────────────────────────────────────────────────────────

def _print_tty_setup_success() -> None:
    """When a human runs `nexus-mcp` directly in a terminal, speaking the MCP
    protocol over stdin/stdout makes no sense (stdin is a keyboard). After
    bootstrap we print an instructional message and exit 0 instead — the
    next MCP-capable tool the user launches will pick the saved credentials
    up automatically."""
    msg = f"""
[nexus-mcp] ✓ Credentials saved to ~/.nexus/credentials
[nexus-mcp]
[nexus-mcp] Running this command directly from a terminal is only useful for
[nexus-mcp] one-time setup — the MCP protocol uses stdin/stdout for JSON-RPC,
[nexus-mcp] so it expects to be launched BY an AI tool (not by you directly).
[nexus-mcp]
[nexus-mcp] Next step: plug `nexus-mcp` into your AI tool of choice.
[nexus-mcp]   Claude Code:    claude mcp add nexus -- uvx --from 'nexustoken-sdk[mcp]' nexus-mcp
[nexus-mcp]   Cursor / Desktop: see per-tool configs at https://nexustoken.ai/#connect
[nexus-mcp]
[nexus-mcp] The saved credentials will be picked up automatically — no extra
[nexus-mcp] config or env vars needed."""
    print(msg.rstrip(), file=sys.stderr)


async def main():
    global http
    _bootstrap_credentials()

    # If we were launched by a human in a terminal (stdin is a TTY), we have
    # just finished setup. Exit cleanly instead of garbling stdin into the
    # MCP JSON-RPC parser. Real MCP clients (Claude Code, Cursor, etc.)
    # pipe us proper JSON-RPC on stdin — `isatty()` is False in that case.
    if sys.stdin.isatty() and os.getenv("NEXUS_MCP_FORCE_SERVE") != "1":
        _print_tty_setup_success()
        return

    http = _build_http_client()
    logger.info(f"NexusToken MCP Server v{MCP_VERSION} starting (base_url={BASE_URL})")
    check_version()
    if _MCP_LEGACY_API:
        # Old mcp package (< 0.2): one-shot coroutine owns the stdio pipe.
        await _legacy_run_server(server)  # type: ignore[name-defined]
    else:
        # Modern mcp package: caller owns the stdio context manager.
        async with _stdio_ctx() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )


def cli_main() -> None:
    """Console-script entry point used by the pip-installed `nexus-mcp` command.

    After ``pip install 'nexustoken-sdk[mcp]'`` users get a ``nexus-mcp``
    binary on their PATH. They can point any MCP-compatible AI tool
    (Claude Code, Claude Desktop, Cursor, OpenCode, Codex) at the command
    and set ``NEXUS_API_KEY`` / ``NEXUS_BASE_URL`` via env.
    """
    import asyncio
    asyncio.run(main())


if __name__ == "__main__":
    cli_main()
