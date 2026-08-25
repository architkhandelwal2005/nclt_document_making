"""Create a sanitized portable configuration from the developer environment."""

from pathlib import Path
from dotenv import dotenv_values


SOURCE = Path(__file__).with_name(".env")
DESTINATION = Path(__file__).parent.parent / "portable_work" / "Casefile.env"
LOGIN_DETAILS = DESTINATION.with_name("LOGIN DETAILS.txt")
ALLOWED = ("JWT_SECRET", "ADMIN_EMAIL", "ADMIN_PASSWORD", "ADMIN_NAME")


def main() -> None:
    values = dotenv_values(SOURCE)
    missing = [key for key in ALLOWED if not values.get(key)]
    if missing:
        raise SystemExit(f"Missing portable configuration: {', '.join(missing)}")
    lines = [f"{key}={values[key]}" for key in ALLOWED]
    lines.extend(("STORAGE_MODE=local", "CORS_ORIGINS=http://127.0.0.1:8765"))
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    DESTINATION.write_text("\n".join(lines) + "\n", encoding="utf-8")
    LOGIN_DETAILS.write_text(
        "CASEFILE OFFICE TEST LOGIN\n"
        "==========================\n"
        f"Email: {values['ADMIN_EMAIL']}\n"
        f"Password: {values['ADMIN_PASSWORD']}\n\n"
        "This file contains the test login. Keep the pen drive within the office.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
