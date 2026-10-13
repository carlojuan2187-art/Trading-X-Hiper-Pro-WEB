from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import PRIVATE_KEY_ENCRYPTION_SECRET, PRIVATE_KEY_ENCRYPTION_SECRET_FALLBACKS

_CIPHER_VERSION = 'fernet-v1'


def _derive_key(secret: str) -> bytes:
    raw = hashlib.sha256((secret or '').encode('utf-8')).digest()
    return base64.urlsafe_b64encode(raw)


class PrivateKeyDecryptError(RuntimeError):
    def __init__(self, message: str, *, version: str | None = None, attempts: int = 0):
        super().__init__(message)
        self.version = version
        self.attempts = int(attempts or 0)


def _get_fernet(secret: str | None = None) -> Fernet:
    return Fernet(_derive_key(secret if secret is not None else PRIVATE_KEY_ENCRYPTION_SECRET))


def _candidate_secrets() -> list[str]:
    candidates: list[str] = []
    for item in [PRIVATE_KEY_ENCRYPTION_SECRET, *list(PRIVATE_KEY_ENCRYPTION_SECRET_FALLBACKS or [])]:
        normalized = str(item or '')
        if normalized in candidates:
            continue
        candidates.append(normalized)
    return candidates or ['']


def encrypt_private_key(value: str) -> tuple[str, str]:
    token = _get_fernet().encrypt((value or '').encode('utf-8')).decode('utf-8')
    return token, _CIPHER_VERSION


def decrypt_private_key(value: str, *, encrypted: bool, version: str | None = None) -> str | None:
    if value is None:
        return None
    if not encrypted:
        return value
    if version not in (None, _CIPHER_VERSION):
        raise PrivateKeyDecryptError(f'Versión de cifrado no soportada: {version}', version=version, attempts=0)

    attempts = 0
    last_exc: Exception | None = None
    for secret in _candidate_secrets():
        attempts += 1
        try:
            return _get_fernet(secret).decrypt(value.encode('utf-8')).decode('utf-8')
        except InvalidToken as exc:
            last_exc = exc
            continue

    raise PrivateKeyDecryptError('No se pudo descifrar la private key almacenada', version=version, attempts=attempts) from last_exc
