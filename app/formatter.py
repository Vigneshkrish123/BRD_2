import io
import datetime
from loguru import logger

from docx import Document
from docx.shared import Pt, RGBColor, Inches, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement


# ── Colours ───────────────────────────────────────────────────────────────────

NAVY   = "1F3864"
BLUE   = "2E75B6"
LIGHT  = "EBF3FB"
WHITE  = "FFFFFF"
TEXT   = "2C2C2C"
BORDER = "C9D8EA"


# ── Low-level XML helpers ─────────────────────────────────────────────────────

def _set_cell_bg(cell, hex_color: str):
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd  = OxmlElement("w:shd")
    shd.set(qn("w:val"),   "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"),  hex_color)
    tcPr.append(shd)


def _set_cell_border(cell, hex_color: str = BORDER):
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcBorders = OxmlElement("w:tcBorders")
    for side in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:val"),   "single")
        el.set(qn("w:sz"),    "4")
        el.set(qn("w:color"), hex_color)
        tcBorders.append(el)
    tcPr.append(tcBorders)


def _cell_padding(cell, top=80, bottom=80, left=120, right=120):
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    mar  = OxmlElement("w:tcMar")
    for side, val in (("top", top), ("bottom", bottom), ("left", left), ("right", right)):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:w"),    str(val))
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tcPr.append(mar)


def _heading_border(paragraph):
    pPr  = paragraph._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bot  = OxmlElement("w:bottom")
    bot.set(qn("w:val"),   "single")
    bot.set(qn("w:sz"),    "6")
    bot.set(qn("w:color"), BLUE)
    bot.set(qn("w:space"), "1")
    pBdr.append(bot)
    pPr.append(pBdr)


# ── Document helpers ──────────────────────────────────────────────────────────

def _setup_styles(doc: Document):
    doc.styles["Normal"].font.name = "Arial"
    doc.styles["Normal"].font.size = Pt(11)

    h1 = doc.styles["Heading 1"]
    h1.font.name = "Arial"
    h1.font.size = Pt(14)
    h1.font.bold = True
    h1.font.color.rgb = RGBColor.from_string(BLUE)

    h2 = doc.styles["Heading 2"]
    h2.font.name = "Arial"
    h2.font.size = Pt(12)
    h2.font.bold = True
    h2.font.color.rgb = RGBColor.from_string(NAVY)


def _add_heading1(doc, text):
    p = doc.add_heading(text, level=1)
    _heading_border(p)
    p.paragraph_format.space_before = Pt(16)
    p.paragraph_format.space_after  = Pt(6)
    return p


def _add_heading2(doc, text):
    p = doc.add_heading(text, level=2)
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after  = Pt(4)
    return p


def _add_body(doc, text):
    p = doc.add_paragraph(str(text or ""))
    p.paragraph_format.space_after = Pt(6)
    for run in p.runs:
        run.font.name = "Arial"
        run.font.size = Pt(11)
        run.font.color.rgb = RGBColor.from_string(TEXT)
    return p


def _add_bullet(doc, text):
    p = doc.add_paragraph(str(text or ""), style="List Bullet")
    p.paragraph_format.space_after = Pt(3)
    for run in p.runs:
        run.font.name = "Arial"
        run.font.size = Pt(11)
    return p


def _safe(val, fallback="—") -> str:
    return str(val) if val not in (None, "", [], {}) else fallback


# ── UC-specific helpers ───────────────────────────────────────────────────────

def _add_uc_field(doc, label: str, value: str):
    """Bold label followed by regular value on the same line."""
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    run_label = p.add_run(f"{label}: ")
    run_label.font.bold = True
    run_label.font.name = "Arial"
    run_label.font.size = Pt(11)
    run_val = p.add_run(str(value or ""))
    run_val.font.name = "Arial"
    run_val.font.size = Pt(11)
    run_val.font.color.rgb = RGBColor.from_string(TEXT)
    return p


def _add_uc_label(doc, label: str):
    """Bold section label (e.g. 'Pre-Condition:')."""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after  = Pt(2)
    run = p.add_run(label)
    run.font.bold = True
    run.font.name = "Arial"
    run.font.size = Pt(11)
    return p


def _add_numbered_para(doc, num: int, text: str):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.left_indent = Cm(0.5)
    run = p.add_run(f"{num}. {str(text or '')}")
    run.font.name = "Arial"
    run.font.size = Pt(11)
    run.font.color.rgb = RGBColor.from_string(TEXT)
    return p


# ── Table builder ─────────────────────────────────────────────────────────────

def _add_table(doc, headers: list, col_widths_cm: list, rows: list):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"

    hdr_cells = table.rows[0].cells
    for i, cell in enumerate(hdr_cells):
        cell.width = Cm(col_widths_cm[i])
        cell.text  = headers[i]
        _set_cell_bg(cell, NAVY)
        _set_cell_border(cell, NAVY)
        _cell_padding(cell)
        run = cell.paragraphs[0].runs[0]
        run.font.bold      = True
        run.font.size      = Pt(10)
        run.font.color.rgb = RGBColor.from_string(WHITE)
        run.font.name      = "Arial"

    for ri, row_data in enumerate(rows):
        row_cells = table.add_row().cells
        bg = LIGHT if ri % 2 == 0 else WHITE
        for i, cell in enumerate(row_cells):
            cell.width = Cm(col_widths_cm[i])
            val = _safe(row_data[i] if i < len(row_data) else "")
            cell.text = val
            _set_cell_bg(cell, bg)
            _set_cell_border(cell)
            _cell_padding(cell)
            runs = cell.paragraphs[0].runs
            run  = runs[0] if runs else cell.paragraphs[0].add_run(val)
            run.font.size      = Pt(10)
            run.font.name      = "Arial"
            run.font.color.rgb = RGBColor.from_string(TEXT)

    doc.add_paragraph()


# ── Section builders ──────────────────────────────────────────────────────────

def _title_page(doc, brd):
    info  = brd.get("document_info", {})
    today = datetime.date.today().strftime("%d %B %Y")

    for _ in range(7):
        doc.add_paragraph()

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("BUSINESS REQUIREMENTS DOCUMENT")
    run.font.name = "Arial"; run.font.size = Pt(26)
    run.font.bold = True; run.font.color.rgb = RGBColor.from_string(BLUE)

    proj = doc.add_paragraph()
    proj.alignment = WD_ALIGN_PARAGRAPH.CENTER
    proj.paragraph_format.space_after = Pt(20)
    run2 = proj.add_run(_safe(info.get("project_name"), "Project"))
    run2.font.name = "Arial"; run2.font.size = Pt(18)
    run2.font.color.rgb = RGBColor.from_string(NAVY)

    _add_table(doc, ["Field", "Details"], [4, 12], [
        ["Version",     _safe(info.get("version"),     "1.0")],
        ["Status",      _safe(info.get("status"),      "Draft")],
        ["Date",        today],
        ["Prepared By", _safe(info.get("prepared_by"), "BRD Agent (AI-assisted)")],
    ])
    doc.add_page_break()


def _section_introduction(doc, brd):
    _add_heading1(doc, "1. Introduction")
    _add_body(doc, brd.get("introduction", ""))


def _section_objectives(doc, brd):
    _add_heading1(doc, "2. Business Objective")
    for obj in brd.get("business_objectives", []):
        obj_id    = _safe(obj.get("id"), "")
        obj_title = _safe(obj.get("title"), "")
        obj_desc  = _safe(obj.get("description"), "")

        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(8)
        p.paragraph_format.space_after  = Pt(4)
        run_title = p.add_run(f"{obj_id}. {obj_title}" if obj_title else obj_id)
        run_title.font.bold  = True
        run_title.font.name  = "Arial"
        run_title.font.size  = Pt(11)
        run_title.font.color.rgb = RGBColor.from_string(NAVY)

        _add_body(doc, obj_desc)


def _section_stakeholders(doc, brd):
    _add_heading1(doc, "3. Stakeholders")
    rows = [[s.get("role", ""), s.get("responsibility", "")]
            for s in brd.get("stakeholders", [])]
    _add_table(doc, ["Role", "Responsibility"], [5, 11], rows)


def _section_scope(doc, brd):
    scope = brd.get("scope", {})
    _add_heading1(doc, "4. Scope")

    _add_heading2(doc, "4.1  In-Scope")
    in_scope_rows = [
        [s.get("module", ""), s.get("feature", ""), s.get("description", ""), s.get("key_outcomes", "")]
        for s in scope.get("in_scope", [])
    ]
    if in_scope_rows:
        _add_table(doc, ["Module", "Feature", "Description", "Key Outcomes"], [3.5, 3.5, 5, 4], in_scope_rows)
    else:
        _add_body(doc, "No in-scope items defined.")

    _add_heading2(doc, "4.2  Out of Scope")
    out_scope_rows = [
        [s.get("item", ""), s.get("description", "")]
        for s in scope.get("out_of_scope", [])
    ]
    if out_scope_rows:
        _add_table(doc, ["Items", "Description"], [5, 11], out_scope_rows)
    else:
        _add_body(doc, "No out-of-scope items defined.")


def _section_assumptions(doc, brd):
    _add_heading1(doc, "5. Assumptions")
    rows = [
        [str(a.get("sr_no", i + 1)), a.get("assumption", ""), a.get("impact_if_changed", "")]
        for i, a in enumerate(brd.get("assumptions", []))
    ]
    if rows:
        _add_table(doc, ["Sr. No.", "Assumption", "Impact if Changed"], [1.5, 8, 6.5], rows)
    else:
        _add_body(doc, "No assumptions defined.")


def _section_use_cases(doc, brd):
    _add_heading1(doc, "6. Use Cases")

    for uc in brd.get("use_cases", []):
        uc_id   = _safe(uc.get("id"), "UC_XX")
        uc_name = _safe(uc.get("name"), "")
        _add_heading2(doc, f"{uc_id}: {uc_name}")

        _add_uc_field(doc, "Description", uc.get("description", ""))
        _add_uc_field(doc, "Role", uc.get("role", ""))

        pre = uc.get("pre_conditions", [])
        if pre:
            _add_uc_label(doc, "Pre-Condition:")
            for i, cond in enumerate(pre, 1):
                _add_numbered_para(doc, i, cond)

        post = uc.get("post_conditions", [])
        if post:
            _add_uc_label(doc, "Post-Condition:")
            for i, cond in enumerate(post, 1):
                _add_numbered_para(doc, i, cond)

        main_flow = uc.get("main_flow", [])
        if main_flow:
            _add_uc_label(doc, "Main Flow:")
            rows = [
                [str(s.get("step", "")), s.get("user_action", ""), s.get("system_action", "")]
                for s in main_flow
            ]
            _add_table(doc, ["Step", "User Action", "System Action"], [1.5, 7.5, 7], rows)

        business_rules = uc.get("business_rules", [])
        if business_rules:
            _add_uc_label(doc, "Business Rules:")
            rows = [
                [str(r.get("sr_no", "")), r.get("rule", "")]
                for r in business_rules
            ]
            _add_table(doc, ["Sr. No.", "Business Rule"], [2, 14], rows)

        exceptional_flow = uc.get("exceptional_flow", [])
        if exceptional_flow:
            _add_uc_label(doc, "Exceptional Flow:")
            rows = [
                [str(e.get("sr_no", "")), e.get("exception", ""), e.get("error_message", "")]
                for e in exceptional_flow
            ]
            _add_table(doc, ["Sr. No.", "Exception", "Error Message"], [2, 7.5, 6.5], rows)

        doc.add_paragraph()


def _section_notifications(doc, brd):
    notifications = brd.get("notifications", [])
    if not notifications:
        return
    _add_heading1(doc, "7. Notification / Communication")
    rows = [
        [n.get("event", ""), n.get("trigger", ""), n.get("channel", ""), n.get("message_template", "")]
        for n in notifications
    ]
    _add_table(doc, ["Event", "Trigger", "Channel", "Message Template"], [3.5, 3.5, 2, 7], rows)


def _section_nfr(doc, brd):
    _add_heading1(doc, "8. Non-Functional Requirements")
    rows = [
        [n.get("id", ""), n.get("category", ""), n.get("description", ""), n.get("priority", "")]
        for n in brd.get("non_functional_requirements", [])
    ]
    if rows:
        _add_table(doc, ["ID", "Category", "Description", "Priority"], [1.5, 3, 9.5, 2], rows)
    else:
        _add_body(doc, "No non-functional requirements defined.")


def _section_adoption(doc, brd):
    criteria = brd.get("adoption_criteria", [])
    if not criteria:
        return
    _add_heading1(doc, "9. Adoption Matrix / Criteria")
    rows = [
        [c.get("success_criteria", ""), c.get("metrics_kpis", "")]
        for c in criteria
    ]
    _add_table(doc, ["Success Criteria", "Metrics or KPIs"], [8, 8], rows)


# ── Public API ────────────────────────────────────────────────────────────────

def format_docx(brd_data: dict) -> bytes:
    doc = Document()
    _setup_styles(doc)

    for section in doc.sections:
        section.top_margin    = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin   = Inches(1)
        section.right_margin  = Inches(1)

    logger.info("Formatter | building document...")

    try:
        _title_page(doc, brd_data)
        _section_introduction(doc, brd_data)
        _section_objectives(doc, brd_data)
        _section_stakeholders(doc, brd_data)
        _section_scope(doc, brd_data)
        _section_assumptions(doc, brd_data)
        _section_use_cases(doc, brd_data)
        _section_notifications(doc, brd_data)
        _section_nfr(doc, brd_data)
        _section_adoption(doc, brd_data)
    except Exception as e:
        logger.error(f"Formatter | document build failed: {e}")
        raise RuntimeError(f"Document generation failed: {e}") from e

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    logger.info("Formatter | document built successfully")
    return buffer.getvalue()
