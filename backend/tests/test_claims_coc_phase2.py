"""CIRP-024--045 Claims, LOC, CoC and reconstitution integration tests."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import server
from claims_coc_core import ClaimsCocCore
from claims_workflow import ClaimsWorkflow
from database import CasefileDatabase
from workflow import EventEngine, WorkflowService


ACTOR = "admin"


def setup_case(tmp_path, name="Phase Two Limited", commencement="2026-08-01"):
    store = CasefileDatabase(tmp_path / f"{name.replace(' ', '-')}.db")
    store.ensure_admin(ACTOR, "admin@example.test", "Admin", "unused")
    case = store.create_case({"name": name, "process_type": "CIRP", "commencement_date": commencement}, ACTOR)
    events = EventEngine(store)
    events.record_event(case["id"], "ADMISSION_ORDER_CONFIRMED", commencement, ACTOR,
                        source_type="test", source_id="admission",
                        metadata={"cirp_commencement_date": commencement}, idempotency_key="admission")
    events.record_event(case["id"], "PUBLIC_ANNOUNCEMENT_CONFIRMED", "2026-08-04", ACTOR,
                        source_type="test", source_id="pa", idempotency_key="pa")
    return store, case


def add_decided_claim(store, case_id, key, name, category, claimed, admitted,
                      received="2026-08-10", related="NO"):
    claims = ClaimsWorkflow(store, store.path.parent)
    claim = claims.create(case_id, {
        "received_date": received, "received_via": "Email", "creditor_name": name,
        "creditor_category": category, "form_type": "Form C" if category == "FINANCIAL_CREDITOR" else "Form B",
        "claimed_amount": claimed, "principal_claimed": claimed, "email": f"{key}@example.test",
        "related_party_status": "UNKNOWN", "idempotency_key": key,
    }, ACTOR)
    claims.confirm_classification(case_id, claim["id"], {}, ACTOR)
    claims.update_scrutiny(case_id, claim["id"], {"scrutiny_status": "COMPLETE"}, ACTOR)
    claims.start_verification(case_id, claim["id"], ACTOR)
    if category == "FINANCIAL_CREDITOR":
        claims.confirm_related_party(case_id, claim["id"], {
            "related_party_status": related, "reason": "Professional review of available ownership records",
        }, ACTOR)
    status = "ADMITTED" if Decimal(str(claimed)) == Decimal(str(admitted)) else "PARTLY_ADMITTED"
    decided = claims.decide(case_id, claim["id"], {
        "decision_status": status, "principal_admitted": admitted, "interest_admitted": 0,
        "other_amount_admitted": 0, "decision_date": "2026-08-20",
        "reason": "Supported only to admitted amount" if status == "PARTLY_ADMITTED" else "",
    }, ACTOR)
    return decided


def build_standard_coc(tmp_path):
    store, case = setup_case(tmp_path)
    fc_a = add_decided_claim(store, case["id"], "fc-a", "FC A", "FINANCIAL_CREDITOR", 7_500_000, 6_000_000)
    fc_b = add_decided_claim(store, case["id"], "fc-b", "FC B", "FINANCIAL_CREDITOR", 5_000_000, 4_000_000)
    oc_c = add_decided_claim(store, case["id"], "oc-c", "OC C", "OPERATIONAL_CREDITOR", 2_500_000, 2_000_000)
    core = ClaimsCocCore(store)
    loc = core.create_loc_snapshot(case["id"], ACTOR, "2026-08-21")
    core.confirm_eligibility(case["id"], fc_a["id"], {"eligibility_status": "ELIGIBLE", "related_party_status": "NO"}, ACTOR)
    core.confirm_eligibility(case["id"], fc_b["id"], {"eligibility_status": "ELIGIBLE", "related_party_status": "NO"}, ACTOR)
    voting = core.calculate_voting(case["id"], ACTOR)
    constitution = core.confirm_constitution(case["id"], {
        "constitution_date": "2026-08-22", "voting_calculation_id": voting["id"],
    }, ACTOR)
    return store, case, core, fc_a, fc_b, oc_c, loc, voting, constitution


def test_standard_loc_voting_and_constitution_trace_to_claims(tmp_path):
    store, case, core, fc_a, fc_b, oc_c, loc, voting, constitution = build_standard_coc(tmp_path)
    loc_replay = core.create_loc_snapshot(case["id"], ACTOR, "2026-08-21")
    voting_replay = core.calculate_voting(case["id"], ACTOR)
    assert loc_replay["id"] == loc["id"] and loc_replay["idempotent_replay"] is True
    assert voting_replay["id"] == voting["id"] and voting_replay["idempotent_replay"] is True
    assert loc["version_number"] == 1
    assert {row["creditor_name"] for row in loc["rows"]} == {"FC A", "FC B", "OC C"}
    assert loc["total_claimed"] == "15000000.00"
    assert loc["total_admitted"] == "12000000.00"
    assert {row["creditor_name"] for row in voting["rows"]} == {"FC A", "FC B"}
    shares = {row["creditor_name"]: row["display_percentage"] for row in voting["rows"]}
    assert shares == {"FC A": "60.0000", "FC B": "40.0000"}
    assert voting["display_total"] == "100.0000"
    assert constitution["constitution_version"] == 1
    assert all(member["claim_id"] in {fc_a["id"], fc_b["id"]} for member in constitution["members"])
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM coc_members WHERE case_id=? AND valid_to IS NULL", (case["id"],)).fetchone()[0] == 2


def test_related_party_exclusion_recalculates_and_unknown_blocks(tmp_path):
    store, case = setup_case(tmp_path, "Related Party Limited")
    fc_a = add_decided_claim(store, case["id"], "rpa", "FC A", "FINANCIAL_CREDITOR", 60, 60)
    fc_b = add_decided_claim(store, case["id"], "rpb", "FC B", "FINANCIAL_CREDITOR", 40, 40, related="YES")
    core = ClaimsCocCore(store)
    core.create_loc_snapshot(case["id"], ACTOR)
    core.confirm_eligibility(case["id"], fc_a["id"], {"eligibility_status": "ELIGIBLE", "related_party_status": "NO"}, ACTOR)
    core.confirm_eligibility(case["id"], fc_b["id"], {
        "eligibility_status": "EXCLUDED", "related_party_status": "YES", "reason": "Confirmed related party",
    }, ACTOR)
    voting = core.calculate_voting(case["id"], ACTOR)
    assert [(row["creditor_name"], row["display_percentage"]) for row in voting["rows"]] == [("FC A", "100.0000")]

    store2, case2 = setup_case(tmp_path, "Unknown Review Limited")
    unknown = add_decided_claim(store2, case2["id"], "unknown", "Unknown FC", "FINANCIAL_CREDITOR", 100, 100, related="UNKNOWN")
    core2 = ClaimsCocCore(store2)
    core2.create_loc_snapshot(case2["id"], ACTOR)
    with pytest.raises(ValueError, match="RELATED_PARTY_REVIEW_REQUIRED"):
        core2.confirm_eligibility(case2["id"], unknown["id"], {"eligibility_status": "ELIGIBLE"}, ACTOR)
    preview = core2.constitution_preview(case2["id"])
    assert "RELATED_PARTY_REVIEW_REQUIRED" in preview["errors"]


def test_claim_revision_preserves_version_one_and_requires_reconstitution(tmp_path):
    store, case, core, fc_a, fc_b, _, _, _, version_one = build_standard_coc(tmp_path)
    claims = ClaimsWorkflow(store, store.path.parent)
    revised = claims.revise(case["id"], fc_a["id"], {
        "reason": "Additional evidence", "changes": {"claimed_amount": 8_000_000, "principal_claimed": 8_000_000},
    }, ACTOR)
    claims.decide(case["id"], revised["id"], {
        "decision_status": "PARTLY_ADMITTED", "principal_admitted": 5_000_000,
        "interest_admitted": 0, "other_amount_admitted": 0, "decision_date": "2026-08-25",
        "reason": "Revised verification outcome",
    }, ACTOR)
    original = core.get_constitution(case["id"], version_one["id"])
    assert {m["creditor_name"]: m["admitted_debt"] for m in original["members"]}["FC A"] == "6000000.00"
    assert original["review_required"] == 1
    stale_preview = core.constitution_preview(case["id"])
    assert "LIST_OF_CREDITORS_SNAPSHOT_STALE" in stale_preview["errors"]
    assert "VOTING_CALCULATION_STALE" in stale_preview["errors"]
    core.create_loc_snapshot(case["id"], ACTOR, "2026-08-25")
    core.confirm_eligibility(case["id"], fc_a["id"], {"eligibility_status": "ELIGIBLE", "related_party_status": "NO"}, ACTOR)
    voting_two = core.calculate_voting(case["id"], ACTOR)
    version_two = core.confirm_constitution(case["id"], {
        "constitution_date": "2026-08-26", "voting_calculation_id": voting_two["id"],
    }, ACTOR, reconstitution=True)
    assert version_two["constitution_version"] == 2
    assert core.get_constitution(case["id"], version_one["id"])["members"] == original["members"]


def test_late_claim_deficiency_and_event_task_idempotency(tmp_path):
    store, case = setup_case(tmp_path, "Late Claim Limited")
    claims = ClaimsWorkflow(store, store.path.parent)
    claim = claims.create(case["id"], {
        "received_date": "2026-08-20", "creditor_name": "Late Supplier", "creditor_category": "OPERATIONAL_CREDITOR",
        "form_type": "Form B", "claimed_amount": 100, "email": "late@example.test", "idempotency_key": "late",
    }, ACTOR)
    assert claim["late_flag"] == 1
    assert claim["claim_deadline_date"] == "2026-08-15"
    assert claim["days_after_deadline"] == 5
    claims.update_scrutiny(case["id"], claim["id"], {"scrutiny_status": "DEFICIENCY_FOUND"}, ACTOR)
    query = claims.create_query(case["id"], claim["id"], {
        "subject": "Ledger required", "query_text": "Please provide ledger", "status": "SENT",
    }, ACTOR)
    claims.record_response(case["id"], claim["id"], query["id"], {
        "response_received_date": "2026-08-22", "query_status": "CLOSED",
    }, ACTOR)
    # Replaying an already stored domain event must not duplicate workflow tasks.
    EventEngine(store).record_event(case["id"], "CLAIM_RECEIVED", "2026-08-20", ACTOR,
                                    source_type="claim", source_id=claim["id"],
                                    metadata={"claim_number": claim["claim_number"], "late_flag": True},
                                    idempotency_key=f"claim:CLAIM_RECEIVED:{claim['id']}:")
    with store.connect() as connection:
        step = connection.execute("""SELECT cws.status FROM case_workflow_steps cws JOIN workflow_step_definitions wsd
                                  ON wsd.id=cws.step_definition_id WHERE cws.case_id=? AND wsd.step_code='CIRP-036'""", (case["id"],)).fetchone()
        assert step["status"] == "READY"
        task_count = connection.execute("""SELECT COUNT(*) FROM tasks t JOIN case_workflow_steps cws ON cws.id=t.workflow_step_id
                                        JOIN workflow_step_definitions wsd ON wsd.id=cws.step_definition_id
                                        WHERE t.case_id=? AND wsd.step_code='CIRP-036'""", (case["id"],)).fetchone()[0]
        assert task_count == 1


def test_awkward_percentages_allocate_exact_display_total(tmp_path):
    store, case = setup_case(tmp_path, "Thirds Limited")
    core = ClaimsCocCore(store)
    for index in range(3):
        claim = add_decided_claim(store, case["id"], f"third-{index}", f"FC {index + 1}", "FINANCIAL_CREDITOR", 1, 1)
        core.confirm_eligibility(case["id"], claim["id"], {"eligibility_status": "ELIGIBLE", "related_party_status": "NO"}, ACTOR)
    voting = core.calculate_voting(case["id"], ACTOR, 4)
    assert sorted(row["display_percentage"] for row in voting["rows"]) == ["33.3333", "33.3333", "33.3334"]
    assert voting["display_total"] == "100.0000"


def test_case_isolation_rejects_cross_case_snapshot_and_claim_ids(tmp_path):
    store_a, case_a = setup_case(tmp_path, "Isolation A")
    claim_a = add_decided_claim(store_a, case_a["id"], "iso-a", "FC A", "FINANCIAL_CREDITOR", 10, 10)
    # Both cases share one store for a meaningful cross-case check.
    case_b = store_a.create_case({"name": "Isolation B", "process_type": "CIRP", "commencement_date": "2026-08-01"}, ACTOR)
    core = ClaimsCocCore(store_a)
    snapshot = core.create_loc_snapshot(case_a["id"], ACTOR)
    with pytest.raises(KeyError):
        core.get_loc_snapshot(case_b["id"], snapshot["id"])
    with pytest.raises(KeyError):
        core.confirm_eligibility(case_b["id"], claim_a["id"], {"eligibility_status": "ELIGIBLE"}, ACTOR)


def test_workflow_master_and_summary_extend_through_045(tmp_path):
    store, case = setup_case(tmp_path, "Summary Phase Two")
    definitions = WorkflowService(store).definitions()
    assert [item["step_code"] for item in definitions] == [f"CIRP-{number:03d}" for number in range(1, 46)]
    summary = WorkflowService(store).summary(case["id"])
    assert summary["total_steps"] == 45
    assert summary["claims_received"] == 0
    assert summary["coc_status"] == "NOT_CONSTITUTED"


def test_undecided_financial_claim_blocks_constitution_preview(tmp_path):
    store, case = setup_case(tmp_path, "Undecided FC Limited")
    ClaimsWorkflow(store, store.path.parent).create(case["id"], {
        "received_date": "2026-08-10", "creditor_name": "Pending FC",
        "creditor_category": "FINANCIAL_CREDITOR", "form_type": "Form C",
        "claimed_amount": 100, "email": "pending-fc@example.com", "idempotency_key": "pending-fc",
    }, ACTOR)
    core = ClaimsCocCore(store)
    core.create_loc_snapshot(case["id"], ACTOR)
    preview = core.constitution_preview(case["id"])
    assert "FINANCIAL_CREDITOR_CLAIMS_UNDECIDED" in preview["errors"]


def test_creditor_class_ar_shell_report_artifact_and_api_authorization(tmp_path, monkeypatch):
    store, case, core, fc_a, _, _, _, _, constitution = build_standard_coc(tmp_path)
    creditor_class = core.upsert_creditor_class(case["id"], {
        "class_name": "Homebuyers", "class_type": "CREDITOR_IN_CLASS",
        "claim_ids": [fc_a["id"]], "ar_required": True,
    }, ACTOR)
    assert creditor_class["creditor_count"] == 1
    ar = core.upsert_ar_process(case["id"], creditor_class["id"], {
        "requirement_status": "REVIEW_REQUIRED", "candidate_name": "Proposed AR",
        "status": "IN_REVIEW", "filing_requirement": "Professional review required",
    }, ACTOR)
    assert ar["status"] == "IN_REVIEW"

    with store.transaction() as connection:
        connection.execute("UPDATE users SET email=?,password_hash=? WHERE id=?",
                           (server.ADMIN_EMAIL, server.ADMIN_PASSWORD_HASH, ACTOR))
    monkeypatch.setattr(server, "casefile_store", store)
    data_dir = tmp_path / "api-data"
    files_dir = data_dir / "files"
    files_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "CASE_FILES_DIR", files_dir)
    with TestClient(server.app) as client:
        login = client.post("/api/auth/login", json={"email": server.ADMIN_EMAIL, "password": server.admin_pwd})
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        report = client.post(
            f"/api/cases/{case['id']}/coc/constitutions/{constitution['id']}/report",
            json={"status": "DRAFT"}, headers=headers,
        )
        assert report.status_code == 200, report.text
        document = report.json()["report_document"]
        assert document["linked_type"] == "coc_constitution"
        assert (data_dir / document["storage_path"]).read_bytes().startswith(b"PK")
        final_report = client.post(
            f"/api/cases/{case['id']}/coc/constitutions/{constitution['id']}/report",
            json={"status": "FINAL"}, headers=headers,
        )
        assert final_report.status_code == 200, final_report.text
        final_document = final_report.json()["report_document"]
        assert final_document["version"] == 2
        assert final_document["parent_document_id"] == document["id"]

        created_user = client.post("/api/admin/users", json={
            "name": "Read Only", "email": "phase2-viewer@example.com", "role": "viewer",
            "password": "Temporary-Password-123",
        }, headers=headers)
        assert created_user.status_code == 200, created_user.text
        user = created_user.json()
        assert client.put(f"/api/admin/cases/{case['id']}/assignments", json={"user_ids": [user["id"]]}, headers=headers).status_code == 200
        viewer_login = client.post("/api/auth/login", json={"email": "phase2-viewer@example.com", "password": "Temporary-Password-123"})
        assert viewer_login.status_code == 200, viewer_login.text
        viewer = {"Authorization": f"Bearer {viewer_login.json()['access_token']}"}
        assert client.get(f"/api/cases/{case['id']}/coc/constitutions", headers=viewer).status_code == 200
        denied = client.put(f"/api/cases/{case['id']}/coc/candidates/{fc_a['id']}/eligibility",
                            json={"eligibility_status": "ELIGIBLE"}, headers=viewer)
        assert denied.status_code == 403

        associate_response = client.post("/api/admin/users", json={
            "name": "Claims Associate", "email": "phase2-associate@example.com", "role": "associate",
            "password": "Temporary-Password-123",
        }, headers=headers)
        assert associate_response.status_code == 200, associate_response.text
        associate_user = associate_response.json()
        assert client.put(f"/api/admin/cases/{case['id']}/assignments",
                          json={"user_ids": [user["id"], associate_user["id"]]}, headers=headers).status_code == 200
        associate_login = client.post("/api/auth/login", json={
            "email": "phase2-associate@example.com", "password": "Temporary-Password-123",
        })
        assert associate_login.status_code == 200, associate_login.text
        associate = {"Authorization": f"Bearer {associate_login.json()['access_token']}"}
        decision_denied = client.post(f"/api/cases/{case['id']}/claims/{fc_a['id']}/decision", json={
            "decision_status": "ADMITTED", "principal_admitted": 6_000_000,
            "interest_admitted": 0, "other_amount_admitted": 0,
        }, headers=associate)
        assert decision_denied.status_code == 403
