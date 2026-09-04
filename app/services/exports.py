from __future__ import annotations

import base64
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import db


TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"
STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
ENVIRONMENT = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)


def filename_slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9áéíóúÁÉÍÓÚñÑ]+", "-", value, flags=re.UNICODE).strip("-").lower()
    return value[:80] or "cotizacion"


def _exported_quotation(quotation: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": quotation["id"],
        "name": quotation["name"],
        "purchase_link": quotation["purchase_link"],
        "quote_date": quotation["quote_date"],
        "description": quotation["description"],
        "extra_fields": quotation["extra_fields"],
        "revision": quotation["revision"],
    }


def _exported_item(item: dict[str, Any], quotation_id: str) -> dict[str, Any]:
    photo = db.image_data_uri(item["image_id"]) if item.get("image_id") else item.get("photo_url", "")
    return {
        "id": item["id"],
        # Con varias cotizaciones en un mismo archivo, cada ítem dice de cuál viene:
        # así el archivo arma sus pestañas y la importación sólo toca lo que le toca.
        "quotation_id": quotation_id,
        "name": item["name"],
        "country": item["country"],
        "photo": photo,
        "purchase_link": item["purchase_link"],
        "description": item["description"],
        "comment": item["comment"],
        "price": item["price"],
        "included": bool(item["included"]),
        "extra_data": item["extra_data"],
    }


def _http_url(value: Any) -> str:
    candidate = str(value or "").strip()
    parsed = urlsplit(candidate)
    return candidate if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _manufacturer_link(item: dict[str, Any], quotation: dict[str, Any]) -> str:
    """Obtiene la página de fabricante sin asumir que todos usan la misma etiqueta."""
    extra = item.get("extra_data") or {}
    exact_keys = ("pagina_fabricante", "pagina_del_fabricante", "link_fabricante", "manufacturer_link", "manufacturer_url")
    for key in exact_keys:
        link = _http_url(extra.get(key))
        if link:
            return link
    for field in quotation.get("extra_fields") or []:
        key = str(field.get("key", "")).lower()
        label = str(field.get("label", "")).lower()
        if "fabricante" not in key and "fabricante" not in label and "manufacturer" not in key and "manufacturer" not in label:
            continue
        link = _http_url(extra.get(field.get("key")))
        if link:
            return link
    return ""


def build_bom_payload(quotations: list[dict[str, Any]]) -> dict[str, Any]:
    """Aplana las cotizaciones activas en una sola lista para el BOM."""
    items: list[dict[str, Any]] = []
    for quotation in quotations:
        for item in quotation["items"]:
            photo = db.image_data_uri(item["image_id"]) if item.get("image_id") else item.get("photo_url", "")
            items.append(
                {
                    "name": item["name"],
                    "country": item["country"],
                    "photo": photo,
                    "purchase_link": _http_url(item["purchase_link"]),
                    "manufacturer_link": _manufacturer_link(item, quotation),
                    "price": item["price"],
                    "included": bool(item["included"]),
                }
            )
    return {
        "exported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "items": items,
    }


def build_share_payload(
    quotation: dict[str, Any],
    *,
    scope: str = "all",
    item_ids: set[str] | None = None,
) -> dict[str, Any]:
    return build_multi_share_payload([quotation], scope=scope, item_ids=item_ids)


def build_multi_share_payload(
    quotations: list[dict[str, Any]],
    *,
    scope: str = "all",
    item_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Una o varias cotizaciones en un solo archivo.

    El nivel superior conserva `quotation` e `items` planos, que es lo que lee la
    importación; `quotations` agrega el detalle de cada pestaña.
    """
    if not quotations:
        raise ValueError("Se necesita al menos una cotización para exportar")

    exported_items: list[dict[str, Any]] = []
    for quotation in quotations:
        items = quotation["items"]
        if item_ids is not None:
            items = [item for item in items if item["id"] in item_ids]
        elif scope == "included":
            items = [item for item in items if item["included"]]
        exported_items.extend(_exported_item(item, quotation["id"]) for item in items)

    if item_ids is not None:
        export_scope = "single" if len(exported_items) == 1 else "selection"
    elif scope == "included":
        export_scope = "selection"
    else:
        export_scope = "all"

    return {
        "format": "cotizador-share",
        "version": 1,
        "export_id": str(uuid.uuid4()),
        "exported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "scope": export_scope,
        "quotation": _exported_quotation(quotations[0]),
        "quotations": [_exported_quotation(quotation) for quotation in quotations],
        "items": exported_items,
    }


def payload_names(payload: dict[str, Any]) -> list[str]:
    listed = payload.get("quotations") or [payload["quotation"]]
    return [str(entry.get("name", "")) for entry in listed]


def share_filename(payload: dict[str, Any], suffix: str) -> str:
    names = payload_names(payload)
    stem = filename_slug(names[0]) if len(names) == 1 else "cotizaciones"
    return f"{stem}{suffix}"


def _app_css() -> str:
    """La hoja de la aplicación viaja dentro del archivo: mismo aspecto, sin red."""
    return (STATIC_DIR / "styles.css").read_text(encoding="utf-8")


def render_standalone_html(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.b64encode(raw).decode("ascii")
    names = payload_names(payload)
    template = ENVIRONMENT.get_template("shared_quote.html")
    return template.render(
        payload_b64=payload_b64,
        title=names[0] if len(names) == 1 else f"{len(names)} cotizaciones",
        app_css=_app_css(),
        download_name=share_filename(payload, "-compartir.html"),
    )


def render_bom_html(payload: dict[str, Any]) -> str:
    template = ENVIRONMENT.get_template("bom.html")
    return template.render(
        title="BOM",
        app_css=_app_css(),
        exported_at=payload["exported_at"],
        items=payload["items"],
    )


def payload_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
