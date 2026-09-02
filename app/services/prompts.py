from __future__ import annotations

import json
from typing import Any


def build_formatting_prompt(quotation: dict[str, Any]) -> str:
    field_lines = []
    example_extras: dict[str, Any] = {}
    for field in quotation.get("extra_fields", []):
        field_lines.append(f"- {field['key']}: {field['type']} ({field['label']})")
        example_extras[field["key"]] = {
            "boolean": True,
            "number": 0,
            "date": "2026-08-30",
            "url": "https://ejemplo.com/dato",
        }.get(field["type"], "valor encontrado")
    if not field_lines:
        field_lines.append("- Esta cotización no tiene campos extra; usa extra_data como objeto vacío.")

    example = {
        "format": "cotizador-ai-import",
        "version": 1,
        "quotation": {
            "id": quotation["id"],
            "name": quotation["name"],
            "extra_fields": quotation.get("extra_fields", []),
        },
        "items": [
            {
                "name": "Nombre exacto del producto",
                "country": "País de origen o texto vacío",
                "photo": "https://ejemplo.com/foto.webp",
                "purchase_link": "https://ejemplo.com/producto",
                "description": "Descripción factual y breve",
                "comment": "Observaciones, límites o datos por confirmar",
                "price": "Precio tal como aparece en la fuente, con moneda",
                "included": True,
                "extra_data": example_extras,
            }
        ],
    }
    return f"""Necesito convertir información de productos a un JSON importable en mi cotizador.

Cotización de destino: {quotation['name']}
ID de destino: {quotation['id']}

Devuelve ÚNICAMENTE JSON válido en UTF-8. No uses Markdown, comentarios, explicaciones ni bloques ```.

Reglas:
1. Crea un objeto con format="cotizador-ai-import", version=1, quotation e items.
2. Cada producto debe aparecer una sola vez. No inventes datos. Usa texto vacío cuando una fuente no informe un campo.
3. Conserva el precio como texto, incluida su moneda, rango, aproximación o condición de usado.
4. purchase_link y photo deben ser URL http/https completas o texto vacío. No conviertas una referencia documental en compra sin indicarlo en comment.
5. country es el país de origen declarado; no lo deduzcas desde el dominio del enlace.
6. included siempre es booleano true/false y por defecto debe ser true.
7. Usa exactamente estas claves dentro de extra_data y respeta sus tipos:
{chr(10).join(field_lines)}
8. No agregues claves desconocidas. Si hay incertidumbre, consérvala en comment.
9. Si te doy datos de varios tipos de producto, incluye sólo los que correspondan a esta cotización de destino.

Esquema de ejemplo (reemplaza sus valores, no lo copies como producto real):
{json.dumps(example, ensure_ascii=False, indent=2)}

Ahora transforma los datos que aparecen después de esta línea:
---
PEGA AQUÍ LOS DATOS ENCONTRADOS
"""

