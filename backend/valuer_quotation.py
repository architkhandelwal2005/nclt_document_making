"""Deterministic Registered Valuer quotation-invitation rendering.

The body below is the supplied office language with named values parameterised.
It is deliberately not an AI prompt or a generative-document routine.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Iterable


ASSET_CLASS_LABELS = {
    "LAND_BUILDING": "Land & Building",
    "PLANT_MACHINERY": "Plant & Machinery",
    "SECURITIES_FINANCIAL_ASSETS": "Securities & Financial Assets",
    "OTHER": "Other",
}


def _office_date(value: Any) -> str:
    text = str(value or "").strip()[:10]
    if not text:
        return ""
    try:
        parsed = date.fromisoformat(text)
        return f"{parsed.day} {parsed:%B} {parsed.year}"
    except ValueError:
        return text


def asset_classes_text(values: Iterable[Any]) -> str:
    labels = [ASSET_CLASS_LABELS.get(str(value).upper(), str(value).replace("_", " ").title()) for value in values if str(value).strip()]
    if not labels:
        raise ValueError("At least one confirmed valuation asset class is required")
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return f"{', '.join(labels[:-1])}, and {labels[-1]}"


def render_quotation_invitation(values: Dict[str, Any]) -> Dict[str, str]:
    required = ("corporate_debtor_name", "nclt_bench", "cirp_commencement_date", "professional_name", "ibbi_registration_number", "recipient_name")
    missing = [label for label in required if not str(values.get(label) or "").strip()]
    if missing:
        raise ValueError("Missing confirmed quotation-invitation fields: " + ", ".join(missing))
    asset_classes = asset_classes_text(values.get("asset_classes") or [])
    role = str(values.get("professional_role") or "IRP").upper()
    if role not in {"IRP", "RP"}:
        raise ValueError("Professional role must be IRP or RP")
    debtor = str(values["corporate_debtor_name"]).strip()
    order_date = _office_date(values.get("admission_order_date") or values["cirp_commencement_date"])
    received = _office_date(values.get("order_received_date"))
    received_text = f" (Copy of order received on {received})" if received else ""
    role_context = ""
    if role == "RP":
        context = str(values.get("rp_appointment_context") or "The Committee of Creditors has confirmed my appointment as the Resolution Professional of the Corporate Debtor.").strip()
        role_context = " " + context
    deadline = _office_date(values.get("quotation_due_date"))
    due_text = f" The quotation may kindly be submitted by {deadline}." if deadline else ""
    process_email = str(values.get("process_email") or "").strip()
    email_text = f" Please send the quotation to {process_email}." if process_email else ""
    body = f"""Dear Sir,

This is with regard to the appointment of the Registered Valuers for conducting the valuation of the assets in the matter of CIRP of {debtor} (the Corporate Debtor) which is undergoing Corporate Insolvency Resolution Process commenced by the order of National Company Law Tribunal, {values['nclt_bench']}, on {order_date}{received_text}. The NCLT vide its above-mentioned order has been pleased to appoint me i.e. {values['professional_name']} Insolvency Professional having Registration No. {values['ibbi_registration_number']}, as the Interim Resolution Professional (IRP), in accordance with Section 16 of The Insolvency Bankruptcy Code, 2016.{role_context}

Kindly note that in accordance with the Insolvency and Bankruptcy Code, 2016 the valuation is to be computed for {asset_classes} classes.

Since you are registered with IBBI, you are requested to kindly submit your quotation for conducting the valuation of the Corporate Debtor at the earliest.{due_text}{email_text}"""
    return {"subject": f"Invitation for quotation for valuation — {debtor}", "body": body, "asset_classes_text": asset_classes}
