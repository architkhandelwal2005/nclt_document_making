"""Versioned CoC meeting lifecycle for CIRP-046 through CIRP-056.

The module extends the retained CoC tables and DOCX generator.  It does not
create factual minutes: ``minutes_text`` is an office-authored, verbatim input
that is only assembled into the retained Word template.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional
import json
import sqlite3

from claims_coc_core import from_paise, to_paise
from database import CasefileDatabase, _from_json, _json, new_id, utc_now
from workflow import EventEngine


SHARE_SCALE = 10_000  # Four displayed decimal places: 100.0000% = 1,000,000 units.
TOTAL_SHARE_UNITS = 100 * SHARE_SCALE
MEETING_TYPES = {"FIRST_COC", "SUBSEQUENT_COC", "ADJOURNED_COC", "SPECIAL_COC"}
MEETING_MODES = {"PHYSICAL", "VIDEO_CONFERENCE", "HYBRID"}
AGENDA_TYPES = {"PROCEDURAL", "FOR_NOTING", "FOR_DISCUSSION", "FOR_RATIFICATION", "FOR_APPROVAL", "FOR_VOTING"}
MINUTES_DISPOSITIONS = {"NO_SEPARATE_DISCUSSION", "NOT_TAKEN_UP", "DEFERRED", "WITHDRAWN"}
VOTES = {"FOR", "AGAINST", "ABSTAIN", "NOT_VOTED"}


def _share_units(value: Any) -> int:
    try:
        decimal = Decimal(str(value or "0")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    except Exception as exc:  # Decimal raises several implementation-specific exceptions.
        raise ValueError("Voting share must be a valid percentage") from exc
    if decimal < 0 or decimal > 100:
        raise ValueError("Voting share must be between 0 and 100")
    return int(decimal * SHARE_SCALE)


def _share_text(units: Any) -> str:
    return format(Decimal(int(units or 0)) / SHARE_SCALE, ".4f")


def _day(value: Any, label: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{label} is required")
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO date") from exc


class CocMeetingCore:
    """Case-isolated meeting, voting, minutes and ATR domain service."""

    def __init__(self, store: CasefileDatabase):
        self.store = store

    def _emit(self, case_id: str, event_type: str, actor_id: str, source_id: str,
              event_date: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
        EventEngine(self.store).record_event(
            case_id, event_type, event_date or date.today().isoformat(), actor_id,
            source_type="domain", source_id=source_id, metadata=metadata or {},
            idempotency_key=f"domain:{event_type}:{source_id}",
        )

    @staticmethod
    def _json_row(row: sqlite3.Row | None, field: str, fallback: Any) -> Any:
        return _from_json(row[field], fallback) if row else fallback

    @staticmethod
    def _meeting(connection: sqlite3.Connection, case_id: str, meeting_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM coc_meetings WHERE id=? AND case_id=? AND archived_at IS NULL", (meeting_id, case_id)
        ).fetchone()
        if not row:
            raise KeyError("CoC meeting not found")
        return row

    @staticmethod
    def _constitution(connection: sqlite3.Connection, case_id: str, constitution_id: Optional[str] = None) -> sqlite3.Row:
        if constitution_id:
            row = connection.execute(
                "SELECT * FROM coc_constitutions WHERE id=? AND case_id=? AND status='CONFIRMED'", (constitution_id, case_id)
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM coc_constitutions WHERE case_id=? AND status='CONFIRMED' ORDER BY constitution_version DESC LIMIT 1",
                (case_id,),
            ).fetchone()
        if not row:
            raise ValueError("A confirmed CoC Constitution is required before creating a CoC meeting")
        return row

    def _ensure_task(self, connection: sqlite3.Connection, case_id: str, title: str, due_date: Optional[str],
                     source_type: str, source_id: str, actor_id: str) -> str:
        existing = connection.execute(
            "SELECT id FROM tasks WHERE case_id=? AND source_type=? AND source_id=? AND title=? AND archived_at IS NULL",
            (case_id, source_type, source_id, title),
        ).fetchone()
        if existing:
            return existing["id"]
        return self.store._create_automated_task(connection, case_id, title, "CoC", due_date, source_type, source_id, actor_id)

    @staticmethod
    def _agenda_rows(connection: sqlite3.Connection, case_id: str, meeting_id: str,
                     agenda_version_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if not agenda_version_id:
            row = connection.execute(
                "SELECT id FROM coc_agenda_versions WHERE case_id=? AND meeting_id=? ORDER BY version_number DESC LIMIT 1",
                (case_id, meeting_id),
            ).fetchone()
            agenda_version_id = row["id"] if row else None
        if not agenda_version_id:
            return []
        rows = connection.execute(
            "SELECT * FROM coc_agenda_version_items WHERE case_id=? AND meeting_id=? AND agenda_version_id=? ORDER BY sequence",
            (case_id, meeting_id, agenda_version_id),
        ).fetchall()
        return [dict(row) | {"supporting_document_ids": _from_json(row["supporting_document_ids_json"], [])} for row in rows]

    @staticmethod
    def _agenda_version(connection: sqlite3.Connection, case_id: str, meeting_id: str,
                        agenda_version_id: Optional[str] = None) -> sqlite3.Row:
        if agenda_version_id:
            row = connection.execute(
                "SELECT * FROM coc_agenda_versions WHERE id=? AND case_id=? AND meeting_id=?",
                (agenda_version_id, case_id, meeting_id),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM coc_agenda_versions WHERE case_id=? AND meeting_id=? ORDER BY version_number DESC LIMIT 1",
                (case_id, meeting_id),
            ).fetchone()
        if not row:
            raise ValueError("Create an Agenda version before this action")
        return row

    def list_meetings(self, case_id: str) -> List[Dict[str, Any]]:
        with self.store.connect() as connection:
            self.store.ensure_case(connection, case_id)
            rows = connection.execute(
                "SELECT * FROM coc_meetings WHERE case_id=? AND archived_at IS NULL ORDER BY meeting_number, created_at",
                (case_id,),
            ).fetchall()
        return [dict(row) | {"notice_snapshot": _from_json(row["notice_snapshot_json"], {})} for row in rows]

    def get_meeting(self, case_id: str, meeting_id: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            meeting = self._meeting(connection, case_id, meeting_id)
            result = dict(meeting)
            result["notice_snapshot"] = _from_json(meeting["notice_snapshot_json"], {})
            result["agendas"] = self.list_agenda_versions(case_id, meeting_id, connection)
            result["notice_versions"] = [dict(row) for row in connection.execute(
                "SELECT * FROM coc_notice_versions WHERE case_id=? AND meeting_id=? ORDER BY version_number", (case_id, meeting_id)
            ).fetchall()]
            result["quorum"] = self.latest_quorum(case_id, meeting_id, connection)
            result["attendance"] = [dict(row) for row in connection.execute(
                "SELECT * FROM coc_attendance WHERE case_id=? AND meeting_id=? AND archived_at IS NULL ORDER BY participant_name",
                (case_id, meeting_id),
            ).fetchall()]
            result["member_snapshot"] = self.member_snapshot(case_id, meeting_id, connection)
            result["resolutions"] = [dict(row) for row in connection.execute(
                "SELECT * FROM coc_resolutions WHERE case_id=? AND meeting_id=? ORDER BY resolution_number", (case_id, meeting_id)
            ).fetchall()]
            result["minutes_versions"] = [dict(row) for row in connection.execute(
                "SELECT * FROM coc_minutes_versions WHERE case_id=? AND meeting_id=? ORDER BY version_number", (case_id, meeting_id)
            ).fetchall()]
            return result

    def create_meeting(self, case_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        meeting_type = str(payload.get("meeting_type") or "FIRST_COC").upper()
        if meeting_type not in MEETING_TYPES:
            raise ValueError("Unsupported CoC meeting type")
        mode = str(payload.get("mode") or "VIDEO_CONFERENCE").upper()
        if mode not in MEETING_MODES:
            raise ValueError("Meeting mode must be PHYSICAL, VIDEO_CONFERENCE or HYBRID")
        now = utc_now()
        with self.store.transaction() as connection:
            self.store.ensure_case(connection, case_id)
            constitution = self._constitution(connection, case_id, payload.get("coc_constitution_id"))
            next_number = int(connection.execute(
                "SELECT COALESCE(MAX(meeting_number),0)+1 FROM coc_meetings WHERE case_id=? AND archived_at IS NULL", (case_id,)
            ).fetchone()[0])
            requested = payload.get("meeting_number")
            if requested is not None and int(requested) != next_number:
                raise ValueError(f"Meeting number must be the next case-specific number: {next_number}")
            if meeting_type == "FIRST_COC" and next_number != 1:
                raise ValueError("FIRST_COC may only be the first numbered CoC meeting")
            meeting_id = new_id()
            scheduled_start = str(payload.get("scheduled_start_at") or payload.get("meeting_at") or "")
            meeting_due = self._workflow_due_date(connection, case_id, "CIRP-046")
            connection.execute(
                """INSERT INTO coc_meetings
                (id,case_id,meeting_number,meeting_type,coc_constitution_id,meeting_at,scheduled_start_at,scheduled_end_at,
                 mode,venue_or_link,notice_due_date,meeting_due_date,status,chair_name,created_by,updated_by,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (meeting_id, case_id, next_number, meeting_type, constitution["id"], scheduled_start, scheduled_start,
                 payload.get("scheduled_end_at"), mode, str(payload.get("venue") or payload.get("venue_or_link") or ""),
                 self._workflow_due_date(connection, case_id, "CIRP-046"), meeting_due, "DRAFT",
                 str(payload.get("chair_name") or ""), actor_id, actor_id, now, now),
            )
            self._ensure_task(connection, case_id, f"Prepare notice and agenda for CoC meeting {next_number}", meeting_due,
                              "coc_meeting", meeting_id, actor_id)
            self._ensure_task(connection, case_id, f"Record attendance and quorum for CoC meeting {next_number}", scheduled_start[:10] or meeting_due,
                              "coc_meeting", meeting_id, actor_id)
            self.store.audit(connection, actor_id, "COC_MEETING_CREATED", "coc_meeting", meeting_id, case_id,
                             after={"meeting_number": next_number, "meeting_type": meeting_type, "constitution_id": constitution["id"]},
                             title=f"CoC meeting {next_number} created")
        self._emit(case_id, "COC_MEETING_CREATED", actor_id, meeting_id, metadata={"meeting_number": next_number})
        return self.get_meeting(case_id, meeting_id)

    def _workflow_due_date(self, connection: sqlite3.Connection, case_id: str, step_code: str) -> Optional[str]:
        row = connection.execute(
            """SELECT cd.override_due_date,cd.calculated_due_date,cd.status FROM case_deadlines cd
            JOIN case_workflow_steps cws ON cws.id=cd.case_workflow_step_id
            JOIN workflow_step_definitions wsd ON wsd.id=cws.step_definition_id
            WHERE cd.case_id=? AND wsd.step_code=? ORDER BY cd.created_at DESC LIMIT 1""", (case_id, step_code)
        ).fetchone()
        if not row or row["status"] == "REVIEW_REQUIRED":
            return None
        return row["override_due_date"] or row["calculated_due_date"]

    def schedule_meeting(self, case_id: str, meeting_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        start = str(payload.get("scheduled_start_at") or "").strip()
        if not start:
            raise ValueError("Scheduled start time is required")
        mode = str(payload.get("mode") or "").upper()
        if mode and mode not in MEETING_MODES:
            raise ValueError("Meeting mode must be PHYSICAL, VIDEO_CONFERENCE or HYBRID")
        with self.store.transaction() as connection:
            meeting = self._meeting(connection, case_id, meeting_id)
            if meeting["status"] in {"COMPLETED", "CANCELLED"}:
                raise ValueError("A completed or cancelled meeting cannot be scheduled")
            connection.execute(
                """UPDATE coc_meetings SET meeting_at=?,scheduled_start_at=?,scheduled_end_at=COALESCE(?,scheduled_end_at),
                mode=COALESCE(NULLIF(?,''),mode),venue_or_link=COALESCE(NULLIF(?,''),venue_or_link),status='SCHEDULED',
                updated_by=?,updated_at=? WHERE id=? AND case_id=?""",
                (start, start, payload.get("scheduled_end_at"), mode, str(payload.get("venue") or payload.get("venue_or_link") or ""), actor_id, utc_now(), meeting_id, case_id),
            )
            self.store.audit(connection, actor_id, "COC_MEETING_SCHEDULED", "coc_meeting", meeting_id, case_id,
                             after={"scheduled_start_at": start, "mode": mode or meeting["mode"]}, title="CoC meeting scheduled")
        self._emit(case_id, "COC_MEETING_SCHEDULED", actor_id, meeting_id, start[:10])
        return self.get_meeting(case_id, meeting_id)

    def create_agenda_version(self, case_id: str, meeting_id: str, actor_id: str,
                              payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = payload or {}
        with self.store.transaction() as connection:
            self._meeting(connection, case_id, meeting_id)
            previous = connection.execute(
                "SELECT * FROM coc_agenda_versions WHERE case_id=? AND meeting_id=? ORDER BY version_number DESC LIMIT 1",
                (case_id, meeting_id),
            ).fetchone()
            version = int(previous["version_number"] + 1) if previous else 1
            agenda_id, now = new_id(), utc_now()
            connection.execute(
                """INSERT INTO coc_agenda_versions(id,case_id,meeting_id,version_number,status,agenda_kind,parent_agenda_version_id,
                created_by,created_at,updated_at) VALUES (?,?,?,?, 'DRAFT', ?,?,?,?,?)""",
                (agenda_id, case_id, meeting_id, version, str(payload.get("agenda_kind") or "STANDARD").upper(),
                 previous["id"] if previous else None, actor_id, now, now),
            )
            if payload.get("copy_previous") and previous:
                for item in self._agenda_rows(connection, case_id, meeting_id, previous["id"]):
                    self._insert_agenda_item(connection, case_id, meeting_id, agenda_id, item, actor_id)
            self.store.audit(connection, actor_id, "COC_AGENDA_VERSION_CREATED", "coc_agenda_version", agenda_id, case_id,
                             after={"version": version, "parent_id": previous["id"] if previous else None}, title=f"Agenda Version {version} created")
        return self.get_agenda_version(case_id, meeting_id, agenda_id)

    def _insert_agenda_item(self, connection: sqlite3.Connection, case_id: str, meeting_id: str, agenda_id: str,
                            payload: Dict[str, Any], actor_id: str) -> str:
        agenda_type = str(payload.get("agenda_type") or "FOR_DISCUSSION").upper()
        if agenda_type not in AGENDA_TYPES:
            raise ValueError("Unsupported agenda type")
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ValueError("Agenda item title is required")
        sequence = int(payload.get("sequence") or 0)
        if sequence < 1:
            sequence = int(connection.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM coc_agenda_version_items WHERE agenda_version_id=?", (agenda_id,)
            ).fetchone()[0])
        now, item_id = utc_now(), new_id()
        connection.execute(
            """INSERT INTO coc_agenda_version_items
            (id,case_id,meeting_id,agenda_version_id,agenda_number,sequence,agenda_type,title,agenda_note,
             proposed_resolution_text,requires_resolution,requires_voting,supporting_document_ids_json,status,
             created_by,updated_by,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (item_id, case_id, meeting_id, agenda_id, str(payload.get("agenda_number") or sequence), sequence, agenda_type,
             title, str(payload.get("agenda_note") or ""), str(payload.get("proposed_resolution_text") or ""),
             int(bool(payload.get("requires_resolution") or payload.get("proposed_resolution_text"))),
             int(bool(payload.get("requires_voting") or agenda_type == "FOR_VOTING")),
             _json(payload.get("supporting_document_ids") or []), "DRAFT", actor_id, actor_id, now, now),
        )
        return item_id

    def add_agenda_item(self, case_id: str, meeting_id: str, agenda_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            agenda = self._agenda_version(connection, case_id, meeting_id, agenda_id)
            if agenda["status"] != "DRAFT":
                raise ValueError("Frozen Agenda cannot be edited; create a new Agenda version")
            for doc_id in payload.get("supporting_document_ids") or []:
                if not connection.execute("SELECT 1 FROM documents WHERE id=? AND case_id=? AND archived_at IS NULL", (doc_id, case_id)).fetchone():
                    raise ValueError("Supporting document does not belong to this case")
            item_id = self._insert_agenda_item(connection, case_id, meeting_id, agenda_id, payload, actor_id)
            self.store.audit(connection, actor_id, "COC_AGENDA_ITEM_CREATED", "coc_agenda_item", item_id, case_id,
                             after={"agenda_version_id": agenda_id, "title": payload.get("title")}, title="CoC Agenda item added")
        return self.get_agenda_version(case_id, meeting_id, agenda_id)

    def update_agenda_item(self, case_id: str, meeting_id: str, agenda_id: str, item_id: str,
                           payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        allowed = {"agenda_number", "sequence", "agenda_type", "title", "agenda_note", "proposed_resolution_text",
                   "requires_resolution", "requires_voting", "supporting_document_ids", "status"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError("Unsupported Agenda item fields")
        with self.store.transaction() as connection:
            agenda = self._agenda_version(connection, case_id, meeting_id, agenda_id)
            if agenda["status"] != "DRAFT":
                raise ValueError("Frozen Agenda cannot be edited; create a new Agenda version")
            existing = connection.execute(
                "SELECT * FROM coc_agenda_version_items WHERE id=? AND case_id=? AND meeting_id=? AND agenda_version_id=?",
                (item_id, case_id, meeting_id, agenda_id),
            ).fetchone()
            if not existing:
                raise KeyError("Agenda item not found")
            values: Dict[str, Any] = {}
            for key in allowed:
                if key not in payload:
                    continue
                db_key = "supporting_document_ids_json" if key == "supporting_document_ids" else key
                values[db_key] = _json(payload[key]) if key == "supporting_document_ids" else payload[key]
            if "agenda_type" in values and str(values["agenda_type"]).upper() not in AGENDA_TYPES:
                raise ValueError("Unsupported agenda type")
            if "title" in values and not str(values["title"]).strip():
                raise ValueError("Agenda item title is required")
            if values:
                values.update({"updated_by": actor_id, "updated_at": utc_now()})
                connection.execute(
                    f"UPDATE coc_agenda_version_items SET {', '.join(f'{key}=?' for key in values)} WHERE id=?",
                    (*values.values(), item_id),
                )
                self.store.audit(connection, actor_id, "COC_AGENDA_ITEM_UPDATED", "coc_agenda_item", item_id, case_id,
                                 before=dict(existing), after=payload, title="CoC Agenda item updated")
        return self.get_agenda_version(case_id, meeting_id, agenda_id)

    def get_agenda_version(self, case_id: str, meeting_id: str, agenda_id: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            agenda = self._agenda_version(connection, case_id, meeting_id, agenda_id)
            result = dict(agenda)
            result["items"] = self._agenda_rows(connection, case_id, meeting_id, agenda_id)
            return result

    def list_agenda_versions(self, case_id: str, meeting_id: str,
                             connection: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
        owns_connection = connection is None
        if owns_connection:
            connection = self.store.connect()
        assert connection is not None
        try:
            rows = connection.execute(
                "SELECT * FROM coc_agenda_versions WHERE case_id=? AND meeting_id=? ORDER BY version_number", (case_id, meeting_id)
            ).fetchall()
            return [dict(row) | {"items": self._agenda_rows(connection, case_id, meeting_id, row["id"])} for row in rows]
        finally:
            if owns_connection:
                connection.close()

    def finalize_agenda(self, case_id: str, meeting_id: str, agenda_id: str, actor_id: str) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            agenda = self._agenda_version(connection, case_id, meeting_id, agenda_id)
            if agenda["status"] == "FINAL":
                return self.get_agenda_version(case_id, meeting_id, agenda_id)
            if agenda["status"] != "DRAFT":
                raise ValueError("Only a draft Agenda may be finalized")
            if not self._agenda_rows(connection, case_id, meeting_id, agenda_id):
                raise ValueError("Add at least one Agenda item before finalizing")
            now = utc_now()
            connection.execute("UPDATE coc_agenda_versions SET status='FINAL',frozen_at=?,frozen_by=?,updated_at=? WHERE id=?",
                               (now, actor_id, now, agenda_id))
            self.store.audit(connection, actor_id, "COC_AGENDA_FINALIZED", "coc_agenda_version", agenda_id, case_id,
                             after={"meeting_id": meeting_id}, title="CoC Agenda finalized")
        self._emit(case_id, "COC_AGENDA_FINALIZED", actor_id, agenda_id)
        return self.get_agenda_version(case_id, meeting_id, agenda_id)

    def member_snapshot(self, case_id: str, meeting_id: str, connection: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
        owns_connection = connection is None
        if owns_connection:
            connection = self.store.connect()
        assert connection is not None
        try:
            return [dict(row) | {"admitted_debt": from_paise(row["admitted_debt_paise"])} for row in connection.execute(
                "SELECT * FROM coc_meeting_member_snapshots WHERE case_id=? AND meeting_id=? ORDER BY voting_share_units DESC, creditor_name",
                (case_id, meeting_id),
            ).fetchall()]
        finally:
            if owns_connection:
                connection.close()

    def _freeze_members(self, connection: sqlite3.Connection, case_id: str, meeting_id: str,
                        constitution_id: str, actor_id: str) -> List[Dict[str, Any]]:
        existing = self.member_snapshot(case_id, meeting_id, connection)
        if existing:
            return existing
        rows = connection.execute(
            """SELECT cm.*,m.id AS operational_member_id,m.authorized_representative,c.email,c.address
            FROM coc_constitution_members cm
            LEFT JOIN coc_members m ON m.case_id=cm.case_id AND m.claim_id=cm.claim_id AND m.valid_to IS NULL AND m.archived_at IS NULL
            LEFT JOIN claims c ON c.id=cm.claim_id AND c.case_id=cm.case_id
            WHERE cm.case_id=? AND cm.constitution_id=? ORDER BY cm.creditor_name""", (case_id, constitution_id)
        ).fetchall()
        if not rows:
            raise ValueError("Confirmed CoC Constitution has no members")
        now = utc_now()
        for row in rows:
            units = _share_units(row["display_voting_percentage"])
            connection.execute(
                """INSERT INTO coc_meeting_member_snapshots
                (id,case_id,meeting_id,coc_constitution_id,operational_member_id,claim_id,creditor_name,representative_name,
                 recipient_email,recipient_address,recipient_category,admitted_debt_paise,voting_share_units,voting_share_text,
                 participation_rights,voting_rights,frozen_at,frozen_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (new_id(), case_id, meeting_id, constitution_id, row["operational_member_id"], row["claim_id"], row["creditor_name"],
                 row["authorized_representative"] or "", row["email"] or "", row["address"] or "", "COC_MEMBER",
                 row["admitted_debt_paise"], units, _share_text(units), 1, 1, now, actor_id),
            )
        self.store.audit(connection, actor_id, "COC_MEMBER_SNAPSHOT_CREATED", "coc_meeting", meeting_id, case_id,
                         after={"constitution_id": constitution_id, "member_count": len(rows)}, title="CoC meeting recipient snapshot frozen")
        return self.member_snapshot(case_id, meeting_id, connection)

    def create_notice_draft(self, case_id: str, meeting_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            meeting = self._meeting(connection, case_id, meeting_id)
            if meeting["status"] in {"COMPLETED", "CANCELLED"}:
                raise ValueError("Cannot create a Notice for a completed or cancelled meeting")
            agenda = self._agenda_version(connection, case_id, meeting_id, payload.get("agenda_version_id"))
            constitution = self._constitution(connection, case_id, meeting["coc_constitution_id"])
            existing = connection.execute(
                """SELECT * FROM coc_notice_versions WHERE case_id=? AND meeting_id=? AND status='DRAFT'
                AND agenda_version_id=? ORDER BY version_number DESC LIMIT 1""", (case_id, meeting_id, agenda["id"])
            ).fetchone()
            if existing:
                return dict(existing) | {"idempotent_replay": True}
            version = int(connection.execute(
                "SELECT COALESCE(MAX(version_number),0)+1 FROM coc_notice_versions WHERE case_id=? AND meeting_id=?", (case_id, meeting_id)
            ).fetchone()[0])
            notice_id, now = new_id(), utc_now()
            snapshot = {"meeting": dict(meeting), "agenda": self._agenda_rows(connection, case_id, meeting_id, agenda["id"]),
                        "constitution_id": constitution["id"], "created_at": now}
            connection.execute(
                """INSERT INTO coc_notice_versions(id,case_id,meeting_id,version_number,status,coc_constitution_id,agenda_version_id,
                snapshot_json,created_by,created_at,updated_at) VALUES (?,?,?,?, 'DRAFT',?,?,?,?,?,?)""",
                (notice_id, case_id, meeting_id, version, constitution["id"], agenda["id"], _json(snapshot), actor_id, now, now),
            )
            connection.execute("UPDATE coc_meetings SET status='SCHEDULED',updated_by=?,updated_at=? WHERE id=?", (actor_id, now, meeting_id))
            self.store.audit(connection, actor_id, "COC_NOTICE_DRAFTED", "coc_notice_version", notice_id, case_id,
                             after={"version": version, "agenda_version_id": agenda["id"]}, title=f"CoC Notice Draft V{version} created")
        self._emit(case_id, "COC_NOTICE_DRAFTED", actor_id, notice_id)
        return self.get_notice_version(case_id, meeting_id, notice_id)

    def get_notice_version(self, case_id: str, meeting_id: str, notice_id: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM coc_notice_versions WHERE id=? AND case_id=? AND meeting_id=?", (notice_id, case_id, meeting_id)).fetchone()
            if not row:
                raise KeyError("CoC Notice version not found")
            return dict(row) | {"snapshot": _from_json(row["snapshot_json"], {})}

    def link_notice_document(self, case_id: str, meeting_id: str, notice_id: str, document_id: str, actor_id: str) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            notice = connection.execute("SELECT * FROM coc_notice_versions WHERE id=? AND case_id=? AND meeting_id=?", (notice_id, case_id, meeting_id)).fetchone()
            if not notice:
                raise KeyError("CoC Notice version not found")
            if notice["document_id"]:
                return self.get_notice_version(case_id, meeting_id, notice_id)
            if not connection.execute("SELECT 1 FROM documents WHERE id=? AND case_id=? AND archived_at IS NULL", (document_id, case_id)).fetchone():
                raise ValueError("Notice document does not belong to this case")
            connection.execute("UPDATE coc_notice_versions SET document_id=?,updated_at=? WHERE id=?", (document_id, utc_now(), notice_id))
            self.store.audit(connection, actor_id, "COC_NOTICE_DOCUMENT_GENERATED", "coc_notice_version", notice_id, case_id,
                             after={"document_id": document_id}, title="CoC Notice DOCX generated")
        return self.get_notice_version(case_id, meeting_id, notice_id)

    def approve_notice(self, case_id: str, meeting_id: str, notice_id: str, actor_id: str) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            notice = connection.execute("SELECT * FROM coc_notice_versions WHERE id=? AND case_id=? AND meeting_id=?", (notice_id, case_id, meeting_id)).fetchone()
            if not notice:
                raise KeyError("CoC Notice version not found")
            if notice["status"] == "ISSUED":
                raise ValueError("Issued Notice cannot be re-approved")
            if not notice["document_id"]:
                raise ValueError("Generate the Notice DOCX before approval")
            now = utc_now()
            connection.execute("UPDATE coc_notice_versions SET status='APPROVED',approved_by=?,approved_at=?,updated_at=? WHERE id=?", (actor_id, now, now, notice_id))
            self.store.audit(connection, actor_id, "COC_NOTICE_APPROVED", "coc_notice_version", notice_id, case_id, after={"document_id": notice["document_id"]}, title="CoC Notice approved")
        return self.get_notice_version(case_id, meeting_id, notice_id)

    def issue_notice(self, case_id: str, meeting_id: str, notice_id: str, actor_id: str) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            meeting = self._meeting(connection, case_id, meeting_id)
            notice = connection.execute("SELECT * FROM coc_notice_versions WHERE id=? AND case_id=? AND meeting_id=?", (notice_id, case_id, meeting_id)).fetchone()
            if not notice:
                raise KeyError("CoC Notice version not found")
            if notice["status"] == "ISSUED":
                return self.get_notice_version(case_id, meeting_id, notice_id)
            if notice["status"] != "APPROVED":
                raise ValueError("Only an approved Notice with an attached DOCX can be issued")
            agenda = self._agenda_version(connection, case_id, meeting_id, notice["agenda_version_id"])
            if agenda["status"] == "DRAFT":
                if not self._agenda_rows(connection, case_id, meeting_id, agenda["id"]):
                    raise ValueError("Add Agenda items before issuing Notice")
                now = utc_now()
                connection.execute("UPDATE coc_agenda_versions SET status='FINAL',frozen_at=?,frozen_by=?,updated_at=? WHERE id=?",
                                   (now, actor_id, now, agenda["id"]))
            elif agenda["status"] != "FINAL":
                raise ValueError("Issued Notice requires a final Agenda")
            members = self._freeze_members(connection, case_id, meeting_id, notice["coc_constitution_id"], actor_id)
            now = utc_now()
            snapshot = {"issued_at": now, "meeting": dict(meeting), "agenda": self._agenda_rows(connection, case_id, meeting_id, agenda["id"]),
                        "members": members, "constitution_id": notice["coc_constitution_id"], "agenda_version_id": agenda["id"]}
            connection.execute(
                """UPDATE coc_notice_versions SET status='ISSUED',issued_by=?,issued_at=?,snapshot_json=?,updated_at=? WHERE id=?""",
                (actor_id, now, _json(snapshot), now, notice_id),
            )
            connection.execute(
                """UPDATE coc_meetings SET status='NOTICE_ISSUED',notice_snapshot_json=?,notice_date=COALESCE(notice_date,substr(?,1,10)),
                updated_by=?,updated_at=? WHERE id=?""", (_json(snapshot), now, actor_id, now, meeting_id)
            )
            self.store.audit(connection, actor_id, "COC_NOTICE_ISSUED", "coc_notice_version", notice_id, case_id,
                             after={"agenda_version_id": agenda["id"], "member_count": len(members)}, title="CoC Notice issued")
        self._emit(case_id, "COC_AGENDA_FINALIZED", actor_id, notice["agenda_version_id"])
        self._emit(case_id, "COC_NOTICE_ISSUED", actor_id, notice_id)
        return self.get_notice_version(case_id, meeting_id, notice_id)

    def record_notice_dispatch(self, case_id: str, meeting_id: str, notice_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        status = str(payload.get("status") or "NOT_SENT").upper()
        if status not in {"NOT_SENT", "SENT_MANUALLY", "DELIVERED_CONFIRMED", "FAILED", "RETURNED"}:
            raise ValueError("Unsupported dispatch status")
        recipient = str(payload.get("recipient_name") or "").strip()
        if not recipient:
            raise ValueError("Dispatch recipient name is required")
        with self.store.transaction() as connection:
            if not connection.execute("SELECT 1 FROM coc_notice_versions WHERE id=? AND case_id=? AND meeting_id=? AND status='ISSUED'", (notice_id, case_id, meeting_id)).fetchone():
                raise ValueError("Dispatch can only be recorded for an issued Notice")
            proof = payload.get("service_proof_document_id")
            if proof and not connection.execute("SELECT 1 FROM documents WHERE id=? AND case_id=?", (proof, case_id)).fetchone():
                raise ValueError("Service proof does not belong to this case")
            snapshot_id = payload.get("meeting_member_snapshot_id")
            if snapshot_id and not connection.execute("SELECT 1 FROM coc_meeting_member_snapshots WHERE id=? AND case_id=? AND meeting_id=?", (snapshot_id, case_id, meeting_id)).fetchone():
                raise ValueError("Recipient snapshot does not belong to this meeting")
            now, dispatch_id = utc_now(), new_id()
            connection.execute(
                """INSERT INTO coc_notice_dispatches(id,case_id,meeting_id,notice_version_id,meeting_member_snapshot_id,recipient_name,
                recipient_address,dispatch_method,dispatch_datetime,status,service_proof_document_id,remarks,recorded_by,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (dispatch_id, case_id, meeting_id, notice_id, snapshot_id, recipient, str(payload.get("recipient_address") or ""),
                 str(payload.get("dispatch_method") or ""), payload.get("dispatch_datetime"), status, proof,
                 str(payload.get("remarks") or ""), actor_id, now, now),
            )
            self.store.audit(connection, actor_id, "COC_NOTICE_DISPATCH_RECORDED", "coc_notice_dispatch", dispatch_id, case_id,
                             after={"notice_id": notice_id, "recipient": recipient, "status": status}, title="CoC Notice dispatch recorded")
        self._emit(case_id, "COC_NOTICE_DISPATCH_RECORDED", actor_id, dispatch_id)
        return {"id": dispatch_id, "status": status}

    def upsert_quorum_rule(self, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        code = str(payload.get("rule_code") or "").strip()
        if not code:
            raise ValueError("Quorum rule code is required")
        minimum = _share_units(payload.get("minimum_voting_share"))
        effective = _day(payload.get("effective_from"), "Quorum rule effective date")
        version = int(payload.get("rule_version") or 1)
        with self.store.transaction() as connection:
            existing = connection.execute("SELECT * FROM coc_quorum_rules WHERE rule_code=? AND rule_version=?", (code, version)).fetchone()
            if existing:
                return dict(existing)
            rule_id, now = new_id(), utc_now()
            connection.execute(
                """INSERT INTO coc_quorum_rules(id,rule_code,process_type,effective_from,effective_to,minimum_voting_share_units,
                rule_reference,rule_version,status,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (rule_id, code, str(payload.get("process_type") or "CIRP"), effective, payload.get("effective_to"), minimum,
                 str(payload.get("rule_reference") or ""), version, str(payload.get("status") or "REVIEW_REQUIRED").upper(), actor_id, now),
            )
            return dict(connection.execute("SELECT * FROM coc_quorum_rules WHERE id=?", (rule_id,)).fetchone())

    def _quorum_rule(self, connection: sqlite3.Connection, rule_id: Optional[str], meeting_date: str) -> Optional[sqlite3.Row]:
        if rule_id:
            return connection.execute("SELECT * FROM coc_quorum_rules WHERE id=?", (rule_id,)).fetchone()
        return connection.execute(
            """SELECT * FROM coc_quorum_rules WHERE process_type='CIRP' AND status='CONFIRMED' AND effective_from<=?
            AND (effective_to IS NULL OR effective_to>=?) ORDER BY rule_version DESC LIMIT 1""", (meeting_date[:10], meeting_date[:10])
        ).fetchone()

    def record_attendance(self, case_id: str, meeting_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        role = str(payload.get("participant_role") or "OTHER").upper()
        allowed_roles = {"COC_MEMBER_REPRESENTATIVE", "AUTHORISED_REPRESENTATIVE", "IRP", "RP", "SUSPENDED_DIRECTOR",
                         "OPERATIONAL_CREDITOR_REPRESENTATIVE", "LEGAL_COUNSEL", "VALUER", "PROFESSIONAL", "INVITEE", "OTHER"}
        if role not in allowed_roles:
            raise ValueError("Unsupported participant role")
        authorization = str(payload.get("authorization_status") or "NOT_APPLICABLE").upper()
        if authorization not in {"VALID", "REVIEW_REQUIRED", "NOT_APPLICABLE"}:
            raise ValueError("Invalid representative authorization status")
        name = str(payload.get("participant_name") or "").strip()
        if not name:
            raise ValueError("Participant name is required")
        with self.store.transaction() as connection:
            self._meeting(connection, case_id, meeting_id)
            snapshot_id = payload.get("meeting_member_snapshot_id")
            snapshot = None
            if snapshot_id:
                snapshot = connection.execute(
                    "SELECT * FROM coc_meeting_member_snapshots WHERE id=? AND case_id=? AND meeting_id=?", (snapshot_id, case_id, meeting_id)
                ).fetchone()
                if not snapshot:
                    raise ValueError("Meeting member snapshot does not belong to this meeting")
            voting_entitled = bool(payload.get("voting_entitled", bool(snapshot and snapshot["voting_rights"])))
            units = int(snapshot["voting_share_units"]) if snapshot and voting_entitled else 0
            if voting_entitled and not snapshot:
                raise ValueError("Voting attendance must reference the frozen Meeting member snapshot")
            evidence = payload.get("authorization_document_id")
            if evidence and not connection.execute("SELECT 1 FROM documents WHERE id=? AND case_id=?", (evidence, case_id)).fetchone():
                raise ValueError("Representative authorization evidence does not belong to this case")
            now, attendance_id = utc_now(), new_id()
            connection.execute(
                """INSERT INTO coc_attendance(id,case_id,meeting_id,member_id,meeting_member_snapshot_id,participant_name,organization,capacity,
                participant_role,email,present,attendance_mode,authorization_status,authorization_document_id,voting_entitled,joined_at,left_at,
                voting_share_snapshot,voting_share_units,notes,created_by,updated_by,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (attendance_id, case_id, meeting_id, snapshot["operational_member_id"] if snapshot else None, snapshot_id, name,
                 str(payload.get("organization") or (snapshot["creditor_name"] if snapshot else "")), str(payload.get("capacity") or ""), role,
                 str(payload.get("email") or (snapshot["recipient_email"] if snapshot else "")), int(bool(payload.get("present", True))),
                 str(payload.get("attendance_mode") or ""), authorization, evidence, int(voting_entitled), payload.get("join_time"), payload.get("leave_time"),
                 float(Decimal(units) / SHARE_SCALE), units, str(payload.get("remarks") or payload.get("notes") or ""), actor_id, actor_id, now, now),
            )
            self.store.audit(connection, actor_id, "COC_ATTENDANCE_RECORDED", "coc_attendance", attendance_id, case_id,
                             after={"participant_role": role, "voting_entitled": voting_entitled, "voting_share_units": units}, title="CoC attendance recorded")
        self._emit(case_id, "COC_ATTENDANCE_RECORDED", actor_id, attendance_id)
        return {"id": attendance_id, "voting_share": _share_text(units), "voting_entitled": voting_entitled}

    def latest_quorum(self, case_id: str, meeting_id: str, connection: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
        owns_connection = connection is None
        if owns_connection:
            connection = self.store.connect()
        assert connection is not None
        try:
            row = connection.execute(
                "SELECT * FROM coc_meeting_quorum_records WHERE case_id=? AND meeting_id=? ORDER BY calculated_at DESC LIMIT 1", (case_id, meeting_id)
            ).fetchone()
            if not row:
                return None
            return dict(row) | {"required_voting_share": _share_text(row["required_voting_share_units"]),
                                "present_voting_share": _share_text(row["present_voting_share_units"]), "quorum_met": bool(row["quorum_met"]) if row["quorum_met"] is not None else None}
        finally:
            if owns_connection:
                connection.close()

    def calculate_quorum(self, case_id: str, meeting_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            meeting = self._meeting(connection, case_id, meeting_id)
            rule = self._quorum_rule(connection, payload.get("quorum_rule_id"), meeting["scheduled_start_at"] or meeting["meeting_at"] or date.today().isoformat())
            attendance = connection.execute(
                """SELECT * FROM coc_attendance WHERE case_id=? AND meeting_id=? AND archived_at IS NULL
                AND present=1 AND voting_entitled=1""", (case_id, meeting_id)
            ).fetchall()
            present = sum(int(row["voting_share_units"] or 0) for row in attendance)
            now, record_id = utc_now(), new_id()
            if not rule or rule["status"] != "CONFIRMED":
                status, met, required = "REVIEW_REQUIRED", None, 0
            else:
                required, met, status = int(rule["minimum_voting_share_units"]), present >= int(rule["minimum_voting_share_units"]), "MET" if present >= int(rule["minimum_voting_share_units"]) else "NO_QUORUM"
            connection.execute(
                """INSERT INTO coc_meeting_quorum_records(id,case_id,meeting_id,quorum_rule_id,required_voting_share_units,
                present_voting_share_units,quorum_met,status,constitution_id,calculated_at,calculated_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (record_id, case_id, meeting_id, rule["id"] if rule else None, required, present, int(met) if met is not None else None,
                 status, meeting["coc_constitution_id"], now, actor_id),
            )
            connection.execute("UPDATE coc_meetings SET status=?,updated_by=?,updated_at=? WHERE id=?",
                               ("READY" if met else "ADJOURNED" if status == "NO_QUORUM" else meeting["status"], actor_id, now, meeting_id))
            self.store.audit(connection, actor_id, "COC_QUORUM_CALCULATED", "coc_meeting_quorum", record_id, case_id,
                             after={"status": status, "present_voting_share": _share_text(present), "required_voting_share": _share_text(required)}, title="CoC quorum calculated")
        if status == "MET":
            self._emit(case_id, "COC_QUORUM_CONFIRMED", actor_id, record_id)
        elif status == "NO_QUORUM":
            self._emit(case_id, "COC_QUORUM_NOT_MET", actor_id, record_id)
        return self.latest_quorum(case_id, meeting_id) or {}

    def start_meeting(self, case_id: str, meeting_id: str, actor_id: str, actual_start_at: Optional[str] = None) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            meeting = self._meeting(connection, case_id, meeting_id)
            if meeting["status"] not in {"NOTICE_ISSUED", "READY", "SCHEDULED"}:
                raise ValueError("Issue Notice and prepare the Meeting before starting it")
            now = actual_start_at or utc_now()
            connection.execute("UPDATE coc_meetings SET actual_start_at=?,status='IN_PROGRESS',updated_by=?,updated_at=? WHERE id=?",
                               (now, actor_id, utc_now(), meeting_id))
            self.store.audit(connection, actor_id, "COC_MEETING_STARTED", "coc_meeting", meeting_id, case_id, after={"actual_start_at": now}, title="CoC meeting started")
        self._emit(case_id, "COC_MEETING_STARTED", actor_id, meeting_id, now[:10])
        return self.get_meeting(case_id, meeting_id)

    def adjourn_meeting(self, case_id: str, meeting_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            raise ValueError("Adjournment reason is required")
        with self.store.transaction() as connection:
            self._meeting(connection, case_id, meeting_id)
            now = utc_now()
            connection.execute("UPDATE coc_meetings SET status='ADJOURNED',updated_by=?,updated_at=? WHERE id=?", (actor_id, now, meeting_id))
            connection.execute(
                """INSERT INTO coc_meeting_adjournments(id,case_id,meeting_id,adjourned_at,reason,next_scheduled_start_at,recorded_by,created_at)
                VALUES (?,?,?,?,?,?,?,?)""", (new_id(), case_id, meeting_id, now, reason, payload.get("next_scheduled_start_at"), actor_id, now)
            )
            self.store.audit(connection, actor_id, "COC_MEETING_ADJOURNED", "coc_meeting", meeting_id, case_id, after={"reason": reason}, title="CoC meeting adjourned")
        self._emit(case_id, "COC_MEETING_ADJOURNED", actor_id, meeting_id)
        return self.get_meeting(case_id, meeting_id)

    def create_resolution(self, case_id: str, meeting_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        agenda_item_id = str(payload.get("agenda_item_id") or "")
        title = str(payload.get("title") or "").strip()
        if not agenda_item_id or not title:
            raise ValueError("Resolution Agenda item and title are required")
        with self.store.transaction() as connection:
            item = connection.execute("SELECT * FROM coc_agenda_version_items WHERE id=? AND case_id=? AND meeting_id=?", (agenda_item_id, case_id, meeting_id)).fetchone()
            if not item:
                raise ValueError("Resolution Agenda item does not belong to this meeting")
            number = str(payload.get("resolution_number") or int(connection.execute(
                "SELECT COUNT(*)+1 FROM coc_resolutions WHERE case_id=? AND meeting_id=?", (case_id, meeting_id)
            ).fetchone()[0]))
            rule_id = payload.get("approval_rule_id")
            if rule_id and not connection.execute("SELECT 1 FROM coc_approval_rules WHERE id=?", (rule_id,)).fetchone():
                raise ValueError("Approval rule not found")
            now, resolution_id = utc_now(), new_id()
            connection.execute(
                """INSERT INTO coc_resolutions(id,case_id,meeting_id,agenda_item_id,resolution_number,title,proposed_resolution_text,
                final_resolution_text,resolution_category,voting_required,approval_rule_id,status,created_by,updated_by,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (resolution_id, case_id, meeting_id, agenda_item_id, number, title,
                 str(payload.get("proposed_resolution_text") or item["proposed_resolution_text"] or ""),
                 str(payload.get("final_resolution_text") or ""), str(payload.get("resolution_category") or "OTHER_APPLICABLE_DECISION"),
                 int(bool(payload.get("voting_required") or item["requires_voting"])), rule_id, "DRAFT", actor_id, actor_id, now, now),
            )
            self.store.audit(connection, actor_id, "COC_RESOLUTION_CREATED", "coc_resolution", resolution_id, case_id, after={"title": title}, title="CoC Resolution created")
        return self.get_resolution(case_id, meeting_id, resolution_id)

    def get_resolution(self, case_id: str, meeting_id: str, resolution_id: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM coc_resolutions WHERE id=? AND case_id=? AND meeting_id=?", (resolution_id, case_id, meeting_id)).fetchone()
            if not row:
                raise KeyError("CoC Resolution not found")
            return dict(row)

    def update_resolution(self, case_id: str, meeting_id: str, resolution_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        allowed = {"title", "final_resolution_text", "status", "approval_rule_id"}
        if set(payload) - allowed:
            raise ValueError("Unsupported Resolution fields")
        with self.store.transaction() as connection:
            row = connection.execute("SELECT * FROM coc_resolutions WHERE id=? AND case_id=? AND meeting_id=?", (resolution_id, case_id, meeting_id)).fetchone()
            if not row:
                raise KeyError("CoC Resolution not found")
            if row["status"] in {"APPROVED", "REJECTED", "WITHDRAWN"}:
                raise ValueError("Final Resolution cannot be changed without a controlled correction")
            values = {key: payload[key] for key in allowed if key in payload}
            if "approval_rule_id" in values and values["approval_rule_id"] and not connection.execute("SELECT 1 FROM coc_approval_rules WHERE id=?", (values["approval_rule_id"],)).fetchone():
                raise ValueError("Approval rule not found")
            if values:
                values.update({"updated_by": actor_id, "updated_at": utc_now()})
                connection.execute(f"UPDATE coc_resolutions SET {', '.join(f'{key}=?' for key in values)} WHERE id=?", (*values.values(), resolution_id))
                self.store.audit(connection, actor_id, "COC_RESOLUTION_WORDING_UPDATED", "coc_resolution", resolution_id, case_id,
                                 before=dict(row), after=payload, title="CoC Resolution updated")
        return self.get_resolution(case_id, meeting_id, resolution_id)

    def place_resolution(self, case_id: str, meeting_id: str, resolution_id: str, actor_id: str) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            row = connection.execute("SELECT * FROM coc_resolutions WHERE id=? AND case_id=? AND meeting_id=?", (resolution_id, case_id, meeting_id)).fetchone()
            if not row:
                raise KeyError("CoC Resolution not found")
            connection.execute("UPDATE coc_resolutions SET status='PLACED',updated_by=?,updated_at=? WHERE id=?", (actor_id, utc_now(), resolution_id))
        self._emit(case_id, "COC_RESOLUTION_PLACED", actor_id, resolution_id)
        return self.get_resolution(case_id, meeting_id, resolution_id)

    def upsert_approval_rule(self, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        code = str(payload.get("rule_code") or "").strip()
        if not code:
            raise ValueError("Approval rule code is required")
        threshold = _share_units(payload.get("minimum_for_voting_share"))
        effective = _day(payload.get("effective_from"), "Approval rule effective date")
        version = int(payload.get("rule_version") or 1)
        with self.store.transaction() as connection:
            existing = connection.execute("SELECT * FROM coc_approval_rules WHERE rule_code=? AND rule_version=?", (code, version)).fetchone()
            if existing:
                return dict(existing)
            rule_id, now = new_id(), utc_now()
            connection.execute(
                """INSERT INTO coc_approval_rules(id,rule_code,process_type,effective_from,effective_to,minimum_for_share_units,
                rule_reference,rule_version,status,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (rule_id, code, str(payload.get("process_type") or "CIRP"), effective, payload.get("effective_to"), threshold,
                 str(payload.get("rule_reference") or ""), version, str(payload.get("status") or "REVIEW_REQUIRED").upper(), actor_id, now),
            )
            return dict(connection.execute("SELECT * FROM coc_approval_rules WHERE id=?", (rule_id,)).fetchone())

    def create_voting_session(self, case_id: str, meeting_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        resolution_ids = list(dict.fromkeys(payload.get("resolution_ids") or []))
        if not resolution_ids:
            raise ValueError("At least one Resolution is required for a voting session")
        with self.store.transaction() as connection:
            self._meeting(connection, case_id, meeting_id)
            snapshots = self.member_snapshot(case_id, meeting_id, connection)
            if not snapshots:
                raise ValueError("Issue Notice and freeze the Meeting member snapshot before voting")
            rows = connection.execute(
                f"SELECT id FROM coc_resolutions WHERE case_id=? AND meeting_id=? AND id IN ({','.join('?' for _ in resolution_ids)})",
                (case_id, meeting_id, *resolution_ids),
            ).fetchall()
            if len(rows) != len(resolution_ids):
                raise ValueError("Every voting Resolution must belong to this meeting")
            session_id, now = new_id(), utc_now()
            connection.execute(
                """INSERT INTO coc_voting_sessions(id,case_id,meeting_id,status,scheduled_close_at,communication_document_id,dispatch_status,
                eligible_voter_snapshot_json,created_by,created_at,updated_at) VALUES (?,?,?,'DRAFT',?,?,?,?,?,?,?)""",
                (session_id, case_id, meeting_id, payload.get("scheduled_close_at"), payload.get("communication_document_id"),
                 str(payload.get("dispatch_status") or "NOT_SENT"), _json(snapshots), actor_id, now, now),
            )
            for resolution_id in resolution_ids:
                connection.execute("INSERT INTO coc_voting_session_resolutions(voting_session_id,resolution_id,case_id) VALUES (?,?,?)", (session_id, resolution_id, case_id))
        return self.get_voting_session(case_id, meeting_id, session_id)

    def get_voting_session(self, case_id: str, meeting_id: str, session_id: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM coc_voting_sessions WHERE id=? AND case_id=? AND meeting_id=?", (session_id, case_id, meeting_id)).fetchone()
            if not row:
                raise KeyError("Voting session not found")
            result = dict(row)
            result["eligible_voter_snapshot"] = _from_json(row["eligible_voter_snapshot_json"], [])
            result["resolution_ids"] = [item["resolution_id"] for item in connection.execute("SELECT resolution_id FROM coc_voting_session_resolutions WHERE voting_session_id=?", (session_id,)).fetchall()]
            return result

    def open_voting(self, case_id: str, meeting_id: str, session_id: str, actor_id: str) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            row = connection.execute("SELECT * FROM coc_voting_sessions WHERE id=? AND case_id=? AND meeting_id=?", (session_id, case_id, meeting_id)).fetchone()
            if not row:
                raise KeyError("Voting session not found")
            if row["status"] == "OPEN":
                return self.get_voting_session(case_id, meeting_id, session_id)
            if row["status"] != "DRAFT":
                raise ValueError("Only a draft voting session can be opened")
            now = utc_now()
            connection.execute("UPDATE coc_voting_sessions SET status='OPEN',opened_at=?,updated_at=? WHERE id=?", (now, now, session_id))
        self._emit(case_id, "COC_VOTING_OPENED", actor_id, session_id)
        return self.get_voting_session(case_id, meeting_id, session_id)

    def record_vote(self, case_id: str, meeting_id: str, session_id: str, resolution_id: str,
                    payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        vote = str(payload.get("vote") or "").upper()
        if vote not in VOTES - {"NOT_VOTED"}:
            raise ValueError("Vote must be FOR, AGAINST or ABSTAIN")
        snapshot_id = str(payload.get("meeting_member_snapshot_id") or "")
        with self.store.transaction() as connection:
            session = connection.execute("SELECT * FROM coc_voting_sessions WHERE id=? AND case_id=? AND meeting_id=?", (session_id, case_id, meeting_id)).fetchone()
            if not session or session["status"] != "OPEN":
                raise ValueError("Votes can only be recorded while the voting session is OPEN")
            if not connection.execute("SELECT 1 FROM coc_voting_session_resolutions WHERE voting_session_id=? AND resolution_id=? AND case_id=?", (session_id, resolution_id, case_id)).fetchone():
                raise ValueError("Resolution is not part of this voting session")
            member = connection.execute("SELECT * FROM coc_meeting_member_snapshots WHERE id=? AND case_id=? AND meeting_id=? AND voting_rights=1", (snapshot_id, case_id, meeting_id)).fetchone()
            if not member:
                raise ValueError("Vote must use an eligible frozen Meeting member snapshot")
            if not member["operational_member_id"]:
                raise ValueError("Frozen member has no operational CoC member reference")
            existing = connection.execute("SELECT * FROM coc_votes WHERE meeting_id=? AND member_id=? AND agenda_key=?", (meeting_id, member["operational_member_id"], resolution_id)).fetchone()
            now = utc_now()
            if existing:
                if existing["vote"] == vote and int(existing["voting_share_units"] or 0) == int(member["voting_share_units"]):
                    return dict(existing) | {"idempotent_replay": True}
                raise ValueError("Use the controlled vote-correction workflow after a vote is recorded")
            vote_id = new_id()
            connection.execute(
                """INSERT INTO coc_votes(id,case_id,meeting_id,member_id,meeting_member_snapshot_id,resolution_id,agenda_key,vote,voting_share,
                voting_share_units,method,source,remarks,cast_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (vote_id, case_id, meeting_id, member["operational_member_id"], snapshot_id, resolution_id, resolution_id, vote,
                 float(Decimal(member["voting_share_units"]) / SHARE_SCALE), member["voting_share_units"], str(payload.get("method") or "E_VOTING"),
                 str(payload.get("source") or "MANUAL"), str(payload.get("remarks") or ""), payload.get("cast_at") or now, now, now),
            )
            self.store.audit(connection, actor_id, "COC_VOTE_RECORDED", "coc_vote", vote_id, case_id,
                             after={"resolution_id": resolution_id, "vote": vote, "voting_share": _share_text(member["voting_share_units"])}, title="CoC vote recorded")
        self._emit(case_id, "COC_VOTE_RECORDED", actor_id, vote_id)
        return {"id": vote_id, "vote": vote, "voting_share": _share_text(member["voting_share_units"])}

    def close_voting(self, case_id: str, meeting_id: str, session_id: str, actor_id: str) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            session = connection.execute("SELECT * FROM coc_voting_sessions WHERE id=? AND case_id=? AND meeting_id=?", (session_id, case_id, meeting_id)).fetchone()
            if not session:
                raise KeyError("Voting session not found")
            if session["status"] == "CLOSED":
                return self.get_voting_session(case_id, meeting_id, session_id)
            if session["status"] != "OPEN":
                raise ValueError("Only an open voting session can be closed")
            now = utc_now()
            connection.execute("UPDATE coc_voting_sessions SET status='CLOSED',actual_close_at=?,updated_at=? WHERE id=?", (now, now, session_id))
        self._emit(case_id, "COC_VOTING_CLOSED", actor_id, session_id)
        return self.get_voting_session(case_id, meeting_id, session_id)

    def correct_vote(self, case_id: str, meeting_id: str, vote_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        new_vote, reason = str(payload.get("new_vote") or "").upper(), str(payload.get("reason") or "").strip()
        if new_vote not in VOTES - {"NOT_VOTED"} or not reason:
            raise ValueError("Vote correction requires FOR/AGAINST/ABSTAIN and a reason")
        with self.store.transaction() as connection:
            vote = connection.execute("SELECT * FROM coc_votes WHERE id=? AND case_id=? AND meeting_id=?", (vote_id, case_id, meeting_id)).fetchone()
            if not vote:
                raise KeyError("Vote not found")
            session = connection.execute("""SELECT s.* FROM coc_voting_sessions s JOIN coc_voting_session_resolutions r ON r.voting_session_id=s.id
                WHERE s.case_id=? AND s.meeting_id=? AND r.resolution_id=? ORDER BY s.created_at DESC LIMIT 1""", (case_id, meeting_id, vote["resolution_id"])).fetchone()
            if not session or session["status"] != "CLOSED":
                raise ValueError("A controlled correction is only needed after voting is closed")
            now = utc_now()
            connection.execute("INSERT INTO coc_vote_corrections(id,case_id,vote_id,old_vote,new_vote,reason,corrected_by,corrected_at) VALUES (?,?,?,?,?,?,?,?)",
                               (new_id(), case_id, vote_id, vote["vote"], new_vote, reason, actor_id, now))
            connection.execute("UPDATE coc_votes SET vote=?,remarks=?,updated_at=? WHERE id=?", (new_vote, f"Corrected: {reason}", now, vote_id))
            self.store.audit(connection, actor_id, "COC_VOTE_CORRECTED", "coc_vote", vote_id, case_id,
                             before={"vote": vote["vote"]}, after={"vote": new_vote, "reason": reason}, title="CoC vote corrected")
        return {"id": vote_id, "vote": new_vote, "corrected": True}

    def calculate_voting_result(self, case_id: str, meeting_id: str, session_id: str, resolution_id: str, actor_id: str) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            session = connection.execute("SELECT * FROM coc_voting_sessions WHERE id=? AND case_id=? AND meeting_id=?", (session_id, case_id, meeting_id)).fetchone()
            if not session or session["status"] != "CLOSED":
                raise ValueError("Close the voting session before calculating a result")
            resolution = connection.execute("SELECT * FROM coc_resolutions WHERE id=? AND case_id=? AND meeting_id=?", (resolution_id, case_id, meeting_id)).fetchone()
            if not resolution:
                raise KeyError("CoC Resolution not found")
            members = self.member_snapshot(case_id, meeting_id, connection)
            votes = connection.execute("SELECT * FROM coc_votes WHERE case_id=? AND meeting_id=? AND resolution_id=?", (case_id, meeting_id, resolution_id)).fetchall()
            by_snapshot = {row["meeting_member_snapshot_id"]: row for row in votes}
            totals = {choice: 0 for choice in VOTES}
            for member in members:
                if not member["voting_rights"]:
                    continue
                recorded = by_snapshot.get(member["id"])
                choice = recorded["vote"] if recorded else "NOT_VOTED"
                totals[choice] += int(member["voting_share_units"])
            rule = connection.execute("SELECT * FROM coc_approval_rules WHERE id=?", (resolution["approval_rule_id"],)).fetchone() if resolution["approval_rule_id"] else None
            if not rule or rule["status"] != "CONFIRMED":
                result, required = "REVIEW_REQUIRED", 0
            else:
                required = int(rule["minimum_for_share_units"])
                result = "APPROVED" if totals["FOR"] >= required else "REJECTED"
            existing = connection.execute("SELECT * FROM coc_voting_results WHERE voting_session_id=? AND resolution_id=?", (session_id, resolution_id)).fetchone()
            now = utc_now()
            snapshot = {"members": members, "votes": [dict(row) for row in votes], "totals": totals, "approval_rule_id": rule["id"] if rule else None}
            if existing and existing["status"] == "FINAL":
                return self.get_voting_result(case_id, meeting_id, existing["id"])
            result_id = existing["id"] if existing else new_id()
            if existing:
                connection.execute("""UPDATE coc_voting_results SET approval_rule_id=?,required_for_share_units=?,for_share_units=?,against_share_units=?,
                abstain_share_units=?,not_voted_share_units=?,result=?,status='CALCULATED',calculated_by=?,calculated_at=?,snapshot_json=? WHERE id=?""",
                (rule["id"] if rule else None, required, totals["FOR"], totals["AGAINST"], totals["ABSTAIN"], totals["NOT_VOTED"], result, actor_id, now, _json(snapshot), result_id))
            else:
                connection.execute("""INSERT INTO coc_voting_results(id,case_id,meeting_id,voting_session_id,resolution_id,approval_rule_id,
                required_for_share_units,for_share_units,against_share_units,abstain_share_units,not_voted_share_units,result,status,
                calculated_by,calculated_at,snapshot_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'CALCULATED',?,?,?)""",
                (result_id, case_id, meeting_id, session_id, resolution_id, rule["id"] if rule else None, required, totals["FOR"], totals["AGAINST"], totals["ABSTAIN"], totals["NOT_VOTED"], result, actor_id, now, _json(snapshot)))
            connection.execute("UPDATE coc_resolutions SET status=?,updated_by=?,updated_at=? WHERE id=?", ("APPROVED" if result == "APPROVED" else "REJECTED" if result == "REJECTED" else "PLACED", actor_id, now, resolution_id))
        return self.get_voting_result(case_id, meeting_id, result_id)

    def get_voting_result(self, case_id: str, meeting_id: str, result_id: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM coc_voting_results WHERE id=? AND case_id=? AND meeting_id=?", (result_id, case_id, meeting_id)).fetchone()
            if not row:
                raise KeyError("Voting result not found")
            result = dict(row)
            for key in ("required_for", "for", "against", "abstain", "not_voted"):
                result[f"{key}_share"] = _share_text(row[f"{key}_share_units"])
            result["snapshot"] = _from_json(row["snapshot_json"], {})
            return result

    def finalize_voting_result(self, case_id: str, meeting_id: str, result_id: str, actor_id: str) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            row = connection.execute("SELECT * FROM coc_voting_results WHERE id=? AND case_id=? AND meeting_id=?", (result_id, case_id, meeting_id)).fetchone()
            if not row:
                raise KeyError("Voting result not found")
            if row["result"] == "REVIEW_REQUIRED":
                raise ValueError("A confirmed Approval Rule is required before finalizing voting result")
            if row["status"] == "FINAL":
                return self.get_voting_result(case_id, meeting_id, result_id)
            now = utc_now()
            connection.execute("UPDATE coc_voting_results SET status='FINAL',finalized_by=?,finalized_at=? WHERE id=?", (actor_id, now, result_id))
            self.store.audit(connection, actor_id, "COC_VOTING_RESULT_FINALIZED", "coc_voting_result", result_id, case_id,
                             after={"result": row["result"]}, title="CoC voting result finalized")
        self._emit(case_id, "COC_VOTING_RESULT_FINALIZED", actor_id, result_id)
        return self.get_voting_result(case_id, meeting_id, result_id)

    def save_minutes_entry(self, case_id: str, meeting_id: str, agenda_item_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        text = payload.get("minutes_text")
        disposition = str(payload.get("disposition") or "").upper()
        if text is None:
            text = ""
        if disposition and disposition not in MINUTES_DISPOSITIONS:
            raise ValueError("Invalid Minutes disposition")
        with self.store.transaction() as connection:
            item = connection.execute("SELECT * FROM coc_agenda_version_items WHERE id=? AND case_id=? AND meeting_id=?", (agenda_item_id, case_id, meeting_id)).fetchone()
            if not item:
                raise KeyError("Agenda item not found")
            if not connection.execute("SELECT 1 FROM coc_notice_versions WHERE case_id=? AND meeting_id=? AND agenda_version_id=? AND status='ISSUED'", (case_id, meeting_id, item["agenda_version_id"])).fetchone():
                raise ValueError("Minutes must use an Agenda issued with the Notice")
            existing = connection.execute("SELECT * FROM coc_minutes_entries WHERE case_id=? AND meeting_id=? AND agenda_item_id=?", (case_id, meeting_id, agenda_item_id)).fetchone()
            now = utc_now()
            if existing:
                connection.execute("UPDATE coc_minutes_entries SET minutes_text=?,disposition=?,updated_by=?,updated_at=? WHERE id=?", (str(text), disposition, actor_id, now, existing["id"]))
                entry_id = existing["id"]
            else:
                entry_id = new_id()
                connection.execute("INSERT INTO coc_minutes_entries(id,case_id,meeting_id,agenda_item_id,minutes_text,disposition,updated_by,updated_at) VALUES (?,?,?,?,?,?,?,?)", (entry_id, case_id, meeting_id, agenda_item_id, str(text), disposition, actor_id, now))
            self.store.audit(connection, actor_id, "COC_MINUTES_TEXT_UPDATED", "coc_minutes_entry", entry_id, case_id,
                             after={"agenda_item_id": agenda_item_id, "disposition": disposition, "text_length": len(str(text))}, title="Office-provided Minutes text saved")
        return {"id": entry_id, "minutes_text": str(text), "disposition": disposition}

    def _minutes_snapshot(self, connection: sqlite3.Connection, case_id: str, meeting_id: str) -> Dict[str, Any]:
        meeting = self._meeting(connection, case_id, meeting_id)
        notice = connection.execute("SELECT * FROM coc_notice_versions WHERE case_id=? AND meeting_id=? AND status='ISSUED' ORDER BY version_number DESC LIMIT 1", (case_id, meeting_id)).fetchone()
        if not notice:
            raise ValueError("Issue Notice before preparing Minutes")
        agenda = self._agenda_rows(connection, case_id, meeting_id, notice["agenda_version_id"])
        entries = {row["agenda_item_id"]: dict(row) for row in connection.execute("SELECT * FROM coc_minutes_entries WHERE case_id=? AND meeting_id=?", (case_id, meeting_id)).fetchall()}
        resolutions = [dict(row) for row in connection.execute("SELECT * FROM coc_resolutions WHERE case_id=? AND meeting_id=? ORDER BY resolution_number", (case_id, meeting_id)).fetchall()]
        results = [dict(row) for row in connection.execute("SELECT * FROM coc_voting_results WHERE case_id=? AND meeting_id=?", (case_id, meeting_id)).fetchall()]
        for item in agenda:
            entry = entries.get(item["id"], {})
            item["minutes_text"] = entry.get("minutes_text", "")
            item["minutes_disposition"] = entry.get("disposition", "")
            item["resolution"] = next((row for row in resolutions if row["agenda_item_id"] == item["id"]), None)
        return {"meeting": dict(meeting), "notice": dict(notice), "agenda": agenda, "member_snapshot": self.member_snapshot(case_id, meeting_id, connection),
                "attendance": [dict(row) for row in connection.execute("SELECT * FROM coc_attendance WHERE case_id=? AND meeting_id=? AND archived_at IS NULL", (case_id, meeting_id)).fetchall()],
                "quorum": self.latest_quorum(case_id, meeting_id, connection), "resolutions": resolutions, "voting_results": results}

    def validate_minutes(self, case_id: str, meeting_id: str, connection: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
        owns_connection = connection is None
        if owns_connection:
            connection = self.store.connect()
        assert connection is not None
        try:
            snapshot = self._minutes_snapshot(connection, case_id, meeting_id)
            missing = [item["agenda_number"] for item in snapshot["agenda"]
                       if item["agenda_type"] != "PROCEDURAL" and not str(item.get("minutes_text") or "").strip() and not item.get("minutes_disposition")]
            return {"valid": not missing, "missing_agenda_numbers": missing, "snapshot": snapshot}
        finally:
            if owns_connection:
                connection.close()

    def create_minutes_version(self, case_id: str, meeting_id: str, actor_id: str) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            snapshot = self._minutes_snapshot(connection, case_id, meeting_id)
            serialized = _json(snapshot)
            existing = connection.execute("SELECT * FROM coc_minutes_versions WHERE case_id=? AND meeting_id=? AND status='DRAFT' ORDER BY version_number DESC LIMIT 1", (case_id, meeting_id)).fetchone()
            if existing and existing["snapshot_json"] == serialized:
                return dict(existing) | {"snapshot": snapshot, "idempotent_replay": True}
            version = int(connection.execute("SELECT COALESCE(MAX(version_number),0)+1 FROM coc_minutes_versions WHERE case_id=? AND meeting_id=?", (case_id, meeting_id)).fetchone()[0])
            previous = connection.execute("SELECT id FROM coc_minutes_versions WHERE case_id=? AND meeting_id=? ORDER BY version_number DESC LIMIT 1", (case_id, meeting_id)).fetchone()
            version_id, now = new_id(), utc_now()
            connection.execute("""INSERT INTO coc_minutes_versions(id,case_id,meeting_id,version_number,status,snapshot_json,generated_by,generated_at,parent_minutes_version_id)
                VALUES (?,?,?,?, 'DRAFT',?,?,?,?)""", (version_id, case_id, meeting_id, version, serialized, actor_id, now, previous["id"] if previous else None))
            self.store.audit(connection, actor_id, "COC_MINUTES_DRAFTED", "coc_minutes_version", version_id, case_id, after={"version": version}, title=f"CoC Minutes Draft V{version} prepared")
        self._emit(case_id, "COC_MINUTES_CONTENT_READY", actor_id, version_id)
        self._emit(case_id, "COC_MINUTES_DRAFTED", actor_id, version_id)
        return self.get_minutes_version(case_id, meeting_id, version_id)

    def get_minutes_version(self, case_id: str, meeting_id: str, version_id: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM coc_minutes_versions WHERE id=? AND case_id=? AND meeting_id=?", (version_id, case_id, meeting_id)).fetchone()
            if not row:
                raise KeyError("Minutes version not found")
            return dict(row) | {"snapshot": _from_json(row["snapshot_json"], {})}

    def link_minutes_document(self, case_id: str, meeting_id: str, version_id: str, document_id: str, actor_id: str) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            version = connection.execute("SELECT * FROM coc_minutes_versions WHERE id=? AND case_id=? AND meeting_id=?", (version_id, case_id, meeting_id)).fetchone()
            if not version:
                raise KeyError("Minutes version not found")
            if version["document_id"]:
                return self.get_minutes_version(case_id, meeting_id, version_id)
            if not connection.execute("SELECT 1 FROM documents WHERE id=? AND case_id=?", (document_id, case_id)).fetchone():
                raise ValueError("Minutes document does not belong to this case")
            connection.execute("UPDATE coc_minutes_versions SET document_id=? WHERE id=?", (document_id, version_id))
        return self.get_minutes_version(case_id, meeting_id, version_id)

    def finalize_minutes(self, case_id: str, meeting_id: str, version_id: str, actor_id: str) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            version = connection.execute("SELECT * FROM coc_minutes_versions WHERE id=? AND case_id=? AND meeting_id=?", (version_id, case_id, meeting_id)).fetchone()
            if not version:
                raise KeyError("Minutes version not found")
            if version["status"] == "FINAL":
                return self.get_minutes_version(case_id, meeting_id, version_id)
            valid = self.validate_minutes(case_id, meeting_id, connection)
            if not valid["valid"]:
                raise ValueError(f"Minutes text or disposition is required for Agenda item(s): {', '.join(valid['missing_agenda_numbers'])}")
            if not version["document_id"]:
                raise ValueError("Generate the Minutes DOCX before finalizing")
            now = utc_now()
            connection.execute("UPDATE coc_minutes_versions SET status='FINAL',finalized_by=?,finalized_at=? WHERE id=?", (actor_id, now, version_id))
            connection.execute("UPDATE coc_meetings SET status='MINUTES_PENDING',updated_by=?,updated_at=? WHERE id=?", (actor_id, now, meeting_id))
            self.store.audit(connection, actor_id, "COC_MINUTES_FINALIZED", "coc_minutes_version", version_id, case_id, after={"document_id": version["document_id"]}, title="CoC Minutes finalized")
        self._emit(case_id, "COC_MINUTES_FINALIZED", actor_id, version_id)
        return self.get_minutes_version(case_id, meeting_id, version_id)

    def record_minutes_circulation(self, case_id: str, meeting_id: str, version_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        status = str(payload.get("status") or "NOT_SENT").upper()
        with self.store.transaction() as connection:
            version = connection.execute("SELECT * FROM coc_minutes_versions WHERE id=? AND case_id=? AND meeting_id=? AND status='FINAL'", (version_id, case_id, meeting_id)).fetchone()
            if not version:
                raise ValueError("Only final Minutes can be circulated")
            now, circulation_id = utc_now(), new_id()
            recipients = payload.get("recipient_snapshot") or self.member_snapshot(case_id, meeting_id, connection)
            connection.execute("""INSERT INTO coc_minutes_circulations(id,case_id,meeting_id,minutes_version_id,recipient_snapshot_json,
                circulation_datetime,method,status,service_proof_document_id,remarks,recorded_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (circulation_id, case_id, meeting_id, version_id, _json(recipients), payload.get("circulation_datetime"),
                 str(payload.get("method") or ""), status, payload.get("service_proof_document_id"), str(payload.get("remarks") or ""), actor_id, now))
            self.store.audit(connection, actor_id, "COC_MINUTES_CIRCULATED", "coc_minutes_circulation", circulation_id, case_id,
                             after={"minutes_version_id": version_id, "status": status}, title="CoC Minutes circulation recorded")
        self._emit(case_id, "COC_MINUTES_CIRCULATED", actor_id, circulation_id)
        return {"id": circulation_id, "status": status}

    def complete_meeting(self, case_id: str, meeting_id: str, actor_id: str, actual_end_at: Optional[str] = None) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            meeting = self._meeting(connection, case_id, meeting_id)
            quorum = self.latest_quorum(case_id, meeting_id, connection)
            if not quorum or quorum["status"] != "MET":
                raise ValueError("A quorum-met record is required before ordinary Meeting completion")
            if not connection.execute("SELECT 1 FROM coc_minutes_versions WHERE case_id=? AND meeting_id=? AND status='FINAL'", (case_id, meeting_id)).fetchone():
                raise ValueError("Final Minutes are required before completing the Meeting")
            open_voting = connection.execute("SELECT 1 FROM coc_voting_sessions WHERE case_id=? AND meeting_id=? AND status='OPEN'", (case_id, meeting_id)).fetchone()
            if open_voting:
                raise ValueError("Close open voting sessions before completing the Meeting")
            now = actual_end_at or utc_now()
            connection.execute("UPDATE coc_meetings SET actual_end_at=?,status='COMPLETED',updated_by=?,updated_at=? WHERE id=?", (now, actor_id, utc_now(), meeting_id))
            self.store.audit(connection, actor_id, "COC_MEETING_HELD", "coc_meeting", meeting_id, case_id, after={"actual_end_at": now}, title="CoC meeting completed")
        self._emit(case_id, "COC_MEETING_HELD", actor_id, meeting_id, now[:10])
        return self.get_meeting(case_id, meeting_id)

    def cancel_meeting(self, case_id: str, meeting_id: str, reason: str, actor_id: str) -> Dict[str, Any]:
        if not str(reason).strip():
            raise ValueError("Cancellation reason is required")
        with self.store.transaction() as connection:
            meeting = self._meeting(connection, case_id, meeting_id)
            if meeting["status"] == "COMPLETED":
                raise ValueError("A completed meeting cannot be cancelled")
            connection.execute("UPDATE coc_meetings SET status='CANCELLED',updated_by=?,updated_at=? WHERE id=?", (actor_id, utc_now(), meeting_id))
            self.store.audit(connection, actor_id, "COC_MEETING_CANCELLED", "coc_meeting", meeting_id, case_id,
                             after={"reason": reason}, title="CoC meeting cancelled")
        return self.get_meeting(case_id, meeting_id)

    def record_cost_statement(self, case_id: str, meeting_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        rows = payload.get("rows") or []
        with self.store.transaction() as connection:
            self._meeting(connection, case_id, meeting_id)
            statement_id, now = new_id(), utc_now()
            total = 0
            connection.execute("""INSERT INTO coc_cost_statements(id,case_id,meeting_id,status,total_paise,expense_period_label,cost_kind,approval_status,approved_meeting_id,approved_resolution_id,created_by,created_at,updated_at)
                VALUES (?,?,?,'DRAFT',0,?,?,?,?,?,?,?,?)""", (statement_id, case_id, meeting_id,
                str(payload.get("expense_period_label") or ""), str(payload.get("cost_kind") or "ESTIMATED").upper(),
                str(payload.get("approval_status") or "DRAFT").upper(), payload.get("approved_meeting_id"), payload.get("approved_resolution_id"), actor_id, now, now))
            for item in rows:
                amount, gst = to_paise(item.get("amount")), to_paise(item.get("gst"))
                total += amount + gst
                connection.execute("""INSERT INTO coc_cost_statement_rows(id,cost_statement_id,case_id,category,vendor_name,description,period_date,
                amount_paise,gst_paise,paid_status,approval_required,supporting_document_id,expense_period_label,expense_type,vendor_contact_id,approval_status,payment_date,payment_reference,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (new_id(), statement_id, case_id, str(item.get("category") or "Other"), str(item.get("vendor_name") or ""), str(item.get("description") or ""),
                 item.get("period_date"), amount, gst, str(item.get("paid_status") or "UNPAID"), int(bool(item.get("approval_required"))), item.get("supporting_document_id"), str(item.get("expense_period_label") or payload.get("expense_period_label") or ""), str(item.get("expense_type") or "ACTUAL").upper(), item.get("vendor_contact_id"), str(item.get("approval_status") or payload.get("approval_status") or "DRAFT").upper(), item.get("payment_date"), str(item.get("payment_reference") or ""), now))
            connection.execute("UPDATE coc_cost_statements SET total_paise=? WHERE id=?", (total, statement_id))
            self.store.audit(connection, actor_id, "COC_COST_STATEMENT_RECORDED", "coc_cost_statement", statement_id, case_id, after={"total_paise": total}, title="CIRP Cost Statement recorded")
        self._emit(case_id, "COC_COST_STATEMENT_RECORDED", actor_id, statement_id)
        return {"id": statement_id, "total": from_paise(total), "total_paise": total}

    def save_operations_update(self, case_id: str, meeting_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        allowed = {"business_status", "cash_position", "employees", "security", "utilities", "insurance", "receivables", "management_cooperation", "important_developments"}
        data = {key: payload.get(key, "") for key in allowed if key in payload}
        with self.store.transaction() as connection:
            self._meeting(connection, case_id, meeting_id)
            existing = connection.execute("SELECT * FROM coc_operations_updates WHERE case_id=? AND meeting_id=?", (case_id, meeting_id)).fetchone()
            now = utc_now()
            if existing:
                merged = _from_json(existing["payload_json"], {}) | data
                connection.execute("UPDATE coc_operations_updates SET payload_json=?,updated_at=? WHERE id=?", (_json(merged), now, existing["id"]))
                update_id = existing["id"]
            else:
                update_id = new_id()
                connection.execute("INSERT INTO coc_operations_updates(id,case_id,meeting_id,payload_json,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (update_id, case_id, meeting_id, _json(data), actor_id, now, now))
        self._emit(case_id, "COC_OPERATIONS_UPDATE_RECORDED", actor_id, update_id)
        return {"id": update_id, "payload": data}

    def create_professional_proposal(self, case_id: str, meeting_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        category = str(payload.get("service_category") or "").strip()
        if not category:
            raise ValueError("Professional service category is required")
        with self.store.transaction() as connection:
            self._meeting(connection, case_id, meeting_id)
            proposal_id, now = new_id(), utc_now()
            connection.execute("""INSERT INTO coc_professional_proposals(id,case_id,meeting_id,agenda_item_id,service_category,professional_name,scope,
                proposed_fee_paise,tax_paise,appointment_status,approval_type,supporting_document_id,resolution_id,created_by,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (proposal_id, case_id, meeting_id, payload.get("agenda_item_id"), category, str(payload.get("professional_name") or ""), str(payload.get("scope") or ""),
                 to_paise(payload.get("proposed_fee")), to_paise(payload.get("tax")), str(payload.get("appointment_status") or "PROPOSED"),
                 str(payload.get("approval_type") or ""), payload.get("supporting_document_id"), payload.get("resolution_id"), actor_id, now, now))
        self._emit(case_id, "COC_PROFESSIONAL_PROPOSAL_RECORDED", actor_id, proposal_id)
        return {"id": proposal_id, "service_category": category}

    def create_action_item(self, case_id: str, meeting_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        text = str(payload.get("action_text") or "").strip()
        if not text:
            raise ValueError("Action item text is required")
        with self.store.transaction() as connection:
            self._meeting(connection, case_id, meeting_id)
            action_id, now = new_id(), utc_now()
            task_id = payload.get("task_id")
            if task_id and not connection.execute("SELECT 1 FROM tasks WHERE id=? AND case_id=? AND archived_at IS NULL", (task_id, case_id)).fetchone():
                raise ValueError("Linked Task does not belong to this case")
            if not task_id:
                task_id = self._ensure_task(connection, case_id, text, payload.get("due_date"), "coc_action_item", action_id, actor_id)
            connection.execute("""INSERT INTO coc_action_items(id,case_id,meeting_id,agenda_item_id,resolution_id,task_id,action_text,owner,due_date,status,
                completion_evidence_document_id,remarks,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (action_id, case_id, meeting_id, payload.get("agenda_item_id"), payload.get("resolution_id"), task_id, text, str(payload.get("owner") or ""),
                 payload.get("due_date"), str(payload.get("status") or "OPEN"), payload.get("completion_evidence_document_id"), str(payload.get("remarks") or ""), actor_id, now, now))
            self.store.audit(connection, actor_id, "COC_ACTION_ITEM_CREATED", "coc_action_item", action_id, case_id, after={"task_id": task_id, "action": text}, title="CoC Action Item created")
        self._emit(case_id, "COC_ATR_CREATED", actor_id, action_id)
        return {"id": action_id, "task_id": task_id, "action_text": text}

    def list_atr(self, case_id: str, meeting_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.store.connect() as connection:
            self.store.ensure_case(connection, case_id)
            sql = """SELECT a.*,t.status AS task_status,t.completed_at AS task_completed_at,t.title AS task_title,r.title AS resolution_title,
            i.agenda_number,i.title AS agenda_title,m.meeting_number FROM coc_action_items a
            LEFT JOIN tasks t ON t.id=a.task_id LEFT JOIN coc_resolutions r ON r.id=a.resolution_id
            LEFT JOIN coc_agenda_version_items i ON i.id=a.agenda_item_id LEFT JOIN coc_meetings m ON m.id=a.meeting_id
            WHERE a.case_id=?"""
            args: List[Any] = [case_id]
            if meeting_id:
                sql += " AND a.meeting_id=?"
                args.append(meeting_id)
            sql += " ORDER BY m.meeting_number,a.due_date,a.created_at"
            return [dict(row) for row in connection.execute(sql, args).fetchall()]

    def document_context(self, case_id: str, meeting_id: str, kind: str, version_id: Optional[str] = None) -> Dict[str, Any]:
        """Adapt a frozen lifecycle snapshot to the existing retained DOCX generator."""
        with self.store.connect() as connection:
            case = self.store.get_case(case_id)
            if not case:
                raise KeyError("Case not found")
            if kind == "notice":
                notice = self.get_notice_version(case_id, meeting_id, version_id or "")
                snapshot = notice["snapshot"]
            elif kind == "minutes":
                version = self.get_minutes_version(case_id, meeting_id, version_id or "")
                snapshot = version["snapshot"]
            else:
                raise ValueError("Document kind must be notice or minutes")
            meeting = snapshot["meeting"]
            members = snapshot.get("members") or self.member_snapshot(case_id, meeting_id, connection)
            if kind == "notice" and not members:
                members = []
                for row in connection.execute(
                    """SELECT cm.creditor_name,cm.admitted_debt_paise,cm.display_voting_percentage,c.email,c.address,c.authorized_representative
                    FROM coc_constitution_members cm LEFT JOIN claims c ON c.id=cm.claim_id AND c.case_id=cm.case_id
                    WHERE cm.case_id=? AND cm.constitution_id=? ORDER BY cm.creditor_name""",
                    (case_id, snapshot.get("constitution_id") or notice["coc_constitution_id"]),
                ).fetchall():
                    members.append({"creditor_name": row["creditor_name"], "organization": row["creditor_name"], "admitted_debt": from_paise(row["admitted_debt_paise"]),
                                    "voting_share": row["display_voting_percentage"], "recipient_email": row["email"] or "", "email": row["email"] or "",
                                    "recipient_address": row["address"] or "", "authorized_representative": row["authorized_representative"] or ""})
            agenda = []
            for item in snapshot["agenda"]:
                resolution = item.get("resolution")
                final = resolution.get("final_resolution_text") if resolution else ""
                agenda.append({
                    "id": item["id"], "position": item.get("sequence"), "section": "voting" if item.get("requires_voting") else "discussion",
                    "title": item["title"], "discussion": item.get("minutes_text", ""), "minutes_text": item.get("minutes_text", ""),
                    "minutes_disposition": item.get("minutes_disposition", ""), "decision": "", "proposed_resolution": item.get("proposed_resolution_text", ""),
                    "resolution_text": final or item.get("proposed_resolution_text", ""), "voting_required": bool(item.get("requires_voting")),
                })
            votes = []
            for result in snapshot.get("voting_results", []):
                votes.extend([{ "agenda_key": result["resolution_id"], "vote": "yes", "voting_share": float(Decimal(result["for_share_units"]) / SHARE_SCALE)}])
            return {"case": case, "workflow": {"meeting": meeting, "agenda": agenda, "attendance": snapshot.get("attendance", []),
                    "members": members, "votes": votes, "quorum": snapshot.get("quorum") or {}}, "snapshot": snapshot}

    def workflow_summary(self, case_id: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            self.store.ensure_case(connection, case_id)
            meetings = connection.execute("SELECT * FROM coc_meetings WHERE case_id=? AND archived_at IS NULL ORDER BY meeting_number", (case_id,)).fetchall()
            next_meeting = next((row for row in meetings if row["status"] not in {"COMPLETED", "CANCELLED"}), None)
            if not next_meeting:
                return {"coc_meeting_count": len(meetings), "next_coc_meeting": None, "meeting_status": None, "notice_status": None,
                        "agenda_status": None, "minutes_status": None, "quorum_status": None, "voting_status": None,
                        "pending_voting_results": 0, "pending_atr_actions": 0}
            meeting_id = next_meeting["id"]
            notice = connection.execute("SELECT status FROM coc_notice_versions WHERE meeting_id=? ORDER BY version_number DESC LIMIT 1", (meeting_id,)).fetchone()
            agenda = connection.execute("SELECT status FROM coc_agenda_versions WHERE meeting_id=? ORDER BY version_number DESC LIMIT 1", (meeting_id,)).fetchone()
            minutes = connection.execute("SELECT status FROM coc_minutes_versions WHERE meeting_id=? ORDER BY version_number DESC LIMIT 1", (meeting_id,)).fetchone()
            quorum = connection.execute("SELECT status FROM coc_meeting_quorum_records WHERE meeting_id=? ORDER BY calculated_at DESC LIMIT 1", (meeting_id,)).fetchone()
            voting = connection.execute("SELECT status FROM coc_voting_sessions WHERE meeting_id=? ORDER BY created_at DESC LIMIT 1", (meeting_id,)).fetchone()
            pending_results = connection.execute("SELECT COUNT(*) FROM coc_voting_results WHERE case_id=? AND status<>'FINAL'", (case_id,)).fetchone()[0]
            pending_actions = connection.execute("""SELECT COUNT(*) FROM coc_action_items a LEFT JOIN tasks t ON t.id=a.task_id
                WHERE a.case_id=? AND COALESCE(t.status,a.status) NOT IN ('completed','COMPLETED','cancelled','CANCELLED')""", (case_id,)).fetchone()[0]
            return {"coc_meeting_count": len(meetings), "next_coc_meeting": {"id": meeting_id, "meeting_number": next_meeting["meeting_number"], "scheduled_start_at": next_meeting["scheduled_start_at"]},
                    "meeting_status": next_meeting["status"], "notice_status": notice["status"] if notice else None, "agenda_status": agenda["status"] if agenda else None,
                    "minutes_status": minutes["status"] if minutes else None, "quorum_status": quorum["status"] if quorum else None,
                    "voting_status": voting["status"] if voting else None, "pending_voting_results": int(pending_results), "pending_atr_actions": int(pending_actions)}
