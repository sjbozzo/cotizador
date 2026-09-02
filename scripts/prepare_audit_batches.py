"""Prepara los lotes para el flujo ligero de auditoría (scripts/auditoria/workflows/auditar-lean.js).

Escribe un JSON pequeño por lote de 4 ítems (id, nombre, precio, textos, columnas extra) y un
`lean-args.json` con la lista {q, file, names} que se pasa como `batches` al flujo. Así cada agente
lee sólo su lote y no el catálogo completo, que es lo que disparaba el costo.

    uv run python -m scripts.prepare_audit_batches <carpeta_destino> [--skip cotizacion_id ...] [--only cotizacion_id ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app import db

# Columnas que llena la auditoría; no se muestran al agente como "columnas extra" a corregir.
AUDIT_COLUMNS = {"disponibilidad_chile", "facilidad_programacion", "m2_acelerador"}
ITEM_KEYS = ("id", "name", "price", "country", "purchase_link", "description", "comment", "included", "extra_data")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destino", type=Path, help="carpeta donde escribir lotes/ y lean-args.json")
    parser.add_argument("--skip", nargs="*", default=[], help="ids de cotización a omitir")
    parser.add_argument("--only", nargs="*", default=[], help="si se indica, sólo estas cotizaciones")
    parser.add_argument("--size", type=int, default=4, help="ítems por lote (4 por defecto)")
    args = parser.parse_args()

    db.init_database()
    lotes = args.destino / "lotes"
    lotes.mkdir(parents=True, exist_ok=True)
    for old in lotes.glob("*.json"):
        old.unlink()

    batches = []
    for quotation in db.list_quotations(include_items=True):
        if quotation["id"] in args.skip or (args.only and quotation["id"] not in args.only):
            continue
        fields = [field["key"] for field in quotation["extra_fields"] if field["key"] not in AUDIT_COLUMNS]
        items = quotation["items"]
        for start in range(0, len(items), args.size):
            chunk = items[start : start + args.size]
            path = lotes / f"lote-{len(batches):02d}-{quotation['id']}.json"
            path.write_text(
                json.dumps(
                    {
                        "quotation_id": quotation["id"],
                        "quotation_name": quotation["name"],
                        "extra_fields": fields,
                        "items": [{key: item[key] for key in ITEM_KEYS} for item in chunk],
                    },
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
            batches.append({"q": quotation["id"], "file": str(path.resolve()), "names": [item["name"] for item in chunk]})

    (args.destino / "lean-args.json").write_text(json.dumps(batches, ensure_ascii=False), encoding="utf-8")
    total = sum(len(batch["names"]) for batch in batches)
    print(f"{len(batches)} lotes, {total} ítems -> {lotes} y {args.destino / 'lean-args.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
