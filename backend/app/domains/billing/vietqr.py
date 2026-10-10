"""VietQR payload encoder (EMVCo merchant-presented QR, NAPAS profile) in pure Python.

A VietQR code is a string of ``tag(2) length(2) value`` fields, ending in a
CRC-16 over everything before it. The app draws the QR image from the string;
this module only builds (and, for tests and tools, reads) the string. The
fields used for a bank transfer to an account (BL-15):

======  =======================================================================
Tag     Content
======  =======================================================================
00      Payload format indicator, ``01``
01      Point of initiation: ``11`` static, ``12`` dynamic (one use, with amount)
38      Merchant account information (NAPAS): ``00`` GUI ``A000000727``,
        ``01`` beneficiary (``00`` bank BIN, ``01`` account number),
        ``02`` service code ``QRIBFTTA`` (transfer to an account)
53      Currency, ``704`` (VND)
54      Amount, whole dong
58      Country, ``VN``
62      Additional data: ``08`` purpose of transaction (the transfer content)
63      CRC-16/CCITT-FALSE of the whole string up to and including ``6304``
======  =======================================================================

Limitation: the receiving account's holder name is not part of the payload (the
bank shows it after the scan); the app shows ``BILLING_VIETQR_ACCOUNT_NAME``
next to the code.
"""

import re
from typing import Final

from app.domains.billing.exceptions import PaymentInputError

_NAPAS_GUI: Final[str] = "A000000727"
_SERVICE_TRANSFER_TO_ACCOUNT: Final[str] = "QRIBFTTA"
_CURRENCY_VND: Final[str] = "704"
_CRC_TAG_PREFIX: Final[str] = "6304"
_MAX_TRANSFER_CONTENT_LENGTH: Final[int] = 25
_TRANSFER_CONTENT_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9 ]+$")


def calculate_crc16(text: str) -> int:
    """Compute the CRC-16/CCITT-FALSE of a text (poly 0x1021, init 0xFFFF).

    Args:
        text: ASCII text.

    Returns:
        The 16-bit checksum; ``"123456789"`` gives ``0x29B1``.
    """
    checksum = 0xFFFF
    for byte in text.encode("ascii"):
        checksum ^= byte << 8
        for _ in range(8):
            if checksum & 0x8000:
                checksum = ((checksum << 1) ^ 0x1021) & 0xFFFF
            else:
                checksum = (checksum << 1) & 0xFFFF
    return checksum


def _encode_field(tag: str, value: str) -> str:
    """Encode one ``tag length value`` field.

    Args:
        tag: Two-digit tag.
        value: The value; at most 99 characters.

    Returns:
        The field text.

    Raises:
        PaymentInputError: The value is empty or too long for the length field.
    """
    if not value or len(value) > 99:
        raise PaymentInputError(f"VietQR field {tag} is empty or longer than 99")
    return f"{tag}{len(value):02d}{value}"


def build_vietqr_payload(
    *, bank_bin: str, account_number: str, amount: int, transfer_content: str
) -> str:
    """Build the VietQR string of a one-use top-up transfer.

    Args:
        bank_bin: The six-digit NAPAS BIN of the receiving bank.
        account_number: The receiving account number.
        amount: Amount to transfer, whole dong, above zero.
        transfer_content: The unique content the payer's transfer must carry
            (letters, digits and spaces, at most 25 characters).

    Returns:
        The payload string, CRC included.

    Raises:
        PaymentInputError: An argument cannot be encoded.
    """
    if amount <= 0:
        raise PaymentInputError("VietQR amount must be above zero")
    if not _TRANSFER_CONTENT_PATTERN.match(transfer_content) or (
        len(transfer_content) > _MAX_TRANSFER_CONTENT_LENGTH
    ):
        raise PaymentInputError("VietQR transfer content is not valid")
    if not (bank_bin.isdigit() and len(bank_bin) == 6):
        raise PaymentInputError("VietQR bank BIN must be six digits")
    if not account_number.isalnum():
        raise PaymentInputError("VietQR account number is not valid")
    beneficiary = _encode_field("00", bank_bin) + _encode_field("01", account_number)
    merchant_account = (
        _encode_field("00", _NAPAS_GUI)
        + _encode_field("01", beneficiary)
        + _encode_field("02", _SERVICE_TRANSFER_TO_ACCOUNT)
    )
    body = (
        _encode_field("00", "01")
        + _encode_field("01", "12")
        + _encode_field("38", merchant_account)
        + _encode_field("53", _CURRENCY_VND)
        + _encode_field("54", str(amount))
        + _encode_field("58", "VN")
        + _encode_field("62", _encode_field("08", transfer_content))
        + _CRC_TAG_PREFIX
    )
    return f"{body}{calculate_crc16(body):04X}"


def parse_tlv_fields(payload: str) -> dict[str, str]:
    """Split a payload into its top-level ``tag -> value`` fields.

    Args:
        payload: A VietQR / EMVCo string.

    Returns:
        The fields in order; nested fields (tag 38, 62) stay as text and can be
        read by calling this function on the value.

    Raises:
        PaymentInputError: The text is not well-formed.
    """
    fields: dict[str, str] = {}
    position = 0
    while position < len(payload):
        header = payload[position : position + 4]
        if len(header) < 4 or not header.isdigit():
            raise PaymentInputError("VietQR payload is malformed")
        tag, length = header[:2], int(header[2:])
        value = payload[position + 4 : position + 4 + length]
        if len(value) != length:
            raise PaymentInputError("VietQR payload is truncated")
        fields[tag] = value
        position += 4 + length
    return fields


def is_crc_valid(payload: str) -> bool:
    """Check the trailing CRC of a payload.

    Args:
        payload: A VietQR / EMVCo string.

    Returns:
        ``True`` when the last four characters are the CRC of the rest.
    """
    if len(payload) < 8 or payload[-8:-4] != _CRC_TAG_PREFIX:
        return False
    return f"{calculate_crc16(payload[:-4]):04X}" == payload[-4:].upper()
