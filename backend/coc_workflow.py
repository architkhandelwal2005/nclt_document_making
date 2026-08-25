"""CoC workflow validation and retained-template DOCX generation.

The generator edits selected OOXML parts directly.  It deliberately does not
open and save authoritative templates through python-docx because doing so can
normalize tracked revisions, fields, content controls, and malformed-but-valid
Word attributes present in the supplied precedents.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence
import io
import re
import zipfile

from lxml import etree


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML = "http://www.w3.org/XML/1998/namespace"
NS = {"w": W}
qn = lambda name: f"{{{W}}}{name}"

TEMPLATE_SPECS = {
    "notice": {1: "notice-first.docx", 2: "notice-subsequent.docx"},
    "minutes": {1: "minutes-first.docx", 2: "minutes-subsequent.docx"},
}


def _text(element: etree._Element) -> str:
    return "".join(element.xpath(".//w:t/text()", namespaces=NS)).strip()


def _set_text(paragraph: etree._Element, value: str) -> None:
    """Replace paragraph content while retaining its paragraph and first-run styling."""
    first_run = paragraph.find(".//w:r", NS)
    run_properties = deepcopy(first_run.find("w:rPr", NS)) if first_run is not None and first_run.find("w:rPr", NS) is not None else None
    for child in list(paragraph):
        if child.tag != qn("pPr"):
            paragraph.remove(child)
    run = etree.SubElement(paragraph, qn("r"))
    if run_properties is not None:
        run.append(run_properties)
    text = etree.SubElement(run, qn("t"))
    if value.startswith(" ") or value.endswith(" "):
        text.set(f"{{{XML}}}space", "preserve")
    text.text = value


def _remove_numbering(paragraph: etree._Element) -> None:
    properties = paragraph.find("w:pPr", NS)
    if properties is not None:
        numbering = properties.find("w:numPr", NS)
        if numbering is not None:
            properties.remove(numbering)


def _reset_indent(paragraph: etree._Element) -> None:
    properties = paragraph.find("w:pPr", NS)
    if properties is not None:
        indent = properties.find("w:ind", NS)
        if indent is not None:
            properties.remove(indent)


def _clone_paragraph(exemplar: etree._Element, value: str, remove_numbering: bool = False) -> etree._Element:
    result = deepcopy(exemplar)
    _set_text(result, value)
    if remove_numbering:
        _remove_numbering(result)
    return result


def _paragraphs(root: etree._Element) -> List[etree._Element]:
    return root.xpath(".//w:p", namespaces=NS)


def _find_paragraph(root: etree._Element, needle: str) -> etree._Element:
    lowered = needle.casefold()
    for paragraph in _paragraphs(root):
        if lowered in _text(paragraph).casefold():
            return paragraph
    raise ValueError(f"Retained template block not found: {needle}")


def _format_date(value: Any, include_day: bool = False) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.strftime("%A, %B %d, %Y" if include_day else "%d-%m-%Y")
    except ValueError:
        return raw


def _format_time(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%I:%M %p")
    except ValueError:
        return raw


def _ordinal(number: int) -> str:
    names = {1: "First", 2: "Second", 3: "Third", 4: "Fourth", 5: "Fifth", 6: "Sixth", 7: "Seventh", 8: "Eighth", 9: "Ninth", 10: "Tenth"}
    return names.get(number, f"{number}th")


def _replace_visible_text(root: etree._Element, replacements: Mapping[str, str]) -> None:
    """Replace phrases paragraph-by-paragraph without flattening unrelated paragraphs."""
    ordered = [(old, new) for old, new in replacements.items() if old and old != new]
    for paragraph in _paragraphs(root):
        current = _text(paragraph)
        if not current:
            continue
        updated = current
        for old, new in ordered:
            updated = re.sub(re.escape(old), lambda _: new, updated, flags=re.IGNORECASE)
        if updated != current:
            _set_text(paragraph, updated)


def _replace_prefixed_paragraph(root: etree._Element, prefix: str, value: str) -> None:
    for paragraph in _paragraphs(root):
        if _text(paragraph).casefold().startswith(prefix.casefold()):
            _set_text(paragraph, f"{prefix}{value}")
            return


def _replace_body_range(
    root: etree._Element, start_text: str, end_text: str,
    values: Sequence[str], numbered: bool = True,
) -> None:
    """Replace direct-body paragraphs between two headings using a native exemplar."""
    body = root.find("w:body", NS)
    if body is None:
        raise ValueError("DOCX has no document body")
    children = list(body)
    start = next((index for index, child in enumerate(children) if child.tag == qn("p") and start_text.casefold() in _text(child).casefold()), None)
    end = next((index for index, child in enumerate(children) if child.tag == qn("p") and end_text.casefold() in _text(child).casefold() and (start is None or index > start)), None)
    if start is None or end is None or end <= start:
        raise ValueError(f"Unable to locate retained range: {start_text} -> {end_text}")
    candidates = [child for child in children[start + 1:end] if child.tag == qn("p") and _text(child)]
    exemplar = candidates[0] if candidates else children[start]
    for child in children[start + 1:end]:
        body.remove(child)
    insertion = start + 1
    for position, value in enumerate(values, 1):
        label = f"{position}. {value}" if numbered else value
        body.insert(insertion, _clone_paragraph(exemplar, label, remove_numbering=True))
        insertion += 1


def _set_cell_text(cell: etree._Element, value: Any) -> None:
    paragraphs = cell.findall(".//w:p", NS)
    if paragraphs:
        _set_text(paragraphs[0], str(value or ""))
        _remove_numbering(paragraphs[0])
        for extra in paragraphs[1:]:
            parent = extra.getparent()
            if parent is not None:
                parent.remove(extra)


def _replace_table_rows(table: etree._Element, rows: Sequence[Sequence[Any]]) -> None:
    originals = table.findall("w:tr", NS)
    if not originals:
        return
    exemplar = originals[1] if len(originals) > 1 else originals[0]
    for row in originals[1:]:
        table.remove(row)
    for values in rows:
        row = deepcopy(exemplar)
        cells = row.findall("w:tc", NS)
        for index, cell in enumerate(cells):
            _set_cell_text(cell, values[index] if index < len(values) else "")
        table.append(row)


def _notice_rows(table: etree._Element, workflow: Dict[str, Any]) -> List[List[Any]]:
    header_cells = table.findall("w:tr", NS)[0].findall("w:tc", NS)
    headers = [_text(cell).casefold() for cell in header_cells]
    result: List[List[Any]] = []
    for index, member in enumerate(workflow.get("members", []), 1):
        name = member.get("organization") or member.get("contact_name") or member.get("authorized_representative") or "CoC member"
        row = []
        for header in headers:
            if "s. no" in header or "sr.no" in header or "sr. no" in header:
                row.append(index)
            elif "name" in header or "creditors" in header and "class" not in header:
                row.append(name)
            elif "class" in header:
                row.append(member.get("creditor_category") or "CoC member")
            elif "email" in header:
                row.append(member.get("email") or "")
            elif "claim admitted" in header:
                row.append(f"{float(member.get('admitted_debt') or 0):,.2f}")
            elif "%" in header or "voting" in header:
                row.append(f"{float(member.get('voting_share') or 0):.4f}%")
            else:
                row.append("")
        result.append(row)
    return result


def _management_rows(table: etree._Element, contacts: Iterable[Dict[str, Any]]) -> List[List[Any]]:
    selected = [item for item in contacts if any(term in str(item.get("role") or "").casefold() for term in ("suspended", "director", "management"))]
    header_cells = table.findall("w:tr", NS)[0].findall("w:tc", NS)
    headers = [_text(cell).casefold() for cell in header_cells]
    rows = []
    for index, item in enumerate(selected, 1):
        row = []
        for header in headers:
            if "s.no" in header or "sr." in header:
                row.append(index)
            elif "name" in header:
                row.append(item.get("name") or "")
            elif "email" in header:
                row.append(item.get("email") or "")
            elif "designation" in header:
                row.append(item.get("role") or item.get("category") or "")
            else:
                row.append("")
        rows.append(row)
    return rows


def _sample_replacements(case: Dict[str, Any], meeting: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, str]:
    company = str(case.get("name") or "Corporate Debtor")
    professional = str(profile.get("ip_name") or profile.get("name") or meeting.get("chair_name") or "Resolution Professional")
    process_email = str(profile.get("process_email") or case.get("registered_email") or profile.get("ip_email") or "")
    return {
        "M/s. Premier Proteins Limited": company,
        "M/s Premier Proteins Limited": company,
        "Premier Proteins Limited": company,
        "Mahakali Foods Private Limited": company,
        "Mahakali Foods Pvt. Ltd.": company,
        "Navin Khandelwal": professional,
        "ibc.proteins@gmail.com": process_email,
        "cirp.mahakalifoods@gmail.com": process_email,
        "cirp.keshav@gmail.com": process_email,
        "28-08-2025": _format_date(case.get("order_date")),
        "31.07.2026": _format_date(case.get("order_date")),
        "31-07-2026": _format_date(case.get("order_date")),
        "[DATE OF FIRST COC MEETING]": _format_date(meeting.get("meeting_at"), include_day=True),
        "[TIME]": _format_time(meeting.get("meeting_at")),
        "Thursday October 16, 2025 at 03:30 P.M.": f"{_format_date(meeting.get('meeting_at'), include_day=True)} at {_format_time(meeting.get('meeting_at'))}",
        "Stadium Parisar, 95, Vikas Nagar,": "",
        "Near V2 Mall, AB Road, Dewas": "",
        "IBBI/IPA-1/P00703/20172018/11301": str(profile.get("ibbi_reg_no") or "Registration number not recorded"),
        "IBBI/IPA-1/P00703/2017-2018/11301": str(profile.get("ibbi_reg_no") or "Registration number not recorded"),
        "navink25@yahoo.com": str(profile.get("ip_email") or process_email),
        "31-12-2025": str(profile.get("afa_validity") or "not recorded"),
        "31-12-2026": str(profile.get("afa_validity") or "not recorded"),
        "206, Navneet Plaza, 5/2 Old Palasia, Indore 452018": str(profile.get("ip_reg_address") or "the registered office address recorded in the case workspace"),
    }


def _build_notice(root: etree._Element, case: Dict[str, Any], workflow: Dict[str, Any], contacts: Sequence[Dict[str, Any]], profile: Dict[str, Any]) -> None:
    meeting = workflow["meeting"]
    agenda = workflow.get("agenda", [])
    replacements = _sample_replacements(case, meeting, profile)
    _replace_visible_text(root, replacements)
    first = int(meeting.get("meeting_number") or 1) == 1
    if first:
        body = root.find("w:body", NS)
        if body is not None:
            for paragraph in list(body.findall("w:p", NS)):
                if _text(paragraph).startswith("DRAFT -"):
                    body.remove(paragraph)
                    break
    _replace_prefixed_paragraph(root, "Day & Date: ", _format_date(meeting.get("meeting_at"), include_day=True))
    _replace_prefixed_paragraph(root, "Time: ", _format_time(meeting.get("meeting_at")))
    _replace_prefixed_paragraph(root, "Mode: ", str(meeting.get("mode") or ""))
    if str(meeting.get("venue_or_link") or ""):
        for prefix in ("Address: ", "Venue: "):
            _replace_prefixed_paragraph(root, prefix, str(meeting.get("venue_or_link")))
    _replace_prefixed_paragraph(root, "Date: ", _format_date(meeting.get("notice_date")))
    _replace_prefixed_paragraph(root, "Place: ", str(meeting.get("notice_place") or ""))
    discussion = [str(item.get("title") or "").strip() for item in agenda if str(item.get("section") or "discussion") == "discussion"]
    voting = [str(item.get("title") or "").strip() for item in agenda if item.get("voting_required") or str(item.get("section") or "") == "voting"]
    _replace_body_range(root, "List of the matters to be discussed", "List of the issues to be voted", discussion or ["No discussion item recorded"])
    _replace_body_range(root, "List of the issues to be voted", "Instructions For E-Voting", voting or ["No voting item recorded"])
    tables = root.xpath(".//w:tbl", namespaces=NS)
    if tables:
        _replace_table_rows(tables[0], _notice_rows(tables[0], workflow) or [[1] + ["No CoC member recorded"] * (len(tables[0].findall('w:tr', NS)[0].findall('w:tc', NS)) - 1)])
    if len(tables) > 1:
        _replace_table_rows(tables[1], _management_rows(tables[1], contacts) or [[1] + ["No suspended-management contact recorded"] * (len(tables[1].findall('w:tr', NS)[0].findall('w:tc', NS)) - 1)])
    _replace_prefixed_paragraph(root, "START AND END TIME: ", f"The voting period will begin on {_format_date(meeting.get('voting_start'), True)} {_format_time(meeting.get('voting_start'))} and end on {_format_date(meeting.get('voting_end'), True)} {_format_time(meeting.get('voting_end'))}.")
    if first:
        body = root.find("w:body", NS)
        if body is not None:
            children = list(body)
            start = next((index for index, child in enumerate(children) if child.tag == qn("p") and "The following Resolution may be passed".casefold() in _text(child).casefold()), None)
            if start is not None:
                sect_pr = next((child for child in children if child.tag == qn("sectPr")), None)
                exemplar = children[start]
                for child in children[start:]:
                    if child is not sect_pr:
                        body.remove(child)
                body.insert(len(body) - (1 if sect_pr is not None else 0), _clone_paragraph(exemplar, "PROPOSED RESOLUTIONS", remove_numbering=True))
                for item in [row for row in agenda if row.get("voting_required")]:
                    resolution = str(item.get("proposed_resolution") or "").strip()
                    body.insert(len(body) - (1 if sect_pr is not None else 0), _clone_paragraph(exemplar, f"{item.get('position')}. {item.get('title')}", remove_numbering=True))
                    if resolution:
                        body.insert(len(body) - (1 if sect_pr is not None else 0), _clone_paragraph(exemplar, resolution, remove_numbering=True))
    else:
        body = root.find("w:body", NS)
        voting_items = [row for row in agenda if row.get("voting_required")]
        if body is not None and voting_items:
            children = list(body)
            sect_pr = next((child for child in children if child.tag == qn("sectPr")), None)
            exemplar = _find_paragraph(root, "Agenda for the meeting")
            resolution_exemplar = _find_paragraph(root, "The committee shall fix")
            insertion = len(body) - (1 if sect_pr is not None else 0)
            body.insert(insertion, _clone_paragraph(exemplar, "PROPOSED RESOLUTIONS", remove_numbering=True))
            insertion += 1
            for item in voting_items:
                body.insert(insertion, _clone_paragraph(exemplar, f"{item.get('position')}. {item.get('title')}", remove_numbering=True))
                insertion += 1
                resolution = str(item.get("proposed_resolution") or "").strip()
                if resolution:
                    body.insert(insertion, _clone_paragraph(resolution_exemplar, resolution, remove_numbering=True))
                    insertion += 1


def _find_direct_exemplar(body: etree._Element, needle: str, fallback: etree._Element | None = None) -> etree._Element:
    for child in body:
        if child.tag == qn("p") and needle.casefold() in _text(child).casefold():
            return child
    if fallback is not None:
        return fallback
    return next(child for child in body if child.tag == qn("p"))


def _minutes_attendance_rows(table: etree._Element, workflow: Dict[str, Any]) -> List[List[Any]]:
    headers = [_text(cell).casefold() for cell in table.findall("w:tr", NS)[0].findall("w:tc", NS)]
    rows = []
    for index, item in enumerate([row for row in workflow.get("attendance", []) if row.get("present")], 1):
        mode = str(item.get("attendance_mode") or "")
        row = []
        for header in headers:
            if "sr." in header or "s.no" in header:
                row.append(index)
            elif "name" in header:
                row.append(item.get("participant_name") or "")
            elif "representing" in header:
                row.append(item.get("organization") or item.get("capacity") or "")
            elif "voting" in header:
                row.append(f"{float(item.get('voting_share_snapshot') or 0):.4f}%")
            elif "physically" in header:
                row.append("Yes" if "physical" in mode.casefold() else "")
            elif "virtually" in header:
                row.append("Yes" if any(word in mode.casefold() for word in ("video", "audio", "virtual")) else "")
            else:
                row.append("")
        rows.append(row)
    return rows


def _vote_summary(agenda_item: Dict[str, Any], votes: Sequence[Dict[str, Any]]) -> str:
    relevant = [row for row in votes if str(row.get("agenda_key")) in {str(agenda_item.get("id")), str(agenda_item.get("position")), str(agenda_item.get("title"))}]
    if not relevant:
        return "Voting result: No vote has been recorded."
    totals = {choice: round(sum(float(row.get("voting_share") or 0) for row in relevant if str(row.get("vote") or "").casefold() == choice), 4) for choice in ("yes", "no", "abstain")}
    return f"Voting result: Yes {totals['yes']:.4f}% | No {totals['no']:.4f}% | Abstain {totals['abstain']:.4f}%"


def _build_minutes(root: etree._Element, case: Dict[str, Any], workflow: Dict[str, Any], profile: Dict[str, Any]) -> None:
    meeting = workflow["meeting"]
    agenda = workflow.get("agenda", [])
    missing = [str(item.get("position")) for item in agenda if not str(item.get("discussion") or "").strip()]
    if missing:
        raise ValueError(f"Manual discussion is required for agenda item(s): {', '.join(missing)}")
    body = root.find("w:body", NS)
    if body is None:
        raise ValueError("DOCX has no document body")
    original_children = list(body)
    fallback = next(child for child in original_children if child.tag == qn("p"))
    title_ex = _find_direct_exemplar(body, "MINUTES OF THE", fallback)
    time_ex = _find_direct_exemplar(body, "meeting was", fallback)
    members_ex = _find_direct_exemplar(body, "MEMBERS PRESENT", fallback)
    agenda_no_ex = _find_direct_exemplar(body, "AGENDA NO.", fallback)
    agenda_title_ex = _find_direct_exemplar(body, "CHAIRMAN OF THE MEETING", fallback)
    discussion_ex = _find_direct_exemplar(body, "welcomed all the members", fallback)
    resolution_ex = _find_direct_exemplar(body, "RESOLVED", discussion_ex)
    thanks_ex = _find_direct_exemplar(body, "VOTE OF THANKS", agenda_title_ex)
    closing_ex = _find_direct_exemplar(body, "There being no other matter", discussion_ex)
    tables = body.findall("w:tbl", NS)
    attendance_table = deepcopy(tables[0]) if tables else None
    sect_pr = next((deepcopy(child) for child in original_children if child.tag == qn("sectPr")), None)
    for child in original_children:
        body.remove(child)
    number = int(meeting.get("meeting_number") or 1)
    title = (
        f"MINUTES OF THE {_ordinal(number).upper()} MEETING OF COMMITTEE OF CREDITORS OF "
        f"{str(case.get('name') or '').upper()} (COMPANY UNDER CIRP), HELD ON "
        f"{_format_date(meeting.get('meeting_at'), True).upper()} AT {_format_time(meeting.get('meeting_at'))} "
        f"{str(meeting.get('mode') or '').upper()} {str(meeting.get('venue_or_link') or '').upper()}"
    )
    title_paragraph = _clone_paragraph(title_ex, title)
    _reset_indent(title_paragraph)
    body.append(title_paragraph)
    body.append(_clone_paragraph(discussion_ex, f"The meeting commenced at {_format_time(meeting.get('actual_start_at') or meeting.get('meeting_at'))} and concluded at {_format_time(meeting.get('actual_end_at')) or 'not recorded'}."))
    body.append(_clone_paragraph(members_ex, "MEMBERS PRESENT"))
    if attendance_table is not None:
        fallback_cells = len(attendance_table.findall("w:tr", NS)[0].findall("w:tc", NS))
        _replace_table_rows(attendance_table, _minutes_attendance_rows(attendance_table, workflow) or [["No participant marked present"] + [""] * (fallback_cells - 1)])
        body.append(attendance_table)
    quorum = workflow.get("quorum", {})
    body.append(_clone_paragraph(discussion_ex, f"Quorum calculation: {float(quorum.get('present_voting_share') or 0):.4f}% voting share present against a configured threshold of {float(quorum.get('threshold') or 33):.4f}%. Quorum {'was' if quorum.get('met') else 'was not'} met."))
    for position, item in enumerate(agenda, 1):
        body.append(_clone_paragraph(agenda_no_ex, f"AGENDA NO. {position}"))
        body.append(_clone_paragraph(agenda_title_ex, str(item.get("title") or "").upper()))
        body.append(_clone_paragraph(discussion_ex, str(item.get("discussion") or "")))
        if str(item.get("decision") or "").strip():
            body.append(_clone_paragraph(discussion_ex, f"Decision: {item['decision']}"))
        resolution = str(item.get("resolution_text") or item.get("proposed_resolution") or "").strip()
        if resolution:
            body.append(_clone_paragraph(resolution_ex, resolution))
        if item.get("voting_required"):
            body.append(_clone_paragraph(discussion_ex, _vote_summary(item, workflow.get("votes", []))))
    body.append(_clone_paragraph(thanks_ex, "VOTE OF THANKS"))
    body.append(_clone_paragraph(closing_ex, "There being no other matter, the meeting ended with a vote of thanks to the Chair."))
    chair = str(meeting.get("chair_name") or profile.get("ip_name") or profile.get("name") or "Resolution Professional")
    body.append(_clone_paragraph(resolution_ex, chair))
    body.append(_clone_paragraph(discussion_ex, f"Resolution Professional of {case.get('name') or ''}"))
    if sect_pr is not None:
        body.append(sect_pr)


class CocDocumentService:
    """Generate versionable CoC documents from retained DOCX packages."""

    def __init__(self, template_dir: Path):
        self.template_dir = Path(template_dir)

    def generate(
        self, document_type: str, case: Dict[str, Any], workflow: Dict[str, Any],
        contacts: Sequence[Dict[str, Any]], profile: Dict[str, Any], output_path: Path,
    ) -> Path:
        if document_type not in TEMPLATE_SPECS:
            raise ValueError("Document type must be notice or minutes")
        meeting_number = int(workflow["meeting"].get("meeting_number") or 1)
        family = 1 if meeting_number == 1 else 2
        source = self.template_dir / TEMPLATE_SPECS[document_type][family]
        if not source.is_file():
            raise FileNotFoundError(f"Retained CoC template unavailable: {source.name}")
        with zipfile.ZipFile(source, "r") as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        document_root = etree.fromstring(parts["word/document.xml"])
        if document_type == "notice":
            _build_notice(document_root, case, workflow, contacts, profile)
        else:
            _build_minutes(document_root, case, workflow, profile)
        parts["word/document.xml"] = etree.tostring(document_root, xml_declaration=True, encoding="UTF-8", standalone=True)
        if "word/settings.xml" in parts:
            settings = etree.fromstring(parts["word/settings.xml"])
            update_fields = settings.find("w:updateFields", NS)
            if update_fields is None:
                update_fields = etree.SubElement(settings, qn("updateFields"))
            update_fields.set(qn("val"), "true")
            parts["word/settings.xml"] = etree.tostring(settings, xml_declaration=True, encoding="UTF-8", standalone=True)
        replacements = _sample_replacements(case, workflow["meeting"], profile)
        for name in list(parts):
            if re.fullmatch(r"word/(header|footer)\d*\.xml", name):
                part_root = etree.fromstring(parts[name])
                _replace_visible_text(part_root, replacements)
                parts[name] = etree.tostring(part_root, xml_declaration=True, encoding="UTF-8", standalone=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as output:
            for name, data in parts.items():
                output.writestr(name, data)
        output_path.write_bytes(buffer.getvalue())
        return output_path
