"""Windows-local backup encryption using the Data Protection API (DPAPI)."""

from __future__ import annotations

from ctypes import POINTER, Structure, byref, c_char, c_void_p, cast, create_string_buffer, sizeof, string_at, windll
from ctypes.wintypes import DWORD
from pathlib import Path


MAGIC = b"CASEFILE-DPAPI-1\n"
CRYPTPROTECT_UI_FORBIDDEN = 0x01


class DataBlob(Structure):
    _fields_ = [("cbData", DWORD), ("pbData", POINTER(c_char))]


def _blob(data: bytes):
    buffer = create_string_buffer(data)
    return DataBlob(len(data), cast(buffer, POINTER(c_char))), buffer


def protect_bytes(data: bytes, description: str = "Casefile encrypted backup") -> bytes:
    source, source_buffer = _blob(data)
    output = DataBlob()
    description_buffer = create_string_buffer(description.encode("utf-16-le") + b"\x00\x00")
    if not windll.crypt32.CryptProtectData(
        byref(source), cast(description_buffer, c_void_p), None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, byref(output),
    ):
        raise OSError("Windows could not encrypt the backup")
    try:
        return MAGIC + string_at(output.pbData, output.cbData)
    finally:
        windll.kernel32.LocalFree(output.pbData)
        _ = source_buffer  # Keep the input buffer alive through the API call.


def unprotect_bytes(data: bytes) -> bytes:
    if not data.startswith(MAGIC):
        raise ValueError("Not a Casefile encrypted backup")
    source, source_buffer = _blob(data[len(MAGIC):])
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

