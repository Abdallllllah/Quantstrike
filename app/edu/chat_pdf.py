"""Render a conversation as a PDF the student can keep."""


def render_chat_pdf(rows: list) -> bytes:
    """Render a conversation into a nicely formatted PDF (reportlab)."""
    from io import BytesIO
    from xml.sax.saxutils import escape
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable

    green = colors.HexColor("#2e7d32")
    green_dark = colors.HexColor("#1b5e20")
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Ct", parent=styles["Title"], textColor=green, fontSize=22, spaceAfter=2)
    meta_style = ParagraphStyle("Cm", parent=styles["Normal"], textColor=colors.grey, fontSize=9, spaceAfter=2)
    you_label = ParagraphStyle("Cyl", parent=styles["Normal"], textColor=green_dark, fontName="Helvetica-Bold", fontSize=9, spaceBefore=12)
    car_label = ParagraphStyle("Ccl", parent=styles["Normal"], textColor=green, fontName="Helvetica-Bold", fontSize=9, spaceBefore=12)
    body_style = ParagraphStyle("Cb", parent=styles["Normal"], fontSize=11, leading=15.5, spaceBefore=2)

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title="Carati chat",
    )
    story = [
        Paragraph("Carati", title_style),
        Paragraph("GCE A-Level study chat", meta_style),
        Paragraph(f"{len(rows)} messages", meta_style),
        Spacer(1, 6),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#d6e8d6")),
    ]
    for r in rows:
        content = escape((r.get("content") or "").strip()).replace("\n", "<br/>")
        if not content:
            continue
        if r.get("role") == "user":
            story.append(Paragraph("You", you_label))
        else:
            story.append(Paragraph("Carati", car_label))
        story.append(Paragraph(content, body_style))
    doc.build(story)
    return buf.getvalue()
