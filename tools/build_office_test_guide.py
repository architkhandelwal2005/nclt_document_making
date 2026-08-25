from pathlib import Path
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


OUTPUT = Path(r"E:\nclt document\portable_dist\Casefile-Portable-Test\CASEFILE USER GUIDE AND TEST PLAN.docx")

BLUE = "2E74B5"
DARK_BLUE = "1F4D78"
PALE_BLUE = "E8EEF5"
PALE_GREEN = "E8F3EC"
PALE_AMBER = "FFF4D6"
PALE_RED = "FBE9E7"
WHITE = "FFFFFF"
GRAY = "666666"
LIGHT_GRAY = "F4F6F8"
BLACK = "000000"
TABLE_WIDTH_DXA = 9360
TABLE_INDENT_DXA = 120


def set_font(run, size=None, bold=None, italic=None, color=BLACK, name="Calibri"):
    run.font.name = name
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), name)
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), name)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic
    if color:
        run.font.color.rgb = RGBColor.from_string(color)


def shade_cell(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def cell_margins(cell, top=80, start=120, bottom=80, end=120):
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for tag, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{tag}"))
        if node is None:
            node = OxmlElement(f"w:{tag}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_table_geometry(table, widths_dxa):
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    tbl_pr = table._tbl.tblPr
    tbl_w = tbl_pr.first_child_found_in("w:tblW")
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:w"), str(sum(widths_dxa)))
    tbl_w.set(qn("w:type"), "dxa")
    tbl_ind = tbl_pr.first_child_found_in("w:tblInd")
    if tbl_ind is None:
        tbl_ind = OxmlElement("w:tblInd")
        tbl_pr.append(tbl_ind)
    tbl_ind.set(qn("w:w"), str(TABLE_INDENT_DXA))
    tbl_ind.set(qn("w:type"), "dxa")
    grid = table._tbl.tblGrid
    for child in list(grid):
        grid.remove(child)
    for width in widths_dxa:
        col = OxmlElement("w:gridCol")
        col.set(qn("w:w"), str(width))
        grid.append(col)
    for row in table.rows:
        for idx, cell in enumerate(row.cells):
            tc_pr = cell._tc.get_or_add_tcPr()
            tc_w = tc_pr.first_child_found_in("w:tcW")
            if tc_w is None:
                tc_w = OxmlElement("w:tcW")
                tc_pr.append(tc_w)
            tc_w.set(qn("w:w"), str(widths_dxa[idx]))
            tc_w.set(qn("w:type"), "dxa")
            cell.width = Inches(widths_dxa[idx] / 1440)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            cell_margins(cell)


def keep_with_next(paragraph):
    paragraph.paragraph_format.keep_with_next = True


def add_page_field(paragraph):
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = paragraph.add_run("Page ")
    set_font(run, 9, color=GRAY)
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend([begin, instr, separate, text, end])


def configure_styles(doc):
    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Calibri"
    normal._element.rPr.rFonts.set(qn("w:ascii"), "Calibri")
    normal._element.rPr.rFonts.set(qn("w:hAnsi"), "Calibri")
    normal.font.size = Pt(11)
    normal.font.color.rgb = RGBColor.from_string(BLACK)
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.25
    for style_name, size, color, before, after in (
        ("Heading 1", 16, BLUE, 18, 10),
        ("Heading 2", 13, BLUE, 14, 7),
        ("Heading 3", 12, DARK_BLUE, 10, 5),
    ):
        style = styles[style_name]
        style.font.name = "Calibri"
        style._element.rPr.rFonts.set(qn("w:ascii"), "Calibri")
        style._element.rPr.rFonts.set(qn("w:hAnsi"), "Calibri")
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor.from_string(color)
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True
    for style_name in ("List Bullet", "List Number"):
        style = styles[style_name]
        style.font.name = "Calibri"
        style.font.size = Pt(11)
        style.paragraph_format.left_indent = Inches(0.375)
        style.paragraph_format.first_line_indent = Inches(-0.188)
        style.paragraph_format.space_after = Pt(4)
        style.paragraph_format.line_spacing = 1.25
    for name, fill, color in (
        ("Note Box", PALE_BLUE, DARK_BLUE),
        ("Warning Box", PALE_AMBER, "7A4E00"),
        ("Critical Box", PALE_RED, "8B1E17"),
    ):
        if name not in styles:
            style = styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        else:
            style = styles[name]
        style.base_style = styles["Normal"]
        style.font.name = "Calibri"
        style.font.size = Pt(10.5)
        style.font.color.rgb = RGBColor.from_string(color)
        style.paragraph_format.left_indent = Inches(0.14)
        style.paragraph_format.right_indent = Inches(0.14)
        style.paragraph_format.space_before = Pt(6)
        style.paragraph_format.space_after = Pt(8)
        style.paragraph_format.keep_together = True
        p_pr = style.element.get_or_add_pPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:fill"), fill)
        p_pr.append(shd)
        borders = OxmlElement("w:pBdr")
        left = OxmlElement("w:left")
        left.set(qn("w:val"), "single")
        left.set(qn("w:sz"), "18")
        left.set(qn("w:color"), color)
        left.set(qn("w:space"), "8")
        borders.append(left)
        p_pr.append(borders)


def add_callout(doc, label, text, style="Note Box"):
    p = doc.add_paragraph(style=style)
    r = p.add_run(f"{label}: ")
    set_font(r, 10.5, bold=True, color=p.style.font.color.rgb.__str__())
    r = p.add_run(text)
    set_font(r, 10.5, color=p.style.font.color.rgb.__str__())
    return p


def add_bullet(doc, text, level=0):
    p = doc.add_paragraph(style="List Bullet" if level == 0 else "List Bullet 2")
    p.add_run(text)
    return p


def add_numbered_list(doc, items):
    """Create a real Word-numbered list with a fresh numbering instance."""
    numbering = doc.part.numbering_part.element
    style_num_id = doc.styles["List Number"]._element.pPr.numPr.numId.val
    base_num = next(node for node in numbering.findall(qn("w:num")) if int(node.get(qn("w:numId"))) == int(style_num_id))
    abstract_id = base_num.find(qn("w:abstractNumId")).get(qn("w:val"))
    next_id = max([int(node.get(qn("w:numId"))) for node in numbering.findall(qn("w:num"))] + [0]) + 1
    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(next_id))
    abstract = OxmlElement("w:abstractNumId")
    abstract.set(qn("w:val"), abstract_id)
    num.append(abstract)
    level_override = OxmlElement("w:lvlOverride")
    level_override.set(qn("w:ilvl"), "0")
    start_override = OxmlElement("w:startOverride")
    start_override.set(qn("w:val"), "1")
    level_override.append(start_override)
    num.append(level_override)
    numbering.append(num)
    for text in items:
        p = doc.add_paragraph(style="List Number")
        num_pr = p._p.get_or_add_pPr().get_or_add_numPr()
        num_pr.get_or_add_ilvl().val = 0
        num_pr.get_or_add_numId().val = next_id
        p.add_run(text)


def add_table(doc, headers, rows, widths_dxa, header_fill=PALE_BLUE, font_size=9.2):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    set_table_geometry(table, widths_dxa)
    hdr = table.rows[0]
    tr_pr = hdr._tr.get_or_add_trPr()
    repeat = OxmlElement("w:tblHeader")
    repeat.set(qn("w:val"), "true")
    tr_pr.append(repeat)
    for idx, text in enumerate(headers):
        shade_cell(hdr.cells[idx], header_fill)
        p = hdr.cells[idx].paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        r = p.add_run(str(text))
        set_font(r, font_size, bold=True, color=DARK_BLUE)
    for row_data in rows:
        row = table.add_row()
        for idx, value in enumerate(row_data):
            cell = row.cells[idx]
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            r = p.add_run(str(value))
            set_font(r, font_size, color=BLACK)
    set_table_geometry(table, widths_dxa)
    after = doc.add_paragraph()
    after.paragraph_format.space_after = Pt(0)
    return table


def h1(doc, text):
    return doc.add_heading(text, level=1)


def h2(doc, text):
    return doc.add_heading(text, level=2)


def h3(doc, text):
    return doc.add_heading(text, level=3)


def new_chapter(doc, title, subtitle=None, force_page=True):
    if force_page:
        doc.add_page_break()
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    r = p.add_run("CASEFILE REFERENCE GUIDE")
    set_font(r, 9, bold=True, color=BLUE)
    h1(doc, title)
    if subtitle:
        p = doc.add_paragraph(subtitle)
        p.paragraph_format.space_after = Pt(14)
        for r in p.runs:
            set_font(r, 11, italic=True, color=GRAY)


def add_module(doc, name, purpose, records, test_points):
    h2(doc, name)
    p = doc.add_paragraph(purpose)
    if records:
        h3(doc, "What can be recorded")
        for item in records:
            add_bullet(doc, item)
    h3(doc, "What to test")
    for item in test_points:
        add_bullet(doc, item)


def create_document():
    doc = Document()
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(0.78)
    section.bottom_margin = Inches(0.78)
    section.left_margin = Inches(1)
    section.right_margin = Inches(1)
    section.header_distance = Inches(0.35)
    section.footer_distance = Inches(0.35)
    configure_styles(doc)

    header = section.header
    hp = header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.LEFT
    hr = hp.add_run("CASEFILE  |  OFFICE TEST BUILD")
    set_font(hr, 8.5, bold=True, color=GRAY)
    footer = section.footer
    fp = footer.paragraphs[0]
    fp.add_run("Internal testing reference  |  ")
    for r in fp.runs:
        set_font(r, 9, color=GRAY)
    add_page_field(fp)

    # Editorial-cover pattern: centered title, restrained metadata, generous whitespace.
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(80)
    kicker = doc.add_paragraph()
    kicker.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = kicker.add_run("OFFICE TESTING MANUAL")
    set_font(r, 11, bold=True, color=BLUE)
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_after = Pt(8)
    r = title.add_run("Casefile")
    set_font(r, 31, bold=True, color=DARK_BLUE)
    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.paragraph_format.space_after = Pt(5)
    r = subtitle.add_run("User Guide and Complete Test Plan")
    set_font(r, 17, bold=True, color=BLUE)
    sub2 = doc.add_paragraph()
    sub2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub2.paragraph_format.space_after = Pt(30)
    r = sub2.add_run("Portable local build for insolvency and NCLT practice management")
    set_font(r, 11, italic=True, color=GRAY)
    add_table(doc, ["Document purpose", "Release status"], [["Explain every current function and guide structured office testing", "Pre-release / local office test build"]], [4680, 4680], font_size=10)
    add_callout(doc, "Important", "Use invented or non-confidential test information during this evaluation. Do not treat calculated compliance dates as legal advice; an Insolvency Professional must verify every statutory date and rule before operational reliance.", "Warning Box")
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(40)
    r = p.add_run("Prepared 22 August 2026")
    set_font(r, 10, color=GRAY)

    new_chapter(doc, "1. Purpose, scope, and testing rules", "Read this section before entering any information.")
    h2(doc, "What this software is")
    doc.add_paragraph("Casefile is a local practice-management application: software that organizes the firm's companies, proceedings, work, deadlines, stakeholders, documents, costs, and history in separate case workspaces. The present package is intended for office evaluation before full production use.")
    h2(doc, "What this test must establish")
    for item in (
        "The application starts and signs in reliably on the office computer.",
        "Users can create a company/case and keep its information separate from every other case.",
        "Each case module accepts realistic records, displays them correctly, and retains them after restart.",
        "Linked workflows create the expected follow-up work, including tasks from tribunal directions.",
        "Uploaded and generated documents can be downloaded and opened.",
        "Firm-wide tasks, calendar items, dashboard counts, and reusable master information are accurate.",
        "Access roles and local backup controls behave safely.",
        "Office users can identify missing fields, confusing labels, unnecessary steps, and required reports.",
    ):
        add_bullet(doc, item)
    h2(doc, "Rules for this evaluation")
    for item in (
        "Use sample data first. Do not enter actual claimant bank details, confidential valuation material, or original case evidence until the build is approved.",
        "Test one feature at a time and record the exact result as Pass, Fail, Partly works, or Not tested.",
        "If something fails, do not repeatedly overwrite the record. Note the screen, company, action, visible message, time, and whether a restart changes the result.",
        "Do not edit, rename, or remove files inside the _internal, web, or templates folders.",
        "Stop Casefile before moving or copying the folder. An open application may still be writing to its database.",
    ):
        add_bullet(doc, item)

    new_chapter(doc, "2. Starting and stopping Casefile", "The portable package runs entirely on the local Windows computer.")
    h2(doc, "Recommended first start")
    steps = (
        "Copy the entire Casefile-Portable-Test folder from the pen drive to a normal folder on the office computer. Do not copy only Casefile.exe.",
        "Open the copied folder and double-click START CASEFILE.bat.",
        "Keep the black Casefile server window open. The server is the local background program that stores and retrieves data for the browser interface.",
        "Wait for the browser to open. If it does not, open a browser and visit http://127.0.0.1:8765.",
        "Open LOGIN DETAILS.txt and use the supplied office test email and password. Keep those details private.",
        "Confirm the top-right indicator says Local database.",
    )
    add_numbered_list(doc, steps)
    add_callout(doc, "Windows warning", "Casefile.exe is not digitally signed. Windows SmartScreen may show Unknown publisher. Proceed only when the folder came directly from the authorized office pen drive.", "Warning Box")
    h2(doc, "Correct shutdown")
    shutdown_steps = (
        "Finish saving the record currently being entered.",
        "Sign out from the left sidebar if convenient.",
        "Close the browser tab.",
        "Return to the black Casefile window and press Ctrl+C, or close that window.",
        "Only after shutdown should the folder be copied, moved, or returned on the pen drive.",
    )
    add_numbered_list(doc, shutdown_steps)
    h2(doc, "Start-up tests")
    add_table(doc, ["ID", "Action", "Expected result", "Result"], [
        ("S-01", "Start from the copied office-PC folder", "Browser opens and login page appears", "Pass / Fail"),
        ("S-02", "Use an incorrect password", "Login is rejected without exposing details", "Pass / Fail"),
        ("S-03", "Use the supplied login", "Firm Dashboard appears", "Pass / Fail"),
        ("S-04", "Refresh the browser", "Signed-in workspace reloads", "Pass / Fail"),
        ("S-05", "Stop and start Casefile again", "Existing records remain available", "Pass / Fail"),
    ], [720, 2520, 4320, 1800])

    new_chapter(doc, "3. Where information is stored and how to protect it")
    h2(doc, "Local data model")
    doc.add_paragraph("The database is the structured record store used for company information, tasks, deadlines, claims, meetings, costs, and history. In this package it is stored at data\\casefile.db. Uploaded files are separately stored under data\\files. Generated documents are downloaded through the browser and should be filed in an appropriate office folder.")
    add_callout(doc, "Critical", "Copying only data\\casefile.db does not copy uploaded files. For a complete transfer of test work, stop Casefile and copy the entire Casefile-Portable-Test folder.", "Critical Box")
    h2(doc, "Backup methods")
    add_table(doc, ["Method", "What it protects", "When to use"], [
        ("Complete folder copy", "Database, uploaded files, application, templates, and configuration", "Returning the test build or moving it to another computer"),
        ("Encrypted database backup", "Structured database records only", "A same-Windows-user safety copy before risky testing"),
        ("Generated-document download", "The produced Word file", "After each document is generated"),
    ], [2160, 3960, 3240], font_size=9.5)
    doc.add_paragraph("An encrypted backup is a database copy protected so only the same Windows user account can decrypt it. It is not a cross-computer transfer method. The current Google Sheets mirror and cloud backup are not yet implemented.")
    h2(doc, "Daily test backup procedure")
    backup_steps = (
        "Stop Casefile.",
        "Copy the complete Casefile-Portable-Test folder to a dated folder, for example Casefile-Test-2026-08-22-EOD.",
        "Keep at least the previous day's copy until the new copy has been opened and checked.",
        "At the end of office testing, return the complete updated folder under a new name; do not overwrite the original package on the pen drive.",
    )
    add_numbered_list(doc, backup_steps)

    new_chapter(doc, "4. Software map and normal workflow")
    h2(doc, "Firm-level navigation")
    add_table(doc, ["Area", "Purpose"], [
        ("Dashboard", "Firm control centre showing portfolio counts and work requiring attention."),
        ("Cases", "Create, search, select, and open a company/case workspace."),
        ("All Tasks", "Review and change task status across all companies."),
        ("Calendar", "Combined diary of tasks, deadlines, hearings, and CoC meetings."),
        ("Firm Masters", "View reusable compliance rules, document formats, and contacts."),
        ("My Profile", "Maintain the Insolvency Professional's details used in document generation."),
        ("Settings", "Administrator-only users, security status, backup, and restore."),
    ], [2160, 7200], font_size=9.5)
    h2(doc, "Recommended daily workflow")
    workflow_steps = (
        "Start at Dashboard and review overdue work and upcoming hearings.",
        "Open All Tasks and update the status of work completed or blocked.",
        "Open Calendar to check deadlines, meetings, and listings by date.",
        "Select the relevant company from Dashboard or Cases.",
        "Enter operational records only inside that company's workspace.",
        "Check Activity after important entries to confirm the chronology was recorded.",
        "Download generated documents and store them under the office's approved file naming system.",
        "At the end of the day, close the application and make a dated complete-folder copy during testing.",
    )
    add_numbered_list(doc, workflow_steps)

    new_chapter(doc, "5. Company and case workspace", "All operational records are isolated inside the selected company.")
    add_module(doc, "Cases list and case creation", "Cases is the entry point for company-specific work.", [
        "Corporate debtor name, internal reference, CIN, process type, NCLT bench, petition/application number, order date, and commencement date.",
        "Supported process types: CIRP, Liquidation, Voluntary Liquidation, Pre-packaged Insolvency, and NCLT Litigation.",
        "Search by company name, CIN, or petition/application number.",
    ], [
        "Create two clearly different sample companies and confirm each opens in its own workspace.",
        "Search using part of the company name, CIN, and petition number.",
        "Confirm a search with no match shows No matching cases.",
        "Open each case and confirm the header shows the correct company, process type, petition number, bench, and risk level.",
    ])
    add_module(doc, "Overview / case master", "Overview maintains the controlling information for the selected company and shows summary totals.", [
        "Company information: name, CIN, registered email, industry, and registered address.",
        "Proceeding information: bench, petition/application number, applicant, applicant category, admission/order date, and insolvency commencement date.",
        "Process control: internal reference, process type, status, current stage, risk level, closure date, outcome, and internal notes.",
        "Summary cards: open tasks, open deadlines, claims, documents, hearings, and expense total.",
    ], [
        "Edit every field, save, leave the case, reopen it, and confirm the same values remain.",
        "Set risk levels normal, watch, high, and critical in turn; confirm the case header reflects the saved level.",
        "Confirm changes affect only the selected company.",
        "After adding records in later modules, return to Overview and verify the summary figures update.",
    ])
    add_callout(doc, "Compliance caution", "When a commencement date is entered, the application may create deadline records from its rule master. The office must verify the rule, event date, exclusions, extensions, and current law independently.", "Warning Box")

    new_chapter(doc, "6. Work, compliance, and tribunal modules", force_page=False)
    add_module(doc, "Tasks", "Tracks assignments and due dates for the selected company.", [
        "Title, description, category, assignee, priority, status, start date, and due date.",
        "Statuses: open, in-progress, blocked, completed, and cancelled.",
        "Records can be archived; archived records leave the active list but remain in audit history.",
    ], [
        "Create tasks with each priority and several due dates, including one past due date.",
        "Change status directly from the list and confirm All Tasks reflects the update.",
        "Mark a task completed and confirm it is no longer treated as open/overdue.",
        "Archive one disposable sample task and confirm the confirmation prompt appears and the task leaves the list.",
    ])
    add_module(doc, "Compliance deadlines", "Tracks statutory and procedural obligations, responsibility, completion, and overrides.", [
        "Obligation, provision, due date, responsible person, status, and override/exception reason.",
        "Statuses: open, in-progress, completed, and not-applicable.",
    ], [
        "Inspect automatically created deadlines after entering a commencement date.",
        "Create a manual deadline and complete it.",
        "Record an override reason and confirm it remains visible in the list.",
        "Verify the item appears on Firm Calendar on the correct date.",
        "Have a qualified Insolvency Professional compare every seeded deadline against current law; report any incorrect or missing rule.",
    ])
    add_module(doc, "Hearings", "Maintains listings, appearances, adjournments, and next dates.", [
        "Date/time, tribunal, bench, purpose, counsel, court hall/link, status, next hearing, and notes.",
        "Tribunals include NCLT, NCLAT, Supreme Court, High Court, and Other.",
    ], [
        "Create a future hearing and confirm it appears in the case and Firm Calendar.",
        "Record hearing notes, change the status, and add a next hearing date.",
        "Confirm a newly scheduled future hearing appears on Dashboard when within the displayed upcoming range.",
    ])
    add_module(doc, "Applications", "Tracks applications from draft and filing through defects and disposal.", [
        "Application type, diary/application number, filing date, parties, relief sought, status, filing defects, and disposal date.",
        "Statuses: draft, filed, defective, listed, allowed, dismissed, and disposed.",
    ], [
        "Create an application, record filing defects, and move it through two status changes.",
        "Confirm long party names and relief text remain readable and saved.",
        "Record disposal and verify the saved disposal date.",
    ])
    add_module(doc, "Orders and directions", "Links tribunal orders to hearings/applications and converts directions into actionable work.", [
        "Order date, related hearing, related application, and order summary.",
        "Direction text and due date linked to a selected order.",
        "Each direction is intended to create a linked task automatically.",
    ], [
        "Record an order linked to the sample hearing and sample application.",
        "Create a direction with a due date.",
        "Open Tasks and confirm a corresponding linked task was created with the correct due date.",
        "Open All Tasks and Calendar and confirm the linked task appears for the correct company.",
    ])

    new_chapter(doc, "7. Claims and Committee of Creditors", force_page=False)
    add_module(doc, "Claims register", "Tracks a creditor claim from receipt through verification and decision.", [
        "Creditor category, claim form, received date, claimed amount, admitted amount, status, security details, deficiencies, and decision reason.",
        "Statuses: received, under_review, deficient, admitted, partially_admitted, rejected, and withdrawn.",
    ], [
        "Create at least one financial-creditor claim and one operational-creditor claim.",
        "Use realistic decimal amounts and confirm claimed/admitted figures are retained.",
        "Mark a claim deficient and confirm any expected follow-up task is created.",
        "Change a claim to partially admitted and record a decision reason.",
        "Confirm Dashboard pending-claims count responds to open claim statuses.",
    ])
    add_module(doc, "Claim supporting-document checklist", "Records which supporting items are required and received for a particular claim.", [
        "Selected claim, required document name, required flag, received flag, and notes in the underlying record.",
    ], [
        "Select a claim, add at least three required document items, and mark one received.",
        "Confirm every item appears against the intended claim and shows Pending or Received correctly.",
        "Report if the office requires direct file attachment to each checklist item; the present screen records checklist status only.",
    ])
    add_module(doc, "CoC membership", "Maintains historically effective admitted debt and voting share for Committee of Creditors members.", [
        "Admitted debt, voting share percentage, effective-from/effective-to dates, and authorized representative.",
    ], [
        "Create two sample members with voting shares totaling 100%.",
        "Create a later effective record and confirm the historical dates remain visible.",
        "Try an intentionally incorrect total in sample data and note whether the office wants a future validation warning.",
    ])
    add_module(doc, "CoC meetings and voting", "Tracks notices, meeting details, voting windows, minutes, and resolution-level votes.", [
        "Meeting number, date/time, mode, venue/link, notice date, voting start/end, status, and minutes.",
        "Votes link a meeting and member to an agenda item, store Yes/No/Abstain, and preserve the voting share used.",
    ], [
        "Create a meeting and confirm it appears in Firm Calendar.",
        "Record notice and voting-window dates, then save minutes.",
        "Record Yes, No, and Abstain votes for sample agenda items.",
        "Confirm the selected member's voting share is carried into the vote record.",
        "Check whether an automatic meeting-related task is created where expected.",
    ])

    new_chapter(doc, "8. Stakeholders, documents, and communications")
    add_module(doc, "Contacts", "Maintains people and organizations and their roles in the selected case while reusing them in the firm directory.", [
        "Name, person/organization type, organization, role in case, email, phone, address, communication preference, and notes in the contact record.",
    ], [
        "Add one person and one organization with different case roles.",
        "Confirm email and phone display correctly on the contact cards.",
        "Open Firm Masters and verify reusable contacts appear in the shared directory.",
        "Add the same real-world contact to another sample case and check whether the workflow is clear enough for office use.",
    ])
    add_module(doc, "Documents - generation", "Creates a Word document from an approved format inside the selected company context.", [
        "Approved document type and its required fields.",
        "Professional-profile and company values are pre-filled where available.",
        "The generated record is added to the case document history and the Word file downloads through the browser.",
    ], [
        "Complete My Profile before generation.",
        "Generate each available approved document type at least once.",
        "Open every downloaded .docx in Microsoft Word and check company name, dates, amounts, names, addresses, page layout, tables, and blank placeholders.",
        "Regenerate one document with a changed field and confirm the new version appears in document history.",
        "Confirm generated documents never use information from another sample company.",
    ])
    add_module(doc, "Documents - upload and download", "Stores copies of case files inside the portable data folder.", [
        "Allowed types: PDF, DOC, DOCX, XLS, XLSX, CSV, TXT, PNG, JPG, and JPEG.",
        "Maximum file size: 25 MB per upload.",
        "Document history shows name, category/source, and version.",
    ], [
        "Upload one small file of each office-relevant type, then download and open it.",
        "Try a disallowed executable file and confirm it is rejected.",
        "Try a file larger than 25 MB only if a safe disposable sample is available; confirm rejection.",
        "Use identical filenames in two different cases and confirm downloads return the correct content for each case.",
        "Restart Casefile and confirm uploaded files remain downloadable.",
    ])
    add_module(doc, "Communications", "Records a communication history; it does not send messages.", [
        "Channel, direction, date/time, sender, recipients, subject, summary/content, and delivery status.",
        "Channels: Email, Physical letter, Courier, WhatsApp, Portal, Telephone, and Meeting.",
        "Directions: outgoing, incoming, and internal.",
    ], [
        "Record one incoming, outgoing, and internal communication.",
        "Test several channels and long recipient/subject text.",
        "Confirm the chronology records the communication.",
        "Do not expect Casefile to send an email, WhatsApp message, or courier request.",
    ])

    new_chapter(doc, "9. Assets, finance, valuation, and costs")
    add_module(doc, "Assets", "Records identified assets, their location, ownership, security, possession, insurance, encumbrance, and condition.", [
        "Category, description, ownership, location, book value, security interest, possession, insurance, encumbrance, and status.",
        "Statuses: identified, verified, secured, under_valuation, sold, and disputed.",
    ], [
        "Create movable, immovable, financial, and disputed sample assets.",
        "Enter decimal book values and confirm they remain accurate.",
        "Move one sample asset through several statuses.",
    ])
    add_module(doc, "Financial information", "Maintains selected books-and-records information for the company.", [
        "Record types: Bank account, Book creditor, Receivable, Statutory due, Employee, Contract, Transaction review, and Other.",
        "Name/description, amount, and as-of date.",
    ], [
        "Create at least three record types with different dates and amounts.",
        "Confirm the module does not claim to connect to a bank or accounting system; entries are manual.",
        "Report any additional columns required by the office's existing workbook.",
    ])
    add_module(doc, "Valuation", "Tracks valuers' progress and value conclusions by asset class.", [
        "Asset class, appointment date, inspection date, report date, fair value, liquidation value, status, confidentiality flag, and notes.",
        "Statuses: appointed, information-pending, inspection-complete, report-received, and finalized.",
    ], [
        "Create valuations for two asset classes and progress them through different statuses.",
        "Test the confidentiality checkbox and confirm its value is saved in the record.",
        "Compare large fair-value and liquidation-value amounts against the entered figures.",
        "Report if confidential values need stronger screen masking or role restrictions.",
    ])
    add_module(doc, "Expenses", "Tracks invoices, approvals, payment, and CIRP-cost eligibility.", [
        "Category, invoice number, date, amount, tax, approval status, payment status, and CIRP-cost-eligible flag.",
    ], [
        "Enter approved, rejected, paid, part-paid, and unpaid samples.",
        "Verify amount plus tax is included correctly in the Overview expense total.",
        "Test decimal values and a zero-tax expense.",
    ])
    add_module(doc, "CoC contributions", "Tracks contribution calls, receipts, balances, and notes.", [
        "Called amount, due date, paid amount, paid date, and notes.",
    ], [
        "Create unpaid, partly paid, and fully paid sample contributions.",
        "Confirm dates and decimal amounts persist after restart.",
        "Report whether the office needs automatic outstanding-balance calculations and reminders beyond the current fields.",
    ])

    new_chapter(doc, "10. Activity, firm-wide views, profile, and settings")
    add_module(doc, "Activity", "Provides a chronological event history for the selected case.", [
        "Timestamp, event title, entity type, and event type for recorded actions.",
    ], [
        "After creating, updating, generating, and archiving sample records, confirm corresponding events appear.",
        "Check timestamps against the office computer's local time.",
        "Confirm activity from one company never appears in another company's chronology.",
    ])
    add_module(doc, "Dashboard", "Summarizes the active portfolio and urgent work.", [
        "Active cases, overdue tasks, overdue deadlines, pending claims, companies/cases, and selected next tasks/hearings.",
    ], [
        "Create known overdue and future sample records and reconcile every displayed count.",
        "Open a company directly from the Dashboard.",
        "Confirm no archived or completed work is incorrectly counted as open.",
    ])
    add_module(doc, "All Tasks", "Combines case-linked tasks into one firm queue.", [
        "Task, company, category, priority, due date, and status.",
    ], [
        "Verify tasks from both sample companies appear with the correct company names.",
        "Change task status in this view and confirm the case-specific Tasks tab reflects it.",
        "Test sorting expectations and report if filters, assignee views, or exports are needed.",
    ])
    add_module(doc, "Firm Calendar", "Combines dated tasks, deadlines, hearings, and CoC meetings.", [
        "Date grouping, item type, title, company, and status.",
    ], [
        "Reconcile each sample dated record against its calendar day.",
        "Check date/time records around midnight and ensure they appear on the intended Indian date.",
        "Report whether month/week views, reminders, print, or calendar export are required.",
    ])
    add_module(doc, "Firm Masters", "Displays reusable reference information shared across the practice.", [
        "Versioned compliance rules, approved document formats, and shared contacts.",
    ], [
        "Review rule names, provisions, day offsets, versions, and effective dates with a professional.",
        "Confirm approved document formats shown here are the same options offered inside a case.",
        "Confirm case contacts appear in the shared directory.",
        "Note that this build primarily displays masters; report the required administrative editing workflow.",
    ])
    add_module(doc, "My Profile", "Maintains professional information used to pre-fill document-generation fields.", [
        "The profile screen contains professional identity and registration details required by approved formats.",
    ], [
        "Complete every available field and save.",
        "Leave and return to confirm persistence.",
        "Generate a document and confirm profile fields are pre-filled correctly.",
    ])
    add_module(doc, "Settings and users", "Provides administrator-only security and user management.", [
        "Security-status display, local users, roles, encrypted database backup, and restore.",
        "Roles available for newly created users: professional, staff, reviewer, and read-only; the supplied administrator has full control.",
    ], [
        "Create one user in each role with a disposable test email and a password of at least 12 characters.",
        "Sign in as each user and record which screens/actions are available.",
        "Confirm a read-only user can view records but cannot create, update, archive, upload, or generate.",
        "Download an encrypted backup and retain it only for same-Windows-user restore testing.",
        "Before restore testing, make a complete folder copy. Restore only a known backup created by the same Windows user, then verify records and restart.",
    ])

    new_chapter(doc, "11. Required end-to-end test scenarios", "Run these scenarios in order with invented data.")
    scenarios = [
        ("E2E-01", "New CIRP workspace", "Create Sample Alpha Ltd.; complete the case master; verify automatically seeded deadlines; leave and reopen the case."),
        ("E2E-02", "Case isolation", "Create Sample Beta Ltd. with different identifiers; add similarly named tasks and files to both; verify no cross-case data appears."),
        ("E2E-03", "Tribunal workflow", "Create a hearing and application; record an order and direction; verify the linked task and calendar item."),
        ("E2E-04", "Claim workflow", "Create a claim, mark it deficient, add supporting-document checklist items, update admission, and reconcile Dashboard counts."),
        ("E2E-05", "CoC workflow", "Create two members, a meeting, voting dates, minutes, and votes; verify historical shares and Firm Calendar."),
        ("E2E-06", "Document workflow", "Complete the professional profile; generate a Word document; upload a PDF; restart; download and open both."),
        ("E2E-07", "Cost workflow", "Enter expenses and contributions; verify amounts persist and the Overview expense total is correct."),
        ("E2E-08", "Chronology", "Review Activity after creates, updates, generation, and archive actions; reconcile event order and timestamps."),
        ("E2E-09", "Role control", "Create a read-only user; sign in; confirm viewing works and every write attempt is blocked."),
        ("E2E-10", "Persistence and transfer", "Stop Casefile, copy the complete folder to a new dated folder, start that copy, and verify records and uploaded files."),
    ]
    add_table(doc, ["ID", "Scenario", "Procedure / expected outcome"], scenarios, [1080, 2160, 6120], font_size=9)
    add_callout(doc, "Pass condition", "A scenario passes only when the visible record, related firm-wide view, downloaded file where applicable, and post-restart persistence all agree.", "Note Box")

    new_chapter(doc, "12. Negative, reliability, and usability tests")
    h2(doc, "Validation and error handling")
    for item in (
        "Submit required forms with a required field blank; the form must not save incomplete data.",
        "Enter an invalid email format; the browser should block submission where the field is typed as email.",
        "Try duplicate-looking records and note whether the office needs duplicate warnings.",
        "Use very long names, addresses, notes, and Indian currency amounts; text must remain readable and values must not be truncated.",
        "Click Save only once, then also test a deliberate double-click; duplicate records should not be created.",
        "Disconnect the pen drive after running from the copied office-PC folder; the application should remain functional.",
        "Close the browser but keep the server open; reopening the local address should restore access.",
        "Close the server while the browser is open; the interface should stop loading data rather than pretending a save succeeded.",
    ):
        add_bullet(doc, item)
    h2(doc, "Usability review")
    for item in (
        "Record every label the office does not understand immediately.",
        "Identify fields present in the father's existing workbook that are missing from the corresponding module.",
        "Identify repeated data entry that should be pre-filled or selected from a master.",
        "Identify reports, printouts, filters, exports, reminders, dashboards, or approval steps needed for daily work.",
        "Record whether each screen can be used comfortably on the office monitor without excessive horizontal scrolling.",
        "Note the three most frequent tasks and count how many clicks each requires.",
    ):
        add_bullet(doc, item)
    h2(doc, "Performance and stability")
    add_table(doc, ["Test", "Expected result", "Observation"], [
        ("Create 50 sample tasks across two cases", "Lists remain usable and save reliably", ""),
        ("Upload several permitted files", "Uploads and downloads complete without corrupting files", ""),
        ("Run continuously for 2 hours", "No unexpected shutdown or progressive slowdown", ""),
        ("Restart Windows, then start Casefile", "All prior records and files remain available", ""),
        ("Repeat start/stop five times", "Port 8765 starts each time without conflict", ""),
    ], [3240, 3960, 2160], font_size=9.2)

    new_chapter(doc, "13. Current limitations and items not to assume", force_page=False)
    limitations = (
        "This is a local single-computer test build. It is not presently a shared network, cloud, or multi-office system.",
        "Google Sheets synchronization, Google Drive backup, MongoDB use, Render deployment, and GitHub deployment are not part of this portable build.",
        "Encrypted database backups are tied to the Windows user that created them and do not include uploaded files. A complete stopped-folder copy is required for transfer and full recovery during testing.",
        "Compliance rules and calculated dates are configurable software records, not legal opinions. A qualified professional must validate them against current law and case-specific facts.",
        "The Communications module logs history; it does not send email, WhatsApp, courier, portal, or SMS messages.",
        "Financial information is manually entered; there is no bank, accounting, GST, or ERP integration in this build. ERP means enterprise resource planning software used for finance and operations.",
        "Claim checklist items record receipt status but are not yet a dedicated file-attachment system for each checklist line.",
        "Firm Masters are mainly a reference view in the current interface; master editing may require a later administrative workflow.",
        "Generated documents must be legally and visually reviewed before filing or circulation.",
        "The executable is unsigned, so Windows may display a security warning.",
        "Production-grade disaster recovery, automatic off-device backup, notification delivery, and audit exports require later work.",
    )
    for item in limitations:
        add_bullet(doc, item)

    new_chapter(doc, "14. How to report a problem or requested change")
    h2(doc, "Information required for every issue")
    for item in (
        "Issue number and short title.",
        "Date/time and tester name.",
        "Computer and Windows version if known.",
        "Screen and selected company/case.",
        "Exact steps performed.",
        "Expected result and actual result.",
        "Visible error message, copied exactly.",
        "Whether the issue occurs every time or only sometimes.",
        "Whether Casefile was restarted and what happened afterward.",
        "Screenshot with confidential sample information only.",
        "Priority: Critical, High, Medium, or Low.",
    ):
        add_bullet(doc, item)
    h2(doc, "Priority definitions")
    add_table(doc, ["Priority", "Definition", "Example"], [
        ("Critical", "Data loss, data leakage between cases, application cannot start, or files cannot be recovered.", "A record saved in Alpha appears in Beta."),
        ("High", "Core work cannot be completed and no reasonable workaround exists.", "A claim or hearing cannot be saved."),
        ("Medium", "Function partly works or requires an inconvenient workaround.", "A required field is missing from a module."),
        ("Low", "Wording, spacing, appearance, or minor convenience issue.", "A label should use office terminology."),
    ], [1260, 5220, 2880], font_size=9.2)
    h2(doc, "Issue form")
    add_table(doc, ["Field", "Tester entry"], [
        ("Issue ID / title", ""), ("Tester and date", ""), ("Screen / company", ""),
        ("Steps", ""), ("Expected result", ""), ("Actual result / exact message", ""),
        ("Frequency", ""), ("Restart result", ""), ("Priority", ""), ("Suggested change", ""),
    ], [2160, 7200], font_size=9.5)

    new_chapter(doc, "15. Final office sign-off checklist", force_page=False)
    signoff = [
        ("A", "Installation and access", "Starts on the office PC, login works, and correct shutdown is understood."),
        ("B", "Case isolation", "Two sample companies were tested with no cross-case leakage."),
        ("C", "Operational modules", "Tasks, compliance, hearings, applications, claims, CoC, contacts, communications, assets, finance, valuations, expenses, and contributions were tested."),
        ("D", "Documents", "Generation, upload, download, opening, version history, and post-restart access were tested."),
        ("E", "Firm views", "Dashboard, All Tasks, Calendar, Firm Masters, Profile, and Activity were reconciled."),
        ("F", "Security", "Wrong password, administrator functions, roles, and read-only restrictions were tested."),
        ("G", "Recovery", "A complete folder copy was opened successfully; optional same-user encrypted restore was tested safely."),
        ("H", "Legal review", "A professional reviewed seeded compliance rules and generated-document contents."),
        ("I", "Feedback", "Every failure, missing field, workflow change, and requested report was documented."),
        ("J", "Return package", "Casefile was stopped and the complete updated folder was copied under a new name."),
    ]
    add_table(doc, ["Ref", "Area", "Acceptance statement", "Status"], [(*row, "Pass / Fail / N.T.") for row in signoff], [720, 1800, 5400, 1440], font_size=8.8)
    add_callout(doc, "Release decision", "Do not use this test build as the sole production record until critical/high issues are resolved, compliance content is professionally verified, and a complete off-device backup method is implemented.", "Critical Box")
    h2(doc, "Sign-off")
    add_table(doc, ["Role", "Name", "Signature / confirmation", "Date"], [
        ("Primary office tester", "", "", ""),
        ("Insolvency Professional reviewer", "", "", ""),
        ("Project owner", "", "", ""),
    ], [2160, 2160, 3240, 1800], font_size=9.5)

    h2(doc, "Glossary")
    glossary = [
        ("CIRP", "Corporate Insolvency Resolution Process."),
        ("CoC", "Committee of Creditors."),
        ("NCLT", "National Company Law Tribunal."),
        ("NCLAT", "National Company Law Appellate Tribunal."),
        ("CIN", "Corporate Identity Number."),
        ("Local database", "The structured data file stored on the same computer/folder as this portable build."),
        ("Case isolation", "The rule that records belonging to one company must never appear in another company's workspace."),
        ("Archive", "Remove a record from active lists while retaining its history instead of permanently deleting it."),
        ("Role-based access", "Permissions determined by the signed-in user's assigned role."),
        ("DPAPI", "Windows Data Protection Application Programming Interface; Windows encryption tied to a user account."),
    ]
    add_table(doc, ["Term", "Meaning in this guide"], glossary, [2160, 7200], font_size=9.5)

    # Core properties and document behavior.
    doc.core_properties.title = "Casefile User Guide and Complete Test Plan"
    doc.core_properties.subject = "Portable office test build reference"
    doc.core_properties.author = "Casefile Project"
    doc.core_properties.keywords = "Casefile, insolvency, NCLT, office testing, user guide"
    settings = doc.settings.element
    update_fields = OxmlElement("w:updateFields")
    update_fields.set(qn("w:val"), "true")
    settings.append(update_fields)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT)
    return OUTPUT


if __name__ == "__main__":
    print(create_document())
