"""Exceptions raised by the Driivz client."""

from __future__ import annotations


class DriivzError(Exception):
    """Base class for all client errors."""


class DriivzConnectionError(DriivzError):
    """Network failure or non-JSON/unexpected response."""


class AuthError(DriivzError):
    """Login failed or the session is no longer authenticated."""

    def __init__(self, error_type: str = "AUTH_FAILED") -> None:
        super().__init__(error_type)
        self.error_type = error_type


class RateLimitError(DriivzError):
    """The portal rejected the request because of rate limiting."""


class ApiError(DriivzError):
    """The portal answered with success=false or rejected an operation."""

    def __init__(self, error_type: str, message_key: str | None = None) -> None:
        super().__init__(f"{error_type}: {message_key}" if message_key else error_type)
        self.error_type = error_type
        self.message_key = message_key
