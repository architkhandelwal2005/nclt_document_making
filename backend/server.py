from fastapi import FastAPI, APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
import uuid
import io
from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from datetime import datetime, timezone


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")


# Define Models
class StatusCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")  # Ignore MongoDB's _id field
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class StatusCheckCreate(BaseModel):
    client_name: str

class TemplateRecord(BaseModel):
    id: str
    name: str
    category: str
    description: str
    fields: List[dict]
    last_used: Optional[str] = None

class DocumentInput(BaseModel):
    template_id: str
    values: dict
    notes: str = ""

class GeneratedDocument(BaseModel):
    id: str
    template_id: str
    template_name: str
    company_name: str
    status: str
    created_at: str
    values: dict
    notes: str = ""

TEMPLATES = [
    {"id": "nclt-company-particulars", "name": "NCLT Company Particulars", "category": "NCLT filing", "description": "Core company and case particulars for an NCLT submission.", "fields": [
        {"key": "company_name", "label": "Company name", "section": "Company details", "required": True, "placeholder": "e.g. Acme Industries Private Limited"},
        {"key": "cin", "label": "CIN / registration number", "section": "Company details", "required": True, "placeholder": "e.g. U12345MH2010PTC000000"},
        {"key": "registered_address", "label": "Registered office address", "section": "Company details", "required": True, "placeholder": "Complete registered office address"},
        {"key": "nclt_bench", "label": "NCLT bench", "section": "Case details", "required": True, "placeholder": "e.g. Mumbai Bench"},
        {"key": "case_number", "label": "Case number", "section": "Case details", "required": False, "placeholder": "e.g. CP (IB) No. 123/2025"},
        {"key": "applicant_name", "label": "Applicant name", "section": "Case details", "required": True, "placeholder": "Name of applicant"},
        {"key": "professional_name", "label": "Professional / authorised signatory", "section": "Signing", "required": True, "placeholder": "Name and designation"},
        {"key": "date", "label": "Document date", "section": "Signing", "required": True, "placeholder": "DD/MM/YYYY"},
    ]},
    {"id": "insolvency-notice", "name": "Insolvency Notice", "category": "Insolvency", "description": "A formal notice with debtor, claimant, and response details.", "fields": [
        {"key": "company_name", "label": "Corporate debtor", "section": "Parties", "required": True, "placeholder": "Company name"},
        {"key": "registered_address", "label": "Corporate debtor address", "section": "Parties", "required": True, "placeholder": "Complete address"},
        {"key": "applicant_name", "label": "Operational / financial creditor", "section": "Parties", "required": True, "placeholder": "Creditor name"},
        {"key": "claim_amount", "label": "Claim amount", "section": "Claim", "required": True, "placeholder": "e.g. ₹ 25,00,000"},
        {"key": "date", "label": "Notice date", "section": "Notice", "required": True, "placeholder": "DD/MM/YYYY"},
        {"key": "professional_name", "label": "Issued by", "section": "Notice", "required": True, "placeholder": "Name and designation"},
    ]},
]

# Add your routes to the router instead of directly to app
@api_router.get("/")
async def root():
    return {"message": "NCLT document workspace ready"}

@api_router.get("/templates", response_model=List[TemplateRecord])
async def get_templates():
    return TEMPLATES

@api_router.get("/documents", response_model=List[GeneratedDocument])
async def get_documents():
    docs = await db.generated_documents.find({}, {"_id": 0}).sort("created_at", -1).to_list(50)
    return docs

@api_router.post("/documents", response_model=GeneratedDocument)
async def generate_document(input: DocumentInput):
    template = next((t for t in TEMPLATES if t["id"] == input.template_id), None)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    missing = [f["label"] for f in template["fields"] if f["required"] and not str(input.values.get(f["key"], "")).strip()]
    if missing:
        raise HTTPException(status_code=422, detail=f"Required fields missing: {', '.join(missing)}")
    record = GeneratedDocument(id=str(uuid.uuid4()), template_id=template["id"], template_name=template["name"], company_name=input.values.get("company_name", "Untitled matter"), status="Ready", created_at=datetime.now(timezone.utc).isoformat(), values=input.values, notes=input.notes)
    await db.generated_documents.insert_one(record.model_dump())
    return record

@api_router.get("/documents/{document_id}/download/{file_format}")
async def download_document(document_id: str, file_format: str):
    item = await db.generated_documents.find_one({"id": document_id}, {"_id": 0})
    if not item or file_format not in {"docx", "pdf"}:
        raise HTTPException(status_code=404, detail="Document or format not found")
    title = item["template_name"]
    lines = [title.upper(), "", f"Company / party: {item['company_name']}"] + [f"{k.replace('_', ' ').title()}: {v}" for k, v in item["values"].items() if v]
    if item.get("notes"):
        lines += ["", "Additional notes:", item["notes"]]
    safe_name = item["company_name"].replace(" ", "-")[:40] or "document"
    if file_format == "docx":
        doc = Document(); doc.add_heading(title, 0)
        for line in lines[2:]: doc.add_paragraph(line)
        stream = io.BytesIO(); doc.save(stream); stream.seek(0)
        return StreamingResponse(stream, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers={"Content-Disposition": f'attachment; filename="{safe_name}.docx"'})
    stream = io.BytesIO(); pdf = canvas.Canvas(stream, pagesize=A4); y = 800
    pdf.setFont("Helvetica-Bold", 16); pdf.drawString(55, y, title); y -= 36; pdf.setFont("Helvetica", 10)
    for line in lines[2:]:
        for chunk in [line[i:i+100] for i in range(0, len(line), 100)]:
            if y < 55: pdf.showPage(); y = 800; pdf.setFont("Helvetica", 10)
            pdf.drawString(55, y, chunk); y -= 18
    pdf.save(); stream.seek(0)
    return StreamingResponse(stream, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{safe_name}.pdf"'})

@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_dict = input.model_dump()
    status_obj = StatusCheck(**status_dict)
    
    # Convert to dict and serialize datetime to ISO string for MongoDB
    doc = status_obj.model_dump()
    doc['timestamp'] = doc['timestamp'].isoformat()
    
    _ = await db.status_checks.insert_one(doc)
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    # Exclude MongoDB's _id field from the query results
    status_checks = await db.status_checks.find({}, {"_id": 0}).to_list(1000)
    
    # Convert ISO string timestamps back to datetime objects
    for check in status_checks:
        if isinstance(check['timestamp'], str):
            check['timestamp'] = datetime.fromisoformat(check['timestamp'])
    
    return status_checks

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()