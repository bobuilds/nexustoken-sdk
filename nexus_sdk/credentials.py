"""Credential storage for the NexusToken SDK.

Follows the pattern of ``aws-cli`` and ``gh``: a plain INI file at
``~/.nexus/credentials`` with sections per profile. Default profile is
``default``; advanced users can set ``NEXUS_PROFILE`` to switch.

The file is chmod 600 on POSIX to reduce accidental key leakage.
"""
from __future__ import annotations

import configparser
import os
import stat
from pathlib import Path
from typing import Optional

CREDENTIALS_DIR = Path.home() / ".nexus"
CREDENTIALS_PATH = CREDENTIALS_DIR / "credentials"

DEFAULT_PROFILE = "default"


def _profile_name() -> str:
    return os.getenv("NEXUS_PROFILE") or DEFAULT_PROFILE


def save_credentials(
    api_key: str,
    *,
    base_url: Optional[str] = None,
    webhook_secret: Optional[str] = None,
    account_id: Optional[str] = None,
    profile: Optional[str] = None,
    path: Optional[Path] = None,
) -> Path:
    """Write credentials to ``~/.nexus/credentials`` (section=profile).

    Existing sections for other profiles are preserved.

    Returns the path written to.
    """
    target = Path(path) if path else CREDENTIALS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    cp = configparser.RawConfigParser()
    if target.exists():
        cp.read(target)

    section = profile or _profile_name()
    if not cp.has_section(section):
        cp.add_section(section)
    cp.set(section, "api_key", api_key)
    if base_url:
        cp.set(section, "base_url", base_url)
    if webhook_secret:
        cp.set(section, "webhook_secret", webhook_secret)
    if account_id:
        cp.set(section, "account_id", account_id)

    with open(target, "w") as f:
        cp.write(f)

    # chmod 600 on POSIX; no-op on Windows
    if os.name == "posix":
        try:
            os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    return target


def load_credentials(
    *,
    profile: Optional[str] = None,
    path: Optional[Path] = None,
) -> Optional[dict]:
    """Return the credentials section as a dict, or None if not found.

    Keys: ``api_key`` (always present if section exists),
    ``base_url``, ``webhook_secret``, ``account_id`` (optional).
    """
    target = Path(path) if path else CREDENTIALS_PATH
    if not target.exists():
        return None

    cp = configparser.RawConfigParser()
    cp.read(target)

    section = profile or _profile_name()
    if not cp.has_section(section):
        return None
    data = dict(cp.items(section))
    if not data.get("api_key"):
        return None
    return data


def clear_credentials(
    *,
    profile: Optional[str] = None,
    path: Optional[Path] = None,
) -> bool:
    """Remove the section for a profile. Returns True if something was removed."""
    target = Path(path) if path else CREDENTIALS_PATH
    if not target.exists():
        return False
    cp = configparser.RawConfigParser()
    cp.read(target)
    section = profile or _profile_name()
    if not cp.has_section(section):
        return False
    cp.remove_section(section)
    with open(target, "w") as f:
        cp.write(f)
    return True
