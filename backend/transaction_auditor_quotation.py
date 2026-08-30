"""Deterministic transaction-auditor quotation invitation rendering.

This deliberately mirrors the office's professional-invitation workflow.  It
does not infer a review period, choose an avoidance provision, or send email.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Iterable, List


SCOPE_WORDING = {
    "PREFERENTIAL": "Preferential transactions under section 43 of the Insolvency and Bankruptcy Code, 2016",
    "UNDERVALUE": "Undervalued transactions under section 45 of the Insolvency and Bankruptcy Code, 2016",
    "EXTORTIONATE": "Extortionate credit transactions under section 50 of the Insolvency and Bankruptcy Code, 2016",
    "FRAUDULENT_WRONGFUL": "Fraudulent or wrongful transactions under section 66 of the Insolvency and Bankruptcy Code, 2016",
    "RELATED_PARTY": "Related-party transactions",
}


def _ordinal(day: int) -> str:
    if 10 < day % 100 < 14:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def _office_date(value: Any) -> str:
    value = str(value or "").strip()[:10]
    if not value:
        return ""
    try:
        parsed = date.fromisoformat(value)
        return f"{_ordinal(parsed.day)} {parsed:%B} {parsed.year}"
    except ValueError:
        return value


def _numeric_date(value: Any) -> str:
    text = str(value or "").strip()[:10]
    if not text:
        return ""
    try:
        return date.fromisoformat(text).strftime("%d.%m.%Y")
    except ValueError:
        return text


def confirmed_scope_clauses(scopes: Iterable[Dict[str, Any]]) -> List[str]:
    """Render only professionally confirmed periods; never derive a period."""
    clauses: List[str] = []
    for scope in scopes:
        if str(scope.get("status") or "").upper() != "CONFIRMED":
            continue
        review_code = str(scope.get("review_type") or "").upper()
        review_type = SCOPE_WORDING.get(review_code, review_code.replace("_", " ").title())
        start, end = _office_date(scope.get("period_from")), _office_date(scope.get("period_to"))
        if not review_type or not start or not end:
            raise ValueError("REVIEW_REQUIRED: every confirmed transaction-review scope needs a period")
        conjunction = "till" if review_code == "FRAUDULENT_WRONGFUL" else "to"
        clauses.append(f"{review_type} for the period from {start} {conjunction} {end}")
    if not clauses:
        raise ValueError("REVIEW_REQUIRED: at least one professionally confirmed transaction-review scope is required")
    return clauses


def confirmed_scope_text(scopes: Iterable[Dict[str, Any]]) -> str:
    return "; ".join(confirmed_scope_clauses(scopes))


def render_transaction_auditor_quotation(values: Dict[str, Any]) -> Dict[str, str]:
    required = ("corporate_debtor_name", "nclt_bench", "cirp_commencement_date", "professional_name", "ibbi_registration_number", "recipient_name")
    missing = [key for key in required if not str(values.get(key) or "").strip()]
    if missing:
        raise ValueError("Missing confirmed quotation-invitation fields: " + ", ".join(missing))
    scope_clauses = confirmed_scope_clauses(values.get("confirmed_scopes") or [])
    scope_text = "; ".join(scope_clauses)
    lettered_scopes = "\n\n".join(f"{chr(97 + index)}) {clause};" for index, clause in enumerate(scope_clauses))
    debtor = str(values["corporate_debtor_name"]).strip()
    professional_role = str(values.get("professional_role") or "IRP").upper()
    if professional_role not in {"IRP", "RP"}:
        raise ValueError("Professional role must be IRP or RP")
    order_date = _office_date(values.get("admission_order_date") or values["cirp_commencement_date"])
    received = _office_date(values.get("order_received_date"))
    received_text = f" (Copy of order received on {received})" if received else ""
    office = str(values.get("registered_office_address") or "").strip()
    office_text = f"\n\nRegistered Office Address- {office}" if office else ""
    afa = str(values.get("afa_validity") or "").strip()
    deadline = _office_date(values.get("quotation_due_date"))
    deadline_time = str(values.get("quotation_due_time") or "").strip()
    deadline_text = f" latest by {deadline}" if deadline else ""
    if deadline_time:
        deadline_text += f" before {deadline_time}"
    process_email = str(values.get("process_email") or "").strip()
    ibbi_email = str(values.get("ibbi_email") or "").strip()
    professional_office = str(values.get("professional_office_address") or "").strip()
    professional_role_label = "Interim Resolution Professional" if professional_role == "IRP" else "Resolution Professional"
    role_context = "as the Interim Resolution Professional (IRP), in accordance with Section 16 of The Insolvency and Bankruptcy Code, 2016."
    if professional_role == "RP":
        role_context += " The Committee of Creditors later appointed me as the Resolution Professional of the Corporate Debtor in terms of section 22 of the Insolvency and Bankruptcy Code, 2016."
    salutation = str(values.get("recipient_salutation") or "Sir").strip()
    commencement_numeric = _numeric_date(values["cirp_commencement_date"])
    signoff_lines = [str(values["professional_name"]).strip(), f"{professional_role} of {debtor} (under CIRP)"]
    if professional_office:
        signoff_lines.append(f"Registered Office: {professional_office}")
    signoff_lines.append(f"Reg. no. {values['ibbi_registration_number']}")
    if afa:
        signoff_lines.append(f"AFA valid till {afa}")
    if process_email:
        signoff_lines.append(f"Process Specific Email ID: {process_email}")
    if ibbi_email:
        signoff_lines.append(f"IBBI Reg. Email ID: {ibbi_email}")
    body = f"""Dear {salutation},

This is with regard to the appointment of the transaction auditor for determining the transactions in the matter of CIRP of {debtor} (the Corporate Debtor), which is undergoing Corporate Insolvency Resolution Process commenced by the order of National Company Law Tribunal, {values['nclt_bench']}, on {order_date}{received_text}. The NCLT vide its above-mentioned order has been pleased to appoint me i.e. {values['professional_name']}, Insolvency Professional having Registration No. {values['ibbi_registration_number']}, {role_context}{office_text}

You will be required to send a quotation for the purpose of conducting the Transaction Audit of the Corporate Debtor. The Transaction Audit is to be carried out for the professionally confirmed periods mentioned below prior to, or otherwise anchored to, the date of CIRP commencement i.e. {commencement_numeric}, as applicable:

{lettered_scopes}

Further, it may please be noted that your appointment shall be valid only if you do not:

1. have any direct or indirect interest in the Corporate Debtor;

2. act as a partner or director of the Resolution Professional or any associate concern of the Insolvency Professional;

3. act as a relative of the Resolution Professional, {values['professional_name']};

4. have been an auditor of {debtor} during any of the professionally confirmed review periods stated above; or

5. have any pecuniary interest in the assets or properties of the Corporate Debtor.

In this regard, you are required to submit your quote{deadline_text}.

{chr(10).join(signoff_lines)}"""
    return {
        "subject": f"Invitation for quotation for Transaction Audit - {debtor}",
        "body": body,
        "scope_text": scope_text,
        "scope_clauses": scope_clauses,
        "recipient_name": str(values["recipient_name"]).strip(),
        "professional_role_label": professional_role_label,
    }
