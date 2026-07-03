import re
import fitz  # PyMuPDF
import tempfile
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from docx import Document
from dotenv import load_dotenv, find_dotenv
from app.gateway.routes.llm_clients import openrouter_client, image_data_url, GEMINI_MODEL

load_dotenv(find_dotenv())

router = APIRouter(prefix="/api/vision", tags=["Vision Extraction"])


# Prompt is the bottleneck for structure quality. We ask Qwen-VL to emit
# Markdown with explicit HTML tags for subscript/superscript so the parser
# below can map them to true Word runs (run.font.subscript / .superscript).
EXTRACTION_PROMPT = """Extract every visible character from this PDF page and emit it as
clean Markdown that PRESERVES the page's document structure exactly.

Use these conventions strictly:
- Headings: '# H1', '## H2', '### H3'. Match the visual hierarchy on the page.
- Paragraphs: separate with a single blank line.
- Bullet lists: '- item' on its own line.
- Numbered lists: '1. item' on its own line.
- Bold: **text**.   Italic: *text*.
- Subscripts: wrap in <sub>...</sub>. Examples: H<sub>2</sub>O, x<sub>i</sub>, log<sub>10</sub>.
- Superscripts: wrap in <sup>...</sup>. Examples: E = mc<sup>2</sup>, x<sup>3</sup>, 10<sup>-3</sup>.
- Chemical formulas, math indices/exponents, isotope notation, footnote
  markers — ALL of these MUST use <sub>/<sup>. Never write H2O or x2.
- Math expressions: keep as written. Use Unicode symbols where they appear
  on the page (×, ÷, ±, √, π, θ, λ, μ, ≤, ≥, ≠, ≈, ∞, Σ, ∫, ∂).
- Tables: standard pipe tables (with the |---|---| separator row).
- Output the page text verbatim — DO NOT translate, paraphrase, summarise,
  or invent page numbers / headers / footers that aren't on the page.
- DO NOT wrap the output in ``` code fences.
"""


# ---------------------------------------------------------------------------
# Inline-run parsing — splits a Markdown line into formatted Word runs.
# Handles **bold**, *italic*, <sub>...</sub>, <sup>...</sup>.
# ---------------------------------------------------------------------------

_BOLD_ITALIC_RE = re.compile(r"(\*\*[^*]+\*\*|\*[^*\n]+\*)")
_SUB_SUP_RE = re.compile(r"(<sub>.+?</sub>|<sup>.+?</sup>)", re.IGNORECASE)


def _add_run(paragraph, text, *, bold=False, italic=False, subscript=False, superscript=False):
    if not text:
        return
    run = paragraph.add_run(text)
    if bold:
        run.bold = True
    if italic:
        run.italic = True
    if subscript:
        run.font.subscript = True
    if superscript:
        run.font.superscript = True


def _emit_subsup(paragraph, text: str, *, bold=False, italic=False) -> None:
    """Emit runs for a piece of plain text, expanding <sub>/<sup> tags."""
    if not text:
        return
    pos = 0
    for match in _SUB_SUP_RE.finditer(text):
        if match.start() > pos:
            _add_run(paragraph, text[pos:match.start()], bold=bold, italic=italic)
        token = match.group(0)
        inner = token[5:-6]
        if token.lower().startswith("<sub>"):
            _add_run(paragraph, inner, bold=bold, italic=italic, subscript=True)
        else:
            _add_run(paragraph, inner, bold=bold, italic=italic, superscript=True)
        pos = match.end()
    if pos < len(text):
        _add_run(paragraph, text[pos:], bold=bold, italic=italic)


def _emit_runs(paragraph, text: str) -> None:
    """Top-level inline parser. Splits on bold/italic, then on sub/sup."""
    if not text:
        return
    pos = 0
    for match in _BOLD_ITALIC_RE.finditer(text):
        if match.start() > pos:
            _emit_subsup(paragraph, text[pos:match.start()])
        token = match.group(0)
        if token.startswith("**"):
            _emit_subsup(paragraph, token[2:-2], bold=True)
        else:
            _emit_subsup(paragraph, token[1:-1], italic=True)
        pos = match.end()
    if pos < len(text):
        _emit_subsup(paragraph, text[pos:])


# ---------------------------------------------------------------------------
# Block-level Markdown → python-docx
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
_FENCE_OPEN_RE = re.compile(r"^```[a-zA-Z0-9]*\n?")
_FENCE_CLOSE_RE = re.compile(r"\n?```$")


def _flush_table(doc, rows):
    if not rows:
        return
    cleaned = [r for r in rows if not _TABLE_SEP_RE.match(r)]
    cells = [[c.strip() for c in row.strip().strip("|").split("|")] for row in cleaned]
    if not cells:
        return
    cols = max(len(r) for r in cells)
    table = doc.add_table(rows=len(cells), cols=cols)
    try:
        table.style = "Table Grid"  # built-in, always available
    except KeyError:
        pass
    for r, row in enumerate(cells):
        # Pad short rows so we don't IndexError on ragged tables
        row = row + [""] * (cols - len(row))
        for c, value in enumerate(row):
            cell = table.rows[r].cells[c]
            cell.text = ""
            _emit_runs(cell.paragraphs[0], value)


def _markdown_to_docx(doc, markdown_text: str) -> None:
    if not markdown_text:
        return
    table_buffer: list[str] = []

    def flush_table():
        nonlocal table_buffer
        if table_buffer:
            _flush_table(doc, table_buffer)
            table_buffer = []

    for raw in markdown_text.splitlines():
        line = raw.rstrip()

        if _TABLE_ROW_RE.match(line):
            table_buffer.append(line)
            continue
        else:
            flush_table()

        if not line.strip():
            continue

        m = _HEADING_RE.match(line)
        if m:
            level = min(len(m.group(1)), 9)
            doc.add_heading(m.group(2).strip(), level=level)
            continue

        m = _BULLET_RE.match(line)
        if m:
            p = doc.add_paragraph(style="List Bullet")
            _emit_runs(p, m.group(1).strip())
            continue

        m = _NUMBERED_RE.match(line)
        if m:
            p = doc.add_paragraph(style="List Number")
            _emit_runs(p, m.group(1).strip())
            continue

        p = doc.add_paragraph()
        _emit_runs(p, line.strip())

    flush_table()


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    text = _FENCE_OPEN_RE.sub("", text)
    text = _FENCE_CLOSE_RE.sub("", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/extract-to-word")
async def extract_pdf_to_word(file: UploadFile = File(...)):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Please upload a PDF.")

    try:
        pdf_content = await file.read()
        pdf_document = fitz.open(stream=pdf_content, filetype="pdf")

        doc = Document()

        for page_num in range(len(pdf_document)):
            page = pdf_document.load_page(page_num)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x for OCR clarity
            img_bytes = pix.tobytes("jpeg")

            response = openrouter_client.chat.completions.create(
                model=GEMINI_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": EXTRACTION_PROMPT},
                            {"type": "image_url", "image_url": {"url": image_data_url(img_bytes, "image/jpeg")}},
                        ],
                    }
                ],
                temperature=0.0,
            )

            raw = response.choices[0].message.content if response.choices else ""
            page_markdown = _strip_code_fences(raw or "")
            _markdown_to_docx(doc, page_markdown)

        temp_word = tempfile.NamedTemporaryFile(delete=False, suffix=".docx")
        doc.save(temp_word.name)
        temp_word.close()

        return FileResponse(
            path=temp_word.name,
            filename=f"{file.filename.split('.')[0]}_converted.docx",
            media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )

    except Exception as e:
        return {"status": "error", "message": str(e)}
