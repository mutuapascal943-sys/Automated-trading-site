import base64
import hashlib
import logging
import os

from django.conf import settings
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

_SALT_FILE = settings.BASE_DIR / ".credential_salt"


def _derive_key(salt: bytes | None = None) -> tuple[bytes, bytes]:
    """Derive a Fernet-compatible 32-byte key using PBKDF2HMAC(SHA256).

    Returns (key, salt) — salt is generated once, persisted to disk,
    and reused across process restarts so previously encrypted values
    remain decryptable.
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


def _load_or_create_salt() -> bytes:
    """Load the persisted salt, or generate and save a new one."""
    if _SALT_FILE.exists():
        return _SALT_FILE.read_bytes()
    salt = os.urandom(16)
    _SALT_FILE.write_bytes(salt)
    return salt


# Lazily initialised — first call derives and caches.
_cipher: Fernet | None = None
_salt: bytes | None = None


def _get_cipher() -> Fernet:
    global _cipher, _salt
    if _cipher is None:
        _salt = _load_or_create_salt()
        key, _ = _derive_key(_salt)
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
        logger.error("Failed to decrypt credential — possible salt mismatch or corrupted data")
        return ""
