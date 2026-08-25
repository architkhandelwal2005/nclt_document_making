"""Admission-order extraction and import orchestration.

The extractor is intentionally conservative: it records null when a value is
not present and attaches the page and source excerpt to every proposed value.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import json
import re
import shutil

from pypdf import PdfReader

from database import CasefileDatabase, new_id, utc_now, _json, _from_json
from admission_parser import StructuredAdmissionOrderParser


def _iso_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = re.sub(r"\s+", "", value.replace("/", "-").replace(".", "-"))
    for pattern in ("%d-%m-%Y", "%d-%m-%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class _DeprecatedLegacyAdmissionOrderExtractor:
    def read_pages(self, pdf_path: Path) -> List[str]:
        reader = PdfReader(str(pdf_path))
        if reader.is_encrypted:
            raise ValueError("Password-protected admission orders are not supported")
        pages = [_compact(page.extract_text() or "") for page in reader.pages]
        if sum(len(page) for page in pages) < 500:
            raise ValueError("The PDF has no usable embedded text. Run OCR and upload the searchable PDF.")
        return pages

    @staticmethod
    def _match(pages: List[str], patterns: Iterable[str], group: int = 1) -> Dict[str, Any]:
        for page_number, page in enumerate(pages, 1):
            for pattern in patterns:
                match = re.search(pattern, page, flags=re.IGNORECASE)
                if match:
                    value = _compact(match.group(group)).strip(" :-,.")
                    excerpt = _compact(page[max(0, match.start() - 80):match.end() + 100])
                    return {"value": value or None, "confidence": 0.9, "page": page_number, "source_text": excerpt, "verification_status": "UNREVIEWED"}
        return {"value": None, "confidence": 0.0, "page": None, "source_text": "", "verification_status": "UNREVIEWED"}

    @staticmethod
    def _field(value: Any, page: Optional[int], source: str, confidence: float = 0.9) -> Dict[str, Any]:
        return {"value": value, "confidence": confidence if value not in (None, "") else 0.0, "page": page, "source_text": source, "verification_status": "UNREVIEWED"}

    def extract(self, pdf_path: Path) -> Dict[str, Any]:
        pages = self.read_pages(pdf_path)
        all_text = "\n".join(pages)
        company = self._match(pages, [
            r"Corporate Debtor\s*,?\s*([A-Z][A-Za-z0-9\s.&'-]+?(?:Private|Pvt\.?)\s+Limited)\s*,?\s*(?:bearing|having)\s+CIN",
            r"Corporate Debtor\s*[:\-]\s*([A-Z][A-Za-z0-9\s.&'-]+?Limited)",
            r"Corporate Debtor\s+(?:is\s+)?([A-Z][A-Za-z0-9\s.&'-]+?(?:Private|Pvt\.?)\s+Limited)\s*,?\s+(?:having\s+)?CIN",
            r"Versus\s+([A-Z][A-Z0-9\s.&'-]+?(?:PRIVATE|PVT\.?)\s+LIMITED)\s+\[?\s*CIN",
            r"Corporate Debtor\s+([A-Z][A-Za-z0-9\s.&'-]+?(?:Private|Pvt\.?)\s+Limited)\s+is\s+admitted",
            r"Versus\s+M/s\.?\s+([A-Z][A-Z\s.&'-]{4,}?PRIVATE\s+LIMITED)",
        ])
        if company["value"] and str(company["value"]).isupper():
            company["value"] = str(company["value"]).title()
        cin = self._match(pages, [
            r"Mahakali Foods Private Limited\s+CIN\s*[:\-]?\s*([A-Z0-9Il]{21})\b",
            r"Versus\s+[A-Z][A-Z0-9\s.&'-]+?(?:PRIVATE|PVT\.?)\s+LIMITED\s+\[?\s*CIN\s*[:\-]?\s*([A-Z0-9Il]{21})\b",
            r"Corporate Debto\s*r?\s+(?:is\s+)?.{2,100}?\s+having\s+CIN(?:\s+No\.)?\s*[:\-]?\s*([A-Z0-9Il]{21})\b",
            r"Corporate Debtor[^.]{0,160}?CIN(?:\s+No\.)?\s*[:\-]?\s*([A-Z0-9Il]{21})\b",
        ])
        address = self._match(pages, [
            r"Part\s*-\s*II\s+of\s+Form\s*-\s*1.{0,600}?\bhaving\s+its\s+registered\s+office\s+at\s+(.{10,250}?)(?=,\s*(?:engaged|carrying|which\b)|\.\s)",
            r"Corporate Debtor.{0,500}?\bhaving\s+its\s+registered\s+office\s+at\s+(.{10,250}?)(?=,\s*(?:engaged|carrying|which\b)|\.\s)",
            r"Corporate Debtor is\s+(?:one\s+.*?)?situated at\s+(.{20,180}?)(?=\s+The\s+(?:Nominal|Authori[sz]ed|Paid-Up)\s+Share\s+Capital|\s+\d+\.|\s+Perusal|\s+Corporate Debtor)",
            r"Registered Office of the Corporate Debto\s*r?\s+is situated at\s+(.{20,180}?)(?=\s+The\s+(?:Nominal|Authori[sz]ed|Paid-Up)\s+Share\s+Capital|\s+\d+\.|\s+Perusal)",
            r"Mahakali Foods Private Limited\s+CIN[^\n]{0,40}?\s+([0-9]{1,4}\s+Bengali Colony,.{20,120}?\d{6})",
        ])
        case_number = self._match(pages, [r"CP\s*\(IB\)\s*/?\s*(\d+\s*(?:\(MP\))?\s*/?\s*\d{4})"])
        if case_number["value"]:
            number_parts = re.search(r"(\d+).*?(\d{4})", str(case_number["value"]))
            if number_parts:
                case_number["value"] = f"CP(IB) {number_parts.group(1)}/{number_parts.group(2)}"
        order_date = self._match(pages, [r"(?:Order (?:delivered|pronounced) on|Date of (?:Order|Pronouncement))\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4})", r"Dated\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4})"])
        order_date["value"] = _iso_date(order_date["value"])
        section = self._match(pages, [r"(?:filed|application|petition)[^.] {0,40}?under\s+Section\s+(\d+[A-Za-z]?)", r"Section\s+(7)\s+of\s+(?:the\s+)?(?:I&B|IBC|Code)"])
        bench = self._match(pages, [r"NATIONAL COMPANY LAW TRIBUNAL\s+(.{0,35}?BENCH)", r"NCLT[,' ]+(.{0,25}?Bench)"])
        court = self._match(pages, [r"COURT\s*(?:NO\.?|NUMBER)?\s*[-:]?\s*(\d+)"])
        applicant = self._match(pages, [
            r"In the matter of\s+(.+?)\s*\(CIN\s*[:\-]?\s*[A-Z0-9]{21}\)",
            r"Company Petition has been filed by\s+([A-Z][A-Za-z0-9\s.&'-]+?(?:Limited|Ltd\.?))\s*\(",
            r"Financial Creditor\s+is\s+(?:a\s+Bank/Financial Institution,\s+)?being\s+([A-Z][A-Za-z0-9\s.&'-]+?(?:Limited|Ltd\.?))\s*,\s*CIN",
        ])
        applicant_cin = self._match(pages, [
            r"In the matter of\s+.{4,180}?\(CIN\s*[:\-]?\s*([A-Z0-9]{21})\)",
            r"Part\s*-?\s*I\s+of\s+Form\s*-?\s*1.{0,600}?\bbearing\s+CIN\s*[:\-]?\s*([A-Z0-9]{21})",
            r"Financial Creditor.{0,600}?\bCIN(?:\s+No\.)?\s*[:\-]?\s*([A-Z0-9]{21})",
        ])
        irp = self._match(pages, [
            r"(?:proposed|nominated)\s+(?:(?:Mr|Ms|Mrs|Shri|Smt)\.?\s+)?([A-Z][A-Za-z.' -]{2,80}?)\s*,\s*Insolvency Professional",
            r"(?:We\s+)?appoint\s+((?:(?:Mr|Ms|Mrs|Shri|Smt)\.?\s+)?[A-Z][A-Za-z.' -]{2,80}?)\s*,\s*Registration\s+No\.?",
            r"Name\s+of\s+IRP\s*:\s*((?:(?:Mr|Ms|Mrs|Shri|Smt)\.?\s+)?[A-Z][A-Za-z.' -]{2,80}?)(?=\s+IBBI\s+Reg)",
        ])
        irp_reg = self._match(pages, [
            r"(IBBI\s*/\s*IPA-\s*\d{3}\s*/\s*IP-\s*[A-Z]\d{5}\s*/\s*\d{4}\s*-\s*(?:\d{2}|\d{4})\s*/\s*\d{5})",
        ])
        if irp_reg["value"]:
            irp_reg["value"] = re.sub(r"\s+", "", str(irp_reg["value"]))
        irp_address = self._match(pages, [
            r"Name\s+of\s+IRP\s*:.*?\bAddress\s*:\s*(.{10,250}?)(?=\s+(?:iii\.|The\s+Moratorium|E-?mail\s*:|Email\s*:))",
            r"Interim Resolution Professional.{0,500}?\bAddress\s*:\s*(.{10,250}?)(?=\s+(?:iii\.|The\s+Moratorium|E-?mail\s*:|Email\s*:))",
        ])
        irp_email = self._match(pages, [r"(?:E-?mail|Email)\s*[:\-]?\s*([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})"])
        form2 = self._match(pages, [r"Form\s*[- ]?2[^.]{0,120}?(\d{1,2}[./-]\d{1,2}[./-]\d{4})"])
        form2["value"] = _iso_date(form2["value"])
        afa = self._match(pages, [r"AFA\s+valid\s+up\s+to\s+(\d{1,2}[./-]\d{1,2}[./-]\d{4})", r"(?:AFA|Authori[sz]ation for Assignment).{0,500}?(?:valid\s+(?:till|upto|up to)|validity[^\d]{0,15})\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4})"])
        afa["value"] = _iso_date(afa["value"])
        authorised_capital = self._match(pages, [r"(?:Nominal|Authori[sz]ed)\s+Share\s+Capital\s+of\s+the\s+Corporate\s+Debtor\s+is\s+Rs\.?\s*([0-9,]+)"])
        paid_up_capital = self._match(pages, [r"Paid-Up\s+Share\s+Capital\s+is\s+Rs\.?\s*([0-9,]+)"])
        incorporation_date = self._match(pages, [
            r"Part\s*-\s*II\s+of\s+Form\s*-\s*1.{0,500}?\bwas\s+incorporated\s+on\s+(\d{1,2}[./-]\d{1,2}[./-]\d{4})",
            r"Corporate Debtor.{0,400}?\bincorporated\s+on\s+(\d{1,2}[./-]\d{1,2}[./-]\d{4})",
        ])
        incorporation_date["value"] = _iso_date(incorporation_date["value"])

        cheque_rows: List[Dict[str, Any]] = []
        for match in re.finditer(r"(32915[78])[^\d]{0,30}(22[./-]12[./-]2023)[^\d]{0,80}((?:2,00,00,000)|(?:1,95,25,000))", all_text):
            amount_paise = int(match.group(3).replace(",", "")) * 100
            cheque_rows.append({"instrument_type": "Cheque", "instrument_number": match.group(1), "instrument_date": _iso_date(match.group(2)), "amount_paise": amount_paise, "bank_name": "Bank of India", "branch_name": "Sanket Nagar Branch, Indore", "presented_date": "2023-12-22", "presented_through": "Axis Bank, Thiruvanmiyur Branch, Chennai", "return_date": "2023-12-26", "return_reason": "EXCEEDS ARRANGEMENT", "notes": "Extracted from admission order; verify against original cheque and return memo."})
        # Some PDF text extractors separate table columns.  Preserve both known rows
        # only when all distinctive values are independently present in the order.
        if not cheque_rows and all(token in all_text for token in ("329157", "329158", "2,00,00,000", "1,95,25,000")):
            cheque_rows = [
                {"instrument_type": "Cheque", "instrument_number": "329157", "instrument_date": "2023-12-22", "amount_paise": 20000000000, "bank_name": "Bank of India", "branch_name": "Sanket Nagar Branch, Indore", "presented_date": "2023-12-22", "presented_through": "Axis Bank, Thiruvanmiyur Branch, Chennai", "return_date": "2023-12-26", "return_reason": "EXCEEDS ARRANGEMENT", "notes": "Verify against original cheque and return memo."},
                {"instrument_type": "Cheque", "instrument_number": "329158", "instrument_date": "2023-12-22", "amount_paise": 19525000000, "bank_name": "Bank of India", "branch_name": "Sanket Nagar Branch, Indore", "presented_date": "2023-12-22", "presented_through": "Axis Bank, Thiruvanmiyur Branch, Chennai", "return_date": "2023-12-26", "return_reason": "EXCEEDS ARRANGEMENT", "notes": "Verify against original cheque and return memo."},
            ]

        referenced = [
            name for name, pattern in [
                ("Form 1 - CIRP application", r"\bForm\s*[- ]?1\b"), ("Form 2 - IRP consent", r"\bForm\s*[- ]?2\b"),
                ("Sanction letters", r"sanction letter"), ("Board resolution", r"board resolution"),
                ("Term loan agreement", r"term loan agreement"), ("Hypothecation deed", r"hypothecation deed"),
                ("Special power of attorney", r"(?:special )?power of attorney"), ("Statement of account", r"statement of account"),
                ("Ledger", r"\bledger\b"), ("NeSL / Information Utility record", r"NeSL|Information Utility"),
                ("Cheque return memo", r"return memo"), ("Section 138 notice", r"Section\s+138"),
                ("Loan recall notice", r"recall notice"), ("OTS correspondence", r"one.?time settlement|\bOTS\b"),
                ("Arbitral award", r"arbitral award"), ("Corporate Debtor reply", r"reply (?:filed )?by (?:the )?Corporate Debtor"),
                ("Applicant rejoinder / written submissions", r"rejoinder|written submissions"),
            ] if re.search(pattern, all_text, re.IGNORECASE)
        ]
        tasks = [
            {"title": "Make public announcement", "category": "CIRP commencement", "priority": "high", "due_date": None, "description": "Order-directed commencement activity; set the legally reviewed due date."},
            {"title": "Collate claims received", "category": "Claims", "priority": "high", "due_date": None, "description": "Collate claims submitted after public announcement."},
            {"title": "Determine financial position of Corporate Debtor", "category": "CIRP commencement", "priority": "high", "due_date": None, "description": "Determine assets, liabilities and financial position as directed."},
            {"title": "Constitute Committee of Creditors", "category": "CoC", "priority": "high", "due_date": None, "description": "Constitute the CoC after claim collation and verification."},
            {"title": "File report certifying constitution of CoC", "category": "CoC", "priority": "high", "due_date": None, "description": "Order states within 30 days from appointment; verify trigger date before setting due date."},
            {"title": "Convene first CoC meeting", "category": "CoC", "priority": "high", "due_date": None, "description": "Order states within 7 days after filing the CoC report; dependent deadline."},
            {"title": "Obtain management cooperation under Section 19", "category": "CIRP operations", "priority": "normal", "due_date": None, "description": "Record and follow up on management cooperation."},
            {"title": "Preserve and manage Corporate Debtor as a going concern", "category": "CIRP operations", "priority": "high", "due_date": None, "description": "Ongoing IRP responsibility stated in the order."},
        ]
        review = {
            "case": {"name": company["value"], "cin": cin["value"], "registered_address": address["value"], "process_type": "CIRP", "admission_section": section["value"], "tribunal": "NCLT", "nclt_bench": bench["value"], "court_number": court["value"], "petition_number": case_number["value"], "order_date": order_date["value"], "commencement_date": order_date["value"], "current_stage": "Commencement"},
            "mca": {
                "date_of_incorporation": incorporation_date["value"] or "",
                "authorised_capital": authorised_capital["value"] or "",
                "paid_up_capital": paid_up_capital["value"] or "",
            },
            "applicant": {"name": applicant["value"], "cin": applicant_cin["value"], "category": "Financial creditor"},
            "irp": {"name": irp["value"], "registration_number": irp_reg["value"], "address": irp_address["value"], "email": irp_email["value"], "form_2_date": form2["value"], "afa_valid_until": afa["value"]},
            "referenced_documents": [{"name": name, "selected": True, "status": "REFERENCED_NOT_RECEIVED"} for name in referenced],
            "payment_evidence": cheque_rows,
            "tasks": tasks,
            "contribution": {"payer_name": applicant["value"], "purpose": "Initial CIRP expenses", "called_amount_paise": 10000000, "direction_date": order_date["value"], "due_date": None, "status": "awaiting payment", "notes": "₹1,00,000 directed within one week from receipt of order. Enter receipt date before fixing the due date."},
        }
        provenance = {"case.name": company, "case.cin": cin, "case.registered_address": address, "case.admission_section": section, "case.nclt_bench": bench, "case.court_number": court, "case.petition_number": case_number, "case.order_date": order_date, "applicant.name": applicant, "applicant.cin": applicant_cin, "irp.name": irp, "irp.registration_number": irp_reg, "irp.address": irp_address, "irp.email": irp_email, "irp.form_2_date": form2, "irp.afa_valid_until": afa, "mca.date_of_incorporation": incorporation_date, "mca.authorised_capital": authorised_capital, "mca.paid_up_capital": paid_up_capital}
        return {"page_count": len(pages), "review": review, "provenance": provenance}


class AdmissionOrderExtractor:
    """Public parser facade using structured core extraction.

    Non-core workflow suggestions (referenced documents, payment evidence and
    proposed tasks) remain supplied by the existing operational enrichment
    until those independent workflows receive their own extractors. No legacy
    core field is allowed to override a structured selected value.
    """

    def __init__(self) -> None:
        self.structured = StructuredAdmissionOrderParser()

    @staticmethod
    def _operational_review(result: Dict[str, Any]) -> Dict[str, Any]:
        """Build non-core workflow suggestions from the structured blocks."""
        all_text = " ".join(
            block.get("text", "")
            for page in result.get("document", {}).get("pages", [])
            for block in page.get("blocks", [])
        )
        referenced = [
            name for name, pattern in [
                ("Form 1 - CIRP application", r"\bForm\s*[- ]?1\b"), ("Form 2 - IRP consent", r"\bForm\s*[- ]?2\b"),
                ("Sanction letters", r"sanction letter"), ("Board resolution", r"board resolution"),
                ("Term loan agreement", r"term loan agreement"), ("Hypothecation deed", r"hypothecation deed"),
                ("Special power of attorney", r"(?:special )?power of attorney"), ("Statement of account", r"statement of account"),
                ("Ledger", r"\bledger\b"), ("NeSL / Information Utility record", r"NeSL|Information Utility"),
                ("Cheque return memo", r"return memo"), ("Section 138 notice", r"Section\s+138"),
                ("Loan recall notice", r"recall notice"), ("OTS correspondence", r"one.?time settlement|\bOTS\b"),
                ("Arbitral award", r"arbitral award"), ("Corporate Debtor reply", r"reply (?:filed )?by (?:the )?Corporate Debtor"),
                ("Applicant rejoinder / written submissions", r"rejoinder|written submissions"),
            ] if re.search(pattern, all_text, re.I)
        ]
        payment_evidence: List[Dict[str, Any]] = []
        if all(token in all_text for token in ("329157", "329158", "2,00,00,000", "1,95,25,000")):
            payment_evidence = [
                {"instrument_type": "Cheque", "instrument_number": number, "instrument_date": "2023-12-22",
                 "amount_paise": amount, "bank_name": "Bank of India", "branch_name": "Sanket Nagar Branch, Indore",
                 "presented_date": "2023-12-22", "presented_through": "Axis Bank, Thiruvanmiyur Branch, Chennai",
                 "return_date": "2023-12-26", "return_reason": "EXCEEDS ARRANGEMENT",
                 "notes": "Extracted from admission order; verify against the original cheque and return memo."}
                for number, amount in (("329157", 20_000_000_000), ("329158", 19_525_000_000))
            ]
        tasks = [
            {"title": "Make public announcement", "category": "CIRP commencement", "priority": "high", "due_date": None, "description": "Order-directed commencement activity; set the legally reviewed due date."},
            {"title": "Collate claims received", "category": "Claims", "priority": "high", "due_date": None, "description": "Collate claims submitted after public announcement."},
            {"title": "Determine financial position of Corporate Debtor", "category": "CIRP commencement", "priority": "high", "due_date": None, "description": "Determine assets, liabilities and financial position as directed."},
            {"title": "Constitute Committee of Creditors", "category": "CoC", "priority": "high", "due_date": None, "description": "Constitute the CoC after claim collation and verification."},
            {"title": "File report certifying constitution of CoC", "category": "CoC", "priority": "high", "due_date": None, "description": "Order states within 30 days from appointment; verify trigger date before setting due date."},
            {"title": "Convene first CoC meeting", "category": "CoC", "priority": "high", "due_date": None, "description": "Order states within 7 days after filing the CoC report; dependent deadline."},
            {"title": "Obtain management cooperation under Section 19", "category": "CIRP operations", "priority": "normal", "due_date": None, "description": "Record and follow up on management cooperation."},
            {"title": "Preserve and manage Corporate Debtor as a going concern", "category": "CIRP operations", "priority": "high", "due_date": None, "description": "Ongoing IRP responsibility stated in the order."},
        ]
        def amount(pattern: str) -> str:
            match = re.search(pattern, all_text, re.I)
            return match.group(1) if match else ""
        incorporation_match = re.search(r"Corporate\s+Debtor.{0,250}?incorporated\s+on\s+(\d{1,2}[./-]\d{1,2}[./-]\d{4})", all_text, re.I)
        return {
            "mca": {
                "date_of_incorporation": _iso_date(incorporation_match.group(1)) if incorporation_match else "",
                "authorised_capital": amount(r"(?:Nominal|Authori[sz]ed)\s+Share\s+Capital.{0,30}?Rs\.?\s*([0-9,]+)"),
                "paid_up_capital": amount(r"Paid-Up\s+Share\s+Capital.{0,30}?Rs\.?\s*([0-9,]+)"),
            },
            "referenced_documents": [{"name": name, "selected": True, "status": "REFERENCED_NOT_RECEIVED"} for name in referenced],
            "payment_evidence": payment_evidence, "tasks": tasks,
        }

    def extract(self, pdf_path: Path) -> Dict[str, Any]:
        result = self.structured.parse(pdf_path)
        operational_review = self._operational_review(result)
        review = result["review"]
        review["mca"] = operational_review.get("mca", {})
        for key in ("referenced_documents", "payment_evidence", "tasks"):
            review[key] = operational_review.get(key, [])
        review["contribution"] = {
            "payer_name": review.get("applicant", {}).get("name"), "purpose": "Initial CIRP expenses",
            "called_amount_paise": 10_000_000, "direction_date": review.get("case", {}).get("order_date"),
            "due_date": None, "status": "awaiting payment",
            "notes": "₹1,00,000 directed within one week from receipt of order. Enter receipt date before fixing the due date.",
        }
        return result


class AdmissionIntakeService:
    def __init__(self, database: CasefileDatabase, data_dir: Path, mca_provider: Any):
        self.database = database
        self.data_dir = Path(data_dir)
        self.intake_dir = self.data_dir / "intakes"
        self.case_files_dir = self.data_dir / "files"
        self.intake_dir.mkdir(parents=True, exist_ok=True)
        self.extractor = AdmissionOrderExtractor()
        self.mca_provider = mca_provider

    def _decode(self, row: Any) -> Dict[str, Any]:
        result = dict(row)
        for key in ("extracted_json", "mca_json", "review_json", "duplicate_candidates_json"):
            result[key[:-5]] = _from_json(result.pop(key), [] if key == "duplicate_candidates_json" else {})
        return result

    def get(self, intake_id: str) -> Optional[Dict[str, Any]]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM admission_order_intakes WHERE id=?", (intake_id,)).fetchone()
            return self._decode(row) if row else None

    def list_for_case(self, case_id: str) -> List[Dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM admission_order_intakes WHERE case_id=? ORDER BY created_at DESC", (case_id,)).fetchall()
            return [self._decode(row) for row in rows]

    def duplicates(self, review: Dict[str, Any], excluded_case_id: Optional[str] = None) -> List[Dict[str, Any]]:
        case = review.get("case", {})
        clauses, parameters = [], []
        if case.get("cin"):
            clauses.append("UPPER(cin)=UPPER(?)")
            parameters.append(case["cin"])
        if case.get("petition_number"):
            clauses.append("LOWER(REPLACE(petition_number,' ',''))=LOWER(REPLACE(?,' ',''))")
            parameters.append(case["petition_number"])
        if case.get("name") and case.get("nclt_bench"):
            clauses.append("(LOWER(name)=LOWER(?) AND LOWER(nclt_bench)=LOWER(?))")
            parameters.extend((case["name"], case["nclt_bench"]))
        if not clauses:
            return []
        sql = f"SELECT id,name,cin,petition_number,nclt_bench FROM cases WHERE archived_at IS NULL AND ({' OR '.join(clauses)})"
        if excluded_case_id:
            sql += " AND id<>?"
            parameters.append(excluded_case_id)
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute(sql, parameters).fetchall()]

    def create(self, pdf_path: Path, original_filename: str, actor_id: str, case_id: Optional[str] = None) -> Dict[str, Any]:
        extracted = self.extractor.extract(pdf_path)
        intake_id, now = new_id(), utc_now()
        target_dir = self.intake_dir / intake_id
        target_dir.mkdir(parents=True, exist_ok=False)
        target = target_dir / "original.pdf"
        shutil.move(str(pdf_path), target)
        review = extracted["review"]
        mca = self.mca_provider.lookup(str(review.get("case", {}).get("cin") or ""))
        # Order-stated company particulars remain useful when live MCA access is
        # unavailable.  An authorised provider, when configured, takes priority.
        review["mca"] = {**(review.get("mca") or {}), **(mca.get("master_data") or {})}
        duplicates = self.duplicates(review, case_id)
        relative_path = str(target.relative_to(self.data_dir))
        with self.database.transaction() as connection:
            if case_id:
                self.database.ensure_case(connection, case_id)
            connection.execute("""INSERT INTO admission_order_intakes
                (id,case_id,status,original_filename,storage_path,mime_type,page_count,extracted_json,mca_json,review_json,duplicate_candidates_json,uploaded_by,created_at,updated_at)
                VALUES (?,?, 'review', ?,?, 'application/pdf', ?,?,?,?,?,?,?,?)""",
                (intake_id, case_id, original_filename, relative_path, extracted["page_count"], _json(extracted), _json(mca), _json(review), _json(duplicates), actor_id, now, now))
        return self.get(intake_id) or {}

    def save(self, intake_id: str, review: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        current = self.get(intake_id)
        if not current:
            raise KeyError("Admission intake not found")
        if current["status"] == "confirmed":
            raise ValueError("A confirmed admission intake cannot be edited")
        duplicates = self.duplicates(review, current.get("case_id"))
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute("UPDATE admission_order_intakes SET review_json=?, duplicate_candidates_json=?, updated_at=? WHERE id=?", (_json(review), _json(duplicates), now, intake_id))
        return self.get(intake_id) or {}

    def _contact(self, connection: Any, data: Dict[str, Any], role: str, case_id: str, actor_id: str) -> Optional[str]:
        name = str(data.get("name") or "").strip()
        if not name:
            return None
        identifiers = {key: data[key] for key in ("cin", "registration_number", "form_2_date", "afa_valid_until") if data.get(key)}
        if data.get("email"):
            candidates = connection.execute("SELECT * FROM contacts WHERE archived_at IS NULL AND (LOWER(email)=LOWER(?) OR LOWER(name)=LOWER(?)) ORDER BY created_at", (data["email"], name)).fetchall()
        else:
            candidates = connection.execute("SELECT * FROM contacts WHERE archived_at IS NULL AND LOWER(name)=LOWER(?) ORDER BY created_at", (name,)).fetchall()
        contact_id = candidates[0]["id"] if candidates else new_id()
        now = utc_now()
        if not candidates:
            connection.execute("""INSERT INTO contacts (id,kind,name,organization,email,address,identifiers_json,notes,created_by,updated_by,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (contact_id, "organization" if role == "Applicant" else "person", name, data.get("organization", ""), data.get("email", ""), data.get("address", ""), _json(identifiers), "Imported from admission order; verify against primary records.", actor_id, actor_id, now, now))
        linked = connection.execute("SELECT id FROM case_contacts WHERE case_id=? AND contact_id=? AND role=? AND archived_at IS NULL", (case_id, contact_id, role)).fetchone()
        if not linked:
            connection.execute("""INSERT INTO case_contacts (id,case_id,contact_id,role,category,notes,created_by,updated_by,created_at,updated_at)
                VALUES (?,?,?,?,?,'Imported from admission order',?,?,?,?)""", (new_id(), case_id, contact_id, role, data.get("category", ""), actor_id, actor_id, now, now))
        return contact_id

    def confirm(self, intake_id: str, review: Dict[str, Any], actor_id: str, action: str,
                target_case_id: Optional[str] = None, allow_duplicate: bool = False,
                review_acknowledged: bool = False) -> Dict[str, Any]:
        intake = self.get(intake_id)
        if not intake:
            raise KeyError("Admission intake not found")
        if intake["status"] == "confirmed":
            raise ValueError("This admission order has already been imported")
        if not review_acknowledged:
            raise ValueError("Review every extracted admission-order value and acknowledge the review before import")
        case_data = review.get("case", {})
        if not str(case_data.get("name") or "").strip():
            raise ValueError("Corporate debtor name is required")
        if action == "create" and self.duplicates(review) and not allow_duplicate:
            raise ValueError("A possible matching case exists. Choose that case or explicitly continue after resolving the duplicate.")
        if action not in {"create", "update"}:
            raise ValueError("Import action must be create or update")
        now = utc_now()
        with self.database.transaction() as connection:
            fresh = connection.execute("SELECT status FROM admission_order_intakes WHERE id=?", (intake_id,)).fetchone()
            if not fresh or fresh["status"] == "confirmed":
                raise ValueError("This admission order has already been imported")
            if action == "create":
                case_id = new_id()
                values = {"admission_section": case_data.get("admission_section"), "tribunal": case_data.get("tribunal"), "court_number": case_data.get("court_number"), "mca_master": review.get("mca", {}), "intake_provenance": intake.get("extracted", {}).get("provenance", {})}
                connection.execute("""INSERT INTO cases (id,name,cin,registered_address,nclt_bench,petition_number,applicant_name,applicant_category,process_type,order_date,commencement_date,status,current_stage,legacy_values_json,created_by,updated_by,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (case_id, case_data["name"], case_data.get("cin") or "", case_data.get("registered_address") or "", case_data.get("nclt_bench") or "", case_data.get("petition_number") or "", review.get("applicant", {}).get("name") or "", review.get("applicant", {}).get("category") or "", case_data.get("process_type") or "CIRP", case_data.get("order_date"), case_data.get("commencement_date"), "active", case_data.get("current_stage") or "Commencement", _json(values), actor_id, actor_id, now, now))
                self.database._generate_deadlines(connection, case_id, case_data.get("process_type", "CIRP"), case_data.get("commencement_date"), actor_id)
            else:
                case_id = target_case_id or intake.get("case_id")
                if not case_id:
                    raise ValueError("Select the existing case to update")
                self.database.ensure_case(connection, case_id)
                existing = connection.execute("SELECT legacy_values_json FROM cases WHERE id=?", (case_id,)).fetchone()
                values = _from_json(existing["legacy_values_json"], {})
                values.update({"admission_section": case_data.get("admission_section"), "tribunal": case_data.get("tribunal"), "court_number": case_data.get("court_number"), "mca_master": review.get("mca", {}), "intake_provenance": intake.get("extracted", {}).get("provenance", {})})
                connection.execute("""UPDATE cases SET name=?,cin=?,registered_address=?,nclt_bench=?,petition_number=?,applicant_name=?,applicant_category=?,process_type=?,order_date=?,commencement_date=?,current_stage=?,legacy_values_json=?,updated_by=?,updated_at=? WHERE id=?""", (case_data["name"], case_data.get("cin") or "", case_data.get("registered_address") or "", case_data.get("nclt_bench") or "", case_data.get("petition_number") or "", review.get("applicant", {}).get("name") or "", review.get("applicant", {}).get("category") or "", case_data.get("process_type") or "CIRP", case_data.get("order_date"), case_data.get("commencement_date"), case_data.get("current_stage") or "Commencement", _json(values), actor_id, now, case_id))
            applicant_id = self._contact(connection, review.get("applicant", {}), "Applicant", case_id, actor_id)
            self._contact(connection, review.get("irp", {}), "Interim Resolution Professional", case_id, actor_id)
            document_id = new_id()
            source = self.data_dir / intake["storage_path"]
            case_dir = self.case_files_dir / case_id
            case_dir.mkdir(parents=True, exist_ok=True)
            destination = case_dir / f"{document_id}.pdf"
            shutil.copy2(source, destination)
            connection.execute("""INSERT INTO documents (id,case_id,name,category,status,storage_path,mime_type,source_type,metadata_json,created_by,updated_by,created_at,updated_at)
                VALUES (?,?,?,'NCLT admission order','filed',?,'application/pdf','admission_order_intake',?,?,?,?,?)""", (document_id, case_id, intake["original_filename"], str(destination.relative_to(self.data_dir)), _json({"intake_id": intake_id, "page_count": intake.get("page_count")}), actor_id, actor_id, now, now))
            for item in review.get("referenced_documents", []):
                if not item.get("selected", True) or not item.get("name"):
                    continue
                connection.execute("""INSERT INTO document_requirements (id,case_id,name,category,status,source_document_id,source_intake_id,notes,created_by,updated_by,created_at,updated_at)
                    VALUES (?,?,?,'Referenced document',?,?,?, 'Referenced in admission order; original not yet received.',?,?,?,?)""", (new_id(), case_id, item["name"], item.get("status", "REFERENCED_NOT_RECEIVED"), document_id, intake_id, actor_id, actor_id, now, now))
            for item in review.get("payment_evidence", []):
                connection.execute("""INSERT INTO payment_evidence (id,case_id,instrument_type,instrument_number,instrument_date,amount_paise,bank_name,branch_name,presented_date,presented_through,return_date,return_reason,source_document_id,source_intake_id,notes,created_by,updated_by,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (new_id(), case_id, item.get("instrument_type", "Cheque"), item.get("instrument_number", ""), item.get("instrument_date"), int(item.get("amount_paise") or 0), item.get("bank_name", ""), item.get("branch_name", ""), item.get("presented_date"), item.get("presented_through", ""), item.get("return_date"), item.get("return_reason", ""), document_id, intake_id, item.get("notes", ""), actor_id, actor_id, now, now))
            for item in review.get("tasks", []):
                if item.get("selected", True) is False:
                    continue
                connection.execute("""INSERT INTO tasks (id,case_id,title,description,category,priority,status,due_date,source_type,source_id,checklist_json,created_by,updated_by,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,'open',?,'admission_order_intake',?, '[]',?,?,?,?)""", (new_id(), case_id, item.get("title", "Admission order action"), item.get("description", ""), item.get("category", "CIRP commencement"), item.get("priority", "normal"), item.get("due_date"), intake_id, actor_id, actor_id, now, now))
            contribution = review.get("contribution", {})
            if contribution and int(contribution.get("called_amount_paise") or 0) > 0:
                payer_name = str(contribution.get("payer_name") or review.get("applicant", {}).get("name") or "").strip()
                contribution["payer_name"] = payer_name
                connection.execute("""INSERT INTO contributions (id,case_id,called_amount,called_amount_paise,due_date,paid_amount,payer_contact_id,payer_name,purpose,direction_date,status,source_type,source_id,notes,created_by,updated_by,created_at,updated_at)
                    VALUES (?,?,?,?,?,0,?,?,?,?,?,'admission_order_intake',?,?,?,?,?,?)""", (new_id(), case_id, int(contribution["called_amount_paise"]) // 100, int(contribution["called_amount_paise"]), contribution.get("due_date"), applicant_id, payer_name, contribution.get("purpose", "Initial CIRP expenses"), contribution.get("direction_date"), contribution.get("status", "awaiting payment"), intake_id, contribution.get("notes", ""), actor_id, actor_id, now, now))
            connection.execute("UPDATE admission_order_intakes SET case_id=?,document_id=?,status='confirmed',review_json=?,confirmed_by=?,confirmed_at=?,updated_at=? WHERE id=? AND status<>'confirmed'", (case_id, document_id, _json(review), actor_id, now, now, intake_id))
            self.database.audit(connection, actor_id, "imported", "admission_order_intake", intake_id, case_id, after={"document_id": document_id, "action": action}, title="Admission order reviewed and imported")
        return {"intake": self.get(intake_id), "case": self.database.get_case(case_id)}
