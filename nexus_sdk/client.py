"""
Nexus SDK — Demand-side client (派单方).

Usage:
    from nexus_sdk import NexusClient

    client = NexusClient(api_key="your-api-key", base_url="http://localhost:8000")
    task = client.create_task(
        input_data="Extract: John is 30",
        schema={"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}, "required": ["name", "age"]},
        example_output={"name": "John", "age": 30},
        budget=50,
    )
    result = task.wait_for_result(timeout=30)
    print(result)
"""

import platform
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from nexus_sdk._compat import check_server_version, raise_for_status
from nexus_sdk._version import __version__ as SDK_VERSION
from nexus_sdk.exceptions import NexusAPIError, VersionError


@dataclass
class TaskResult:
    task_id: str
    status: str
    awarded_price: Optional[int] = None
    result_data: Optional[dict] = None
    error: Optional[str] = None


class TaskHandle:
    """Handle returned from create_task. Allows polling for results."""

    def __init__(self, client: "NexusClient", task_id: str, task_data: dict):
        self.client = client
        self.task_id = task_id
        self.task_data = task_data

    @property
    def status(self) -> str:
        return self.task_data.get("status", "UNKNOWN")

    def refresh(self) -> dict:
        """Refresh task status from server."""
        self.task_data = self.client._get(f"/api/v1/tasks/{self.task_id}")
        return self.task_data

    def wait_for_result(self, timeout: int = 60, poll_interval: float = 1.0) -> TaskResult:
        """
        Poll until the task reaches a terminal state.
        Returns TaskResult with final status.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.refresh()
            status = self.status
            if status in ("SETTLED", "EXPIRED", "CANCELLED"):
                return TaskResult(
                    task_id=self.task_id,
                    status=status,
                    awarded_price=self.task_data.get("awarded_price"),
                    result_data=self.task_data.get("result_data"),
                )
            time.sleep(poll_interval)

        return TaskResult(
            task_id=self.task_id,
            status="TIMEOUT",
            error=f"Task did not complete within {timeout}s",
        )

    def cancel(self) -> dict:
        """Cancel a task in PENDING_POOL or BIDDING state."""
        return self.client._delete(f"/api/v1/tasks/{self.task_id}")


class NexusClient:
    """Demand-side SDK client.

    The most common ways to construct one:

    * ``NexusClient(api_key=...)`` — explicit key, explicit base_url
    * ``NexusClient.from_env()`` — read ``NEXUS_API_KEY`` + ``NEXUS_BASE_URL``
      from env, or fall back to ``~/.nexus/credentials``
    * ``NexusClient.login_device_flow(base_url=...)`` — interactive CLI login
      using OAuth 2.0 Device Flow; persists credentials to disk on success
    """

    DEFAULT_BASE_URL = "https://api.nexustoken.ai"

    # ── Factory methods ──────────────────────────────────────────────

    @classmethod
    def from_env(
        cls,
        *,
        profile: str | None = None,
        skip_version_check: bool = False,
    ) -> "NexusClient":
        """Construct a client from env vars, falling back to the credentials file.

        Resolution order:
          1. ``NEXUS_API_KEY`` + optional ``NEXUS_BASE_URL`` env vars
          2. ``~/.nexus/credentials`` [profile] section (profile from arg, else
             ``NEXUS_PROFILE`` env var, else ``default``)

        Raises ``ValueError`` if neither is available.
        """
        import os
        api_key = os.getenv("NEXUS_API_KEY", "").strip()
        base_url = os.getenv("NEXUS_BASE_URL", "").strip() or cls.DEFAULT_BASE_URL

        if not api_key:
            # Lazy import to avoid pulling configparser when not needed
            from nexus_sdk.credentials import load_credentials
            creds = load_credentials(profile=profile)
            if creds:
                api_key = creds["api_key"]
                base_url = creds.get("base_url") or base_url

        if not api_key:
            raise ValueError(
                "No NexusToken credentials found.\n"
                "  - Set NEXUS_API_KEY env var, or\n"
                "  - Save a key with NexusClient.login_device_flow(), or\n"
                "  - Generate one at https://nexustoken.ai/dashboard/api-keys"
            )

        return cls(api_key=api_key, base_url=base_url, skip_version_check=skip_version_check)

    @classmethod
    def login_device_flow(
        cls,
        *,
        base_url: str | None = None,
        client_name: str | None = None,
        timeout: int = 600,
        poll_interval: float = 5.0,
        print_fn=None,
        save: bool = True,
        skip_version_check: bool = False,
    ) -> "NexusClient":
        """Run the OAuth 2.0 Device Flow to get an API key interactively.

        Prints a short user code and a verification URL, then polls until the
        user approves the request from a browser. On success, credentials are
        persisted to ``~/.nexus/credentials`` (unless ``save=False``) and a
        ready-to-use client is returned.

        Args:
            base_url: Nexus API base URL. Defaults to production.
            client_name: Display name shown on the approval screen, e.g.
                ``"my-agent"``. Helps the user understand what they're approving.
            timeout: Max seconds to wait for user approval.
            poll_interval: Seconds between polls (server may request slower).
            print_fn: Custom printer (defaults to stdout). Use ``lambda _: None``
                to suppress output in embedded contexts.
            save: If True, write the resulting key to ~/.nexus/credentials.
        """
        import time as _time
        out = print_fn or print
        api_base = (base_url or cls.DEFAULT_BASE_URL).rstrip("/")

        with httpx.Client(base_url=api_base, timeout=30) as client:
            resp = client.post(
                "/api/v1/oauth/device/authorize",
                json={"client_name": client_name or "nexus-sdk"},
            )
            raise_for_status(resp)
            start = resp.json()
            device_code = start["device_code"]
            user_code = start["user_code"]
            verification_url = start["verification_url"]
            verification_url_complete = start.get("verification_url_complete") or verification_url
            server_interval = float(start.get("interval", poll_interval))

            out("")
            out("┌─ Connect NexusToken ─────────────────────────────┐")
            out(f"│ 1) Open: {verification_url_complete:<40} │")
            out(f"│ 2) Enter code: {user_code:<34} │")
            out("│                                                  │")
            out("│ Waiting for approval in your browser…            │")
            out("└──────────────────────────────────────────────────┘")
            out("")

            deadline = _time.monotonic() + timeout
            interval = max(1.0, server_interval)
            while _time.monotonic() < deadline:
                _time.sleep(interval)
                try:
                    poll = client.post(
                        "/api/v1/oauth/device/token",
                        json={"device_code": device_code},
                    )
                except httpx.HTTPError:
                    continue
                if poll.status_code == 200:
                    data = poll.json()
                    api_key = data["api_key"]
                    if save:
                        from nexus_sdk.credentials import save_credentials
                        try:
                            path = save_credentials(
                                api_key,
                                base_url=api_base,
                                webhook_secret=data.get("webhook_secret"),
                                account_id=data.get("account_id"),
                            )
                            out(f"✓ Saved credentials to {path}")
                        except OSError as e:
                            out(f"! Could not save credentials: {e}")
                    out("✓ Connected to NexusToken")
                    return cls(api_key=api_key, base_url=api_base, skip_version_check=skip_version_check)
                if poll.status_code == 429:
                    # slow_down
                    interval = min(interval + 5, 30.0)
                    continue
                if poll.status_code == 410:
                    raise RuntimeError(
                        "Device flow expired or was denied. Please try again."
                    )
                if poll.status_code == 428:
                    # authorization_pending — keep polling at current interval
                    continue
                # Unknown error — continue polling but log
                out(f"! Unexpected response while polling: {poll.status_code}")

        raise TimeoutError(
            f"Device flow timed out after {timeout}s without approval."
        )

    # ── Constructor ──────────────────────────────────────────────────

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
        if not skip_version_check:
            check_server_version(self._http)

    def _get(self, path: str) -> dict:
        resp = self._http.get(path)
        raise_for_status(resp)
        return resp.json()

    def _post(self, path: str, json: dict) -> dict:
        resp = self._http.post(path, json=json)
        raise_for_status(resp)
        return resp.json()

    def _delete(self, path: str) -> dict:
        resp = self._http.delete(path)
        raise_for_status(resp)
        return resp.json()

    # --- Public API ---

    def balance(self) -> dict:
        """Get current credit balance."""
        return self._get("/api/v1/credits/balance")

    def topup(self, amount: int) -> dict:
        """Top up credits (V1: simulated, no real payment)."""
        return self._post("/api/v1/credits/topup", {"amount_credits": amount})

    def reputation(self) -> dict:
        """Get account reputation info."""
        return self._get("/api/v1/account/reputation")

    def create_task(
        self,
        input_data: str,
        schema: dict,
        example_output: dict,
        budget: int = 50,
        max_seconds: int = 120,
        rules: Optional[List[dict]] = None,
        task_type: str = "json_extraction",
        quality: str = "balanced",
        min_skill: int = 0,
        callback_url: Optional[str] = None,
        input_file_ids: Optional[List[str]] = None,
    ) -> TaskHandle:
        """
        Create a new task and return a TaskHandle for polling.

        Args:
            input_data: The text to extract from
            schema: JSON Schema for the expected output
            example_output: Example output that passes the schema
            budget: Maximum credits to spend (min 5)
            max_seconds: Maximum execution time (1-300)
            rules: Optional list of hard validation rules
            task_type: Task type — json_extraction / text_classification /
                       text_generation / data_transformation / code_execution
            quality: Matching strategy — "best", "balanced", or "cheapest"
            min_skill: Minimum skill tier to bid (0=any, 1-5)
            callback_url: Webhook URL — receives POST with result on SETTLED/EXPIRED/CANCELLED.
                          If set, no need to poll with wait_for_result().
            input_file_ids: Optional list of file UUIDs (from upload_file())
                            to attach as binary inputs. Up to 10 per task.
        """
        payload = {
            "task_type": task_type,
            "input_data": input_data,
            "validation_schema": schema,
            "validation_rules": rules or [],
            "example_output": example_output,
            "max_budget_credits": budget,
            "max_execution_seconds": max_seconds,
            "quality_preference": quality,
            "min_skill_rating": min_skill,
        }
        if callback_url:
            payload["callback_url"] = callback_url
        if input_file_ids:
            payload["input_file_ids"] = input_file_ids
        data = self._post("/api/v1/tasks", payload)
        return TaskHandle(self, data["id"], data)

    def create_physical_proof_task(
        self,
        brief: str,
        *,
        budget: int = 100,
        max_seconds: int = 1800,
        quality: str = "balanced",
        min_skill: int = 0,
        callback_url: Optional[str] = None,
        reference_file_ids: Optional[List[str]] = None,
    ) -> TaskHandle:
        """Create a physical-world task where the worker must submit photo/video
        evidence of a real-world action.

        ``brief`` describes the action (e.g. "Photograph the Eiffel Tower at
        sunset, facing east"). The worker submits a result containing a
        ``description`` and at least one ``{type: file_ref, file_id: ...}``
        marker pointing to proof they uploaded. Quality ("did they actually
        do it?") is enforced by the existing dispute flow, not schema match.

        ``reference_file_ids`` optionally lets you attach a reference photo or
        map — the worker can download them for context (same mechanism as
        every other multimodal task).
        """
        payload: Dict[str, Any] = {
            "task_type": "physical_proof",
            "input_data": brief,
            "max_budget_credits": budget,
            "max_execution_seconds": max_seconds,
            "quality_preference": quality,
            "min_skill_rating": min_skill,
        }
        if callback_url:
            payload["callback_url"] = callback_url
        if reference_file_ids:
            payload["input_file_ids"] = list(reference_file_ids)
        data = self._post("/api/v1/tasks", payload)
        return TaskHandle(self, data["id"], data)

    # --- Files ---

    def upload_file(
        self,
        path: str,
        *,
        content_type: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> str:
        """Upload a file and return its ``file_id`` for use in create_task().

        Args:
            path: Path to a local file.
            content_type: Optional MIME type (auto-detected if omitted).
            filename: Optional display name (defaults to the basename).

        Returns: the uploaded file's UUID (``file_id``).
        """
        import mimetypes
        import os

        ctype = content_type or mimetypes.guess_type(path)[0] or "application/octet-stream"
        name = filename or os.path.basename(path)
        with open(path, "rb") as fh:
            resp = self._http.post(
                "/api/v1/files/upload",
                files={"file": (name, fh, ctype)},
            )
        raise_for_status(resp)
        return resp.json()["file_id"]

    def download_task_file(self, task_id: str, file_id: str) -> bytes:
        """Download a task-attached file's bytes.

        Works for any file the current account can see in the task scope —
        input attachments (if you're the creator or the awarded worker) or
        output artifacts (referenced via {type: 'file_ref', ...} markers in
        submissions on this task).
        """
        resp = self._http.get(f"/api/v1/tasks/{task_id}/files/{file_id}/download")
        raise_for_status(resp)
        return resp.content

    def list_tasks(self) -> List[dict]:
        """List available tasks (useful for debugging)."""
        return self._get("/api/v1/tasks/available")

    # --- Chains ---

    def create_chain(
        self,
        steps: List[dict],
        *,
        callback_url: Optional[str] = None,
    ) -> dict:
        """
        Create a linear task chain.

        Args:
            steps: List of step dicts, each with task_type, input_data,
                   max_budget_credits, and other task fields.
            callback_url: Optional webhook URL for chain completion.
        """
        payload: Dict[str, Any] = {"steps": steps}
        if callback_url:
            payload["callback_url"] = callback_url
        return self._post("/api/v1/chains", payload)

    def get_chain(self, chain_id: str) -> dict:
        """Get chain status and task IDs."""
        return self._get(f"/api/v1/chains/{chain_id}")

    def cancel_chain(self, chain_id: str) -> dict:
        """Cancel a running chain."""
        return self._delete(f"/api/v1/chains/{chain_id}")

    # --- Batches ---

    def create_batch(
        self,
        template: dict,
        items: List[dict],
        *,
        max_concurrent: int = 5,
    ) -> dict:
        """
        Create a batch of tasks from a template.

        Args:
            template: Task template with shared fields (schema, rules, etc.).
            items: List of per-item overrides (e.g. different input_data).
            max_concurrent: Maximum tasks to run in parallel.
        """
        payload = {
            "template": template,
            "items": items,
            "max_concurrent": max_concurrent,
        }
        return self._post("/api/v1/tasks/batch", payload)

    def get_batch(self, batch_id: str) -> dict:
        """Get batch status and progress."""
        return self._get(f"/api/v1/batches/{batch_id}")

    # --- Templates ---

    def list_templates(self) -> List[dict]:
        """List all available task templates."""
        return self._get("/api/v1/templates")

    def get_template(self, template_id: str) -> dict:
        """Get template details."""
        return self._get(f"/api/v1/templates/{template_id}")

    def use_template(
        self,
        template_id: str,
        input_data: str,
        *,
        overrides: Optional[dict] = None,
    ) -> dict:
        """
        Create a task from a template.

        Args:
            template_id: ID of the template to use.
            input_data: The text to process.
            overrides: Optional dict of fields to override from the template.
        """
        payload: Dict[str, Any] = {"input_data": input_data}
        if overrides:
            payload["overrides"] = overrides
        return self._post(f"/api/v1/templates/{template_id}/use", payload)

    # --- Capability marketplace (V2) ---

    def list_capability_specs(
        self,
        *,
        category: Optional[str] = None,
        input_mode: Optional[str] = None,
        output_mode: Optional[str] = None,
        max_price: Optional[int] = None,
        tag: Optional[str] = None,
        page: int = 1,
        per_page: int = 50,
    ) -> list[dict]:
        """Browse the V2 capability marketplace.

        All filters are optional and combine with AND semantics. Returns
        a list of spec dicts; use the ``id`` field as ``capability_spec_id``
        when (future) Phase C creates jobs against a specific provider.
        """
        params: dict = {"page": page, "per_page": per_page}
        if category is not None:
            params["category"] = category
        if input_mode is not None:
            params["input_mode"] = input_mode
        if output_mode is not None:
            params["output_mode"] = output_mode
        if max_price is not None:
            params["max_price"] = max_price
        if tag is not None:
            params["tag"] = tag
        resp = self._http.get("/api/v2/capability-specs", params=params)
        raise_for_status(resp)
        return resp.json()["data"]

    # --- Public Artifacts (Phase 1 skill marketplace) ---

    def list_public_artifacts(
        self,
        *,
        task_type: Optional[str] = None,
        tag: Optional[str] = None,
        min_price: Optional[int] = None,
        max_price: Optional[int] = None,
        page: int = 1,
        per_page: int = 50,
    ) -> List[dict]:
        """Browse the public artifact marketplace.

        Cached outputs other workers have published. You consume one
        implicitly by calling ``create_task()`` with matching content — if the
        platform finds a cache hit, the task is settled instantly at 70% of
        the original price and you receive the cached result.
        """
        params: Dict[str, Any] = {"page": page, "per_page": per_page}
        if task_type is not None:
            params["task_type"] = task_type
        if tag is not None:
            params["tag"] = tag
        if min_price is not None:
            params["min_price"] = min_price
        if max_price is not None:
            params["max_price"] = max_price
        resp = self._http.get("/api/v1/public-artifacts", params=params)
        raise_for_status(resp)
        return resp.json()

    def get_public_artifact(self, artifact_id: str) -> dict:
        """Metadata for one artifact. ``result_data`` is None unless you authored it."""
        return self._get(f"/api/v1/public-artifacts/{artifact_id}")

    def my_public_artifacts(
        self, *, include_inactive: bool = True, page: int = 1, per_page: int = 50,
    ) -> List[dict]:
        """Artifacts you have published (active + retracted)."""
        params = {
            "include_inactive": str(bool(include_inactive)).lower(),
            "page": page,
            "per_page": per_page,
        }
        resp = self._http.get("/api/v1/public-artifacts/me/list", params=params)
        raise_for_status(resp)
        return resp.json()

    def my_artifact_earnings(self) -> dict:
        """Royalty summary — totals + top artifacts by cache-hit volume."""
        return self._get("/api/v1/public-artifacts/me/earnings")

    def retract_public_artifact(self, artifact_id: str) -> dict:
        """Deactivate one of your artifacts — future cache lookups skip it."""
        resp = self._http.post(f"/api/v1/public-artifacts/{artifact_id}/retract")
        raise_for_status(resp)
        return resp.json()

    # --- Promo ---

    def redeem_promo(self, code: str) -> dict:
        """Redeem a promotional code for credits."""
        return self._post("/api/v1/promo/redeem", {"code": code})

    def close(self):
        """Close the HTTP client."""
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
