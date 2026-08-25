from dotenv import load_dotenv
from pathlib import Path
import os

SOURCE_ROOT = Path(__file__).parent
ROOT_DIR = Path(os.environ.get("CASEFILE_APP_DIR", SOURCE_ROOT))
load_dotenv(ROOT_DIR / ".env")

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
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


class CocDocumentInput(BaseModel):
    document_type: str = Field(pattern="^(notice|minutes)$")
    status: str = Field(default="review", pattern="^(draft|review|final)$")


class PublicAnnouncementDraftInput(BaseModel):
    values: Dict[str, Any] = Field(default_factory=dict)


class PublicAnnouncementReviewInput(BaseModel):
    values: Dict[str, Any] = Field(default_factory=dict)


class PublicAnnouncementConfirmInput(BaseModel):
    accept_conflicts: bool = False

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
        return admission_intakes.save(intake_id, payload.get("review", payload), current["id"])
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
        if action == "create" and not casefile_store.has_full_case_access(current["role"]):
            casefile_store.set_case_assignments(result["case"]["id"], [current["id"]], current["id"])
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
        return record
    except Exception:
        output.unlink(missing_ok=True)
        raise


@api_router.post("/cases/{case_id}/public-announcement/ready")
async def finalise_public_announcement(case_id: str, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    try:
        return casefile_store.set_public_announcement_stage(case_id, "READY_FOR_PUBLICATION", current["id"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@api_router.post("/cases/{case_id}/public-announcement/sent")
async def mark_public_announcement_sent(case_id: str, current=Depends(get_current_user)):
    require_case_access(case_id, current)
    try:
        return casefile_store.set_public_announcement_stage(case_id, "SENT_FOR_PUBLICATION", current["id"])
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
        return casefile_store.store_published_announcement(
            case_id, original_name, str(target.relative_to(DATA_DIR)), file.content_type or "application/pdf",
            extraction, review, conflicts, current["id"],
        )
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
        return casefile_store.confirm_published_announcement(case_id, current["id"], payload.accept_conflicts)
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
    try:
        return casefile_store.list_module_records(case_id, module)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc


@api_router.post("/cases/{case_id}/{module}")
async def create_case_module(case_id: str, module: str, payload: Dict[str, Any], current=Depends(get_current_user)):
    require_case_access(case_id, current)
    _valid_module(module)
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
    candidate = (DATA_DIR / record["storage_path"]).resolve()
    files_root = CASE_FILES_DIR.resolve()
    if files_root not in candidate.parents or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Document file not found")
    return FileResponse(candidate, media_type=record.get("mime_type") or None, filename=record["name"])

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
