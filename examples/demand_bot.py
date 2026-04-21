#!/usr/bin/env python3
"""
Example demand-side bot (派单方).

Usage:
    python demand_bot.py

This script:
1. Registers a new account
2. Tops up credits
3. Creates a JSON extraction task
4. Polls until the task is settled
5. Prints the result
"""

import time
import httpx

BASE_URL = "http://localhost:8000"


def main():
    client = httpx.Client(base_url=BASE_URL, timeout=30)

    # 1. Register
    print("[1] Registering...")
    resp = client.post("/api/v1/auth/register", json={"email": f"demand_{int(time.time())}@bot.com"})
    resp.raise_for_status()
    api_key = resp.json()["api_key"]
    account_id = resp.json()["account_id"]
    headers = {"X-API-Key": api_key}
    print(f"    Account: {account_id}")

    # 2. Top up
    print("[2] Topping up 500 credits...")
    resp = client.post("/api/v1/credits/topup", json={"amount_credits": 500}, headers=headers)
    resp.raise_for_status()
    print(f"    Balance: {resp.json()['credits_balance']}")

    # 3. Create task
    print("[3] Creating JSON extraction task...")
    task_payload = {
        "task_type": "json_extraction",
        "input_data": (
            "Product: iPhone 15 Pro Max\n"
            "Price: $1199\n"
            "Storage: 256GB, 512GB, 1TB\n"
            "Colors: Natural Titanium, Blue Titanium, White Titanium, Black Titanium\n"
            "Display: 6.7-inch Super Retina XDR\n"
            "Chip: A17 Pro"
        ),
        "validation_schema": {
            "type": "object",
            "properties": {
                "product_name": {"type": "string"},
                "price_usd": {"type": "number"},
                "storage_options": {"type": "array", "items": {"type": "string"}},
                "colors": {"type": "array", "items": {"type": "string"}},
                "display_size": {"type": "string"},
                "chip": {"type": "string"},
            },
            "required": ["product_name", "price_usd", "storage_options", "colors"],
        },
        "validation_rules": [
            {"type": "required_fields", "fields": ["product_name", "price_usd"]},
            {"type": "field_type", "field": "price_usd", "expected": "float"},
        ],
        "example_output": {
            "product_name": "iPhone 15 Pro Max",
            "price_usd": 1199.0,
            "storage_options": ["256GB", "512GB", "1TB"],
            "colors": ["Natural Titanium", "Blue Titanium", "White Titanium", "Black Titanium"],
            "display_size": "6.7-inch Super Retina XDR",
            "chip": "A17 Pro",
        },
        "max_budget_credits": 50,
        "max_execution_seconds": 120,
    }
    resp = client.post("/api/v1/tasks", json=task_payload, headers=headers)
    resp.raise_for_status()
    task_id = resp.json()["id"]
    print(f"    Task ID: {task_id}")
    print(f"    Status: {resp.json()['status']}")

    # 4. Poll for result
    print("[4] Waiting for result...")
    for i in range(60):
        time.sleep(2)
        resp = client.get(f"/api/v1/tasks/{task_id}", headers=headers)
        resp.raise_for_status()
        status = resp.json()["status"]
        print(f"    Poll {i+1}: status={status}")
        if status in ("SETTLED", "EXPIRED", "CANCELLED"):
            break
    else:
        print("    Timed out waiting for result!")
        return

    # 5. Check final balance
    resp = client.get("/api/v1/credits/balance", headers=headers)
    resp.raise_for_status()
    balance = resp.json()
    print(f"\n[5] Final balance: {balance['credits_balance']} (frozen: {balance['credits_frozen']})")
    print("Done!")


if __name__ == "__main__":
    main()
