from __future__ import annotations

import base64
import json
import re
import unicodedata
from datetime import date
from html.parser import HTMLParser
from typing import Any

from pydantic import ValidationError

from .. import db
from ..schemas import MAX_EXTRA_FIELDS, ItemUpdate, normalize_extra_fields, safe_link
from .images import InvalidImage, process_photo


class ImportProblem(ValueError):
    pass


class _PayloadScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.capture = False
        self.kind = ""
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and values.get("id") == "quotation-data":
            self.capture = True
            self.kind = values.get("type") or ""

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.capture:
            self.capture = False


def _normalized_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def parse_import_content(content: str) -> dict[str, Any]:
    stripped = content.lstrip("\ufeff \t\r\n")
    if stripped.startswith("<"):
        parser = _PayloadScriptParser()
        parser.feed(content)
        encoded = "".join(parser.parts).strip()
        if not encoded:
            raise ImportProblem("El HTML no contiene el bloque seguro quotation-data")
        try:
            if parser.kind == "text/plain":
                raw = base64.b64decode(encoded, validate=True).decode("utf-8")
            elif parser.kind == "application/json":
                raw = encoded
            else:
                raise ImportProblem("El bloque quotation-data usa un tipo no admitido")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ImportProblem("El bloque de datos del HTML está dañado") from exc
    else:
        raw = stripped
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ImportProblem(f"JSON inválido cerca de la línea {exc.lineno}") from exc
    if isinstance(value, list):
        value = {"format": "cotizador-ai-import", "version": 1, "items": value}
    if not isinstance(value, dict):
        raise ImportProblem("La importación debe contener un objeto JSON o una lista de ítems")
    if not isinstance(value.get("items"), list):
        raise ImportProblem("La importación no contiene una lista items")
    if len(value["items"]) > 5000:
        raise ImportProblem("La importación supera el máximo de 5.000 ítems")
    return value


ALIASES = {
    "id": ("id", "item_id", "external_id"),
    "name": ("name", "nombre", "nombre_producto", "producto"),
    "country": ("country", "pais_origen", "paisDeOrigen", "originCountry"),
    "photo": ("photo", "foto", "imageUrl", "imagen"),
    "purchase_link": ("purchase_link", "link_compra", "linkDeCompra", "purchaseUrl", "url"),
    "description": ("description", "descripcion"),
    "comment": ("comment", "comentario"),
    "price": ("price", "precio", "precio_promedio", "precioPromedio", "averagePrice"),
    "included": ("included", "incluir", "seleccionar", "selected"),
    "extra_data": ("extra_data", "campos_extra", "extras"),
}


def _first(record: dict[str, Any], names: tuple[str, ...]) -> tuple[bool, Any]:
    for name in names:
        if name in record:
            return True, record[name]
    return False, None


def _coerce_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "sí", "si", "yes", "s", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    raise ImportProblem(f"Valor booleano no reconocido: {value!r}")


def _coerce_extra(value: Any, field_type: str) -> Any:
    if value is None or value == "":
        return None
    if field_type == "boolean":
        return _coerce_boolean(value)
    if field_type == "number":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        normalized = str(value).strip().replace(" ", "")
        if normalized.count(",") == 1 and "." not in normalized:
            normalized = normalized.replace(",", ".")
        try:
            number = float(normalized)
            return int(number) if number.is_integer() else number
        except ValueError as exc:
            raise ImportProblem(f"Número no reconocido: {value!r}") from exc
    if field_type == "url":
        return safe_link(str(value))
    if field_type == "date":
        try:
            return date.fromisoformat(str(value).strip()).isoformat()
        except ValueError as exc:
            raise ImportProblem(f"Fecha no reconocida: {value!r}; use AAAA-MM-DD") from exc
    return str(value).strip()


def _normalize_ai_item(record: Any, field_definitions: list[dict[str, str]]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ImportProblem("Cada ítem debe ser un objeto JSON")
    normalized: dict[str, Any] = {}
    for target, aliases in ALIASES.items():
        present, value = _first(record, aliases)
        if present:
            normalized[target] = value
    name = str(normalized.get("name") or "").strip()
    if not name:
        raise ImportProblem("Cada ítem debe tener nombre")
    normalized["name"] = name
    if "included" in normalized:
        normalized["included"] = _coerce_boolean(normalized["included"])
    extras_present = "extra_data" in normalized or any(field["key"] in record for field in field_definitions)
    extras = normalized.get("extra_data")
    if extras is None:
        extras = {}
    if not isinstance(extras, dict):
        raise ImportProblem(f"campos_extra de {name!r} debe ser un objeto")
    definitions = {field["key"]: field for field in field_definitions}
    for key in definitions:
        if key in record and key not in extras:
            extras[key] = record[key]
    normalized_extras = {}
    for key, value in extras.items():
        definition = definitions.get(key)
        normalized_extras[key] = _coerce_extra(value, definition["type"] if definition else "text")
    if extras_present:
        normalized["extra_data"] = normalized_extras
    else:
        normalized.pop("extra_data", None)
    for key in ("country", "photo", "purchase_link", "description", "comment", "price"):
        if key in normalized:
            normalized[key] = "" if normalized[key] is None else str(normalized[key]).strip()
    if "purchase_link" in normalized:
        normalized["purchase_link"] = safe_link(normalized["purchase_link"])
    if "photo" in normalized:
        normalized["photo"] = safe_link(normalized["photo"], allow_data_image=True)
    return normalized


def _source_quotations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Un archivo puede traer varias cotizaciones; el formato viejo trae una sola."""
    listed = payload.get("quotations")
    if isinstance(listed, list):
        entries = [entry for entry in listed if isinstance(entry, dict)]
        if entries:
            return entries
    source = payload.get("quotation")
    return [source] if isinstance(source, dict) else []


def _source_quotation(
    payload: dict[str, Any], quotation: dict[str, Any] | None = None
) -> dict[str, Any]:
    """La cotización del archivo que corresponde a la actual; si no está, la primera."""
    entries = _source_quotations(payload)
    if quotation is not None:
        for entry in entries:
            if str(entry.get("id") or "").strip() == quotation["id"]:
                return entry
    return entries[0] if entries else {}


def _is_same_quotation(quotation: dict[str, Any], payload: dict[str, Any]) -> bool:
    """Un payload sin cotización declarada se asume dirigido a la actual."""
    identifiers = [str(entry.get("id") or "").strip() for entry in _source_quotations(payload)]
    identifiers = [identifier for identifier in identifiers if identifier]
    return not identifiers or quotation["id"] in identifiers


def _items_for(quotation: dict[str, Any], payload: dict[str, Any]) -> list[Any]:
    """De un archivo con varias cotizaciones, sólo los ítems de la actual."""
    items = payload["items"]
    if len(_source_quotations(payload)) < 2:
        return items
    return [
        item
        for item in items
        if not isinstance(item, dict)
        or not item.get("quotation_id")
        or str(item["quotation_id"]) == quotation["id"]
    ]


def _match_local(
    normalized: dict[str, Any],
    local_by_id: dict[str, Any],
    local_by_name: dict[str, Any],
) -> dict[str, Any] | None:
    identifier = str(normalized.get("id") or "").strip()
    return local_by_id.get(identifier) or local_by_name.get(_normalized_name(normalized["name"]))


def _extra_field_plan(
    quotation: dict[str, Any], payload: dict[str, Any]
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[str]]:
    """Definiciones a usar al normalizar, las que faltan en el destino y avisos."""
    local = list(quotation["extra_fields"])
    local_keys = {field["key"] for field in local}
    warnings: list[str] = []
    incoming = _source_quotation(payload, quotation).get("extra_fields")
    source: list[dict[str, str]] = []
    if incoming:
        try:
            source = normalize_extra_fields(incoming)
        except ValueError as exc:
            warnings.append(f"Definición de campos extra ignorada: {exc}")
    new_definitions = [field for field in source if field["key"] not in local_keys]
    return local + new_definitions, new_definitions, warnings


def _normalize_records(
    payload: dict[str, Any], definitions: list[dict[str, str]]
) -> list[dict[str, Any]]:
    records = []
    for index, raw in enumerate(payload["items"]):
        try:
            records.append(_normalize_ai_item(raw, definitions))
        except (ImportProblem, ValueError) as exc:
            raise ImportProblem(f"Ítem {index + 1}: {exc}") from exc
    return records


def _current_photo(local: dict[str, Any]) -> str:
    return local.get("photo_url") or ("[imagen guardada]" if local.get("image_id") else "")


def _photo_diff(local: dict[str, Any], photo: str, source_kind: str) -> tuple[str, str] | None:
    current = _current_photo(local)
    if source_kind == "copy":
        # Copiar no pisa una foto que el ítem del destino ya tiene.
        if current or not photo:
            return None
        return "", ("[imagen incrustada]" if photo.startswith("data:") else photo)
    if photo == current:
        return None
    return current, photo


def _preview_after(normalized: dict[str, Any]) -> dict[str, Any]:
    """Versión liviana para la respuesta: sin id ajeno y sin data-URI de megabytes."""
    trimmed = {key: value for key, value in normalized.items() if key != "id"}
    photo = trimmed.get("photo")
    if isinstance(photo, str) and photo.startswith("data:"):
        trimmed["photo"] = "[imagen incrustada]"
    return trimmed


def _photo_changes(photo: str, name: str) -> dict[str, Any]:
    try:
        return process_photo(photo)
    except InvalidImage as exc:
        raise ImportProblem(f"No se pudo procesar la foto de {name}: {exc}") from exc


def _share_preview(quotation: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Sincroniza included contra la MISMA cotización: el flujo de revisión compartida."""
    source_quote = _source_quotation(payload, quotation)
    imported_items = _items_for(quotation, payload)
    local_by_id = {item["id"]: item for item in quotation["items"]}
    local_by_name = {_normalized_name(item["name"]): item for item in quotation["items"]}
    changes = []
    unmatched = []
    for raw in imported_items:
        if not isinstance(raw, dict) or not raw.get("name"):
            unmatched.append({"name": "Ítem sin nombre", "reason": "Registro incompleto"})
            continue
        local = local_by_id.get(str(raw.get("id", ""))) or local_by_name.get(_normalized_name(str(raw["name"])))
        if not local:
            unmatched.append({"name": str(raw["name"]), "reason": "Ya no existe en esta cotización"})
            continue
        desired = _coerce_boolean(raw.get("included", True))
        if desired != local["included"]:
            changes.append(
                {
                    "action": "include" if desired else "exclude",
                    "item_id": local["id"],
                    "name": local["name"],
                    "before": local["included"],
                    "after": desired,
                }
            )
    source_revision = source_quote.get("revision")
    warnings = []
    if source_revision and source_revision != quotation["revision"]:
        warnings.append(
            f"La cotización cambió desde la exportación (archivo {source_revision}, actual {quotation['revision']})"
        )
    if unmatched:
        warnings.append(f"{len(unmatched)} ítems no pudieron asociarse")
    return {
        "source_kind": "share",
        "source_scope": payload.get("scope", "all"),
        "source_quotation_id": source_quote.get("id"),
        "source_quotation_name": source_quote.get("name", ""),
        "source_revision": source_revision,
        "target_revision": quotation["revision"],
        "new_extra_fields": [],
        "warnings": warnings,
        "summary": {
            "include": sum(1 for change in changes if change["action"] == "include"),
            "exclude": sum(1 for change in changes if change["action"] == "exclude"),
            "update": 0,
            "add": 0,
            "unchanged": len(imported_items) - len(changes) - len(unmatched),
            "unmatched": len(unmatched),
        },
        "changes": changes,
        "unmatched": unmatched,
    }


def _merge_plan(
    quotation: dict[str, Any], payload: dict[str, Any], *, source_kind: str
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, str]], list[dict[str, str]]]:
    """Plan de copiar/actualizar ítems: sirve para el JSON de la AI y para copiar entre cotizaciones."""
    definitions, new_definitions, warnings = _extra_field_plan(quotation, payload)
    records = _normalize_records(payload, definitions)
    local_by_id = {item["id"]: item for item in quotation["items"]}
    local_by_name = {_normalized_name(item["name"]): item for item in quotation["items"]}
    changes = []
    for normalized in records:
        local = _match_local(normalized, local_by_id, local_by_name)
        if not local:
            changes.append({"action": "add", "name": normalized["name"], "after": _preview_after(normalized)})
            continue
        field_changes: dict[str, Any] = {}
        for key in ("name", "country", "purchase_link", "description", "comment", "price", "included"):
            if key in normalized and normalized[key] != local.get(key):
                field_changes[key] = {"before": local.get(key), "after": normalized[key]}
        if "extra_data" in normalized:
            merged = {**local.get("extra_data", {}), **normalized["extra_data"]}
            if merged != local.get("extra_data", {}):
                field_changes["extra_data"] = {"before": local.get("extra_data", {}), "after": merged}
        if "photo" in normalized:
            diff = _photo_diff(local, normalized["photo"], source_kind)
            if diff:
                field_changes["photo"] = {"before": diff[0], "after": diff[1]}
        if field_changes:
            changes.append(
                {"action": "update", "item_id": local["id"], "name": local["name"], "fields": field_changes}
            )
    source = _source_quotation(payload)
    if source_kind == "copy":
        origin = str(source.get("name") or source.get("id") or "otra cotización")
        warnings.insert(0, f"Los ítems provienen de «{origin}» y se copiarán a esta cotización")
        if new_definitions:
            labels = ", ".join(field["label"] for field in new_definitions)
            warnings.append(
                f"Campos extra nuevos: {labels}. Se agregarán a esta cotización si dejas marcada la casilla; "
                "los valores se copian igual"
            )
    elif new_definitions:
        warnings.append(
            "Campos extra no configurados en esta cotización: "
            + ", ".join(sorted(field["key"] for field in new_definitions))
        )
    preview = {
        "source_kind": source_kind,
        "source_scope": payload.get("scope", "all") if source_kind == "copy" else "items",
        "source_quotation_id": source.get("id"),
        "source_quotation_name": source.get("name", ""),
        "source_revision": source.get("revision"),
        "target_revision": quotation["revision"],
        "new_extra_fields": new_definitions,
        "warnings": warnings,
        "summary": {
            "include": 0,
            "exclude": 0,
            "update": sum(1 for change in changes if change["action"] == "update"),
            "add": sum(1 for change in changes if change["action"] == "add"),
            "unchanged": len(records) - len(changes),
            "unmatched": 0,
        },
        "changes": changes,
        "unmatched": [],
    }
    return preview, records, definitions, new_definitions


def _source_kind(quotation: dict[str, Any], payload: dict[str, Any]) -> str:
    """El formato ya no decide: decide de qué cotización viene el archivo."""
    if payload.get("format") == "cotizador-share":
        return "share" if _is_same_quotation(quotation, payload) else "copy"
    return "ai"


def preview_import(quotation: dict[str, Any], content: str) -> dict[str, Any]:
    payload = parse_import_content(content)
    kind = _source_kind(quotation, payload)
    if kind == "share":
        return _share_preview(quotation, payload)
    return _merge_plan(quotation, payload, source_kind=kind)[0]


def apply_import(
    quotation_id: str,
    content: str,
    expected_revision: int | None = None,
    *,
    adopt_extra_fields: bool = True,
) -> dict[str, Any]:
    quotation = db.get_quotation(quotation_id)
    if not quotation:
        raise ImportProblem("La cotización ya no existe")
    if expected_revision is not None and quotation["revision"] != expected_revision:
        raise ImportProblem(
            f"La cotización cambió después de la vista previa (era {expected_revision}, ahora {quotation['revision']})"
        )
    payload = parse_import_content(content)
    kind = _source_kind(quotation, payload)

    if kind == "share":
        preview = _share_preview(quotation, payload)
        for change in preview["changes"]:
            db.update_item(change["item_id"], {"included": change["after"]})
        return {
            "applied": len(preview["changes"]),
            "preview": preview,
            "quotation": db.get_quotation(quotation_id),
        }

    preview, records, _definitions, new_definitions = _merge_plan(quotation, payload, source_kind=kind)

    if adopt_extra_fields and new_definitions:
        merged_fields = list(quotation["extra_fields"]) + new_definitions
        if len(merged_fields) > MAX_EXTRA_FIELDS:
            raise ImportProblem(
                f"La cotización quedaría con {len(merged_fields)} campos extra y el máximo es {MAX_EXTRA_FIELDS}"
            )
        db.update_quotation(quotation_id, {"extra_fields": merged_fields})

    local_by_id = {item["id"]: item for item in quotation["items"]}
    local_by_name = {_normalized_name(item["name"]): item for item in quotation["items"]}
    applied = 0
    for normalized in records:
        local = _match_local(normalized, local_by_id, local_by_name)
        photo = normalized.get("photo")
        if local:
            changes = {key: value for key, value in normalized.items() if key not in {"id", "photo"}}
            if "extra_data" in changes:
                changes["extra_data"] = {**local.get("extra_data", {}), **changes["extra_data"]}
            try:
                validated = ItemUpdate(**changes).model_dump(exclude_unset=True)
            except ValidationError as exc:
                raise ImportProblem(
                    f"No se pudo actualizar {normalized['name']}: {exc.errors()[0]['msg']}"
                ) from exc
            validated.pop("photo", None)
            if photo is not None and _photo_diff(local, photo, kind):
                validated.update(_photo_changes(photo, normalized["name"]))
            db.update_item(local["id"], validated)
        else:
            # Nunca se reusa el id del origen: items.id es clave primaria global.
            data = {
                "name": normalized["name"],
                "country": normalized.get("country", ""),
                "purchase_link": normalized.get("purchase_link", ""),
                "description": normalized.get("description", ""),
                "comment": normalized.get("comment", ""),
                "price": normalized.get("price", ""),
                "included": normalized.get("included", True),
                "extra_data": normalized.get("extra_data", {}),
                **(_photo_changes(photo, normalized["name"]) if photo else {}),
            }
            db.create_item(quotation_id, data)
        applied += 1
    return {"applied": applied, "preview": preview, "quotation": db.get_quotation(quotation_id)}
