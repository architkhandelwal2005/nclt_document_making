from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import io
import logging
import os
import re
import shutil
import uuid

import bcrypt
import jwt
from docx import Document
from docx.shared import Inches
from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from starlette.middleware.cors import CORSMiddleware

client = AsyncIOMotorClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]
app = FastAPI(title="Casefile Document API", version="1.2.0")
api_router = APIRouter(prefix="/api")
TEMPLATE_DIR = ROOT_DIR / "templates"
CUSTOM_TEMPLATE_DIR = TEMPLATE_DIR / "custom"
CUSTOM_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

JWT_ALGORITHM = "HS256"
JWT_SECRET = os.environ["JWT_SECRET"]
ACCESS_TOKEN_MINUTES = 60 * 12
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

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


# ---------------- Auth models ----------------
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


# ---------------- Custom template & draft models ----------------
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


class DraftInput(BaseModel):
    id: Optional[str] = None
    name: str
    template_id: str
    values: Dict[str, str] = Field(default_factory=dict)
    tables: Dict[str, List[List[str]]] = Field(default_factory=dict)
    notes: str = ""


class DraftRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    template_id: str
    template_name: str
    values: Dict[str, str]
    tables: Dict[str, List[List[str]]] = Field(default_factory=dict)
    notes: str = ""
    updated_at: str


# ---------------- Document models ----------------
class TemplateRecord(BaseModel):
    id: str
    name: str
    category: str
    description: str
    fields: List[dict]
    table_inputs: List[dict]
    source: str = "builtin"


class DocumentInput(BaseModel):
    template_id: str
    values: Dict[str, str] = Field(default_factory=dict)
    tables: Dict[str, List[List[str]]] = Field(default_factory=dict)
    notes: str = ""


class GeneratedDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    template_id: str
    template_name: str
    company_name: str
    status: str
    created_at: str
    values: Dict[str, str]
    tables: Dict[str, List[List[str]]] = Field(default_factory=dict)
    notes: str = ""


# ---------------- Auth helpers ----------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_MINUTES),
        "type": "access",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


async def get_current_user(request: Request) -> dict:
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
    user = await db.users.find_one({"id": payload.get("sub")})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    user.pop("_id", None)
    user.pop("password_hash", None)
    return user


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


def _expand_custom_template(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
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


def _builtin_public(template: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in template.items() if key not in {"filename", "field_keys"}} | {"source": "builtin"}


async def list_all_templates() -> List[Dict[str, Any]]:
    custom = await db.custom_templates.find({}, {"_id": 0}).sort("created_at", -1).to_list(200)
    return [_builtin_public(t) for t in TEMPLATES] + [
        {key: value for key, value in _expand_custom_template(c).items() if key not in {"filename", "field_keys"}}
        for c in custom
    ]


async def resolve_template(template_id: str) -> Optional[Dict[str, Any]]:
    for template in TEMPLATES:
        if template["id"] == template_id:
            return {**template, "source": "builtin", "template_dir": TEMPLATE_DIR}
    record = await db.custom_templates.find_one({"id": template_id}, {"_id": 0})
    if not record:
        return None
    expanded = _expand_custom_template(record)
    expanded["template_dir"] = CUSTOM_TEMPLATE_DIR
    return expanded


def canonical_placeholder(raw):
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


# ---------------- Auth routes ----------------
@api_router.post("/auth/login", response_model=TokenOut)
async def login(payload: LoginInput, request: Request):
    email = payload.email.lower().strip()
    ip = request.client.host if request.client else "unknown"
    identifier = f"{ip}:{email}"
    attempt = await db.login_attempts.find_one({"identifier": identifier})
    if attempt and attempt.get("locked_until") and attempt["locked_until"] > datetime.now(timezone.utc):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again shortly.")
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user.get("password_hash", "")):
        count = (attempt.get("count", 0) if attempt else 0) + 1
        locked = datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_MINUTES) if count >= MAX_FAILED_ATTEMPTS else None
        await db.login_attempts.update_one({"identifier": identifier}, {"$set": {"count": count, "locked_until": locked}}, upsert=True)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    await db.login_attempts.delete_one({"identifier": identifier})
    token = create_access_token(user["id"], user["email"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": user["id"], "email": user["email"], "name": user["name"], "role": user["role"]},
    }


@api_router.post("/auth/logout")
async def logout(current=Depends(get_current_user)):
    return {"ok": True}


@api_router.get("/auth/me", response_model=UserOut)
async def me(current=Depends(get_current_user)):
    return {"id": current["id"], "email": current["email"], "name": current["name"], "role": current["role"]}


# ---------------- Document routes (protected) ----------------
@api_router.get("/")
async def root():
    return {"message": "Casefile document API ready"}


@api_router.get("/templates", response_model=List[TemplateRecord])
async def get_templates(current=Depends(get_current_user)):
    return await list_all_templates()


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
    src.rename(CUSTOM_TEMPLATE_DIR / filename)
    record = {
        "id": template_id,
        "name": spec.name.strip() or "Custom template",
        "category": spec.category.strip() or "Custom",
        "description": spec.description.strip(),
        "fields": [f.model_dump() for f in spec.fields],
        "table_inputs": [t.model_dump() for t in spec.table_inputs],
        "filename": filename,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": current["id"],
    }
    await db.custom_templates.insert_one(record)
    expanded = _expand_custom_template(record)
    return {key: value for key, value in expanded.items() if key not in {"filename", "field_keys", "template_dir"}}


@api_router.delete("/templates/{template_id}")
async def delete_custom_template(template_id: str, current=Depends(get_current_user)):
    record = await db.custom_templates.find_one({"id": template_id}, {"_id": 0})
    if not record:
        raise HTTPException(status_code=404, detail="Custom template not found")
    (CUSTOM_TEMPLATE_DIR / record["filename"]).unlink(missing_ok=True)
    await db.custom_templates.delete_one({"id": template_id})
    return {"ok": True}


# ---------------- Draft routes ----------------
@api_router.get("/drafts", response_model=List[DraftRecord])
async def list_drafts(current=Depends(get_current_user)):
    return await db.case_drafts.find({"user_id": current["id"]}, {"_id": 0, "user_id": 0}).sort("updated_at", -1).to_list(100)


@api_router.get("/drafts/{draft_id}", response_model=DraftRecord)
async def get_draft(draft_id: str, current=Depends(get_current_user)):
    record = await db.case_drafts.find_one({"id": draft_id, "user_id": current["id"]}, {"_id": 0, "user_id": 0})
    if not record:
        raise HTTPException(status_code=404, detail="Draft not found")
    return record


@api_router.post("/drafts", response_model=DraftRecord)
async def save_draft(payload: DraftInput, current=Depends(get_current_user)):
    template = await resolve_template(payload.template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    draft_id = payload.id or str(uuid.uuid4())
    record = {
        "id": draft_id,
        "name": payload.name.strip() or f"Draft {datetime.now(timezone.utc).strftime('%d %b %Y')}",
        "template_id": template["id"],
        "template_name": template["name"],
        "values": {k: str(v) for k, v in payload.values.items()},
        "tables": payload.tables,
        "notes": payload.notes,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "user_id": current["id"],
    }
    await db.case_drafts.update_one({"id": draft_id, "user_id": current["id"]}, {"$set": record}, upsert=True)
    return {k: v for k, v in record.items() if k != "user_id"}


@api_router.delete("/drafts/{draft_id}")
async def delete_draft(draft_id: str, current=Depends(get_current_user)):
    result = await db.case_drafts.delete_one({"id": draft_id, "user_id": current["id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"ok": True}


@api_router.get("/documents", response_model=List[GeneratedDocument])
async def get_documents(current=Depends(get_current_user)):
    return await db.generated_documents.find({}, {"_id": 0}).sort("created_at", -1).to_list(50)


@api_router.post("/documents", response_model=GeneratedDocument)
async def generate_document(input: DocumentInput, current=Depends(get_current_user)):
    template = await resolve_template(input.template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    missing_labels: List[str] = []
    for field in template.get("fields", []):
        if field.get("required") and not str(input.values.get(field["key"], "")).strip():
            missing_labels.append(field.get("label") or field["key"])
    if missing_labels:
        raise HTTPException(status_code=422, detail=f"Required fields missing: {', '.join(missing_labels)}")
    values = {key: str(value).strip() for key, value in input.values.items()}
    cleaned_tables = {k: [[str(c) for c in row] for row in rows if any(str(c).strip() for c in row)] for k, rows in input.tables.items() if rows}
    record = GeneratedDocument(
        id=str(uuid.uuid4()),
        template_id=template["id"],
        template_name=template["name"],
        company_name=values.get("cd_name") or values.get("company_name") or "Untitled matter",
        status="Ready",
        created_at=datetime.now(timezone.utc).isoformat(),
        values=values,
        tables=cleaned_tables,
        notes=input.notes,
    )
    await db.generated_documents.insert_one(record.model_dump())
    return record


@api_router.get("/documents/{document_id}/download/{file_format}")
async def download_document(document_id: str, file_format: str, current=Depends(get_current_user)):
    item = await db.generated_documents.find_one({"id": document_id}, {"_id": 0})
    if not item or file_format not in {"docx", "pdf"}:
        raise HTTPException(status_code=404, detail="Document or format not found")
    template = await resolve_template(item.get("template_id"))
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


# ---------------- Download as authenticated GET can't send Authorization header from window.open
# Provide token-in-query fallback for browser-triggered downloads.
@api_router.get("/documents/{document_id}/export/{file_format}")
async def export_document(document_id: str, file_format: str, token: str):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = await db.users.find_one({"id": payload.get("sub")})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return await download_document(document_id, file_format, current=user)


app.include_router(api_router)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@app.on_event("startup")
async def startup():
    await db.users.create_index("email", unique=True)
    await db.login_attempts.create_index("identifier", unique=True)
    admin_email = os.environ["ADMIN_EMAIL"].lower().strip()
    admin_password = os.environ["ADMIN_PASSWORD"]
    existing = await db.users.find_one({"email": admin_email})
    if not existing:
        await db.users.insert_one({
            "id": str(uuid.uuid4()),
            "email": admin_email,
            "password_hash": hash_password(admin_password),
            "name": "Arun Kumar",
            "role": "admin",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info("Admin user seeded: %s", admin_email)
    elif not verify_password(admin_password, existing.get("password_hash", "")):
        await db.users.update_one({"email": admin_email}, {"$set": {"password_hash": hash_password(admin_password)}})
        logger.info("Admin password refreshed")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
