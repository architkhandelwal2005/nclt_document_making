from io import BytesIO
from hashlib import sha256
import json
import zipfile

from fastapi.testclient import TestClient
import pytest

import server
from backup_bundle import CompleteBackupError, create_complete_backup, verify_complete_backup
from database import CasefileDatabase
from security import protect_bytes, unprotect_bytes


def _configure(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    files_dir = data_dir / "files"
    files_dir.mkdir(parents=True)
    store = CasefileDatabase(data_dir / "casefile.db")
    store.ensure_admin(server.ADMIN_ID, server.ADMIN_EMAIL, server.ADMIN_NAME, server.ADMIN_PASSWORD_HASH)
    monkeypatch.setattr(server, "casefile_store", store)
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "CASE_FILES_DIR", files_dir)
    server.DOC_CACHE.clear()
    return store, data_dir, files_dir


def _login(client):
    response = client.post("/api/auth/login", json={"email": server.ADMIN_EMAIL, "password": server.admin_pwd})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _case(store, name):
    return store.create_case({"name": name, "process_type": "CIRP"}, server.ADMIN_ID)


def test_complete_backup_round_trip_restores_database_and_documents(tmp_path, monkeypatch):
    store, data_dir, files_dir = _configure(tmp_path, monkeypatch)
    original = _case(store, "Original Limited")
    original_file = files_dir / original["id"] / "orders" / "admission-order.pdf"
    original_file.parent.mkdir(parents=True)
    original_file.write_bytes(b"original order bytes")

    with TestClient(server.app) as client:
        headers = _login(client)
        assert client.get("/api/admin/complete-backup").status_code == 401
        downloaded = client.get("/api/admin/complete-backup", headers=headers)
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.headers["x-casefile-document-count"] == "1"
        verified = verify_complete_backup(downloaded.content, store)
        assert verified.database_bytes.startswith(b"SQLite format 3")
        assert len(verified.documents) == 1

        later = _case(store, "Later Limited")
        later_file = files_dir / later["id"] / "later.docx"
        later_file.parent.mkdir(parents=True)
        later_file.write_bytes(b"later bytes")

        restored = client.post(
            "/api/admin/complete-restore",
            files={"file": ("complete.casefile-complete-backup", downloaded.content, "application/octet-stream")},
            headers=headers,
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["documents_restored"] == 1
        assert store.get_case(original["id"])["name"] == "Original Limited"
        assert store.get_case(later["id"]) is None
        assert original_file.read_bytes() == b"original order bytes"
        assert not later_file.exists()
        assert (data_dir / "backups" / restored.json()["safety_backup"]).is_file()


def test_complete_backup_rejects_checksum_failure_without_changes(tmp_path, monkeypatch):
    store, _data_dir, files_dir = _configure(tmp_path, monkeypatch)
    original = _case(store, "Safe Limited")
    source = files_dir / original["id"] / "source.txt"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"safe source")
    result = create_complete_backup(store, files_dir, tmp_path / "backups")

    plain = unprotect_bytes(result.path.read_bytes())
    with zipfile.ZipFile(BytesIO(plain)) as archive:
        manifest = json.loads(archive.read("casefile-complete-backup-manifest.json"))
        document_path = manifest["documents"][0]["path"]
        database = archive.read("database/casefile.db")
        document = archive.read(document_path)
    manifest["documents"][0]["sha256"] = "0" * 64
    damaged = BytesIO()
    with zipfile.ZipFile(damaged, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("database/casefile.db", database)
        archive.writestr(document_path, document)
        archive.writestr("casefile-complete-backup-manifest.json", json.dumps(manifest))

    with pytest.raises(CompleteBackupError, match="checksum"):
        verify_complete_backup(protect_bytes(damaged.getvalue()), store)
    assert store.get_case(original["id"])["name"] == "Safe Limited"
    assert source.read_bytes() == b"safe source"


def test_complete_backup_rejects_path_traversal(tmp_path, monkeypatch):
    store, _data_dir, files_dir = _configure(tmp_path, monkeypatch)
    _case(store, "Traversal Limited")
    result = create_complete_backup(store, files_dir, tmp_path / "backups")
    plain = unprotect_bytes(result.path.read_bytes())
    with zipfile.ZipFile(BytesIO(plain)) as archive:
        database = archive.read("database/casefile.db")
        manifest = json.loads(archive.read("casefile-complete-backup-manifest.json"))
    payload = b"must not escape"
    unsafe_path = "documents/../../outside.txt"
    manifest["documents"] = [{"path": unsafe_path, "bytes": len(payload), "sha256": sha256(payload).hexdigest()}]
    manifest["document_count"] = 1
    manifest["document_bytes"] = len(payload)
    damaged = BytesIO()
    with zipfile.ZipFile(damaged, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("database/casefile.db", database)
        archive.writestr(unsafe_path, payload)
        archive.writestr("casefile-complete-backup-manifest.json", json.dumps(manifest))

    with pytest.raises(CompleteBackupError, match="unsafe archive path"):
        verify_complete_backup(protect_bytes(damaged.getvalue()), store)
    assert not (tmp_path / "outside.txt").exists()
