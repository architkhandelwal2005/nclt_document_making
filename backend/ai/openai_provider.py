"""Official OpenAI Responses API provider implementation."""

from __future__ import annotations

from .provider import AIProvider, AIProviderError, ProviderResult
from pydantic import BaseModel


class OpenAIProvider(AIProvider):
    name = "openai"

    def __init__(self, api_key: str, timeout_seconds: float = 120.0):
        if not api_key:
            raise AIProviderError("API_KEY_NOT_CONFIGURED", "OPENAI_API_KEY is not configured")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def extract(self, *, system_prompt: str, document_text: str, model: str,
                output_schema: type[BaseModel] | None = None,
                schema_name: str = "admission_order_extraction") -> ProviderResult:
        if output_schema is None:
            from .schemas.admission_order import AdmissionOrderExtraction
            output_schema = AdmissionOrderExtraction
        try:
            import openai
            from openai import OpenAI
        except ImportError as error:
            raise AIProviderError("API_PROVIDER_ERROR", "The OpenAI Python SDK is not installed") from error
        try:
            client = OpenAI(api_key=self.api_key, timeout=self.timeout_seconds, max_retries=1)
            response = client.responses.parse(
                model=model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": document_text},
                ],
                text_format=output_schema,
                store=False,
            )
            parsed = response.output_parsed
            if parsed is None:
                raise AIProviderError("INVALID_STRUCTURED_RESPONSE", "Provider returned no parsed structured output")
            usage = getattr(response, "usage", None)
            return ProviderResult(
                parsed=parsed.model_dump(mode="json"), provider=self.name,
                model=getattr(response, "model", None) or model,
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
                response_id=str(getattr(response, "id", "") or ""),
            )
        except AIProviderError:
            raise
        except getattr(openai, "APITimeoutError", TimeoutError) as error:
            raise AIProviderError("API_TIMEOUT", "The AI provider request timed out") from error
        except getattr(openai, "RateLimitError", RuntimeError) as error:
            raise AIProviderError("API_RATE_LIMIT", "The AI provider rate limit was reached") from error
        except Exception as error:
            raise AIProviderError("API_PROVIDER_ERROR", f"The AI provider request failed: {type(error).__name__}") from error
