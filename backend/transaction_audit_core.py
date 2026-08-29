"""Manual, evidence-led transaction-audit and avoidance workflow (CIRP-077--084)."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from database import CasefileDatabase, _json, new_id, utc_now
from workflow import EventEngine
from transaction_auditor_quotation import render_transaction_auditor_quotation


FINDING_CLASSIFICATIONS = {
    "PREFERENTIAL", "UNDERVALUE", "EXTORTIONATE", "FRAUDULENT_WRONGFUL", "RELATED_PARTY",
    "SUSPICIOUS_UNCLASSIFIED", "FINANCIAL_STATEMENT_OBSERVATION", "DEBTOR_RECOVERY", "INVENTORY",
    "CASH", "LOAN_ADVANCE", "NON_AVOIDANCE_OBSERVATION", "OTHER",
}
WORKSTREAM_TYPES = {"PREFERENTIAL", "UNDERVALUE", "EXTORTIONATE", "FRAUDULENT_WRONGFUL", "RELATED_PARTY"}
WORKSTREAM_STATUSES = {"NOT_STARTED", "DATA_PENDING", "IN_PROGRESS", "REVIEW_COMPLETE", "REVIEW_COMPLETE_NO_FINDING", "FINDINGS_RECORDED", "LEGAL_REVIEW", "CLOSED"}


def _paise(value: Any) -> int:
    return int((Decimal(str(value or 0)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class TransactionAuditCore:
    """Keeps auditor work, RP/legal review, and avoidance actions distinct."""
    def __init__(self, store: CasefileDatabase): self.store = store

    def _emit(self, case_id: str, event: str, actor: str, source: str, metadata: Optional[Dict[str, Any]] = None):
        return EventEngine(self.store).record_event(case_id, event, utc_now()[:10], actor, source_type="transaction_audit", source_id=source, metadata=metadata or {}, idempotency_key=f"ta:{event}:{source}")

    def _row(self, connection, table: str, case_id: str, record_id: str):
        row = connection.execute(f"SELECT * FROM {table} WHERE id=? AND case_id=?", (record_id, case_id)).fetchone()
        if not row: raise KeyError("Transaction-audit record not found")
        return row

    def engagement(self, case_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        key, now = str(payload.get("idempotency_key") or f"auto:{new_id()}"), utc_now()
        with self.store.transaction() as c:
            self.store.ensure_case(c, case_id)
            if key and (old := c.execute("SELECT * FROM transaction_audit_engagements WHERE case_id=? AND idempotency_key=?", (case_id,key)).fetchone()): return dict(old) | {"idempotent_replay":True}
            if payload.get("auditor_contact_id") and not c.execute("SELECT 1 FROM contacts WHERE id=? AND archived_at IS NULL", (payload["auditor_contact_id"],)).fetchone(): raise ValueError("Auditor contact not found")
            record_id=new_id(); c.execute("""INSERT INTO transaction_audit_engagements(id,case_id,auditor_contact_id,status,quotation_request_id,selection_record_id,coc_meeting_id,coc_resolution_id,appointment_date,scope_confirmed_date,audit_period_from,audit_period_to,draft_report_due_date,final_report_due_date,remarks,data_json,idempotency_key,created_by,updated_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (record_id,case_id,payload.get("auditor_contact_id"),payload.get("status","DRAFT"),payload.get("quotation_request_id"),payload.get("selection_record_id"),payload.get("coc_meeting_id"),payload.get("coc_resolution_id"),payload.get("appointment_date"),payload.get("scope_confirmed_date"),payload.get("audit_period_from"),payload.get("audit_period_to"),payload.get("draft_report_due_date"),payload.get("final_report_due_date"),payload.get("remarks", ""),_json(payload.get("data",{})),key,actor,actor,now,now))
            self.store.audit(c,actor,"TRANSACTION_AUDIT_ENGAGEMENT_CREATED","transaction_audit_engagement",record_id,case_id,after=payload,title="Transaction audit engagement created")
        self._emit(case_id,"TRANSACTION_AUDIT_REQUIRED",actor,record_id); return self.get(case_id,"transaction_audit_engagements",record_id)

    def get(self, case_id: str, table: str, record_id: str) -> Dict[str, Any]:
        with self.store.connect() as c: return dict(self._row(c,table,case_id,record_id))

    def scope(self, case_id: str, engagement_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        if str(payload.get("status","REVIEW_REQUIRED")).upper()=="CONFIRMED" and not payload.get("professional_confirmed"): raise ValueError("Professional confirmation is required for a confirmed review scope")
        now, record_id=utc_now(),new_id(); key=str(payload.get("idempotency_key") or f"auto:{record_id}")
        with self.store.transaction() as c:
            self._row(c,"transaction_audit_engagements",case_id,engagement_id)
            replay=c.execute("SELECT * FROM transaction_review_scopes WHERE case_id=? AND idempotency_key=?",(case_id,key)).fetchone()
            if replay: return dict(replay) | {"idempotent_replay": True}
            c.execute("""INSERT INTO transaction_review_scopes(id,case_id,engagement_id,review_type,counterparty_scope,period_from,period_to,anchor_date,basis,legal_reference_text,status,confirmed_by,confirmed_at,data_json,idempotency_key,created_by,updated_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(record_id,case_id,engagement_id,str(payload["review_type"]).upper(),payload.get("counterparty_scope","ALL"),payload.get("period_from"),payload.get("period_to"),payload.get("anchor_date"),payload.get("basis",""),payload.get("legal_reference_text",""),payload.get("status","REVIEW_REQUIRED"),actor if payload.get("professional_confirmed") else None,now if payload.get("professional_confirmed") else None,_json(payload.get("data",{})),key,actor,actor,now,now))
        self._emit(case_id,"TRANSACTION_REVIEW_SCOPE_CONFIRMED" if payload.get("professional_confirmed") else "TRANSACTION_REVIEW_SCOPE_DRAFTED",actor,record_id); return self.get(case_id,"transaction_review_scopes",record_id)

    def document_requirement(self, case_id: str, engagement_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        now,record_id=utc_now(),new_id()
        with self.store.transaction() as c:
            self._row(c,"transaction_audit_engagements",case_id,engagement_id)
            link=payload.get("management_requisition_record_id")
            if link: self._row(c,"phase4_records",case_id,link)
            c.execute("""INSERT INTO transaction_audit_document_requirements(id,case_id,engagement_id,category,description,period_from,period_to,requested_from,requested_date,required_date,status,management_requisition_record_id,auditor_request_reference,rp_remarks,auditor_remarks,deficiency,data_json,idempotency_key,created_by,updated_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(record_id,case_id,engagement_id,payload["category"],payload.get("description",""),payload.get("period_from"),payload.get("period_to"),payload.get("requested_from",""),payload.get("requested_date"),payload.get("required_date"),payload.get("status","REQUESTED"),link,payload.get("auditor_request_reference",""),payload.get("rp_remarks",""),payload.get("auditor_remarks",""),payload.get("deficiency",""),_json(payload.get("data",{})),payload.get("idempotency_key") or f"auto:{record_id}",actor,actor,now,now))
        self._emit(case_id,"TRANSACTION_AUDIT_DOCUMENT_REQUESTED",actor,record_id); return self.get(case_id,"transaction_audit_document_requirements",record_id)

    def requirement_status(self, case_id: str, requirement_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        status=str(payload["status"]).upper(); allowed={"REQUESTED","PARTLY_RECEIVED","RECEIVED","NOT_AVAILABLE","FOLLOW_UP_REQUIRED","REVIEWED","NOT_APPLICABLE"}
        if status not in allowed: raise ValueError("Unsupported audit document status")
        with self.store.transaction() as c:
            self._row(c,"transaction_audit_document_requirements",case_id,requirement_id)
            for doc in payload.get("document_ids",[]):
                if not c.execute("SELECT 1 FROM documents WHERE id=? AND case_id=? AND archived_at IS NULL",(doc,case_id)).fetchone(): raise ValueError("Document does not belong to this case")
                c.execute("INSERT OR IGNORE INTO transaction_audit_requirement_documents(id,case_id,requirement_id,document_id,created_by,created_at) VALUES (?,?,?,?,?,?)",(new_id(),case_id,requirement_id,doc,actor,utc_now()))
            c.execute("UPDATE transaction_audit_document_requirements SET status=?,received_date=?,followup_count=followup_count+?,reviewed_by=?,reviewed_at=?,updated_by=?,updated_at=? WHERE id=?",(status,payload.get("received_date"),int(status=="FOLLOW_UP_REQUIRED"),actor if status=="REVIEWED" else None,utc_now() if status=="REVIEWED" else None,actor,utc_now(),requirement_id))
        self._emit(case_id,"TRANSACTION_AUDIT_DOCUMENT_RECEIVED" if status in {"RECEIVED","PARTLY_RECEIVED"} else "TRANSACTION_AUDIT_DATA_GAP_RECORDED",actor,requirement_id); return self.get(case_id,"transaction_audit_document_requirements",requirement_id)

    def availability_summary(self, case_id: str, engagement_id: str) -> Dict[str,int]:
        with self.store.connect() as c:
            rows=c.execute("SELECT status,COUNT(*) n FROM transaction_audit_document_requirements WHERE case_id=? AND engagement_id=? GROUP BY status",(case_id,engagement_id)).fetchall()
        totals={"requested":0,"received":0,"partly_received":0,"not_available":0,"follow_up_required":0,"reviewed":0}
        for r in rows:
            key=r["status"].lower(); totals["requested"]+=r["n"];
            if key in totals: totals[key]=r["n"]
        return totals

    def related_party(self, case_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        now,record_id=utc_now(),new_id()
        with self.store.transaction() as c:
            self.store.ensure_case(c,case_id)
            if not c.execute("SELECT 1 FROM contacts WHERE id=? AND archived_at IS NULL",(payload["contact_id"],)).fetchone(): raise ValueError("Contact not found")
            source_doc=payload.get("source_document_id")
            if source_doc and not c.execute("SELECT 1 FROM documents WHERE id=? AND case_id=?",(source_doc,case_id)).fetchone(): raise ValueError("Source document does not belong to this case")
            c.execute("INSERT INTO case_related_parties(id,case_id,contact_id,relationship_type,relationship_description,effective_from,effective_to,source,source_document_id,confirmed_status,remarks,created_by,updated_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(record_id,case_id,payload["contact_id"],payload["relationship_type"],payload.get("relationship_description",""),payload.get("effective_from"),payload.get("effective_to"),payload.get("source",""),source_doc,payload.get("confirmed_status","UNVERIFIED"),payload.get("remarks",""),actor,actor,now,now))
        return self.get(case_id,"case_related_parties",record_id)

    def confirm_related_party(self, case_id: str, record_id: str, status: str, actor: str) -> Dict[str, Any]:
        if status not in {"CONFIRMED","DISPUTED","REVIEW_REQUIRED"}: raise ValueError("Invalid related-party confirmation status")
        with self.store.transaction() as c:
            self._row(c,"case_related_parties",case_id,record_id); c.execute("UPDATE case_related_parties SET confirmed_status=?,confirmed_by=?,confirmed_at=?,updated_by=?,updated_at=? WHERE id=?",(status,actor,utc_now(),actor,utc_now(),record_id))
        self._emit(case_id,"RELATED_PARTY_STATUS_CONFIRMED",actor,record_id); return self.get(case_id,"case_related_parties",record_id)

    def workstream(self, case_id: str, engagement_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        now,record_id=utc_now(),new_id(); review_type=str(payload["review_type"]).upper(); status=str(payload.get("status","NOT_STARTED")).upper()
        if review_type not in WORKSTREAM_TYPES: raise ValueError("Unsupported transaction-review workstream")
        if status not in WORKSTREAM_STATUSES: raise ValueError("Unsupported transaction-review workstream status")
        with self.store.transaction() as c:
            self._row(c,"transaction_audit_engagements",case_id,engagement_id)
            scope_id=payload.get("scope_id")
            if scope_id: self._row(c,"transaction_review_scopes",case_id,scope_id)
            c.execute("INSERT INTO transaction_review_workstreams(id,case_id,engagement_id,scope_id,review_type,assigned_to,status,start_date,completion_date,review_conclusion,notes,created_by,updated_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(record_id,case_id,engagement_id,scope_id,review_type,payload.get("assigned_to",""),status,payload.get("start_date"),payload.get("completion_date"),payload.get("review_conclusion",""),payload.get("notes",""),actor,actor,now,now))
        return self.get(case_id,"transaction_review_workstreams",record_id)

    def complete_workstream(self, case_id: str, workstream_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        status=str(payload.get("status") or "REVIEW_COMPLETE").upper()
        if status not in {"REVIEW_COMPLETE","REVIEW_COMPLETE_NO_FINDING","CLOSED"}: raise ValueError("Use an explicit valid workstream completion status")
        if payload.get("professional_confirmed") is not True: raise ValueError("Professional confirmation is required to complete a review workstream")
        with self.store.transaction() as c:
            self._row(c,"transaction_review_workstreams",case_id,workstream_id); c.execute("UPDATE transaction_review_workstreams SET status=?,completion_date=?,review_conclusion=?,professional_confirmed_by=?,professional_confirmed_at=?,updated_by=?,updated_at=? WHERE id=?",(status,payload.get("completion_date") or utc_now()[:10],payload.get("review_conclusion",""),actor,utc_now(),actor,utc_now(),workstream_id))
        return self.get(case_id,"transaction_review_workstreams",workstream_id)

    def finding(self, case_id: str, engagement_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        now,record_id=utc_now(),new_id(); classification=str(payload["auditor_classification"]).upper()
        if classification not in FINDING_CLASSIFICATIONS: raise ValueError("Unsupported auditor finding classification")
        with self.store.transaction() as c:
            self._row(c,"transaction_audit_engagements",case_id,engagement_id)
            workstream=payload.get("workstream_id")
            if workstream: self._row(c,"transaction_review_workstreams",case_id,workstream)
            number=payload.get("finding_number") or f"TA-{c.execute('SELECT COUNT(*)+1 FROM transaction_findings WHERE case_id=?',(case_id,)).fetchone()[0]:03d}"
            c.execute("INSERT INTO transaction_findings(id,case_id,engagement_id,workstream_id,finding_number,auditor_classification,status,title,narrative,finding_amount_paise,transaction_amount_paise,estimated_impact_paise,amount_recoverable_paise,data_json,created_by,updated_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(record_id,case_id,engagement_id,workstream,number,classification,payload.get("status","DRAFT"),payload["title"],payload.get("narrative",""),_paise(payload.get("finding_amount")),_paise(payload.get("transaction_amount")),_paise(payload.get("estimated_impact")),_paise(payload.get("amount_recoverable")),_json(payload.get("data",{})),actor,actor,now,now))
        self._emit(case_id,"TRANSACTION_FINDING_CREATED",actor,record_id); return self.get(case_id,"transaction_findings",record_id)

    def finalize_finding(self, case_id: str, finding_id: str, actor: str) -> Dict[str, Any]:
        with self.store.transaction() as c:
            self._row(c,"transaction_findings",case_id,finding_id); c.execute("UPDATE transaction_findings SET status='AUDITOR_FINAL',auditor_finalized_by=?,auditor_finalized_at=?,updated_by=?,updated_at=? WHERE id=?",(actor,utc_now(),actor,utc_now(),finding_id))
        self._emit(case_id,"TRANSACTION_FINDING_FINALIZED",actor,finding_id); return self.get(case_id,"transaction_findings",finding_id)

    def finding_evidence(self, case_id: str, finding_id: str, payload: Dict[str, Any], actor: str) -> None:
        with self.store.transaction() as c:
            self._row(c,"transaction_findings",case_id,finding_id)
            document=c.execute("SELECT version FROM documents WHERE id=? AND case_id=? AND archived_at IS NULL",(payload["document_id"],case_id)).fetchone()
            if not document: raise ValueError("Evidence document does not belong to this case")
            source=payload.get("source_index_id")
            if source: self._row(c,"transaction_audit_source_index",case_id,source)
            c.execute("INSERT OR IGNORE INTO transaction_finding_evidence(id,case_id,finding_id,document_id,source_index_id,page_reference,exhibit_reference,document_version,remarks,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",(new_id(),case_id,finding_id,payload["document_id"],source,payload.get("page_reference",""),payload.get("exhibit_reference",""),document["version"],payload.get("remarks",""),actor,utc_now()))
        self._emit(case_id,"TRANSACTION_FINDING_EVIDENCE_LINKED",actor,finding_id)

    def review_finding(self, case_id: str, finding_id: str, layer: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        layer=layer.upper()
        if layer not in {"RP","LEGAL"}: raise ValueError("Review layer must be RP or LEGAL")
        with self.store.transaction() as c:
            self._row(c,"transaction_findings",case_id,finding_id)
            doc=payload.get("document_id")
            if doc and not c.execute("SELECT 1 FROM documents WHERE id=? AND case_id=?",(doc,case_id)).fetchone(): raise ValueError("Review document does not belong to this case")
            now=utc_now(); c.execute("INSERT INTO transaction_finding_reviews(id,case_id,finding_id,review_layer,status,opinion_text,document_id,reviewed_by,reviewed_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(finding_id,review_layer) DO UPDATE SET status=excluded.status,opinion_text=excluded.opinion_text,document_id=excluded.document_id,reviewed_by=excluded.reviewed_by,reviewed_at=excluded.reviewed_at",(new_id(),case_id,finding_id,layer,payload["status"],payload.get("opinion_text",""),doc,actor,now,now))
        self._emit(case_id,"TRANSACTION_FINDING_RP_REVIEWED" if layer=="RP" else "TRANSACTION_FINDING_LEGAL_REVIEWED",actor,finding_id); return {"finding_id":finding_id,"review_layer":layer,"status":payload["status"]}

    def avoidance_decision(self, case_id: str, engagement_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        if payload.get("professional_confirmed") is not True: raise ValueError("Professional confirmation is required for an avoidance decision")
        finding_ids=payload.get("finding_ids") or []
        if not finding_ids: raise ValueError("At least one finalized finding is required")
        with self.store.transaction() as c:
            self._row(c,"transaction_audit_engagements",case_id,engagement_id)
            for finding_id in finding_ids:
                finding=self._row(c,"transaction_findings",case_id,finding_id)
                if finding["status"] != "AUDITOR_FINAL": raise ValueError("Only finalized auditor findings may be placed before the RP")
                rp=c.execute("SELECT status FROM transaction_finding_reviews WHERE finding_id=? AND review_layer='RP'",(finding_id,)).fetchone()
                legal=c.execute("SELECT status FROM transaction_finding_reviews WHERE finding_id=? AND review_layer='LEGAL'",(finding_id,)).fetchone()
                if payload.get("decision")=="FILE_APPLICATION" and (not rp or rp["status"] not in {"ACCEPTED_FOR_LEGAL_REVIEW","COMPLETE"}): raise ValueError("Completed RP review is required before filing decision")
                if payload.get("decision")=="FILE_APPLICATION" and (not legal or legal["status"] not in {"COMPLETE","RECOMMEND_FILE"}): raise ValueError("Completed legal review is required before filing decision")
            now,record_id=utc_now(),new_id(); c.execute("INSERT INTO avoidance_decisions(id,case_id,engagement_id,status,decision,rationale,confirmed_by,confirmed_at,created_by,updated_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",(record_id,case_id,engagement_id,"CONFIRMED",payload.get("decision","NO_CURRENT_AVOIDANCE_DECISION"),payload.get("rationale",""),actor,now,actor,actor,now,now))
            for finding_id in finding_ids: c.execute("INSERT INTO avoidance_decision_findings(id,decision_id,finding_id,created_at) VALUES (?,?,?,?)",(new_id(),record_id,finding_id,now))
        self._emit(case_id,"AVOIDANCE_DECISION_CONFIRMED",actor,record_id); return self.get(case_id,"avoidance_decisions",record_id)

    def avoidance_application(self, case_id: str, decision_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        with self.store.transaction() as c:
            decision=self._row(c,"avoidance_decisions",case_id,decision_id)
            if decision["status"] != "CONFIRMED" or decision["decision"] != "FILE_APPLICATION": raise ValueError("A confirmed FILE_APPLICATION decision is required")
        application=self.store.create_module_record(case_id,"applications",{"application_type":"Avoidance Application","number":payload.get("ia_number",""),"filing_date":payload.get("filed_date"),"parties":payload.get("parties",""),"relief_sought":payload.get("relief_sought",""),"status":payload.get("status","draft")},actor)
        with self.store.transaction() as c:
            hearing_id=payload.get("hearing_id")
            if hearing_id and not c.execute("SELECT 1 FROM hearings WHERE id=? AND case_id=? AND archived_at IS NULL", (hearing_id,case_id)).fetchone(): raise ValueError("Hearing does not belong to this case")
            now,record_id=utc_now(),new_id(); c.execute("INSERT INTO avoidance_applications(id,case_id,decision_id,application_id,hearing_id,status,ia_number,filed_date,next_hearing_date,data_json,created_by,updated_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(record_id,case_id,decision_id,application["id"],hearing_id,payload.get("status","DRAFT").upper(),payload.get("ia_number",""),payload.get("filed_date"),payload.get("next_hearing_date"),_json(payload.get("data",{})),actor,actor,now,now))
            for row in c.execute("SELECT finding_id FROM avoidance_decision_findings WHERE decision_id=?",(decision_id,)).fetchall(): c.execute("INSERT INTO avoidance_application_findings(id,avoidance_application_id,finding_id,created_at) VALUES (?,?,?,?)",(new_id(),record_id,row["finding_id"],now))
        self._emit(case_id,"AVOIDANCE_APPLICATION_FILED" if payload.get("filed_date") else "AVOIDANCE_APPLICATION_CREATED",actor,record_id); return self.get(case_id,"avoidance_applications",record_id)

    # Quotation lifecycle ----------------------------------------------------
    def quotation_preview(self, case_id: str, engagement_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        """Create an editable, deterministic transaction-auditor invitation draft."""
        from phase4_core import Phase4Core
        with self.store.connect() as c:
            self._row(c, "transaction_audit_engagements", case_id, engagement_id)
            scopes = [dict(row) for row in c.execute(
                "SELECT * FROM transaction_review_scopes WHERE case_id=? AND engagement_id=? ORDER BY created_at",
                (case_id, engagement_id),
            ).fetchall()]
            recipient = None
            if payload.get("recipient_contact_id"):
                recipient = c.execute("SELECT name,email FROM contacts WHERE id=? AND archived_at IS NULL", (payload["recipient_contact_id"],)).fetchone()
                if not recipient:
                    raise ValueError("Recipient professional contact not found")
        case = self.store.get_case(case_id) or {}
        values = dict(payload) | {
            "corporate_debtor_name": payload.get("corporate_debtor_name") or case.get("name"),
            "registered_office_address": payload.get("registered_office_address") or case.get("registered_address"),
            "nclt_bench": payload.get("nclt_bench") or case.get("nclt_bench"),
            "admission_order_date": payload.get("admission_order_date") or case.get("order_date"),
            "cirp_commencement_date": payload.get("cirp_commencement_date") or case.get("commencement_date"),
            "recipient_name": payload.get("recipient_name") or (recipient["name"] if recipient else ""),
            "recipient_email": payload.get("recipient_email") or (recipient["email"] if recipient else ""),
            "confirmed_scopes": scopes,
        }
        rendered = render_transaction_auditor_quotation(values)
        return Phase4Core(self.store).create(case_id, "transaction_audit", "AUDITOR_QUOTATION_REQUEST", {
            "record_key": str(payload.get("recipient_contact_id") or values.get("recipient_email") or values["recipient_name"]),
            "status": "DRAFT", "idempotency_key": payload.get("idempotency_key"),
            "confidentiality_level": "RESTRICTED_TRANSACTION_AUDIT",
            "data": values | rendered | {"engagement_id": engagement_id, "template_source": "OFFICE_EMAIL_TRANSACTION_AUDITOR_QUOTATION"},
        }, actor)

    def issue_quotation(self, case_id: str, request_id: str, actor: str) -> Dict[str, Any]:
        from phase4_core import Phase4Core
        phase4 = Phase4Core(self.store); request = phase4.get(case_id, request_id)
        if request["domain"] != "transaction_audit" or request["record_type"] != "AUDITOR_QUOTATION_REQUEST":
            raise ValueError("Transaction-auditor quotation request not found")
        if request["status"] == "ISSUED":
            return request | {"idempotent_replay": True}
        if request["status"] != "DRAFT":
            raise ValueError("Only a draft quotation invitation may be issued")
        engagement_id = request["data"].get("engagement_id")
        if not engagement_id:
            raise ValueError("Quotation request is missing its transaction-audit engagement link")
        with self.store.connect() as c:
            scopes = c.execute("SELECT review_type,status,period_from,period_to FROM transaction_review_scopes WHERE case_id=? AND engagement_id=?", (case_id, engagement_id)).fetchall()
        # Re-validate immediately before issue, so an outdated browser draft cannot bypass scope confirmation.
        render_transaction_auditor_quotation(dict(request["data"]) | {"confirmed_scopes": [dict(row) for row in scopes]})
        data = request["data"]
        communication = self.store.create_module_record(case_id, "communications", {
            "channel": "Email", "direction": "OUTBOUND", "occurred_at": utc_now(), "sender": data.get("process_email", ""),
            "recipients": data.get("recipient_email", ""), "subject": data["subject"], "summary": data["body"],
            "delivery_status": "ISSUED", "linked_type": "phase4_record", "linked_id": request_id,
        }, actor)
        result = phase4.update(case_id, request_id, {"status": "ISSUED", "data": {"communication_id": communication["id"], "issued_at": utc_now()}}, actor)
        self._emit(case_id, "TRANSACTION_AUDITOR_QUOTATION_INVITED", actor, request_id, {"communication_id": communication["id"]})
        return result

    def record_quotation_dispatch(self, case_id: str, request_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        from phase4_core import Phase4Core
        phase4 = Phase4Core(self.store); request = phase4.get(case_id, request_id)
        if request["status"] != "ISSUED":
            raise ValueError("Issue the quotation invitation before recording dispatch")
        communication_id = request["data"].get("communication_id")
        if not communication_id:
            raise ValueError("Issued quotation communication is missing")
        proof = payload.get("proof_document_id")
        with self.store.connect() as c:
            if proof and not c.execute("SELECT 1 FROM documents WHERE id=? AND case_id=? AND archived_at IS NULL", (proof, case_id)).fetchone():
                raise ValueError("Dispatch proof does not belong to this case")
        self.store.update_module_record(case_id, "communications", communication_id, {"delivery_status": "DISPATCH_RECORDED", "proof_document_id": proof}, actor)
        return phase4.add_item(case_id, request_id, {"item_key": str(payload.get("idempotency_key") or "DISPATCH"), "status": "DISPATCH_RECORDED", "document_id": proof, "data": {"communication_id": communication_id, **payload}}, actor)

    def quotation_response(self, case_id: str, request_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        """Record, but do not automatically select, a professional's quotation."""
        from phase4_core import Phase4Core
        phase4 = Phase4Core(self.store); request = phase4.get(case_id, request_id)
        if request["domain"] != "transaction_audit" or request["record_type"] != "AUDITOR_QUOTATION_REQUEST":
            raise ValueError("Transaction-auditor quotation request not found")
        contact_id = payload.get("professional_contact_id") or request["data"].get("recipient_contact_id")
        with self.store.connect() as c:
            if not contact_id or not c.execute("SELECT 1 FROM contacts WHERE id=? AND archived_at IS NULL", (contact_id,)).fetchone():
                raise ValueError("Quotation professional contact not found")
        response = phase4.create(case_id, "transaction_audit", "AUDITOR_QUOTATION_RESPONSE", {
            "parent_record_id": request_id, "record_key": str(contact_id), "status": "RECEIVED",
            "document_id": payload.get("document_id"), "amount": payload.get("fee"), "tax_amount": payload.get("tax_amount"),
            "confidentiality_level": "RESTRICTED_TRANSACTION_AUDIT", "idempotency_key": payload.get("idempotency_key"),
            "data": {"professional_contact_id": contact_id, **payload},
        }, actor)
        self._emit(case_id, "TRANSACTION_AUDITOR_QUOTATION_RECEIVED", actor, response["id"])
        return response

    def select_auditor(self, case_id: str, engagement_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        from phase4_core import Phase4Core
        if payload.get("professional_confirmed") is not True:
            raise ValueError("Professional confirmation is required to select a transaction auditor")
        phase4 = Phase4Core(self.store); response = phase4.get(case_id, payload["quotation_response_id"])
        if response["domain"] != "transaction_audit" or response["record_type"] != "AUDITOR_QUOTATION_RESPONSE":
            raise ValueError("Transaction-auditor quotation response not found")
        contact_id = response["data"].get("professional_contact_id")
        comparison = phase4.create(case_id, "transaction_audit", "AUDITOR_QUOTATION_COMPARISON", {
            "parent_record_id": response["id"], "record_key": str(contact_id), "status": "SELECTED",
            "confidentiality_level": "RESTRICTED_TRANSACTION_AUDIT", "idempotency_key": payload.get("idempotency_key"),
            "data": {"selected_response_id": response["id"], "selection_rationale": payload.get("selection_rationale", ""), "confirmed_by": actor, "confirmed_at": utc_now()},
        }, actor)
        with self.store.transaction() as c:
            self._row(c, "transaction_audit_engagements", case_id, engagement_id)
            c.execute("UPDATE transaction_audit_engagements SET auditor_contact_id=?,selection_record_id=?,status='SELECTED',updated_by=?,updated_at=? WHERE id=? AND case_id=?", (contact_id,comparison["id"],actor,utc_now(),engagement_id,case_id))
        self._emit(case_id, "TRANSACTION_AUDITOR_SELECTED", actor, engagement_id, {"comparison_id": comparison["id"]})
        return self.get(case_id, "transaction_audit_engagements", engagement_id)

    def appoint_auditor(self, case_id: str, engagement_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        status = str(payload.get("status") or "APPOINTED").upper()
        if status not in {"APPOINTED", "ACTIVE"}: raise ValueError("Transaction-audit appointment status must be APPOINTED or ACTIVE")
        with self.store.transaction() as c:
            engagement = self._row(c, "transaction_audit_engagements", case_id, engagement_id)
            if not engagement["selection_record_id"]: raise ValueError("A recorded auditor selection is required before appointment")
            c.execute("UPDATE transaction_audit_engagements SET status=?,appointment_date=?,updated_by=?,updated_at=? WHERE id=? AND case_id=?", (status,payload.get("appointment_date") or engagement["appointment_date"],actor,utc_now(),engagement_id,case_id))
        self._emit(case_id, "TRANSACTION_AUDITOR_APPOINTED", actor, engagement_id)
        return self.get(case_id, "transaction_audit_engagements", engagement_id)

    # Registers and evidence -------------------------------------------------
    def bank_coverage(self, case_id: str, engagement_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        now, record_id = utc_now(), new_id(); status = str(payload.get("coverage_status") or "REVIEW_REQUIRED").upper()
        if status not in {"COMPLETE", "PARTIAL", "MISSING", "REVIEW_REQUIRED"}:
            raise ValueError("Unsupported bank coverage status")
        with self.store.transaction() as c:
            self._row(c, "transaction_audit_engagements", case_id, engagement_id)
            c.execute("""INSERT INTO transaction_audit_bank_coverages(id,case_id,engagement_id,bank_name,account_identifier_masked,account_type,required_period_from,required_period_to,available_period_from,available_period_to,coverage_status,remarks,data_json,created_by,updated_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (record_id,case_id,engagement_id,payload["bank_name"],payload.get("account_identifier_masked", ""),payload.get("account_type", ""),payload.get("required_period_from"),payload.get("required_period_to"),payload.get("available_period_from"),payload.get("available_period_to"),status,payload.get("remarks", ""),_json(payload.get("data", {})),actor,actor,now,now))
        return self.get(case_id, "transaction_audit_bank_coverages", record_id)

    def update_bank_coverage(self, case_id: str, coverage_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        allowed = {"account_identifier_masked", "account_type", "required_period_from", "required_period_to", "available_period_from", "available_period_to", "coverage_status", "remarks"}
        updates = {key: payload[key] for key in allowed if key in payload}
        if not updates and not payload.get("document_ids"):
            raise ValueError("Supply a coverage update or linked statement document")
        if "coverage_status" in updates:
            updates["coverage_status"] = str(updates["coverage_status"]).upper()
            if updates["coverage_status"] not in {"COMPLETE", "PARTIAL", "MISSING", "REVIEW_REQUIRED"}: raise ValueError("Unsupported bank coverage status")
        with self.store.transaction() as c:
            self._row(c, "transaction_audit_bank_coverages", case_id, coverage_id)
            for document_id in payload.get("document_ids", []):
                if not c.execute("SELECT 1 FROM documents WHERE id=? AND case_id=? AND archived_at IS NULL", (document_id, case_id)).fetchone(): raise ValueError("Bank statement document does not belong to this case")
                c.execute("INSERT OR IGNORE INTO transaction_audit_bank_documents(id,bank_coverage_id,case_id,document_id,created_at) VALUES (?,?,?,?,?)", (new_id(), coverage_id, case_id, document_id, utc_now()))
            if updates:
                updates.update({"updated_by": actor, "updated_at": utc_now()})
                c.execute(f"UPDATE transaction_audit_bank_coverages SET {', '.join(f'{key}=?' for key in updates)} WHERE id=? AND case_id=?", (*updates.values(),coverage_id,case_id))
        return self.get(case_id, "transaction_audit_bank_coverages", coverage_id)

    def source_index_entry(self, case_id: str, engagement_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        record_id, now = new_id(), utc_now()
        with self.store.transaction() as c:
            self._row(c, "transaction_audit_engagements", case_id, engagement_id)
            document_id = payload["document_id"]
            if not c.execute("SELECT 1 FROM documents WHERE id=? AND case_id=? AND archived_at IS NULL", (document_id,case_id)).fetchone(): raise ValueError("Source document does not belong to this case")
            c.execute("""INSERT INTO transaction_audit_source_index(id,case_id,engagement_id,document_id,source_category,source_reference,period_from,period_to,received_from,received_date,availability_status,relied_upon,remarks,data_json,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (record_id,case_id,engagement_id,document_id,payload["source_category"],payload.get("source_reference", ""),payload.get("period_from"),payload.get("period_to"),payload.get("received_from", ""),payload.get("received_date"),str(payload.get("availability_status") or "RECEIVED").upper(),int(bool(payload.get("relied_upon", False))),payload.get("remarks", ""),_json(payload.get("data", {})),actor,now))
        return self.get(case_id, "transaction_audit_source_index", record_id)

    def transaction(self, case_id: str, engagement_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        record_id, now = new_id(), utc_now()
        with self.store.transaction() as c:
            self._row(c, "transaction_audit_engagements", case_id, engagement_id)
            workstream_id = payload.get("workstream_id")
            related_party_id = payload.get("related_party_id")
            if workstream_id: self._row(c, "transaction_review_workstreams", case_id, workstream_id)
            if related_party_id: self._row(c, "case_related_parties", case_id, related_party_id)
            c.execute("""INSERT INTO transaction_audit_transactions(id,case_id,engagement_id,workstream_id,transaction_date,narration,transaction_amount_paise,counterparty_contact_id,related_party_id,source_reference,status,data_json,created_by,updated_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (record_id,case_id,engagement_id,workstream_id,payload.get("transaction_date"),payload.get("narration", ""),_paise(payload.get("transaction_amount")),payload.get("counterparty_contact_id"),related_party_id,payload.get("source_reference", ""),payload.get("status", "UNREVIEWED"),_json(payload.get("data", {})),actor,actor,now,now))
        return self.get(case_id, "transaction_audit_transactions", record_id)

    def finding_party(self, case_id: str, finding_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        record_id, now = new_id(), utc_now()
        with self.store.transaction() as c:
            self._row(c, "transaction_findings", case_id, finding_id)
            if not c.execute("SELECT 1 FROM contacts WHERE id=? AND archived_at IS NULL", (payload["contact_id"],)).fetchone(): raise ValueError("Finding party contact not found")
            c.execute("INSERT INTO transaction_finding_parties(id,case_id,finding_id,contact_id,party_role,created_at) VALUES (?,?,?,?,?,?)", (record_id,case_id,finding_id,payload["contact_id"],payload["party_role"],now))
        return self.get(case_id, "transaction_finding_parties", record_id)

    def report_version(self, case_id: str, engagement_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        status = str(payload.get("status") or "DRAFT").upper()
        if status not in {"DRAFT", "REVISED_DRAFT", "FINAL", "ADDENDUM"}: raise ValueError("Unsupported transaction-audit report status")
        document_id = payload.get("document_id")
        with self.store.transaction() as c:
            self._row(c, "transaction_audit_engagements", case_id, engagement_id)
            if document_id and not c.execute("SELECT 1 FROM documents WHERE id=? AND case_id=? AND archived_at IS NULL", (document_id,case_id)).fetchone(): raise ValueError("Report document does not belong to this case")
            version_number = int(payload.get("version_number") or c.execute("SELECT COALESCE(MAX(version_number),0)+1 FROM transaction_audit_report_versions WHERE engagement_id=?", (engagement_id,)).fetchone()[0])
            record_id, now = new_id(), utc_now()
            c.execute("""INSERT INTO transaction_audit_report_versions(id,case_id,engagement_id,report_type,version_number,status,document_id,received_date,auditor_contact_id,remarks,created_by,created_at,finalized_by,finalized_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (record_id,case_id,engagement_id,payload.get("report_type", "TRANSACTION_AUDIT_REPORT"),version_number,status,document_id,payload.get("received_date"),payload.get("auditor_contact_id"),payload.get("remarks", ""),actor,now,actor if status=="FINAL" else None,now if status=="FINAL" else None))
        self._emit(case_id, "TRANSACTION_AUDIT_FINAL_REPORT_RECEIVED" if status == "FINAL" else "TRANSACTION_AUDIT_DRAFT_REPORT_RECEIVED", actor, record_id)
        return self.get(case_id, "transaction_audit_report_versions", record_id)

    def update_avoidance_application(self, case_id: str, application_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        with self.store.transaction() as c:
            app = self._row(c, "avoidance_applications", case_id, application_id)
            order_document_id = payload.get("order_document_id")
            if order_document_id and not c.execute("SELECT 1 FROM documents WHERE id=? AND case_id=? AND archived_at IS NULL", (order_document_id,case_id)).fetchone(): raise ValueError("Order document does not belong to this case")
            hearing_id = payload.get("hearing_id")
            if hearing_id and not c.execute("SELECT 1 FROM hearings WHERE id=? AND case_id=? AND archived_at IS NULL", (hearing_id,case_id)).fetchone(): raise ValueError("Hearing does not belong to this case")
            updates = {key: payload[key] for key in ("status","ia_number","filed_date","next_hearing_date","order_document_id","hearing_id") if key in payload}
            if not updates: raise ValueError("Supply an application status, filing, hearing or order update")
            updates["status"] = str(updates.get("status", app["status"])).upper()
            updates.update({"updated_by": actor, "updated_at": utc_now()})
            c.execute(f"UPDATE avoidance_applications SET {', '.join(f'{key}=?' for key in updates)} WHERE id=? AND case_id=?", (*updates.values(),application_id,case_id))
        generic = app["application_id"]
        if generic:
            self.store.update_module_record(case_id, "applications", generic, {"number": payload.get("ia_number", app["ia_number"]), "filing_date": payload.get("filed_date", app["filed_date"]), "status": str(payload.get("status", app["status"])).lower()}, actor)
        if payload.get("filed_date"): self._emit(case_id, "AVOIDANCE_APPLICATION_FILED", actor, application_id)
        return self.get(case_id, "avoidance_applications", application_id)

    def list(self, case_id: str, table: str, engagement_id: Optional[str] = None) -> List[Dict[str, Any]]:
        allowed = {"transaction_audit_engagements", "transaction_review_scopes", "transaction_audit_document_requirements", "transaction_audit_bank_coverages", "case_related_parties", "transaction_review_workstreams", "transaction_audit_transactions", "transaction_findings", "transaction_audit_source_index", "transaction_audit_report_versions", "avoidance_decisions", "avoidance_applications"}
        if table not in allowed: raise ValueError("Unsupported transaction-audit register")
        with self.store.connect() as c:
            self.store.ensure_case(c, case_id)
            sql, args = f"SELECT * FROM {table} WHERE case_id=?", [case_id]
            if engagement_id and table not in {"case_related_parties", "avoidance_applications"}:
                sql += " AND engagement_id=?"; args.append(engagement_id)
            return [dict(row) for row in c.execute(sql + " ORDER BY created_at", tuple(args)).fetchall()]

    def workflow_summary(self, case_id: str) -> Dict[str, Any]:
        with self.store.connect() as c:
            engagement=c.execute("SELECT * FROM transaction_audit_engagements WHERE case_id=? ORDER BY created_at DESC LIMIT 1",(case_id,)).fetchone()
            rows=c.execute("SELECT status,COUNT(*) n FROM transaction_audit_document_requirements WHERE case_id=? GROUP BY status",(case_id,)).fetchall()
            streams=c.execute("SELECT review_type,status FROM transaction_review_workstreams WHERE case_id=?",(case_id,)).fetchall()
            pending_rp=c.execute("SELECT COUNT(*) FROM transaction_findings f WHERE case_id=? AND status='AUDITOR_FINAL' AND NOT EXISTS(SELECT 1 FROM transaction_finding_reviews r WHERE r.finding_id=f.id AND r.review_layer='RP')",(case_id,)).fetchone()[0]
            pending_legal=c.execute("SELECT COUNT(*) FROM transaction_finding_reviews WHERE case_id=? AND review_layer='RP' AND status='ACCEPTED_FOR_LEGAL_REVIEW'",(case_id,)).fetchone()[0]
            status={r['status']:r['n'] for r in rows}; next_hearing=c.execute("SELECT MIN(next_hearing_date) FROM avoidance_applications WHERE case_id=? AND status NOT IN ('CLOSED','DISPOSED')",(case_id,)).fetchone()[0]; scope=c.execute("SELECT status FROM transaction_review_scopes WHERE case_id=? ORDER BY created_at DESC LIMIT 1",(case_id,)).fetchone()
            return {"transaction_audit_status": engagement['status'] if engagement else 'NOT_RECORDED',"transaction_auditor": engagement['auditor_contact_id'] if engagement else None,"audit_scope_status": scope['status'] if scope else 'NOT_RECORDED',"audit_document_requests_total":sum(status.values()),"audit_documents_received":status.get('RECEIVED',0),"audit_documents_partial":status.get('PARTLY_RECEIVED',0),"audit_documents_missing":status.get('NOT_AVAILABLE',0)+status.get('FOLLOW_UP_REQUIRED',0),"bank_coverage_gaps":c.execute("SELECT COUNT(*) FROM transaction_audit_bank_coverages WHERE case_id=? AND coverage_status IN ('MISSING','PARTIAL','REVIEW_REQUIRED')",(case_id,)).fetchone()[0],"related_parties_confirmed":c.execute("SELECT COUNT(*) FROM case_related_parties WHERE case_id=? AND confirmed_status='CONFIRMED'",(case_id,)).fetchone()[0],"related_parties_review_required":c.execute("SELECT COUNT(*) FROM case_related_parties WHERE case_id=? AND confirmed_status IN ('UNVERIFIED','REVIEW_REQUIRED')",(case_id,)).fetchone()[0],"preference_review_status":next((r['status'] for r in streams if r['review_type']=='PREFERENTIAL'),'NOT_STARTED'),"undervalue_review_status":next((r['status'] for r in streams if r['review_type']=='UNDERVALUE'),'NOT_STARTED'),"extortionate_review_status":next((r['status'] for r in streams if r['review_type']=='EXTORTIONATE'),'NOT_STARTED'),"section66_review_status":next((r['status'] for r in streams if r['review_type']=='FRAUDULENT_WRONGFUL'),'NOT_STARTED'),"related_party_review_status":next((r['status'] for r in streams if r['review_type']=='RELATED_PARTY'),'NOT_STARTED'),"open_findings":c.execute("SELECT COUNT(*) FROM transaction_findings WHERE case_id=? AND status NOT IN ('CLOSED')",(case_id,)).fetchone()[0],"findings_pending_RP_review":pending_rp,"findings_pending_legal_review":pending_legal,"confirmed_avoidance_decisions":c.execute("SELECT COUNT(*) FROM avoidance_decisions WHERE case_id=? AND status='CONFIRMED'",(case_id,)).fetchone()[0],"avoidance_applications_pending":c.execute("SELECT COUNT(*) FROM avoidance_applications WHERE case_id=? AND status IN ('DRAFT','PENDING')",(case_id,)).fetchone()[0],"avoidance_applications_filed":c.execute("SELECT COUNT(*) FROM avoidance_applications WHERE case_id=? AND filed_date IS NOT NULL",(case_id,)).fetchone()[0],"next_avoidance_hearing":next_hearing}
