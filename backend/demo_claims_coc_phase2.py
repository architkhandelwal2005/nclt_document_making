"""Deterministic local demonstration of the CIRP-024--045 backend chain."""

from pathlib import Path
from tempfile import TemporaryDirectory

from claims_coc_core import ClaimsCocCore
from claims_workflow import ClaimsWorkflow
from database import CasefileDatabase
from workflow import EventEngine


ACTOR = "demo-rp"


def add_claim(store, case_id, key, name, category, claimed, admitted):
    service = ClaimsWorkflow(store, store.path.parent)
    claim = service.create(case_id, {
        "received_date": "2026-08-10", "creditor_name": name, "creditor_category": category,
        "form_type": "Form C" if category == "FINANCIAL_CREDITOR" else "Form B",
        "claimed_amount": claimed, "principal_claimed": claimed,
        "email": f"{key}@example.com", "idempotency_key": key,
    }, ACTOR)
    service.confirm_classification(case_id, claim["id"], {}, ACTOR)
    service.update_scrutiny(case_id, claim["id"], {"scrutiny_status": "COMPLETE"}, ACTOR)
    service.start_verification(case_id, claim["id"], ACTOR)
    if category == "FINANCIAL_CREDITOR":
        service.confirm_related_party(case_id, claim["id"], {
            "related_party_status": "NO", "reason": "Demo professional confirmation",
        }, ACTOR)
    return service.decide(case_id, claim["id"], {
        "decision_status": "ADMITTED" if claimed == admitted else "PARTLY_ADMITTED",
        "principal_admitted": admitted, "interest_admitted": 0, "other_amount_admitted": 0,
        "decision_date": "2026-08-20", "reason": "Demo supported amount" if claimed != admitted else "",
    }, ACTOR)


def main():
    with TemporaryDirectory(prefix="casefile-phase2-demo-") as temporary:
        store = CasefileDatabase(Path(temporary) / "demo.db")
        store.ensure_admin(ACTOR, "demo-rp@example.com", "Demo RP", "unused")
        case = store.create_case({"name": "Demo CIRP", "process_type": "CIRP", "commencement_date": "2026-08-01"}, ACTOR)
        events = EventEngine(store)
        events.record_event(case["id"], "ADMISSION_ORDER_CONFIRMED", "2026-08-01", ACTOR,
                            source_type="demo", source_id="admission",
                            metadata={"cirp_commencement_date": "2026-08-01"})
        events.record_event(case["id"], "PUBLIC_ANNOUNCEMENT_CONFIRMED", "2026-08-04", ACTOR,
                            source_type="demo", source_id="pa")
        fc_a = add_claim(store, case["id"], "fc-a", "FC-A", "FINANCIAL_CREDITOR", 7_500_000, 6_000_000)
        fc_b = add_claim(store, case["id"], "fc-b", "FC-B", "FINANCIAL_CREDITOR", 5_000_000, 4_000_000)
        add_claim(store, case["id"], "oc-c", "OC-C", "OPERATIONAL_CREDITOR", 2_500_000, 2_000_000)
        core = ClaimsCocCore(store)
        loc = core.create_loc_snapshot(case["id"], ACTOR, "2026-08-21")
        for claim in (fc_a, fc_b):
            core.confirm_eligibility(case["id"], claim["id"], {
                "eligibility_status": "ELIGIBLE", "related_party_status": "NO",
            }, ACTOR)
        voting = core.calculate_voting(case["id"], ACTOR)
        version_one = core.confirm_constitution(case["id"], {
            "constitution_date": "2026-08-22", "voting_calculation_id": voting["id"],
        }, ACTOR)

        print("CASE: Demo CIRP")
        print("CLAIMS REGISTER")
        for row in loc["rows"]:
            print(f"{row['claim_number']}  {row['creditor_name']:<4}  {row['creditor_category']:<20}  "
                  f"claimed={row['claimed']}  admitted={row['admitted']}  {row['decision_status']}")
        print(f"LIST OF CREDITORS V{loc['version_number']}: claimed={loc['total_claimed']} admitted={loc['total_admitted']}")
        print("COC VOTING")
        for row in voting["rows"]:
            print(f"{row['creditor_name']}: eligible_debt={row['admitted_debt']} voting={row['display_percentage']}%")
        print(f"CONSTITUTION: Version {version_one['constitution_version']} {version_one['status']}")

        claims = ClaimsWorkflow(store, store.path.parent)
        revised = claims.revise(case["id"], fc_a["id"], {
            "reason": "Demo revised evidence", "changes": {"claimed_amount": 8_000_000, "principal_claimed": 8_000_000},
        }, ACTOR)
        claims.decide(case["id"], revised["id"], {
            "decision_status": "PARTLY_ADMITTED", "principal_admitted": 5_000_000,
            "interest_admitted": 0, "other_amount_admitted": 0, "decision_date": "2026-08-25",
            "reason": "Demo revised admitted amount",
        }, ACTOR)
        unchanged = core.get_constitution(case["id"], version_one["id"])
        print(f"COC_REVIEW_REQUIRED: {bool(unchanged['review_required'])}")
        print(f"VERSION 1 FC-A DEBT UNCHANGED: {[m for m in unchanged['members'] if m['creditor_name'] == 'FC-A'][0]['admitted_debt']}")
        core.create_loc_snapshot(case["id"], ACTOR, "2026-08-25")
        core.confirm_eligibility(case["id"], fc_a["id"], {
            "eligibility_status": "ELIGIBLE", "related_party_status": "NO",
        }, ACTOR)
        voting_two = core.calculate_voting(case["id"], ACTOR)
        version_two = core.confirm_constitution(case["id"], {
            "constitution_date": "2026-08-26", "voting_calculation_id": voting_two["id"],
        }, ACTOR, reconstitution=True)
        print(f"RECONSTITUTION: Version {version_two['constitution_version']} {version_two['status']}")


if __name__ == "__main__":
    main()
