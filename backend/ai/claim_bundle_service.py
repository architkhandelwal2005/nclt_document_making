"""Staged AI orchestration for real claim submissions and annexures.

The manual ``claims`` row remains canonical.  AI jobs create auditable
proposals, evidence and conflicts; only explicit review decisions update the
canonical claim.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, ValidationError

from database import CasefileDatabase, _from_json, _json, new_id, utc_now
from ai.claim_bundle_documents import ClaimDocumentError, PreparedDocument, page_batches, pages_text, prepare_document
from ai.config import AIConfig
from ai.prompts.claim_bundle import (
    ANNEXURE_PROMPT_VERSION, ANNEXURE_SYSTEM_PROMPT, CLASSIFY_PROMPT_VERSION,
    CLASSIFY_SYSTEM_PROMPT, FORM_PROMPT_VERSION, FORM_SYSTEM_PROMPT,
    RECONCILE_PROMPT_VERSION, RECONCILE_SYSTEM_PROMPT,
)
from ai.provider import AIProvider, AIProviderError
from ai.router import build_provider
from ai.schemas.claim_bundle import (
    AnnexureExtraction, ClaimBundleClassification, ClaimFormExtraction,
    ClaimReconcileExtraction, DOCUMENT_TYPES, SCHEMA_VERSION,
)
from ai.service import AdmissionAIService
from ai.usage import estimate_cost
from ai.validators.claim_bundle import build_inventory, parse_money, reconcile, validate_stage_facts


CORE_TYPES = {"CLAIM_FORM", "DECLARATION", "VERIFICATION", "AUTHORIZATION", "KYC"}
TASK_CLASSIFY = "CLAIM_BUNDLE_CLASSIFICATION"
TASK_FORM = "CLAIM_FORM_EXTRACTION"
TASK_ANNEXURE = "CLAIM_ANNEXURE_EXTRACTION"
TASK_RECONCILE = "CLAIM_RECONCILIATION"

CANONICAL_MAP = {
    "form.claim_form_type": "form_type", "form.claim_submission_date": "claim_submission_date",
    "form.claim_as_on_date": "claim_as_on_date", "form.claim_reference": "claim_reference",
    "form.creditor_name": "creditor_name", "form.creditor_legal_name": "creditor_name",
    "form.creditor_identifier": "creditor_identifier", "form.creditor_address": "address",
    "form.email": "email", "form.phone": "phone", "form.contact_person": "contact_person",
    "form.authorized_representative": "authorized_representative",
    "form.authorized_representative_designation": "authorized_representative_designation",
    "form.creditor_category": "creditor_category", "form.total_claimed": "claimed_amount",
    "form.principal_claimed": "principal_claimed", "form.interest_claimed": "interest_claimed",
    "form.other_amount_claimed": "other_amount_claimed", "form.nature_of_debt": "nature_of_debt",
    "form.basis_of_claim": "basis_of_claim", "form.facility_type": "facility_type",
    "form.secured_status_as_claimed": "secured_status", "form.security_description_as_claimed": "security_details",
    "form.bank_details": "bank_details", "annexure.facility.0.original_facility_amount": "original_facility_amount",
    "annexure.facility.0.facility_type": "facility_type",
    "annexure.facility.0.sanction_letter_reference": "sanction_letter_reference",
    "annexure.facility.0.sanction_date": "sanction_date", "annexure.facility.0.agreement_date": "agreement_date",
    "annexure.facility.0.contractual_interest_rate": "interest_rate",
}


class ClaimBundleError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _decoded_job(row: Any) -> dict[str, Any]:
    result = dict(row)
    for key in ("parsed_result_json", "validation_json", "comparison_json", "review_json"):
        result[key[:-5]] = _from_json(result.pop(key), {})
    result["cache_hit"] = result["status"] == "CACHE_HIT"
    return result


def _fact_items(value: Any, path: str = "") -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict) and {"value", "basis", "document_type", "page", "source_text"} <= set(value):
        yield path, value
    elif isinstance(value, dict) and {"name", "document_id", "bundle_file", "page", "source_text", "basis", "document_type"} <= set(value):
        yield path, {**value, "value": value["name"]}
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _fact_items(child, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _fact_items(child, f"{path}.{index}" if path else str(index))


class ClaimBundleService:
    def __init__(self, database: CasefileDatabase, data_dir: Path, config: AIConfig | None = None,
                 provider: AIProvider | None = None):
        self.database = database
        self.data_dir = Path(data_dir).resolve()
        self.config = config or AIConfig.from_environment()
        self._provider = provider

    def public_config(self) -> dict[str, Any]:
        return {**self.config.public(), "pipeline": "STAGED_CLAIM_BUNDLE_V1", "schema_version": SCHEMA_VERSION,
                "prompt_versions": [CLASSIFY_PROMPT_VERSION, FORM_PROMPT_VERSION, ANNEXURE_PROMPT_VERSION, RECONCILE_PROMPT_VERSION]}

    def _provider_instance(self) -> AIProvider:
        if not self.config.enabled:
            raise ClaimBundleError("AI_DISABLED", "AI is disabled; the complete manual Claims workflow remains available")
        if not self.config.api_key and self._provider is None:
            name = "GROQ_API_KEY" if self.config.provider == "groq" else "OPENAI_API_KEY"
            raise ClaimBundleError(self.config.missing_key_code, f"{name} is not configured")
        try:
            return self._provider or build_provider(self.config)
        except AIProviderError as exc:
            raise ClaimBundleError(exc.code, str(exc)) from exc

    def _claim_documents(self, case_id: str, claim_id: str, document_ids: list[str] | None = None) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            claim = connection.execute("SELECT id FROM claims WHERE id=? AND case_id=? AND archived_at IS NULL", (claim_id, case_id)).fetchone()
            if not claim:
                raise ClaimBundleError("CLAIM_NOT_FOUND", "Claim not found")
            rows = connection.execute(
                "SELECT * FROM documents WHERE case_id=? AND linked_type='claim' AND linked_id=? AND archived_at IS NULL ORDER BY created_at",
                (case_id, claim_id),
            ).fetchall()
            documents = [self.database.row(row) for row in rows]
        if document_ids:
            wanted = set(document_ids)
            documents = [item for item in documents if item["id"] in wanted]
            if {item["id"] for item in documents} != wanted:
                raise ClaimBundleError("DOCUMENT_NOT_FOUND", "One or more selected Claim documents are unavailable")
        if not documents:
            raise ClaimBundleError("NO_CLAIM_DOCUMENTS", "Upload at least one Claim document before creating a bundle")
        return documents

    def create_bundle(self, case_id: str, claim_id: str, actor_id: str, *, name: str = "",
                      document_ids: list[str] | None = None) -> dict[str, Any]:
        documents = self._claim_documents(case_id, claim_id, document_ids)
        try:
            prepared = [prepare_document(item, self.data_dir) for item in documents]
        except ClaimDocumentError as exc:
            raise ClaimBundleError(exc.code, str(exc)) from exc
        bundle_id, now = new_id(), utc_now()
        source_hash = sha256("|".join(item.sha256 for item in prepared).encode()).hexdigest()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO claim_bundles(id,case_id,claim_id,name,status,source_hash,created_by,updated_by,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (bundle_id, case_id, claim_id, name.strip() or f"Claim bundle {now[:10]}", "READY", source_hash,
                 actor_id, actor_id, now, now),
            )
            for position, item in enumerate(prepared, 1):
                extraction_status = "OCR_REVIEW_REQUIRED" if any(page.extraction_status == "OCR_REQUIRED" for page in item.pages) else "TEXT_EXTRACTED"
                connection.execute(
                    """INSERT INTO claim_bundle_documents(id,bundle_id,document_id,document_hash,page_count,extraction_status,position,created_at)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (new_id(), bundle_id, item.document_id, item.sha256, item.page_count, extraction_status, position, now),
                )
            self.database.audit(connection, actor_id, "CLAIM_BUNDLE_CREATED", "claim_bundle", bundle_id, case_id,
                                after={"claim_id": claim_id, "document_count": len(prepared)}, title="Claim bundle created")
        return self.get_bundle(case_id, claim_id, bundle_id)

    def list_bundles(self, case_id: str, claim_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM claim_bundles WHERE case_id=? AND claim_id=? ORDER BY created_at DESC", (case_id, claim_id)).fetchall()
            return [self._decode_bundle(dict(row)) for row in rows]

    @staticmethod
    def _decode_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
        for key in ("review_json", "inventory_json", "reconciliation_json", "query_suggestions_json"):
            fallback = [] if key in {"inventory_json", "query_suggestions_json"} else {}
            bundle[key[:-5]] = _from_json(bundle.pop(key, None), fallback)
        return bundle

    def get_bundle(self, case_id: str, claim_id: str, bundle_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM claim_bundles WHERE id=? AND case_id=? AND claim_id=?", (bundle_id, case_id, claim_id)).fetchone()
            if not row:
                raise ClaimBundleError("BUNDLE_NOT_FOUND", "Claim bundle not found")
            result = self._decode_bundle(dict(row))
            result["documents"] = [dict(item) for item in connection.execute(
                """SELECT cbd.*,d.name,d.category,d.storage_path FROM claim_bundle_documents cbd
                JOIN documents d ON d.id=cbd.document_id WHERE cbd.bundle_id=? ORDER BY cbd.position""", (bundle_id,)).fetchall()]
            result["segments"] = [dict(item) for item in connection.execute(
                "SELECT * FROM claim_bundle_segments WHERE bundle_id=? ORDER BY document_id,start_page", (bundle_id,)).fetchall()]
            result["conflicts"] = [self._decode_conflict(dict(item)) for item in connection.execute(
                "SELECT * FROM claim_conflicts WHERE bundle_id=? ORDER BY created_at", (bundle_id,)).fetchall()]
            result["jobs"] = [_decoded_job(item) for item in connection.execute(
                "SELECT * FROM ai_jobs WHERE bundle_id=? ORDER BY started_at", (bundle_id,)).fetchall()]
        return result

    @staticmethod
    def _decode_conflict(conflict: dict[str, Any]) -> dict[str, Any]:
        conflict["source_a"] = _from_json(conflict.pop("source_a_json"), {})
        conflict["source_b"] = _from_json(conflict.pop("source_b_json"), {})
        return conflict

    def _prepared_documents(self, bundle: dict[str, Any]) -> list[PreparedDocument]:
        documents = []
        for item in bundle["documents"]:
            document = {"id": item["document_id"], "name": item["name"], "storage_path": item["storage_path"]}
            try:
                documents.append(prepare_document(document, self.data_dir))
            except ClaimDocumentError as exc:
                raise ClaimBundleError(exc.code, str(exc)) from exc
        return documents

    def _prior_state(self, bundle: dict[str, Any]) -> dict[str, Any]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT field_name,value_json FROM claim_evidence_facts WHERE bundle_id=?", (bundle["id"],)).fetchall()
            source_bundle = bundle["id"]
            if not rows:
                previous = connection.execute(
                    """SELECT id FROM claim_bundles WHERE claim_id=? AND id<>? AND status NOT IN ('DRAFT','READY','ANALYZING')
                    ORDER BY updated_at DESC LIMIT 1""", (bundle["claim_id"], bundle["id"]),
                ).fetchone()
                if previous:
                    source_bundle = previous["id"]
                    rows = connection.execute("SELECT field_name,value_json FROM claim_evidence_facts WHERE bundle_id=?", (source_bundle,)).fetchall()
            conflicts = {row[0] for row in connection.execute("SELECT conflict_type FROM claim_conflicts WHERE bundle_id=?", (source_bundle,)).fetchall()}
        return {"bundle_id": source_bundle if rows else None,
                "facts": {row["field_name"]: _from_json(row["value_json"], None) for row in rows},
                "conflicts": conflicts}

    @staticmethod
    def _reanalysis_delta(previous: dict[str, Any], form: dict[str, Any], annexures: list[dict[str, Any]],
                          conflicts: list[dict[str, Any]]) -> dict[str, Any]:
        if not previous.get("bundle_id"):
            return {"baseline_bundle_id": None, "new_facts": [], "changed_facts": [], "removed_facts": [],
                    "new_conflicts": [], "resolved_conflicts": []}
        current = {path: fact.get("value") for path, fact in _fact_items(form, "form")}
        current.update({path: fact.get("value") for path, fact in _fact_items(annexures, "annexure")})
        prior = previous["facts"]
        present = lambda value: value not in (None, "")
        new = sorted(path for path, value in current.items() if present(value) and not present(prior.get(path)))
        changed = sorted(path for path, value in current.items() if present(value) and present(prior.get(path)) and str(value) != str(prior[path]))
        removed = sorted(path for path, value in prior.items() if present(value) and not present(current.get(path)))
        current_conflicts = {item["conflict_type"] for item in conflicts}
        return {"baseline_bundle_id": previous["bundle_id"], "new_facts": new, "changed_facts": changed,
                "removed_facts": removed, "new_conflicts": sorted(current_conflicts - previous["conflicts"]),
                "resolved_conflicts": sorted(previous["conflicts"] - current_conflicts)}

    def _task(self, *, bundle: dict[str, Any], document_hash: str, document_id: str | None,
              start_page: int | None, end_page: int | None, task_type: str, prompt_version: str,
              prompt: str, text: str, schema: type[BaseModel], schema_name: str,
              actor_id: str, reanalyze: bool) -> tuple[dict[str, Any], str, bool]:
        if not text.strip():
            raise ClaimBundleError("DOCUMENT_EXTRACTION_FAILED", "A staged Claim AI task has no locally extracted text")
        if len(text) > self.config.max_document_characters:
            raise ClaimBundleError("DOCUMENT_TOO_LARGE", "A staged Claim AI input exceeds the configured character limit")
        selection = f"{document_id or 'BUNDLE'}:{start_page or 0}:{end_page or 0}"
        fingerprint = sha256(f"{document_hash}|{selection}|{task_type}|{self.config.provider}|{self.config.extraction_model}|{prompt_version}|{SCHEMA_VERSION}".encode()).hexdigest()
        if not reanalyze:
            with self.database.connect() as connection:
                cached = connection.execute(
                    """SELECT * FROM ai_jobs WHERE input_fingerprint=? AND status IN ('SUCCEEDED','REVIEW_REQUIRED')
                    ORDER BY completed_at DESC LIMIT 1""", (fingerprint,),
                ).fetchone()
            if cached:
                cached_job = _decoded_job(cached)
                job_id, now = new_id(), utc_now()
                with self.database.transaction() as connection:
                    connection.execute(
                        """INSERT INTO ai_jobs(id,case_id,claim_id,bundle_id,document_id,document_hash,input_fingerprint,
                        page_start,page_end,task_type,provider,model,prompt_version,schema_version,status,started_at,completed_at,
                        parsed_result_json,validation_json,comparison_json,review_json,cache_hit_of,created_by)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (job_id, bundle["case_id"], bundle["claim_id"], bundle["id"], document_id, document_hash,
                         fingerprint, start_page, end_page, task_type, self.config.provider, self.config.extraction_model,
                         prompt_version, SCHEMA_VERSION, "CACHE_HIT", now, now, _json(cached_job["parsed_result"]),
                         _json(cached_job["validation"]), "{}", "{}", cached_job["id"], actor_id),
                    )
                return cached_job["parsed_result"], job_id, True

        provider = self._provider_instance()
        job_id, started = new_id(), utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO ai_jobs(id,case_id,claim_id,bundle_id,document_id,document_hash,input_fingerprint,
                page_start,page_end,task_type,provider,model,prompt_version,schema_version,status,started_at,
                parsed_result_json,validation_json,comparison_json,review_json,created_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job_id, bundle["case_id"], bundle["claim_id"], bundle["id"], document_id, document_hash, fingerprint,
                 start_page, end_page, task_type, self.config.provider, self.config.extraction_model, prompt_version,
                 SCHEMA_VERSION, "RUNNING", started, "{}", "{}", "{}", "{}", actor_id),
            )
        try:
            response = AdmissionAIService._extract_with_bounded_rate_retry(
                provider, system_prompt=prompt, document_text=text, model=self.config.extraction_model,
                output_schema=schema, schema_name=schema_name,
            )
            parsed = schema.model_validate(response.parsed).model_dump(mode="json")
            page_counts = {item["document_id"]: int(item["page_count"]) for item in bundle["documents"]}
            validation = validate_stage_facts(parsed, page_counts)
            status = "SUCCEEDED" if validation["status"] == "VALID" else "REVIEW_REQUIRED"
            estimated = estimate_cost(response.model, response.input_tokens, response.output_tokens)
            actual = 0.0 if self.config.provider == "groq" and self.config.billing_mode == "free" else None
            with self.database.transaction() as connection:
                connection.execute(
                    """UPDATE ai_jobs SET status=?,provider=?,model=?,input_tokens=?,output_tokens=?,api_calls=?,latency_ms=?,
                    actual_cost=?,estimated_list_cost=?,estimated_cost=?,completed_at=?,parsed_result_json=?,validation_json=? WHERE id=?""",
                    (status, response.provider, response.model, response.input_tokens, response.output_tokens,
                     response.api_calls, response.latency_ms, actual, estimated, estimated, utc_now(), _json(parsed), _json(validation), job_id),
                )
            return parsed, job_id, False
        except (AIProviderError, ValidationError) as exc:
            code = exc.code if isinstance(exc, AIProviderError) else "INVALID_STRUCTURED_RESPONSE"
            with self.database.transaction() as connection:
                connection.execute("UPDATE ai_jobs SET status='FAILED',completed_at=?,error_code=?,error_message=? WHERE id=?",
                                   (utc_now(), code, str(exc)[:1000], job_id))
            raise ClaimBundleError(code, "A staged Claim AI task failed; manual Claims remains available") from exc

    @staticmethod
    def _normalize_segments(items: list[dict[str, Any]], start: int, end: int) -> list[dict[str, Any]]:
        valid = []
        for item in sorted(items, key=lambda row: (int(row.get("start_page") or 0), int(row.get("end_page") or 0))):
            first, last = max(start, int(item.get("start_page") or start)), min(end, int(item.get("end_page") or end))
            if first <= last:
                item = dict(item); item["start_page"], item["end_page"] = first, last
                valid.append(item)
        normalized, cursor = [], start
        for item in valid:
            if item["end_page"] < cursor:
                continue
            if item["start_page"] > cursor:
                normalized.append({"start_page": cursor, "end_page": item["start_page"] - 1, "document_type": "UNKNOWN",
                                   "confidence": "NOT_FOUND", "title_text": "", "evidence_text": "No classification returned"})
            item["start_page"] = max(cursor, item["start_page"])
            normalized.append(item); cursor = item["end_page"] + 1
        if cursor <= end:
            normalized.append({"start_page": cursor, "end_page": end, "document_type": "UNKNOWN",
                               "confidence": "NOT_FOUND", "title_text": "", "evidence_text": "No classification returned"})
        return normalized

    def analyze(self, case_id: str, claim_id: str, bundle_id: str, actor_id: str, *, reanalyze: bool = False) -> dict[str, Any]:
        bundle = self.get_bundle(case_id, claim_id, bundle_id)
        documents = self._prepared_documents(bundle)
        prior_state = self._prior_state(bundle)
        all_segments: list[dict[str, Any]] = []
        cache_hits = 0
        with self.database.transaction() as connection:
            connection.execute("UPDATE claim_bundles SET status='ANALYZING',updated_at=?,updated_by=? WHERE id=?", (utc_now(), actor_id, bundle_id))
            connection.execute("DELETE FROM claim_bundle_segments WHERE bundle_id=?", (bundle_id,))
            connection.execute("DELETE FROM claim_evidence_facts WHERE bundle_id=?", (bundle_id,))
            connection.execute("DELETE FROM claim_conflicts WHERE bundle_id=?", (bundle_id,))

        try:
            for document in documents:
                for first, last, batch_text in page_batches(document):
                    parsed, job_id, cache_hit = self._task(
                        bundle=bundle, document_hash=document.sha256, document_id=document.document_id,
                        start_page=first, end_page=last, task_type=TASK_CLASSIFY, prompt_version=CLASSIFY_PROMPT_VERSION,
                        prompt=CLASSIFY_SYSTEM_PROMPT, text=batch_text, schema=ClaimBundleClassification,
                        schema_name="claim_bundle_classification", actor_id=actor_id, reanalyze=reanalyze,
                    )
                    cache_hits += int(cache_hit)
                    for item in self._normalize_segments(parsed.get("segments", []), first, last):
                        item.update({"document_id": document.document_id, "ai_job_id": job_id})
                        all_segments.append(item)
            now = utc_now()
            with self.database.transaction() as connection:
                for item in all_segments:
                    status = "REVIEW_REQUIRED" if item["document_type"] == "UNKNOWN" or item["confidence"] in {"LOW", "NOT_FOUND"} else "AI_CLASSIFIED"
                    connection.execute(
                        """INSERT INTO claim_bundle_segments(id,bundle_id,document_id,start_page,end_page,document_type,
                        classification_status,confidence,title_text,evidence_text,ai_job_id,created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (new_id(), bundle_id, item["document_id"], item["start_page"], item["end_page"],
                         item["document_type"], status, item["confidence"], item.get("title_text", ""),
                         item.get("evidence_text", ""), item["ai_job_id"], now),
                    )

            core_text_parts, core_selection = [], []
            by_document = {item.document_id: item for item in documents}
            for segment in all_segments:
                if segment["document_type"] in CORE_TYPES:
                    document = by_document[segment["document_id"]]
                    core_text_parts.append(pages_text(document, segment["start_page"], segment["end_page"]))
                    core_selection.append(f"{segment['document_id']}:{segment['start_page']}-{segment['end_page']}")
            if not core_text_parts:
                raise ClaimBundleError("CLAIM_FORM_NOT_FOUND", "No Claim Form/core pages were classified; correct page classifications and re-analyze")
            core_hash = sha256((bundle["source_hash"] + "|" + "|".join(core_selection)).encode()).hexdigest()
            form, _, hit = self._task(
                bundle=bundle, document_hash=core_hash, document_id=None, start_page=None, end_page=None,
                task_type=TASK_FORM, prompt_version=FORM_PROMPT_VERSION, prompt=FORM_SYSTEM_PROMPT,
                text="\n\n".join(core_text_parts), schema=ClaimFormExtraction, schema_name="claim_form_extraction",
                actor_id=actor_id, reanalyze=reanalyze,
            )
            cache_hits += int(hit)

            annexures: list[dict[str, Any]] = []
            for index, segment in enumerate(all_segments):
                if segment["document_type"] in CORE_TYPES or segment["document_type"] == "UNKNOWN":
                    continue
                document = by_document[segment["document_id"]]
                segment_text = pages_text(document, segment["start_page"], segment["end_page"])
                parsed, _, hit = self._task(
                    bundle=bundle, document_hash=document.sha256, document_id=document.document_id,
                    start_page=segment["start_page"], end_page=segment["end_page"], task_type=TASK_ANNEXURE,
                    prompt_version=ANNEXURE_PROMPT_VERSION, prompt=ANNEXURE_SYSTEM_PROMPT,
                    text=segment_text, schema=AnnexureExtraction, schema_name="claim_annexure_extraction",
                    actor_id=actor_id, reanalyze=reanalyze,
                )
                cache_hits += int(hit)
                annexures.append(parsed)

            inventory = build_inventory(all_segments)
            inventory_index = {item["document_type"]: item for item in inventory}
            for _, fact in _fact_items(annexures, "annexure"):
                kind, page = fact.get("document_type"), fact.get("page")
                if fact.get("value") in (None, "") or kind not in inventory_index:
                    continue
                entry = inventory_index[kind]
                entry["status"] = "FOUND"
                if page is not None and page not in entry["pages"]:
                    entry["pages"].append(page); entry["pages"].sort()
                excerpt = fact.get("source_text")
                if excerpt and excerpt not in entry["important_references"]:
                    entry["important_references"].append(excerpt)
            deterministic = reconcile(form, annexures, inventory)
            compact = json.dumps({"form": form, "annexures": annexures, "inventory": inventory,
                                  "deterministic_reconciliation": deterministic}, ensure_ascii=False)
            ai_reconcile, _, hit = self._task(
                bundle=bundle, document_hash=bundle["source_hash"], document_id=None, start_page=None, end_page=None,
                task_type=TASK_RECONCILE, prompt_version=RECONCILE_PROMPT_VERSION, prompt=RECONCILE_SYSTEM_PROMPT,
                text=compact, schema=ClaimReconcileExtraction,
                schema_name="claim_reconciliation", actor_id=actor_id, reanalyze=reanalyze,
            )
            cache_hits += int(hit)
            deterministic["ai_relationships"] = ai_reconcile.get("relationships", [])
            query_suggestions = list(ai_reconcile.get("query_suggestions", []))
            existing_subjects = {item["subject"] for item in query_suggestions}
            if any(item["conflict_type"] == "SECURITY_STATUS_CONFLICT" for item in deterministic["conflicts"]) and "Clarify security status" not in existing_subjects:
                query_suggestions.append({"subject": "Clarify security status", "text": "Please clarify the security status because the Claim Form and supporting facility/security documents differ.", "reason": "Security assertion conflicts with annexure evidence", "supporting_pages": []})
            if deterministic["status"] in {"SUPPORTING_CALCULATION_MISSING", "AMOUNT_MISMATCH"} and "Provide claim amount reconciliation" not in existing_subjects:
                query_suggestions.append({"subject": "Provide claim amount reconciliation", "text": "Please provide the detailed interest computation and supporting statement reconciling the final amount claimed as at the claim date.", "reason": "The final Claim total could not be fully reconciled to supporting numerical components", "supporting_pages": []})
            inventory_by_type = {item["document_type"]: item for item in inventory}
            for suggestion in ai_reconcile.get("missing_documents", []):
                if inventory_by_type.get(suggestion["document_type"], {}).get("status") != "FOUND":
                    inventory_by_type.setdefault(suggestion["document_type"], {"document_type": suggestion["document_type"], "status": "POSSIBLY_MISSING", "pages": [], "important_references": []})
                    inventory_by_type[suggestion["document_type"]]["missing_reason"] = suggestion["reason"]
                    inventory_by_type[suggestion["document_type"]]["suggestion_status"] = "REVIEW_REQUIRED"
            inventory = list(inventory_by_type.values())
            review = self._build_review(case_id, claim_id, form, deterministic, inventory, query_suggestions)
            review["reanalysis_delta"] = self._reanalysis_delta(prior_state, form, annexures, deterministic["conflicts"])
            self._persist_analysis(bundle, actor_id, form, annexures, deterministic, inventory, query_suggestions, review)
            result = self.get_bundle(case_id, claim_id, bundle_id)
            result["cache_hits_this_run"] = cache_hits
            return result
        except ClaimBundleError:
            with self.database.transaction() as connection:
                connection.execute("UPDATE claim_bundles SET status='REVIEW_REQUIRED',updated_at=?,updated_by=? WHERE id=?", (utc_now(), actor_id, bundle_id))
            raise

    def _build_review(self, case_id: str, claim_id: str, form: dict[str, Any], reconciliation: dict[str, Any],
                      inventory: list[dict[str, Any]], query_suggestions: list[dict[str, Any]]) -> dict[str, Any]:
        with self.database.connect() as connection:
            claim = self.database.row(connection.execute("SELECT * FROM claims WHERE id=? AND case_id=?", (claim_id, case_id)).fetchone())
        conflict_topics = {item["topic"] for item in reconciliation["conflicts"]}

        def item(path: str, fact: dict[str, Any], label: str, current_key: str | None = None) -> dict[str, Any]:
            current_key = current_key or CANONICAL_MAP.get(path)
            current = claim.get(current_key) if current_key else None
            ai_value = fact.get("value")
            state = "BOTH_EMPTY" if current in (None, "") and ai_value in (None, "") else "AI_ONLY" if current in (None, "") else "MANUAL_ONLY" if ai_value in (None, "") else "MATCH" if str(current).strip().lower() == str(ai_value).strip().lower() else "CONFLICT"
            return {"id": path, "label": label, "fact": fact, "current_value": current, "comparison_state": state,
                    "validation": "VALID" if fact.get("basis") != "NOT_FOUND" else "NOT_FOUND",
                    "conflict": any(word.lower() in " ".join(conflict_topics).lower() for word in label.split())}

        facilities = reconciliation.get("facility_evidence", [])
        securities = reconciliation.get("security_evidence", [])
        guarantees = reconciliation.get("guarantee_evidence", [])
        proceedings = reconciliation.get("proceedings", [])
        sections = {
            "A_CLAIM_SUMMARY": [item("form.claim_form_type", form["claim_form_type"], "Claim form"), item("form.creditor_category", form["creditor_category"], "Creditor category"), item("form.claim_submission_date", form["claim_submission_date"], "Claim submission date"), item("form.claim_as_on_date", form["claim_as_on_date"], "Claim as-on date"), item("form.corporate_debtor_name", form["corporate_debtor_name"], "Corporate debtor")],
            "B_CREDITOR": [item(f"form.{key}", form[key], label) for key, label in (("creditor_legal_name", "Creditor legal name"), ("creditor_identifier", "Creditor identifier"), ("creditor_address", "Creditor address"), ("email", "Email"), ("phone", "Phone"), ("contact_person", "Contact person"), ("authorized_representative", "Authorised representative"), ("authorized_representative_designation", "Representative designation"))],
            "C_AMOUNT_CLAIMED": [item(f"form.{key}", form[key], label) for key, label in (("total_claimed", "Total claimed"), ("principal_claimed", "Principal claimed"), ("interest_claimed", "Interest claimed"), ("other_amount_claimed", "Other amount claimed"), ("currency", "Currency"))],
            "D_UNDERLYING_DEBT": [], "E_SECURITY_GUARANTEE": [], "F_DEFAULT_LITIGATION_AWARD": [],
            "G_DOCUMENT_INVENTORY": inventory, "H_DISCREPANCIES": reconciliation["conflicts"],
            "I_MISSING_QUERY_SUGGESTIONS": query_suggestions,
        }
        if facilities:
            for key, label in (("facility_type", "Facility type"), ("original_facility_amount", "Original facility amount"), ("sanction_letter_reference", "Sanction reference"), ("sanction_date", "Sanction date"), ("agreement_date", "Agreement date"), ("contractual_interest_rate", "Contractual interest rate"), ("default_interest_rate", "Default interest rate"), ("repayment_terms", "Repayment terms"), ("recall_notice_date", "Recall date"), ("facility_purpose", "Facility purpose")):
                sections["D_UNDERLYING_DEBT"].append(item(f"annexure.facility.0.{key}", facilities[0][key], label))
        sections["E_SECURITY_GUARANTEE"] = ([{"id": f"form.guarantor.{index}", "label": "Guarantor asserted in Claim Form", "fact": entry, "conflict": False} for index, entry in enumerate(form.get("guarantors_as_claimed", []))]
            + [{"id": f"security.{index}", "label": "Security evidence", "fact": entry, "conflict": True} for index, entry in enumerate(securities)]
            + [{"id": f"guarantee.{index}", "label": "Guarantee evidence", "fact": entry, "conflict": False} for index, entry in enumerate(guarantees)])
        sections["F_DEFAULT_LITIGATION_AWARD"] = [{"id": f"proceeding.{index}", "label": "Proceeding / award", "fact": entry, "conflict": True} for index, entry in enumerate(proceedings)]
        return {"sections": sections, "reconciliation_status": reconciliation["status"], "mandatory_review": True}

    def _persist_analysis(self, bundle: dict[str, Any], actor_id: str, form: dict[str, Any], annexures: list[dict[str, Any]],
                          reconciliation: dict[str, Any], inventory: list[dict[str, Any]],
                          query_suggestions: list[dict[str, Any]], review: dict[str, Any]) -> None:
        now = utc_now()
        with self.database.transaction() as connection:
            for layer, prefix, payload in (("CLAIMANT_ASSERTION", "form", form), ("SUPPORTING_EVIDENCE", "annexure", annexures)):
                for path, fact in _fact_items(payload, prefix):
                    connection.execute(
                        """INSERT INTO claim_evidence_facts(id,case_id,claim_id,bundle_id,layer,topic,field_name,value_json,
                        document_id,bundle_file,document_type,page,source_text,basis,confidence,review_status,created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (new_id(), bundle["case_id"], bundle["claim_id"], bundle["id"], layer, path.rsplit(".", 1)[-1], path,
                         json.dumps(fact.get("value"), ensure_ascii=False), fact.get("document_id"), fact.get("bundle_file") or "",
                         fact.get("document_type") or "UNKNOWN", fact.get("page"), fact.get("source_text") or "",
                         fact.get("basis") or "NOT_FOUND", fact.get("confidence") or "NOT_FOUND", "PROPOSED", now),
                    )
            for conflict in reconciliation["conflicts"]:
                connection.execute(
                    """INSERT INTO claim_conflicts(id,case_id,claim_id,bundle_id,conflict_type,topic,source_a_json,source_b_json,
                    status,user_resolution,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (new_id(), bundle["case_id"], bundle["claim_id"], bundle["id"], conflict["conflict_type"], conflict["topic"],
                     _json(conflict["source_a"]), _json(conflict["source_b"]), "OPEN", "", "", now, now),
                )
            has_ocr = any(item["extraction_status"] == "OCR_REVIEW_REQUIRED" for item in bundle["documents"])
            status = "REVIEW_REQUIRED" if reconciliation["conflicts"] or has_ocr else "READY_FOR_CONFIRMATION"
            connection.execute(
                """UPDATE claim_bundles SET status=?,review_json=?,inventory_json=?,reconciliation_json=?,query_suggestions_json=?,
                updated_at=?,updated_by=? WHERE id=?""",
                (status, _json(review), _json(inventory), _json(reconciliation), _json(query_suggestions), now, actor_id, bundle["id"]),
            )
            self.database.audit(connection, actor_id, "CLAIM_BUNDLE_ANALYZED", "claim_bundle", bundle["id"], bundle["case_id"],
                                after={"claim_id": bundle["claim_id"], "status": status, "conflict_count": len(reconciliation["conflicts"])},
                                title="Claim bundle analysis ready for mandatory review")

    def correct_segments(self, case_id: str, claim_id: str, bundle_id: str, corrections: list[dict[str, Any]], actor_id: str) -> dict[str, Any]:
        self.get_bundle(case_id, claim_id, bundle_id)
        with self.database.transaction() as connection:
            for correction in corrections:
                kind = str(correction.get("document_type") or "")
                if kind not in DOCUMENT_TYPES:
                    raise ClaimBundleError("INVALID_DOCUMENT_TYPE", "Unsupported Claim document classification")
                row = connection.execute("SELECT id FROM claim_bundle_segments WHERE id=? AND bundle_id=?", (correction.get("segment_id"), bundle_id)).fetchone()
                if not row:
                    raise ClaimBundleError("SEGMENT_NOT_FOUND", "Claim bundle segment not found")
                connection.execute("UPDATE claim_bundle_segments SET user_document_type=?,corrected_by=?,corrected_at=?,classification_status='USER_CORRECTED' WHERE id=?",
                                   (kind, actor_id, utc_now(), row["id"]))
        return self.get_bundle(case_id, claim_id, bundle_id)

    def decide_suggestion(self, case_id: str, claim_id: str, bundle_id: str, index: int,
                          action: str, actor_id: str) -> dict[str, Any]:
        bundle = self.get_bundle(case_id, claim_id, bundle_id)
        if action not in {"ADDED_TO_QUERY", "IGNORED", "NOT_REQUIRED"}:
            raise ClaimBundleError("INVALID_SUGGESTION_ACTION", "Unsupported suggestion action")
        suggestions = list(bundle.get("query_suggestions") or [])
        if not 0 <= index < len(suggestions):
            raise ClaimBundleError("SUGGESTION_NOT_FOUND", "Claim query suggestion not found")
        suggestions[index] = {**suggestions[index], "review_action": action, "reviewed_by": actor_id,
                              "reviewed_at": utc_now()}
        with self.database.transaction() as connection:
            connection.execute("UPDATE claim_bundles SET query_suggestions_json=?,updated_at=?,updated_by=? WHERE id=?",
                               (_json(suggestions), utc_now(), actor_id, bundle_id))
            self.database.audit(connection, actor_id, "CLAIM_AI_SUGGESTION_REVIEWED", "claim_bundle", bundle_id, case_id,
                                after={"claim_id": claim_id, "suggestion_index": index, "action": action},
                                title="Claim AI query suggestion reviewed")
        return self.get_bundle(case_id, claim_id, bundle_id)

    def review(self, case_id: str, claim_id: str, bundle_id: str, decisions: list[dict[str, Any]], actor_id: str,
               *, accept_all_non_conflicting: bool = False, confirm: bool = False,
               conflict_resolutions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        bundle = self.get_bundle(case_id, claim_id, bundle_id)
        if not bundle.get("review"):
            raise ClaimBundleError("ANALYSIS_NOT_READY", "Analyze the Claim bundle before review")
        evidence = {}
        with self.database.connect() as connection:
            for row in connection.execute("SELECT * FROM claim_evidence_facts WHERE bundle_id=?", (bundle_id,)).fetchall():
                item = dict(row); item["value"] = _from_json(item.pop("value_json"), None); evidence[item["field_name"]] = item
        conflict_fields: set[str] = set()
        for conflict in bundle["conflicts"]:
            kind = conflict["conflict_type"]
            tokens = {
                "SECURITY_STATUS_CONFLICT": ("security", "secured"),
                "CLAIM_TOTAL_MISMATCH": ("total_claimed", "principal_claimed", "interest_claimed", "other_amount_claimed"),
                "CLAIM_VS_LEDGER_MISMATCH": ("total_claimed", "amount_components"),
                "AWARD_VS_CLAIM_RECONCILIATION": ("total_claimed", "award_amount"),
                "INTEREST_RATE_CONFLICT": ("interest_rate",),
                "GUARANTOR_CONFLICT": ("guarantor",),
            }.get(kind, ())
            conflict_fields.update(path for path in evidence if any(token in path for token in tokens))
        selected = {str(item.get("field")): item for item in decisions if item.get("field")}
        if accept_all_non_conflicting:
            for path, item in evidence.items():
                if item["value"] not in (None, "") and path not in conflict_fields and path in CANONICAL_MAP:
                    selected.setdefault(path, {"field": path, "action": "ACCEPT"})
        allowed_actions = {"ACCEPT", "KEEP_CURRENT", "EDIT", "REJECT_AI"}
        updates: dict[str, Any] = {}
        now = utc_now()
        with self.database.transaction() as connection:
            for path, decision in selected.items():
                action = decision.get("action")
                if action not in allowed_actions or path not in evidence:
                    raise ClaimBundleError("INVALID_REVIEW_DECISION", "A Claim AI review decision is invalid")
                fact = evidence[path]
                reviewed_value = decision.get("value") if action == "EDIT" else fact["value"] if action == "ACCEPT" else None
                connection.execute(
                    "UPDATE claim_evidence_facts SET review_status=?,reviewed_value_json=?,reviewed_by=?,reviewed_at=? WHERE id=?",
                    (action, json.dumps(reviewed_value, ensure_ascii=False) if reviewed_value is not None else None, actor_id, now, fact["id"]),
                )
                target = CANONICAL_MAP.get(path)
                if confirm and target and action in {"ACCEPT", "EDIT"}:
                    if target in {"claimed_amount", "principal_claimed", "interest_claimed", "other_amount_claimed", "original_facility_amount", "interest_rate"}:
                        amount = parse_money(reviewed_value)
                        if amount is None:
                            raise ClaimBundleError("INVALID_REVIEW_VALUE", f"{target} must be a valid number")
                        reviewed_value = float(amount)
                    updates[target] = reviewed_value
            for resolution in conflict_resolutions or []:
                row = connection.execute("SELECT id FROM claim_conflicts WHERE id=? AND bundle_id=?", (resolution.get("conflict_id"), bundle_id)).fetchone()
                if not row:
                    raise ClaimBundleError("CONFLICT_NOT_FOUND", "Claim conflict not found")
                status = str(resolution.get("status") or "OPEN")
                if status not in {"OPEN", "RESOLVED", "NOT_APPLICABLE"}:
                    raise ClaimBundleError("INVALID_CONFLICT_RESOLUTION", "Unsupported conflict status")
                connection.execute("UPDATE claim_conflicts SET status=?,user_resolution=?,notes=?,resolved_by=?,resolved_at=?,updated_at=? WHERE id=?",
                                   (status, str(resolution.get("resolution") or ""), str(resolution.get("notes") or ""), actor_id if status != "OPEN" else None, now if status != "OPEN" else None, now, row["id"]))
            if confirm:
                if updates:
                    updates["supporting_evidence_json"] = _json({"bundle_id": bundle_id, "confirmed_fields": sorted(updates)})
                    updates["reconciliation_json"] = _json(bundle["reconciliation"])
                    updates["updated_by"], updates["updated_at"] = actor_id, now
                    connection.execute(f"UPDATE claims SET {', '.join(f'{key}=?' for key in updates)} WHERE id=? AND case_id=?", (*updates.values(), claim_id, case_id))
                open_conflicts = connection.execute("SELECT COUNT(*) FROM claim_conflicts WHERE bundle_id=? AND status='OPEN'", (bundle_id,)).fetchone()[0]
                status = "CONFIRMED_WITH_OPEN_ISSUES" if open_conflicts else "CONFIRMED"
                connection.execute("UPDATE claim_bundles SET status=?,confirmed_at=?,confirmed_by=?,updated_at=?,updated_by=? WHERE id=?",
                                   (status, now, actor_id, now, actor_id, bundle_id))
                self.database.audit(connection, actor_id, "CLAIM_BUNDLE_CONFIRMED", "claim_bundle", bundle_id, case_id,
                                    after={"claim_id": claim_id, "updated_fields": sorted(updates), "open_conflicts": open_conflicts},
                                    title="Reviewed Claim bundle values confirmed")
        return self.get_bundle(case_id, claim_id, bundle_id)
