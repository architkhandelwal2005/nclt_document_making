"""Self-contained Windows launcher for the Casefile office test package."""

from __future__ import annotations

from pathlib import Path
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

from dotenv import load_dotenv


PORT = 8765
URL = f"http://127.0.0.1:{PORT}"


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent / "portable_work"


APP_DIR = application_dir()
load_dotenv(APP_DIR / "Casefile.env")
os.environ["CASEFILE_APP_DIR"] = str(APP_DIR)
os.environ["CASEFILE_DATA_DIR"] = str(APP_DIR / "data")
os.environ["CASEFILE_TEMPLATE_DIR"] = str(APP_DIR / "templates")
os.environ["CASEFILE_DATABASE_PATH"] = str(APP_DIR / "data" / "casefile.db")
os.environ["STORAGE_MODE"] = "local"
os.environ["CORS_ORIGINS"] = URL


def existing_casefile() -> bool:
    try:
        with urllib.request.urlopen(f"{URL}/api/", timeout=1) as response:
            return "Casefile" in response.read().decode("utf-8", errors="ignore")
    except Exception:
        return False


def port_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return probe.connect_ex(("127.0.0.1", PORT)) == 0


def open_when_ready() -> None:
    for _ in range(60):
        if existing_casefile():
            webbrowser.open(URL)
            return
        time.sleep(0.25)


def main() -> None:
    if existing_casefile():
        webbrowser.open(URL)
        return
    if port_in_use():
        raise SystemExit(f"Port {PORT} is already in use. Close the other program and start Casefile again.")
    required = {
        "web interface": APP_DIR / "web" / "index.html",
        "templates": APP_DIR / "templates",
        "configuration": APP_DIR / "Casefile.env",
    }
    missing = [label for label, path in required.items() if not path.exists()]
    if missing:
        raise SystemExit(f"Portable package is incomplete; missing: {', '.join(missing)}")

    from fastapi.staticfiles import StaticFiles
    import uvicorn
    from server import app

    app.mount("/", StaticFiles(directory=APP_DIR / "web", html=True), name="portable-web")
    threading.Thread(target=open_when_ready, daemon=True).start()
    print("=" * 58)
    print(" CASEFILE PORTABLE TEST SERVER")
    print(f" Browser address: {URL}")
    print(" Keep this window open while testing.")
    print(" Close this window or press Ctrl+C to stop Casefile safely.")
    print("=" * 58)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()

