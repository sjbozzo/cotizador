"""Aplica al catálogo los resultados de una auditoría hecha con agentes.

Cada archivo JSON de `scripts/auditoria/` es el resultado de un flujo de agentes:
la auditoría ítem por ítem (precios, disponibilidad, ajuste al proyecto, bajas
sostenidas por jueces) o la lista de candidatos nuevos elegidos por cotización.
Es idempotente: sólo escribe lo que difiere y nunca duplica un ítem.

    uv run python -m scripts.apply_audit scripts/auditoria/2026-09-02-auditoria.json --dry-run
    uv run python -m scripts.apply_audit scripts/auditoria/2026-09-02-auditoria.json
    uv run python -m scripts.apply_audit scripts/auditoria/2026-09-02-candidatos.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app import db
from app.schemas import ItemCreate, ItemUpdate, normalize_extra_fields
from app.services.images import InvalidImage, process_photo


# Columnas que la auditoría rellena en todas las cotizaciones (y una sólo en los PC).
COMMON_FIELDS = [
    {"key": "disponibilidad_chile", "label": "Disponibilidad en Chile", "type": "text"},
    {"key": "facilidad_programacion", "label": "Facilidad de programación (1-5)", "type": "number"},
]
PC_FIELDS = [{"key": "m2_acelerador", "label": "M.2 para acelerador", "type": "text"}]
# Los PC industriales y los pares PC + Hailo llevan la columna de la ranura M.2.
PC_QUOTATIONS = {"pcs-industriales-m2-ai", "pares-pc-fanless-npu"}


def _normalized_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _safe_link(value: Any) -> str:
    """Un link que no valide se descarta en silencio: mejor vacío que roto."""
    value = _clean(value)
    try:
        return ItemUpdate(purchase_link=value).purchase_link or ""
    except ValidationError:
        return ""


def _m2_summary(m2: dict[str, Any] | None) -> str:
    if not m2 or not m2.get("applies"):
        return ""
    parts = [part for part in (_clean(m2.get("key")), _clean(m2.get("length")), _clean(m2.get("pcie"))) if part]
    detail = " ".join(parts) or "sin dato"
    if m2.get("ok_for_accelerator"):
        libre = "libre" if m2.get("free") else "compartida con el SSD"
        return f"Sí: {detail}, {libre}"
    note = _clean(m2.get("note"))
    return f"No: {detail}" + (f" — {note}" if note else "")


def ensure_fields(dry_run: bool) -> int:
    changes = 0
    for quotation in db.list_quotations():
        wanted = list(COMMON_FIELDS) + (PC_FIELDS if quotation["id"] in PC_QUOTATIONS else [])
        existing = {field["key"] for field in quotation["extra_fields"]}
        missing = [field for field in wanted if field["key"] not in existing]
        if not missing:
            continue
        print(f"  columnas · {quotation['name']} · {', '.join(field['key'] for field in missing)}")
        changes += 1
        if not dry_run:
            fields = normalize_extra_fields(list(quotation["extra_fields"]) + missing)
            db.update_quotation(quotation["id"], {"extra_fields": fields})
    return changes


def apply_audit(payload: dict[str, Any], dry_run: bool) -> int:
    changes = ensure_fields(dry_run)
    field_keys = {q["id"]: {field["key"] for field in q["extra_fields"]} for q in db.list_quotations()}
    if dry_run:
        for quotation_id in field_keys:
            field_keys[quotation_id] |= {field["key"] for field in COMMON_FIELDS}
        for quotation_id in PC_QUOTATIONS:
            field_keys.setdefault(quotation_id, set()).update(field["key"] for field in PC_FIELDS)

    for batch in payload.get("batches", []):
        for audited in batch.get("items", []):
            item = db.get_item(audited["id"])
            if not item:
                print(f"  ! ítem ausente, se omite: {audited.get('name')}")
                continue
            verdict = audited.get("verdict", "keep")
            votes = audited.get("votes")
            judged = votes in ("usuario", "revisado") or (isinstance(votes, list) and len(votes) >= 2)
            # Una baja vale si la pidió el usuario o si la juzgaron al menos dos agentes.
            holds = verdict == "keep" or (bool(audited.get("removal_holds")) and judged)
            if verdict != "keep" and not holds:
                print(f"  sin jueces, se mantiene · {item['name']} ({verdict} propuesto)")
            if verdict == "delete" and holds:
                print(f"  eliminar · {item['name']} — {audited.get('verdict_reason', '')[:110]}")
                changes += 1
                if not dry_run:
                    db.delete_item(item["id"])
                continue

            allowed = field_keys.get(item["quotation_id"], set())
            extra = dict(item["extra_data"])
            for key, value in (audited.get("extra_data") or {}).items():
                if key in allowed and value not in (None, ""):
                    extra[key] = value
            if audited.get("availability_chile"):
                extra["disponibilidad_chile"] = _clean(audited["availability_chile"])
            if audited.get("programming_score"):
                extra["facilidad_programacion"] = int(audited["programming_score"])
            m2_text = _m2_summary(audited.get("m2"))
            if m2_text and "m2_acelerador" in allowed:
                extra["m2_acelerador"] = m2_text

            wanted: dict[str, Any] = {
                "name": _clean(audited.get("name")) or item["name"],
                "price": _clean(audited.get("price")) or item["price"],
                "country": _clean(audited.get("country")) or item["country"],
                "purchase_link": _safe_link(audited.get("purchase_link")) or item["purchase_link"],
                "description": _clean(audited.get("description")) or item["description"],
                "comment": _clean(audited.get("comment")) or item["comment"],
                "included": not (verdict == "discard" and holds),
                "extra_data": extra,
            }
            try:
                validated = ItemUpdate(**wanted).model_dump(exclude_unset=True)
            except ValidationError as exc:
                print(f"  ! {item['name']}: {exc.errors()[0]['msg']} — se omite")
                continue
            diff = {key: value for key, value in validated.items() if value != item.get(key)}
            if not diff:
                continue
            labels = ", ".join(sorted(diff))
            state = "descartar" if diff.get("included") is False else "incluir" if diff.get("included") is True else "actualizar"
            print(f"  {state} · {item['name']} · {labels}")
            changes += 1
            if not dry_run:
                db.update_item(item["id"], diff)
    return changes


def apply_candidates(payload: dict[str, Any], dry_run: bool) -> int:
    changes = ensure_fields(dry_run)
    for entry in payload.get("quotations", []):
        quotation = db.get_quotation(entry["quotation_id"])
        if not quotation:
            print(f"  ! cotización ausente, se omite: {entry['quotation_id']}")
            continue
        allowed = {field["key"] for field in quotation["extra_fields"]} | {field["key"] for field in COMMON_FIELDS}
        present = {_normalized_name(item["name"]) for item in quotation["items"]}
        for pick in entry.get("picks", [])[:3]:
            name = _clean(pick.get("name"))
            if not name or _normalized_name(name) in present:
                continue
            extra = {
                key: value
                for key, value in (pick.get("extra_data") or {}).items()
                if key in allowed and value not in (None, "")
            }
            extra["disponibilidad_chile"] = _clean(pick.get("availability_chile"))
            if pick.get("programming_score"):
                extra["facilidad_programacion"] = int(pick["programming_score"])
            data = {
                "name": name,
                "country": _clean(pick.get("country")),
                "photo": _safe_link(pick.get("photo_url")),
                "purchase_link": _safe_link(pick.get("purchase_link")),
                "description": _clean(pick.get("description")),
                "comment": _clean(pick.get("comment")),
                "price": _clean(pick.get("price")),
                "included": True,
                "extra_data": extra,
            }
            try:
                validated = ItemCreate(**data).model_dump(mode="json")
            except ValidationError as exc:
                print(f"  ! {name}: {exc.errors()[0]['msg']} — se omite")
                continue
            print(f"  agregar · {quotation['name']} · {name} · {validated['price']}")
            changes += 1
            present.add(_normalized_name(name))
            if not dry_run:
                photo = validated.pop("photo")
                try:
                    validated.update(process_photo(photo))
                except InvalidImage:
                    validated.update({"photo_url": "", "image_id": None})
                db.create_item(quotation["id"], validated)
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archivo", type=Path, help="resultado JSON de la auditoría o de los candidatos")
    parser.add_argument("--dry-run", action="store_true", help="muestra los cambios sin escribirlos")
    args = parser.parse_args()
    payload = json.loads(args.archivo.read_text(encoding="utf-8"))
    db.init_database()
    print(f"Base de datos: {db.database_path()}")
    if "batches" in payload:
        changes = apply_audit(payload, args.dry_run)
    elif "quotations" in payload:
        changes = apply_candidates(payload, args.dry_run)
    else:
        print("El archivo no parece una auditoría ni una lista de candidatos")
        return 1
    verb = "pendientes" if args.dry_run else "aplicados"
    print(f"{changes} cambio(s) {verb}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
