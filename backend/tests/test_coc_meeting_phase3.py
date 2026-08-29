"""Focused CIRP-046--056 meeting lifecycle tests."""

from pathlib import Path
import zipfile

import pytest
from fastapi.testclient import TestClient

import server
from coc_meeting_core import CocMeetingCore
from coc_workflow import CocDocumentService
from workflow import EventEngine, WorkflowService
from test_claims_coc_phase2 import ACTOR, build_standard_coc


def _first_meeting(tmp_path):
    store, case, _, _, _, _, _, _, constitution = build_standard_coc(tmp_path)
    core = CocMeetingCore(store)
    meeting = core.create_meeting(case["id"], {
        "meeting_type": "FIRST_COC", "coc_constitution_id": constitution["id"],
        "scheduled_start_at": "2026-08-30T10:00:00", "scheduled_end_at": "2026-08-30T12:00:00",
        "mode": "VIDEO_CONFERENCE", "venue": "https://meeting.example.test/first",
    }, ACTOR)
    agenda = core.create_agenda_version(case["id"], meeting["id"], ACTOR)
    agenda = core.add_agenda_item(case["id"], meeting["id"], agenda["id"], {
        "agenda_number": "1", "agenda_type": "PROCEDURAL", "title": "IRP to take Chair",
    }, ACTOR)
    agenda = core.add_agenda_item(case["id"], meeting["id"], agenda["id"], {
        "agenda_number": "2", "agenda_type": "FOR_NOTING", "title": "Roll Call and CoC Constitution",
    }, ACTOR)
    agenda = core.add_agenda_item(case["id"], meeting["id"], agenda["id"], {
        "agenda_number": "3", "agenda_type": "FOR_VOTING", "title": "Approve process bank account",
        "proposed_resolution_text": "RESOLVED THAT the process bank account be opened.", "requires_resolution": True, "requires_voting": True,
    }, ACTOR)
    agenda = core.finalize_agenda(case["id"], meeting["id"], agenda["id"], ACTOR)
    notice = core.create_notice_draft(case["id"], meeting["id"], {"agenda_version_id": agenda["id"]}, ACTOR)
    notice_document = store.create_module_record(
        case["id"], "documents",
        {"name": "notice.docx", "category": "CoC Notice", "storage_path": "test/notice.docx"}, ACTOR,
    )
    core.link_notice_document(case["id"], meeting["id"], notice["id"], notice_document["id"], ACTOR)
    notice = core.approve_notice(case["id"], meeting["id"], notice["id"], ACTOR)
    notice = core.issue_notice(case["id"], meeting["id"], notice["id"], ACTOR)
    return store, case, core, meeting, agenda, notice


def _confirmed_rules(core):
    quorum = core.upsert_quorum_rule({
        "rule_code": "TEST-QUORUM-51", "effective_from": "2026-01-01", "minimum_voting_share": "51.0000",
        "rule_reference": "Explicit test fixture", "status": "CONFIRMED",
    }, ACTOR)
    approval = core.upsert_approval_rule({
        "rule_code": "TEST-APPROVAL-51", "effective_from": "2026-01-01", "minimum_for_voting_share": "51.0000",
        "rule_reference": "Explicit test fixture", "status": "CONFIRMED",
    }, ACTOR)
    return quorum, approval


def _record_all_members(core, case_id, meeting_id, snapshots):
    for snapshot in snapshots:
        core.record_attendance(case_id, meeting_id, {
            "meeting_member_snapshot_id": snapshot["id"], "participant_name": snapshot["creditor_name"],
            "participant_role": "COC_MEMBER_REPRESENTATIVE", "authorization_status": "VALID", "present": True,
            "attendance_mode": "VIDEO_CONFERENCE",
        }, ACTOR)


def test_standard_first_coc_notice_snapshot_quorum_minutes_voting_and_atr(tmp_path):
    store, case, core, meeting, agenda, notice = _first_meeting(tmp_path)
    snapshots = core.member_snapshot(case["id"], meeting["id"])
    assert [(row["creditor_name"], row["voting_share_text"]) for row in snapshots] == [("FC A", "60.0000"), ("FC B", "40.0000")]
    assert notice["status"] == "ISSUED"

    quorum_rule, approval_rule = _confirmed_rules(core)
    _record_all_members(core, case["id"], meeting["id"], snapshots)
    quorum = core.calculate_quorum(case["id"], meeting["id"], {"quorum_rule_id": quorum_rule["id"]}, ACTOR)
    assert quorum["present_voting_share"] == "100.0000" and quorum["status"] == "MET"
    core.start_meeting(case["id"], meeting["id"], ACTOR, "2026-08-30T10:02:00")

    items = agenda["items"]
    actual_text = [
        "The Interim Resolution Professional took the Chair.",
        "The participants introduced themselves and the roll call was completed.",
        "The requisite quorum being present, the resolution was placed before the CoC.",
    ]
    for item, text in zip(items, actual_text):
        saved = core.save_minutes_entry(case["id"], meeting["id"], item["id"], {"minutes_text": text}, ACTOR)
        assert saved["minutes_text"] == text
    resolution = core.create_resolution(case["id"], meeting["id"], {
        "agenda_item_id": items[2]["id"], "title": "Process bank account", "voting_required": True,
        "proposed_resolution_text": "RESOLVED THAT the process bank account be opened.",
        "final_resolution_text": "RESOLVED THAT the CoC approves opening the process bank account.",
        "approval_rule_id": approval_rule["id"],
    }, ACTOR)
    assert resolution["proposed_resolution_text"] != resolution["final_resolution_text"]
    core.place_resolution(case["id"], meeting["id"], resolution["id"], ACTOR)
    session = core.create_voting_session(case["id"], meeting["id"], {"resolution_ids": [resolution["id"]]}, ACTOR)
    core.open_voting(case["id"], meeting["id"], session["id"], ACTOR)
    for snap, vote in zip(snapshots, ("FOR", "AGAINST")):
        core.record_vote(case["id"], meeting["id"], session["id"], resolution["id"], {"meeting_member_snapshot_id": snap["id"], "vote": vote}, ACTOR)
    core.close_voting(case["id"], meeting["id"], session["id"], ACTOR)
    result = core.calculate_voting_result(case["id"], meeting["id"], session["id"], resolution["id"], ACTOR)
    assert (result["for_share"], result["against_share"], result["result"]) == ("60.0000", "40.0000", "APPROVED")
    core.finalize_voting_result(case["id"], meeting["id"], result["id"], ACTOR)

    draft = core.create_minutes_version(case["id"], meeting["id"], ACTOR)
    assert draft["snapshot"]["agenda"][1]["minutes_text"] == actual_text[1]
    document = store.create_module_record(case["id"], "documents", {"name": "minutes.docx", "category": "CoC Minutes", "storage_path": "test/minutes.docx"}, ACTOR)
    core.link_minutes_document(case["id"], meeting["id"], draft["id"], document["id"], ACTOR)
    final = core.finalize_minutes(case["id"], meeting["id"], draft["id"], ACTOR)
    assert final["status"] == "FINAL"
    action = core.create_action_item(case["id"], meeting["id"], {"agenda_item_id": items[2]["id"], "resolution_id": resolution["id"], "action_text": "Open process bank account", "owner": "RP", "due_date": "2026-09-01"}, ACTOR)
    assert core.list_atr(case["id"], meeting["id"])[0]["id"] == action["id"]
    completed = core.complete_meeting(case["id"], meeting["id"], ACTOR, "2026-08-30T11:55:00")
    assert completed["status"] == "COMPLETED"


def test_minutes_use_verbatim_office_text_and_missing_substantive_entry_blocks_final(tmp_path):
    store, case, core, meeting, agenda, _ = _first_meeting(tmp_path)
    items = agenda["items"]
    for item in items[:2]:
        core.save_minutes_entry(case["id"], meeting["id"], item["id"], {"minutes_text": "Office text."}, ACTOR)
    with pytest.raises(ValueError, match="Agenda item"):
        core.finalize_minutes(case["id"], meeting["id"], core.create_minutes_version(case["id"], meeting["id"], ACTOR)["id"], ACTOR)
    exact = "The IRP informed the CoC that...\nLine two remains unchanged."
    core.save_minutes_entry(case["id"], meeting["id"], items[2]["id"], {"minutes_text": exact}, ACTOR)
    draft = core.create_minutes_version(case["id"], meeting["id"], ACTOR)
    context = core.document_context(case["id"], meeting["id"], "minutes", draft["id"])
    path = CocDocumentService(Path(__file__).resolve().parents[1] / "templates" / "coc").generate(
        "minutes", context["case"], context["workflow"], [], {}, tmp_path / "minutes.docx"
    )
    with zipfile.ZipFile(path) as archive:
        text = archive.read("word/document.xml").decode("utf-8")
    assert "The IRP informed the CoC that..." in text and "Line two remains unchanged." in text


def test_invitees_do_not_count_and_no_quorum_blocks_ordinary_completion(tmp_path):
    _, case, core, meeting, agenda, _ = _first_meeting(tmp_path)
    snapshots = core.member_snapshot(case["id"], meeting["id"])
    rule, _ = _confirmed_rules(core)
    core.record_attendance(case["id"], meeting["id"], {"meeting_member_snapshot_id": snapshots[0]["id"], "participant_name": "FC A rep", "participant_role": "COC_MEMBER_REPRESENTATIVE", "authorization_status": "VALID", "present": True}, ACTOR)
    core.record_attendance(case["id"], meeting["id"], {"participant_name": "Suspended director", "participant_role": "SUSPENDED_DIRECTOR", "present": True}, ACTOR)
    core.record_attendance(case["id"], meeting["id"], {"participant_name": "Lawyer", "participant_role": "LEGAL_COUNSEL", "present": True}, ACTOR)
    quorum = core.calculate_quorum(case["id"], meeting["id"], {"quorum_rule_id": rule["id"]}, ACTOR)
    assert (quorum["present_voting_share"], quorum["status"]) == ("60.0000", "MET")
    high = core.upsert_quorum_rule({"rule_code": "TEST-QUORUM-70", "effective_from": "2026-01-01", "minimum_voting_share": "70.0000", "status": "CONFIRMED"}, ACTOR)
    no_quorum = core.calculate_quorum(case["id"], meeting["id"], {"quorum_rule_id": high["id"]}, ACTOR)
    assert no_quorum["status"] == "NO_QUORUM"
    with pytest.raises(ValueError, match="quorum-met"):
        core.complete_meeting(case["id"], meeting["id"], ACTOR)
    assert core.adjourn_meeting(case["id"], meeting["id"], {"reason": "Quorum not met"}, ACTOR)["status"] == "ADJOURNED"


def test_notice_freeze_reconstitution_review_and_agenda_immutability(tmp_path):
    store, case, core, meeting, agenda, _ = _first_meeting(tmp_path)
    issued_items = agenda["items"]
    with pytest.raises(ValueError, match="Frozen Agenda"):
        core.update_agenda_item(case["id"], meeting["id"], agenda["id"], issued_items[0]["id"], {"title": "Changed"}, ACTOR)
    copied = core.create_agenda_version(case["id"], meeting["id"], ACTOR, {"copy_previous": True})
    assert copied["version_number"] == 2
    EventEngine(store).record_event(case["id"], "COC_RECONSTITUTED", "2026-08-31", ACTOR, source_type="test", source_id="reconstitution-v2", idempotency_key="reconstitution-v2")
    after = core.get_meeting(case["id"], meeting["id"])
    assert after["membership_review_required"] == 1
    assert [(row["creditor_name"], row["voting_share_text"]) for row in after["member_snapshot"]] == [("FC A", "60.0000"), ("FC B", "40.0000")]


def test_closed_vote_requires_controlled_correction_and_case_isolation(tmp_path):
    store, case, core, meeting, agenda, _ = _first_meeting(tmp_path)
    snapshots = core.member_snapshot(case["id"], meeting["id"])
    _, approval = _confirmed_rules(core)
    resolution = core.create_resolution(case["id"], meeting["id"], {"agenda_item_id": agenda["items"][2]["id"], "title": "Vote", "voting_required": True, "approval_rule_id": approval["id"]}, ACTOR)
    session = core.create_voting_session(case["id"], meeting["id"], {"resolution_ids": [resolution["id"]]}, ACTOR)
    core.open_voting(case["id"], meeting["id"], session["id"], ACTOR)
    vote = core.record_vote(case["id"], meeting["id"], session["id"], resolution["id"], {"meeting_member_snapshot_id": snapshots[0]["id"], "vote": "FOR"}, ACTOR)
    core.close_voting(case["id"], meeting["id"], session["id"], ACTOR)
    with pytest.raises(ValueError, match="OPEN"):
        core.record_vote(case["id"], meeting["id"], session["id"], resolution["id"], {"meeting_member_snapshot_id": snapshots[1]["id"], "vote": "AGAINST"}, ACTOR)
    assert core.correct_vote(case["id"], meeting["id"], vote["id"], {"new_vote": "AGAINST", "reason": "External result corrected"}, ACTOR)["vote"] == "AGAINST"
    other = store.create_case({"name": "Other case", "process_type": "CIRP", "commencement_date": "2026-08-01"}, ACTOR)
    with pytest.raises(KeyError):
        core.get_meeting(other["id"], meeting["id"])


def test_workflow_master_extends_through_076_and_summary_has_meeting_keys(tmp_path):
    store, case, _, _, _, _, _, _, _ = build_standard_coc(tmp_path)
    definitions = WorkflowService(store).definitions()
    # Phase 3 owns the CoC-meeting sequence; this assertion must not freeze later workflow extensions.
    assert {f"CIRP-{number:03d}" for number in range(46, 57)}.issubset({row["step_code"] for row in definitions})
    summary = WorkflowService(store).summary(case["id"])
    assert "coc_meeting_count" in summary and summary["coc_meeting_count"] == 0


def test_meeting_api_enforces_read_only_role_and_case_isolation(tmp_path, monkeypatch):
    store, case, _, _, _, _, _, _, constitution = build_standard_coc(tmp_path)
    manager = store.create_user("phase3-manager@example.com", "Phase 3 Manager", "manager", server.ADMIN_PASSWORD_HASH)
    viewer = store.create_user("phase3-viewer@example.com", "Phase 3 Viewer", "viewer", server.ADMIN_PASSWORD_HASH)
    store.set_case_assignments(case["id"], [manager["id"], viewer["id"]], ACTOR)
    other = store.create_case({"name": "Unassigned case", "process_type": "CIRP", "commencement_date": "2026-08-01"}, ACTOR)
    monkeypatch.setattr(server, "casefile_store", store)
    with TestClient(server.app) as client:
        manager_login = client.post("/api/auth/login", json={"email": manager["email"], "password": server.admin_pwd})
        viewer_login = client.post("/api/auth/login", json={"email": viewer["email"], "password": server.admin_pwd})
        assert manager_login.status_code == 200, manager_login.text
        assert viewer_login.status_code == 200, viewer_login.text
        manager_headers = {"Authorization": f"Bearer {manager_login.json()['access_token']}"}
        viewer_headers = {"Authorization": f"Bearer {viewer_login.json()['access_token']}"}
        created = client.post(f"/api/cases/{case['id']}/coc/meetings", json={
            "meeting_type": "FIRST_COC", "coc_constitution_id": constitution["id"],
            "scheduled_start_at": "2026-08-30T10:00:00", "mode": "VIDEO_CONFERENCE",
        }, headers=manager_headers)
        assert created.status_code == 200, created.text
        meeting_id = created.json()["id"]
        assert client.get(f"/api/cases/{case['id']}/coc/meetings/{meeting_id}", headers=viewer_headers).status_code == 200
        denied = client.put(f"/api/cases/{case['id']}/coc/meetings/{meeting_id}/schedule", json={"scheduled_start_at": "2026-08-31T10:00:00"}, headers=viewer_headers)
        assert denied.status_code == 403
        isolated = client.get(f"/api/cases/{other['id']}/coc/meetings/{meeting_id}", headers=manager_headers)
        assert isolated.status_code == 404
