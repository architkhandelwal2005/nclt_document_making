"""Mandatory offline Phase 3 demonstration: CoC Constitution -> Meeting -> ATR.

This script uses no AI, network or external voting service.  It records only
the office-provided Minutes text supplied below and writes retained-template
Notice/Minutes DOCX files to the supplied output directory.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from claims_coc_core import ClaimsCocCore
from claims_workflow import ClaimsWorkflow
from coc_meeting_core import CocMeetingCore
from coc_workflow import CocDocumentService
from database import CasefileDatabase
from workflow import EventEngine


ACTOR = "admin"


def _add_claim(store: CasefileDatabase, case_id: str, key: str, creditor: str, claimed: int, admitted: int):
    claims = ClaimsWorkflow(store, store.path.parent)
    claim = claims.create(case_id, {
        "received_date": "2026-08-10", "received_via": "Email", "creditor_name": creditor,
        "creditor_category": "FINANCIAL_CREDITOR", "form_type": "Form C", "claimed_amount": claimed,
        "principal_claimed": claimed, "email": f"{key}@example.test", "idempotency_key": key,
    }, ACTOR)
    claims.confirm_classification(case_id, claim["id"], {}, ACTOR)
    claims.update_scrutiny(case_id, claim["id"], {"scrutiny_status": "COMPLETE"}, ACTOR)
    claims.start_verification(case_id, claim["id"], ACTOR)
    claims.confirm_related_party(case_id, claim["id"], {"related_party_status": "NO", "reason": "Demo professional review"}, ACTOR)
    claims.decide(case_id, claim["id"], {
        "decision_status": "ADMITTED", "principal_admitted": admitted, "interest_admitted": 0,
        "other_amount_admitted": 0, "decision_date": "2026-08-20",
    }, ACTOR)
    return claim


def run(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    database_path = output_dir / "phase3-demo.db"
    database_path.unlink(missing_ok=True)
    store = CasefileDatabase(database_path)
    store.ensure_admin(ACTOR, "admin@example.test", "Demo Administrator", "unused")
    case = store.create_case({"name": "Demo CIRP", "process_type": "CIRP", "commencement_date": "2026-08-01"}, ACTOR)
    events = EventEngine(store)
    events.record_event(case["id"], "ADMISSION_ORDER_CONFIRMED", "2026-08-01", ACTOR, source_type="demo", source_id="admission", metadata={"cirp_commencement_date": "2026-08-01"}, idempotency_key="demo-admission")
    events.record_event(case["id"], "PUBLIC_ANNOUNCEMENT_CONFIRMED", "2026-08-04", ACTOR, source_type="demo", source_id="announcement", idempotency_key="demo-announcement")
    fc_a = _add_claim(store, case["id"], "fc-a", "FC-A", 6_000_000, 6_000_000)
    fc_b = _add_claim(store, case["id"], "fc-b", "FC-B", 4_000_000, 4_000_000)
    coc = ClaimsCocCore(store)
    loc = coc.create_loc_snapshot(case["id"], ACTOR, "2026-08-21")
    coc.confirm_eligibility(case["id"], fc_a["id"], {"eligibility_status": "ELIGIBLE", "related_party_status": "NO"}, ACTOR)
    coc.confirm_eligibility(case["id"], fc_b["id"], {"eligibility_status": "ELIGIBLE", "related_party_status": "NO"}, ACTOR)
    voting = coc.calculate_voting(case["id"], ACTOR)
    constitution = coc.confirm_constitution(case["id"], {"constitution_date": "2026-08-22", "voting_calculation_id": voting["id"]}, ACTOR)

    core = CocMeetingCore(store)
    meeting = core.create_meeting(case["id"], {
        "meeting_type": "FIRST_COC", "coc_constitution_id": constitution["id"], "scheduled_start_at": "2026-08-30T10:00:00",
        "scheduled_end_at": "2026-08-30T12:00:00", "mode": "VIDEO_CONFERENCE", "venue": "Google Meet (demo)", "chair_name": "Demo RP",
    }, ACTOR)
    agenda = core.create_agenda_version(case["id"], meeting["id"], ACTOR)
    agenda_items = [
        {"agenda_number": "1", "agenda_type": "PROCEDURAL", "title": "IRP to take Chair"},
        {"agenda_number": "2", "agenda_type": "FOR_NOTING", "title": "Roll Call and CoC Constitution"},
        {"agenda_number": "3", "agenda_type": "FOR_VOTING", "title": "Approve process bank account", "proposed_resolution_text": "RESOLVED THAT the process bank account be opened.", "requires_resolution": True, "requires_voting": True},
    ]
    for item in agenda_items:
        agenda = core.add_agenda_item(case["id"], meeting["id"], agenda["id"], item, ACTOR)
    agenda = core.finalize_agenda(case["id"], meeting["id"], agenda["id"], ACTOR)
    notice = core.create_notice_draft(case["id"], meeting["id"], {"agenda_version_id": agenda["id"]}, ACTOR)
    context = core.document_context(case["id"], meeting["id"], "notice", notice["id"])
    notice_path = CocDocumentService(Path(__file__).resolve().parent / "templates" / "coc").generate("notice", context["case"], context["workflow"], [], {}, output_dir / "First_CoC_Notice_Draft.docx")
    notice_doc = store.store_coc_document(case["id"], meeting["id"], "CoC Notice", notice_path.name, str(notice_path), "draft", {"retained_template": True}, ACTOR)
    core.link_notice_document(case["id"], meeting["id"], notice["id"], notice_doc["id"], ACTOR)
    core.approve_notice(case["id"], meeting["id"], notice["id"], ACTOR)
    notice = core.issue_notice(case["id"], meeting["id"], notice["id"], ACTOR)
    recipients = core.member_snapshot(case["id"], meeting["id"])
    for recipient in recipients:
        core.record_notice_dispatch(case["id"], meeting["id"], notice["id"], {
            "meeting_member_snapshot_id": recipient["id"], "recipient_name": recipient["creditor_name"],
            "recipient_address": recipient["recipient_email"], "dispatch_method": "Manual email record", "dispatch_datetime": "2026-08-25T12:00:00", "status": "SENT_MANUALLY",
        }, ACTOR)

    quorum_rule = core.upsert_quorum_rule({"rule_code": "DEMO-QUORUM", "effective_from": "2026-01-01", "minimum_voting_share": "51.0000", "rule_reference": "Explicit demo fixture", "status": "CONFIRMED"}, ACTOR)
    approval_rule = core.upsert_approval_rule({"rule_code": "DEMO-APPROVAL", "effective_from": "2026-01-01", "minimum_for_voting_share": "51.0000", "rule_reference": "Explicit demo fixture", "status": "CONFIRMED"}, ACTOR)
    for recipient in recipients:
        core.record_attendance(case["id"], meeting["id"], {"meeting_member_snapshot_id": recipient["id"], "participant_name": recipient["creditor_name"], "participant_role": "COC_MEMBER_REPRESENTATIVE", "authorization_status": "VALID", "present": True, "attendance_mode": "VIDEO_CONFERENCE"}, ACTOR)
    quorum = core.calculate_quorum(case["id"], meeting["id"], {"quorum_rule_id": quorum_rule["id"]}, ACTOR)
    core.start_meeting(case["id"], meeting["id"], ACTOR, "2026-08-30T10:02:00")
    office_minutes = [
        "The Interim Resolution Professional took the Chair.",
        "Roll call of the participants was conducted.",
        "The requisite quorum being present, the process bank account resolution was placed before the CoC.",
    ]
    for item, text in zip(agenda["items"], office_minutes):
        core.save_minutes_entry(case["id"], meeting["id"], item["id"], {"minutes_text": text}, ACTOR)
    resolution = core.create_resolution(case["id"], meeting["id"], {
        "agenda_item_id": agenda["items"][2]["id"], "title": "Process bank account", "voting_required": True,
        "proposed_resolution_text": "RESOLVED THAT the process bank account be opened.",
        "final_resolution_text": "RESOLVED THAT the CoC approves opening the process bank account.", "approval_rule_id": approval_rule["id"],
    }, ACTOR)
    core.place_resolution(case["id"], meeting["id"], resolution["id"], ACTOR)
    draft = core.create_minutes_version(case["id"], meeting["id"], ACTOR)
    minutes_context = core.document_context(case["id"], meeting["id"], "minutes", draft["id"])
    minutes_path = CocDocumentService(Path(__file__).resolve().parent / "templates" / "coc").generate("minutes", minutes_context["case"], minutes_context["workflow"], [], {}, output_dir / "First_CoC_Minutes_Draft.docx")
    minutes_doc = store.store_coc_document(case["id"], meeting["id"], "CoC Minutes", minutes_path.name, str(minutes_path), "draft", {"retained_template": True, "office_provided_minutes_text_only": True}, ACTOR)
    core.link_minutes_document(case["id"], meeting["id"], draft["id"], minutes_doc["id"], ACTOR)
    final_minutes = core.finalize_minutes(case["id"], meeting["id"], draft["id"], ACTOR)
    session = core.create_voting_session(case["id"], meeting["id"], {"resolution_ids": [resolution["id"]]}, ACTOR)
    core.open_voting(case["id"], meeting["id"], session["id"], ACTOR)
    core.record_vote(case["id"], meeting["id"], session["id"], resolution["id"], {"meeting_member_snapshot_id": recipients[0]["id"], "vote": "FOR", "method": "E_VOTING"}, ACTOR)
    core.record_vote(case["id"], meeting["id"], session["id"], resolution["id"], {"meeting_member_snapshot_id": recipients[1]["id"], "vote": "AGAINST", "method": "E_VOTING"}, ACTOR)
    core.close_voting(case["id"], meeting["id"], session["id"], ACTOR)
    result = core.calculate_voting_result(case["id"], meeting["id"], session["id"], resolution["id"], ACTOR)
    core.finalize_voting_result(case["id"], meeting["id"], result["id"], ACTOR)
    core.create_action_item(case["id"], meeting["id"], {"agenda_item_id": agenda["items"][2]["id"], "resolution_id": resolution["id"], "action_text": "Open process bank account", "owner": "RP", "due_date": "2026-09-01"}, ACTOR)
    core.create_action_item(case["id"], meeting["id"], {"action_text": "Circulate final Minutes", "owner": "Associate", "due_date": "2026-09-02"}, ACTOR)
    core.complete_meeting(case["id"], meeting["id"], ACTOR, "2026-08-30T11:55:00")

    print("Meeting:", "1st CoC Meeting")
    print("Status:", core.get_meeting(case["id"], meeting["id"])["status"])
    print("CoC Constitution:", f"Version {constitution['constitution_version']}")
    print("Notice:", notice["status"])
    print("Agenda:", agenda["status"])
    print("Attendance:", f"{len(recipients)}/{len(recipients)} voting members")
    print("Voting Share Present:", f"{quorum['present_voting_share']}%")
    print("Quorum:", quorum["status"])
    print("Minutes Input:", "COMPLETE")
    print("Minutes:", f"FINAL V{final_minutes['version_number']}")
    print("Resolutions:", 1)
    print("Voting:", core.get_voting_session(case["id"], meeting["id"], session["id"])["status"])
    print("Voting Result:", "FINAL")
    print("ATR Actions:", len(core.list_atr(case["id"], meeting["id"])))
    print("Notice DOCX:", notice_path)
    print("Minutes DOCX:", minutes_path)
    print("LOC version:", loc["version_number"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("phase3-demo-output"))
    args = parser.parse_args()
    run(args.output_dir)
