"""Focused safety checks for the shared-office UAT mode."""

from pathlib import Path

from fastapi.testclient import TestClient
from dotenv import dotenv_values
import pytest

import server
import uat_launcher
import uat_tools
from database import CasefileDatabase


def test_health_check_exposes_only_safe_operational_state(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "casefile_store", CasefileDatabase(tmp_path / "uat-health.db"))
    monkeypatch.setattr(server, "APP_ENVIRONMENT", "UAT")
    with TestClient(server.app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database_reachable": True, "environment": "UAT"}
    assert not ({"path", "secret", "client", "database"} & set(response.json()))


def test_uat_configuration_requires_separate_storage_and_compiled_frontend(tmp_path, monkeypatch):
    project = tmp_path / "project"
    backend = project / "backend"
    frontend_index = project / "frontend" / "build" / "index.html"
    environment = project / ".env.uat"
    frontend_index.parent.mkdir(parents=True)
    backend.mkdir(parents=True)
    frontend_index.write_text("<html>UAT</html>", encoding="utf-8")
    environment.write_text("\n".join([
        "CASEFILE_ENVIRONMENT=UAT",
        "CASEFILE_SERVE_FRONTEND=true",
        f"CASEFILE_DATABASE_PATH={(project / 'data' / 'nclt_uat.db').as_posix()}",
        f"CASEFILE_DOCUMENTS_DIR={(project / 'data' / 'uat_documents').as_posix()}",
        "JWT_SECRET=synthetic-test-secret-with-more-than-32-characters",
        "ADMIN_EMAIL=admin@example.test",
        "ADMIN_PASSWORD=SyntheticPassword2026!",
        "ADMIN_NAME=Synthetic Administrator",
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(uat_launcher, "PROJECT_ROOT", project)
    monkeypatch.setattr(uat_launcher, "BACKEND_DIR", backend)
    monkeypatch.setattr(uat_launcher, "FRONTEND_INDEX", frontend_index)
    monkeypatch.setattr(uat_launcher, "ENV_FILE", environment)
    values = uat_launcher._required_configuration()
    assert Path(values["CASEFILE_DATABASE_PATH"]).parent.is_dir()
    assert Path(values["CASEFILE_DOCUMENTS_DIR"]).is_dir()


def test_uat_configuration_writer_preserves_special_characters(tmp_path, monkeypatch):
    environment = tmp_path / ".env.uat"
    monkeypatch.setattr(uat_tools, "ENV_FILE", environment)
    values = {
        "ADMIN_NAME": "O'Brien # UAT",
        "ADMIN_PASSWORD": "A complex $ password # 2026!",
        "JWT_SECRET": "synthetic-secret-with-'quotes'-and-$-characters",
    }
    uat_tools._write_configuration(values)
    assert dotenv_values(environment) == values


def test_uat_launcher_never_overwrites_existing_process_record(tmp_path):
    pid_file = tmp_path / "data" / "nclt_uat.pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text("12345", encoding="ascii")
    with pytest.raises(RuntimeError, match="STOP_NCLT_UAT"):
        uat_launcher._claim_pid_file(pid_file)
    assert pid_file.read_text(encoding="ascii") == "12345"
