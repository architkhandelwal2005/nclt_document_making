"""Validated, encrypted complete backups for Casefile data and uploaded files.

The database snapshot is created through SQLite's online-backup API.  The
resulting ZIP archive, including its manifest, is encrypted as one unit by the
existing platform-specific protection in :mod:`security`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any
import json
import shutil
import tempfile
import zipfile

from database import CasefileDatabase
from security import protect_bytes, unprotect_bytes


COMPLETE_BACKUP_EXTENSION = ".casefile-complete-backup"
MANIFEST_PATH = "casefile-complete-backup-manifest.json"
DATABASE_PATH = "database/casefile.db"
FORMAT_NAME = "casefile-complete-backup"
FORMAT_VERSION = 1
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024


class CompleteBackupError(ValueError):
    """Raised when a complete backup is malformed or unsafe to restore."""


@dataclass(frozen=True)
class CompleteBackupInfo:
    path: Path
    document_count: int
    document_bytes: int


@dataclass(frozen=True)
class VerifiedCompleteBackup:
    """A decrypted archive whose manifest, hashes and database were validated."""

    archive_bytes: bytes
    database_bytes: bytes
    documents: tuple[dict[str, Any], ...]
    created_at: str


def _sha256(data: bytes) -> str:
    return sha256(data).hexdigest()


def _normal_relative_path(value: str) -> PurePosixPath:
    """Return a safe archive path; reject traversal and Windows path aliases."""
    if not value or "\\" in value:
        raise CompleteBackupError("Backup contains an unsafe archive path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.parts[0] in {"", "."}:
        raise CompleteBackupError("Backup contains an unsafe archive path")
    return path


def _document_entry(relative_path: Path) -> str:
    return (PurePosixPath("documents") / PurePosixPath(relative_path.as_posix())).as_posix()


def _write_file(bundle: zipfile.ZipFile, source: Path, archive_path: str) -> dict[str, Any]:
    data = source.read_bytes()
    bundle.writestr(archive_path, data, compress_type=zipfile.ZIP_DEFLATED)
    return {"path": archive_path, "sha256": _sha256(data), "bytes": len(data)}


def create_complete_backup(
    store: CasefileDatabase,
    documents_dir: Path,
    destination_dir: Path,
    *,
    label: str = "manual",
) -> CompleteBackupInfo:
    """Create an encrypted bundle containing a consistent DB snapshot and documents.

    Symlinks are deliberately excluded: an uploaded document backup must never
    follow a link outside the configured document directory.
    """
    destination_dir.mkdir(parents=True, exist_ok=True)
    safe_label = "".join(character if character.isalnum() or character == "-" else "-" for character in label.lower()).strip("-") or "manual"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    final_path = destination_dir / f"casefile-complete-{safe_label}-{timestamp}{COMPLETE_BACKUP_EXTENSION}"

    with tempfile.TemporaryDirectory(prefix="casefile-complete-backup-", dir=destination_dir) as temporary_name:
        temporary = Path(temporary_name)
        snapshot = store.backup(temporary)
        archive_path = temporary / "complete-backup.zip"
        documents: list[dict[str, Any]] = []
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as bundle:
            database = _write_file(bundle, snapshot, DATABASE_PATH)
            if documents_dir.is_dir():
                for source in sorted(documents_dir.rglob("*")):
                    if source.is_file() and not source.is_symlink():
                        documents.append(_write_file(bundle, source, _document_entry(source.relative_to(documents_dir))))
            manifest = {
                "format": FORMAT_NAME,
                "version": FORMAT_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "database": database,
                "documents": documents,
                "document_count": len(documents),
                "document_bytes": sum(int(item["bytes"]) for item in documents),
            }
            bundle.writestr(MANIFEST_PATH, json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
        archive_bytes = archive_path.read_bytes()
        final_path.write_bytes(protect_bytes(archive_bytes, "Casefile complete encrypted backup"))

    return CompleteBackupInfo(
        path=final_path,
        document_count=len(documents),
        document_bytes=sum(int(item["bytes"]) for item in documents),
    )


def _read_manifest(archive: zipfile.ZipFile) -> dict[str, Any]:
    try:
        manifest_raw = archive.read(MANIFEST_PATH)
        manifest = json.loads(manifest_raw.decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise CompleteBackupError("Complete backup has no valid manifest") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT_NAME or manifest.get("version") != FORMAT_VERSION:
        raise CompleteBackupError("Backup is not a supported Casefile complete backup")
    if not isinstance(manifest.get("database"), dict) or not isinstance(manifest.get("documents"), list):
        raise CompleteBackupError("Complete backup manifest is incomplete")
    return manifest


def _validate_archive_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    entries = archive.infolist()
    if len(entries) > MAX_ARCHIVE_MEMBERS:
        raise CompleteBackupError("Backup contains too many files")
    if sum(entry.file_size for entry in entries) > MAX_UNCOMPRESSED_BYTES:
        raise CompleteBackupError("Backup expands beyond the allowed restore size")
    paths: dict[str, zipfile.ZipInfo] = {}
    for entry in entries:
        path = _normal_relative_path(entry.filename)
        if entry.is_dir() or entry.filename in paths:
            raise CompleteBackupError("Backup contains unsupported or duplicate archive entries")
        if entry.filename != MANIFEST_PATH and entry.filename != DATABASE_PATH and path.parts[0] != "documents":
            raise CompleteBackupError("Backup contains an unexpected archive entry")
        paths[entry.filename] = entry
    return paths


def _validate_manifest_file(value: Any, expected_path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("path") != expected_path:
        raise CompleteBackupError("Complete backup manifest does not match its archive")
    if not isinstance(value.get("sha256"), str) or len(value["sha256"]) != 64 or not isinstance(value.get("bytes"), int) or value["bytes"] < 0:
        raise CompleteBackupError("Complete backup manifest has invalid file metadata")
    return value


def verify_complete_backup(encrypted_bytes: bytes, store: CasefileDatabase) -> VerifiedCompleteBackup:
    """Decrypt and verify a bundle before any live database or file is changed."""
    if len(encrypted_bytes) > MAX_ARCHIVE_BYTES:
        raise CompleteBackupError("Complete backup must not exceed 512 MB")
    try:
        archive_bytes = unprotect_bytes(encrypted_bytes)
    except (ValueError, OSError, RuntimeError) as exc:
        raise CompleteBackupError(str(exc)) from exc
    if len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise CompleteBackupError("Decrypted backup exceeds the allowed restore size")
    try:
        archive = zipfile.ZipFile(BytesIO(archive_bytes))
    except zipfile.BadZipFile as exc:
        raise CompleteBackupError("Complete backup is not a valid ZIP archive") from exc
    with archive:
        paths = _validate_archive_members(archive)
        if MANIFEST_PATH not in paths or DATABASE_PATH not in paths:
            raise CompleteBackupError("Complete backup is missing required data")
        manifest = _read_manifest(archive)
        database_manifest = _validate_manifest_file(manifest["database"], DATABASE_PATH)
        documents: list[dict[str, Any]] = []
        expected_paths = {MANIFEST_PATH, DATABASE_PATH}
        for value in manifest["documents"]:
            if not isinstance(value, dict) or not isinstance(value.get("path"), str):
                raise CompleteBackupError("Complete backup manifest has an invalid document entry")
            document_path = value["path"]
            path = _normal_relative_path(document_path)
            if path.parts[0] != "documents" or len(path.parts) == 1:
                raise CompleteBackupError("Complete backup has an invalid document path")
            documents.append(_validate_manifest_file(value, document_path))
            expected_paths.add(document_path)
        if len({item["path"] for item in documents}) != len(documents):
            raise CompleteBackupError("Complete backup manifest lists a document more than once")
        if manifest.get("document_count") != len(documents) or manifest.get("document_bytes") != sum(int(item["bytes"]) for item in documents):
            raise CompleteBackupError("Complete backup document totals do not match its manifest")
        if set(paths) != expected_paths or len(expected_paths) != len(paths):
            raise CompleteBackupError("Complete backup manifest does not list every archive file")

        database_bytes = archive.read(DATABASE_PATH)
        if len(database_bytes) != database_manifest["bytes"] or _sha256(database_bytes) != database_manifest["sha256"]:
            raise CompleteBackupError("Complete backup database checksum does not match")
        store.validate_sqlite_bytes(database_bytes)

        for document in documents:
            content = archive.read(document["path"])
            if len(content) != document["bytes"] or _sha256(content) != document["sha256"]:
                raise CompleteBackupError(f"Complete backup document checksum does not match: {document['path']}")

    return VerifiedCompleteBackup(
        archive_bytes=archive_bytes,
        database_bytes=database_bytes,
        documents=tuple(documents),
        created_at=str(manifest.get("created_at", "")),
    )


def stage_verified_documents(backup: VerifiedCompleteBackup, staging_dir: Path) -> None:
    """Write already-verified documents under a new directory, never into live data."""
    staging_dir.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(BytesIO(backup.archive_bytes)) as archive:
        for document in backup.documents:
            relative = PurePosixPath(document["path"]).relative_to("documents")
            destination = staging_dir.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(document["path"]) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
