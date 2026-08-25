"""Replaceable MCA company-master lookup providers.

MCA means the Ministry of Corporate Affairs.  Local installations deliberately
default to manual review because this project has no authorised live MCA API.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class ManualMcaProvider:
    name = "manual"

    def lookup(self, cin: str) -> Dict[str, Any]:
        return {
            "provider": self.name,
            "status": "manual_required",
            "queried_cin": cin,
            "message": "Enter or verify current MCA master data manually.",
            "master_data": {},
        }


class MockMcaProvider:
    """Deterministic provider for automated tests; never selected in production."""

    name = "mock"

    def __init__(self, records: Optional[Dict[str, Dict[str, Any]]] = None):
        self.records = records or {}

    def lookup(self, cin: str) -> Dict[str, Any]:
        record = self.records.get(cin.upper())
        return {
            "provider": self.name,
            "status": "found" if record else "not_found",
            "queried_cin": cin,
            "master_data": record or {},
        }

