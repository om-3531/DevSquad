"""Fail-closed bearer authentication for proposal review endpoints.

This is a local deployment foundation, not a replacement for a managed identity
provider. Tokens are provisioned out of band through environment configuration.
"""

from __future__ import annotations

import hmac
import json
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime


PROPOSAL_READER = "proposal_reviewer"
PROPOSAL_APPROVER = "proposal_approver"


class AuthenticationConfigurationError(RuntimeError):
    pass


class AuthenticationError(PermissionError):
    pass


class AuthorizationError(PermissionError):
    pass


@dataclass(frozen=True)
class AuthenticatedIdentity:
    subject: str
    roles: frozenset[str]
    expires_at: datetime | None = None


class BearerTokenAuthenticator:
    """Validate explicitly configured bearer tokens without logging them."""

    def __init__(self, tokens: dict[str, AuthenticatedIdentity] | None):
        self._tokens = tokens or {}

    @classmethod
    def from_json(cls, raw_value: str | None) -> "BearerTokenAuthenticator":
        if not raw_value:
            return cls({})
        try:
            configured = json.loads(raw_value)
            tokens = {
                token: AuthenticatedIdentity(
                    subject=details["subject"],
                    roles=frozenset(details["roles"]),
                    expires_at=datetime.fromisoformat(details["expires_at"]) if details.get("expires_at") else None,
                )
                for token, details in configured.items()
            }
        except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AuthenticationConfigurationError("Approval authentication is not configured correctly.") from error
        if not tokens or any(not isinstance(token, str) or not token or not identity.subject or not identity.roles for token, identity in tokens.items()):
            raise AuthenticationConfigurationError("Approval authentication is not configured correctly.")
        return cls(tokens)

    def authenticate(self, authorization: str | None) -> AuthenticatedIdentity:
        if not self._tokens:
            raise AuthenticationConfigurationError("Approval authentication is not configured.")
        scheme, _, candidate = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not candidate:
            raise AuthenticationError("Authentication credentials were not provided.")
        for token, identity in self._tokens.items():
            if hmac.compare_digest(token, candidate):
                expiry = identity.expires_at.replace(tzinfo=UTC) if identity.expires_at and identity.expires_at.tzinfo is None else identity.expires_at
                if expiry and expiry <= datetime.now(UTC):
                    raise AuthenticationError("Authentication credentials are invalid or expired.")
                return identity
        raise AuthenticationError("Authentication credentials are invalid or expired.")

    def require_role(self, identity: AuthenticatedIdentity, role: str) -> AuthenticatedIdentity:
        if role not in identity.roles:
            raise AuthorizationError("You are not authorized to perform this action.")
        return identity


class ApprovalRateLimiter:
    """Small in-process limiter for authenticated approval attempts.

    Deployment behind multiple workers requires a shared limiter at the edge or
    in a shared store; this guard intentionally makes no distributed claim.
    """

    def __init__(self, maximum_attempts: int = 5, window_seconds: int = 60):
        self.maximum_attempts = maximum_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, subject: str) -> int | None:
        now = time.monotonic()
        with self._lock:
            attempts = self._attempts[subject]
            while attempts and attempts[0] <= now - self.window_seconds:
                attempts.popleft()
            if len(attempts) >= self.maximum_attempts:
                return max(1, int(self.window_seconds - (now - attempts[0])))
            attempts.append(now)
        return None

    def reset(self) -> None:
        """Reset limiter state, primarily for testing."""
        with self._lock:
            self._attempts.clear()

