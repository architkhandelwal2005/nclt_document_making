"""Environment-only AI configuration; secrets never cross the backend boundary."""

from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class AIConfig:
    enabled: bool = False
    provider: str = "openai"
    api_key: str = ""
    extraction_model: str = "gpt-5.6-luna"
    timeout_seconds: float = 120.0
    max_document_characters: int = 200_000
    billing_mode: str = "unknown"
    direct_document_token_limit: int = 4_000
    chunk_target_tokens: int = 1_800

    @classmethod
    def from_environment(cls) -> "AIConfig":
        provider = os.environ.get("AI_PROVIDER", "openai").strip().lower() or "openai"
        if provider == "groq":
            api_key = os.environ.get("GROQ_API_KEY", "").strip()
            extraction_model = os.environ.get(
                "GROQ_EXTRACTION_MODEL", "openai/gpt-oss-120b"
            ).strip() or "openai/gpt-oss-120b"
            billing_mode = os.environ.get("GROQ_BILLING_MODE", "unknown").strip().lower() or "unknown"
        else:
            api_key = os.environ.get("OPENAI_API_KEY", "").strip()
            extraction_model = os.environ.get(
                "OPENAI_EXTRACTION_MODEL", "gpt-5.6-luna"
            ).strip() or "gpt-5.6-luna"
            billing_mode = "paid"
        return cls(
            enabled=os.environ.get("AI_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
            provider=provider,
            api_key=api_key,
            extraction_model=extraction_model,
            timeout_seconds=max(5.0, float(os.environ.get("AI_TIMEOUT_SECONDS", "120"))),
            max_document_characters=max(10_000, int(os.environ.get("AI_MAX_DOCUMENT_CHARACTERS", "200000"))),
            billing_mode=billing_mode,
            direct_document_token_limit=max(1_000, int(os.environ.get("GROQ_DIRECT_DOCUMENT_TOKEN_LIMIT", "4000"))),
            chunk_target_tokens=max(1_000, int(os.environ.get("GROQ_CHUNK_TARGET_TOKENS", "1800"))),
        )

    @property
    def missing_key_code(self) -> str:
        return "GROQ_API_KEY_NOT_CONFIGURED" if self.provider == "groq" else "API_KEY_NOT_CONFIGURED"

    def public(self) -> dict:
        return {
            "enabled": self.enabled,
            "configured": bool(self.api_key),
            "available": self.enabled and bool(self.api_key),
            "provider": self.provider,
            "model": self.extraction_model,
            "billing_mode": self.billing_mode if self.provider == "groq" else "paid",
        }
