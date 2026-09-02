"""Crea cotizaciones NUEVAS a partir de un HTML o JSON exportado por el Cotizador.

La importación de la aplicación mete ítems en una cotización existente; este
script, en cambio, recrea cada cotización del archivo con sus columnas y sus
ítems. Si una cotización con el mismo id ya existe, se omite (idempotente).

    uv run python -m scripts.import_quotations "C:/ruta/cotizaciones-compartir.html" --dry-run
    uv run python -m scripts.import_quotations "C:/ruta/cotizaciones-compartir.html"
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from app import db
from app.schemas import ItemCreate, normalize_extra_fields
from app.services.images import InvalidImage, process_photo
from app.services.imports import parse_import_content


def import_file(path: Path, dry_run: bool) -> int:
    payload = parse_import_content(path.read_text(encoding="utf-8"))
    listed = payload.get("quotations") or [payload.get("quotation")]
    listed = [entry for entry in listed if isinstance(entry, dict) and entry.get("id")]
    if not listed:
        print("El archivo no declara cotizaciones")
        return 0

    created = 0
    for source in listed:
        if db.get_quotation(source["id"]):
            print(f"  ya existe, se omite: {source['id']}")
            continue
        try:
            fields = normalize_extra_fields(source.get("extra_fields") or [])
        except ValueError as exc:
            print(f"  ! {source['id']}: campos extra inválidos ({exc}); se crean sin columnas")
            fields = []
        items = [
            item
            for item in payload["items"]
            if isinstance(item, dict) and (item.get("quotation_id") or listed[0]["id"]) == source["id"]
        ]
        print(f"  crear · {source['name']} · {len(items)} ítems · columnas: {', '.join(f['key'] for f in fields) or '—'}")
        created += 1
        if dry_run:
            continue
        db.create_quotation(
            {
                "id": source["id"],
                "name": str(source.get("name") or source["id"]).strip(),
                "purchase_link": str(source.get("purchase_link") or ""),
                "quote_date": str(source.get("quote_date") or date.today().isoformat()),
                "description": str(source.get("description") or ""),
                "extra_fields": fields,
            }
        )
        allowed = {field["key"] for field in fields}
        for item in items:
            data = {
                "name": str(item.get("name") or "").strip(),
                "country": str(item.get("country") or ""),
                "photo": str(item.get("photo") or ""),
                "purchase_link": str(item.get("purchase_link") or ""),
                "description": str(item.get("description") or ""),
                "comment": str(item.get("comment") or ""),
                "price": str(item.get("price") or ""),
                "included": bool(item.get("included", True)),
                "extra_data": {k: v for k, v in (item.get("extra_data") or {}).items() if k in allowed},
            }
            try:
                validated = ItemCreate(**data).model_dump(mode="json")
            except ValidationError:
                # Un enlace o foto mal formados no deben frenar la importación.
                data["purchase_link"] = ""
                data["photo"] = ""
                try:
                    validated = ItemCreate(**data).model_dump(mode="json")
                except ValidationError as exc:
                    print(f"    ! {data['name'] or '(sin nombre)'}: {exc.errors()[0]['msg']} — se omite")
                    continue
            photo = validated.pop("photo")
            try:
                validated.update(process_photo(photo))
            except InvalidImage:
                validated.update({"photo_url": "", "image_id": None})
            db.create_item(source["id"], validated)
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archivo", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    db.init_database()
    print(f"Base de datos: {db.database_path()}")
    created = import_file(args.archivo, args.dry_run)
    print(f"{created} cotización(es) {'por crear' if args.dry_run else 'creadas'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
