"""Encrypted backup helpers for local Windows and single-service cloud deployments."""

from __future__ import annotations

import os
from base64 import urlsafe_b64encode
from hashlib import sha256
from pathlib import Path

WINDOWS_MAGIC = b"CASEFILE-DPAPI-1\n"
FERNET_MAGIC = b"CASEFILE-FERNET-1\n"
# Backward-compatible public name used by existing Windows backup tests.
MAGIC = WINDOWS_MAGIC
CRYPTPROTECT_UI_FORBIDDEN = 0x01


if os.name == "nt":
    from ctypes import POINTER, Structure, byref, c_char, c_void_p, cast, create_string_buffer, string_at, windll
    from ctypes.wintypes import DWORD

    class DataBlob(Structure):
        _fields_ = [("cbData", DWORD), ("pbData", POINTER(c_char))]

    def _blob(data: bytes):
        buffer = create_string_buffer(data)
        return DataBlob(len(data), cast(buffer, POINTER(c_char))), buffer


def _cloud_fernet():
    """Return a configured cloud backup key; never create an unrecoverable key."""
    from cryptography.fernet import Fernet

    value = os.environ.get("CASEFILE_BACKUP_KEY", "").strip()
    if not value:
        raise RuntimeError("CASEFILE_BACKUP_KEY must be configured before cloud backups can be encrypted")
    if len(value) < 32:
        raise RuntimeError("CASEFILE_BACKUP_KEY must contain at least 32 characters")
    try:
        return Fernet(value.encode("ascii"))
    except (ValueError, TypeError):
        # Render's generated values are high-entropy secrets, but are not
        # necessarily formatted as a Fernet key. Derive the required 32 bytes
        # deterministically so backups remain recoverable with that one secret.
        return Fernet(urlsafe_b64encode(sha256(value.encode("utf-8")).digest()))


def _protect_with_dpapi(data: bytes, description: str) -> bytes:
    """Protect data with the current Windows account's DPAPI key."""
    if os.name != "nt":
        raise RuntimeError("DPAPI encryption is available only on Windows")
    source, source_buffer = _blob(data)
    output = DataBlob()
    description_buffer = create_string_buffer(description.encode("utf-16-le") + b"\x00\x00")
    if not windll.crypt32.CryptProtectData(
        byref(source), cast(description_buffer, c_void_p), None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, byref(output),
    ):
        raise OSError("Windows could not encrypt the backup")
    try:
        return WINDOWS_MAGIC + string_at(output.pbData, output.cbData)
    finally:
        windll.kernel32.LocalFree(output.pbData)
        _ = source_buffer  # Keep the input buffer alive through the API call.


def protect_bytes(data: bytes, description: str = "Casefile encrypted backup") -> bytes:
    """Encrypt a backup using DPAPI locally or Fernet on a cloud Linux host."""
    if os.name == "nt":
        return _protect_with_dpapi(data, description)
    return FERNET_MAGIC + _cloud_fernet().encrypt(data)


def unprotect_bytes(data: bytes) -> bytes:
    """Decrypt a backup only with its corresponding platform/key protection."""
    if data.startswith(WINDOWS_MAGIC):
        return _unprotect_with_dpapi(data[len(WINDOWS_MAGIC):])
    if not data.startswith(FERNET_MAGIC):
        raise ValueError("Not a Casefile encrypted backup")
    from cryptography.fernet import InvalidToken
    try:
        return _cloud_fernet().decrypt(data[len(FERNET_MAGIC):])
    except InvalidToken as exc:
        raise ValueError("Backup cannot be decrypted with the configured cloud backup key") from exc


def _unprotect_with_dpapi(data: bytes) -> bytes:
    """Decrypt a Windows-local backup with the Windows account that created it."""
    if os.name != "nt":
        raise ValueError("Windows DPAPI backups can be restored only on Windows")
    source, source_buffer = _blob(data)
    output = DataBlob()
    if not windll.crypt32.CryptUnprotectData(
        byref(source), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, byref(output)
    ):
        raise OSError("Windows could not decrypt the backup for this user")
    try:
        return string_at(output.pbData, output.cbData)
    finally:
        windll.kernel32.LocalFree(output.pbData)
        _ = source_buffer


def encrypt_file(source: Path, destination: Path) -> Path:
    destination.write_bytes(protect_bytes(source.read_bytes()))
    return destination
