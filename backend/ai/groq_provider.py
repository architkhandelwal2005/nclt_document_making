"""Official Groq SDK provider using strict JSON Schema Structured Outputs."""

from __future__ import annotations

import json
import re
from time import perf_counter

from pydantic import ValidationError

from .provider import AIProvider, AIProviderError, ProviderResult
from .schemas.admission_order import AdmissionOrderExtraction


class GroqProvider(AIProvider):
    name = "groq"

    def __init__(self, api_key: str, timeout_seconds: float = 120.0):
        if not api_key:
            raise AIProviderError("GROQ_API_KEY_NOT_CONFIGURED", "GROQ_API_KEY is not configured")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def extract(self, *, system_prompt: str, document_text: str, model: str) -> ProviderResult:
        try:
            import groq
            from groq import Groq
        except ImportError as error:
            raise AIProviderError("API_PROVIDER_ERROR", "The Groq Python SDK is not installed") from error

        started = perf_counter()
        try:
            # Rate-limit retries are handled once by the orchestration layer so
            # every HTTP attempt and wait remains explicit and auditable.
            client = Groq(api_key=self.api_key, timeout=self.timeout_seconds, max_retries=0)
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": document_text},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "admission_order_extraction",
                        "strict": True,
                        "schema": AdmissionOrderExtraction.model_json_schema(),
                    },
                },
                reasoning_effort="low",
                max_completion_tokens=2_200,
                temperature=0,
                store=False,
            )
            content = response.choices[0].message.content if response.choices else None
            if not content:
                raise AIProviderError("INVALID_STRUCTURED_RESPONSE", "Groq returned no structured output")
            parsed = AdmissionOrderExtraction.model_validate(json.loads(content))
            usage = getattr(response, "usage", None)
            return ProviderResult(
                parsed=parsed.model_dump(mode="json"),
                provider=self.name,
                model=str(getattr(response, "model", None) or model),
                input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                response_id=str(getattr(response, "id", "") or ""),
                api_calls=1,
                latency_ms=round((perf_counter() - started) * 1000),
            )
        except AIProviderError:
            raise
        except (json.JSONDecodeError, ValidationError) as error:
            raise AIProviderError("INVALID_STRUCTURED_RESPONSE", "Groq output did not match the admission schema") from error
        except getattr(groq, "APITimeoutError", TimeoutError) as error:
            raise AIProviderError("API_TIMEOUT", "The Groq request timed out") from error
        except getattr(groq, "RateLimitError", RuntimeError) as error:
            elapsed = round((perf_counter() - started) * 1000)
            header = getattr(getattr(error, "response", None), "headers", {}).get("retry-after")
            retry_after = None
            try:
                retry_after = float(header) if header else None
            except (TypeError, ValueError):
                retry_after = None
            if retry_after is None:
                match = re.search(r"try again in\s+([0-9.]+)s", str(error), re.I)
                retry_after = float(match.group(1)) if match else None
            raise AIProviderError(
                "API_RATE_LIMIT", "The Groq rate limit was reached",
                retry_after_seconds=retry_after, api_calls=1, latency_ms=elapsed,
            ) from error
        except getattr(groq, "APIStatusError", RuntimeError) as error:
            status_code = int(getattr(error, "status_code", 0) or 0)
            if status_code == 400 and "json_validate_failed" in str(error):
                body = getattr(error, "body", {}) or {}
                detail = body.get("error", {}).get("message", "Generated JSON did not match the schema") if isinstance(body, dict) else "Generated JSON did not match the schema"
                raise AIProviderError(
                    "INVALID_STRUCTURED_RESPONSE", f"Groq strict output failed schema validation: {detail[:800]}",
                    api_calls=1, latency_ms=round((perf_counter() - started) * 1000),
                ) from error
            if status_code == 413:
                raise AIProviderError(
                    "DOCUMENT_CHUNK_TOO_LARGE",
                    "The Groq-formatted document chunk exceeds the free-tier request limit",
                    api_calls=1, latency_ms=round((perf_counter() - started) * 1000),
                ) from error
            raise AIProviderError("API_PROVIDER_ERROR", f"The Groq request failed: {type(error).__name__}") from error
        except Exception as error:
            raise AIProviderError("API_PROVIDER_ERROR", f"The Groq request failed: {type(error).__name__}") from error
