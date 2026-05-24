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
    p.paragraph_format.space_before = Pt(10)
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


def _section_exec_summary(doc, brd):
    _add_heading1(doc, "1. Executive Summary")
    _add_body(doc, brd.get("executive_summary"))
    doc.add_page_break()


def _section_overview(doc, brd):
    _add_heading1(doc, "2. Project Overview")
    _add_body(doc, brd.get("project_overview"))


def _section_scope(doc, brd):
    scope = brd.get("scope", {})
    _add_heading1(doc, "3. Scope")
    _add_heading2(doc, "3.1  In Scope")
    for item in scope.get("in_scope", []):
        _add_bullet(doc, item)
    _add_heading2(doc, "3.2  Out of Scope")
    for item in scope.get("out_of_scope", []):
        _add_bullet(doc, item)


def _section_stakeholders(doc, brd):
    _add_heading1(doc, "4. Stakeholders")
    rows = [[s.get("name"), s.get("role"), s.get("responsibility")]
            for s in brd.get("stakeholders", [])]
    _add_table(doc, ["Name", "Role", "Responsibility"], [4, 4, 8], rows)


def _section_objectives(doc, brd):
    _add_heading1(doc, "5. Business Objectives")
    rows = [[o.get("id"), o.get("description"), o.get("success_criteria")]
            for o in brd.get("business_objectives", [])]
    _add_table(doc, ["ID", "Description", "Success Criteria"], [2, 7, 7], rows)


def _section_fr(doc, brd):
    _add_heading1(doc, "6. Functional Requirements")
    rows = [[f.get("id"), f.get("description"), f.get("priority"), f.get("acceptance_criteria")]
            for f in brd.get("functional_requirements", [])]
    _add_table(doc, ["ID", "Description", "Priority", "Acceptance Criteria"], [1.5, 5.5, 2, 7], rows)


def _section_nfr(doc, brd):
    _add_heading1(doc, "7. Non-Functional Requirements")
    rows = [[n.get("id"), n.get("category"), n.get("description"), n.get("priority")]
            for n in brd.get("non_functional_requirements", [])]
    _add_table(doc, ["ID", "Category", "Description", "Priority"], [1.5, 3, 9.5, 2], rows)


def _section_assumptions(doc, brd):
    _add_heading1(doc, "8. Assumptions & Constraints")
    _add_heading2(doc, "8.1  Assumptions")
    for item in brd.get("assumptions", []):
        _add_bullet(doc, item)
    _add_heading2(doc, "8.2  Constraints")
    for item in brd.get("constraints", []):
        _add_bullet(doc, item)


def _section_risks(doc, brd):
    _add_heading1(doc, "9. Risks")
    rows = [[r.get("id"), r.get("description"), r.get("impact"), r.get("mitigation")]
            for r in brd.get("risks", [])]
    _add_table(doc, ["ID", "Description", "Impact", "Mitigation"], [1.5, 5, 2, 7.5], rows)


def _section_open_questions(doc, brd):
    _add_heading1(doc, "10. Open Questions")
    rows = [[q.get("id"), q.get("question"), q.get("owner"), q.get("target_date")]
            for q in brd.get("open_questions", [])]
    _add_table(doc, ["ID", "Question", "Owner", "Target Date"], [1.5, 8.5, 3.5, 2.5], rows)


def _section_action_items(doc, brd):
    _add_heading1(doc, "11. Action Items")
    rows = [[a.get("id"), a.get("action"), a.get("owner"), a.get("due_date")]
            for a in brd.get("action_items", [])]
    _add_table(doc, ["ID", "Action", "Owner", "Due Date"], [1.5, 8.5, 3.5, 2.5], rows)


# ── Public API ────────────────────────────────────────────────────────────────

def format_docx(brd_data: dict) -> bytes:
    """
    Convert a BRD JSON dict into a styled .docx and return the raw bytes.

    Nothing is written to disk — the caller receives bytes directly and passes
    them to st.download_button(). This eliminates the outputs/ directory entirely.

    Args:
        brd_data: output of generator.generate() after model_dump()

    Returns:
        Raw .docx bytes ready for st.download_button(data=...).

    Raises:
        RuntimeError: if document construction fails, with a clean message.
    """
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
        _section_exec_summary(doc, brd_data)
        _section_overview(doc, brd_data)
        _section_scope(doc, brd_data)
        _section_stakeholders(doc, brd_data)
        _section_objectives(doc, brd_data)
        _section_fr(doc, brd_data)
        _section_nfr(doc, brd_data)
        _section_assumptions(doc, brd_data)
        _section_risks(doc, brd_data)
        _section_open_questions(doc, brd_data)
        _section_action_items(doc, brd_data)
    except Exception as e:
        logger.error(f"Formatter | document build failed: {e}")
        raise RuntimeError(f"Document generation failed: {e}") from e

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    logger.info("Formatter | document built successfully")
    return buffer.getvalue()