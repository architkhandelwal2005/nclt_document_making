"""Deterministic transaction-auditor quotation invitation rendering.

This deliberately mirrors the office's professional-invitation workflow.  It
does not infer a review period, choose an avoidance provision, or send email.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Iterable


def _office_date(value: Any) -> str:
    value = str(value or "").strip()[:10]
    if not value:
        return ""
    try:
        parsed = date.fromisoformat(value)
        return f"{parsed.day} {parsed:%B} {parsed.year}"
    except ValueError:
        return value


def confirmed_scope_text(scopes: Iterable[Dict[str, Any]]) -> str:
    """Return only professionally confirmed scope periods for the invitation."""
    labels = []
    for scope in scopes:
        if str(scope.get("status") or "").upper() != "CONFIRMED":
            continue
        review_type = str(scope.get("review_type") or "").replace("_", " ").title()
        start, end = _office_date(scope.get("period_from")), _office_date(scope.get("period_to"))
        if not review_type or not start or not end:
            raise ValueError("REVIEW_REQUIRED: every confirmed transaction-review scope needs a period")
        labels.append(f"{review_type} ({start} to {end})")
    if not labels:
        raise ValueError("REVIEW_REQUIRED: at least one professionally confirmed transaction-review scope is required")
    return "; ".join(labels)


def render_transaction_auditor_quotation(values: Dict[str, Any]) -> Dict[str, str]:
    required = ("corporate_debtor_name", "nclt_bench", "cirp_commencement_date", "professional_name", "ibbi_registration_number", "recipient_name")
    missing = [key for key in required if not str(values.get(key) or "").strip()]
    if missing:
        raise ValueError("Missing confirmed quotation-invitation fields: " + ", ".join(missing))
    scope_text = confirmed_scope_text(values.get("confirmed_scopes") or [])
    debtor = str(values["corporate_debtor_name"]).strip()
    professional_role = str(values.get("professional_role") or "IRP").upper()
    if professional_role not in {"IRP", "RP"}:
        raise ValueError("Professional role must be IRP or RP")
    order_date = _office_date(values.get("admission_order_date") or values["cirp_commencement_date"])
    received = _office_date(values.get("order_received_date"))
    received_text = f" (order copy received on {received})" if received else ""
    office = str(values.get("registered_office_address") or "").strip()
    office_text = f" The registered office recorded for the Corporate Debtor is {office}." if office else ""
    afa = str(values.get("afa_validity") or "").strip()
    afa_text = f" AFA validity noted: {afa}." if afa else ""
    deadline = _office_date(values.get("quotation_due_date"))
    deadline_text = f" Please submit your quotation by {deadline}." if deadline else ""
    process_email = str(values.get("process_email") or "").strip()
    email_text = f" Please send it to {process_email}." if process_email else ""
    body = f"""Dear Sir/Madam,

This is with regard to inviting a quotation for conducting a Transaction Audit in the CIRP of {debtor} (the Corporate Debtor). The CIRP commenced pursuant to the order of the National Company Law Tribunal, {values['nclt_bench']}, dated {order_date}{received_text}. I, {values['professional_name']}, Insolvency Professional having IBBI Registration No. {values['ibbi_registration_number']}, am acting as the {professional_role} for the Corporate Debtor.{afa_text}{office_text}

The professionally confirmed review scope is: {scope_text}.

You are requested to submit your quotation, proposed methodology, team details, expected timeline and confirmation of independence for the above assignment. {deadline_text}{email_text}

Regards,
{values['professional_name']}
{professional_role}"""
    return {"subject": f"Invitation for quotation for Transaction Audit - {debtor}", "body": body, "scope_text": scope_text}
