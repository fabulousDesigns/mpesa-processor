"""The 6-digit code unlocks someone's statement, so it's encrypted at rest and wiped once used."""
import re

from cryptography.fernet import Fernet

from app.core.config import get_settings

_CODE = re.compile(r"^\d{6}$")


class InvalidCode(ValueError):
    pass


def clean_code(raw: str) -> str:
    code = re.sub(r"\s", "", raw or "")
    if not _CODE.match(code):
        raise InvalidCode("code must be exactly 6 digits")
    return code


def _fernet() -> Fernet:
    key = get_settings().code_encryption_key
    if not key:
        raise RuntimeError("CODE_ENCRYPTION_KEY is not set")
    return Fernet(key.encode())


def encrypt_code(code: str) -> bytes:
    return _fernet().encrypt(code.encode())


def decrypt_code(blob: bytes) -> str:
    return _fernet().decrypt(bytes(blob)).decode()