from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


PYTHON = Path(r"C:\Users\archi\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe")
SCRIPTS = Path(r"C:\Users\archi\.codex\plugins\cache\openai-primary-runtime\documents\26.819.11345\skills\documents\scripts")
ROOT = Path(r"E:\nclt document")
REFERENCE_DIR = ROOT / "reference documents"
OUTPUT_DIR = ROOT / "tmp" / "coc_reference_audit"


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")


def run(args: list[str]) -> dict:
    completed = subprocess.run(args, text=True, capture_output=True, encoding="utf-8", errors="replace")
    return {
        "command": args,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def main() -> None:
    for docx in sorted(REFERENCE_DIR.glob("*.docx")):
        target = OUTPUT_DIR / safe_name(docx.stem)
        target.mkdir(parents=True, exist_ok=True)
        reports = {
            "section": run([str(PYTHON), str(SCRIPTS / "section_audit.py"), str(docx)]),
            "style": run([str(PYTHON), str(SCRIPTS / "style_lint.py"), str(docx)]),
            "fields": run([str(PYTHON), str(SCRIPTS / "fields_report.py"), str(docx)]),
            "images": run([str(PYTHON), str(SCRIPTS / "images_audit.py"), str(docx)]),
            "content_controls": run([str(PYTHON), str(SCRIPTS / "content_controls.py"), str(docx), "list", "--json"]),
            "table_geometry": run([str(PYTHON), str(SCRIPTS / "table_geometry.py"), str(docx)]),
        }
        (target / "skill-audits.json").write_text(
            json.dumps(reports, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        failures = [name for name, result in reports.items() if result["exit_code"] != 0]
        print(f"{docx.name}: {'OK' if not failures else 'FAILED ' + ','.join(failures)}")


if __name__ == "__main__":
    main()
