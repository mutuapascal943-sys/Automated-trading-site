from django.conf import settings
from cryptography.fernet import Fernet
import base64
import hashlib


def _derive_key() -> bytes:
    raw = settings.SECRET_KEY.encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())


_cipher = Fernet(_derive_key())


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _cipher.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _cipher.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except Exception:
        return ciphertext
