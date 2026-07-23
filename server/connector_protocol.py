# This file was modified in 2026 by YiQiao contributors. See NOTICE.

"""Public Service Connector Protocol 1.0 constants and credential helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from collections.abc import Iterable

PROTOCOL_VERSION = "1.0"
SERVICE_ID = "yiqiao"
AUDIENCE = "yiqiao:memory-api"
MEMORY_READ_SCOPE = "memory:read"
MEMORY_WRITE_SCOPE = "memory:write"
SUPPORTED_SCOPES = (MEMORY_READ_SCOPE, MEMORY_WRITE_SCOPE)
SCOPES = SUPPORTED_SCOPES

ACCESS_TOKEN_TTL_SECONDS = 15 * 60
DEVICE_CODE_TTL_SECONDS = 10 * 60
DEVICE_AUTHORIZATION_TTL_SECONDS = DEVICE_CODE_TTL_SECONDS
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60

ACCESS_TOKEN_PREFIX = "yqoa_"
REFRESH_TOKEN_PREFIX = "yqor_"
DEVICE_CODE_PREFIX = "yqod_"

_PKCE_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_USER_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_opaque_token(prefix: str, *, entropy_bytes: int = 32) -> str:
    """Return a high-entropy opaque value suitable for one-time disclosure."""

    if not prefix or not prefix.endswith("_"):
        raise ValueError("opaque token prefixes must be non-empty and end with '_'")
    if entropy_bytes < 32:
        raise ValueError("opaque tokens require at least 256 bits of entropy")
    return f"{prefix}{secrets.token_urlsafe(entropy_bytes)}"


def hash_opaque_value(value: str) -> str:
    """Hash a high-entropy credential for persistent lookup."""

    if not value:
        raise ValueError("cannot hash an empty credential")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def credential_prefix(value: str, *, length: int = 12) -> str:
    """Return a display-safe prefix that is never sufficient to authenticate."""

    if length < 1:
        raise ValueError("credential prefix length must be positive")
    return value[:length]


def opaque_value_matches(value: str, expected_hash: str) -> bool:
    """Compare a presented opaque value with a stored SHA-256 digest."""

    if not value or not expected_hash:
        return False
    return hmac.compare_digest(hash_opaque_value(value), expected_hash)


def generate_user_code(*, groups: int = 2, group_length: int = 4) -> str:
    """Generate a human-readable code without ambiguous characters."""

    if groups < 1 or group_length < 1:
        raise ValueError("user-code dimensions must be positive")
    return "-".join("".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(group_length)) for _ in range(groups))


def normalize_user_code(value: str) -> str:
    """Canonicalize a user-entered code before its keyed lookup."""

    normalized = "".join(character for character in value.upper() if character != "-" and not character.isspace())
    if any(character not in _USER_CODE_ALPHABET for character in normalized):
        return ""
    return normalized


def hash_user_code(value: str, secret: str | bytes) -> str:
    """HMAC a low-entropy user code so an offline table cannot enumerate it."""

    normalized = normalize_user_code(value)
    if not normalized:
        raise ValueError("cannot hash an empty user code")
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    if not key:
        raise ValueError("a dedicated user-code HMAC secret is required")
    return hmac.new(key, normalized.encode("ascii"), hashlib.sha256).hexdigest()


def user_code_matches(value: str, expected_hash: str, secret: str | bytes) -> bool:
    """Compare a user-entered code with its stored HMAC digest."""

    if not value or not expected_hash:
        return False
    if not normalize_user_code(value):
        return False
    return hmac.compare_digest(hash_user_code(value, secret), expected_hash)


def is_valid_pkce_verifier(verifier: str) -> bool:
    return bool(_PKCE_VERIFIER_RE.fullmatch(verifier))


def pkce_s256(verifier: str) -> str:
    """Return the unpadded base64url RFC 7636 S256 challenge."""

    if not is_valid_pkce_verifier(verifier):
        raise ValueError("PKCE verifier must be 43-128 RFC 7636 unreserved characters")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def normalize_scopes(scopes: str | Iterable[str]) -> tuple[str, ...]:
    """Canonicalize and validate a requested scope set."""

    values = scopes.split() if isinstance(scopes, str) else list(scopes)
    normalized = tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
    unsupported = sorted(set(normalized) - set(SUPPORTED_SCOPES))
    if unsupported:
        raise ValueError(f"unsupported scopes: {', '.join(unsupported)}")
    return tuple(scope for scope in SUPPORTED_SCOPES if scope in normalized)
