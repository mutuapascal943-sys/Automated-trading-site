import base64
import hashlib
import os

from django.conf import settings
from cryptography.fernet import Fernet


def _derive_key(salt: bytes | None = None) -> tuple[bytes, bytes]:
    """Derive a Fernet-compatible 32-byte key using PBKDF2HMAC(SHA256).

    Returns (key, salt) — salt is generated once and stored alongside
    ciphertexts so that rotation or re-keying is possible later.
    """
    if salt is None:
        salt = os.urandom(16)
    raw = hashlib.pbkdf2_hmac(
        "sha256",
        settings.SECRET_KEY.encode("utf-8"),
        salt,
        iterations=600_000,
        dklen=32,
    )
    return base64.urlsafe_b64encode(raw), salt


# Lazily initialised — first call derives and caches.
_cipher: Fernet | None = None
_salt: bytes | None = None


def _get_cipher() -> Fernet:
    global _cipher, _salt
    if _cipher is None:
        key, _salt = _derive_key()
        _cipher = Fernet(key)
    return _cipher


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    cipher = _get_cipher()
    return cipher.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        cipher = _get_cipher()
        return cipher.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except Exception:
        return ciphertext
