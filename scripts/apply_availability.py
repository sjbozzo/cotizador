"""Aplica al catálogo el resultado del flujo «disponibilidad-mercados».

El flujo devuelve, por ítem, dónde se puede comprar desde Chile (Mercado Libre, Amazon,
AliExpress), el mejor enlace y su precio. Este script busca cada ítem por nombre, llena
«Disponibilidad en Chile», y actualiza precio y link de compra sólo cuando el flujo
encontró algo concreto. Es idempotente.

    uv run python -m scripts.apply_availability scripts/auditoria/2026-09-03-disponibilidad.json --dry-run
    uv run python -m scripts.apply_availability scripts/auditoria/2026-09-03-disponibilidad.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

from pydantic import ValidationError

from app import db
from app.schemas import ItemUpdate


def _normal(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _tokens(value: str) -> set[str]:
    # Se conservan los tokens con dígitos aunque sean cortos ("5", "x4", "2"): distinguen modelos.
    return {token for token in _normal(value).split() if len(token) > 2 or any(char.isdigit() for char in token)}


def find_item(name: str, items: list[dict]) -> dict | None:
    """El flujo escribe nombres largos («Raspberry Pi 5 (8 GB)»). La primera palabra (la marca)
    tiene que estar en el nombre del ítem; entre los que la tienen gana el de más tokens en común."""
    words = _normal(name).split()
    if not words:
        return None
    brand = words[0]
    wanted = _tokens(name)
    best, score = None, 0
    for item in items:
        item_tokens = _tokens(item["name"])
        if brand not in _normal(item["name"]).split():
            continue
        common = len(wanted & item_tokens)
        if common > score:
            best, score = item, common
    return best if score >= 2 else None


def _valid_link(value: str) -> str:
    try:
        return ItemUpdate(purchase_link=(value or "").strip()).purchase_link or ""
    except ValidationError:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archivo", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-links", action="store_true", help="no cambia el link de compra aunque haya uno mejor")
    args = parser.parse_args()
    payload = json.loads(args.archivo.read_text(encoding="utf-8"))
    db.init_database()
    print(f"Base de datos: {db.database_path()}")

    visible = [item for q in db.list_quotations(include_items=True) if not q.get("archived") for item in q["items"]]
    changes = 0
    for batch in payload.get("batches", []):
        for found in batch.get("items", []):
            item = find_item(found["name"], visible)
            if not item:
                print(f"  ! sin ítem para: {found['name']}")
                continue
            extra = dict(item["extra_data"])
            summary = (found.get("summary") or "").strip()
            if summary:
                extra["disponibilidad_chile"] = summary
            wanted = {"extra_data": extra}
            link = "" if args.keep_links else _valid_link(found.get("best_link", ""))
            price = (found.get("best_price") or "").strip()
            no_price = not price or price.lower().startswith(("no aparece", "sin dato", "no se encontr"))
            if link and not no_price:
                wanted["purchase_link"] = link
                wanted["price"] = price
            try:
                validated = ItemUpdate(**wanted).model_dump(exclude_unset=True)
            except ValidationError as exc:
                print(f"  ! {item['name']}: {exc.errors()[0]['msg']}")
                continue
            diff = {key: value for key, value in validated.items() if value != item.get(key)}
            if not diff:
                continue
            print(f"  actualizar · {found['name'][:34]:34} -> {item['name'][:44]:44} · {', '.join(sorted(diff))}")
            changes += 1
            if not args.dry_run:
                db.update_item(item["id"], diff)
    print(f"{changes} cambio(s) {'pendientes' if args.dry_run else 'aplicados'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
