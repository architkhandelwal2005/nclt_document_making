"""Generate deterministic retained-template samples for visual/fidelity QA."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from coc_workflow import CocDocumentService  # noqa: E402


def workflow(number: int, issued: bool = True):
    agenda = [
        {"id": "agenda-1", "position": 1, "section": "discussion", "title": "The Resolution Professional to take the chair", "discussion": "The members considered the chairing arrangements and confirmed the Resolution Professional as Chair.", "decision": "Noted and approved.", "proposed_resolution": "", "resolution_text": "", "voting_required": False},
        {"id": "agenda-2", "position": 2, "section": "discussion", "title": "Roll call and confirmation of participants", "discussion": "A roll call was conducted. Each participant stated their name, capacity, location and confirmed receipt of the meeting papers.", "decision": "The attendance was taken on record.", "proposed_resolution": "", "resolution_text": "", "voting_required": False},
        {"id": "agenda-3", "position": 3, "section": "voting", "title": "Approval of process-specific bank account", "discussion": "The funding and operational requirements of the CIRP account were discussed using the figures entered in the case workspace.", "decision": "Placed for voting.", "proposed_resolution": "RESOLVED THAT a process-specific bank account be opened for CIRP transactions.", "resolution_text": "RESOLVED THAT the Resolution Professional is authorised to open and operate a process-specific bank account for CIRP transactions.", "voting_required": True},
    ]
    meeting = {"id": f"meeting-{number}", "meeting_number": number, "meeting_at": "2026-09-15T15:30:00", "actual_start_at": "2026-09-15T15:35:00", "actual_end_at": "2026-09-15T17:05:00", "mode": "Hybrid", "venue_or_link": "Board Room, Indore and secure video conference", "notice_date": "2026-09-09", "notice_place": "Indore (M.P.)", "voting_start": "2026-09-15T18:00:00", "voting_end": "2026-09-16T18:00:00", "quorum_threshold": 33, "chair_name": "Arjun Mehta", "notice_snapshot": {"issued_at": "2026-09-09T10:00:00Z", "agenda": agenda} if issued else {}}
    return {"meeting": meeting, "agenda": agenda, "attendance": [{"participant_name": "Nisha Rao", "organization": "National Commercial Bank", "capacity": "Authorised representative", "present": True, "attendance_mode": "Video conference", "voting_share_snapshot": 62.5}, {"participant_name": "Kabir Shah", "organization": "Trade Creditors Association", "capacity": "Authorised representative", "present": True, "attendance_mode": "Physical", "voting_share_snapshot": 24.25}], "members": [{"contact_name": "National Commercial Bank", "organization": "National Commercial Bank", "email": "coc.one@example.test", "admitted_debt": 12500000, "voting_share": 62.5}, {"contact_name": "Trade Creditors Association", "organization": "Trade Creditors Association", "email": "coc.two@example.test", "admitted_debt": 4850000, "voting_share": 24.25}], "votes": [{"agenda_key": "agenda-3", "vote": "yes", "voting_share": 62.5}, {"agenda_key": "agenda-3", "vote": "yes", "voting_share": 24.25}], "quorum": {"present_voting_share": 86.75, "threshold": 33, "met": True}}


def main():
    output = Path(__file__).resolve().parent / "generated"
    service = CocDocumentService(ROOT / "backend" / "templates" / "coc")
    profile = {"ip_name": "Arjun Mehta", "process_email": "cirp.qa@example.test", "ibbi_reg_no": "IBBI/IPA-1/P00000/2026-2027/10000"}
    contacts = [{"name": "Rohan Verma", "email": "director.qa@example.test", "role": "Suspended Director"}]
    cases = [("mahakali-qa", {"name": "Mahakali Foods Private Limited", "order_date": "2026-07-31", "registered_email": "mahakali.qa@example.test"}, 1), ("premier-qa", {"name": "Premier Proteins Limited", "order_date": "2025-08-28", "registered_email": "premier.qa@example.test"}, 2)]
    for slug, case, number in cases:
        data = workflow(number)
        for document_type in ("notice", "minutes"):
            service.generate(document_type, case, data, contacts, profile, output / f"{slug}-{document_type}.docx")


if __name__ == "__main__":
    main()
