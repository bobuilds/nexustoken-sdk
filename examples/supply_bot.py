#!/usr/bin/env python3
"""
Example supply-side bot (接单方 / 官方龙虾).

Usage:
    python supply_bot.py

This script:
1. Registers a new account
2. Polls for available tasks
3. Bids on tasks at 80% of max budget
4. Processes tasks with a simple JSON extraction logic
5. Submits results
"""

import json
import re
import time
import httpx

BASE_URL = "http://localhost:8000"


def extract_json_from_text(input_data: str, schema: dict) -> dict:
    """
    Simple JSON extraction logic.
    In production, this would call an LLM like GPT-4o-mini.
    For demo, we do basic pattern matching.
    """
    result = {}
    properties = schema.get("properties", {})

    for field, field_schema in properties.items():
        field_type = field_schema.get("type", "string")

        if field_type == "string":
            # Look for patterns like "Field: Value"
            pattern = rf"(?i){field.replace('_', '[_ ]')}[:\s]+(.+?)(?:\n|$)"
            match = re.search(pattern, input_data)
            if match:
                result[field] = match.group(1).strip()

        elif field_type == "number":
            # Look for dollar amounts or numbers near the field name
            pattern = rf"(?i){field.replace('_', '[_ ]')}[:\s]*\$?([\d,]+\.?\d*)"
            match = re.search(pattern, input_data)
            if match:
                result[field] = float(match.group(1).replace(",", ""))

        elif field_type == "integer":
            pattern = rf"(?i){field.replace('_', '[_ ]')}[:\s]*(\d+)"
            match = re.search(pattern, input_data)
            if match:
                result[field] = int(match.group(1))

        elif field_type == "array":
            # Look for comma-separated values
            pattern = rf"(?i){field.replace('_', '[_ ]')}[:\s]+(.+?)(?:\n|$)"
            match = re.search(pattern, input_data)
            if match:
                items = [x.strip() for x in match.group(1).split(",")]
                result[field] = items

    return result


def main():
    client = httpx.Client(base_url=BASE_URL, timeout=30)

    # 1. Register
    print("[1] Registering supply bot...")
    resp = client.post("/api/v1/auth/register", json={"email": f"supply_{int(time.time())}@bot.com"})
    resp.raise_for_status()
    api_key = resp.json()["api_key"]
    headers = {"X-API-Key": api_key}
    print(f"    Account: {resp.json()['account_id']}")

    # 2. Main loop
    print("[2] Starting worker loop (Ctrl+C to stop)...")
    bid_ratio = 0.8  # Bid at 80% of max budget

    try:
        while True:
            # Poll for tasks
            resp = client.get("/api/v1/tasks/available", headers=headers)
            resp.raise_for_status()
            tasks = resp.json()

            if not tasks:
                time.sleep(1)
                continue

            for task in tasks:
                task_id = task["id"]
                budget = task["max_budget_credits"]
                bid_amount = max(1, int(budget * bid_ratio))

                print(f"\n    Found task {task_id[:8]}... (budget={budget})")

                # Bid
                try:
                    resp = client.post(
                        f"/api/v1/tasks/{task_id}/bid",
                        json={"bid_credits": bid_amount},
                        headers=headers,
                    )
                    if resp.status_code == 200:
                        print(f"    Bid placed: {bid_amount} credits")
                    elif resp.status_code == 409:
                        print("    Already bid on this task")
                        continue
                    else:
                        print(f"    Bid failed: {resp.status_code} {resp.text}")
                        continue
                except Exception as e:
                    print(f"    Bid error: {e}")
                    continue

                # Wait for award (check task status)
                awarded = False
                for _ in range(10):
                    time.sleep(1)
                    resp = client.get(f"/api/v1/tasks/{task_id}", headers=headers)
                    if resp.status_code != 200:
                        break
                    status = resp.json()["status"]
                    if status == "AWARDED":
                        awarded = True
                        break
                    elif status in ("SETTLED", "EXPIRED", "CANCELLED", "BIDDING"):
                        break

                if not awarded:
                    print("    Not awarded (or task moved on)")
                    continue

                print("    Awarded! Processing...")

                # Get full task data
                resp = client.get(f"/api/v1/tasks/{task_id}", headers=headers)
                task_detail = resp.json()

                # Extract JSON using simple logic — use full input_data from detail endpoint
                input_text = task_detail.get("input_data") or task_detail.get("input_data_preview", "")
                result = extract_json_from_text(
                    input_text,
                    task_detail.get("validation_schema", task["validation_schema"]),
                )
                print(f"    Extracted: {json.dumps(result, indent=2)}")

                # Submit
                resp = client.post(
                    f"/api/v1/tasks/{task_id}/submit",
                    json={"result_data": result},
                    headers=headers,
                )
                if resp.status_code == 200:
                    submit_data = resp.json()
                    if submit_data["error_code"] is None:
                        print("    PASS! Task settled.")
                    else:
                        print(f"    FAIL: {submit_data['error_code']} (retries: {submit_data['retries_left']})")
                else:
                    print(f"    Submit error: {resp.status_code} {resp.text}")

            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\nWorker stopped.")

    # Check earnings
    resp = client.get("/api/v1/credits/balance", headers=headers)
    if resp.status_code == 200:
        balance = resp.json()
        print(f"Final balance: {balance['credits_balance']} credits")

    resp = client.get("/api/v1/account/reputation", headers=headers)
    if resp.status_code == 200:
        rep = resp.json()
        print(f"Reputation: {rep['reputation']}, Tasks: {rep['task_count']}")


if __name__ == "__main__":
    main()
