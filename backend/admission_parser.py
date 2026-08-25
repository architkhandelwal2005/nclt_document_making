"""Structured, conservative parser for NCLT admission orders.

The parser separates extraction, candidate discovery and role-based selection.
Every selected value is traceable to a page/block/snippet, while all competing
candidates remain available for the mandatory human review step.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence
import re

from pypdf import PdfReader


CONFIDENCE_SCORES = {"HIGH": 0.95, "MEDIUM": 0.72, "LOW": 0.42, "NOT_FOUND": 0.0}
CIN_PATTERN = re.compile(r"\b[LU][A-Za-z0-9]{20}\b")
VALID_CIN_PATTERN = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.I)
IBBI_PATTERN = re.compile(
    r"IBBI\s*/\s*IPA\s*-\s*\d{3}\s*/\s*IP\s*-\s*[A-Z]\d{5}\s*/\s*\d{4}\s*-\s*\d{4}\s*/\s*\d{5}",
    re.I,
)
DATE_PATTERN = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{4}\b")
LEGAL_ENDING = r"(?:Private\s+Limited|Pvt\.?\s+Ltd\.?|Bank\s+Limited|Limited)"
HONORIFIC = re.compile(r"^(?:Mr|Ms|Mrs|Shri|Smt)\.?\s+", re.I)


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = value.strip().replace("/", "-").replace(".", "-")
    for pattern in ("%d-%m-%Y", "%d-%m-%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, pattern).date().isoformat()
        except ValueError:
            pass
    return None


def _name(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = re.sub(r"^(?:In\s+the\s+matter\s+of\s+|M/s\.?\s*)+", "", _compact(value), flags=re.I).strip(" ,.-")
    value = re.sub(r"\bPvt\.?\s+Ltd\.?\b", "Private Limited", value, flags=re.I)
    if value.isupper():
        value = value.title().replace("&", "&")
    # Title casing turns some legal acronyms into mixed case; these are the
    # only legal suffix terms normalized, not arbitrary words.
    return _compact(value)


def _person(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return HONORIFIC.sub("", _compact(value)).strip(" ,.-")


def _ibbi(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


def _cin(value: str) -> tuple[str, str]:
    """Normalize OCR-like substitutions only in positions that must be digits."""
    raw = _compact(value)
    if len(raw) != 21:
        return raw.upper(), "INVALID_FORMAT"
    chars = list(raw.upper())
    corrected = False
    for index in (*range(1, 6), *range(8, 12), *range(15, 21)):
        replacement = {"I": "1", "L": "1", "O": "0"}.get(chars[index])
        if replacement:
            chars[index] = replacement
            corrected = True
    normalized = "".join(chars)
    if not VALID_CIN_PATTERN.fullmatch(normalized):
        return normalized, "INVALID_FORMAT"
    return normalized, "CONFIRMED_POSITIONAL_CORRECTION" if corrected else "VALID"


def _address(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = _compact(value).strip(" .,:;…")
    replacements = {
        "Floo r": "Floor", "Mad hya": "Madhya", "C hennai": "Chennai",
        "Thiruvanmiyur ,": "Thiruvanmiyur,", "Nadu,India": "Nadu, India",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    value = value.replace("�", '"').replace("“", '"').replace("”", '"')
    value = re.sub(r"\s*[–—‑]\s*", " - ", value)
    value = re.sub(r"\s*-\s*(\d{6})\b", r" - \1", value)
    value = re.sub(r",\s*(\d{6})\b", r" - \1", value)
    value = re.sub(r"\bNo\.?\s+(\d)", r"No. \1", value)
    value = re.sub(r"(\b\d+(?:st|nd|rd|th)\s+Floor)\s+(SH\s+\d+)", r"\1, \2", value, flags=re.I)
    value = re.sub(r"(Survey\s+No\.\s*\d+(?:/\d+)+)\s+(Gram\b)", r"\1, \2", value, flags=re.I)
    value = re.sub(r"\s+,", ",", value)
    value = re.sub(r",(?=\S)", ", ", value)
    return _compact(value)


@dataclass
class TextBlock:
    id: str
    page: int
    index: int
    text: str
    bbox: Optional[List[float]]


@dataclass
class PageModel:
    number: int
    text: str
    blocks: List[TextBlock]
    extraction_method: str
    text_quality: str


@dataclass
class Candidate:
    field: str
    value: Any
    raw: Any
    page: Optional[int]
    block_id: Optional[str]
    source_snippet: str
    confidence: str
    validation_status: str
    role: Optional[str] = None
    reason: str = ""
    selected: bool = False

    def json(self) -> Dict[str, Any]:
        result = asdict(self)
        result["confidence_score"] = CONFIDENCE_SCORES[self.confidence]
        return result


class PageBlockExtractor:
    """Extract page text plus line-level blocks and approximate coordinates."""

    def extract(self, pdf_path: Path) -> List[PageModel]:
        reader = PdfReader(str(pdf_path))
        if reader.is_encrypted:
            raise ValueError("Password-protected admission orders are not supported")
        pages: List[PageModel] = []
        for page_number, page in enumerate(reader.pages, 1):
            fragments: List[tuple[float, float, str, float]] = []

            def visit(text: str, _cm: Sequence[float], tm: Sequence[float], _font: Any, size: float) -> None:
                text = _compact(text)
                if text:
                    fragments.append((float(tm[5]), float(tm[4]), text, float(size or 0)))

            raw = page.extract_text(visitor_text=visit) or ""
            lines = [line for line in (_compact(line) for line in raw.splitlines()) if line]
            blocks: List[TextBlock] = []
            for index, line in enumerate(lines):
                matching = [item for item in fragments if item[2] in line or line in item[2]]
                bbox = None
                if matching:
                    ys = [item[0] for item in matching]
                    xs = [item[1] for item in matching]
                    heights = [max(item[3], 1.0) for item in matching]
                    bbox = [round(min(xs), 2), round(min(ys), 2), round(max(xs) + len(line) * median(heights) * 0.45, 2), round(max(ys) + max(heights), 2)]
                blocks.append(TextBlock(f"p{page_number}-b{index + 1}", page_number, index + 1, line, bbox))
            text = _compact(" ".join(lines))
            quality = "HIGH" if len(text) >= 100 else "LOW"
            pages.append(PageModel(page_number, text, blocks, "pypdf_text_with_visitor_coordinates", quality))
        if sum(len(page.text) for page in pages) < 500:
            raise ValueError("The PDF has no usable embedded text. Run OCR and upload the searchable PDF.")
        return pages


class StructuredAdmissionOrderParser:
    def __init__(self) -> None:
        self.extractor = PageBlockExtractor()

    @staticmethod
    def _snippet(page: PageModel, start: int, end: int, radius: int = 130) -> str:
        return _compact(page.text[max(0, start - radius):min(len(page.text), end + radius)])

    @staticmethod
    def _block(page: PageModel, snippet: str) -> Optional[str]:
        terms = set(re.findall(r"[A-Za-z0-9]{4,}", snippet.lower()))
        ranked = [(len(terms.intersection(re.findall(r"[A-Za-z0-9]{4,}", block.text.lower()))), block.id) for block in page.blocks]
        return max(ranked, default=(0, None))[1]

    def _candidate(self, field: str, value: Any, raw: Any, page: Optional[PageModel], snippet: str,
                   confidence: str, validation: str, role: Optional[str] = None, reason: str = "") -> Candidate:
        return Candidate(field, value, raw, page.number if page else None,
                         self._block(page, snippet) if page else None, snippet,
                         confidence, validation, role, reason)

    def _regex_candidates(self, pages: Iterable[PageModel], field: str, pattern: re.Pattern[str],
                          transform: Callable[[str], Any] = lambda value: value,
                          confidence: str = "MEDIUM", validation: str = "FORMAT_VALID") -> List[Candidate]:
        output: List[Candidate] = []
        for page in pages:
            for match in pattern.finditer(page.text):
                raw = match.group(1) if match.lastindex else match.group(0)
                output.append(self._candidate(field, transform(raw), raw, page,
                                              self._snippet(page, match.start(), match.end()),
                                              confidence, validation))
        return output

    def _all_cins(self, pages: List[PageModel]) -> List[Candidate]:
        candidates: List[Candidate] = []
        for page in pages:
            cause_start = page.text.lower().rfind("in the matter of")
            cause_end = page.text.lower().find("c o r a m")
            versus = page.text.lower().find("versus", cause_start if cause_start >= 0 else 0)
            for match in CIN_PATTERN.finditer(page.text):
                normalized, validation = _cin(match.group(0))
                role = None
                if cause_start >= 0 and (cause_end < 0 or match.start() < cause_end):
                    role = "APPLICANT" if versus < 0 or match.start() < versus else "CORPORATE_DEBTOR"
                else:
                    context = page.text[max(0, match.start() - 180):match.end() + 80].lower()
                    if "financial creditor" in context or "part-i" in context or "part -i" in context:
                        role = "APPLICANT"
                    if "corporate debtor" in context or "part-ii" in context or "part -ii" in context:
                        role = "CORPORATE_DEBTOR"
                candidates.append(self._candidate(
                    "cin", normalized, match.group(0), page, self._snippet(page, match.start(), match.end()),
                    "HIGH" if validation == "VALID" else "MEDIUM", validation, role,
                    "CIN retained before role-based selection",
                ))
        return candidates

    def _entities(self, pages: List[PageModel]) -> tuple[List[Candidate], Optional[Candidate], Optional[Candidate]]:
        candidates: List[Candidate] = []
        applicant: Optional[Candidate] = None
        debtor: Optional[Candidate] = None
        ending = re.compile(rf"(?:M/s\.?\s*)?([A-Z][A-Za-z&. '\-]{{2,100}}?{LEGAL_ENDING})", re.I)
        for page in pages:
            cause_lines = page.blocks
            versus_index = next((index for index, block in enumerate(cause_lines) if re.fullmatch(r"(?:V/?s\.?|Versus)", block.text, re.I)), None)
            for index, block in enumerate(cause_lines):
                for match in ending.finditer(block.text):
                    raw = match.group(1)
                    cleaned = _name(raw)
                    if not cleaned or len(cleaned) < 6:
                        continue
                    role = None
                    confidence = "LOW"
                    reason = "Legal-entity mention retained"
                    if versus_index is not None:
                        if index < versus_index and any("applicant" in row.text.lower() for row in cause_lines[index:min(versus_index + 1, index + 4)]):
                            role, confidence, reason = "APPLICANT", "HIGH", "Cause-title entity before Versus"
                        elif index > versus_index and any(word in " ".join(row.text.lower() for row in cause_lines[index:index + 4]) for word in ("respondent", "corporate debtor")):
                            role, confidence, reason = "CORPORATE_DEBTOR", "HIGH", "Cause-title entity after Versus"
                    candidate = self._candidate("legal_entity", cleaned, raw, page, block.text, confidence, "ENTITY_SUFFIX_VALID", role, reason)
                    candidates.append(candidate)
                    if role == "APPLICANT" and applicant is None:
                        applicant = candidate
                    if role == "CORPORATE_DEBTOR" and debtor is None:
                        debtor = candidate
        # Layout extraction occasionally places the role marker several lines
        # away. Fall back to the explicit cause-title region, never body prose.
        for page in pages[:4]:
            lower = page.text.lower()
            start = lower.rfind("in the matter of")
            versus = lower.find("versus", max(start, 0))
            if versus < 0:
                continue
            if start < 0:
                # Some formal cause titles omit the heading and begin directly
                # with the applicant. Limit the region to the current page.
                start = 0
            end = lower.find("c o r a m", max(versus, 0))
            before, after = page.text[start:versus], page.text[versus:end if end > 0 else None]
            if applicant is None:
                matches = list(ending.finditer(before))
                if matches:
                    match = matches[-1]
                    applicant = self._candidate("legal_entity", _name(match.group(1)), match.group(1), page, _compact(before[-400:]), "HIGH", "ENTITY_SUFFIX_VALID", "APPLICANT", "Cause-title entity before Versus")
                    candidates.append(applicant)
            if debtor is None:
                matches = list(ending.finditer(after))
                if matches:
                    match = matches[0]
                    debtor = self._candidate("legal_entity", _name(match.group(1)), match.group(1), page, _compact(after[:400]), "HIGH", "ENTITY_SUFFIX_VALID", "CORPORATE_DEBTOR", "Cause-title entity after Versus")
                    candidates.append(debtor)
        return candidates, applicant, debtor

    def _addresses(self, pages: List[PageModel]) -> Dict[str, Any]:
        candidates: List[Candidate] = []
        patterns = [
            ("APPLICANT", re.compile(r"Registered\s+[Oo]ffice\s+of\s+the\s+Financial\s+Creditor\s+is\s+situated\s+at\s+(.+?)(?=,\s+and\s+its\s+Branch\s+Office|\.\s+This|,\s+engaged|\.\s+Perusal|\.$)", re.I)),
            ("APPLICANT", re.compile(r"Financial\s+Creditor.{0,520}?having\s+its\s+registered\s+office\s+at\s+(.+?)(?=,\s+engaged|\.\s+Perusal|\.$)", re.I)),
            ("CORPORATE_DEBTOR", re.compile(r"Registered\s+[Oo]ffice\s+of\s+the\s+Corporate\s+Debto\s*r?\s+is\s+situated\s+at\s+(.+?)(?=\.\s+(?:The|Perusal|\d+\.)|\.$)", re.I)),
            ("CORPORATE_DEBTOR", re.compile(r"Corporate\s+Debtor.{0,180}?having\s+its\s+registered\s+office\s+at\s+(.+?)(?=,\s+engaged|\.\s+Perusal|\.$)", re.I)),
            ("CORPORATE_DEBTOR", re.compile(r"(?:^|Page\s+\d+\s+of\s+\d+\s+)situated\s+at\s+(.+?)(?=\.\s+\d+\.)", re.I)),
        ]
        for page in pages:
            for role, pattern in patterns:
                for match in pattern.finditer(page.text):
                    value = _address(match.group(1))
                    if value:
                        candidates.append(self._candidate("address", value, match.group(1), page, self._snippet(page, match.start(), match.end()), "HIGH", "ORDER_DERIVED_NOT_MCA_VERIFIED", role, "Expressly labelled registered office"))
            for match in re.finditer(r"Branch\s+Office(?:\s+is\s+situated|\s+at)?\s*:?\s*(.+?)(?=\.\s+This|\.\s+\d+\.|\s+…\s+Applicant|$)", page.text, re.I):
                value = _address(match.group(1))
                if value:
                    candidates.append(self._candidate("address", value, match.group(1), page, self._snippet(page, match.start(), match.end()), "HIGH", "SECONDARY_ADDRESS", "APPLICANT_BRANCH_OFFICE", "Expressly labelled branch office"))
        # Retain cause-title addresses as secondary evidence. Expressly labelled
        # Part-I/Part-II registered offices outrank these candidates.
        cause_debtor = re.compile(
            rf"Versus\s+(?:M/s\.?\s*)?.+?{LEGAL_ENDING}\s+(?:\[?\s*CIN\s*:\s*[LU][A-Za-z0-9]{{20}}\s*\]?\s*)?(.+?)(?=\s+…\s*Respondent)",
            re.I,
        )
        for page in pages[:4]:
            for match in cause_debtor.finditer(page.text):
                value = _address(match.group(1))
                if value and len(value) >= 15:
                    candidates.append(self._candidate("address", value, match.group(1), page, self._snippet(page, match.start(), match.end()), "MEDIUM", "SECONDARY_CAUSE_TITLE", "CORPORATE_DEBTOR", "Cause-title address retained as a competing candidate"))
        selected: Dict[str, Optional[Candidate]] = {"APPLICANT": None, "CORPORATE_DEBTOR": None}
        for role in selected:
            selected[role] = next((item for item in candidates if item.role == role and item.validation_status == "ORDER_DERIVED_NOT_MCA_VERIFIED"), None)
            if selected[role] is None:
                selected[role] = next((item for item in candidates if item.role == role), None)
        return {"candidates": candidates, "selected": selected}

    def _case_fields(self, pages: List[PageModel]) -> Dict[str, Candidate]:
        fields: Dict[str, Candidate] = {}
        specs = {
            "nclt_bench": (re.compile(r"NATIONAL\s+COMPANY\s+LAW\s+TRIBUNAL\s+(?:BENCH\s+AT\s+)?([A-Z ]+?BENCH)\b", re.I), lambda x: _compact(x).title()),
            "court_number": (re.compile(r"COURT\s*(?:NO\.?|NUMBER)?\s*[-:]?\s*(\d+)", re.I), _compact),
            "case_number": (re.compile(r"CP\s*\(IB\)\s*(?:No\.)?\s*[/ ]?\s*(\d+)(?:\s*\(MP\))?\s*(?:/|of)\s*(\d{4})", re.I), None),
            "ibc_section": (re.compile(r"(?:Under|under)\s+Section\s+(\d+[A-Za-z]?)\s+of\s+the\s+Insolvency", re.I), _compact),
            "order_date": (re.compile(r"(?:Order\s+Pronounced\s+on\s*:|Date\s+of\s+Order\s*:|Delivered\s+on)\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4})", re.I), _date),
        }
        for key, (pattern, transform) in specs.items():
            options: List[Candidate] = []
            for page in pages:
                for match in pattern.finditer(page.text):
                    raw = match.group(1)
                    value = f"CP(IB) {match.group(1)}/{match.group(2)}" if key == "case_number" else transform(raw)  # type: ignore[misc]
                    options.append(self._candidate(key, value, match.group(0), page, self._snippet(page, match.start(), match.end()), "HIGH", "SEMANTIC_ANCHOR_VALID", reason=f"Matched {key.replace('_', ' ')} anchor"))
            if options:
                # Prefer the formal order/cause-title pages over listing-sheet
                # duplicates, while keeping every option in candidate inventory.
                selected = next((item for item in options if item.page and item.page >= 2), options[0]) if key in {"case_number", "order_date"} else options[0]
                selected.selected = True
                fields[key] = selected
                fields[f"_{key}_candidates"] = options  # type: ignore[assignment]
        order = fields.get("order_date")
        commencement: Optional[Candidate] = None
        for page in pages:
            match = re.search(r"commencement\s+of\s+(?:th\s*e\s+)?(?:Corporate\s+Insolvency\s+Resolution\s+Process\s*\(CIRP\)|CIRP)\s+shall\s+be\s+effective\s+from\s+th\s*e\s+date\s+of\s+this\s+order", page.text, re.I)
            if match and order:
                commencement = self._candidate("cirp_commencement_date", order.value, match.group(0), page, self._snippet(page, match.start(), match.end()), "HIGH", "SEMANTIC_REFERENCE_RESOLVED", reason="Operative commencement clause resolves to selected order date")
                commencement.selected = True
                break
        if commencement:
            fields["cirp_commencement_date"] = commencement
        # Merely directing immediate upload is not an upload-date statement.
        fields["order_upload_date"] = self._candidate("order_upload_date", None, None, None, "", "NOT_FOUND", "NOT_FOUND", reason="No explicit order upload date")
        return fields

    def _irp(self, pages: List[PageModel]) -> Dict[str, Any]:
        registrations: List[Candidate] = []
        emails: List[Candidate] = []
        appointed_page: Optional[PageModel] = None
        appointed_reg: Optional[Candidate] = None
        proposed_reg: Optional[Candidate] = None
        for page in pages:
            for match in IBBI_PATTERN.finditer(page.text):
                role = "APPOINTED_IRP" if re.search(r"We\s+appoint|Name\s+of\s+IRP", page.text, re.I) else "PROPOSED_IRP" if re.search(r"proposed|nominated|Part\s*-?\s*III", page.text, re.I) else "UNASSIGNED_IRP"
                candidate = self._candidate("ibbi_registration", _ibbi(match.group(0)), match.group(0), page, self._snippet(page, match.start(), match.end()), "HIGH", "IBBI_FORMAT_VALID", role, "Role assigned from operative/proposal context")
                registrations.append(candidate)
                if role == "APPOINTED_IRP" and appointed_reg is None:
                    appointed_reg, appointed_page = candidate, page
                if role == "PROPOSED_IRP" and proposed_reg is None:
                    proposed_reg = candidate
            for match in EMAIL_PATTERN.finditer(page.text):
                role = "APPOINTED_IRP" if re.search(r"We\s+appoint|Name\s+of\s+IRP", page.text, re.I) else None
                emails.append(self._candidate("email", match.group(0).lower(), match.group(0), page, self._snippet(page, match.start(), match.end()), "HIGH", "EMAIL_FORMAT_VALID", role))

        name_candidate: Optional[Candidate] = None
        address_candidate: Optional[Candidate] = None
        if appointed_page:
            match = re.search(r"Name\s+of\s+IRP\s*:\s*((?:(?:Mr|Ms|Mrs|Shri|Smt)\.?\s+)?[A-Za-z][A-Za-z.' ]{2,70}?)(?=\s+IBBI\s+Reg)", appointed_page.text, re.I)
            if not match:
                match = re.search(r"We\s+appoint\s+((?:(?:Mr|Ms|Mrs|Shri|Smt)\.?\s+)?[A-Za-z][A-Za-z.' ]{2,70}?),\s+Registration", appointed_page.text, re.I)
            if match:
                name_candidate = self._candidate("irp_name", _person(match.group(1)), match.group(1), appointed_page, self._snippet(appointed_page, match.start(), match.end()), "HIGH", "APPOINTMENT_CLAUSE_VALID", "APPOINTED_IRP", "Selected only from operative appointment clause")
            match = re.search(r"Address\s*:\s*(.+?)(?=\s+E-?mail\s*:|\s+iii\.|\s+ii\.|\s+The\s+Moratorium)", appointed_page.text, re.I)
            if match:
                address_candidate = self._candidate("irp_address", _address(match.group(1)), match.group(1), appointed_page, self._snippet(appointed_page, match.start(), match.end()), "HIGH", "APPOINTMENT_BLOCK_VALID", "APPOINTED_IRP", "Address in operative appointment block")
        appointed_email = next((item for item in emails if item.role == "APPOINTED_IRP"), None)

        afa: Optional[Candidate] = None
        form2: Optional[Candidate] = None
        for page in pages:
            match = re.search(r"(?:AFA|Authori[sz]ation\s+for\s+Assignment).*?(?:valid\s+(?:up\s*to|upto|till))\s+(\d{1,2}[./-]\d{1,2}[./-]\d{4})", page.text, re.I)
            if match and afa is None:
                afa = self._candidate("afa_valid_until", _date(match.group(1)), match.group(1), page, self._snippet(page, match.start(), match.end()), "HIGH", "DATE_FORMAT_VALID", "PROPOSED_IRP", "Proposal AFA carried only because registration matches appointed IRP")
            match = re.search(r"Form\s*-?\s*2\s+dated\s+(\d{1,2}[./-]\d{1,2}[./-]\d{4})", page.text, re.I)
            if match and form2 is None:
                form2 = self._candidate("form_2_date", _date(match.group(1)), match.group(1), page, self._snippet(page, match.start(), match.end()), "HIGH", "DATE_FORMAT_VALID", "PROPOSED_IRP")
        if afa and (not appointed_reg or not proposed_reg or appointed_reg.value != proposed_reg.value):
            afa.confidence, afa.validation_status = "LOW", "ROLE_REGISTRATION_CONFLICT"
        selected = {
            "name": name_candidate,
            "registration_number": appointed_reg,
            "address": address_candidate,
            "email": appointed_email,
            "afa_valid_until": afa,
            "form_2_date": form2,
        }
        for item in selected.values():
            if item:
                item.selected = True
        return {"registrations": registrations, "emails": emails, "selected": selected,
                "proposed": {"registration_number": proposed_reg.json() if proposed_reg else None}}

    @staticmethod
    def _selected_value(candidate: Optional[Candidate]) -> Any:
        return candidate.value if candidate and candidate.confidence != "LOW" else None

    def parse(self, pdf_path: Path) -> Dict[str, Any]:
        pages = self.extractor.extract(pdf_path)
        entities, applicant_name, debtor_name = self._entities(pages)
        cins = self._all_cins(pages)
        addresses = self._addresses(pages)
        case_fields = self._case_fields(pages)
        irp = self._irp(pages)

        applicant_cin = next((item for item in cins if item.role == "APPLICANT"), None)
        debtor_cin = next((item for item in cins if item.role == "CORPORATE_DEBTOR"), None)
        applicant_address = addresses["selected"]["APPLICANT"]
        debtor_address = addresses["selected"]["CORPORATE_DEBTOR"]
        for item in (applicant_name, debtor_name, applicant_cin, debtor_cin, applicant_address, debtor_address):
            if item:
                item.selected = True

        selected_irp = irp["selected"]
        selected = {
            "case": {
                "nclt_bench": self._selected_value(case_fields.get("nclt_bench")),
                "court_number": self._selected_value(case_fields.get("court_number")),
                "case_number": self._selected_value(case_fields.get("case_number")),
                "ibc_section": self._selected_value(case_fields.get("ibc_section")),
                "order_date": self._selected_value(case_fields.get("order_date")),
                "cirp_commencement_date": self._selected_value(case_fields.get("cirp_commencement_date")),
                "order_upload_date": None,
            },
            "applicant": {
                "name": self._selected_value(applicant_name), "cin": self._selected_value(applicant_cin),
                "address": self._selected_value(applicant_address),
                "branch_address": next((item.value for item in addresses["candidates"] if item.role == "APPLICANT_BRANCH_OFFICE"), None),
            },
            "corporate_debtor": {
                "name": self._selected_value(debtor_name), "cin": self._selected_value(debtor_cin),
                "address": self._selected_value(debtor_address),
            },
            "irp": {key: self._selected_value(value) for key, value in selected_irp.items() if key != "form_2_date"},
        }

        provenance: Dict[str, Dict[str, Any]] = {}
        mapping = {
            "case.nclt_bench": case_fields.get("nclt_bench"), "case.court_number": case_fields.get("court_number"),
            "case.petition_number": case_fields.get("case_number"), "case.admission_section": case_fields.get("ibc_section"),
            "case.order_date": case_fields.get("order_date"), "case.commencement_date": case_fields.get("cirp_commencement_date"),
            "case.order_upload_date": case_fields.get("order_upload_date"),
            "case.name": debtor_name, "case.cin": debtor_cin, "case.registered_address": debtor_address,
            "applicant.name": applicant_name, "applicant.cin": applicant_cin, "applicant.address": applicant_address,
            "irp.name": selected_irp.get("name"), "irp.registration_number": selected_irp.get("registration_number"),
            "irp.address": selected_irp.get("address"), "irp.email": selected_irp.get("email"),
            "irp.form_2_date": selected_irp.get("form_2_date"), "irp.afa_valid_until": selected_irp.get("afa_valid_until"),
        }
        for key, candidate in mapping.items():
            if candidate:
                provenance[key] = candidate.json()
            else:
                provenance[key] = self._candidate(key, None, None, None, "", "NOT_FOUND", "NOT_FOUND").json()

        candidate_fields: Dict[str, List[Dict[str, Any]]] = {
            "legal_entities": [item.json() for item in entities],
            "cin_candidates": [item.json() for item in cins],
            "address_candidates": [item.json() for item in addresses["candidates"]],
            "ibbi_registration_candidates": [item.json() for item in irp["registrations"]],
            "email_candidates": [item.json() for item in irp["emails"]],
            "date_candidates": [],
        }
        for page in pages:
            for match in DATE_PATTERN.finditer(page.text):
                candidate_fields["date_candidates"].append(self._candidate("date", _date(match.group(0)), match.group(0), page, self._snippet(page, match.start(), match.end()), "LOW", "DATE_FORMAT_VALID", reason="Unassigned date candidate retained").json())
        for key, value in case_fields.items():
            if key.startswith("_"):
                candidate_fields.setdefault(key[1:], []).extend(item.json() for item in value)  # type: ignore[arg-type]

        review = {
            "case": {
                "name": selected["corporate_debtor"]["name"], "cin": selected["corporate_debtor"]["cin"],
                "registered_address": selected["corporate_debtor"]["address"], "process_type": "CIRP",
                "admission_section": selected["case"]["ibc_section"], "tribunal": "NCLT",
                "nclt_bench": selected["case"]["nclt_bench"], "court_number": selected["case"]["court_number"],
                "petition_number": selected["case"]["case_number"], "order_date": selected["case"]["order_date"],
                "commencement_date": selected["case"]["cirp_commencement_date"], "order_upload_date": None,
                "current_stage": "Commencement",
            },
            "applicant": {**selected["applicant"], "category": "Financial creditor"},
            "irp": {**selected["irp"], "form_2_date": self._selected_value(selected_irp.get("form_2_date"))},
        }
        return {
            "schema_version": "2.0", "page_count": len(pages), "extraction_method": "page_block_aware_pypdf",
            "document": {"pages": [{"number": page.number, "extraction_method": page.extraction_method,
                                      "text_quality": page.text_quality, "blocks": [asdict(block) for block in page.blocks]} for page in pages]},
            "entities": [item.json() for item in entities], "candidates": candidate_fields,
            "proposed_irp": irp["proposed"], "selected": selected, "review": review, "provenance": provenance,
        }
