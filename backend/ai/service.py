"""Admission-order AI job orchestration, persistence, cache, and comparison."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from dataclasses import replace
from time import sleep
from typing import Any, Optional

from pydantic import ValidationError

from database import CasefileDatabase, _from_json, _json, new_id, utc_now
from .cache import find_cached_job
from .comparison import compare_extractions
from .config import AIConfig
from .input_prep import plan_inputs
from .merge import merge_chunk_extractions, not_found_extraction
from .prompts.admission_order import PROMPT_VERSION, SYSTEM_PROMPT
from .provider import AIProvider, AIProviderError, ProviderResult
from .router import build_provider
from .schemas.admission_order import AdmissionOrderExtraction, SCHEMA_VERSION
from .usage import estimate_cost
from .validators.admission_order import validate_extraction


TASK_TYPE = "ADMISSION_ORDER_EXTRACTION"


class AIServiceError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class AdmissionAIService:
    def __init__(self, database: CasefileDatabase, data_dir: Path, config: Optional[AIConfig] = None,
                 provider: Optional[AIProvider] = None):
        self.database = database
        self.data_dir = Path(data_dir).resolve()
        self.config = config or AIConfig.from_environment()
        self._provider = provider

    def public_config(self) -> dict[str, Any]:
        return {
            **self.config.public(), "task_type": TASK_TYPE,
            "prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION,
        }

    @staticmethod
    def _decode(row: Any) -> dict[str, Any]:
        result = dict(row)
        for key in ("parsed_result_json", "validation_json", "comparison_json", "review_json"):
            result[key[:-5]] = _from_json(result.pop(key), {})
        result["cache_hit"] = result["status"] == "CACHE_HIT"
        return result

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM ai_jobs WHERE id=?", (job_id,)).fetchone()
            return self._decode(row) if row else None

    def latest_for_intake(self, intake_id: str) -> Optional[dict[str, Any]]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM ai_jobs WHERE intake_id=? ORDER BY started_at DESC LIMIT 1", (intake_id,),
            ).fetchone()
            return self._decode(row) if row else None

    @staticmethod
    def _page_text(extracted: dict[str, Any]) -> tuple[str, int]:
        sections: list[str] = []
        pages = extracted.get("document", {}).get("pages", [])
        for page in pages:
            number = int(page.get("number") or len(sections) + 1)
            text = "\n".join(str(block.get("text") or "").strip() for block in page.get("blocks", []) if block.get("text"))
            sections.append(f"--- PAGE {number} ---\n{text}")
        return "\n\n".join(sections), len(pages)

    def _source_path(self, intake: dict[str, Any]) -> Path:
        source = (self.data_dir / str(intake.get("storage_path") or "")).resolve()
        if not source.is_file() or self.data_dir not in source.parents:
            raise AIServiceError("EXTRACTION_FAILED", "The staged admission-order PDF is unavailable")
        return source

    def _provider_instance(self) -> AIProvider:
        if not self.config.enabled:
            raise AIServiceError("AI_DISABLED", "AI extraction is disabled; deterministic extraction remains available")
        if not self.config.api_key and self._provider is None:
            key_name = "GROQ_API_KEY" if self.config.provider == "groq" else "OPENAI_API_KEY"
            raise AIServiceError(self.config.missing_key_code, f"{key_name} is not configured")
        try:
            return self._provider or build_provider(self.config)
        except AIProviderError as error:
            raise AIServiceError(error.code, str(error)) from error

    def _insert_job(self, *, intake: dict[str, Any], actor_id: str, document_hash: str,
                    status: str, cache_hit_of: Optional[str] = None, copied: Optional[dict[str, Any]] = None) -> str:
        job_id, now = new_id(), utc_now()
        copied = copied or {}
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO ai_jobs
                (id,intake_id,case_id,document_id,document_hash,task_type,provider,model,prompt_version,schema_version,
                 status,input_tokens,output_tokens,api_calls,latency_ms,actual_cost,estimated_list_cost,estimated_cost,
                 started_at,completed_at,error_code,error_message,
                 parsed_result_json,validation_json,comparison_json,review_json,cache_hit_of,created_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job_id, intake["id"], intake.get("case_id"), intake.get("document_id"), document_hash, TASK_TYPE,
                 self.config.provider, self.config.extraction_model, PROMPT_VERSION, SCHEMA_VERSION, status,
                 0, 0, 0, 0, 0.0 if status == "CACHE_HIT" else None,
                 0.0 if status == "CACHE_HIT" else None, 0.0 if status == "CACHE_HIT" else None,
                 now, now if status == "CACHE_HIT" else None,
                 "", "", _json(copied.get("parsed_result", {})), _json(copied.get("validation", {})),
                 _json(copied.get("comparison", {})), _json({}), cache_hit_of, actor_id),
            )
        return job_id

    @staticmethod
    def _extract_with_bounded_rate_retry(provider: AIProvider, *, system_prompt: str,
                                         document_text: str, model: str):
        failed_calls = 0
        failed_latency = 0
        # At most two rate-limit retries. Groq's rolling TPM window can require
        # a second provider-directed wait when a request nearly fills 8K TPM.
        for attempt in range(3):
            try:
                result = provider.extract(
                    system_prompt=system_prompt, document_text=document_text, model=model,
                )
                return replace(
                    result, api_calls=result.api_calls + failed_calls,
                    latency_ms=result.latency_ms + failed_latency,
                )
            except AIProviderError as error:
                failed_calls += error.api_calls
                failed_latency += error.latency_ms
                retry_after = error.retry_after_seconds
                if error.code != "API_RATE_LIMIT" or attempt >= 2 or retry_after is None or retry_after > 60:
                    error.api_calls = failed_calls
                    error.latency_ms = failed_latency
                    raise
                sleep(max(1.0, retry_after + 0.75))
        raise AssertionError("bounded retry loop must return or raise")

    def run(self, intake: dict[str, Any], actor_id: str, reanalyze: bool = False) -> dict[str, Any]:
        provider = self._provider_instance()
        source = self._source_path(intake)
        document_hash = sha256(source.read_bytes()).hexdigest()
        if not reanalyze:
            with self.database.connect() as connection:
                cached_row = find_cached_job(
                    connection, document_hash=document_hash, task_type=TASK_TYPE, provider=self.config.provider,
                    model=self.config.extraction_model, prompt_version=PROMPT_VERSION, schema_version=SCHEMA_VERSION,
                )
            if cached_row:
                cached = self._decode(cached_row)
                job_id = self._insert_job(
                    intake=intake, actor_id=actor_id, document_hash=document_hash, status="CACHE_HIT",
                    cache_hit_of=cached["id"], copied=cached,
                )
                with self.database.transaction() as connection:
                    self.database.audit(connection, actor_id, "AI_EXTRACTION_CACHE_HIT", "ai_job", job_id,
                                        intake.get("case_id"), after={"cached_job_id": cached["id"]},
                                        title="AI admission extraction reused from cache")
                return self.get_job(job_id) or {}

        extracted = intake.get("extracted", {})
        plan = plan_inputs(
            extracted, provider=self.config.provider,
            direct_token_limit=self.config.direct_document_token_limit,
            chunk_target_tokens=self.config.chunk_target_tokens,
        )
        page_count = len(extracted.get("document", {}).get("pages", []))
        if not plan.document_text.strip():
            raise AIServiceError("EXTRACTION_FAILED", "No page-aware admission-order text is available")
        if len(plan.document_text) > self.config.max_document_characters:
            raise AIServiceError("DOCUMENT_TOO_LARGE", "The admission-order text exceeds the configured AI input limit")
        job_id = self._insert_job(intake=intake, actor_id=actor_id, document_hash=document_hash, status="RUNNING")
        try:
            provider_results = []
            chunk_errors = []
            for index, chunk in enumerate(plan.chunks, 1):
                chunk_text = chunk
                if len(plan.chunks) > 1:
                    first_page, last_page = plan.page_ranges[index - 1]
                    chunk_text = (
                        f"DOCUMENT CHUNK {index} OF {len(plan.chunks)}; ORIGINAL PDF PAGES "
                        f"{first_page}-{last_page}.\n\n{chunk}"
                    )
                try:
                    provider_results.append(self._extract_with_bounded_rate_retry(
                        provider,
                        system_prompt=SYSTEM_PROMPT, document_text=chunk_text, model=self.config.extraction_model,
                    ))
                except AIProviderError as error:
                    if error.code != "INVALID_STRUCTURED_RESPONSE":
                        raise
                    first_page, last_page = plan.page_ranges[index - 1]
                    chunk_errors.append({
                        "chunk": index, "pages": [first_page, last_page],
                        "code": error.code, "message": str(error),
                    })
                    provider_results.append(ProviderResult(
                        parsed=not_found_extraction(), provider=self.config.provider,
                        model=self.config.extraction_model, api_calls=max(1, error.api_calls),
                        latency_ms=error.latency_ms,
                    ))
            parsed_chunks = [AdmissionOrderExtraction.model_validate(result.parsed).model_dump(mode="json")
                             for result in provider_results]
            parsed, chunk_conflicts = merge_chunk_extractions(parsed_chunks)
            parsed_model = AdmissionOrderExtraction.model_validate(parsed)
            parsed = parsed_model.model_dump(mode="json")
            validation = validate_extraction(parsed, page_count)
            comparison = compare_extractions(extracted, parsed, validation, chunk_conflicts)
            comparison["input_plan"] = {
                "estimated_document_tokens": plan.estimated_document_tokens,
                "chunking_required": plan.chunking_required,
                "chunk_count": len(plan.chunks),
                "page_ranges": [list(item) for item in plan.page_ranges],
                "chunk_errors": chunk_errors,
            }
            input_tokens = sum(result.input_tokens for result in provider_results)
            output_tokens = sum(result.output_tokens for result in provider_results)
            api_calls = sum(result.api_calls for result in provider_results)
            latency_ms = sum(result.latency_ms for result in provider_results)
            response_model = provider_results[-1].model
            estimated_list_cost = estimate_cost(response_model, input_tokens, output_tokens)
            actual_cost = 0.0 if self.config.provider == "groq" and self.config.billing_mode == "free" else None
            now = utc_now()
            job_status = "SUCCEEDED" if validation["status"] == "VALID" and not chunk_errors else "REVIEW_REQUIRED"
            error_code = "" if job_status == "SUCCEEDED" else "PARTIAL_STRUCTURED_OUTPUT_FAILURE" if chunk_errors else "VALIDATION_FAILED"
            error_message = "" if job_status == "SUCCEEDED" else "One or more Groq chunks failed strict schema validation" if chunk_errors else "One or more AI fields failed deterministic validation and require review"
            with self.database.transaction() as connection:
                connection.execute(
                    """UPDATE ai_jobs SET status=?,provider=?,model=?,input_tokens=?,output_tokens=?,api_calls=?,
                    latency_ms=?,actual_cost=?,estimated_list_cost=?,estimated_cost=?,
                    completed_at=?,error_code=?,error_message=?,parsed_result_json=?,validation_json=?,comparison_json=? WHERE id=?""",
                    (job_status, provider_results[-1].provider, response_model, input_tokens,
                     output_tokens, api_calls, latency_ms, actual_cost, estimated_list_cost, estimated_list_cost,
                     now, error_code, error_message,
                     _json(parsed), _json(validation), _json(comparison), job_id),
                )
                self.database.audit(
                    connection, actor_id, "AI_ADMISSION_EXTRACTION_COMPLETED", "ai_job", job_id,
                    intake.get("case_id"), after={"intake_id": intake["id"], "model": response_model,
                    "prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION,
                    "input_tokens": input_tokens, "output_tokens": output_tokens, "api_calls": api_calls,
                    "latency_ms": latency_ms, "actual_cost": actual_cost,
                    "estimated_list_cost": estimated_list_cost, "validation_status": validation["status"]},
                    title="AI admission extraction completed",
                )
            return self.get_job(job_id) or {}
        except ValidationError as error:
            failure = AIServiceError("INVALID_STRUCTURED_RESPONSE", "AI output did not match the extraction schema")
            self._fail(job_id, intake, actor_id, failure)
            raise failure from error
        except AIProviderError as error:
            failure = AIServiceError(error.code, str(error))
            completed_calls = sum(result.api_calls for result in provider_results)
            completed_latency = sum(result.latency_ms for result in provider_results)
            self._fail(
                job_id, intake, actor_id, failure,
                api_calls=completed_calls + error.api_calls,
                latency_ms=completed_latency + error.latency_ms,
            )
            raise failure from error
        except AIServiceError as error:
            self._fail(job_id, intake, actor_id, error)
            raise
        except Exception as error:
            failure = AIServiceError("EXTRACTION_FAILED", f"AI extraction failed: {type(error).__name__}")
            self._fail(job_id, intake, actor_id, failure)
            raise failure from error

    def _fail(self, job_id: str, intake: dict[str, Any], actor_id: str, error: AIServiceError,
              *, api_calls: int = 0, latency_ms: int = 0) -> None:
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE ai_jobs SET status='FAILED',error_code=?,error_message=?,api_calls=?,latency_ms=?,actual_cost=?,completed_at=? WHERE id=?",
                (error.code, str(error), api_calls, latency_ms,
                 0.0 if self.config.provider == "groq" and self.config.billing_mode == "free" else None,
                 now, job_id),
            )
            self.database.audit(connection, actor_id, "AI_ADMISSION_EXTRACTION_FAILED", "ai_job", job_id,
                                intake.get("case_id"), after={"error_code": error.code},
                                title="AI admission extraction failed")

    def record_review(self, job_id: str, intake_id: str, review: dict[str, Any], actor_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if not job or job.get("intake_id") != intake_id:
            raise AIServiceError("EXTRACTION_FAILED", "AI comparison job is unavailable for this intake")
        with self.database.transaction() as connection:
            connection.execute("UPDATE ai_jobs SET review_json=? WHERE id=?", (_json(review), job_id))
            self.database.audit(connection, actor_id, "AI_COMPARISON_REVIEWED", "ai_job", job_id,
                                job.get("case_id"), after={"decisions": review},
                                title="AI admission comparison reviewed")
        return self.get_job(job_id) or {}
