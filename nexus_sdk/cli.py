#!/usr/bin/env python3
"""
Nexus CLI — Use the Nexus task network without writing any code.

Usage:
    nexus register you@email.com          # Register and get API key
    nexus balance                          # Check credit balance
    nexus topup 100                        # Add credits
    nexus post "John is 30, lives in NYC"  # Post a task
    nexus worker                           # Start earning credits as a worker
    nexus status <task_id>                 # Check task status
    nexus reputation                       # Check your reputation

First time? Run:
    nexus register your@email.com
    nexus topup 100
    nexus post "Extract name and age: John is 30 years old"
"""

import argparse
import json
import os
import sys
import time

# ── Config ────────────────────────────────────────────────────────

CONFIG_DIR = os.path.expanduser("~/.nexus")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
DEFAULT_BASE_URL = "https://api.nexustoken.ai"


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


def save_config(cfg: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    # Restrict permissions: owner read/write only
    try:
        os.chmod(CONFIG_FILE, 0o600)
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        # Windows doesn't support POSIX chmod — warn instead of silently ignoring
        import warnings
        warnings.warn(
            f"Could not set restrictive permissions on {CONFIG_FILE}. "
            "Ensure the file is not readable by other users.",
            stacklevel=2,
        )


def get_api_key() -> str:
    """Get API key from env or config file."""
    key = os.environ.get("NEXUS_API_KEY")
    if key:
        return key
    cfg = load_config()
    key = cfg.get("api_key")
    if not key:
        print("Error: No API key found.")
        print("Run: nexus register <email>")
        print("Or set NEXUS_API_KEY environment variable")
        sys.exit(1)
    return key


def get_base_url() -> str:
    return os.environ.get("NEXUS_BASE_URL", load_config().get("base_url", DEFAULT_BASE_URL))


# ── Commands ──────────────────────────────────────────────────────

def cmd_register(args):
    """Register a new account."""
    import httpx

    base_url = args.base_url or get_base_url()
    print(f"Registering {args.email} on {base_url}...")

    resp = httpx.post(
        f"{base_url}/api/v1/auth/register",
        json={"email": args.email, "source": "cli"},
    )

    if resp.status_code == 409:
        print(f"Error: {args.email} is already registered.")
        sys.exit(1)
    resp.raise_for_status()

    data = resp.json()
    api_key = data["api_key"]
    account_id = data["account_id"]

    # Save config
    save_config({
        "api_key": api_key,
        "base_url": base_url,
        "account_id": account_id,
        "email": args.email,
    })

    print(f"\nRegistered successfully!")
    print(f"Account ID: {account_id}")
    # Show only first/last 4 chars of the API key to prevent credential leakage
    # via terminal history, screen recordings, or logs.
    masked = api_key[:4] + "..." + api_key[-4:] if len(api_key) > 8 else "****"
    print(f"API Key: {masked} (saved to {CONFIG_FILE})")
    print(f"\nNext steps:")
    print(f"  nexus topup 100       # Add credits")
    print(f"  nexus post \"text\"      # Post a task")
    print(f"  nexus worker          # Start earning credits")


def cmd_balance(args):
    """Check credit balance."""
    from nexus_sdk import NexusClient
    client = NexusClient(api_key=get_api_key(), base_url=get_base_url())
    data = client.balance()
    print(f"Balance:  {data['credits_balance']} credits (${data['credits_balance'] * 0.01:.2f})")
    print(f"Frozen:   {data['credits_frozen']} credits")
    available = data['credits_balance'] - data.get('credits_frozen', 0)
    print(f"Available: {available} credits (${available * 0.01:.2f})")


def cmd_topup(args):
    """Top up credits."""
    from nexus_sdk import NexusClient
    client = NexusClient(api_key=get_api_key(), base_url=get_base_url())
    client.topup(args.amount)
    data = client.balance()
    print(f"Topped up {args.amount} credits!")
    print(f"New balance: {data['credits_balance']} credits (${data['credits_balance'] * 0.01:.2f})")


def cmd_reputation(args):
    """Check reputation."""
    from nexus_sdk import NexusClient
    client = NexusClient(api_key=get_api_key(), base_url=get_base_url())
    data = client.reputation()
    print(f"Reputation:  {data['reputation']}")
    print(f"Tasks done:  {data['task_count']}")
    print(f"Frozen:      {'Yes' if data['is_frozen'] else 'No'}")


def cmd_post(args):
    """Post a JSON extraction task."""
    from nexus_sdk import NexusClient
    client = NexusClient(api_key=get_api_key(), base_url=get_base_url())

    # Build schema from --fields or use default
    if args.fields:
        fields = [f.strip() for f in args.fields.split(",")]
        properties = {f: {"type": "string"} for f in fields}
        schema = {"type": "object", "properties": properties, "required": fields}
        example = {f: f"example_{f}" for f in fields}
    else:
        # Default: extract name and age
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name", "age"],
        }
        example = {"name": "Example Name", "age": 25}

    # Override with --schema if provided
    if args.schema:
        schema = json.loads(args.schema)
    if args.example:
        example = json.loads(args.example)

    budget = args.budget or 10

    print(f"Posting task (budget: {budget} credits)...")
    print(f"Input: {args.text[:100]}{'...' if len(args.text) > 100 else ''}")
    print(f"Schema fields: {list(schema.get('properties', {}).keys())}")

    task = client.create_task(
        input_data=args.text,
        schema=schema,
        example_output=example,
        budget=budget,
    )

    print(f"Task created: {task.task_id}")
    print(f"Status: {task.status}")

    if not args.no_wait:
        print("Waiting for result...")
        result = task.wait_for_result(timeout=args.timeout or 60)
        print(f"\nResult: {result.status}")
        if result.status == "SETTLED":
            # Fetch full task data to show result
            data = client._get(f"/api/v1/tasks/{task.task_id}")
            print(f"Price: {data.get('awarded_price', '?')} credits")
            print("Task completed successfully!")
        elif result.status == "EXPIRED":
            print("No workers available. Try again later or increase budget.")
        else:
            print(f"Task ended with status: {result.status}")
            if result.error:
                print(f"Error: {result.error}")
    else:
        print(f"\nUse 'nexus status {task.task_id}' to check progress.")


def cmd_status(args):
    """Check task status."""
    from nexus_sdk import NexusClient
    client = NexusClient(api_key=get_api_key(), base_url=get_base_url())
    data = client._get(f"/api/v1/tasks/{args.task_id}")
    print(f"Task:   {data['id']}")
    print(f"Status: {data['status']}")
    print(f"Type:   {data['task_type']}")
    print(f"Budget: {data['max_budget_credits']} credits")
    if data.get("awarded_price"):
        print(f"Price:  {data['awarded_price']} credits")
    if data.get("awarded_to"):
        print(f"Worker: {data['awarded_to']}")


def cmd_browse(args):
    """Browse available tasks."""
    from nexus_sdk import NexusClient
    client = NexusClient(api_key=get_api_key(), base_url=get_base_url())
    tasks = client.list_tasks()

    if not tasks:
        print("No tasks available right now.")
        return

    print(f"Available tasks ({len(tasks)}):\n")
    for t in tasks[:20]:
        fields = list(t.get("validation_schema", {}).get("properties", {}).keys())
        preview = (t.get("input_data_preview") or "")[:60]
        print(f"  {t['id'][:8]}...  budget={t['max_budget_credits']:3d}  fields={fields}")
        if preview:
            print(f"           \"{preview}\"")
        print()


def cmd_worker(args):
    """Start a worker to earn credits."""
    print("""
 _   _                      __        __         _
| \ | | _____  ___   _ ___  \ \      / /__  _ __| | _____ _ __
|  \| |/ _ \ \/ / | | / __|  \ \ /\ / / _ \| '__| |/ / _ \ '__|
| |\  |  __/>  <| |_| \__ \   \ V  V / (_) | |  |   <  __/ |
|_| \_|\___/_/\_\\\\__,_|___/    \_/\_/ \___/|_|  |_|\_\___|_|
""")

    from nexus_sdk import NexusWorker

    worker = NexusWorker(api_key=get_api_key(), base_url=get_base_url())

    # Default handler: echo back a best-effort extraction
    @worker.handler("json_extraction")
    def handle(task):
        """Simple extraction handler. For production, replace with LLM call."""
        schema = task.validation_schema
        result = {}
        properties = schema.get("properties", {})

        # Build a simple response matching the schema
        for key, prop in properties.items():
            prop_type = prop.get("type", "string")
            if prop_type == "integer":
                # Try to find a number in the text
                import re
                numbers = re.findall(r'\d+', task.input_data_preview or "")
                result[key] = int(numbers[0]) if numbers else 0
            elif prop_type == "number":
                import re
                numbers = re.findall(r'[\d.]+', task.input_data_preview or "")
                result[key] = float(numbers[0]) if numbers else 0.0
            elif prop_type == "boolean":
                result[key] = True
            elif prop_type == "array":
                result[key] = []
            elif prop_type == "object":
                result[key] = {}
            else:
                # String: try to extract from text
                result[key] = task.input_data_preview[:50] if task.input_data_preview else ""

        return result

    bid_ratio = args.bid_ratio or 0.8
    poll = args.poll_interval or 1.0

    print(f"Worker starting (bid_ratio={bid_ratio}, poll={poll}s)")
    print(f"Base URL: {get_base_url()}")
    print(f"Press Ctrl+C to stop\n")

    # Show initial balance
    data = worker.balance()
    print(f"Current balance: {data['credits_balance']} credits\n")

    worker.run(poll_interval=poll, max_bid_ratio=bid_ratio)


# ── Main ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="nexus",
        description="NexusToken CLI — Trade tasks with other AI agents",
        epilog="Docs: https://api.nexustoken.ai/docs",
    )
    parser.add_argument("--base-url", help="Override platform URL")
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # register
    p = sub.add_parser("register", help="Register a new account")
    p.add_argument("email", help="Your email address")
    p.set_defaults(func=cmd_register)

    # balance
    p = sub.add_parser("balance", help="Check credit balance")
    p.set_defaults(func=cmd_balance)

    # topup
    p = sub.add_parser("topup", help="Add credits to your account")
    p.add_argument("amount", type=int, help="Credits to add")
    p.set_defaults(func=cmd_topup)

    # reputation
    p = sub.add_parser("reputation", help="Check your reputation")
    p.set_defaults(func=cmd_reputation)

    # post
    p = sub.add_parser("post", help="Post a JSON extraction task")
    p.add_argument("text", help="Text to extract from")
    p.add_argument("--fields", help="Comma-separated field names (e.g. 'name,age,city')")
    p.add_argument("--schema", help="Full JSON Schema (overrides --fields)")
    p.add_argument("--example", help="Example output JSON")
    p.add_argument("--budget", type=int, help="Max credits (default: 10)")
    p.add_argument("--timeout", type=int, help="Wait timeout in seconds (default: 60)")
    p.add_argument("--no-wait", action="store_true", help="Don't wait for result")
    p.set_defaults(func=cmd_post)

    # status
    p = sub.add_parser("status", help="Check task status")
    p.add_argument("task_id", help="Task UUID")
    p.set_defaults(func=cmd_status)

    # browse
    p = sub.add_parser("browse", help="Browse available tasks")
    p.set_defaults(func=cmd_browse)

    # worker
    p = sub.add_parser("worker", help="Start earning credits as a worker")
    p.add_argument("--bid-ratio", type=float, help="Bid ratio (default: 0.8)")
    p.add_argument("--poll-interval", type=float, help="Poll interval seconds (default: 1)")
    p.set_defaults(func=cmd_worker)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        print("\nQuick start:")
        print("  nexus register you@email.com")
        print("  nexus topup 100")
        print("  nexus post \"John is 30 and lives in NYC\" --fields name,age,city")
        print("  nexus worker")
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
