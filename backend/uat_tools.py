"""Administrative setup, synthetic-data and complete-backup tools for office UAT."""

from __future__ import annotations

import argparse
from datetime import datetime
from getpass import getpass
import os
from pathlib import Path
import re
import secrets
import tempfile
import zipfile

from dotenv import dotenv_values, set_key


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env.uat"


def _path_value(path: Path) -> str:
    return path.resolve().as_posix()


def _write_configuration(values: dict[str, str]) -> None:
    """Write a quoted dotenv file atomically so special characters remain intact."""
    temporary = ENV_FILE.with_name(f".{ENV_FILE.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.touch(mode=0o600, exist_ok=False)
        for key, value in values.items():
            set_key(str(temporary), key, value, quote_mode="always")
        os.replace(temporary, ENV_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def configure() -> int:
    if ENV_FILE.exists():
        print(f"UAT configuration already exists: {ENV_FILE}")
        print("It was not overwritten. Delete it manually only if a full UAT reset is intended.")
        return 0
    print("NCLT CIRP SOFTWARE — FIRST UAT SETUP")
    print("Create the first Administrator account. Password characters will not be displayed.")
    name = input("Administrator name: ").strip()
    email = input("Administrator email: ").strip().lower()
    password = getpass("Administrator password (minimum 12 characters): ")
    confirmation = getpass("Confirm password: ")
    if not name or "@" not in email:
        print("ERROR: Enter an administrator name and a valid email address.")
        return 1
    if len(password) < 12 or password != confirmation:
        print("ERROR: Passwords must match and contain at least 12 characters.")
        return 1

    data_dir = PROJECT_ROOT / "data"
    document_dir = data_dir / "uat_documents"
    data_dir.mkdir(parents=True, exist_ok=True)
    document_dir.mkdir(parents=True, exist_ok=True)
    values = {
        "CASEFILE_ENVIRONMENT": "UAT",
        "CASEFILE_SERVE_FRONTEND": "true",
        "CASEFILE_FRONTEND_BUILD_DIR": _path_value(PROJECT_ROOT / "frontend" / "build"),
        "CASEFILE_DATA_DIR": _path_value(data_dir),
        "CASEFILE_DATABASE_PATH": _path_value(data_dir / "nclt_uat.db"),
        "CASEFILE_DOCUMENTS_DIR": _path_value(document_dir),
        "CASEFILE_UAT_PORT": "8001",
        "STORAGE_MODE": "local",
        "JWT_SECRET": secrets.token_urlsafe(48),
        "ADMIN_EMAIL": email,
        "ADMIN_PASSWORD": password,
        "ADMIN_NAME": name,
        "CORS_ORIGINS": "http://localhost:3000,http://127.0.0.1:3000",
        "AI_ENABLED": "false",
    }
    _write_configuration(values)
    print(f"\nUAT configuration created: {ENV_FILE}")
    print("The secret configuration is excluded from Git. Run START_NCLT_UAT.bat next.")
    return 0


def _load_values() -> dict[str, str]:
    if not ENV_FILE.is_file():
        raise RuntimeError("Run SETUP_NCLT_UAT.bat first")
    values = {key: str(value or "").strip() for key, value in dotenv_values(ENV_FILE).items()}
    os.environ.update(values)
    return values


def create_synthetic_case() -> int:
    values = _load_values()
    import bcrypt
    from database import CasefileDatabase

    store = CasefileDatabase(Path(values["CASEFILE_DATABASE_PATH"]))
    password_hash = bcrypt.hashpw(values["ADMIN_PASSWORD"].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    store.ensure_admin("admin", values["ADMIN_EMAIL"], values["ADMIN_NAME"], password_hash)
    existing = next((item for item in store.list_cases(include_archived=True) if item.get("internal_reference") == "UAT-SYNTHETIC-001"), None)
    if existing:
        print(f"Synthetic case already exists: {existing['name']}")
        return 0
    case = store.create_case({
        "name": "SYNTHETIC UAT COMPANY PRIVATE LIMITED",
        "internal_reference": "UAT-SYNTHETIC-001",
        "cin": "U99999ZZ2026PTC999999",
        "registered_address": "100 Test Lane, Sample City, Test State - 000000",
        "registered_email": "corporate.debtor@example.test",
        "nclt_bench": "TEST BENCH",
        "petition_number": "CP(IB)/TEST/001/2026",
        "applicant_name": "DEMO BANK (SYNTHETIC)",
        "applicant_category": "Financial Creditor",
        "process_type": "CIRP",
        "order_date": "2026-08-01",
        "commencement_date": "2026-08-01",
        "current_stage": "Commencement",
        "notes": "Synthetic UAT data only. Not a real client or proceeding.",
    }, "admin")
    print(f"Created optional synthetic case: {case['name']}")
    return 0


def backup(label: str) -> int:
    values = _load_values()
    from database import CasefileDatabase
    from security import encrypt_file

    database = Path(values["CASEFILE_DATABASE_PATH"])
    documents = Path(values["CASEFILE_DOCUMENTS_DIR"])
    if not database.is_file():
        raise RuntimeError("UAT database has not been initialized; start UAT once before backing up")
    safe_label = re.sub(r"[^a-z0-9-]+", "-", label.strip().lower()).strip("-") or "manual"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = database.parent / "uat_backups"
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / f"nclt-uat-complete-{safe_label}-{timestamp}.casefile-uat-backup"

    with tempfile.TemporaryDirectory(dir=database.parent) as temporary_name:
        temporary = Path(temporary_name)
        snapshot = CasefileDatabase(database).backup(temporary)
        archive = temporary / "complete-uat-backup.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(snapshot, "database/nclt_uat.db")
            if documents.is_dir():
                for source in documents.rglob("*"):
                    if source.is_file():
                        bundle.write(source, Path("documents") / source.relative_to(documents))
        encrypt_file(archive, final_path)
    print(f"Complete encrypted UAT backup created:\n{final_path}")
    print("It includes the SQLite database and the shared UAT document directory.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("configure")
    subparsers.add_parser("synthetic-case")
    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--label", default="manual")
    args = parser.parse_args()
    try:
        if args.command == "configure":
            return configure()
        if args.command == "synthetic-case":
            return create_synthetic_case()
        return backup(args.label)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
