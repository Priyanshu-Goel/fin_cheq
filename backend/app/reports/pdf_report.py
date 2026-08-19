"""
Generates a professional PDF research note - the qualitative LLM-written
note up front, followed by the quantitative tables, styled to resemble a
sell-side research note layout (banner header, section rules, tables).
"""
import html
import re
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)

from app.models import AnalyzeResponse

styles = getSampleStyleSheet()
TITLE_STYLE = ParagraphStyle("TitleCustom", parent=styles["Title"], fontSize=18, spaceAfter=4)
SUBTITLE_STYLE = ParagraphStyle("Subtitle", parent=styles["Normal"], fontSize=10, textColor=colors.grey)
SECTION_STYLE = ParagraphStyle("Section", parent=styles["Heading2"], spaceBefore=14, spaceAfter=6)
SUBSECTION_STYLE = ParagraphStyle("Subsection", parent=styles["Heading3"], fontSize=11, spaceBefore=10, spaceAfter=4)
BODY_STYLE = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10, leading=14)


def _inline_markdown_to_reportlab(text: str) -> str:
    """
    Escapes the text for reportlab's strict mini-XML paragraph markup,
    then re-introduces just **bold** as <b>...</b> - the only inline
    markdown the LLM note actually uses. Escaping first (not after)
    matters: without it, a stray '<' or '&' in the note breaks reportlab's
    parser outright instead of just looking like unstyled text.
    """
    escaped = html.escape(text, quote=False)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)


def _markdown_to_flowables(markdown_text: str) -> list:
    """
    Lightweight markdown -> reportlab flowables for the LLM-written note.
    Not a full CommonMark implementation - just enough for the small,
    predictable subset note_generator.py's prompt actually produces
    (# / ## / ### headings, **bold** section titles on their own line,
    --- rules, blank-line-separated paragraphs) so literal '#' and '**'
    characters don't show up verbatim in the PDF the way they used to.
    """
    elements = []
    for block in markdown_text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if block in ("---", "***", "___"):
            elements.append(HRFlowable(
                width="100%", thickness=0.75, color=colors.HexColor("#CCCCCC"),
                spaceBefore=4, spaceAfter=8,
            ))
            continue

        heading_match = re.match(r"^(#{1,3})\s+(.*)", block)
        bold_line_match = None if heading_match else re.match(r"^\*\*(.+)\*\*$", block)
        if heading_match:
            hashes, heading_text = heading_match.groups()
            style = SECTION_STYLE if len(hashes) == 1 else SUBSECTION_STYLE
            elements.append(Paragraph(_inline_markdown_to_reportlab(heading_text), style))
        elif bold_line_match:
            elements.append(Paragraph(_inline_markdown_to_reportlab(bold_line_match.group(1)), SUBSECTION_STYLE))
        else:
            body_html = _inline_markdown_to_reportlab(block).replace("\n", "<br/>")
            elements.append(Paragraph(body_html, BODY_STYLE))
            elements.append(Spacer(1, 6))
    return elements


def _table(data: list[list], col_widths=None) -> Table:
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F5F5")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def build_pdf_report(analysis: AnalyzeResponse, research_note_text: str, output_path: str) -> str:
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        topMargin=20 * mm, bottomMargin=20 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
    )
    elements = []

    elements.append(Paragraph(f"{analysis.company_name} ({analysis.symbol})", TITLE_STYLE))
    elements.append(Paragraph(
        f"Equity Research Note &nbsp;|&nbsp; Exchange: {analysis.exchange} &nbsp;|&nbsp; "
        f"Risk Grade: {analysis.risk.risk_grade}",
        SUBTITLE_STYLE,
    ))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1F2937"), spaceBefore=8, spaceAfter=10))

    # Research note body (LLM-generated, grounded in retrieved sources)
    elements.extend(_markdown_to_flowables(research_note_text))

    # CAPM & Risk table
    elements.append(Paragraph("CAPM &amp; Risk Metrics", SECTION_STYLE))
    capm_data = [
        ["Metric", "Value"],
        ["Beta", f"{analysis.capm.beta}"],
        ["Cost of Equity (CAPM)", f"{analysis.capm.cost_of_equity:.2%}"],
        ["Annualized Volatility", f"{analysis.risk.annualized_volatility:.2%}"],
        ["Sharpe Ratio", f"{analysis.risk.sharpe_ratio}"],
        ["Max Drawdown", f"{analysis.risk.max_drawdown:.2%}"],
        ["Value at Risk (95%, 1-day)", f"{analysis.risk.value_at_risk_95:.2%}"],
    ]
    elements.append(_table(capm_data, col_widths=[260, 200]))
    elements.append(Spacer(1, 10))

    # Ratio trends table
    elements.append(Paragraph("Key Ratio Trends", SECTION_STYLE))
    ratio_data = [["Metric", "Trend"]] + [[r.metric, r.trend] for r in analysis.ratios]
    elements.append(_table(ratio_data, col_widths=[260, 200]))
    elements.append(Spacer(1, 10))

    # Red flags table
    elements.append(Paragraph("Red Flags", SECTION_STYLE))
    flag_data = [["Severity", "Title"]] + [[f.severity, f.title] for f in analysis.red_flags]
    elements.append(_table(flag_data, col_widths=[100, 360]))
    elements.append(Spacer(1, 10))

    # Backtest summary
    elements.append(Paragraph("Signal Backtest", SECTION_STYLE))
    bt = analysis.backtest
    bt_data = [
        ["Metric", "Value"],
        ["As-of Date", bt.as_of_date],
        ["Predicted Expected Return", f"{bt.predicted_expected_return:.2%}"],
        ["Actual Realized Return", f"{bt.actual_realized_return:.2%}"],
        ["Directional Hit", "Yes" if bt.directional_hit else "No"],
    ]
    elements.append(_table(bt_data, col_widths=[260, 200]))
    elements.append(Spacer(1, 14))

    elements.append(Paragraph(
        "<i>This document is a research aid generated by an automated pipeline "
        "combining public financial data with quantitative models. It is not "
        "investment advice.</i>",
        ParagraphStyle("Disclaimer", parent=styles["Normal"], fontSize=8, textColor=colors.grey),
    ))

    doc.build(elements)
    return output_path
