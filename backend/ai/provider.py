"""Provider contract used by business modules instead of vendor SDK calls."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class AIProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retry_after_seconds: float | None = None,
                 api_calls: int = 0, latency_ms: int = 0):
        super().__init__(message)
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        self.api_calls = api_calls
        self.latency_ms = latency_ms


@dataclass(frozen=True)
class ProviderResult:
    parsed: dict[str, Any]
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    response_id: str = ""
    api_calls: int = 1
    latency_ms: int = 0


class AIProvider(ABC):
    name: str

    @abstractmethod
    def extract(self, *, system_prompt: str, document_text: str, model: str) -> ProviderResult:
        """Return a schema-conforming extraction or raise ``AIProviderError``."""
