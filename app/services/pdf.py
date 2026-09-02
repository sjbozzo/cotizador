from __future__ import annotations

import html
import io
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _register_fonts() -> tuple[str, str]:
    regular = Path("C:/Windows/Fonts/arial.ttf")
    bold = Path("C:/Windows/Fonts/arialbd.ttf")
    if regular.exists() and bold.exists():
        if "CotizadorSans" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("CotizadorSans", regular))
            pdfmetrics.registerFont(TTFont("CotizadorSans-Bold", bold))
        return "CotizadorSans", "CotizadorSans-Bold"
    return "Helvetica", "Helvetica-Bold"


def _plain(value: Any) -> str:
    return ("" if value is None else str(value)).replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", "-")


def _paragraph(value: Any, style: ParagraphStyle) -> Paragraph:
    escaped = html.escape(_plain(value)).replace("\n", "<br/>")
    return Paragraph(escaped, style)


def build_pdf(
    quotation: dict[str, Any],
    *,
    scope: str = "included",
    item_ids: set[str] | None = None,
) -> bytes:
    regular_font, bold_font = _register_fonts()
    page_size = landscape(A4)
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=page_size,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=19 * mm,
        bottomMargin=16 * mm,
        title=quotation["name"],
        author="Cotizador Edge AI",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "QuoteTitle",
        parent=styles["Title"],
        fontName=bold_font,
        fontSize=19,
        leading=22,
        textColor=colors.HexColor("#171717"),
        spaceAfter=4 * mm,
        alignment=0,
    )
    meta_style = ParagraphStyle(
        "QuoteMeta",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#666666"),
    )
    body_style = ParagraphStyle(
        "QuoteBody",
        parent=styles["BodyText"],
        fontName=regular_font,
        fontSize=7.8,
        leading=10,
        textColor=colors.HexColor("#222222"),
    )
    name_style = ParagraphStyle(
        "QuoteName",
        parent=body_style,
        fontName=bold_font,
        fontSize=8.2,
        leading=10.4,
    )
    header_style = ParagraphStyle(
        "QuoteHeader",
        parent=body_style,
        fontName=bold_font,
        fontSize=7.4,
        textColor=colors.HexColor("#333333"),
    )

    items = quotation["items"]
    if item_ids is not None:
        items = [item for item in items if item["id"] in item_ids]
    elif scope == "included":
        items = [item for item in items if item["included"]]

    story = [
        _paragraph(quotation["name"], title_style),
        _paragraph(
            f"Fecha: {quotation['quote_date']}  |  {len(items)} elementos  |  Revisión {quotation['revision']}",
            meta_style,
        ),
    ]
    if quotation.get("description"):
        story.extend([Spacer(1, 2 * mm), _paragraph(quotation["description"], meta_style)])
    story.append(Spacer(1, 5 * mm))

    if not items:
        story.append(_paragraph("No hay elementos incluidos en esta cotización.", body_style))
    else:
        headers = ["#", "Producto", "Precio", "País de origen", "Campos extra", "Comentario"]
        data: list[list[Any]] = [[_paragraph(label, header_style) for label in headers]]
        field_map = {field["key"]: field for field in quotation.get("extra_fields", [])}
        for index, item in enumerate(items, start=1):
            product_parts = [f"<b>{html.escape(_plain(item['name']))}</b>"]
            if item.get("description"):
                product_parts.append(html.escape(_plain(item["description"])))
            if item.get("purchase_link"):
                product_parts.append(f'<link href="{html.escape(item["purchase_link"], quote=True)}" color="#333333">Abrir enlace</link>')
            detail_lines = []
            for key, value in item.get("extra_data", {}).items():
                if value is None or value == "":
                    continue
                definition = field_map.get(key, {"label": key.replace("_", " ").capitalize(), "type": "text"})
                if definition.get("type") == "boolean":
                    value = "Sí" if value is True else "No" if value is False else value
                detail_lines.append(f"<b>{html.escape(_plain(definition['label']))}:</b> {html.escape(_plain(value))}")
            data.append(
                [
                    _paragraph(index, body_style),
                    Paragraph("<br/>".join(product_parts), body_style),
                    _paragraph(item.get("price", ""), name_style),
                    _paragraph(item.get("country", ""), body_style),
                    Paragraph("<br/>".join(detail_lines) or "-", body_style),
                    _paragraph(item.get("comment", ""), body_style),
                ]
            )
        table = LongTable(
            data,
            repeatRows=1,
            colWidths=[8 * mm, 69 * mm, 29 * mm, 29 * mm, 58 * mm, 70 * mm],
            hAlign="LEFT",
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F3F0")),
                    ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#171717")),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#DDDDD8")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FBFBF9")]),
                ]
            )
        )
        story.append(table)

    footer_style = ParagraphStyle(
        "Footer",
        fontName=regular_font,
        fontSize=7.5,
        textColor=colors.HexColor("#777777"),
        alignment=TA_RIGHT,
    )

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#E5E5E0"))
        canvas.line(14 * mm, 11 * mm, page_size[0] - 14 * mm, 11 * mm)
        page_text = Paragraph(f"Cotizador Edge AI &nbsp;&nbsp;·&nbsp;&nbsp; Página {doc.page}", footer_style)
        page_text.wrapOn(canvas, 70 * mm, 6 * mm)
        page_text.drawOn(canvas, page_size[0] - 84 * mm, 5 * mm)
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
