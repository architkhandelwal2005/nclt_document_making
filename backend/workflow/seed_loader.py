"""Load versioned workflow master data without creating case-specific records."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
import json
import sqlite3
import uuid


def _id(*parts: str) -> str:
    return ":".join(str(part) for part in parts)


def seed_cirp_workflow(connection: sqlite3.Connection, source: Path) -> None:
    """Upsert the immutable identity of a versioned seed and editable rule fields.

    Case workflow instances keep their ``workflow_version`` and definition IDs,
    so loading a future version cannot silently alter an existing case.
    """
    payload: Dict[str, Any] = json.loads(Path(source).read_text(encoding="utf-8"))
    workflow_type = payload["workflow_type"]
    workflow_version = payload["workflow_version"]
    now = payload.get("seeded_at") or "2026-08-27T00:00:00+00:00"

    phase_ids: Dict[str, str] = {}
    for phase in payload["phases"]:
        phase_id = _id(workflow_version, "phase", phase["code"])
        phase_ids[phase["code"]] = phase_id
        connection.execute(
            """INSERT INTO workflow_phases
            (id,workflow_type,workflow_version,code,name,sequence,description,is_active,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name,sequence=excluded.sequence,
            description=excluded.description,is_active=excluded.is_active,updated_at=excluded.updated_at""",
            (phase_id, workflow_type, workflow_version, phase["code"], phase["name"],
             phase["sequence"], phase.get("description", ""), int(phase.get("is_active", True)), now, now),
        )

    definition_ids: Dict[str, str] = {}
    for step in payload["steps"]:
        definition_id = _id(workflow_version, step["step_code"], f"r{step.get('rule_version', 1)}")
        definition_ids[step["step_code"]] = definition_id
        connection.execute(
            """INSERT INTO workflow_step_definitions
            (id,workflow_type,workflow_version,step_code,phase_id,sequence,day_trigger_text,
             legal_reference,area,activity,staff_action,standard_output,automation_type,
             approval_role,depends_on_description,requires_coc_approval,requires_nclt_filing,
             requires_ibbi_filing,evidence_requirement_text,trigger_event_type,creates_task,
             task_title,default_priority,is_active,rule_version,effective_from,effective_to,
             created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET phase_id=excluded.phase_id,sequence=excluded.sequence,
            day_trigger_text=excluded.day_trigger_text,legal_reference=excluded.legal_reference,
            area=excluded.area,activity=excluded.activity,staff_action=excluded.staff_action,
            standard_output=excluded.standard_output,automation_type=excluded.automation_type,
            approval_role=excluded.approval_role,depends_on_description=excluded.depends_on_description,
            requires_coc_approval=excluded.requires_coc_approval,
            requires_nclt_filing=excluded.requires_nclt_filing,
            requires_ibbi_filing=excluded.requires_ibbi_filing,
            evidence_requirement_text=excluded.evidence_requirement_text,
            trigger_event_type=excluded.trigger_event_type,creates_task=excluded.creates_task,
            task_title=excluded.task_title,default_priority=excluded.default_priority,
            is_active=excluded.is_active,effective_from=excluded.effective_from,
            effective_to=excluded.effective_to,updated_at=excluded.updated_at""",
            (
                definition_id, workflow_type, workflow_version, step["step_code"],
                phase_ids[step["phase_code"]], step["sequence"], step.get("day_trigger_text", ""),
                step.get("legal_reference", ""), step.get("area", ""), step["activity"],
                step.get("staff_action", step["activity"]), step.get("standard_output", ""),
                step["automation_type"], step.get("approval_role", ""),
                step.get("depends_on_description", ""), int(step.get("requires_coc_approval", False)),
                int(step.get("requires_nclt_filing", False)), int(step.get("requires_ibbi_filing", False)),
                step.get("evidence_requirement_text", ""), step["trigger_event_type"],
                int(step.get("creates_task", True)), step.get("task_title", step["standard_output"]),
                step.get("default_priority", "normal"), int(step.get("is_active", True)),
                step.get("rule_version", 1), step["effective_from"], step.get("effective_to"), now, now,
            ),
        )

        deadline = step.get("deadline_rule")
        if not deadline and int(step["sequence"]) >= 24:
            # An explicit unresolved rule is safer than omitting the control:
            # the case deadline remains REVIEW_REQUIRED until legal timing is
            # configured, and no service is tempted to guess a statutory date.
            deadline = {
                "anchor_event_type": step["trigger_event_type"],
                "offset_days": None,
                "rule_text": "Exact legal timing is not encoded; professional review is required.",
                "legal_reference": step.get("legal_reference", ""),
            }
        if deadline:
            rule_id = _id(definition_id, "deadline", f"r{deadline.get('rule_version', 1)}")
            connection.execute(
                """INSERT INTO deadline_rules
                (id,step_definition_id,anchor_event_type,offset_days,offset_direction,calendar_basis,
                 rule_text,legal_reference,rule_version,effective_from,effective_to,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET anchor_event_type=excluded.anchor_event_type,
                offset_days=excluded.offset_days,offset_direction=excluded.offset_direction,
                calendar_basis=excluded.calendar_basis,rule_text=excluded.rule_text,
                legal_reference=excluded.legal_reference,effective_from=excluded.effective_from,
                effective_to=excluded.effective_to,updated_at=excluded.updated_at""",
                (rule_id, definition_id, deadline["anchor_event_type"], deadline.get("offset_days"),
                 deadline.get("offset_direction", "AFTER"), deadline.get("calendar_basis", "CALENDAR_DAYS"),
                 deadline.get("rule_text", ""), deadline.get("legal_reference", step.get("legal_reference", "")),
                 deadline.get("rule_version", 1), deadline.get("effective_from", step["effective_from"]),
                 deadline.get("effective_to"), now, now),
            )

        for evidence in step.get("evidence_requirements", []):
            evidence_id = _id(definition_id, "evidence", evidence["evidence_type"])
            connection.execute(
                """INSERT INTO workflow_evidence_requirements
                (id,step_definition_id,evidence_type,mandatory,description,created_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET mandatory=excluded.mandatory,description=excluded.description""",
                (evidence_id, definition_id, evidence["evidence_type"],
                 int(evidence.get("mandatory", False)), evidence.get("description", ""), now),
            )

        for template in step.get("template_links", []):
            link_id = _id(definition_id, "template", template["template_id"], template.get("template_role", "output"))
            connection.execute(
                """INSERT INTO workflow_template_links
                (id,step_definition_id,template_id,template_role,created_at)
                VALUES (?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
                (link_id, definition_id, template["template_id"], template.get("template_role", "output"), now),
            )

    for dependency in payload.get("dependencies", []):
        parent_id = definition_ids[dependency["parent_step_code"]]
        child_id = definition_ids[dependency["child_step_code"]]
        dependency_type = dependency["dependency_type"]
        dependency_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{parent_id}|{child_id}|{dependency_type}"))
        connection.execute(
            """INSERT INTO workflow_dependencies
            (id,parent_step_definition_id,child_step_definition_id,dependency_type,created_at)
            VALUES (?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
            (dependency_id, parent_id, child_id, dependency_type, now),
        )
