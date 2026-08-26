"""Persistent successful-extraction cache lookup."""

from typing import Any, Optional


def find_cached_job(connection: Any, *, document_hash: str, task_type: str, provider: str,
                    model: str, prompt_version: str, schema_version: str) -> Optional[Any]:
    return connection.execute(
        """SELECT * FROM ai_jobs WHERE document_hash=? AND task_type=? AND provider=? AND model=?
        AND prompt_version=? AND schema_version=? AND status IN ('SUCCEEDED','REVIEW_REQUIRED')
        ORDER BY completed_at DESC LIMIT 1""",
        (document_hash, task_type, provider, model, prompt_version, schema_version),
    ).fetchone()
