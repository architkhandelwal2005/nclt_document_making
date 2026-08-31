"""Validated Windows launcher for the shared office UAT server."""

from __future__ import annotations

import os
from pathlib import Path
import socket

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
ENV_FILE = PROJECT_ROOT / ".env.uat"
FRONTEND_INDEX = PROJECT_ROOT / "frontend" / "build" / "index.html"


def _claim_pid_file(pid_file: Path) -> None:
    """Atomically reserve the process record without replacing a running instance."""
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with pid_file.open("x", encoding="ascii") as stream:
            stream.write(str(os.getpid()))
    except FileExistsError as exc:
        raise RuntimeError(
            "A UAT process record already exists. Run STOP_NCLT_UAT.bat before starting again."
        ) from exc


def _usable_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = item[4][0]
            if not address.startswith(("127.", "169.254.")):
                addresses.add(address)
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))
        address = probe.getsockname()[0]
        if not address.startswith(("127.", "169.254.")):
            addresses.add(address)
    except OSError:
        pass
    finally:
        try:
            probe.close()
        except (NameError, OSError):
            pass
    return sorted(addresses)


def _required_configuration() -> dict[str, str]:
    if not ENV_FILE.is_file():
        raise RuntimeError("UAT configuration is missing. Run SETUP_NCLT_UAT.bat first.")
    values = {key: str(value or "").strip() for key, value in dotenv_values(ENV_FILE).items()}
    required = [
        "CASEFILE_DATABASE_PATH", "CASEFILE_DOCUMENTS_DIR", "JWT_SECRET",
        "ADMIN_EMAIL", "ADMIN_PASSWORD", "ADMIN_NAME",
    ]
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise RuntimeError(f"UAT configuration is incomplete: {', '.join(missing)}")
    if values.get("CASEFILE_ENVIRONMENT", "").upper() != "UAT":
        raise RuntimeError("CASEFILE_ENVIRONMENT must be UAT")
    if values.get("CASEFILE_SERVE_FRONTEND", "").lower() not in {"1", "true", "yes", "on"}:
        raise RuntimeError("CASEFILE_SERVE_FRONTEND must be true")
    if len(values["JWT_SECRET"]) < 32 or "replace" in values["JWT_SECRET"].lower():
        raise RuntimeError("JWT_SECRET must be a generated secret of at least 32 characters")
    if len(values["ADMIN_PASSWORD"]) < 12 or "replace" in values["ADMIN_PASSWORD"].lower():
        raise RuntimeError("ADMIN_PASSWORD must contain at least 12 characters")
    database = Path(values["CASEFILE_DATABASE_PATH"]).resolve()
    documents = Path(values["CASEFILE_DOCUMENTS_DIR"]).resolve()
    development_database = (BACKEND_DIR / "data" / "casefile.db").resolve()
    development_documents = (BACKEND_DIR / "data" / "files").resolve()
    if database == development_database or documents == development_documents:
        raise RuntimeError("UAT storage must remain separate from development storage")
    database.parent.mkdir(parents=True, exist_ok=True)
    documents.mkdir(parents=True, exist_ok=True)
    if not FRONTEND_INDEX.is_file():
        raise RuntimeError("Compiled frontend is missing. Run npm run build on the server PC.")
    return values


def main() -> int:
    pid_file = PROJECT_ROOT / "data" / "nclt_uat.pid"
    try:
        values = _required_configuration()
        _claim_pid_file(pid_file)
    except RuntimeError as exc:
        print("\nNCLT CIRP SOFTWARE - UAT STARTUP ERROR")
        print(str(exc))
        return 1

    os.environ.update(values)
    os.environ["CASEFILE_ENV_FILE"] = str(ENV_FILE)
    port = int(values.get("CASEFILE_UAT_PORT", "8001"))
    urls = _usable_ipv4_addresses()

    print("\n============================================================")
    print("  NCLT CIRP SOFTWARE - UAT SERVER RUNNING")
    print("============================================================")
    print("\nOffice URL:")
    if urls:
        for address in urls:
            print(f"  http://{address}:{port}")
    else:
        print(f"  http://SERVER-IP:{port}")
        print("  The LAN address could not be detected automatically; run ipconfig.")
    print(f"\nServer-PC check: http://127.0.0.1:{port}")
    print("\nKeep this window open while staff are using the software.")
    print("Use STOP_NCLT_UAT.bat to stop only this UAT server.\n")

    try:
        import uvicorn

        os.chdir(BACKEND_DIR)
        uvicorn.run("server:app", host="0.0.0.0", port=port, log_level="info")
        return 0
    except Exception as exc:
        print("\nNCLT CIRP SOFTWARE - UAT SERVER STOPPED WITH AN ERROR")
        print(f"{type(exc).__name__}: {exc}")
        return 1
    finally:
        try:
            if pid_file.read_text(encoding="ascii").strip() == str(os.getpid()):
                pid_file.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
