"""
Nexus SDK exceptions — domain-specific errors that wrap httpx internals.
"""


class NexusError(Exception):
    """Base exception for all Nexus SDK errors."""
    pass


class NexusAPIError(NexusError):
    """Raised when the Nexus API returns an error response."""

    def __init__(self, status_code: int, detail: str, response=None):
        self.status_code = status_code
        self.detail = detail
        self.response = response
        super().__init__(f"API error {status_code}: {detail}")


class VersionError(NexusError):
    """Raised when the SDK version is too old for the server."""
    pass
