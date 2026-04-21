"""
Shared utilities between NexusClient and NexusWorker.
"""

import logging

import httpx

from nexus_sdk._version import __version__ as SDK_VERSION
from nexus_sdk.exceptions import NexusAPIError, VersionError

logger = logging.getLogger(__name__)


def version_lt(a: str, b: str) -> bool:
    """Compare semver strings: return True if a < b."""
    def parse(v):
        return tuple(int(x) for x in v.split(".")[:3])
    return parse(a) < parse(b)


def check_server_version(http_client: httpx.Client) -> None:
    """Check SDK version compatibility with server."""
    try:
        resp = http_client.get("/api/v1/heartbeat")
        if resp.status_code == 200:
            data = resp.json()
            min_ver = data.get("min_sdk_version", "0.0.0")
            latest_ver = data.get("latest_sdk_version", SDK_VERSION)
            announcement = data.get("announcement", "")

            if version_lt(SDK_VERSION, min_ver):
                raise VersionError(
                    f"SDK version {SDK_VERSION} is too old. "
                    f"Minimum required: {min_ver}. "
                    f"Please upgrade: pip install --upgrade nexus-sdk"
                )
            if version_lt(SDK_VERSION, latest_ver):
                import warnings
                warnings.warn(
                    f"nexus-sdk {latest_ver} is available (you have {SDK_VERSION}). "
                    f"Upgrade: pip install --upgrade nexus-sdk",
                    stacklevel=3,
                )
            if announcement:
                logger.info(f"[Nexus] {announcement}")
    except VersionError:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        pass  # Don't block on network errors during init


def raise_for_status(resp: httpx.Response) -> None:
    """Raise NexusAPIError with server detail instead of raw httpx error."""
    if resp.is_success:
        return
    detail = resp.text
    try:
        body = resp.json()
        detail = body.get("detail", detail)
    except Exception:
        pass
    raise NexusAPIError(resp.status_code, detail, response=resp)
