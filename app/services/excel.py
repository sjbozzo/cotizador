"""Genera un .xlsx sin dependencias: un libro es un ZIP con unos pocos XML.

Se escribe con `zipfile` y cadenas en línea (inlineStr), que es lo mínimo que
Excel, LibreOffice y Google Sheets abren sin quejarse. Una hoja por cotización.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from typing import Any


_INVALID_SHEET = set(r"[]:*?/\'")


def _sheet_name(name: str, used: set[str]) -> str:
    clean = "".join(" " if char in _INVALID_SHEET else char for char in name).strip() or "Cotización"
    clean = clean[:31]
    candidate = clean
    index = 2
    while candidate.casefold() in used:
        suffix = f" ({index})"
        candidate = clean[: 31 - len(suffix)] + suffix
        index += 1
    used.add(candidate.casefold())
    return candidate


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        # Excel rechaza los caracteres de control salvo tabulador y salto de línea.
        .translate({code: None for code in range(32) if code not in (9, 10, 13)})
    )


def _column_letter(index: int) -> str:
    letters = ""
    while index >= 0:
        letters = chr(ord("A") + index % 26) + letters
        index = index // 26 - 1
    return letters


def _cell(reference: str, value: Any, *, header: bool = False) -> str:
    style = ' s="1"' if header else ""
    if isinstance(value, bool):
        value = "Sí" if value else "No"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{reference}"{style}><v>{value}</v></c>'
    text = "" if value is None else str(value)
    if not text:
        return f'<c r="{reference}"{style}/>'
    return f'<c r="{reference}"{style} t="inlineStr"><is><t xml:space="preserve">{_escape(text)}</t></is></c>'


def _sheet_xml(rows: list[list[Any]], widths: list[int]) -> str:
    body = []
    for number, row in enumerate(rows, start=1):
        cells = "".join(
            _cell(f"{_column_letter(column)}{number}", value, header=number == 1)
            for column, value in enumerate(row)
        )
        body.append(f'<row r="{number}">{cells}</row>')
    columns = "".join(
        f'<col min="{index + 1}" max="{index + 1}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(widths)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>"
        f"<cols>{columns}</cols>"
        f"<sheetData>{''.join(body)}</sheetData>"
        "</worksheet>"
    )


_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
    '<fills count="2"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
    "</styleSheet>"
)


def write_workbook(sheets: list[tuple[str, list[list[Any]], list[int]]]) -> bytes:
    """`sheets` son tuplas (nombre, filas, anchos); la primera fila es el encabezado."""
    if not sheets:
        raise ValueError("Se necesita al menos una hoja")

    definitions = "".join(
        f'<sheet name="{_escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, (name, _rows, _widths) in enumerate(sheets, start=1)
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{definitions}</sheets></workbook>"
    )
    relations = "".join(
        f'<Relationship Id="rId{index}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, len(sheets) + 1)
    )
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, len(sheets) + 1)
    )

    buffer = io.BytesIO()
    stamp = datetime.now(timezone.utc).timetuple()[:6]
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:

        def write(name: str, content: str) -> None:
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content.encode("utf-8"))

        write(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            f"{overrides}</Types>",
        )
        write(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>',
        )
        write("xl/workbook.xml", workbook)
        write(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'{relations}<Relationship Id="rId{len(sheets) + 1}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/></Relationships>',
        )
        write("xl/styles.xml", _STYLES)
        for index, (_name, rows, widths) in enumerate(sheets, start=1):
            write(f"xl/worksheets/sheet{index}.xml", _sheet_xml(rows, widths))
    return buffer.getvalue()


# --- Armado de las hojas a partir de las cotizaciones -------------------------

def _extra_value(item: dict[str, Any], field: dict[str, str]) -> Any:
    value = item.get("extra_data", {}).get(field["key"])
    if field["type"] == "boolean":
        return "Sí" if value is True else "No" if value is False else ""
    if value is None:
        return ""
    return value


def quotation_sheet(quotation: dict[str, Any], items: list[dict[str, Any]]) -> tuple[str, list[list[Any]], list[int]]:
    extra_fields = quotation.get("extra_fields", [])
    after_price = [field for field in extra_fields if field["key"] == "tops"]
    remaining = [field for field in extra_fields if field["key"] != "tops"]
    header = ["Incluir", "Nombre", "Precio"]
    header += [field["label"] for field in after_price]
    header += ["Descripción", "Comentario", "País de origen"]
    header += [field["label"] for field in remaining]
    header += ["Link"]
    widths = [8, 40, 22] + [18] * len(after_price) + [46, 46, 16] + [18] * len(remaining) + [34]
    rows: list[list[Any]] = [header]
    for item in items:
        rows.append(
            [
                "Sí" if item.get("included") else "No",
                item.get("name", ""),
                item.get("price", ""),
                *[_extra_value(item, field) for field in after_price],
                item.get("description", ""),
                item.get("comment", ""),
                item.get("country", ""),
                *[_extra_value(item, field) for field in remaining],
                item.get("purchase_link", ""),
            ]
        )
    return quotation["name"], rows, widths


def build_workbook(
    quotations: list[dict[str, Any]],
    *,
    scope: str = "all",
    item_ids: set[str] | None = None,
) -> bytes:
    used: set[str] = set()
    sheets = []
    for quotation in quotations:
        items = quotation["items"]
        if item_ids is not None:
            items = [item for item in items if item["id"] in item_ids]
        elif scope == "included":
            items = [item for item in items if item["included"]]
        name, rows, widths = quotation_sheet(quotation, items)
        sheets.append((_sheet_name(name, used), rows, widths))
    return write_workbook(sheets)
