"""Password hashing, token and one-time-code primitives of the identity domain.

Everything here is pure standard library (`hashlib.scrypt`, `hmac`,
`secrets`): no third-party dependency was approved for this. The functions
are synchronous and CPU-bound where noted; services run the slow ones with
`asyncio.to_thread`.

Limitations: access tokens are signed ``<session_id>.<expiry>.<signature>``
strings (not JWT) that the server validates against the session row, so a
logout takes effect at once. The signing key comes from
`IDENTITY_TOKEN_SECRET_KEY`; when it is empty a random key is drawn per
process, which is acceptable only for development.
"""

import base64
import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime
from functools import lru_cache
from uuid import UUID

from app.libs.common.clock import utc_now
from app.libs.common.config import settings

logger = logging.getLogger(__name__)

# scrypt cost: N=2**14, r=8, p=1 needs about 16 MiB and ~50 ms, inside
# hashlib's default memory limit.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_SALT_BYTES = 16
_SCRYPT_KEY_BYTES = 32

# Passwords stored by this module start with this tag, so the format can change.
PASSWORD_HASH_SCHEME = "scrypt"

_E164_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")
_PHONE_SEPARATORS = re.compile(r"[\s().\-]")


def _b64encode(raw: bytes) -> str:
    """Encode bytes as unpadded URL-safe base64 text."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    """Decode unpadded URL-safe base64 text.

    Raises:
        ValueError: If the text is not valid base64.
    """
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


@lru_cache
def _master_key() -> bytes:
    """Return the process-wide secret key behind tokens and code hashes.

    Returns:
        The configured secret as bytes, or a random key when none is
        configured (a warning is logged once).
    """
    if settings.IDENTITY_TOKEN_SECRET_KEY:
        return settings.IDENTITY_TOKEN_SECRET_KEY.encode("utf-8")
    logger.warning(
        "IDENTITY_TOKEN_SECRET_KEY is empty: using a random key for this "
        "process; every restart invalidates all access tokens"
    )
    return secrets.token_bytes(32)


def _derived_key(purpose: str) -> bytes:
    """Derive a key for one purpose so a signature of one kind never verifies as another.

    Args:
        purpose: Short label such as ``access-token`` or ``one-time-code``.

    Returns:
        32 bytes derived from the master key and the label.
    """
    return hmac.new(_master_key(), purpose.encode("ascii"), hashlib.sha256).digest()


def normalize_phone_number(raw_phone_number: str) -> str:
    """Normalize a phone number to E.164, the login ID (ID-08).

    Spaces, dots, dashes and brackets are dropped; a Vietnamese national
    number starting with ``0`` becomes ``+84...``; ``00`` becomes ``+``.

    Args:
        raw_phone_number: The number as typed.

    Returns:
        The number in E.164 form such as ``+84901234567``.

    Raises:
        ValueError: If the result is not a plausible E.164 number.
    """
    cleaned = _PHONE_SEPARATORS.sub("", raw_phone_number.strip())
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    elif cleaned.startswith("0"):
        cleaned = "+84" + cleaned[1:]
    if not _E164_PATTERN.match(cleaned):
        raise ValueError("phone number must be in international format")
    return cleaned


def hash_password(password: str) -> str:
    """Hash a password with scrypt and a fresh random salt (ID-17).

    CPU- and memory-heavy: call it through `asyncio.to_thread` in async code.

    Args:
        password: The plain password.

    Returns:
        ``scrypt$<n>$<r>$<p>$<salt>$<hash>`` text for `user_credentials`.
    """
    salt = secrets.token_bytes(_SCRYPT_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_KEY_BYTES,
    )
    return (
        f"{PASSWORD_HASH_SCHEME}${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}$"
        f"{_b64encode(salt)}${_b64encode(digest)}"
    )


def verify_password(password: str, stored_hash: str) -> bool:
    """Check a password against a stored hash in constant time.

    CPU- and memory-heavy: call it through `asyncio.to_thread` in async code.

    Args:
        password: The password typed by the person.
        stored_hash: A value produced by `hash_password`.

    Returns:
        `True` if the password matches; `False` for a mismatch or a hash in an
        unknown format.
    """
    try:
        scheme, n, r, p, salt_text, hash_text = stored_hash.split("$")
        if scheme != PASSWORD_HASH_SCHEME:
            return False
        expected = _b64decode(hash_text)
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_b64decode(salt_text),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except ValueError:
        return False
    return hmac.compare_digest(candidate, expected)


# Verified against when the phone number is unknown, so a wrong phone number
# costs the same time as a wrong password (no account enumeration by timing).
DUMMY_PASSWORD_HASH = hash_password("not-a-real-password")


def generate_refresh_token() -> str:
    """Create an unguessable refresh token.

    Returns:
        A URL-safe random string of about 64 characters.
    """
    return secrets.token_urlsafe(48)


def hash_refresh_token(refresh_token: str) -> str:
    """Hash a refresh token for storage (the table never holds the token).

    A fast hash is enough because the token is 384 bits of randomness.

    Args:
        refresh_token: The token as handed to the client.

    Returns:
        Hex SHA-256 of the token.
    """
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


def create_access_token(session_id: UUID, expires_at: datetime) -> str:
    """Sign a short-lived access token for a login session.

    Args:
        session_id: The `user_sessions` row the token belongs to.
        expires_at: When the token stops working.

    Returns:
        ``<session_id>.<unix expiry>.<signature>``.
    """
    payload = f"{session_id}.{int(expires_at.timestamp())}"
    signature = hmac.new(
        _derived_key("access-token"), payload.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{payload}.{_b64encode(signature)}"


def read_access_token(access_token: str) -> UUID | None:
    """Verify an access token's signature and expiry.

    Args:
        access_token: The bearer token of a request.

    Returns:
        The session ID it names, or `None` if the token is malformed, forged
        or expired. Whether the session still exists is the caller's check.
    """
    try:
        session_text, expiry_text, signature_text = access_token.split(".")
        payload = f"{session_text}.{expiry_text}"
        expected = hmac.new(
            _derived_key("access-token"), payload.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(_b64decode(signature_text), expected):
            return None
        if int(expiry_text) <= int(utc_now().timestamp()):
            return None
        return UUID(session_text)
    except (ValueError, UnicodeEncodeError):
        return None


def generate_one_time_code() -> str:
    """Create a random 6-digit SMS code (ID-37).

    Returns:
        Six decimal digits, leading zeros kept.
    """
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_one_time_code(one_time_code_id: UUID, code: str) -> str:
    """Hash a one-time code, keyed by the server secret and the row ID.

    Args:
        one_time_code_id: ID of the `one_time_codes` row, so equal codes in
            two rows hash differently.
        code: The code as sent by SMS.

    Returns:
        Hex HMAC-SHA256 of the code.
    """
    return hmac.new(
        _derived_key("one-time-code"),
        f"{one_time_code_id}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


def verify_one_time_code(one_time_code_id: UUID, code: str, code_hash: str) -> bool:
    """Check a typed code against the stored hash in constant time.

    Args:
        one_time_code_id: ID of the `one_time_codes` row.
        code: The code typed by the person.
        code_hash: The stored value from `hash_one_time_code`.

    Returns:
        `True` if the code matches.
    """
    return hmac.compare_digest(hash_one_time_code(one_time_code_id, code), code_hash)
