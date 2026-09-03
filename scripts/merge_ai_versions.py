"""Mezcla las dos versiones del catálogo y deja 5 dispositivos por cotización.

Dos IA distintas resolvieron el mismo encargo: una dejó pocos ítems pero muy
verificados (P/N exactos, checkout real a Chile, notas de durabilidad, página del
fabricante) y la otra dejó más candidatos, mejores precios y comentarios más ricos,
pero sin fotos ni columnas nuevas. Este script toma lo mejor de las dos: fija el
esquema de columnas, reemplaza los ítems por los cinco elegidos y deja cada foto
embebida en la base, para que ninguna dependa de que el vendedor mantenga la URL.

    uv run python -m scripts.merge_ai_versions <mezcla.json> --dry-run
    uv run python -m scripts.merge_ai_versions <mezcla.json>

Antes de escribir hace un respaldo y verifica las reglas que puso el usuario:
cinco ítems, todos con foto, sin campos vacíos, máximo dos ítems por marca y
máximo cuatro por país.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import shutil
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from app import db
from app.schemas import normalize_extra_fields
from app.services.images import InvalidImage, process_photo


MAX_POR_MARCA = 2
MAX_POR_PAIS = 4
ITEMS_POR_COTIZACION = 5
# Campos que nunca pueden quedar vacíos: el usuario pidió que nada quede al azar.
CAMPOS_OBLIGATORIOS = ("name", "country", "price", "purchase_link", "description", "comment")
NAVEGADOR = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
# La base rechaza fotos de más de 5 MB, y varias tiendas publican imágenes de 8 MB o más.
MAXIMO_BYTES = 5 * 1024 * 1024
LADO_MAXIMO = 1280
ADMITIDOS = {"image/webp", "image/png", "image/jpeg", "image/gif"}


class MezclaInvalida(ValueError):
    pass


def descargar_foto(url: str) -> str:
    """Baja la imagen y la devuelve como data URI para que quede dentro de la base.

    Las fichas de tienda publican imágenes de varios MB y en formatos que la base no
    reconoce, así que lo que no entra tal cual se reencoda a WebP antes de guardarlo.
    """
    peticion = urllib.request.Request(url, headers={"User-Agent": NAVEGADOR, "Accept": "image/*,*/*"})
    with urllib.request.urlopen(peticion, timeout=45) as respuesta:
        tipo = (respuesta.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        datos = respuesta.read()
    if not tipo.startswith("image/"):
        raise MezclaInvalida(f"{url} devolvió {tipo or 'sin tipo'}, no una imagen")
    if tipo in ADMITIDOS and len(datos) <= MAXIMO_BYTES:
        return f"data:{tipo};base64,{base64.b64encode(datos).decode('ascii')}"
    return f"data:image/webp;base64,{base64.b64encode(_reencodar(datos, url)).decode('ascii')}"


def _reencodar(datos: bytes, url: str) -> bytes:
    """Achica la imagen a WebP para que quepa en el límite de la base."""
    try:
        with Image.open(io.BytesIO(datos)) as abierta:
            imagen = abierta.convert("RGBA" if "transparency" in abierta.info else "RGB")
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise MezclaInvalida(f"{url}: no se pudo leer la imagen ({error})") from error
    imagen.thumbnail((LADO_MAXIMO, LADO_MAXIMO), Image.Resampling.LANCZOS)
    destino = io.BytesIO()
    imagen.save(destino, format="WEBP", quality=80, method=6)
    reducida = destino.getvalue()
    if len(reducida) > MAXIMO_BYTES:
        raise MezclaInvalida(f"{url}: la imagen sigue sobre {MAXIMO_BYTES // (1024 * 1024)} MB tras reducirla")
    return reducida


def fotos_actuales() -> dict[str, str]:
    """Fotos ya embebidas, por nombre de ítem, como data URI.

    Se leen antes de tocar nada: al borrar un ítem la base también borra su imagen
    si nadie más la usa, así que guardar el id no basta para reaprovecharla.
    """
    guardadas: dict[str, str] = {}
    for quotation in db.list_quotations():
        for item in quotation["items"]:
            if item["image_id"]:
                guardadas[item["name"]] = db.image_data_uri(item["image_id"])
    return guardadas


def resolver_foto(item: dict[str, Any], guardadas: dict[str, str]) -> str:
    """Devuelve la foto como data URI, ya sea reaprovechada o recién bajada."""
    reutilizar = item.get("reusar_foto_de")
    if reutilizar:
        if reutilizar not in guardadas:
            raise MezclaInvalida(f"{item['name']}: no hay foto guardada para «{reutilizar}»")
        return guardadas[reutilizar]
    url = (item.get("photo_url") or "").strip()
    if not url:
        raise MezclaInvalida(f"{item['name']}: sin foto (regla a)")
    try:
        return descargar_foto(url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as error:
        raise MezclaInvalida(f"{item['name']}: no se pudo bajar la foto ({error})") from error


def _revisar_foto(nombre: str, foto: str) -> None:
    """Simula lo que hará process_photo: tamaño, formato y que la imagen abra."""
    cabecera, _, contenido = foto.partition(",")
    tipo = cabecera.removeprefix("data:").removesuffix(";base64")
    if tipo not in ADMITIDOS:
        raise MezclaInvalida(f"{nombre}: formato {tipo} no admitido")
    crudo = base64.b64decode(contenido)
    if len(crudo) > MAXIMO_BYTES:
        raise MezclaInvalida(f"{nombre}: la foto pesa {len(crudo) // 1024} KB, sobre el máximo de 5 MB")
    try:
        with Image.open(io.BytesIO(crudo)) as imagen:
            imagen.verify()
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise MezclaInvalida(f"{nombre}: la foto no se puede leer ({error})") from error


def validar(quotation: dict[str, Any]) -> None:
    items = quotation["items"]
    nombre = quotation["name"]
    if len(items) != ITEMS_POR_COTIZACION:
        raise MezclaInvalida(f"{nombre}: {len(items)} ítems, se pidieron {ITEMS_POR_COTIZACION}")
    claves = {campo["key"] for campo in quotation["extra_fields"]}
    for item in items:
        for campo in CAMPOS_OBLIGATORIOS:
            if not str(item.get(campo, "")).strip():
                raise MezclaInvalida(f"{nombre} · {item.get('name')}: falta «{campo}» (regla f)")
        extra = item.get("extra_data") or {}
        faltantes = [clave for clave in claves if not str(extra.get(clave, "")).strip()]
        if faltantes:
            raise MezclaInvalida(f"{nombre} · {item['name']}: columnas vacías {faltantes} (regla f)")
        sobran = [clave for clave in extra if clave not in claves]
        if sobran:
            raise MezclaInvalida(f"{nombre} · {item['name']}: columnas no declaradas {sobran}")
        nota = extra.get("durabilidad_confiabilidad")
        if not isinstance(nota, int) or not 1 <= nota <= 10:
            raise MezclaInvalida(f"{nombre} · {item['name']}: durabilidad {nota!r} fuera de 1-10 (regla c)")
        if not str(extra.get("pagina_fabricante", "")).startswith("http"):
            raise MezclaInvalida(f"{nombre} · {item['name']}: sin página del fabricante (regla k)")
    for etiqueta, tope, cuenta in (
        ("marca", MAX_POR_MARCA, Counter(item["extra_data"]["marca"] for item in items)),
        ("país", MAX_POR_PAIS, Counter(item["country"] for item in items)),
    ):
        excedidos = {valor: total for valor, total in cuenta.items() if total > tope}
        if excedidos:
            raise MezclaInvalida(f"{nombre}: más de {tope} ítems por {etiqueta} → {excedidos}")


def aplicar(payload: dict[str, Any], dry_run: bool) -> int:
    guardadas = fotos_actuales()
    cambios = 0
    for quotation in payload["quotations"]:
        validar(quotation)
        actual = db.get_quotation(quotation["id"])
        if not actual:
            raise MezclaInvalida(f"no existe la cotización {quotation['id']}")
        print(f"\n{quotation['name']}")
        fotos = [resolver_foto(item, guardadas) for item in quotation["items"]]
        if dry_run:
            for item, foto in zip(quotation["items"], fotos):
                # Se comprueba aquí lo mismo que rechazaría el guardado, sin escribir nada.
                _revisar_foto(item["name"], foto)
                print(f"  · {item['name']} — {item['extra_data']['marca']} — {item['country']}")
            cambios += 1
            continue
        db.update_quotation(
            quotation["id"],
            {
                "name": quotation["name"],
                "description": quotation["description"],
                "extra_fields": normalize_extra_fields(quotation["extra_fields"]),
                "quote_date": quotation.get("quote_date", "2026-09-03"),
            },
        )
        for item in actual["items"]:
            db.delete_item(item["id"])
        for posicion, (item, foto) in enumerate(zip(quotation["items"], fotos)):
            try:
                embebida = process_photo(foto)
            except InvalidImage as error:
                raise MezclaInvalida(f"{item['name']}: foto inválida ({error})") from error
            db.create_item(
                quotation["id"],
                {
                    "position": posicion,
                    "name": item["name"],
                    "country": item["country"],
                    "purchase_link": item["purchase_link"],
                    "description": item["description"],
                    "comment": item["comment"],
                    "price": item["price"],
                    "included": True,
                    "extra_data": item["extra_data"],
                    **embebida,
                },
            )
            print(f"  · {item['name']}")
            cambios += 1
    return cambios


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archivo", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.archivo.read_text(encoding="utf-8"))
    if not args.dry_run:
        origen = db.database_path()
        respaldo = origen.with_name(f"{origen.stem}.backup-{datetime.now():%Y%m%d-%H%M%S}.db")
        shutil.copy2(origen, respaldo)
        print(f"respaldo · {respaldo.name}")
    try:
        cambios = aplicar(payload, args.dry_run)
    except MezclaInvalida as error:
        print(f"\n✗ {error}", file=sys.stderr)
        return 1
    print(f"\n{cambios} cambios" + (" (simulación)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
