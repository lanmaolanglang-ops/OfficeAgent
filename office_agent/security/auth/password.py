"""Password hashing, verification, and legacy-upgrade helpers.

The stored format is self-describing::

    pbkdf2_sha256$iterations$salt_b64$digest_b64

New passwords use the canonical work factor below. Hashes emitted by the
previous OfficeAgent implementation (100,000 iterations, 32-byte salt and
digest) remain verifiable and are upgraded only after a correct password is
presented.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import logging
import secrets
from typing import Tuple

from cryptography.exceptions import InvalidKey
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)


PASSWORD_HASH_SCHEME = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 600_000
LEGACY_PASSWORD_HASH_ITERATIONS = 100_000
MAX_PASSWORD_LENGTH = 1024
_SALT_BYTES = 32
_HASH_BYTES = 32
_ACCEPTED_ITERATIONS = frozenset({
    LEGACY_PASSWORD_HASH_ITERATIONS,
    PASSWORD_HASH_ITERATIONS,
})


class _InvalidStoredHash(ValueError):
    """The database value is not an issued OfficeAgent password hash."""


@dataclass(frozen=True)
class PasswordVerification:
    """Result of one verification, with an optional canonical replacement."""

    valid: bool
    replacement_hash: str | None = None


@dataclass(frozen=True)
class _ParsedHash:
    iterations: int
    salt: bytes
    digest: bytes


def generate_salt(length: int = _SALT_BYTES) -> bytes:
    """Generate a cryptographically random salt."""
    if isinstance(length, bool) or not isinstance(length, int) or length < 1:
        raise ValueError("salt length must be a positive integer")
    return secrets.token_bytes(length)


def _password_bytes(password: str, *, enforce_new_limit: bool) -> bytes:
    if not isinstance(password, str):
        raise TypeError("password must be a string")
    if enforce_new_limit and len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"password must not exceed {MAX_PASSWORD_LENGTH} characters")
    # Passwords are intentionally not stripped, normalized, or case-folded.
    return password.encode("utf-8")


def _kdf(salt: bytes, iterations: int) -> PBKDF2HMAC:
    return PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=_HASH_BYTES,
        salt=salt,
        iterations=iterations,
    )


def _encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise _InvalidStoredHash("invalid base64") from exc


def _parse_password_hash(password_hash: str) -> _ParsedHash:
    if not isinstance(password_hash, str):
        raise _InvalidStoredHash("hash is not text")
    parts = password_hash.split("$")
    if len(parts) != 4:
        raise _InvalidStoredHash("invalid field count")
    scheme, iterations_text, salt_text, digest_text = parts
    if scheme != PASSWORD_HASH_SCHEME:
        raise _InvalidStoredHash("unknown scheme")
    try:
        iterations = int(iterations_text)
    except ValueError as exc:
        raise _InvalidStoredHash("invalid work factor") from exc
    if iterations_text != str(iterations) or iterations not in _ACCEPTED_ITERATIONS:
        raise _InvalidStoredHash("unissued work factor")
    salt = _decode(salt_text)
    digest = _decode(digest_text)
    if len(salt) != _SALT_BYTES or len(digest) != _HASH_BYTES:
        raise _InvalidStoredHash("invalid component length")
    return _ParsedHash(iterations=iterations, salt=salt, digest=digest)


def hash_password(password: str) -> str:
    """Hash a new password with the canonical OfficeAgent strategy."""
    password_bytes = _password_bytes(password, enforce_new_limit=True)
    salt = generate_salt()
    digest = _kdf(salt, PASSWORD_HASH_ITERATIONS).derive(password_bytes)
    return (
        f"{PASSWORD_HASH_SCHEME}${PASSWORD_HASH_ITERATIONS}$"
        f"{_encode(salt)}${_encode(digest)}"
    )


def _verify(password: str, password_hash: str) -> tuple[bool, _ParsedHash | None]:
    if not isinstance(password, str) or not isinstance(password_hash, str):
        return False, None
    try:
        parsed = _parse_password_hash(password_hash)
    except _InvalidStoredHash as exc:
        # The reason is safe metadata; never include password/hash values.
        logger.warning("拒绝无效的存储密码哈希: %s", exc)
        return False, None
    password_bytes = _password_bytes(password, enforce_new_limit=False)
    try:
        _kdf(parsed.salt, parsed.iterations).verify(password_bytes, parsed.digest)
    except InvalidKey:
        return False, parsed
    return True, parsed


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a canonical or known legacy hash; malformed values fail closed."""
    valid, _parsed = _verify(password, password_hash)
    return valid


def needs_rehash(password_hash: str) -> bool:
    """Return whether a valid issued hash uses the legacy work factor.

    Malformed and unknown hashes return ``False``: they must first fail
    verification and must never be replaced based on untrusted metadata alone.
    """
    try:
        parsed = _parse_password_hash(password_hash)
    except _InvalidStoredHash:
        return False
    return parsed.iterations != PASSWORD_HASH_ITERATIONS


def verify_password_and_rehash(password: str, password_hash: str) -> PasswordVerification:
    """Verify once and prepare a canonical replacement after legacy success.

    Passwords accepted by the historical helper beyond today's creation limit
    remain verifiable. They are not silently changed or locked out; callers
    should require a policy-compliant password on the next explicit change.
    """
    valid, parsed = _verify(password, password_hash)
    if not valid or parsed is None:
        return PasswordVerification(valid=False)
    if parsed.iterations == PASSWORD_HASH_ITERATIONS:
        return PasswordVerification(valid=True)
    try:
        replacement = hash_password(password)
    except ValueError:
        logger.warning("旧密码哈希已验证，但密码超出当前新建策略上限，暂不升级")
        return PasswordVerification(valid=True)
    return PasswordVerification(valid=True, replacement_hash=replacement)


def generate_password(length: int = 16) -> str:
    """Generate a random password."""
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def is_password_strong(password: str) -> Tuple[bool, list[str]]:
    """Check the existing password policy and its resource-safety ceiling."""
    issues = []
    if len(password) < 8:
        issues.append("密码长度至少8位")
    if len(password) > MAX_PASSWORD_LENGTH:
        issues.append(f"密码长度不能超过{MAX_PASSWORD_LENGTH}位")
    if not any(c.isupper() for c in password):
        issues.append("需要包含大写字母")
    if not any(c.islower() for c in password):
        issues.append("需要包含小写字母")
    if not any(c.isdigit() for c in password):
        issues.append("需要包含数字")
    if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password):
        issues.append("建议包含特殊字符")
    return (len(issues) == 0, issues)
