"""Smoke tests for the identity security primitives (no database)."""

from datetime import timedelta
from uuid import uuid4

import pytest

from app.domains.identity.security import (
    create_access_token,
    generate_one_time_code,
    generate_refresh_token,
    hash_one_time_code,
    hash_password,
    hash_refresh_token,
    normalize_phone_number,
    read_access_token,
    verify_one_time_code,
    verify_password,
)
from app.libs.common.clock import utc_now


def test_password_hash_round_trip_and_wrong_password() -> None:
    """A password verifies against its own hash only, and hashes are salted."""
    stored_hash = hash_password("correct horse")

    assert stored_hash.startswith("scrypt$")
    assert "correct horse" not in stored_hash
    assert verify_password("correct horse", stored_hash)
    assert not verify_password("wrong horse", stored_hash)
    assert hash_password("correct horse") != stored_hash
    assert not verify_password("correct horse", "garbage")


def test_access_token_round_trip_expiry_and_tampering() -> None:
    """A signed token yields its session ID until it expires or is altered."""
    session_id = uuid4()
    token = create_access_token(session_id, utc_now() + timedelta(minutes=5))

    assert read_access_token(token) == session_id
    assert (
        read_access_token(
            create_access_token(session_id, utc_now() - timedelta(seconds=5))
        )
        is None
    )
    first, expiry, signature = token.split(".")
    assert read_access_token(f"{uuid4()}.{expiry}.{signature}") is None
    assert read_access_token(f"{first}.{int(expiry) + 1000}.{signature}") is None
    assert read_access_token("not-a-token") is None


def test_refresh_token_is_random_and_stored_hashed() -> None:
    """Refresh tokens differ each time and only their hash is kept."""
    first = generate_refresh_token()

    assert first != generate_refresh_token()
    assert hash_refresh_token(first) != first
    assert hash_refresh_token(first) == hash_refresh_token(first)


def test_one_time_code_is_six_digits_and_verified_by_hash() -> None:
    """A code is six digits; the hash depends on the row ID and the code."""
    one_time_code_id = uuid4()
    code = generate_one_time_code()
    code_hash = hash_one_time_code(one_time_code_id, code)

    assert len(code) == 6 and code.isdigit()
    assert verify_one_time_code(one_time_code_id, code, code_hash)
    assert not verify_one_time_code(
        one_time_code_id, "000000" if code != "000000" else "111111", code_hash
    )
    assert not verify_one_time_code(uuid4(), code, code_hash)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0901 234 567", "+84901234567"),
        ("+84 901-234-567", "+84901234567"),
        ("0084901234567", "+84901234567"),
        ("(090) 123.4567", "+84901234567"),
    ],
)
def test_phone_number_is_normalized_to_e164(raw: str, expected: str) -> None:
    """Vietnamese national and international spellings give one stored form."""
    assert normalize_phone_number(raw) == expected


def test_invalid_phone_number_is_rejected() -> None:
    """Letters and too-short numbers are refused."""
    with pytest.raises(ValueError):
        normalize_phone_number("abc")
    with pytest.raises(ValueError):
        normalize_phone_number("+123")
