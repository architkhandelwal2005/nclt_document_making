from dotenv import load_dotenv
from pathlib import Path
import os

SOURCE_ROOT = Path(__file__).parent
ROOT_DIR = Path(os.environ.get("CASEFILE_APP_DIR", SOURCE_ROOT))
load_dotenv(ROOT_DIR / ".env")

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import asyncio
import io
import json
import logging
import re
import shutil
import sqlite3
import threading
import uuid

from database import CasefileDatabase, MODULE_FIELDS
from security import encrypt_file, unprotect_bytes
from admission_intake import AdmissionIntakeService
from mca_provider import ManualMcaProvider
from coc_workflow import CocDocumentService
from public_announcement import FORM_FIELDS, build_form_defaults, clean_registered_address, extract_published_pdf, generate_form_a
from nclt_fetcher import NcltOrderFetcherService
from ai import AdmissionAIService, AIServiceError
from ai.claim_bundle_service import ClaimBundleError, ClaimBundleService
from claims_workflow import ClaimsWorkflow, QUERY_TEMPLATES
from claims_coc_core import ClaimsCocCore
from coc_meeting_core import CocMeetingCore
from phase4_core import Phase4Core
from workflow import EventEngine, WorkflowError, WorkflowService

MONGODB_URI = os.environ.get("MONGODB_URI", "")
STORAGE_MODE = os.environ.get("STORAGE_MODE", "local").strip().lower()
if STORAGE_MODE == "mongodb" and MONGODB_URI:
    from pymongo import MongoClient
    mongo_client = MongoClient(MONGODB_URI)
else:
    mongo_client = None
db = mongo_client.casefile_db if mongo_client else None


import bcrypt
import jwt
from docx import Document
from docx.shared import Inches
from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from starlette.middleware.cors import CORSMiddleware

app = FastAPI(title="Casefile Document API", version="2.0.0")
api_router = APIRouter(prefix="/api")

TEMPLATE_DIR = Path(os.environ.get("CASEFILE_TEMPLATE_DIR", ROOT_DIR / "templates"))
CUSTOM_TEMPLATE_DIR = TEMPLATE_DIR / "custom"
CUSTOM_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR = Path(os.environ.get("CASEFILE_DATA_DIR", ROOT_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
CASE_FILES_DIR = DATA_DIR / "files"
CASE_FILES_DIR.mkdir(parents=True, exist_ok=True)
LOCAL_DATA_FILE = DATA_DIR / "casefile.json"
DATABASE_FILE = Path(os.environ.get("CASEFILE_DATABASE_PATH", DATA_DIR / "casefile.db"))
LOCAL_DATA_LOCK = threading.RLock()

JWT_ALGORITHM = "HS256"
JWT_SECRET = os.environ.get("JWT_SECRET", "").strip()
ACCESS_TOKEN_MINUTES = 60 * 12
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
DOC_CACHE_TTL_MINUTES = 30

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").lower().strip()
ADMIN_NAME = os.environ.get("ADMIN_NAME", "Casefile Administrator").strip()
ADMIN_ID = "admin"
admin_pwd = os.environ.get("ADMIN_PASSWORD", "")
if not JWT_SECRET or len(JWT_SECRET) < 32:
    raise RuntimeError("JWT_SECRET must be configured with at least 32 characters")
if not ADMIN_EMAIL or not admin_pwd:
    raise RuntimeError("ADMIN_EMAIL and ADMIN_PASSWORD must be configured")
ADMIN_PASSWORD_HASH = bcrypt.hashpw(admin_pwd.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

# SQLite is authoritative for local application data.  The JSON file is read
# once as a compatibility source and is intentionally never deleted.
casefile_store = CasefileDatabase(DATABASE_FILE)
casefile_store.migrate_legacy_json(LOCAL_DATA_FILE, ADMIN_ID)
casefile_store.ensure_admin(ADMIN_ID, ADMIN_EMAIL, ADMIN_NAME, ADMIN_PASSWORD_HASH)
admission_intakes = AdmissionIntakeService(casefile_store, DATA_DIR, ManualMcaProvider())
coc_documents = CocDocumentService(SOURCE_ROOT / "templates" / "coc")
nclt_fetcher = NcltOrderFetcherService(casefile_store, DATA_DIR)
admission_ai = AdmissionAIService(casefile_store, DATA_DIR)
claim_bundle_ai = ClaimBundleService(casefile_store, DATA_DIR)
phase4 = Phase4Core(casefile_store)

# In-memory state (resets on restart)
LOGIN_ATTEMPTS: Dict[str, Dict[str, Any]] = {}
DOC_CACHE: Dict[str, Dict[str, Any]] = {}


def _empty_local_data() -> Dict[str, Any]:
    return {"matters": [], "profiles": {}, "generated_documents": []}


def _read_local_data() -> Dict[str, Any]:
    """Read local application data without returning a partially written file."""
    with LOCAL_DATA_LOCK:
        if not LOCAL_DATA_FILE.exists():
            return _empty_local_data()
        try:
            data = json.loads(LOCAL_DATA_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logging.getLogger(__name__).error("Could not read local data file: %s", exc)
            raise HTTPException(status_code=500, detail="Local data file is unreadable") from exc
        baseline = _empty_local_data()
        for key, default in baseline.items():
            if not isinstance(data.get(key), type(default)):
                data[key] = default
        return data


def _write_local_data(data: Dict[str, Any]) -> None:
    """Persist JSON atomically so an interrupted save cannot corrupt the main file."""
    with LOCAL_DATA_LOCK:
        temporary = LOCAL_DATA_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(LOCAL_DATA_FILE)


CANONICAL_FIELDS = {
    "cd_name": ("Corporate debtor name", "Corporate debtor", True),
    "cin": ("CIN", "Corporate debtor", True),
    "nclt_bench": ("NCLT bench", "Corporate debtor", True),
    "cp_ib_number": ("CP (IB) number", "Corporate debtor", True),
    "md_name": ("Managing director name", "Corporate debtor", False),
    "cd_address": ("Corporate debtor address", "Corporate debtor", False),
    "ip_name": ("IP name", "Insolvency professional", True),
    "ibbi_reg_no": ("IBBI registration number", "Insolvency professional", True),
    "afa_validity": ("AFA validity date", "Insolvency professional", False),
    "ip_reg_address": ("Registered address", "Insolvency professional", False),
    "ip_email": ("Registered email", "Insolvency professional", False),
    "process_email": ("Process-specific email", "Insolvency professional", False),
    "cirp_order_date": ("CIRP order date", "CIRP timeline", False),
    "order_upload_date": ("Order upload date", "CIRP timeline", False),
    "pa_date": ("Public announcement date", "CIRP timeline", False),
    "claim_cutoff_date": ("Claim cut-off date", "CIRP timeline", False),
    "loc_date": ("LOC / CoC date", "CIRP timeline", False),
    "loc_filing_date": ("LOC filing date", "Filing specifics", False),
    "loc_ia_number": ("LOC IA number", "Filing specifics", False),
    "nclt_fee": ("NCLT filing fee", "Filing specifics", False),
    "meeting_number": ("Meeting number", "Meeting details", False),
    "meeting_date": ("Meeting date", "Meeting details", False),
    "meeting_time": ("Meeting time", "Meeting details", False),
    "meeting_mode": ("Meeting mode", "Meeting details", False),
    "meeting_venue": ("Meeting venue", "Meeting details", False),
    "notice_date": ("Notice date", "Meeting details", False),
    "evoting_link": ("E-voting link", "Meeting details", False),
    "process_bank": ("Process bank and branch", "Financial details", False),
    "initial_funding": ("Initial funding", "Financial details", False),
    "ip_fee": ("IP fee", "Financial details", False),
    "ip_ope": ("IP out-of-pocket expenses", "Financial details", False),
    "valuer_fee_cap": ("Valuer fee cap", "Financial details", False),
}

TABLE_LABELS = {
    "df_creditors": ("Creditors list", ["Sr. No.", "Financial creditor", "Voting share (%)"]),
    "df_suspended_mgmt": ("Suspended management", ["Sr. No.", "Name", "Designation"]),
    "df_expenses": ("CIRP expenses", ["Sr. No.", "Head of expense", "Amount (INR)"]),
}

TEMPLATE_CONFIG = [
    ("voting-agenda", "Voting Agenda", "CoC meeting", "Voting agenda with creditor and expense schedules.", "Voting_Agenda_Template.docx", ["cd_name", "ip_name", "ibbi_reg_no", "meeting_number", "meeting_date", "meeting_time", "ip_fee", "ip_ope", "valuer_fee_cap", "process_bank", "df_creditors", "df_expenses"]),
    ("constitution-coc", "Constitution of CoC", "CIRP constitution", "Formal constitution notice with the creditor composition.", "Constitution_of_CoC_Template.docx", ["loc_date", "cd_name", "cin", "nclt_bench", "cp_ib_number", "claim_cutoff_date", "df_creditors", "ip_name", "ibbi_reg_no", "afa_validity", "process_email", "ip_email", "ip_reg_address"]),
    ("notice-first-coc", "Notice of 1st CoC", "CoC meeting", "Notice for the first meeting of the Committee of Creditors.", "Notice_1st_CoC_Template.docx", ["cd_name", "cirp_order_date", "meeting_date", "meeting_time", "meeting_mode", "notice_date", "evoting_link", "ip_name", "ibbi_reg_no", "ip_email", "ip_fee", "ip_reg_address", "process_bank", "process_email", "df_creditors", "df_suspended_mgmt"]),
    ("notice-second-coc", "Notice of 2nd CoC", "CoC meeting", "Notice for a subsequent meeting of the Committee of Creditors.", "Notice_2nd_CoC_Template.docx", ["cd_name", "cirp_order_date", "meeting_date", "meeting_time", "meeting_mode", "meeting_venue", "notice_date", "evoting_link", "ip_name", "ibbi_reg_no", "afa_validity", "ip_email", "ip_reg_address", "process_email", "df_creditors", "df_suspended_mgmt"]),
    ("minutes-first-coc", "Minutes of 1st CoC", "CoC meeting", "Minutes for the first meeting, populated solely from office-provided minutes text.", "Minutes_1st_CoC_Template.docx", ["cd_name", "meeting_date", "meeting_time", "meeting_mode", "ip_name", "ibbi_reg_no", "df_agenda"]),
    ("minutes-subsequent-coc", "Minutes of subsequent CoC", "CoC meeting", "Minutes for a subsequent meeting, populated solely from office-provided minutes text.", "Minutes_Subsequent_CoC_Template.docx", ["cd_name", "meeting_date", "meeting_time", "meeting_mode", "ip_name", "ibbi_reg_no", "df_agenda"]),
    ("loc-filing", "LOC Filing & CoC Report", "NCLT filing", "Interlocutory application filing with index and list of dates.", "LOC_Filing_Template.docx", ["loc_ia_number", "cp_ib_number", "cd_name", "md_name", "ip_name", "cirp_order_date", "order_upload_date", "pa_date", "claim_cutoff_date", "loc_date", "loc_filing_date", "nclt_fee", "ip_reg_address", "process_email", "ip_email", "ibbi_reg_no", "afa_validity"]),
]


# ---------------- Models ----------------
class LoginInput(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class TemplateFieldSpec(BaseModel):
    key: str
    label: str
    section: str = "Details"
    required: bool = False
    placeholder: str = ""


class TemplateTableSpec(BaseModel):
    key: str
    label: str
    columns: List[str]


class CustomTemplateSpec(BaseModel):
    upload_id: str
    name: str
    category: str = "Custom"
    description: str = ""
    fields: List[TemplateFieldSpec]
    table_inputs: List[TemplateTableSpec] = Field(default_factory=list)


class InspectResult(BaseModel):
    upload_id: str
    detected_fields: List[TemplateFieldSpec]
    detected_tables: List[TemplateTableSpec]


class TemplateRecord(BaseModel):
    id: str
    name: str
    category: str
    description: str
    fields: List[dict]
    table_inputs: List[dict]
    source: str = "builtin"


class DocumentInput(BaseModel):
    case_id: str
    template_id: str
    values: Dict[str, str] = Field(default_factory=dict)
    tables: Dict[str, List[List[str]]] = Field(default_factory=dict)
    notes: str = ""


class GeneratedDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    case_id: str
    template_id: str
    template_name: str
    company_name: str
    status: str
    created_at: str
    values: Dict[str, str]
    tables: Dict[str, List[List[str]]] = Field(default_factory=dict)
    notes: str = ""


# ---------------- Auth ----------------
def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_MINUTES),
        "type": "access",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def admin_user() -> Dict[str, str]:
    stored = casefile_store.get_user(ADMIN_ID)
    if not stored:
        return {"id": ADMIN_ID, "email": ADMIN_EMAIL, "name": ADMIN_NAME, "role": "admin"}
    return {key: stored[key] for key in ("id", "email", "name", "role")}


async def get_current_user(request: Request) -> Dict[str, str]:
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid session token")
    user = casefile_store.get_user(str(payload.get("sub") or ""))
    if not user or user.get("email", "").lower() != str(payload.get("email") or "").lower():
        raise HTTPException(status_code=401, detail="Unknown user")
    if user["role"] in {"read-only", "viewer"} and request.method not in {"GET", "HEAD", "OPTIONS"}:
        raise HTTPException(status_code=403, detail="Read-only users cannot change records")
    return {key: user[key] for key in ("id", "email", "name", "role")}


def require_case_access(case_id: str, current: Dict[str, str]) -> None:
    """Return 404 for unavailable cases so identifiers are not disclosed."""
    if not casefile_store.user_can_access_case(current["id"], current["role"], case_id):
        raise HTTPException(status_code=404, detail="Case not found")


def require_case_creation(current: Dict[str, str]) -> None:
    if current.get("role") not in {"admin", "administrator", "professional", "manager"}:
        raise HTTPException(status_code=403, detail="Your role cannot create company workspaces")


def require_intake_access(record: Dict[str, Any], current: Dict[str, str]) -> None:
    if record.get("case_id"):
        require_case_access(record["case_id"], current)
    elif record.get("uploaded_by") != current["id"] and current.get("role") not in {"admin", "administrator", "professional"}:
        raise HTTPException(status_code=404, detail="Admission intake not found")


def require_nclt_run_access(record: Dict[str, Any], current: Dict[str, str]) -> None:
    if record.get("case_id"):
        require_case_access(record["case_id"], current)
    elif record.get("created_by") != current["id"] and not casefile_store.has_full_case_access(current["role"]):
        raise HTTPException(status_code=404, detail="NCLT fetch run not found")


# ---------------- Template records ----------------
def template_records():
    records = []
    for ident, name, category, description, filename, keys in TEMPLATE_CONFIG:
        fields = [{"key": key, "label": CANONICAL_FIELDS[key][0], "section": CANONICAL_FIELDS[key][1], "required": CANONICAL_FIELDS[key][2], "placeholder": f"Enter {CANONICAL_FIELDS[key][0].lower()}"} for key in keys if key in CANONICAL_FIELDS]
        tables = [{"key": key, "label": TABLE_LABELS[key][0], "columns": TABLE_LABELS[key][1], "required": False} for key in keys if key in TABLE_LABELS]
        records.append({"id": ident, "name": name, "category": category, "description": description, "fields": fields, "table_inputs": tables, "filename": filename, "field_keys": keys})
    return records


TEMPLATES = template_records()


def default_columns_for_key(key: str) -> List[str]:
    if key in TABLE_LABELS:
        return TABLE_LABELS[key][1]
    return ["Sr. No.", "Description", "Value"]


def humanize_key(key: str) -> str:
    if key in TABLE_LABELS:
        return TABLE_LABELS[key][0]
    stripped = re.sub(r"^df_", "", key)
    return re.sub(r"[_\-]+", " ", stripped).strip().title() or key


def canonical_placeholder(raw: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", raw.lower())
    aliases = {re.sub(r"[^a-z0-9]", "", k.lower()): k for k in list(CANONICAL_FIELDS) + list(TABLE_LABELS)}
    return aliases.get(key, raw)


def iter_paragraphs(container):
    for paragraph in getattr(container, "paragraphs", []):
        yield paragraph
    for table in getattr(container, "tables", []):
        for row in table.rows:
            for cell in row.cells:
                yield from iter_paragraphs(cell)


def extract_placeholders(docx_path) -> List[str]:
    doc = Document(str(docx_path))
    pattern = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")
    seen: List[str] = []
    for container in [doc] + [section.header for section in doc.sections] + [section.footer for section in doc.sections]:
        for paragraph in iter_paragraphs(container):
            for match in pattern.finditer(paragraph.text):
                key = match.group(1).strip()
                if key and key not in seen:
                    seen.append(key)
    return seen


def replace_paragraph(paragraph, values):
    token_pattern = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")
    runs = list(paragraph.runs)
    if not runs:
        if token_pattern.search(paragraph.text):
            paragraph.text = token_pattern.sub(lambda match: str(values.get(canonical_placeholder(match.group(1)), "")), paragraph.text)
        return
    combined = "".join(run.text or "" for run in runs)
    matches = list(token_pattern.finditer(combined))
    if not matches and token_pattern.search(paragraph.text):
        paragraph.text = token_pattern.sub(lambda match: str(values.get(canonical_placeholder(match.group(1)), "")), paragraph.text)
        return
    for match in reversed(matches):
        replacement = str(values.get(canonical_placeholder(match.group(1)), ""))
        start_run = end_run = None
        start_offset = end_offset = 0
        cursor = 0
        for index, run in enumerate(runs):
            run_end = cursor + len(run.text or "")
            if start_run is None and cursor <= match.start() < run_end:
                start_run, start_offset = index, match.start() - cursor
            if cursor < match.end() <= run_end:
                end_run, end_offset = index, match.end() - cursor
                break
            cursor = run_end
        if start_run is None or end_run is None:
            continue
        if start_run == end_run:
            text = runs[start_run].text or ""
            runs[start_run].text = text[:start_offset] + replacement + text[end_offset:]
        else:
            start_text = runs[start_run].text or ""
            end_text = runs[end_run].text or ""
            runs[start_run].text = start_text[:start_offset] + replacement
            for index in range(start_run + 1, end_run):
                runs[index].text = ""
            runs[end_run].text = end_text[end_offset:]
    if token_pattern.search(paragraph.text):
        nodes = [node for node in paragraph._p.iter() if node.tag.endswith("}t")]
        combined_xml = "".join(node.text or "" for node in nodes)
        for match in reversed(list(token_pattern.finditer(combined_xml))):
            first = last = None
            first_offset = last_offset = 0
            cursor = 0
            for index, node in enumerate(nodes):
                end = cursor + len(node.text or "")
                if first is None and cursor <= match.start() < end:
                    first, first_offset = index, match.start() - cursor
                if cursor < match.end() <= end:
                    last, last_offset = index, match.end() - cursor
                    break
                cursor = end
            if first is None or last is None:
                continue
            replacement = str(values.get(canonical_placeholder(match.group(1)), ""))
            if first == last:
                text = nodes[first].text or ""
                nodes[first].text = text[:first_offset] + replacement + text[last_offset:]
            else:
                first_text = nodes[first].text or ""
                last_text = nodes[last].text or ""
                nodes[first].text = first_text[:first_offset] + replacement
                for index in range(first + 1, last):
                    nodes[index].text = ""
                nodes[last].text = last_text[last_offset:]


def add_data_table(paragraph, rows):
    if not rows:
        return
    parent = paragraph._parent
    table = parent.add_table(rows=1, cols=max(len(row) for row in rows), width=Inches(6.2))
    table.style = "Table Grid"
    for index, value in enumerate(rows[0]):
        table.rows[0].cells[index].text = str(value)
        for run in table.rows[0].cells[index].paragraphs[0].runs:
            run.bold = True
    for row in rows[1:]:
        cells = table.add_row().cells
        for index, value in enumerate(row):
            cells[index].text = str(value)
    paragraph._p.addnext(table._tbl)


def build_docx(template, values, tables, notes):
    if template.get("source") == "custom" and template.get("docx_data"):
        doc = Document(io.BytesIO(template["docx_data"]))
    else:
        template_dir = template.get("template_dir", TEMPLATE_DIR)
        doc = Document(str(template_dir / template["filename"]))
    replacements = {key: value for key, value in values.items() if value is not None}
    for container in [doc] + [section.header for section in doc.sections] + [section.footer for section in doc.sections]:
        for paragraph in list(iter_paragraphs(container)):
            for table_key, rows in tables.items():
                if f"{{{{ {table_key} }}}}" in paragraph.text or f"{{{{{table_key}}}}}" in paragraph.text:
                    paragraph.text = re.sub(r"\{\{\s*" + re.escape(table_key) + r"\s*\}\}", "", paragraph.text, flags=re.I)
                    add_data_table(paragraph, rows)
            replace_paragraph(paragraph, replacements)
    if notes.strip():
        doc.add_heading("Additional matter notes", level=2)
        doc.add_paragraph(notes.strip())
    stream = io.BytesIO()
    doc.save(stream)
    return stream.getvalue()


def doc_text(template, values, tables, notes):
    if template.get("source") == "custom" and template.get("docx_data"):
        doc = Document(io.BytesIO(template["docx_data"]))
    else:
        template_dir = template.get("template_dir", TEMPLATE_DIR)
        doc = Document(str(template_dir / template["filename"]))
    chunks = []
    for container in [doc] + [section.header for section in doc.sections] + [section.footer for section in doc.sections]:
        for paragraph in iter_paragraphs(container):
            text = re.sub(r"\{\{\s*([^}]+?)\s*\}\}", lambda m: str(values.get(canonical_placeholder(m.group(1)), "")), paragraph.text)
            if text.strip():
                chunks.append(text.strip())
    for key, rows in tables.items():
        chunks.extend(" | ".join(map(str, row)) for row in rows)
    if notes.strip():
        chunks += ["Additional matter notes", notes.strip()]
    return chunks


# ---------------- Custom template file storage ----------------

def _all_custom_templates() -> List[Dict[str, Any]]:
    if db is not None:
        return list(db.templates.find({}, {"docx_data": 0, "_id": 0}))
    
    records = []
    for meta in CUSTOM_TEMPLATE_DIR.glob("*.json"):
        try:
            records.append(json.loads(meta.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    records.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return records

def _load_custom_template(template_id: str) -> Optional[Dict[str, Any]]:
    if db is not None:
        t = db.templates.find_one({"id": template_id}, {"_id": 0})
        return t

    meta = CUSTOM_TEMPLATE_DIR / f"{template_id}.json"
    if not meta.exists():
        return None
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _expand_custom_template(record: Dict[str, Any]) -> Dict[str, Any]:
    expanded = {
        "id": record["id"],
        "name": record["name"],
        "category": record.get("category", "Custom"),
        "description": record.get("description", ""),
        "fields": record.get("fields", []),
        "table_inputs": record.get("table_inputs", []),
        "filename": record["filename"],
        "source": "custom",
        "field_keys": [f["key"] for f in record.get("fields", [])] + [t["key"] for t in record.get("table_inputs", [])],
    }
    if record.get("docx_data") is not None:
        expanded["docx_data"] = record["docx_data"]
    return expanded


def _builtin_public(template: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in template.items() if key not in {"filename", "field_keys"}} | {"source": "builtin"}


def list_all_templates() -> List[Dict[str, Any]]:
    return [_builtin_public(t) for t in TEMPLATES] + [
        {key: value for key, value in _expand_custom_template(c).items() if key not in {"filename", "field_keys"}}
        for c in _all_custom_templates()
    ]


def resolve_template(template_id: str) -> Optional[Dict[str, Any]]:
    for template in TEMPLATES:
        if template["id"] == template_id:
            return {**template, "source": "builtin", "template_dir": TEMPLATE_DIR}
    record = _load_custom_template(template_id)
    if not record:
        return None
    expanded = _expand_custom_template(record)
    expanded["template_dir"] = CUSTOM_TEMPLATE_DIR
    return expanded


# ---------------- Doc cache helpers ----------------
def _cache_prune():
    now = datetime.now(timezone.utc)
    for key in [k for k, v in DOC_CACHE.items() if v["expires_at"] < now]:
        DOC_CACHE.pop(key, None)


def _cache_put(record: Dict[str, Any]) -> None:
    _cache_prune()
    DOC_CACHE[record["id"]] = {**record, "expires_at": datetime.now(timezone.utc) + timedelta(minutes=DOC_CACHE_TTL_MINUTES)}


def _cache_get(doc_id: str) -> Optional[Dict[str, Any]]:
    _cache_prune()
    cached = DOC_CACHE.get(doc_id)
    if cached:
        return cached
    stored = casefile_store.get_generated_document(doc_id)
    if stored:
        _cache_put(stored)
    return stored


def _persist_generated_document(record: Dict[str, Any], actor_id: str) -> None:
    casefile_store.store_generated_document(record, actor_id)


# ---------------- Auth routes ----------------
@api_router.post("/auth/login", response_model=TokenOut)
async def login(payload: LoginInput, request: Request):
    email = payload.email.lower().strip()
    ip = request.client.host if request.client else "unknown"
    identifier = f"{ip}:{email}"
    now = datetime.now(timezone.utc)
    attempt = LOGIN_ATTEMPTS.get(identifier)
    if attempt and attempt.get("locked_until") and attempt["locked_until"] > now:
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again shortly.")
    stored_user = casefile_store.find_user_by_email(email)
    password_ok = bool(stored_user) and bcrypt.checkpw(payload.password.encode("utf-8"), stored_user["password_hash"].encode("utf-8"))
    if not password_ok:
        count = (attempt.get("count", 0) if attempt else 0) + 1
        locked = now + timedelta(minutes=LOCKOUT_MINUTES) if count >= MAX_FAILED_ATTEMPTS else None
        LOGIN_ATTEMPTS[identifier] = {"count": count, "locked_until": locked}
        raise HTTPException(status_code=401, detail="Invalid email or password")
    LOGIN_ATTEMPTS.pop(identifier, None)
    user = {key: stored_user[key] for key in ("id", "email", "name", "role")}
    token = create_access_token(user["id"], user["email"])
    return {"access_token": token, "token_type": "bearer", "user": user}


@api_router.post("/auth/logout")
async def logout(current=Depends(get_current_user)):
    return {"ok": True}


@api_router.get("/auth/me", response_model=UserOut)
async def me(current=Depends(get_current_user)):
    return current



# ---------------- Matter and profile models ----------------
class MatterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=250)
    internal_reference: str = ""
    cin: str = ""
    registered_address: str = ""
    registered_email: str = ""
    industry: str = ""
    nclt_bench: str = ""
    petition_number: str = ""
    applicant_name: str = ""
    applicant_category: str = ""
    process_type: str = "CIRP"
    order_date: Optional[str] = None
    commencement_date: Optional[str] = None
    status: str = "active"
    current_stage: str = "Commencement"
    closure_date: Optional[str] = None
    outcome: str = ""
    assigned_user_id: str = ""
    notes: str = ""
    risk_level: str = "normal"
    values: Dict[str, Any] = Field(default_factory=dict)
    tables: Dict[str, List[List[str]]] = Field(default_factory=dict)
    timeline: Dict[str, Any] = Field(default_factory=dict)


class NcltFetchInput(BaseModel):
    case_number: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9/() .-]+$")
    case_year: int = Field(ge=1990, le=2100)
    bench: str = Field(default="", max_length=40)
    case_type: str = Field(default="", max_length=20)
    case_type_label: str = Field(default="", max_length=100)
    case_id: Optional[str] = None


class NcltCandidateSelection(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=100)


class AIExtractionInput(BaseModel):
    reanalyze: bool = False


class AIReviewInput(BaseModel):
    intake_id: str = Field(min_length=1, max_length=100)
    decisions: Dict[str, Any] = Field(default_factory=dict)


class ClaimBundleCreateInput(BaseModel):
    name: str = Field(default="", max_length=200)
    document_ids: List[str] = Field(default_factory=list)


class ClaimBundleAnalyzeInput(BaseModel):
    reanalyze: bool = False


class ClaimBundleClassificationInput(BaseModel):
    corrections: List[Dict[str, Any]] = Field(default_factory=list)


class ClaimBundleReviewInput(BaseModel):
    decisions: List[Dict[str, Any]] = Field(default_factory=list)
    conflict_resolutions: List[Dict[str, Any]] = Field(default_factory=list)
    accept_all_non_conflicting: bool = False
    confirm: bool = False


class ClaimBundleSuggestionInput(BaseModel):
    action: str = Field(pattern="^(ADDED_TO_QUERY|IGNORED|NOT_REQUIRED)$")


class CocDocumentInput(BaseModel):
    document_type: str = Field(pattern="^(notice|minutes)$")
    status: str = Field(default="review", pattern="^(draft|review|final)$")


class PublicAnnouncementDraftInput(BaseModel):
    values: Dict[str, Any] = Field(default_factory=dict)


class PublicAnnouncementReviewInput(BaseModel):
    values: Dict[str, Any] = Field(default_factory=dict)


class PublicAnnouncementConfirmInput(BaseModel):
    accept_conflicts: bool = False


class WorkflowEventInput(BaseModel):
    event_type: str
    event_date: str
    source_type: str = "manual"
    source_id: str = ""
    source_document_id: Optional[str] = None
    status: str = "CONFIRMED"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = None


class WorkflowStepActionInput(BaseModel):
    reason: str = ""
    remarks: str = ""
    evidence_override_reason: str = ""


class WorkflowAssignmentInput(BaseModel):
    owner_user_id: Optional[str] = None
    checker_user_id: Optional[str] = None
    internal_due_date: Optional[str] = None
    priority: Optional[str] = None


class WorkflowEvidenceInput(BaseModel):
    evidence_type: str
    document_id: Optional[str] = None
    event_id: Optional[str] = None
    source_type: str = "document"
    source_id: str = ""


class WorkflowDeadlineOverrideInput(BaseModel):
    due_date: str
    reason: str


class ConstitutionReportInput(BaseModel):
    status: str = Field(default="DRAFT", pattern="^(DRAFT|FINAL)$")


def require_professional_action(current: Dict[str, str], label: str) -> None:
    if current.get("role") not in {"admin", "administrator", "professional", "manager"}:
        raise HTTPException(status_code=403, detail={"code": "UNAUTHORIZED", "message": f"Only an authorised professional or manager may {label}."})


def _workflow_error(exc: WorkflowError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.as_detail())


def _workflow_services() -> tuple[WorkflowService, EventEngine]:
    return WorkflowService(casefile_store), EventEngine(casefile_store)


def _phase4_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc).strip("'"))
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail={"code": "VDR_ACCESS_DENIED", "message": str(exc)})
    return HTTPException(status_code=422, detail={"code": "PHASE4_VALIDATION", "message": str(exc)})

# ---------------- Matter and profile routes ----------------
@api_router.get("/cases")
@api_router.get("/matters")
async def get_matters(current=Depends(get_current_user)):
    return casefile_store.list_cases(user_id=current["id"], role=current["role"])

@api_router.get("/cases/{matter_id}")
@api_router.get("/matters/{matter_id}")
async def get_matter(matter_id: str, current=Depends(get_current_user)):
    require_case_access(matter_id, current)
    matter = casefile_store.get_case(matter_id)
    if not matter:
        raise HTTPException(status_code=404, detail="Case not found")
    return matter

@api_router.post("/cases")
@api_router.post("/matters")
async def create_matter(payload: MatterInput, current=Depends(get_current_user)):
    require_case_creation(current)
    try:
        created = casefile_store.create_case(payload.model_dump(), current["id"])
        if not casefile_store.has_full_case_access(current["role"]):
            casefile_store.set_case_assignments(created["id"], [current["id"]], current["id"])
        return created
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@api_router.put("/cases/{matter_id}")
@api_router.put("/matters/{matter_id}")
async def update_matter(matter_id: str, payload: MatterInput, current=Depends(get_current_user)):
    require_case_access(matter_id, current)
    try:
        return casefile_store.update_case(matter_id, payload.model_dump(), current["id"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Case not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@api_router.delete("/cases/{matter_id}")
@api_router.delete("/matters/{matter_id}")
async def delete_matter(matter_id: str, current=Depends(get_current_user)):
    require_case_access(matter_id, current)
    if not casefile_store.archive_case(matter_id, current["id"]):
        raise HTTPException(status_code=404, detail="Case not found")
    return {"ok": True}

@api_router.get("/profile")
async def get_profile(current=Depends(get_current_user)):
    return casefile_store.get_profile(current["id"])

@api_router.put("/profile")
async def update_profile(payload: Dict[str, Any], current=Depends(get_current_user)):
    return casefile_store.save_profile(current["id"], payload)


# ---------------- Firm dashboard and case modules ----------------
@api_router.get("/dashboard")
async def get_dashboard(current=Depends(get_current_user)):
    return casefile_store.dashboard(current["id"], current["role"])


@api_router.get("/firm/tasks")
async def get_firm_tasks(current=Depends(get_current_user)):
    return casefile_store.firm_tasks(current["id"], current["role"])


@api_router.get("/firm/calendar")
async def get_firm_calendar(start: Optional[str] = None, end: Optional[str] = None, current=Depends(get_current_user)):
    return casefile_store.firm_calendar(start, end, current["id"], current["role"])


@api_router.get("/compliance-rules")
async def get_compliance_rules(current=Depends(get_current_user)):
    return casefile_store.list_compliance_rules()


@api_router.get("/cases/{case_id}/report")
async def get_case_report(case_id: str, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    try:
        return casefile_store.case_report(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Case not found") from exc


def require_admin(current=Depends(get_current_user)):
    if current.get("role") not in {"admin", "administrator"}:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return current


@api_router.get("/admin/users")
async def list_users(current=Depends(require_admin)):
    return casefile_store.list_users()


@api_router.post("/admin/users")
async def create_user(payload: Dict[str, Any], current=Depends(require_admin)):
    email = str(payload.get("email") or "").lower().strip()
    name = str(payload.get("name") or "").strip()
    password = str(payload.get("password") or "")
    role = str(payload.get("role") or "associate")
    if not email or "@" not in email or not name:
        raise HTTPException(status_code=422, detail="A valid email and name are required")
    if len(password) < 12:
        raise HTTPException(status_code=422, detail="Temporary password must contain at least 12 characters")
    if role not in {"professional", "manager", "associate", "viewer", "staff", "reviewer", "read-only"}:
        raise HTTPException(status_code=422, detail="Invalid role")
    try:
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        user = casefile_store.create_user(email, name, role, password_hash)
        return {key: user[key] for key in ("id", "email", "name", "role", "active", "created_at", "updated_at")}
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):
            raise HTTPException(status_code=409, detail="A user with this email already exists") from exc
        raise


@api_router.put("/admin/users/{user_id}")
async def update_user(user_id: str, payload: Dict[str, Any], current=Depends(require_admin)):
    if user_id == ADMIN_ID and payload.get("active") is False:
        raise HTTPException(status_code=422, detail="The environment administrator cannot be disabled")
    clean = {key: payload[key] for key in ("name", "role", "active") if key in payload}
    if "role" in clean and clean["role"] not in {"admin", "professional", "manager", "associate", "viewer", "staff", "reviewer", "read-only"}:
        raise HTTPException(status_code=422, detail="Invalid role")
    if payload.get("password"):
        if len(str(payload["password"])) < 12:
            raise HTTPException(status_code=422, detail="Password must contain at least 12 characters")
        clean["password_hash"] = bcrypt.hashpw(str(payload["password"]).encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    updated = casefile_store.update_user(user_id, clean)
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    return {key: value for key, value in updated.items() if key != "password_hash"}


@api_router.get("/admin/case-assignments")
async def list_case_assignments(current=Depends(require_admin)):
    return casefile_store.list_case_assignments()


@api_router.put("/admin/cases/{case_id}/assignments")
async def update_case_assignments(case_id: str, payload: Dict[str, Any], current=Depends(require_admin)):
    user_ids = payload.get("user_ids", [])
    if not isinstance(user_ids, list):
        raise HTTPException(status_code=422, detail="user_ids must be a list")
    try:
        return casefile_store.set_case_assignments(case_id, user_ids, current["id"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Case not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@api_router.get("/admin/backup")
async def download_backup(current=Depends(require_admin)):
    backup_path = casefile_store.backup(DATA_DIR / "backups")
    encrypted_path = backup_path.with_suffix(".casefile-backup")
    encrypt_file(backup_path, encrypted_path)
    backup_path.unlink(missing_ok=True)
    return FileResponse(encrypted_path, media_type="application/octet-stream", filename=encrypted_path.name)


@api_router.post("/admin/restore")
async def restore_backup(file: UploadFile = File(...), current=Depends(require_admin)):
    encrypted = await file.read(100 * 1024 * 1024 + 1)
    if len(encrypted) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Backup must not exceed 100 MB")
    try:
        plain = unprotect_bytes(encrypted)
        safety_copy = casefile_store.backup(DATA_DIR / "backups")
        encrypted_safety = safety_copy.with_suffix(".pre-restore.casefile-backup")
        encrypt_file(safety_copy, encrypted_safety)
        safety_copy.unlink(missing_ok=True)
        casefile_store.restore_sqlite_bytes(plain)
        casefile_store.ensure_admin(ADMIN_ID, ADMIN_EMAIL, ADMIN_NAME, ADMIN_PASSWORD_HASH)
        return {"ok": True, "safety_backup": encrypted_safety.name}
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@api_router.get("/admin/security-status")
async def security_status(current=Depends(require_admin)):
    return {
        "database": "SQLite with foreign keys and transaction journaling",
        "authentication": "bcrypt password hashing and signed expiring sessions",
        "authorization": "role-based writes plus backend-enforced case assignments",
        "audit": "immutable activity and before/after audit entries",
        "backup": "consistent SQLite backup encrypted for the current Windows user",
        "file_limit_mb": MAX_CASE_FILE_BYTES // (1024 * 1024),
    }


@api_router.get("/cases/{case_id}/activity")
async def get_case_activity(case_id: str, limit: int = 100, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    try:
        return casefile_store.activity(case_id, limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Case not found") from exc


@api_router.get("/cases/{case_id}/audit")
async def get_case_audit(case_id: str, limit: int = 100, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    if current.get("role") not in {"admin", "administrator"}:
        raise HTTPException(status_code=403, detail="Administrator access required")
    try:
        return casefile_store.audit_history(case_id, limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Case not found") from exc


# ---------------- CIRP workflow / event / deadline engine ----------------
@api_router.get("/workflow/definitions")
async def get_workflow_definitions(workflow_version: Optional[str] = None, current=Depends(get_current_user)):
    workflow, _ = _workflow_services()
    return workflow.definitions(workflow_version)


@api_router.get("/workflow/definitions/{step_code}")
async def get_workflow_definition(step_code: str, workflow_version: Optional[str] = None,
                                  current=Depends(get_current_user)):
    workflow, _ = _workflow_services()
    try:
        return workflow.definition(step_code, workflow_version)
    except WorkflowError as exc:
        raise _workflow_error(exc) from exc


@api_router.post("/cases/{case_id}/workflow/initialize")
async def initialize_case_workflow(case_id: str, payload: Optional[Dict[str, Any]] = None,
                                   current=Depends(get_current_user)):
    require_case_access(case_id, current)
    workflow, _ = _workflow_services()
    try:
        return workflow.initialize_cirp(case_id, current["id"], (payload or {}).get("workflow_version"))
    except WorkflowError as exc:
        raise _workflow_error(exc) from exc


@api_router.get("/cases/{case_id}/workflow")
async def get_case_workflow(case_id: str, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    workflow, _ = _workflow_services()
    try:
        return workflow.get_case_workflow(case_id)
    except WorkflowError as exc:
        raise _workflow_error(exc) from exc


@api_router.get("/cases/{case_id}/workflow/summary")
async def get_case_workflow_summary(case_id: str, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    workflow, _ = _workflow_services()
    try:
        return workflow.summary(case_id)
    except WorkflowError as exc:
        raise _workflow_error(exc) from exc


@api_router.post("/cases/{case_id}/events")
async def record_case_event(case_id: str, payload: WorkflowEventInput, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    _, events = _workflow_services()
    try:
        return events.record_event(
            case_id, payload.event_type, payload.event_date, current["id"], payload.source_type,
            payload.source_id, payload.source_document_id, payload.status, payload.metadata,
            payload.idempotency_key,
        )
    except WorkflowError as exc:
        raise _workflow_error(exc) from exc


@api_router.get("/cases/{case_id}/events")
async def get_case_events(case_id: str, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    _, events = _workflow_services()
    try:
        return events.list_events(case_id)
    except WorkflowError as exc:
        raise _workflow_error(exc) from exc


@api_router.post("/cases/{case_id}/events/{event_id}/confirm")
async def confirm_case_event(case_id: str, event_id: str, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    if current.get("role") not in {"admin", "administrator", "professional", "manager"}:
        raise HTTPException(status_code=403, detail={"code": "UNAUTHORIZED", "message": "Only an authorised professional or manager may confirm a case event."})
    _, events = _workflow_services()
    try:
        return events.confirm_event(case_id, event_id, current["id"])
    except WorkflowError as exc:
        raise _workflow_error(exc) from exc


@api_router.post("/cases/{case_id}/workflow/steps/{step_id}/start")
async def start_workflow_step(case_id: str, step_id: str, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    workflow, _ = _workflow_services()
    try:
        return workflow.start_step(case_id, step_id, current["id"])
    except WorkflowError as exc:
        raise _workflow_error(exc) from exc


@api_router.post("/cases/{case_id}/workflow/steps/{step_id}/complete")
async def complete_workflow_step(case_id: str, step_id: str, payload: WorkflowStepActionInput,
                                 current=Depends(get_current_user)):
    require_case_access(case_id, current)
    if payload.evidence_override_reason and current.get("role") not in {"admin", "administrator", "professional", "manager"}:
        raise HTTPException(status_code=403, detail={"code": "UNAUTHORIZED", "message": "Only an authorised professional or manager may override evidence requirements."})
    workflow, _ = _workflow_services()
    try:
        return workflow.complete_step(
            case_id, step_id, current["id"], payload.remarks, payload.evidence_override_reason,
        )
    except WorkflowError as exc:
        raise _workflow_error(exc) from exc


@api_router.post("/cases/{case_id}/workflow/steps/{step_id}/block")
async def block_workflow_step(case_id: str, step_id: str, payload: WorkflowStepActionInput,
                              current=Depends(get_current_user)):
    require_case_access(case_id, current)
    workflow, _ = _workflow_services()
    try:
        return workflow.block_step(case_id, step_id, payload.reason, current["id"])
    except WorkflowError as exc:
        raise _workflow_error(exc) from exc


@api_router.post("/cases/{case_id}/workflow/steps/{step_id}/waive")
async def waive_workflow_step(case_id: str, step_id: str, payload: WorkflowStepActionInput,
                              current=Depends(get_current_user)):
    require_case_access(case_id, current)
    if current.get("role") not in {"admin", "administrator", "professional", "manager"}:
        raise HTTPException(status_code=403, detail={"code": "UNAUTHORIZED", "message": "Only an authorised professional or manager may waive a workflow step."})
    workflow, _ = _workflow_services()
    try:
        return workflow.waive_step(case_id, step_id, payload.reason, current["id"])
    except WorkflowError as exc:
        raise _workflow_error(exc) from exc


@api_router.post("/cases/{case_id}/workflow/steps/{step_id}/approve")
async def approve_workflow_step(case_id: str, step_id: str, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    if current.get("role") not in {"admin", "administrator", "professional", "manager"}:
        raise HTTPException(status_code=403, detail={"code": "UNAUTHORIZED", "message": "Only an authorised professional or manager may approve a workflow step."})
    workflow, _ = _workflow_services()
    try:
        return workflow.approve_step(case_id, step_id, current["id"])
    except WorkflowError as exc:
        raise _workflow_error(exc) from exc


@api_router.patch("/cases/{case_id}/workflow/steps/{step_id}/assignment")
async def assign_workflow_step(case_id: str, step_id: str, payload: WorkflowAssignmentInput,
                               current=Depends(get_current_user)):
    require_case_access(case_id, current)
    workflow, _ = _workflow_services()
    try:
        return workflow.assign_step(
            case_id, step_id, current["id"], payload.owner_user_id, payload.checker_user_id,
            payload.internal_due_date, payload.priority,
        )
    except WorkflowError as exc:
        raise _workflow_error(exc) from exc


@api_router.post("/cases/{case_id}/workflow/steps/{step_id}/evidence")
async def attach_workflow_evidence(case_id: str, step_id: str, payload: WorkflowEvidenceInput,
                                   current=Depends(get_current_user)):
    require_case_access(case_id, current)
    workflow, _ = _workflow_services()
    try:
        return workflow.attach_evidence(
            case_id, step_id, current["id"], payload.evidence_type, payload.document_id,
            payload.event_id, payload.source_type, payload.source_id,
        )
    except WorkflowError as exc:
        raise _workflow_error(exc) from exc


@api_router.post("/cases/{case_id}/workflow/steps/{step_id}/deadline-override")
async def override_workflow_deadline(case_id: str, step_id: str, payload: WorkflowDeadlineOverrideInput,
                                     current=Depends(get_current_user)):
    require_case_access(case_id, current)
    if current.get("role") not in {"admin", "administrator", "professional", "manager"}:
        raise HTTPException(status_code=403, detail={"code": "UNAUTHORIZED", "message": "Only an authorised professional or manager may override a statutory deadline."})
    workflow, _ = _workflow_services()
    try:
        return workflow.deadlines.override(case_id, step_id, payload.due_date, payload.reason, current["id"])
    except WorkflowError as exc:
        raise _workflow_error(exc) from exc


# ---------------- Admission-order intake ----------------
@api_router.post("/admission-intakes")
async def create_admission_intake(
    file: UploadFile = File(...), case_id: Optional[str] = Form(None), current=Depends(get_current_user)
):
    if case_id:
        require_case_access(case_id, current)
    else:
        require_case_creation(current)
    original_name = Path(file.filename or "admission-order.pdf").name
    if Path(original_name).suffix.lower() != ".pdf":
        raise HTTPException(status_code=422, detail="Admission-order intake accepts searchable PDF files only")
    temporary_dir = DATA_DIR / "intake-uploads"
    temporary_dir.mkdir(parents=True, exist_ok=True)
    temporary = temporary_dir / f"{uuid.uuid4()}.pdf"
    size = 0
    try:
        with temporary.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_CASE_FILE_BYTES:
                    raise HTTPException(status_code=413, detail="Files must not exceed 25 MB")
                output.write(chunk)
        return admission_intakes.create(temporary, original_name, current["id"], case_id or None)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Case not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        temporary.unlink(missing_ok=True)


@api_router.get("/admission-intakes/{intake_id}")
async def get_admission_intake(intake_id: str, current=Depends(get_current_user)):
    record = admission_intakes.get(intake_id)
    if not record:
        raise HTTPException(status_code=404, detail="Admission intake not found")
    require_intake_access(record, current)
    return record


@api_router.put("/admission-intakes/{intake_id}")
async def save_admission_intake(intake_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    record = admission_intakes.get(intake_id)
    if not record:
        raise HTTPException(status_code=404, detail="Admission intake not found")
    require_intake_access(record, current)
    try:
        saved = admission_intakes.save(intake_id, payload.get("review", payload), current["id"])
        if payload.get("ai_job_id"):
            try:
                admission_ai.record_review(str(payload["ai_job_id"]), intake_id, payload.get("ai_review", {}), current["id"])
            except AIServiceError as exc:
                logging.getLogger(__name__).warning("AI review metadata was not saved: %s", exc.code)
        return saved
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@api_router.post("/admission-intakes/{intake_id}/confirm")
async def confirm_admission_intake(intake_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    record = admission_intakes.get(intake_id)
    if not record:
        raise HTTPException(status_code=404, detail="Admission intake not found")
    require_intake_access(record, current)
    action = str(payload.get("action") or "")
    target_case_id = payload.get("target_case_id")
    if action == "create":
        require_case_creation(current)
    elif target_case_id:
        require_case_access(str(target_case_id), current)
    try:
        result = admission_intakes.confirm(
            intake_id, payload.get("review", {}), current["id"],
            action, target_case_id, bool(payload.get("allow_duplicate", False)),
            bool(payload.get("review_acknowledged", False)),
        )
        if payload.get("ai_job_id"):
            try:
                admission_ai.record_review(str(payload["ai_job_id"]), intake_id, payload.get("ai_review", {}), current["id"])
            except AIServiceError as exc:
                logging.getLogger(__name__).warning("AI review metadata was not saved after import: %s", exc.code)
        if action == "create" and not casefile_store.has_full_case_access(current["role"]):
            casefile_store.set_case_assignments(result["case"]["id"], [current["id"]], current["id"])
        case_data = payload.get("review", {}).get("case", {})
        irp_data = payload.get("review", {}).get("irp", {})
        try:
            workflow_event = EventEngine(casefile_store).record_event(
                result["case"]["id"], "ADMISSION_ORDER_CONFIRMED",
                case_data.get("order_date") or case_data.get("commencement_date") or datetime.now().date().isoformat(),
                current["id"], source_type="admission_order_intake", source_id=intake_id,
                source_document_id=result.get("intake", {}).get("document_id"),
                metadata={
                    "cirp_commencement_date": case_data.get("commencement_date"),
                    "irp_appointment_date": irp_data.get("appointment_date") or case_data.get("commencement_date"),
                },
                idempotency_key=f"admission-intake-confirmed:{intake_id}",
            )
            result["workflow_event"] = workflow_event
        except WorkflowError as exc:
            # The reviewed admission import is already durable. Preserve that
            # mature flow and expose an explicit retryable integration warning.
            logging.getLogger(__name__).exception("Admission workflow hook failed: %s", exc.code)
            result["workflow_warning"] = exc.as_detail()
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        detail = str(exc)
        code = 409 if "matching case" in detail or "already" in detail else 422
        raise HTTPException(status_code=code, detail=detail) from exc


@api_router.get("/cases/{case_id}/admission-intakes")
async def list_case_admission_intakes(case_id: str, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    return admission_intakes.list_for_case(case_id)


@api_router.get("/ai/config")
async def get_ai_config(current=Depends(get_current_user)):
    return admission_ai.public_config()


@api_router.get("/admission-intakes/{intake_id}/ai-extraction")
async def get_admission_ai_extraction(intake_id: str, current=Depends(get_current_user)):
    record = admission_intakes.get(intake_id)
    if not record:
        raise HTTPException(status_code=404, detail="Admission intake not found")
    require_intake_access(record, current)
    return admission_ai.latest_for_intake(intake_id) or {}


@api_router.post("/admission-intakes/{intake_id}/ai-extraction")
async def run_admission_ai_extraction(intake_id: str, payload: AIExtractionInput, current=Depends(get_current_user)):
    record = admission_intakes.get(intake_id)
    if not record:
        raise HTTPException(status_code=404, detail="Admission intake not found")
    require_intake_access(record, current)
    if record.get("status") == "confirmed":
        raise HTTPException(status_code=409, detail="Confirmed admission intakes cannot be re-extracted")
    try:
        return await asyncio.to_thread(admission_ai.run, record, current["id"], payload.reanalyze)
    except AIServiceError as exc:
        status = 409 if exc.code in {"AI_DISABLED", "API_KEY_NOT_CONFIGURED", "GROQ_API_KEY_NOT_CONFIGURED"} else 413 if exc.code == "DOCUMENT_TOO_LARGE" else 502
        raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)}) from exc


@api_router.put("/ai-jobs/{job_id}/review")
async def save_ai_review(job_id: str, payload: AIReviewInput, current=Depends(get_current_user)):
    record = admission_intakes.get(payload.intake_id)
    if not record:
        raise HTTPException(status_code=404, detail="Admission intake not found")
    require_intake_access(record, current)
    try:
        return admission_ai.record_review(job_id, payload.intake_id, payload.decisions, current["id"])
    except AIServiceError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc


# ---------------- NCLT Order Fetcher ----------------
@api_router.get("/nclt-fetcher/config")
async def get_nclt_fetcher_config(current=Depends(get_current_user)):
    return nclt_fetcher.public_config()


@api_router.post("/nclt-fetcher/runs")
async def start_nclt_fetch(payload: NcltFetchInput, current=Depends(get_current_user)):
    clean = payload.model_dump()
    config = nclt_fetcher.public_config()
    clean["case_number"] = str(clean["case_number"]).strip()
    clean["bench"] = clean.get("bench") or config["default_bench"]
    clean["case_type"] = clean.get("case_type") or config["default_case_type"]
    clean["case_type_label"] = clean.get("case_type_label") or config["default_case_type_label"]
    if clean.get("case_id"):
        require_case_access(clean["case_id"], current)
    allowed_benches = {item["value"] for item in config["benches"]}
    allowed_types = {item["value"] for item in config["case_types"]}
    if clean["bench"] not in allowed_benches or clean["case_type"] not in allowed_types:
        raise HTTPException(status_code=422, detail="This bench or case type is not configured for the first NCLT fetcher version")
    return await nclt_fetcher.start(clean, current["id"])


@api_router.get("/nclt-fetcher/runs")
async def list_nclt_fetch_history(case_id: Optional[str] = None, limit: int = 20, current=Depends(get_current_user)):
    if case_id:
        require_case_access(case_id, current)
    return nclt_fetcher.list_history(current["id"], case_id, casefile_store.has_full_case_access(current["role"]), limit)


@api_router.get("/nclt-fetcher/runs/{run_id}")
async def get_nclt_fetch_run(run_id: str, current=Depends(get_current_user)):
    record = nclt_fetcher.get_run(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="NCLT fetch run not found")
    require_nclt_run_access(record, current)
    return record


@api_router.post("/nclt-fetcher/runs/{run_id}/continue")
async def continue_nclt_fetch(run_id: str, current=Depends(get_current_user)):
    record = nclt_fetcher.get_run(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="NCLT fetch run not found")
    require_nclt_run_access(record, current)
    return await nclt_fetcher.continue_run(run_id, current["id"])


@api_router.post("/nclt-fetcher/runs/{run_id}/select")
async def select_nclt_fetch_candidate(run_id: str, payload: NcltCandidateSelection, current=Depends(get_current_user)):
    record = nclt_fetcher.get_run(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="NCLT fetch run not found")
    require_nclt_run_access(record, current)
    try:
        return await nclt_fetcher.select_candidate(run_id, payload.candidate_id, current["id"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@api_router.post("/nclt-fetcher/runs/{run_id}/download")
async def download_new_nclt_orders(run_id: str, current=Depends(get_current_user)):
    record = nclt_fetcher.get_run(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="NCLT fetch run not found")
    require_nclt_run_access(record, current)
    return await nclt_fetcher.download_new(run_id, current["id"])


@api_router.get("/nclt-fetcher/records/{record_id}/file")
async def download_nclt_order_file(record_id: str, current=Depends(get_current_user)):
    record = nclt_fetcher.get_record(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="NCLT order file not found")
    run = nclt_fetcher.get_run(record["run_id"])
    if not run:
        raise HTTPException(status_code=404, detail="NCLT order file not found")
    require_nclt_run_access(run, current)
    path = nclt_fetcher.get_record_file(record_id)
    if not path:
        raise HTTPException(status_code=404, detail="NCLT order file not found")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


# ---------------- Public Announcement ----------------
def _latest_confirmed_intake(case_id: str) -> Optional[Dict[str, Any]]:
    return next((item for item in admission_intakes.list_for_case(case_id) if item.get("status") == "confirmed"), None)


def _announcement_context(case_id: str, current: Dict[str, str]) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    case = casefile_store.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    profile = casefile_store.get_profile(current["id"])
    defaults, provenance = build_form_defaults(case, profile, _latest_confirmed_intake(case_id))
    return case, defaults, provenance


def _clean_announcement_values(values: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = {key: values.get(key, "") for key in FORM_FIELDS}
    cleaned["registered_and_principal_address"] = clean_registered_address(
        cleaned.get("registered_and_principal_address")
    )
    return cleaned


def _announcement_conflicts(case: Dict[str, Any], draft: Dict[str, Any], published: Dict[str, Any]) -> List[Dict[str, Any]]:
    existing = {
        "corporate_debtor_name": case.get("name", ""),
        "cin": case.get("cin", ""),
        "cirp_commencement_date": case.get("commencement_date", ""),
        "irp_name": draft.get("irp_name", ""),
        "irp_registration_number": draft.get("irp_registration_number", ""),
        "process_specific_email": draft.get("process_specific_email", ""),
        "claims_submission_last_date": draft.get("claims_submission_last_date", ""),
    }
    conflicts = []
    for key, current_value in existing.items():
        published_value = published.get(key)
        if not current_value or not published_value:
            continue
        normalized_current = re.sub(r"\s+", "", str(current_value)).casefold()
        normalized_published = re.sub(r"\s+", "", str(published_value)).casefold()
        if normalized_current != normalized_published:
            conflicts.append({
                "field": key,
                "existing_value": current_value,
                "published_value": published_value,
                "warning": "Published announcement differs from existing case information.",
            })
    return conflicts


@api_router.get("/cases/{case_id}/public-announcement")
async def get_public_announcement(case_id: str, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    case, defaults, provenance = _announcement_context(case_id, current)
    record = casefile_store.get_public_announcement(case_id)
    admission_confirmed = bool(_latest_confirmed_intake(case_id))
    if record:
        return {**record, "admission_order_confirmed": admission_confirmed}
    return {
        "case_id": case_id, "status": "NOT_STARTED", "draft_data": defaults,
        "provenance": provenance, "published_review": {}, "conflicts": [],
        "admission_order_confirmed": admission_confirmed,
    }


@api_router.put("/cases/{case_id}/public-announcement/draft")
async def save_public_announcement_draft(case_id: str, payload: PublicAnnouncementDraftInput, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    _, defaults, provenance = _announcement_context(case_id, current)
    values = {**defaults, **_clean_announcement_values(payload.values)}
    return casefile_store.save_public_announcement_draft(case_id, values, provenance, current["id"])


@api_router.post("/cases/{case_id}/public-announcement/generate")
async def generate_public_announcement(case_id: str, payload: PublicAnnouncementDraftInput, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    case, defaults, provenance = _announcement_context(case_id, current)
    if not _latest_confirmed_intake(case_id):
        raise HTTPException(status_code=422, detail="Confirm the NCLT admission order before generating Form A")
    values = {**defaults, **_clean_announcement_values(payload.values)}
    required = {
        "corporate_debtor_name": "corporate debtor name",
        "date_of_incorporation": "date of incorporation",
        "registration_authority": "registration authority / RoC location",
        "cin": "CIN",
        "registered_and_principal_address": "registered office address",
        "cirp_commencement_date": "CIRP commencement date",
        "estimated_closure_date": "estimated CIRP closure date",
        "irp_name": "IRP name",
        "irp_registration_number": "IRP registration number",
        "irp_registered_address": "IRP address registered with IBBI",
        "irp_registered_email": "IRP email registered with IBBI",
        "correspondence_address": "correspondence address",
        "process_specific_email": "process-specific email",
        "claims_submission_last_date": "last date for claims",
        "forms_weblink": "relevant forms web link",
        "announcement_date": "Form A date",
        "announcement_place": "Form A place",
    }
    missing = [label for key, label in required.items() if not str(values.get(key) or "").strip()]
    if missing:
        raise HTTPException(status_code=422, detail=f"Complete required Form A fields: {', '.join(missing)}")
    document_token = str(uuid.uuid4())
    output_dir = CASE_FILES_DIR / case_id / "public-announcement"
    safe_company = re.sub(
        r"[^A-Za-z0-9]+", "_", str(values.get("corporate_debtor_name") or case["name"])
    ).strip("_")[:60] or "Company"
    form_date = str(values.get("announcement_date") or datetime.now().date().isoformat())[:10]
    filename = f"Public_Announcement_Form_A_{safe_company}_{form_date}.docx"
    output = output_dir / f"{document_token}.docx"
    try:
        generate_form_a(values, output)
        record = casefile_store.save_public_announcement_draft(
            case_id, values, provenance, current["id"],
            {"name": filename, "storage_path": str(output.relative_to(DATA_DIR)),
             "metadata": {"form": "Form A", "reference_pages": "1-2", "editable": True}},
        )
        EventEngine(casefile_store).record_event(
            case_id, "PUBLIC_ANNOUNCEMENT_DRAFT_READY",
            str(values.get("announcement_date") or datetime.now().date().isoformat())[:10],
            current["id"], source_type="public_announcement", source_id=record["id"],
            source_document_id=record.get("draft_document_id"),
            metadata={"status": record["status"]},
            idempotency_key=f"public-announcement-draft-ready:{record['id']}:{record.get('draft_document_id')}",
        )
        return record
    except Exception:
        output.unlink(missing_ok=True)
        raise


@api_router.post("/cases/{case_id}/public-announcement/ready")
async def finalise_public_announcement(case_id: str, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    try:
        record = casefile_store.set_public_announcement_stage(case_id, "READY_FOR_PUBLICATION", current["id"])
        EventEngine(casefile_store).record_event(
            case_id, "PUBLIC_ANNOUNCEMENT_DRAFT_READY", datetime.now().date().isoformat(), current["id"],
            source_type="public_announcement", source_id=record["id"],
            source_document_id=record.get("draft_document_id"), metadata={"status": record["status"]},
            idempotency_key=f"public-announcement-ready:{record['id']}",
        )
        return record
    except WorkflowError as exc:
        raise _workflow_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@api_router.post("/cases/{case_id}/public-announcement/sent")
async def mark_public_announcement_sent(case_id: str, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    try:
        record = casefile_store.set_public_announcement_stage(case_id, "SENT_FOR_PUBLICATION", current["id"])
        EventEngine(casefile_store).record_event(
            case_id, "PUBLIC_ANNOUNCEMENT_SENT_FOR_PUBLICATION", datetime.now().date().isoformat(), current["id"],
            source_type="public_announcement", source_id=record["id"],
            source_document_id=record.get("draft_document_id"), metadata={"status": record["status"]},
            idempotency_key=f"public-announcement-sent:{record['id']}",
        )
        return record
    except WorkflowError as exc:
        raise _workflow_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@api_router.post("/cases/{case_id}/public-announcement/published")
async def upload_published_public_announcement(case_id: str, file: UploadFile = File(...), current=Depends(get_current_user)):
    require_case_access(case_id, current)
    original_name = Path(file.filename or "published-public-announcement.pdf").name
    if Path(original_name).suffix.lower() != ".pdf":
        raise HTTPException(status_code=422, detail="Published Public Announcement must be uploaded as a PDF")
    token = str(uuid.uuid4())
    target_dir = CASE_FILES_DIR / case_id / "public-announcement"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"published-{token}.pdf"
    size = 0
    try:
        with target.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_CASE_FILE_BYTES:
                    raise HTTPException(status_code=413, detail="Files must not exceed 25 MB")
                output.write(chunk)
        extraction = extract_published_pdf(target)
        existing = casefile_store.get_public_announcement(case_id)
        if not existing:
            raise ValueError("Generate Form A before uploading the published announcement")
        review = {key: extraction.get("fields", {}).get(key, "") for key in FORM_FIELDS}
        conflicts = _announcement_conflicts(casefile_store.get_case(case_id) or {}, existing.get("draft_data", {}), review)
        record = casefile_store.store_published_announcement(
            case_id, original_name, str(target.relative_to(DATA_DIR)), file.content_type or "application/pdf",
            extraction, review, conflicts, current["id"],
        )
        EventEngine(casefile_store).record_event(
            case_id, "PUBLIC_ANNOUNCEMENT_PUBLISHED",
            str(review.get("publication_date") or datetime.now().date().isoformat())[:10],
            current["id"], source_type="public_announcement", source_id=record["id"],
            source_document_id=record.get("published_document_id"), status="PENDING_REVIEW",
            metadata={"status": record["status"], "extraction_method": record.get("extraction_method")},
            idempotency_key=f"public-announcement-published-upload:{record['id']}:{record.get('published_document_id')}",
        )
        return record
    except (ValueError, OSError) as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        target.unlink(missing_ok=True)
        raise


@api_router.put("/cases/{case_id}/public-announcement/published-review")
async def review_published_public_announcement(case_id: str, payload: PublicAnnouncementReviewInput, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    record = casefile_store.get_public_announcement(case_id)
    if not record:
        raise HTTPException(status_code=422, detail="Upload the published announcement before reviewing it")
    review = _clean_announcement_values(payload.values)
    conflicts = _announcement_conflicts(casefile_store.get_case(case_id) or {}, record.get("draft_data", {}), review)
    try:
        return casefile_store.review_published_announcement(case_id, review, conflicts, current["id"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@api_router.post("/cases/{case_id}/public-announcement/confirm")
async def confirm_published_public_announcement(case_id: str, payload: PublicAnnouncementConfirmInput, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    if current.get("role") not in {"admin", "administrator", "professional", "manager"}:
        raise HTTPException(status_code=403, detail="Only an authorised professional or manager may confirm publication")
    try:
        record = casefile_store.confirm_published_announcement(case_id, current["id"], payload.accept_conflicts)
        review = record.get("published_review", {})
        EventEngine(casefile_store).record_event(
            case_id, "PUBLIC_ANNOUNCEMENT_CONFIRMED",
            str(review.get("publication_date") or datetime.now().date().isoformat())[:10],
            current["id"], source_type="public_announcement", source_id=record["id"],
            source_document_id=record.get("published_document_id"),
            metadata={"status": record["status"], "claims_submission_last_date": review.get("claims_submission_last_date")},
            idempotency_key=f"public-announcement-confirmed:{record['id']}:{record.get('published_document_id')}",
        )
        return record
    except WorkflowError as exc:
        raise _workflow_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@api_router.get("/contacts")
async def get_contacts(current=Depends(get_current_user)):
    return casefile_store.list_contacts()


@api_router.get("/cases/{case_id}/contacts")
async def get_case_contacts(case_id: str, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    try:
        return casefile_store.list_contacts(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Case not found") from exc


@api_router.post("/contacts")
async def create_contact(payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return casefile_store.create_contact(payload, current["id"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@api_router.post("/cases/{case_id}/contacts")
async def create_case_contact(case_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    require_case_access(case_id, current)
    try:
        return casefile_store.create_contact(payload, current["id"], case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Case not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _valid_module(module: str) -> None:
    if module not in MODULE_FIELDS:
        raise HTTPException(status_code=404, detail="Unknown case module")


def _validate_coc_module_payload(case_id: str, module: str, payload: Dict[str, Any], record_id: Optional[str] = None) -> Dict[str, Any]:
    """Validate ownership and required workflow invariants before generic CRUD."""
    clean = dict(payload)
    if module == "coc-meetings":
        if "meeting_number" in clean and int(clean["meeting_number"] or 0) < 1:
            raise HTTPException(status_code=422, detail="Meeting number must be at least 1")
        if not record_id and not str(clean.get("meeting_at") or "").strip():
            raise HTTPException(status_code=422, detail="Meeting date and time are required")
        if "quorum_threshold" in clean and not 0 <= float(clean["quorum_threshold"] or 0) <= 100:
            raise HTTPException(status_code=422, detail="Quorum threshold must be between 0 and 100")
    if module in {"coc-agenda-items", "coc-attendance"}:
        meeting_id = str(clean.get("meeting_id") or "")
        if record_id and not meeting_id:
            existing = casefile_store.get_module_record(case_id, module, record_id)
            meeting_id = str((existing or {}).get("meeting_id") or "")
        meeting = casefile_store.get_module_record(case_id, "coc-meetings", meeting_id)
        if not meeting:
            raise HTTPException(status_code=422, detail="Select a CoC meeting belonging to this case")
    if module == "coc-agenda-items":
        if not record_id and not str(clean.get("title") or "").strip():
            raise HTTPException(status_code=422, detail="Agenda title is required")
        if "position" in clean and int(clean["position"] or 0) < 1:
            raise HTTPException(status_code=422, detail="Agenda position must be at least 1")
        if "section" in clean and clean["section"] not in {"discussion", "voting"}:
            raise HTTPException(status_code=422, detail="Agenda section must be discussion or voting")
        clean["voting_required"] = bool(clean.get("voting_required") or clean.get("section") == "voting")
    if module == "coc-attendance":
        if not record_id and not str(clean.get("participant_name") or "").strip():
            raise HTTPException(status_code=422, detail="Participant name is required")
        if "voting_share_snapshot" in clean and not 0 <= float(clean["voting_share_snapshot"] or 0) <= 100:
            raise HTTPException(status_code=422, detail="Voting share must be between 0 and 100")
    return clean


@api_router.get("/cases/{case_id}/{module}")
async def list_case_module(case_id: str, module: str, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    _valid_module(module)
    if module == "claims":
        return ClaimsWorkflow(casefile_store, DATA_DIR).list(case_id)
    try:
        return casefile_store.list_module_records(case_id, module)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc


@api_router.post("/cases/{case_id}/{module}")
async def create_case_module(case_id: str, module: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    require_case_access(case_id, current)
    _valid_module(module)
    if module == "claims":
        try:
            return ClaimsWorkflow(casefile_store, DATA_DIR).create(case_id, payload, current["id"])
        except (ValueError, TypeError, sqlite3.IntegrityError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    payload = _validate_coc_module_payload(case_id, module, payload) if module.startswith("coc-") else payload
    try:
        return casefile_store.create_module_record(case_id, module, payload, current["id"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except (ValueError, TypeError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@api_router.put("/cases/{case_id}/{module}/{record_id}")
async def update_case_module(case_id: str, module: str, record_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    require_case_access(case_id, current)
    _valid_module(module)
    if module == "claims":
        try:
            return ClaimsWorkflow(casefile_store, DATA_DIR).update(case_id, record_id, payload, current["id"])
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
        except (ValueError, TypeError, sqlite3.IntegrityError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    payload = _validate_coc_module_payload(case_id, module, payload, record_id) if module.startswith("coc-") else payload
    try:
        return casefile_store.update_module_record(case_id, module, record_id, payload, current["id"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except (ValueError, TypeError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@api_router.delete("/cases/{case_id}/{module}/{record_id}")
async def archive_case_module(case_id: str, module: str, record_id: str, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    _valid_module(module)
    try:
        if not casefile_store.archive_module_record(case_id, module, record_id, current["id"]):
            raise HTTPException(status_code=404, detail="Record not found")
        return {"ok": True}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------- Structured claims workflow ----------------

def _claims(case_id: str, current: Dict[str, str]) -> ClaimsWorkflow:
    require_case_access(case_id, current)
    return ClaimsWorkflow(casefile_store, DATA_DIR)


@api_router.get("/cases/{case_id}/claims/summary")
async def claim_summary(case_id: str, current=Depends(get_current_user)):
    return _claims(case_id, current).summary(case_id)


@api_router.get("/cases/{case_id}/claims/query-templates")
async def claim_query_templates(case_id: str, current=Depends(get_current_user)):
    _claims(case_id, current)
    return QUERY_TEMPLATES


@api_router.get("/cases/{case_id}/claims/list-of-creditors")
async def list_of_creditors(case_id: str, current=Depends(get_current_user)):
    return _claims(case_id, current).creditors(case_id)


@api_router.get("/cases/{case_id}/claims/list-of-creditors/export/docx")
async def export_list_of_creditors(case_id: str, current=Depends(get_current_user)):
    workflow = _claims(case_id, current)
    export_id = str(uuid.uuid4())
    output = CASE_FILES_DIR / case_id / "claims" / f"list-of-creditors-{export_id}.docx"
    try:
        workflow.export_creditors_docx(case_id, output)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    return FileResponse(output, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=f"List_of_Creditors_{case_id[:8]}.docx")


def _claim_bundle_error(exc: ClaimBundleError) -> HTTPException:
    if exc.code in {
        "CLAIM_NOT_FOUND", "BUNDLE_NOT_FOUND", "DOCUMENT_NOT_FOUND", "SEGMENT_NOT_FOUND",
        "CONFLICT_NOT_FOUND", "SUGGESTION_NOT_FOUND",
    }:
        status = 404
    elif exc.code in {"AI_DISABLED", "API_KEY_NOT_CONFIGURED", "GROQ_API_KEY_NOT_CONFIGURED"}:
        status = 409
    elif exc.code in {"DOCUMENT_TOO_LARGE", "DOCUMENT_CHUNK_TOO_LARGE"}:
        status = 413
    elif exc.code in {"API_TIMEOUT", "API_RATE_LIMIT", "API_PROVIDER_ERROR"}:
        status = 502
    else:
        status = 422
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


@api_router.get("/cases/{case_id}/claims/{claim_id}/ai/config")
async def claim_bundle_ai_config(case_id: str, claim_id: str, current=Depends(get_current_user)):
    _claims(case_id, current).get(case_id, claim_id)
    return claim_bundle_ai.public_config()


@api_router.get("/cases/{case_id}/claims/{claim_id}/ai/bundles")
async def list_claim_bundles(case_id: str, claim_id: str, current=Depends(get_current_user)):
    _claims(case_id, current).get(case_id, claim_id)
    return claim_bundle_ai.list_bundles(case_id, claim_id)


@api_router.post("/cases/{case_id}/claims/{claim_id}/ai/bundles")
async def create_claim_bundle(case_id: str, claim_id: str, payload: ClaimBundleCreateInput, current=Depends(get_current_user)):
    _claims(case_id, current).get(case_id, claim_id)
    try:
        return claim_bundle_ai.create_bundle(case_id, claim_id, current["id"], name=payload.name,
                                             document_ids=payload.document_ids or None)
    except ClaimBundleError as exc:
        raise _claim_bundle_error(exc) from exc


@api_router.get("/cases/{case_id}/claims/{claim_id}/ai/bundles/{bundle_id}")
async def get_claim_bundle(case_id: str, claim_id: str, bundle_id: str, current=Depends(get_current_user)):
    _claims(case_id, current).get(case_id, claim_id)
    try:
        return claim_bundle_ai.get_bundle(case_id, claim_id, bundle_id)
    except ClaimBundleError as exc:
        raise _claim_bundle_error(exc) from exc


@api_router.post("/cases/{case_id}/claims/{claim_id}/ai/bundles/{bundle_id}/analyze")
async def analyze_claim_bundle(case_id: str, claim_id: str, bundle_id: str, payload: ClaimBundleAnalyzeInput,
                               current=Depends(get_current_user)):
    _claims(case_id, current).get(case_id, claim_id)
    try:
        return await asyncio.to_thread(claim_bundle_ai.analyze, case_id, claim_id, bundle_id, current["id"],
                                       reanalyze=payload.reanalyze)
    except ClaimBundleError as exc:
        raise _claim_bundle_error(exc) from exc


@api_router.put("/cases/{case_id}/claims/{claim_id}/ai/bundles/{bundle_id}/classifications")
async def correct_claim_bundle_classifications(case_id: str, claim_id: str, bundle_id: str,
                                                payload: ClaimBundleClassificationInput,
                                                current=Depends(get_current_user)):
    _claims(case_id, current).get(case_id, claim_id)
    try:
        return claim_bundle_ai.correct_segments(case_id, claim_id, bundle_id, payload.corrections, current["id"])
    except ClaimBundleError as exc:
        raise _claim_bundle_error(exc) from exc


@api_router.put("/cases/{case_id}/claims/{claim_id}/ai/bundles/{bundle_id}/review")
async def review_claim_bundle(case_id: str, claim_id: str, bundle_id: str, payload: ClaimBundleReviewInput,
                              current=Depends(get_current_user)):
    _claims(case_id, current).get(case_id, claim_id)
    try:
        result = claim_bundle_ai.review(
            case_id, claim_id, bundle_id, payload.decisions, current["id"],
            accept_all_non_conflicting=payload.accept_all_non_conflicting, confirm=payload.confirm,
            conflict_resolutions=payload.conflict_resolutions,
        )
        return result
    except ClaimBundleError as exc:
        raise _claim_bundle_error(exc) from exc


@api_router.put("/cases/{case_id}/claims/{claim_id}/ai/bundles/{bundle_id}/suggestions/{suggestion_index}")
async def decide_claim_bundle_suggestion(case_id: str, claim_id: str, bundle_id: str, suggestion_index: int,
                                         payload: ClaimBundleSuggestionInput, current=Depends(get_current_user)):
    _claims(case_id, current).get(case_id, claim_id)
    try:
        return claim_bundle_ai.decide_suggestion(case_id, claim_id, bundle_id, suggestion_index, payload.action, current["id"])
    except ClaimBundleError as exc:
        raise _claim_bundle_error(exc) from exc


@api_router.get("/cases/{case_id}/claims/{claim_id}")
async def get_claim(case_id: str, claim_id: str, current=Depends(get_current_user)):
    try:
        return _claims(case_id, current).get(case_id, claim_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc


@api_router.post("/cases/{case_id}/claims/{claim_id}/verification/start")
async def start_claim_verification(case_id: str, claim_id: str, current=Depends(get_current_user)):
    try:
        return _claims(case_id, current).start_verification(case_id, claim_id, current["id"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@api_router.post("/cases/{case_id}/claims/{claim_id}/decision")
async def decide_claim(case_id: str, claim_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    require_professional_action(current, "confirm a Claim decision")
    try:
        return _claims(case_id, current).decide(case_id, claim_id, payload, current["id"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@api_router.post("/cases/{case_id}/claims/{claim_id}/classification/confirm")
async def confirm_claim_classification(case_id: str, claim_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _claims(case_id, current).confirm_classification(case_id, claim_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, KeyError) else 422, detail=str(exc).strip("'")) from exc


@api_router.put("/cases/{case_id}/claims/{claim_id}/scrutiny")
async def update_claim_scrutiny(case_id: str, claim_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _claims(case_id, current).update_scrutiny(case_id, claim_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, KeyError) else 422, detail=str(exc).strip("'")) from exc


@api_router.post("/cases/{case_id}/claims/{claim_id}/verification/complete")
async def complete_claim_verification(case_id: str, claim_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _claims(case_id, current).complete_verification(case_id, claim_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, KeyError) else 422, detail=str(exc).strip("'")) from exc


@api_router.post("/cases/{case_id}/claims/{claim_id}/related-party/confirm")
async def confirm_claim_related_party(case_id: str, claim_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    require_professional_action(current, "confirm related-party status")
    try:
        return _claims(case_id, current).confirm_related_party(case_id, claim_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, KeyError) else 422, detail=str(exc).strip("'")) from exc


@api_router.post("/cases/{case_id}/claims/{claim_id}/security-reviews")
async def record_claim_security_review(case_id: str, claim_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _claims(case_id, current).record_security_review(case_id, claim_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, KeyError) else 422, detail=str(exc).strip("'")) from exc


@api_router.post("/cases/{case_id}/claims/{claim_id}/late-review")
async def record_late_claim_review(case_id: str, claim_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    require_professional_action(current, "confirm a late-Claim review")
    try:
        return _claims(case_id, current).record_late_review(case_id, claim_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, KeyError) else 422, detail=str(exc).strip("'")) from exc


@api_router.post("/cases/{case_id}/claims/{claim_id}/decision-communication")
async def record_claim_decision_communication(case_id: str, claim_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _claims(case_id, current).prepare_decision_communication(case_id, claim_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, KeyError) else 422, detail=str(exc).strip("'")) from exc


@api_router.post("/cases/{case_id}/claims/{claim_id}/revisions")
async def revise_claim(case_id: str, claim_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _claims(case_id, current).revise(case_id, claim_id, payload, current["id"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@api_router.post("/cases/{case_id}/claims/{claim_id}/checklist")
async def add_claim_checklist_item(case_id: str, claim_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _claims(case_id, current).set_checklist(case_id, claim_id, None, payload, current["id"])
    except (KeyError, ValueError) as exc:
        status = 404 if isinstance(exc, KeyError) else 422
        raise HTTPException(status_code=status, detail=str(exc).strip("'")) from exc


@api_router.put("/cases/{case_id}/claims/{claim_id}/checklist/{item_id}")
async def update_claim_checklist_item(case_id: str, claim_id: str, item_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _claims(case_id, current).set_checklist(case_id, claim_id, item_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        status = 404 if isinstance(exc, KeyError) else 422
        raise HTTPException(status_code=status, detail=str(exc).strip("'")) from exc


@api_router.post("/cases/{case_id}/claims/{claim_id}/queries")
async def create_claim_query(case_id: str, claim_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _claims(case_id, current).create_query(case_id, claim_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        status = 404 if isinstance(exc, KeyError) else 422
        raise HTTPException(status_code=status, detail=str(exc).strip("'")) from exc


@api_router.post("/cases/{case_id}/claims/{claim_id}/queries/{query_id}/responses")
async def record_claim_query_response(case_id: str, claim_id: str, query_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _claims(case_id, current).record_response(case_id, claim_id, query_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        status = 404 if isinstance(exc, KeyError) else 422
        raise HTTPException(status_code=status, detail=str(exc).strip("'")) from exc


@api_router.post("/cases/{case_id}/claims/{claim_id}/documents/upload")
async def upload_claim_document(
    case_id: str,
    claim_id: str,
    file: UploadFile = File(...),
    document_type: str = Form("Claim Form"),
    current=Depends(get_current_user),
):
    workflow = _claims(case_id, current)
    try:
        workflow.get(case_id, claim_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    original_name = Path(file.filename or "").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_CASE_FILE_SUFFIXES:
        raise HTTPException(status_code=422, detail="This file type is not allowed")
    case_dir = CASE_FILES_DIR / case_id / "claims"
    case_dir.mkdir(parents=True, exist_ok=True)
    destination = case_dir / f"{uuid.uuid4()}{suffix}"
    size = 0
    try:
        with destination.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_CASE_FILE_BYTES:
                    raise HTTPException(status_code=413, detail="Files must not exceed 25 MB")
                output.write(chunk)
        document = casefile_store.create_module_record(case_id, "documents", {
            "name": original_name, "category": document_type, "status": "filed",
            "storage_path": str(destination.relative_to(DATA_DIR)), "mime_type": file.content_type or "application/octet-stream",
            "source_type": "claim-upload", "linked_type": "claim", "linked_id": claim_id,
            "metadata": {"size": size, "original_name": original_name, "claim_id": claim_id},
        }, current["id"])
        return workflow.link_document(case_id, claim_id, document, document_type, current["id"])
    except Exception:
        if 'document' not in locals():
            destination.unlink(missing_ok=True)
        raise


# ---------------- Claims -> List of Creditors -> CoC core ----------------

def _claims_coc(case_id: str, current: Dict[str, str]) -> ClaimsCocCore:
    require_case_access(case_id, current)
    return ClaimsCocCore(casefile_store)


def _claims_coc_http(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404 if isinstance(exc, KeyError) else 422, detail=str(exc).strip("'"))


def _coc_meetings(case_id: str, current: Dict[str, str]) -> CocMeetingCore:
    require_case_access(case_id, current)
    return CocMeetingCore(casefile_store)


def _coc_meeting_http(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404 if isinstance(exc, KeyError) else 422, detail=str(exc).strip("'"))


@api_router.get("/cases/{case_id}/claims/list-of-creditors/current")
async def get_current_list_of_creditors(case_id: str, current=Depends(get_current_user)):
    return _claims_coc(case_id, current).current_loc(case_id)


@api_router.post("/cases/{case_id}/claims/list-of-creditors/snapshots")
async def create_list_of_creditors_snapshot(case_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _claims_coc(case_id, current).create_loc_snapshot(case_id, current["id"], payload.get("as_on_date"))
    except (KeyError, ValueError) as exc:
        raise _claims_coc_http(exc) from exc


@api_router.get("/cases/{case_id}/claims/list-of-creditors/snapshots")
async def list_list_of_creditors_snapshots(case_id: str, current=Depends(get_current_user)):
    return _claims_coc(case_id, current).list_loc_snapshots(case_id)


@api_router.get("/cases/{case_id}/claims/list-of-creditors/snapshots/{snapshot_id}")
async def get_list_of_creditors_snapshot(case_id: str, snapshot_id: str, current=Depends(get_current_user)):
    try:
        return _claims_coc(case_id, current).get_loc_snapshot(case_id, snapshot_id)
    except (KeyError, ValueError) as exc:
        raise _claims_coc_http(exc) from exc


@api_router.post("/cases/{case_id}/claims/list-of-creditors/snapshots/{snapshot_id}/publication")
async def publish_list_of_creditors_snapshot(case_id: str, snapshot_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    require_professional_action(current, "confirm a List of Creditors filing/display record")
    try:
        return _claims_coc(case_id, current).publish_loc(case_id, snapshot_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _claims_coc_http(exc) from exc


@api_router.get("/cases/{case_id}/coc/candidates")
async def get_coc_candidates(case_id: str, current=Depends(get_current_user)):
    return _claims_coc(case_id, current).coc_candidates(case_id)


@api_router.put("/cases/{case_id}/coc/candidates/{claim_id}/eligibility")
async def confirm_coc_eligibility(case_id: str, claim_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    require_professional_action(current, "confirm CoC eligibility")
    try:
        return _claims_coc(case_id, current).confirm_eligibility(case_id, claim_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _claims_coc_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/voting-calculations")
async def calculate_coc_voting(case_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _claims_coc(case_id, current).calculate_voting(case_id, current["id"], int(payload.get("display_precision", 4)))
    except (KeyError, ValueError) as exc:
        raise _claims_coc_http(exc) from exc


@api_router.get("/cases/{case_id}/coc/voting-calculations/{calculation_id}")
async def get_coc_voting_calculation(case_id: str, calculation_id: str, current=Depends(get_current_user)):
    try:
        return _claims_coc(case_id, current).get_voting_calculation(case_id, calculation_id)
    except (KeyError, ValueError) as exc:
        raise _claims_coc_http(exc) from exc


@api_router.get("/cases/{case_id}/coc/constitution-preview")
async def preview_coc_constitution(case_id: str, calculation_id: Optional[str] = None, current=Depends(get_current_user)):
    return _claims_coc(case_id, current).constitution_preview(case_id, calculation_id)


@api_router.post("/cases/{case_id}/coc/constitutions")
async def confirm_coc_constitution(case_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    require_professional_action(current, "confirm CoC Constitution")
    try:
        return _claims_coc(case_id, current).confirm_constitution(case_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _claims_coc_http(exc) from exc


@api_router.get("/cases/{case_id}/coc/constitutions")
async def list_coc_constitutions(case_id: str, current=Depends(get_current_user)):
    return _claims_coc(case_id, current).list_constitutions(case_id)


@api_router.get("/cases/{case_id}/coc/constitutions/{constitution_id}")
async def get_coc_constitution(case_id: str, constitution_id: str, current=Depends(get_current_user)):
    try:
        return _claims_coc(case_id, current).get_constitution(case_id, constitution_id)
    except (KeyError, ValueError) as exc:
        raise _claims_coc_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/reconstitutions")
async def confirm_coc_reconstitution(case_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    require_professional_action(current, "confirm CoC reconstitution")
    try:
        return _claims_coc(case_id, current).confirm_constitution(case_id, payload, current["id"], reconstitution=True)
    except (KeyError, ValueError) as exc:
        raise _claims_coc_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/constitutions/{constitution_id}/report")
async def generate_coc_constitution_report(case_id: str, constitution_id: str, payload: ConstitutionReportInput,
                                            current=Depends(get_current_user)):
    require_case_access(case_id, current)
    if payload.status == "FINAL":
        require_professional_action(current, "finalize a CoC Constitution Report")
    core = ClaimsCocCore(casefile_store)
    try:
        constitution = core.get_constitution(case_id, constitution_id)
        case = casefile_store.get_case(case_id)
        snapshot = core.get_loc_snapshot(case_id, constitution["loc_snapshot_id"])
        profile = casefile_store.get_profile(current["id"])
        template = resolve_template("constitution-coc")
        if not template:
            raise ValueError("Existing Constitution of CoC office template is unavailable")
        creditor_rows = [[str(index), member["creditor_name"], member["display_voting_percentage"]]
                         for index, member in enumerate(constitution["members"], 1)]
        values = {
            "loc_date": snapshot["as_on_date"], "cd_name": case["name"], "cin": case.get("cin", ""),
            "nclt_bench": case.get("nclt_bench", ""), "cp_ib_number": case.get("petition_number", ""),
            "claim_cutoff_date": snapshot["as_on_date"], "ip_name": profile.get("ip_name") or profile.get("name", ""),
            "ibbi_reg_no": profile.get("ibbi_reg_no", ""), "afa_validity": profile.get("afa_validity", ""),
            "process_email": profile.get("process_email", ""), "ip_email": profile.get("ip_email", ""),
            "ip_reg_address": profile.get("ip_reg_address", ""),
        }
        document_id = str(uuid.uuid4())
        output = CASE_FILES_DIR / case_id / "coc" / f"constitution-report-v{constitution['constitution_version']}-{document_id}.docx"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(build_docx(template, values, {"df_creditors": creditor_rows}, ""))
        document = casefile_store.store_coc_constitution_document(
            case_id, constitution_id, f"CoC Constitution Report Version {constitution['constitution_version']}.docx",
            str(output.relative_to(DATA_DIR)), payload.status.lower(),
            {"constitution_version": constitution["constitution_version"],
             "loc_snapshot_id": constitution["loc_snapshot_id"],
             "voting_calculation_id": constitution["voting_calculation_id"],
             "template_verification_required": True}, current["id"],
        )
        result = core.link_constitution_report(case_id, constitution_id, document["id"], payload.status, current["id"])
        result["report_document"] = document
        return result
    except (KeyError, ValueError) as exc:
        raise _claims_coc_http(exc) from exc


@api_router.get("/cases/{case_id}/coc/creditor-classes")
async def list_creditor_classes(case_id: str, current=Depends(get_current_user)):
    return _claims_coc(case_id, current).list_creditor_classes(case_id)


@api_router.post("/cases/{case_id}/coc/creditor-classes")
async def upsert_creditor_class(case_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _claims_coc(case_id, current).upsert_creditor_class(case_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _claims_coc_http(exc) from exc


@api_router.put("/cases/{case_id}/coc/creditor-classes/{class_id}/authorised-representative")
async def upsert_authorised_representative_process(case_id: str, class_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    require_professional_action(current, "update an authorised representative process")
    try:
        return _claims_coc(case_id, current).upsert_ar_process(case_id, class_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _claims_coc_http(exc) from exc


# ---------------- Phase 3 CoC meeting lifecycle ----------------

@api_router.get("/cases/{case_id}/coc/meetings")
async def list_coc_meetings_v3(case_id: str, current=Depends(get_current_user)):
    return _coc_meetings(case_id, current).list_meetings(case_id)


@api_router.post("/cases/{case_id}/coc/meetings")
async def create_coc_meeting_v3(case_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).create_meeting(case_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.get("/cases/{case_id}/coc/meetings/{meeting_id}")
async def get_coc_meeting_v3(case_id: str, meeting_id: str, current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).get_meeting(case_id, meeting_id)
    except KeyError as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.put("/cases/{case_id}/coc/meetings/{meeting_id}/schedule")
async def schedule_coc_meeting_v3(case_id: str, meeting_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).schedule_meeting(case_id, meeting_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/start")
async def start_coc_meeting_v3(case_id: str, meeting_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).start_meeting(case_id, meeting_id, current["id"], payload.get("actual_start_at"))
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/complete")
async def complete_coc_meeting_v3(case_id: str, meeting_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    require_professional_action(current, "complete a CoC meeting")
    try:
        return _coc_meetings(case_id, current).complete_meeting(case_id, meeting_id, current["id"], payload.get("actual_end_at"))
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/adjourn")
async def adjourn_coc_meeting_v3(case_id: str, meeting_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    require_professional_action(current, "adjourn a CoC meeting")
    try:
        return _coc_meetings(case_id, current).adjourn_meeting(case_id, meeting_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/cancel")
async def cancel_coc_meeting_v3(case_id: str, meeting_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    require_professional_action(current, "cancel a CoC meeting")
    try:
        return _coc_meetings(case_id, current).cancel_meeting(case_id, meeting_id, str(payload.get("reason") or ""), current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.get("/cases/{case_id}/coc/meetings/{meeting_id}/agendas")
async def list_coc_agendas_v3(case_id: str, meeting_id: str, current=Depends(get_current_user)):
    return _coc_meetings(case_id, current).list_agenda_versions(case_id, meeting_id)


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/agendas")
async def create_coc_agenda_v3(case_id: str, meeting_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).create_agenda_version(case_id, meeting_id, current["id"], payload)
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/agendas/{agenda_id}/items")
async def add_coc_agenda_item_v3(case_id: str, meeting_id: str, agenda_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).add_agenda_item(case_id, meeting_id, agenda_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.put("/cases/{case_id}/coc/meetings/{meeting_id}/agendas/{agenda_id}/items/{item_id}")
async def update_coc_agenda_item_v3(case_id: str, meeting_id: str, agenda_id: str, item_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).update_agenda_item(case_id, meeting_id, agenda_id, item_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/agendas/{agenda_id}/finalize")
async def finalize_coc_agenda_v3(case_id: str, meeting_id: str, agenda_id: str, current=Depends(get_current_user)):
    require_professional_action(current, "finalize a CoC Agenda")
    try:
        return _coc_meetings(case_id, current).finalize_agenda(case_id, meeting_id, agenda_id, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.get("/cases/{case_id}/coc/meetings/{meeting_id}/notices")
async def list_coc_notices_v3(case_id: str, meeting_id: str, current=Depends(get_current_user)):
    return _coc_meetings(case_id, current).get_meeting(case_id, meeting_id)["notice_versions"]


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/notices")
async def create_coc_notice_v3(case_id: str, meeting_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).create_notice_draft(case_id, meeting_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/notices/{notice_id}/approve")
async def approve_coc_notice_v3(case_id: str, meeting_id: str, notice_id: str, current=Depends(get_current_user)):
    require_professional_action(current, "approve a CoC Notice")
    try:
        return _coc_meetings(case_id, current).approve_notice(case_id, meeting_id, notice_id, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/notices/{notice_id}/issue")
async def issue_coc_notice_v3(case_id: str, meeting_id: str, notice_id: str, current=Depends(get_current_user)):
    require_professional_action(current, "issue a CoC Notice")
    try:
        return _coc_meetings(case_id, current).issue_notice(case_id, meeting_id, notice_id, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/notices/{notice_id}/dispatches")
async def dispatch_coc_notice_v3(case_id: str, meeting_id: str, notice_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).record_notice_dispatch(case_id, meeting_id, notice_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/coc/quorum-rules")
async def create_coc_quorum_rule_v3(payload: Dict[str, Any], current=Depends(get_current_user)):
    require_professional_action(current, "configure a quorum rule")
    try:
        return CocMeetingCore(casefile_store).upsert_quorum_rule(payload, current["id"])
    except ValueError as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/attendance")
async def record_coc_attendance_v3(case_id: str, meeting_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).record_attendance(case_id, meeting_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/quorum/calculate")
async def calculate_coc_quorum_v3(case_id: str, meeting_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).calculate_quorum(case_id, meeting_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/resolutions")
async def create_coc_resolution_v3(case_id: str, meeting_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).create_resolution(case_id, meeting_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.put("/cases/{case_id}/coc/meetings/{meeting_id}/resolutions/{resolution_id}")
async def update_coc_resolution_v3(case_id: str, meeting_id: str, resolution_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).update_resolution(case_id, meeting_id, resolution_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/resolutions/{resolution_id}/place")
async def place_coc_resolution_v3(case_id: str, meeting_id: str, resolution_id: str, current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).place_resolution(case_id, meeting_id, resolution_id, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/coc/approval-rules")
async def create_coc_approval_rule_v3(payload: Dict[str, Any], current=Depends(get_current_user)):
    require_professional_action(current, "configure an approval rule")
    try:
        return CocMeetingCore(casefile_store).upsert_approval_rule(payload, current["id"])
    except ValueError as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/minutes/entries/{agenda_item_id}")
async def save_coc_minutes_entry_v3(case_id: str, meeting_id: str, agenda_item_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).save_minutes_entry(case_id, meeting_id, agenda_item_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.get("/cases/{case_id}/coc/meetings/{meeting_id}/minutes/validation")
async def validate_coc_minutes_v3(case_id: str, meeting_id: str, current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).validate_minutes(case_id, meeting_id)
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/minutes/drafts")
async def create_coc_minutes_draft_v3(case_id: str, meeting_id: str, current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).create_minutes_version(case_id, meeting_id, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/minutes/{version_id}/finalize")
async def finalize_coc_minutes_v3(case_id: str, meeting_id: str, version_id: str, current=Depends(get_current_user)):
    require_professional_action(current, "finalize CoC Minutes")
    try:
        return _coc_meetings(case_id, current).finalize_minutes(case_id, meeting_id, version_id, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/minutes/{version_id}/circulation")
async def circulate_coc_minutes_v3(case_id: str, meeting_id: str, version_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).record_minutes_circulation(case_id, meeting_id, version_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/voting-sessions")
async def create_coc_voting_session_v3(case_id: str, meeting_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).create_voting_session(case_id, meeting_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/voting-sessions/{session_id}/open")
async def open_coc_voting_v3(case_id: str, meeting_id: str, session_id: str, current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).open_voting(case_id, meeting_id, session_id, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/voting-sessions/{session_id}/resolutions/{resolution_id}/votes")
async def record_coc_vote_v3(case_id: str, meeting_id: str, session_id: str, resolution_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).record_vote(case_id, meeting_id, session_id, resolution_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/voting-sessions/{session_id}/close")
async def close_coc_voting_v3(case_id: str, meeting_id: str, session_id: str, current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).close_voting(case_id, meeting_id, session_id, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/votes/{vote_id}/correct")
async def correct_coc_vote_v3(case_id: str, meeting_id: str, vote_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    require_professional_action(current, "correct a closed CoC vote")
    try:
        return _coc_meetings(case_id, current).correct_vote(case_id, meeting_id, vote_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/voting-sessions/{session_id}/resolutions/{resolution_id}/result")
async def calculate_coc_voting_result_v3(case_id: str, meeting_id: str, session_id: str, resolution_id: str, current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).calculate_voting_result(case_id, meeting_id, session_id, resolution_id, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/voting-results/{result_id}/finalize")
async def finalize_coc_voting_result_v3(case_id: str, meeting_id: str, result_id: str, current=Depends(get_current_user)):
    require_professional_action(current, "finalize a CoC voting result")
    try:
        return _coc_meetings(case_id, current).finalize_voting_result(case_id, meeting_id, result_id, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/cost-statements")
async def record_coc_cost_statement_v3(case_id: str, meeting_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).record_cost_statement(case_id, meeting_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.put("/cases/{case_id}/coc/meetings/{meeting_id}/operations-update")
async def save_coc_operations_update_v3(case_id: str, meeting_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).save_operations_update(case_id, meeting_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/professional-proposals")
async def create_coc_professional_proposal_v3(case_id: str, meeting_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).create_professional_proposal(case_id, meeting_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/actions")
async def create_coc_action_v3(case_id: str, meeting_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        return _coc_meetings(case_id, current).create_action_item(case_id, meeting_id, payload, current["id"])
    except (KeyError, ValueError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.get("/cases/{case_id}/coc/meetings/{meeting_id}/atr")
async def get_coc_atr_v3(case_id: str, meeting_id: str, current=Depends(get_current_user)):
    return _coc_meetings(case_id, current).list_atr(case_id, meeting_id)


def _generate_coc_lifecycle_document(case_id: str, meeting_id: str, kind: str, version_id: str, status: str, current: Dict[str, str]) -> Dict[str, Any]:
    core = _coc_meetings(case_id, current)
    context = core.document_context(case_id, meeting_id, kind, version_id)
    profile = casefile_store.get_profile(current["id"])
    document_id = str(uuid.uuid4())
    output = CASE_FILES_DIR / case_id / "coc" / f"phase3-{kind}-{document_id}.docx"
    output.parent.mkdir(parents=True, exist_ok=True)
    coc_documents.generate(kind, context["case"], context["workflow"], casefile_store.list_contacts(case_id), profile, output)
    meeting_number = int(context["workflow"]["meeting"].get("meeting_number") or 1)
    label = "CoC Notice" if kind == "notice" else "CoC Minutes"
    document = casefile_store.store_coc_document(
        case_id, meeting_id, label,
        f"{label} - Meeting {meeting_number} - {context['case']['name']}.docx",
        str(output.relative_to(DATA_DIR)), status.lower(),
        {"meeting_number": meeting_number, "retained_template": True, "document_type": kind, "lifecycle_version_id": version_id,
         "office_provided_minutes_text_only": kind == "minutes"}, current["id"],
    )
    return {"document": document, "core": core}


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/notices/{notice_id}/document")
async def generate_coc_notice_document_v3(case_id: str, meeting_id: str, notice_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        generated = _generate_coc_lifecycle_document(case_id, meeting_id, "notice", notice_id, str(payload.get("status") or "DRAFT"), current)
        notice = generated["core"].link_notice_document(case_id, meeting_id, notice_id, generated["document"]["id"], current["id"])
        return {"notice": notice, "document": generated["document"]}
    except (KeyError, ValueError, FileNotFoundError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.post("/cases/{case_id}/coc/meetings/{meeting_id}/minutes/{version_id}/document")
async def generate_coc_minutes_document_v3(case_id: str, meeting_id: str, version_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    try:
        generated = _generate_coc_lifecycle_document(case_id, meeting_id, "minutes", version_id, str(payload.get("status") or "DRAFT"), current)
        minutes = generated["core"].link_minutes_document(case_id, meeting_id, version_id, generated["document"]["id"], current["id"])
        return {"minutes": minutes, "document": generated["document"]}
    except (KeyError, ValueError, FileNotFoundError) as exc:
        raise _coc_meeting_http(exc) from exc


@api_router.get("/cases/{case_id}/coc-meetings/{meeting_id}/workflow")
async def get_coc_meeting_workflow(case_id: str, meeting_id: str, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    try:
        return casefile_store.get_coc_workflow(case_id, meeting_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc


@api_router.post("/cases/{case_id}/coc-meetings/{meeting_id}/issue-notice")
async def issue_coc_meeting_notice(case_id: str, meeting_id: str, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    try:
        return casefile_store.issue_coc_notice(case_id, meeting_id, current["id"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _minutes_workflow_from_notice(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """Use the issued notice order/title while overlaying later manual minutes fields."""
    snapshot = workflow.get("meeting", {}).get("notice_snapshot") or {}
    frozen = snapshot.get("agenda") or []
    if not frozen:
        return workflow
    current = {str(item.get("id")): item for item in workflow.get("agenda", [])}
    agenda = []
    for frozen_item in frozen:
        overlay = current.get(str(frozen_item.get("id")), {})
        agenda.append({**frozen_item, **{key: overlay.get(key, frozen_item.get(key, "")) for key in ("discussion", "decision", "resolution_text", "status")}})
    return {**workflow, "agenda": agenda}


@api_router.post("/cases/{case_id}/coc-meetings/{meeting_id}/documents")
async def generate_coc_meeting_document(case_id: str, meeting_id: str, payload: CocDocumentInput, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    case = casefile_store.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        workflow = casefile_store.get_coc_workflow(case_id, meeting_id)
        if payload.document_type == "notice" and payload.status == "final" and not workflow["meeting"].get("notice_snapshot"):
            workflow = casefile_store.issue_coc_notice(case_id, meeting_id, current["id"])
        if payload.document_type == "minutes":
            if not workflow["meeting"].get("notice_snapshot"):
                raise ValueError("Issue the notice before generating minutes so the agenda chain is frozen")
            workflow = _minutes_workflow_from_notice(workflow)
        contacts = casefile_store.list_contacts(case_id)
        profile = casefile_store.get_profile(current["id"])
        document_id = str(uuid.uuid4())
        case_dir = CASE_FILES_DIR / case_id / "coc"
        output = case_dir / f"{document_id}.docx"
        coc_documents.generate(payload.document_type, case, workflow, contacts, profile, output)
        number = int(workflow["meeting"].get("meeting_number") or 1)
        label = "Notice" if payload.document_type == "notice" else "Minutes"
        record = casefile_store.store_coc_document(
            case_id, meeting_id, f"CoC {label}",
            f"{label} of {_ordinal_label(number)} CoC Meeting - {case['name']}.docx",
            str(output.relative_to(DATA_DIR)), payload.status,
            {"meeting_number": number, "retained_template": True, "document_type": payload.document_type},
            current["id"],
        )
        return record
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _ordinal_label(number: int) -> str:
    names = {1: "First", 2: "Second", 3: "Third", 4: "Fourth", 5: "Fifth", 6: "Sixth", 7: "Seventh", 8: "Eighth", 9: "Ninth", 10: "Tenth"}
    return names.get(number, f"Meeting {number}")


ALLOWED_CASE_FILE_SUFFIXES = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".png", ".jpg", ".jpeg"}
MAX_CASE_FILE_BYTES = 25 * 1024 * 1024


@api_router.post("/cases/{case_id}/documents/upload")
async def upload_case_document(case_id: str, file: UploadFile = File(...), category: str = "Uploaded document", current=Depends(get_current_user)):
    require_case_access(case_id, current)
    original_name = Path(file.filename or "").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_CASE_FILE_SUFFIXES:
        raise HTTPException(status_code=422, detail="This file type is not allowed")
    document_id = str(uuid.uuid4())
    case_dir = CASE_FILES_DIR / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    destination = case_dir / f"{document_id}{suffix}"
    size = 0
    try:
        with destination.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_CASE_FILE_BYTES:
                    raise HTTPException(status_code=413, detail="Files must not exceed 25 MB")
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    payload = {
        "name": original_name,
        "category": category,
        "status": "filed",
        "storage_path": str(destination.relative_to(DATA_DIR)),
        "mime_type": file.content_type or "application/octet-stream",
        "source_type": "upload",
        "metadata": {"size": size, "original_name": original_name},
    }
    # Preserve the generated UUID so disk and database identifiers match.
    record = casefile_store.create_module_record(case_id, "documents", payload, current["id"])
    if record["id"] != document_id:
        renamed = case_dir / f"{record['id']}{suffix}"
        destination.rename(renamed)
        record = casefile_store.update_module_record(
            case_id, "documents", record["id"], {"storage_path": str(renamed.relative_to(DATA_DIR))}, current["id"]
        )
    return record


@api_router.get("/cases/{case_id}/documents/{document_id}/file")
async def download_case_file(case_id: str, document_id: str, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    record = casefile_store.get_module_record(case_id, "documents", document_id)
    if not record or not record.get("storage_path"):
        raise HTTPException(status_code=404, detail="Document file not found")
    if str(record.get("confidentiality_classification") or "NORMAL").upper() in {"RESTRICTED_VALUATION", "RESTRICTED_RESOLUTION_PLAN"} and current.get("role") not in {"admin", "administrator", "professional", "manager"}:
        raise HTTPException(status_code=403, detail={"code": "RESTRICTED_DOCUMENT", "message": "Use the controlled VDR access route for this document."})
    candidate = (DATA_DIR / record["storage_path"]).resolve()
    files_root = CASE_FILES_DIR.resolve()
    if files_root not in candidate.parents or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Document file not found")
    return FileResponse(candidate, media_type=record.get("mime_type") or None, filename=record["name"])


# ---------------- Phase 4: operations, valuation, IM and VDR ----------------
@api_router.get("/cases/{case_id}/phase4/{domain}")
async def list_phase4_records(case_id: str, domain: str, record_type: Optional[str] = None, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    try:
        return phase4.list(case_id, domain, record_type.upper() if record_type else None)
    except Exception as exc:
        raise _phase4_error(exc) from exc


@api_router.get("/cases/{case_id}/phase4/records/{record_id}")
async def get_phase4_record(case_id: str, record_id: str, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    try:
        return phase4.get(case_id, record_id)
    except Exception as exc:
        raise _phase4_error(exc) from exc


@api_router.post("/cases/{case_id}/phase4/{domain}/{record_type}")
async def create_phase4_record(case_id: str, domain: str, record_type: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    require_case_access(case_id, current); require_professional_action(current, "create a Phase 4 control record")
    if domain not in {"operations", "cooperation", "section19", "finance", "valuation", "im", "confidentiality", "vdr"}:
        raise HTTPException(status_code=404, detail="Unknown Phase 4 domain")
    try:
        return phase4.create(case_id, domain, record_type.upper(), payload, current["id"])
    except Exception as exc:
        raise _phase4_error(exc) from exc


@api_router.patch("/cases/{case_id}/phase4/records/{record_id}")
async def update_phase4_record(case_id: str, record_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    require_case_access(case_id, current); require_professional_action(current, "update a Phase 4 control record")
    try:
        return phase4.update(case_id, record_id, payload, current["id"])
    except Exception as exc:
        raise _phase4_error(exc) from exc


@api_router.post("/cases/{case_id}/phase4/records/{record_id}/items")
async def add_phase4_item(case_id: str, record_id: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    require_case_access(case_id, current); require_professional_action(current, "record Phase 4 evidence")
    try:
        return phase4.add_item(case_id, record_id, payload, current["id"])
    except Exception as exc:
        raise _phase4_error(exc) from exc


@api_router.post("/cases/{case_id}/phase4/actions/{action}")
async def phase4_action(case_id: str, action: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    """Named controls keep irreversible professional decisions separate from data entry."""
    require_case_access(case_id, current); require_professional_action(current, action.replace("_", " "))
    action = action.lower()
    try:
        actions = {
            "going_concern": lambda: phase4.create_going_concern(case_id, payload, current["id"]),
            "cash_flow": lambda: phase4.create_cash_flow(case_id, payload, current["id"]),
            "receivable": lambda: phase4.create_receivable(case_id, payload, current["id"]),
            "asset_movement": lambda: phase4.asset_movement(case_id, payload, current["id"]),
            "asset_protection": lambda: phase4.asset_protection_incident(case_id, payload, current["id"]),
            "compliance": lambda: phase4.create_compliance(case_id, payload, current["id"]),
            "moratorium": lambda: phase4.create_moratorium_review(case_id, payload, current["id"]),
            "requisition": lambda: phase4.create_requisition(case_id, payload, current["id"]),
            "section19_draft": lambda: phase4.create_section19(case_id, payload, current["id"]),
            "interim_finance": lambda: phase4.interim_finance(case_id, payload, current["id"]),
            "section28": lambda: phase4.section28_review(case_id, payload, current["id"]),
            "valuation": lambda: phase4.valuation_record(case_id, str(payload["record_type"]).upper(), payload, current["id"], payload.get("event_type")),
            "im_initialize": lambda: phase4.initialize_im(case_id, current["id"]),
            "undertaking": lambda: phase4.undertaking(case_id, payload, current["id"]),
            "cost_allocation_snapshot": lambda: phase4.cost_allocation_snapshot(case_id, payload["cost_statement_id"], current["id"], payload.get("allocation_date")),
        }
        if action in actions: return actions[action]()
        if action == "going_concern_approve": return phase4.approve_going_concern(case_id, payload["record_id"], current["id"])
        if action == "cash_flow_transaction": return phase4.cash_flow_transaction(case_id, payload["record_id"], payload, current["id"])
        if action == "cash_flow_finalize": return phase4.finalize_cash_flow(case_id, payload["record_id"], current["id"])
        if action == "receivable_activity": return phase4.receivable_activity(case_id, payload["record_id"], payload, current["id"])
        if action == "requisition_item": return phase4.requisition_item(case_id, payload["record_id"], payload, current["id"])
        if action == "requisition_action": return phase4.requisition_action(case_id, payload["record_id"], payload["action"], payload, current["id"])
        if action == "section19_finalize": return phase4.finalize_section19(case_id, payload["record_id"], payload, current["id"])
        if action == "section19_file": return phase4.file_section19(case_id, payload["record_id"], payload, current["id"])
        if action == "section28_decide": return phase4.decide_section28(case_id, payload["record_id"], payload, current["id"])
        if action == "valuer_declaration_verify": return phase4.verify_valuer_declaration(case_id, payload["record_id"], current["id"], payload.get("status", "VERIFIED"))
        if action == "valuation_assignment_activate": return phase4.activate_assignment(case_id, payload["record_id"], current["id"])
        if action == "im_version": return phase4.im_version(case_id, payload["workspace_id"], payload, current["id"])
        if action == "undertaking_verify": return phase4.verify_undertaking(case_id, payload["record_id"], current["id"], payload.get("status", "VERIFIED"))
        if action == "vdr_grant": return phase4.grant_vdr_access(case_id, payload["workspace_id"], payload["recipient_id"], payload, current["id"])
        if action == "vdr_revoke": return phase4.revoke_vdr_access(case_id, payload["workspace_id"], payload["recipient_id"], current["id"], payload["reason"])
        if action == "vdr_publish_document": return phase4.publish_vdr_document(case_id, payload["workspace_id"], payload["document_id"], payload, current["id"])
        raise HTTPException(status_code=404, detail="Unknown Phase 4 action")
    except HTTPException: raise
    except Exception as exc:
        raise _phase4_error(exc) from exc

# ---------------- Root ----------------
@api_router.get("/")
async def root():
    return {"message": "Casefile practice management API ready"}


# ---------------- Template routes ----------------
@api_router.get("/templates", response_model=List[TemplateRecord])
async def get_templates(current=Depends(get_current_user)):
    return list_all_templates()


@api_router.post("/templates/inspect", response_model=InspectResult)
async def inspect_template(file: UploadFile = File(...), current=Depends(get_current_user)):
    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are supported")
    upload_id = str(uuid.uuid4())
    dest = CUSTOM_TEMPLATE_DIR / f"upload-{upload_id}.docx"
    with dest.open("wb") as sink:
        shutil.copyfileobj(file.file, sink)
    try:
        keys = extract_placeholders(dest)
    except Exception as exc:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Could not read DOCX: {exc}")
    if not keys:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="No {{placeholders}} were found in this document")
    detected_fields: List[TemplateFieldSpec] = []
    detected_tables: List[TemplateTableSpec] = []
    for key in keys:
        if key.startswith("df_") or key.lower().startswith(("table_", "list_")):
            detected_tables.append(TemplateTableSpec(key=key, label=humanize_key(key), columns=default_columns_for_key(key)))
        else:
            canonical = CANONICAL_FIELDS.get(canonical_placeholder(key))
            if canonical:
                detected_fields.append(TemplateFieldSpec(key=canonical_placeholder(key), label=canonical[0], section=canonical[1], required=canonical[2], placeholder=f"Enter {canonical[0].lower()}"))
            else:
                detected_fields.append(TemplateFieldSpec(key=key, label=humanize_key(key), section="Details", required=False, placeholder=f"Enter {humanize_key(key).lower()}"))
    return InspectResult(upload_id=upload_id, detected_fields=detected_fields, detected_tables=detected_tables)


@api_router.post("/templates", response_model=TemplateRecord)
async def save_custom_template(spec: CustomTemplateSpec, current=Depends(get_current_user)):
    src = CUSTOM_TEMPLATE_DIR / f"upload-{spec.upload_id}.docx"
    if not src.exists():
        raise HTTPException(status_code=404, detail="Upload not found. Please re-upload the template.")
    template_id = f"custom-{uuid.uuid4().hex[:10]}"
    filename = f"{template_id}.docx"
    
    docx_bytes = src.read_bytes()
    
    record = {
        "id": template_id,
        "name": spec.name.strip() or "Custom template",
        "category": spec.category.strip() or "Custom",
        "description": spec.description.strip(),
        "fields": [f.model_dump() for f in spec.fields],
        "table_inputs": [t.model_dump() for t in spec.table_inputs],
        "filename": filename,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    
    if db is not None:
        db.templates.insert_one({**record, "docx_data": docx_bytes})
        src.unlink(missing_ok=True)
    else:
        src.rename(CUSTOM_TEMPLATE_DIR / filename)
        (CUSTOM_TEMPLATE_DIR / f"{template_id}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        
    expanded = _expand_custom_template(record)
    return {key: value for key, value in expanded.items() if key not in {"filename", "field_keys", "template_dir", "docx_data"}}



@api_router.delete("/templates/{template_id}")
async def delete_custom_template(template_id: str, current=Depends(get_current_user)):
    if db is not None:
        db.templates.delete_one({"id": template_id})
        return {"ok": True}
        
    record = _load_custom_template(template_id)
    if not record:
        raise HTTPException(status_code=404, detail="Custom template not found")
    (CUSTOM_TEMPLATE_DIR / record["filename"]).unlink(missing_ok=True)
    (CUSTOM_TEMPLATE_DIR / f"{template_id}.json").unlink(missing_ok=True)
    return {"ok": True}



# ---------------- Document generation (cache-only, no DB) ----------------
@api_router.post("/documents", response_model=GeneratedDocument)
async def generate_document(payload: DocumentInput, current=Depends(get_current_user)):
    require_case_access(payload.case_id, current)
    matter = casefile_store.get_case(payload.case_id)
    if not matter:
        raise HTTPException(status_code=404, detail="Case not found")
    template = resolve_template(payload.template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    missing_labels: List[str] = []
    for field in template.get("fields", []):
        if field.get("required") and not str(payload.values.get(field["key"], "")).strip():
            missing_labels.append(field.get("label") or field["key"])
    if missing_labels:
        raise HTTPException(status_code=422, detail=f"Required fields missing: {', '.join(missing_labels)}")
    values = {key: str(value).strip() for key, value in payload.values.items()}
    cleaned_tables = {k: [[str(c) for c in row] for row in rows if any(str(c).strip() for c in row)] for k, rows in payload.tables.items() if rows}
    record = {
        "id": str(uuid.uuid4()),
        "case_id": payload.case_id,
        "template_id": template["id"],
        "template_name": template["name"],
        "company_name": values.get("cd_name") or "Untitled matter",
        "status": "Ready",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "values": values,
        "tables": cleaned_tables,
        "notes": payload.notes,
    }
    _cache_put(record)
    _persist_generated_document(record, current["id"])
    return record


def _render(doc_id: str, file_format: str) -> StreamingResponse:
    item = _cache_get(doc_id)
    if not item or file_format not in {"docx", "pdf"}:
        raise HTTPException(status_code=404, detail="Document not ready. Please generate again.")
    template = resolve_template(item["template_id"])
    if not template:
        raise HTTPException(status_code=404, detail="Template no longer available")
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", item["company_name"]).strip("-")[:50] or "casefile-document"
    if file_format == "docx":
        stream = io.BytesIO(build_docx(template, item["values"], item.get("tables", {}), item.get("notes", "")))
        return StreamingResponse(stream, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers={"Content-Disposition": f'attachment; filename="{safe_name}-{template["id"]}.docx"'})
    stream = io.BytesIO()
    pdf = canvas.Canvas(stream, pagesize=A4)
    y = 800
    pdf.setTitle(template["name"])
    pdf.setFont("Helvetica-Bold", 14)
    for line in doc_text(template, item["values"], item.get("tables", {}), item.get("notes", "")):
        words = line.split()
        current_line = ""
        for word in words:
            candidate = f"{current_line} {word}".strip()
            if stringWidth(candidate, "Helvetica", 9) > 490:
                if y < 55:
                    pdf.showPage()
                    y = 800
                pdf.setFont("Helvetica", 9)
                pdf.drawString(55, y, current_line)
                y -= 14
                current_line = word
            else:
                current_line = candidate
        if y < 55:
            pdf.showPage()
            y = 800
        pdf.setFont("Helvetica-Bold" if y == 800 else "Helvetica", 9)
        pdf.drawString(55, y, current_line)
        y -= 15
    pdf.save()
    stream.seek(0)
    return StreamingResponse(stream, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{safe_name}-{template["id"]}.pdf"'})


@api_router.get("/documents/{document_id}/download/{file_format}")
async def download_document(document_id: str, file_format: str, current=Depends(get_current_user)):
    item = _cache_get(document_id)
    if not item:
        raise HTTPException(status_code=404, detail="Document not ready. Please generate again.")
    require_case_access(str(item.get("case_id") or ""), current)
    return _render(document_id, file_format)


app.include_router(api_router)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
logger.info("Casefile API ready. Admin: %s", ADMIN_EMAIL)
