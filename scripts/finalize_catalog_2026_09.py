"""Aplica la auditoría final de las cinco cotizaciones visibles.

La investigación reproducible vive en
``scripts/auditoria/2026-09-03-final.json``. Antes de escribir se descargan y
decodifican todas las fotografías, se validan las reglas de selección y se crea
un respaldo SQLite consistente. La actualización de cotizaciones, ítems e
imágenes se ejecuta en una sola transacción.

Uso::

    uv run python -m scripts.finalize_catalog_2026_09 --dry-run
    uv run python -m scripts.finalize_catalog_2026_09
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sqlite3
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PIL import Image, ImageOps, UnidentifiedImageError

from app import db
from app.services.excel import build_workbook
from app.services.exports import build_multi_share_payload, render_standalone_html


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "auditoria" / "2026-09-03-final.json"
DATABASE = ROOT / "data" / "cotizador.db"
BACKUP = ROOT / "data" / "cotizador.backup-20260903-final.db"
OUTPUT = ROOT / "output"
RESERVE = "reserva-fuera-de-categorias"
ACTIVE_QUOTES = (
    "aceleradores-usb-yolo",
    "edge-ai-aceleradores-m2",
    "pcs-industriales-m2-ai",
    "edge-ai-plataformas-host",
    "microcomputadores",
)
COMMON_REQUIRED = {
    "marca",
    "pagina_fabricante",
    "durabilidad_confiabilidad",
    "disponibilidad_chile",
    "verificado_el",
}
USER_AGENT = "Mozilla/5.0 (compatible; CotizadorEdgeAI/1.0; product-photo-audit)"
MAX_SOURCE_BYTES = 15 * 1024 * 1024
MAX_DIMENSION = 1280


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _valid_url(value: Any) -> bool:
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _load_manifest() -> dict[str, Any]:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if payload.get("audit_date") != "2026-09-03":
        raise ValueError("La fecha de auditoría del manifiesto no es 2026-09-03")
    return payload


def _validate_manifest(payload: dict[str, Any]) -> None:
    quotations = payload.get("quotations") or []
    if tuple(entry.get("id") for entry in quotations) != ACTIVE_QUOTES:
        raise ValueError("El manifiesto debe contener las cinco cotizaciones, en orden")

    decision_ids = [entry.get("id") for entry in payload.get("decisions", [])]
    initial_ids = payload.get("initial_active_item_ids") or []
    if not initial_ids or len(initial_ids) != len(set(initial_ids)):
        raise ValueError("initial_active_item_ids está vacío o tiene duplicados")
    if set(decision_ids) != set(initial_ids) or len(decision_ids) != len(initial_ids):
        raise ValueError("Las decisiones no cubren exactamente todos los ítems iniciales")
    for decision in payload["decisions"]:
        if decision.get("verdict") not in {"keep", "discard"}:
            raise ValueError(f"{decision.get('id')}: veredicto inválido")
        if not all(str(decision.get(key) or "").strip() for key in ("quotation_id", "name", "reason")):
            raise ValueError(f"{decision.get('id')}: decisión incompleta")
        sources = decision.get("sources") or []
        if not sources or any(not _valid_url(source) for source in sources):
            raise ValueError(f"{decision.get('id')}: fuentes de decisión incompletas")

    selected_ids: set[str] = set()
    original_quote_by_item = {
        str(decision["id"]): str(decision["quotation_id"])
        for decision in payload.get("decisions", [])
    }
    for quotation in quotations:
        items = quotation.get("items") or []
        if not 1 <= len(items) <= 8:
            raise ValueError(f"{quotation['id']}: debe conservar entre 1 y 8 ítems")
        genuinely_new = [item for item in items if str(item.get("id")) not in original_quote_by_item]
        if len(genuinely_new) > 1:
            raise ValueError(f"{quotation['id']}: hay más de un dispositivo nuevo")
        fields = quotation.get("fields") or []
        field_keys = [field.get("key") for field in fields]
        if len(field_keys) != len(set(field_keys)):
            raise ValueError(f"{quotation['id']}: columnas repetidas")
        missing_definitions = COMMON_REQUIRED - set(field_keys)
        if missing_definitions:
            raise ValueError(f"{quotation['id']}: faltan columnas {sorted(missing_definitions)}")
        if quotation["id"] == "edge-ai-aceleradores-m2" and "requiere_host" in field_keys:
            raise ValueError("La cotización M.2 todavía declara requiere_host")

        brands: Counter[str] = Counter()
        countries: Counter[str] = Counter()
        for item in items:
            item_id = str(item.get("id") or "")
            if not item_id or item_id in selected_ids:
                raise ValueError(f"ID de ítem vacío o repetido: {item_id!r}")
            selected_ids.add(item_id)
            expected_new = item_id not in original_quote_by_item
            if item.get("new") is not expected_new:
                raise ValueError(f"{item_id}: indicador new inconsistente")
            if not expected_new and original_quote_by_item[item_id] != quotation["id"]:
                raise ValueError(f"{item_id}: fue trasladado entre cotizaciones durante la auditoría")
            for key in ("name", "country", "price", "purchase_link", "description", "comment", "photo_url"):
                if not str(item.get(key) or "").strip():
                    raise ValueError(f"{item_id}: falta {key}")
            if not _valid_url(item["purchase_link"]) or not _valid_url(item["photo_url"]):
                raise ValueError(f"{item_id}: enlace de compra o foto inválido")
            if re.search(r"(?:≈|~|aprox|por confirmar|sin precio|desde\s+US\$)", item["price"], flags=re.I):
                raise ValueError(f"{item_id}: el precio no es puntual: {item['price']}")
            extra = item.get("extra_data") or {}
            missing = [key for key in field_keys if key not in extra or extra[key] in (None, "")]
            if missing:
                raise ValueError(f"{item_id}: faltan valores para {missing}")
            if not _valid_url(extra["pagina_fabricante"]):
                raise ValueError(f"{item_id}: página de fabricante inválida")
            score = extra["durabilidad_confiabilidad"]
            if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 10:
                raise ValueError(f"{item_id}: nota fuera de 1–10")
            brands[str(extra["marca"]).casefold()] += 1
            countries[str(item["country"]).casefold()] += 1
            if extra["verificado_el"] != payload["audit_date"]:
                raise ValueError(f"{item_id}: fecha de verificación inconsistente")
            if "chile" not in str(extra["disponibilidad_chile"]).casefold():
                raise ValueError(f"{item_id}: disponibilidad no confirma Chile")
            sources = item.get("sources") or []
            if len(sources) < 2 or any(not _valid_url(source) for source in sources):
                raise ValueError(f"{item_id}: registro de fuentes insuficiente")
            if quotation["id"] == "edge-ai-aceleradores-m2" and "requiere_host" in extra:
                raise ValueError(f"{item_id}: conserva requiere_host en extra_data")
        if brands and max(brands.values()) > 2:
            raise ValueError(f"{quotation['id']}: más de dos ítems de una marca: {brands}")
        if countries and max(countries.values()) > 4:
            raise ValueError(f"{quotation['id']}: más de cuatro ítems de un país: {countries}")

    by_id = {entry["id"]: entry for entry in quotations}
    required_by_quote = {
        "aceleradores-usb-yolo": {"tops"},
        "edge-ai-aceleradores-m2": {"tops", "formato"},
        "pcs-industriales-m2-ai": {"procesador", "ram", "m2_acelerador", "almacenamiento_separado"},
        "edge-ai-plataformas-host": {"tops", "procesador", "ram"},
        "microcomputadores": {"procesador", "ram", "tops"},
    }
    for quotation_id, required in required_by_quote.items():
        keys = {field["key"] for field in by_id[quotation_id]["fields"]}
        if not required <= keys:
            raise ValueError(f"{quotation_id}: faltan columnas específicas {sorted(required - keys)}")

    amd_count = sum(
        1
        for item in by_id["edge-ai-plataformas-host"]["items"]
        if "AMD" in str(item["extra_data"].get("procesador", "")).upper()
        or str(item["extra_data"].get("marca", "")).upper() == "AMD"
    )
    if amd_count < 2:
        raise ValueError("PC con NPU debe conservar al menos dos dispositivos AMD")

    expected_kept = {item_id for item_id in selected_ids if item_id in original_quote_by_item}
    declared_kept = {
        str(decision["id"])
        for decision in payload["decisions"]
        if decision["verdict"] == "keep"
    }
    if declared_kept != expected_kept:
        raise ValueError("Los veredictos keep no coinciden con los ítems originales conservados")


def _download_photo(url: str) -> tuple[bytes, str, int, int]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*"})
    with urlopen(request, timeout=35) as response:
        source = response.read(MAX_SOURCE_BYTES + 1)
    if len(source) > MAX_SOURCE_BYTES:
        raise ValueError(f"La fotografía supera 15 MB: {url}")
    try:
        with Image.open(io.BytesIO(source)) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError(f"No se pudo decodificar la fotografía: {url}") from exc
    if image.width < 120 or image.height < 120:
        raise ValueError(f"Fotografía demasiado pequeña ({image.width}×{image.height}): {url}")
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA" if "transparency" in image.info else "RGB")
    image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="WEBP", quality=82, method=6)
    data = output.getvalue()
    return data, hashlib.sha256(data).hexdigest(), image.width, image.height


def _prepare_photos(payload: dict[str, Any]) -> dict[str, tuple[bytes, str, int, int]]:
    prepared: dict[str, tuple[bytes, str, int, int]] = {}
    for quotation in payload["quotations"]:
        for item in quotation["items"]:
            print(f"  foto · {item['name']}")
            prepared[item["id"]] = _download_photo(item["photo_url"])
    return prepared


def _backup_database() -> None:
    database = DATABASE.resolve()
    backup = BACKUP.resolve()
    if database.parent != (ROOT / "data").resolve() or backup.parent != database.parent:
        raise RuntimeError("Las rutas de base y respaldo salieron de data/")
    if backup.exists():
        return
    with sqlite3.connect(database) as source, sqlite3.connect(backup) as target:
        source.backup(target)


def _store_photo(
    connection: sqlite3.Connection,
    prepared: tuple[bytes, str, int, int],
    timestamp: str,
) -> str:
    data, digest, width, height = prepared
    row = connection.execute("SELECT id FROM images WHERE sha256 = ?", (digest,)).fetchone()
    if row:
        return str(row[0])
    image_id = f"img-{uuid.uuid4().hex}"
    connection.execute(
        "INSERT INTO images (id, sha256, mime_type, width, height, data, created_at) VALUES (?, ?, 'image/webp', ?, ?, ?, ?)",
        (image_id, digest, width, height, data, timestamp),
    )
    return image_id


def _apply(payload: dict[str, Any], photos: dict[str, tuple[bytes, str, int, int]]) -> None:
    timestamp = _now()
    selected = {item["id"] for quotation in payload["quotations"] for item in quotation["items"]}
    decisions = {entry["id"]: entry for entry in payload["decisions"]}
    connection = sqlite3.connect(DATABASE, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    try:
        connection.execute("BEGIN IMMEDIATE")
        if not connection.execute("SELECT 1 FROM quotations WHERE id = ?", (RESERVE,)).fetchone():
            raise RuntimeError("No existe la cotización de reserva")

        # Todo ítem auditado que no superó la revisión sale de las cinco tablas,
        # pero se conserva recuperable en Reserva con el motivo de la decisión.
        next_reserve = connection.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM items WHERE quotation_id = ?", (RESERVE,)
        ).fetchone()[0]
        for item_id, decision in decisions.items():
            row = connection.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            if not row:
                raise RuntimeError(f"No existe el ítem auditado {item_id}")
            extra = json.loads(row["extra_data_json"] or "{}")
            extra.pop("requiere_host", None)
            extra.update(
                {
                    "auditoria_estado": decision["verdict"],
                    "auditoria_motivo": decision["reason"],
                    "verificado_el": payload["audit_date"],
                }
            )
            if item_id not in selected:
                reserve_position = row["position"]
                if row["quotation_id"] != RESERVE:
                    reserve_position = next_reserve
                    next_reserve += 1
                connection.execute(
                    """
                    UPDATE items
                    SET quotation_id = ?, position = ?, included = 0,
                        extra_data_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (RESERVE, reserve_position, _json(extra), timestamp, item_id),
                )

        for quote_position, quotation in enumerate(payload["quotations"]):
            connection.execute(
                """
                UPDATE quotations
                SET position = ?, archived = 0, name = ?, purchase_link = '',
                    quote_date = ?, description = ?, extra_fields_json = ?,
                    revision = revision + 1, updated_at = ?
                WHERE id = ?
                """,
                (
                    quote_position,
                    quotation["name"],
                    payload["audit_date"],
                    quotation["description"],
                    _json(quotation["fields"]),
                    timestamp,
                    quotation["id"],
                ),
            )
            for position, item in enumerate(quotation["items"]):
                image_id = _store_photo(connection, photos[item["id"]], timestamp)
                values = (
                    quotation["id"],
                    position,
                    item["name"],
                    item["country"],
                    item["photo_url"],
                    image_id,
                    item["purchase_link"],
                    item["description"],
                    item["comment"],
                    item["price"],
                    _json(item["extra_data"]),
                    timestamp,
                    item["id"],
                )
                existing = connection.execute("SELECT 1 FROM items WHERE id = ?", (item["id"],)).fetchone()
                if existing:
                    connection.execute(
                        """
                        UPDATE items
                        SET quotation_id = ?, position = ?, name = ?, country = ?, photo_url = ?,
                            image_id = ?, purchase_link = ?, description = ?, comment = ?, price = ?,
                            included = 1, extra_data_json = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        values,
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO items (
                            quotation_id, position, name, country, photo_url, image_id,
                            purchase_link, description, comment, price, included,
                            extra_data_json, created_at, updated_at, id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                        """,
                        values[:-1] + (timestamp, values[-1]),
                    )

        connection.execute(
            "UPDATE quotations SET archived = 1, updated_at = ? WHERE id = ?", (timestamp, RESERVE)
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _verify_applied(payload: dict[str, Any]) -> None:
    """Comprueba que la transacción dejó exactamente el catálogo investigado."""
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite no superó integrity_check")
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise RuntimeError(f"SQLite tiene referencias inválidas: {foreign_key_errors}")

        final_ids: set[str] = set()
        for quotation in payload["quotations"]:
            quote_row = connection.execute(
                "SELECT * FROM quotations WHERE id = ?", (quotation["id"],)
            ).fetchone()
            if not quote_row or quote_row["archived"]:
                raise RuntimeError(f"{quotation['id']}: cotización ausente o archivada")
            if json.loads(quote_row["extra_fields_json"]) != quotation["fields"]:
                raise RuntimeError(f"{quotation['id']}: columnas no coinciden con el manifiesto")
            rows = connection.execute(
                "SELECT * FROM items WHERE quotation_id = ? ORDER BY position", (quotation["id"],)
            ).fetchall()
            expected_ids = [item["id"] for item in quotation["items"]]
            if [row["id"] for row in rows] != expected_ids:
                raise RuntimeError(f"{quotation['id']}: selección u orden final incorrectos")
            for expected, row in zip(quotation["items"], rows, strict=True):
                final_ids.add(row["id"])
                actual_extra = json.loads(row["extra_data_json"] or "{}")
                comparable = {
                    "name": row["name"],
                    "country": row["country"],
                    "photo_url": row["photo_url"],
                    "purchase_link": row["purchase_link"],
                    "description": row["description"],
                    "comment": row["comment"],
                    "price": row["price"],
                    "extra_data": actual_extra,
                }
                if any(comparable[key] != expected[key] for key in comparable):
                    raise RuntimeError(f"{row['id']}: contenido distinto del manifiesto")
                if row["included"] != 1 or not row["image_id"]:
                    raise RuntimeError(f"{row['id']}: no está incluido o no tiene foto")
                image_row = connection.execute(
                    "SELECT mime_type, width, height, data FROM images WHERE id = ?", (row["image_id"],)
                ).fetchone()
                if (
                    not image_row
                    or image_row["mime_type"] != "image/webp"
                    or image_row["width"] < 120
                    or image_row["height"] < 120
                ):
                    raise RuntimeError(f"{row['id']}: metadatos de foto inválidos")
                with Image.open(io.BytesIO(image_row["data"])) as image:
                    image.verify()

        for decision in payload["decisions"]:
            row = connection.execute(
                "SELECT quotation_id, included, extra_data_json FROM items WHERE id = ?", (decision["id"],)
            ).fetchone()
            if not row:
                raise RuntimeError(f"{decision['id']}: desapareció después de aplicar la auditoría")
            if decision["verdict"] == "discard":
                extra = json.loads(row["extra_data_json"] or "{}")
                if (
                    row["quotation_id"] != RESERVE
                    or row["included"] != 0
                    or extra.get("auditoria_estado") != "discard"
                    or extra.get("auditoria_motivo") != decision["reason"]
                ):
                    raise RuntimeError(f"{decision['id']}: descarte no quedó trazable en Reserva")
            elif decision["id"] not in final_ids:
                raise RuntimeError(f"{decision['id']}: veredicto keep no quedó activo")
    finally:
        connection.close()


def _render_report(payload: dict[str, Any]) -> str:
    kept = {item["id"] for quote in payload["quotations"] for item in quote["items"]}
    lines = [
        "# Auditoría final de cotizaciones Edge AI",
        "",
        f"Fecha de verificación: {payload['audit_date']}",
        "",
        "La nota de durabilidad + confiabilidad usa la rúbrica documentada en el registro de fuentes: "
        "robustez física/térmica (40 %), soporte y ciclo de vida (30 %), y madurez del suministro/software (30 %).",
        "",
        "## Resultado",
        "",
    ]
    for quotation in payload["quotations"]:
        count = len(quotation["items"])
        noun = "ítem final" if count == 1 else "ítems finales"
        lines.append(f"- {quotation['name']}: {count} {noun}")
    lines.extend(["", "## Selección final", ""])
    for quotation in payload["quotations"]:
        lines.extend([f"### {quotation['name']}", ""])
        for item in quotation["items"]:
            suffix = " · **ALTA NUEVA**" if item["new"] else ""
            lines.append(
                f"- **{item['name']}** — {item['price']} · {item['country']} · "
                f"nota {item['extra_data']['durabilidad_confiabilidad']}/10{suffix}"
            )
        lines.append("")
    lines.extend(["## Decisión sobre cada ítem original", ""])
    grouped: dict[str, list[dict[str, Any]]] = {quote_id: [] for quote_id in ACTIVE_QUOTES}
    for decision in payload["decisions"]:
        grouped[decision["quotation_id"]].append(decision)
    quote_names = {quote["id"]: quote["name"] for quote in payload["quotations"]}
    for quote_id in ACTIVE_QUOTES:
        lines.extend([f"### {quote_names[quote_id]}", ""])
        for decision in grouped[quote_id]:
            state = "CONSERVADO" if decision["id"] in kept else "RESERVA"
            sources = decision.get("sources") or []
            source_text = " · ".join(f"[fuente {index + 1}]({url})" for index, url in enumerate(sources))
            suffix = f" {source_text}" if source_text else ""
            lines.append(f"- **{state} — {decision['name']}**: {decision['reason']}{suffix}")
        lines.append("")
    lines.extend(
        [
            "## Controles aplicados",
            "",
            "- Máximo 8 ítems, 2 por marca y 4 por país en cada cotización.",
            "- Una o ninguna alta nueva por cotización.",
            "- Foto embebida, precio puntual, compra vigente para Chile, fabricante, nota y fecha en todo ítem activo.",
            "- USB con TOPS; M.2 sin ‘Requiere equipo host’ y TOPS junto al precio; PC industrial con M.2 PCIe y almacenamiento separado; microcomputadores con CPU y RAM.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    quotations = [db.get_quotation(quote_id) for quote_id in ACTIVE_QUOTES]
    if any(quotation is None for quotation in quotations):
        raise RuntimeError("No se pudieron volver a leer las cinco cotizaciones")
    complete = [quotation for quotation in quotations if quotation is not None]
    share = build_multi_share_payload(complete, scope="included")
    (OUTPUT / "cotizaciones-auditadas-2026-09-03.html").write_text(
        render_standalone_html(share), encoding="utf-8"
    )
    (OUTPUT / "cotizaciones-auditadas-2026-09-03.xlsx").write_bytes(
        build_workbook(complete, scope="included")
    )
    report = _render_report(payload)
    (OUTPUT / "auditoria-cotizaciones-2026-09-03.md").write_text(report, encoding="utf-8")
    source_lines = [
        "# Registro interno de fuentes",
        "",
        "Rúbrica de la nota: robustez física/térmica 40 %, soporte/ciclo de vida 30 %, suministro y software 30 %. "
        "La nota no es una prueba MTBF; es una evaluación comparativa trazable a las fuentes de cada fila.",
        "",
        "## Fuentes de los ítems finales",
        "",
    ]
    for quotation in payload["quotations"]:
        for item in quotation["items"]:
            source_lines.extend(
                [
                    f"### {item['name']}",
                    "",
                    f"Compra: {item['purchase_link']}",
                    f"Fabricante: {item['extra_data']['pagina_fabricante']}",
                    f"Foto: {item['photo_url']}",
                    "",
                ]
            )
            source_lines.extend(f"- {url}" for url in item.get("sources") or [])
            source_lines.append("")
    source_lines.extend(["## Fuentes de las decisiones sobre ítems originales", ""])
    for decision in payload["decisions"]:
        source_lines.extend([f"### {decision['name']}", "", decision["reason"], ""])
        source_lines.extend(f"- {url}" for url in decision.get("sources") or [])
        source_lines.append("")
    internal_report = ROOT / "scripts" / "auditoria" / "report-source.md"
    internal_report.parent.mkdir(parents=True, exist_ok=True)
    internal_report.write_text("\n".join(source_lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    payload = _load_manifest()
    _validate_manifest(payload)
    print("Manifiesto válido; descargando fotografías antes de abrir la transacción…")
    photos = _prepare_photos(payload)
    if args.dry_run:
        print(f"Dry-run correcto: {len(photos)} fotografías decodificadas; no se escribió la base.")
        return 0
    _backup_database()
    _apply(payload, photos)
    _verify_applied(payload)
    _write_outputs(payload)
    print(f"Auditoría aplicada: {sum(len(q['items']) for q in payload['quotations'])} ítems activos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
