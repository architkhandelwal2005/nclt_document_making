from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from database import CasefileDatabase

store = CasefileDatabase(Path(__file__).with_name("browser-pa-data") / "casefile.db")
case = store.list_cases()[0]
values = {
    "corporate_debtor_name": case["name"], "cin": case["cin"],
    "cirp_commencement_date": "2026-08-01", "estimated_closure_date": "2027-01-28",
    "irp_name": "Test IRP", "irp_registration_number": "IBBI/TEST/001",
    "irp_registered_address": "Test address", "irp_registered_email": "irp@example.test",
    "correspondence_address": "Test address", "process_specific_email": "cirp@example.test",
    "claims_submission_last_date": "2026-08-15", "announcement_date": "2026-08-03",
    "announcement_place": "Indore", "creditor_classes": "No class ascertained.",
    "authorised_representatives": "Not applicable.", "forms_weblink": "https://ibbi.gov.in/en/home/downloads",
    "authorised_representative_details": "Not applicable.",
}
record = store.get_public_announcement(case["id"])
if not record:
    store.save_public_announcement_draft(case["id"], values, {}, "admin", {"name": "Form_A.docx", "storage_path": "files/test-form-a.docx"})
    store.set_public_announcement_stage(case["id"], "READY_FOR_PUBLICATION", "admin")
    store.set_public_announcement_stage(case["id"], "SENT_FOR_PUBLICATION", "admin")
published = {**values, "publication_date": "2026-08-05", "newspaper_name": "Test Daily", "publication_language": "English", "edition_or_place": "Indore", "claims_submission_last_date": "2026-08-16"}
conflicts = [{"field": "claims_submission_last_date", "existing_value": "2026-08-15", "published_value": "2026-08-16", "warning": "Published announcement differs from existing case information."}]
record = store.get_public_announcement(case["id"])
if not record.get("published_document_id"):
    store.store_published_announcement(case["id"], "published.pdf", "files/test-published.pdf", "application/pdf", {"method": "pdf_text", "warnings": [], "page_count": 1}, published, conflicts, "admin")
    store.review_published_announcement(case["id"], published, conflicts, "admin")
