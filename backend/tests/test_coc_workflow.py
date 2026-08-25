from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coc_workflow import CocDocumentService  # noqa: E402
from database import CasefileDatabase  # noqa: E402


def build_database(tmp_path):
    store = CasefileDatabase(tmp_path / "casefile.db")
    store.ensure_admin("admin", "admin@example.test", "Admin", "unused")
    case = store.create_case({"name": "Acme Foods Limited", "order_date": "2026-01-01"}, "admin")
    meeting = store.create_module_record(case["id"], "coc-meetings", {"meeting_number": 1, "meeting_at": "2026-02-01T10:00:00", "quorum_threshold": 33}, "admin")
    agenda = store.create_module_record(case["id"], "coc-agenda-items", {"meeting_id": meeting["id"], "position": 1, "section": "voting", "title": "Approve process bank account", "discussion": "The members discussed the account.", "proposed_resolution": "RESOLVED THAT the account be opened.", "voting_required": True}, "admin")
    store.create_module_record(case["id"], "coc-attendance", {"meeting_id": meeting["id"], "participant_name": "Test Member", "present": True, "voting_share_snapshot": 62.5}, "admin")
    return store, case, meeting, agenda


def test_notice_snapshot_quorum_and_document_versions(tmp_path):
    store, case, meeting, agenda = build_database(tmp_path)
    workflow = store.get_coc_workflow(case["id"], meeting["id"])
    assert workflow["quorum"] == {"present_voting_share": 62.5, "threshold": 33.0, "met": True}
    issued = store.issue_coc_notice(case["id"], meeting["id"], "admin")
    assert issued["meeting"]["notice_snapshot"]["agenda"][0]["title"] == "Approve process bank account"
    store.update_module_record(case["id"], "coc-agenda-items", agenda["id"], {"title": "Changed after issue"}, "admin")
    after = store.get_coc_workflow(case["id"], meeting["id"])
    assert after["meeting"]["notice_snapshot"]["agenda"][0]["title"] == "Approve process bank account"
    first = store.store_coc_document(case["id"], meeting["id"], "CoC Notice", "Notice.docx", "files/one.docx", "review", {}, "admin")
    second = store.store_coc_document(case["id"], meeting["id"], "CoC Notice", "Notice.docx", "files/two.docx", "final", {}, "admin")
    assert (first["version"], second["version"], second["parent_document_id"]) == (1, 2, first["id"])


def test_retained_templates_generate_without_cross_company_contamination(tmp_path):
    template_dir = Path(__file__).resolve().parents[1] / "templates" / "coc"
    service = CocDocumentService(template_dir)
    workflow = {
        "meeting": {"meeting_number": 2, "meeting_at": "2026-02-01T10:00:00", "actual_start_at": "2026-02-01T10:05:00", "actual_end_at": "2026-02-01T11:00:00", "mode": "Video conference", "venue_or_link": "Secure link", "notice_date": "2026-01-25", "notice_place": "Indore", "voting_start": "2026-02-01T12:00:00", "voting_end": "2026-02-02T12:00:00", "quorum_threshold": 33, "chair_name": "Test RP"},
        "agenda": [{"id": "a1", "position": 1, "section": "voting", "title": "Test resolution", "discussion": "The members discussed only the entered facts.", "decision": "Placed for vote.", "proposed_resolution": "RESOLVED THAT the test action be approved.", "resolution_text": "RESOLVED THAT the test action was approved.", "voting_required": True}],
        "attendance": [{"participant_name": "Test Member", "organization": "Test Bank", "present": True, "attendance_mode": "Video conference", "voting_share_snapshot": 75}],
        "members": [{"organization": "Test Bank", "email": "member@example.test", "admitted_debt": 100, "voting_share": 75, "creditor_category": "Financial creditor"}],
        "votes": [{"agenda_key": "a1", "vote": "yes", "voting_share": 75}],
        "quorum": {"present_voting_share": 75, "threshold": 33, "met": True},
    }
    case = {"name": "Acme Foods Limited", "order_date": "2026-01-01"}
    output = service.generate("notice", case, workflow, [], {"ip_name": "Test RP"}, tmp_path / "notice.docx")
    minutes = service.generate("minutes", case, workflow, [], {"ip_name": "Test RP"}, tmp_path / "minutes.docx")
    import zipfile
    combined = ""
    for path in (output, minutes):
        with zipfile.ZipFile(path) as archive:
            combined += archive.read("word/document.xml").decode("utf-8")
    assert "Acme Foods Limited" in combined
    assert "Mahakali Foods" not in combined
    assert "Premier Proteins" not in combined
    assert "Navin Khandelwal" not in combined


def test_minutes_reject_missing_manual_discussion(tmp_path):
    service = CocDocumentService(Path(__file__).resolve().parents[1] / "templates" / "coc")
    workflow = {"meeting": {"meeting_number": 1, "meeting_at": "2026-02-01T10:00:00"}, "agenda": [{"position": 1, "title": "Item", "discussion": ""}], "attendance": [], "members": [], "votes": [], "quorum": {}}
    with pytest.raises(ValueError, match="Manual discussion"):
        service.generate("minutes", {"name": "Acme Foods Limited"}, workflow, [], {}, tmp_path / "minutes.docx")
