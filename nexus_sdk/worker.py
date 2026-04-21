"""
Nexus SDK — Supply-side worker (接单方).

Usage:
    from nexus_sdk import NexusWorker

    worker = NexusWorker(api_key="your-api-key", base_url="http://localhost:8000")

    @worker.handler("json_extraction")
    def handle(task):
        # task has: task_id, input_data_preview, validation_schema, max_budget_credits
        return {"extracted": "data"}

    worker.run(poll_interval=1, max_bid_ratio=0.8)
"""

import logging
import platform
import sys
import time
from typing import Any, Callable, Dict, Optional

import httpx

from nexus_sdk._compat import check_server_version, raise_for_status
from nexus_sdk._version import __version__ as SDK_VERSION
from nexus_sdk.exceptions import NexusAPIError

logger = logging.getLogger(__name__)


class TaskContext:
    """Context object passed to handler functions."""

    def __init__(self, task_data: dict):
        self.task_id: str = task_data["id"]
        self.task_type: str = task_data["task_type"]
        self.input_data: Optional[str] = task_data.get("input_data")
        self.input_data_preview: Optional[str] = task_data.get("input_data_preview")
        self.validation_schema: dict = task_data.get("validation_schema", {})
        self.max_budget_credits: int = task_data["max_budget_credits"]
        self.max_execution_seconds: int = task_data["max_execution_seconds"]
        self._raw = task_data


class NexusWorker:
    """Supply-side SDK worker."""

    def __init__(self, api_key: str, base_url: str = "http://localhost:8000",
                 skip_version_check: bool = False):
        self.base_url = base_url.rstrip("/")
        # Warn if using unencrypted HTTP for non-local URLs
        if self.base_url.startswith("http://") and "localhost" not in self.base_url and "127.0.0.1" not in self.base_url:
            import warnings
            warnings.warn(
                f"Using unencrypted HTTP for {self.base_url}. "
                "Use https:// for production to prevent credential interception.",
                stacklevel=2,
            )
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={
                "X-API-Key": api_key,
                "User-Agent": f"nexus-sdk/{SDK_VERSION} (python/{py_ver}; {platform.system()})",
                "X-SDK-Version": SDK_VERSION,
                "X-SDK-Source": "sdk",
            },
            timeout=30,
        )
        self._handlers: Dict[str, Callable] = {}
        if not skip_version_check:
            check_server_version(self._http)

    def handler(self, task_type: str):
        """Decorator to register a handler for a task type."""
        def decorator(func: Callable[[TaskContext], dict]):
            self._handlers[task_type] = func
            return func
        return decorator

    def _bid(self, task_id: str, bid_credits: int) -> Optional[dict]:
        try:
            resp = self._http.post(
                f"/api/v1/tasks/{task_id}/bid",
                json={"bid_credits": bid_credits},
            )
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 409:
                return None  # already bid
            else:
                logger.warning(f"Bid failed: {resp.status_code} {resp.text}")
                return None
        except (httpx.HTTPError, OSError) as e:
            logger.error(f"Bid error: {e}")
            return None

    def _submit(self, task_id: str, result_data: dict) -> dict:
        resp = self._http.post(
            f"/api/v1/tasks/{task_id}/submit",
            json={"result_data": result_data},
        )
        raise_for_status(resp)
        return resp.json()

    def _wait_for_award(self, task_id: str, timeout: int = 10) -> bool:
        """Poll until task is AWARDED to us or moves to another state."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = self._http.get(f"/api/v1/tasks/{task_id}")
                if resp.status_code != 200:
                    return False
                status = resp.json()["status"]
                if status == "AWARDED":
                    return True
                if status in ("SETTLED", "EXPIRED", "CANCELLED"):
                    return False
            except (httpx.HTTPError, OSError):
                pass
            time.sleep(0.5)
        return False

    def run(
        self,
        poll_interval: float = 1.0,
        max_bid_ratio: float = 0.8,
        max_retries: int = 2,
    ):
        """
        Main worker loop. Polls for tasks, bids, processes, and submits.

        Args:
            poll_interval: Seconds between polls
            max_bid_ratio: Bid this fraction of max_budget_credits
            max_retries: Max retry attempts on validation failure
        """
        logger.info("Worker started. Polling for tasks...")
        print(f"Worker running (poll_interval={poll_interval}s, bid_ratio={max_bid_ratio})")

        try:
            while True:
                try:
                    resp = self._http.get("/api/v1/tasks/available")
                    resp.raise_for_status()
                    tasks = resp.json()
                except Exception as e:
                    logger.error(f"Poll error: {e}")
                    time.sleep(poll_interval)
                    continue

                for task_data in tasks:
                    task_type = task_data["task_type"]
                    if task_type not in self._handlers:
                        continue

                    task_id = task_data["id"]
                    budget = task_data["max_budget_credits"]
                    bid_amount = max(1, int(budget * max_bid_ratio))

                    # Bid
                    bid_result = self._bid(task_id, bid_amount)
                    if bid_result is None:
                        continue

                    logger.info(f"Bid placed on {task_id[:8]}... ({bid_amount} credits)")

                    # Wait for award
                    if not self._wait_for_award(task_id):
                        logger.info(f"Not awarded: {task_id[:8]}...")
                        continue

                    logger.info(f"Awarded! Processing {task_id[:8]}...")

                    # Fetch full task data (available listing only has preview)
                    try:
                        full_resp = self._http.get(f"/api/v1/tasks/{task_id}")
                        if full_resp.status_code == 200:
                            task_data = full_resp.json()
                        else:
                            logger.warning(f"Failed to fetch full task data: {full_resp.status_code}")
                    except Exception as e:
                        logger.warning(f"Error fetching full task data: {e}")

                    # Process
                    ctx = TaskContext(task_data)
                    handler = self._handlers[task_type]

                    for attempt in range(max_retries + 1):
                        try:
                            result = handler(ctx)
                            submit_resp = self._submit(task_id, result)

                            if submit_resp.get("error_code") is None:
                                logger.info(f"PASS: {task_id[:8]}... settled!")
                                print(f"  Task {task_id[:8]}... SETTLED")
                                break
                            else:
                                logger.warning(
                                    f"FAIL: {submit_resp.get('error_code')} "
                                    f"(retries: {submit_resp.get('retries_left', 0)})"
                                )
                                if submit_resp.get("retries_left", 0) <= 0:
                                    break
                        except Exception as e:
                            logger.error(f"Handler error on attempt {attempt + 1}: {e}")
                            if attempt >= max_retries:
                                logger.error(f"Handler retries exhausted for {task_id[:8]}...")
                                break
                            # Retry after a brief pause for transient errors
                            time.sleep(1)

                time.sleep(poll_interval)

        except KeyboardInterrupt:
            print("\nWorker stopped.")
        finally:
            self.close()

    def run_sse(
        self,
        topics: Optional[list] = None,
        max_bid_ratio: float = 0.8,
        max_retries: int = 2,
    ):
        """
        Run worker in SSE event-driven mode instead of polling.

        Connects to GET /api/v1/events/stream and processes server-sent
        events. Falls back to polling on connection failure.

        Args:
            topics: Optional list of event topics to subscribe to.
            max_bid_ratio: Bid this fraction of max_budget_credits.
            max_retries: Max retry attempts on validation failure.
        """
        import json as _json

        params = {}
        if topics:
            params["topics"] = ",".join(topics)

        logger.info("Worker started in SSE mode.")
        print(f"Worker running (SSE mode, bid_ratio={max_bid_ratio})")

        try:
            with self._http.stream(
                "GET",
                "/api/v1/events/stream",
                params=params,
                timeout=None,
            ) as stream:
                buffer = ""
                for chunk in stream.iter_text():
                    buffer += chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue

                        raw = line[len("data:"):].strip()
                        if not raw:
                            continue

                        try:
                            event = _json.loads(raw)
                        except _json.JSONDecodeError:
                            logger.warning(f"Invalid SSE JSON: {raw[:120]}")
                            continue

                        event_type = event.get("type", "")
                        if event_type != "task.new":
                            continue

                        task_data = event.get("task", event.get("data", {}))
                        task_type = task_data.get("task_type", "")
                        if task_type not in self._handlers:
                            continue

                        task_id = task_data.get("id", "")
                        budget = task_data.get("max_budget_credits", 0)
                        bid_amount = max(1, int(budget * max_bid_ratio))

                        # Bid
                        bid_result = self._bid(task_id, bid_amount)
                        if bid_result is None:
                            continue

                        logger.info(f"Bid placed on {task_id[:8]}... ({bid_amount} credits)")

                        # Wait for award
                        if not self._wait_for_award(task_id):
                            logger.info(f"Not awarded: {task_id[:8]}...")
                            continue

                        logger.info(f"Awarded! Processing {task_id[:8]}...")

                        # Fetch full task data
                        try:
                            full_resp = self._http.get(f"/api/v1/tasks/{task_id}")
                            if full_resp.status_code == 200:
                                task_data = full_resp.json()
                            else:
                                logger.warning(f"Failed to fetch full task data: {full_resp.status_code}")
                        except Exception as e:
                            logger.warning(f"Error fetching full task data: {e}")

                        # Process
                        ctx = TaskContext(task_data)
                        handler = self._handlers[task_type]

                        for attempt in range(max_retries + 1):
                            try:
                                result = handler(ctx)
                                submit_resp = self._submit(task_id, result)

                                if submit_resp.get("error_code") is None:
                                    logger.info(f"PASS: {task_id[:8]}... settled!")
                                    print(f"  Task {task_id[:8]}... SETTLED")
                                    break
                                else:
                                    logger.warning(
                                        f"FAIL: {submit_resp.get('error_code')} "
                                        f"(retries: {submit_resp.get('retries_left', 0)})"
                                    )
                                    if submit_resp.get("retries_left", 0) <= 0:
                                        break
                            except Exception as e:
                                logger.error(f"Handler error on attempt {attempt + 1}: {e}")
                                if attempt >= max_retries:
                                    logger.error(f"Handler retries exhausted for {task_id[:8]}...")
                                    break
                                time.sleep(1)

        except KeyboardInterrupt:
            print("\nWorker stopped.")
        except (httpx.HTTPError, OSError) as e:
            logger.warning(f"SSE connection failed: {e}. Falling back to polling.")
            self.run(
                poll_interval=1.0,
                max_bid_ratio=max_bid_ratio,
                max_retries=max_retries,
            )
        finally:
            self.close()

    def balance(self) -> dict:
        resp = self._http.get("/api/v1/credits/balance")
        raise_for_status(resp)
        return resp.json()

    def reputation(self) -> dict:
        resp = self._http.get("/api/v1/account/reputation")
        raise_for_status(resp)
        return resp.json()

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
