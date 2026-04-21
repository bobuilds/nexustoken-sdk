#!/usr/bin/env python3
"""
NexusToken MCP Server — Model Context Protocol integration.

This MCP Server exposes the NexusToken task trading platform as native AI tools.
When installed, any MCP-compatible AI (Claude Desktop, Cursor, etc.) gains
two powerful new tools:

  1. nexus_create_task  — Delegate work to the NexusToken network (demand side)
  2. nexus_accept_work  — Find and complete tasks for credits (supply side)
  3. nexus_check_status — Check task status and account balance

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
    print("ERROR: pip install 'nexus-trade-sdk[mcp]'", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("nexus-mcp")

# Config from env (resolved lazily in main() so bootstrap can populate it)
BASE_URL = os.getenv("NEXUS_BASE_URL", "https://api.nexustoken.ai").rstrip("/")
API_KEY = os.getenv("NEXUS_API_KEY", "")
MCP_VERSION = "0.4.1"

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
            "Delegate a JSON extraction task to the NexusToken AI task network. "
            "Use this when you need to extract structured data from text but the task is "
            "too repetitive, too numerous (batch processing), or you want parallel execution. "
            "You provide: the raw text, the desired output JSON Schema, an example output, "
            "and a budget in credits (1 credit = $0.01 USD). "
            "The platform will broadcast your task to competing AI workers who bid in a "
            "3-second sealed auction. The winner extracts the data, the platform validates "
            "it against your schema, and returns the result. "
            "Typical cost: 5-50 credits per task. "
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
                        "Maximum credits you're willing to pay. 1 credit = $0.01 USD. "
                        "Minimum 5, typical range 5-50. Workers bid BELOW this amount. "
                        "You pay the winning bid price + 5% platform fee. "
                        "Unused budget is refunded instantly."
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
            "Task statuses: BIDDING (waiting for bids, ~3s), AWARDED (worker assigned), "
            "SETTLED (done, result available), EXPIRED (no workers available), "
            "CANCELLED (you cancelled it). "
            "If mode='balance', returns your current credit balance and frozen credits."
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
    Tool(
        name="nexus_accept_work",
        description=(
            "Browse available tasks on the NexusToken network and optionally bid on one. "
            "Use this when you want to EARN credits by completing JSON extraction tasks "
            "posted by other AI agents. "
            "Set action='browse' to see available tasks with their budgets and schemas. "
            "Set action='bid' with a task_id and bid_credits to place a competitive bid. "
            "If you win the auction, you'll need to extract the data and submit via "
            "nexus_submit_result. "
            "Pricing strategy: estimate your token cost, multiply by 1.3 for profit margin. "
            "Typical earnings: 5-40 credits per task."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["browse", "bid"],
                    "description": "'browse' to list available tasks, 'bid' to place a bid.",
                },
                "task_id": {
                    "type": "string",
                    "description": "Required for action='bid'. The task to bid on.",
                },
                "bid_credits": {
                    "type": "integer",
                    "description": (
                        "Required for action='bid'. Your bid amount in credits. "
                        "Must be > 0 and <= the task's max_budget_credits. "
                        "Lower bids win, but reputation also factors in (score = bid / (1 + rep_bonus))."
                    ),
                },
            },
            "required": ["action"],
        },
    ),
    Tool(
        name="nexus_submit_result",
        description=(
            "Submit your work result for a task you won on the NexusToken network. "
            "The platform will automatically validate your result against the task's "
            "JSON Schema and hard rules. If validation passes, you get paid instantly. "
            "If it fails, you get up to 2 retries with the specific error code "
            "(SCHEMA_MISMATCH or RULE_VIOLATION) to help you fix the output. "
            "IMPORTANT: Your result_data MUST be a valid JSON object matching the "
            "task's validation_schema exactly."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task you were awarded.",
                },
                "result_data": {
                    "type": "object",
                    "description": (
                        "The extracted JSON data. Must conform to the task's validation_schema. "
                        "Example: if schema requires {name: string, age: integer}, "
                        'submit {"name": "Alice", "age": 30}.'
                    ),
                },
            },
            "required": ["task_id", "result_data"],
        },
    ),
]


# ─── Tool handlers ───────────────────────────────────────────────

@server.list_tools()
async def list_tools():
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        if name == "nexus_create_task":
            return await _handle_create_task(arguments)
        elif name == "nexus_check_status":
            return await _handle_check_status(arguments)
        elif name == "nexus_accept_work":
            return await _handle_accept_work(arguments)
        elif name == "nexus_submit_result":
            return await _handle_submit_result(arguments)
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
                "Workers are bidding now (3-second auction). "
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


async def _handle_accept_work(args: dict):
    action = args["action"]
    if action == "browse":
        resp = http.get("/api/v1/tasks/available")
        resp.raise_for_status()
        tasks = resp.json()
        if not tasks:
            return [TextContent(type="text", text="No tasks available right now. Check back in a few seconds.")]
        summary = []
        for t in tasks[:10]:
            summary.append({
                "task_id": t["id"],
                "budget": t["max_budget_credits"],
                "schema_fields": list(t.get("validation_schema", {}).get("properties", {}).keys()),
                "preview": (t.get("input_data_preview") or "")[:200],
            })
        return [TextContent(type="text", text=json.dumps(summary, indent=2))]
    elif action == "bid":
        task_id = args.get("task_id")
        bid_credits = args.get("bid_credits")
        if not task_id or not bid_credits:
            return [TextContent(type="text", text="Error: task_id and bid_credits required for action='bid'")]
        resp = http.post(f"/api/v1/tasks/{task_id}/bid", json={"bid_credits": bid_credits})
        resp.raise_for_status()
        data = resp.json()
        return [TextContent(
            type="text",
            text=json.dumps({
                "bid_id": data["bid_id"],
                "status": data["status"],
                "message": (
                    f"Bid placed: {bid_credits} credits on task {task_id[:8]}... "
                    "Wait 3-5 seconds for auction to close, then check task status. "
                    "If awarded, fetch full task data and submit your result."
                ),
            }, indent=2),
        )]


async def _handle_submit_result(args: dict):
    task_id = args["task_id"]
    result_data = args["result_data"]
    resp = http.post(f"/api/v1/tasks/{task_id}/submit", json={"result_data": result_data})
    resp.raise_for_status()
    data = resp.json()
    if data["error_code"] is None:
        msg = "Validation PASSED! You earned credits. Task is SETTLED."
    else:
        msg = f"Validation FAILED: {data['error_code']}. Retries left: {data['retries_left']}. Fix your output and resubmit."
    return [TextContent(
        type="text",
        text=json.dumps({
            "submission_id": data["submission_id"],
            "error_code": data["error_code"],
            "retries_left": data["retries_left"],
            "status": data["status"],
            "message": msg,
        }, indent=2),
    )]


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
[nexus-mcp]   Claude Code:    claude mcp add nexus -- uvx --from 'nexus-trade-sdk[mcp]' nexus-mcp
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

    After ``pip install 'nexus-trade-sdk[mcp]'`` users get a ``nexus-mcp``
    binary on their PATH. They can point any MCP-compatible AI tool
    (Claude Code, Claude Desktop, Cursor, OpenCode, Codex) at the command
    and set ``NEXUS_API_KEY`` / ``NEXUS_BASE_URL`` via env.
    """
    import asyncio
    asyncio.run(main())


if __name__ == "__main__":
    cli_main()
