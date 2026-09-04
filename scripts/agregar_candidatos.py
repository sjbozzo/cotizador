"""Agrega candidatos a una cotización que ya existe, con la foto embebida en la base.

`apply_audit.py` también agrega candidatos, pero deja la foto como URL remota y corta
la lista en tres por cotización. Este script cubre el otro caso: pocos ítems, elegidos
a mano, cada uno con su foto guardada dentro de la base para que no dependa de que el
vendedor mantenga viva la imagen. La foto se toma de `photo_file` (un archivo del
repositorio) o de `photo_url` (se descarga).

    uv run python -m scripts.agregar_candidatos <archivo.json> --dry-run
    uv run python -m scripts.agregar_candidatos <archivo.json>

Antes de escribir hace un respaldo y aplica las reglas del catálogo: ningún campo ni
columna vacía, nota de durabilidad entre 1 y 10, página del fabricante distinta del
link de compra y foto obligatoria. Es idempotente: un ítem cuyo nombre ya está en la
cotización se omite, así que el archivo se puede volver a aplicar sin duplicar nada.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import sys
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from app import db
from app.services.images import InvalidImage, process_photo


ROOT = Path(__file__).resolve().parents[1]
NAVEGADOR = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
TIPOS_ADMITIDOS = {"image/webp", "image/png", "image/jpeg", "image/gif"}
EXTENSIONES = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}
# Los mismos campos que exige la mezcla: el usuario pidió que nada quede al azar.
CAMPOS_OBLIGATORIOS = ("name", "country", "price", "purchase_link", "description", "comment")


class CandidatoInvalido(ValueError):
    pass


def _normalizar(valor: str) -> str:
    """Nombre comparable, para no agregar dos veces el mismo equipo."""
    valor = unicodedata.normalize("NFKD", valor)
    valor = "".join(caracter for caracter in valor if not unicodedata.combining(caracter))
    return re.sub(r"[^a-z0-9]+", " ", valor.lower()).strip()


def resolver_foto(pick: dict[str, Any]) -> str:
    """Devuelve la foto como data URI, desde un archivo del repositorio o de la web."""
    archivo = (pick.get("photo_file") or "").strip()
    if archivo:
        ruta = Path(archivo)
        ruta = ruta if ruta.is_absolute() else ROOT / ruta
        if not ruta.is_file():
            raise CandidatoInvalido(f"{pick['name']}: no existe la foto {archivo}")
        tipo = EXTENSIONES.get(ruta.suffix.lower())
        if not tipo:
            raise CandidatoInvalido(f"{pick['name']}: formato de foto no admitido ({ruta.suffix})")
        return f"data:{tipo};base64,{base64.b64encode(ruta.read_bytes()).decode('ascii')}"
    url = (pick.get("photo_url") or "").strip()
    if not url:
        raise CandidatoInvalido(f"{pick['name']}: sin foto")
    peticion = urllib.request.Request(url, headers={"User-Agent": NAVEGADOR, "Accept": "image/*,*/*"})
    try:
        with urllib.request.urlopen(peticion, timeout=45) as respuesta:
            tipo = (respuesta.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            datos = respuesta.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as error:
        raise CandidatoInvalido(f"{pick['name']}: no se pudo bajar la foto ({error})") from error
    if tipo not in TIPOS_ADMITIDOS:
        raise CandidatoInvalido(f"{pick['name']}: {url} devolvió {tipo or 'sin tipo'}, no una imagen admitida")
    return f"data:{tipo};base64,{base64.b64encode(datos).decode('ascii')}"


def validar(pick: dict[str, Any], claves: set[str]) -> None:
    nombre = pick.get("name") or "(sin nombre)"
    for campo in CAMPOS_OBLIGATORIOS:
        if not str(pick.get(campo, "")).strip():
            raise CandidatoInvalido(f"{nombre}: falta «{campo}»")
    extra = pick.get("extra_data") or {}
    faltantes = [clave for clave in claves if not str(extra.get(clave, "")).strip()]
    if faltantes:
        raise CandidatoInvalido(f"{nombre}: columnas vacías {faltantes}")
    sobran = [clave for clave in extra if clave not in claves]
    if sobran:
        raise CandidatoInvalido(f"{nombre}: columnas no declaradas en la cotización {sobran}")
    nota = extra.get("durabilidad_confiabilidad")
    if not isinstance(nota, int) or not 1 <= nota <= 10:
        raise CandidatoInvalido(f"{nombre}: durabilidad {nota!r} fuera de 1-10")
    fabricante = str(extra.get("pagina_fabricante", ""))
    if not fabricante.startswith("http"):
        raise CandidatoInvalido(f"{nombre}: sin página del fabricante")
    if fabricante == str(pick.get("purchase_link", "")):
        raise CandidatoInvalido(f"{nombre}: la página del fabricante repite el link de compra")


def aplicar(payload: dict[str, Any], dry_run: bool) -> int:
    agregados = 0
    for entrada in payload["quotations"]:
        cotizacion = db.get_quotation(entrada["quotation_id"])
        if not cotizacion:
            raise CandidatoInvalido(f"no existe la cotización {entrada['quotation_id']}")
        claves = {campo["key"] for campo in cotizacion["extra_fields"]}
        presentes = {_normalizar(item["name"]) for item in cotizacion["items"]}
        posicion = max((item["position"] for item in cotizacion["items"]), default=-1) + 1
        print(f"\n{cotizacion['name']} ({len(cotizacion['items'])} ítems)")
        for pick in entrada.get("picks", []):
            # «Stock y despacho a Chile» viene aparte porque es la columna que
            # rellenan todos los flujos de auditoría; se une antes de validar.
            extra = dict(pick.get("extra_data") or {})
            if pick.get("availability_chile") and "disponibilidad_chile" in claves:
                extra["disponibilidad_chile"] = str(pick["availability_chile"]).strip()
            validar(pick | {"extra_data": extra}, claves)
            if _normalizar(pick["name"]) in presentes:
                print(f"  ya estaba · {pick['name']}")
                continue
            foto = resolver_foto(pick)
            datos = {
                "position": posicion,
                "name": pick["name"],
                "country": pick["country"],
                "purchase_link": pick["purchase_link"],
                "description": pick["description"],
                "comment": pick["comment"],
                "price": pick["price"],
                "included": True,
                "extra_data": extra,
            }
            print(f"  agregar · {pick['name']}")
            agregados += 1
            presentes.add(_normalizar(pick["name"]))
            posicion += 1
            if dry_run:
                continue
            try:
                datos.update(process_photo(foto))
            except InvalidImage as error:
                raise CandidatoInvalido(f"{pick['name']}: foto inválida ({error})") from error
            db.create_item(cotizacion["id"], datos)
    return agregados


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archivo", type=Path, help="JSON con {\"quotations\": [{\"quotation_id\", \"picks\": [...]}]}")
    parser.add_argument("--dry-run", action="store_true", help="muestra los cambios sin escribirlos")
    args = parser.parse_args()

    payload = json.loads(args.archivo.read_text(encoding="utf-8"))
    if "quotations" not in payload:
        print("El archivo no trae la clave «quotations»", file=sys.stderr)
        return 1
    db.init_database()
    print(f"Base de datos: {db.database_path()}")
    if not args.dry_run:
        origen = db.database_path()
        respaldo = origen.with_name(f"{origen.stem}.backup-{datetime.now():%Y%m%d-%H%M%S}.db")
        shutil.copy2(origen, respaldo)
        print(f"respaldo · {respaldo.name}")
    try:
        agregados = aplicar(payload, args.dry_run)
    except CandidatoInvalido as error:
        print(f"\n✗ {error}", file=sys.stderr)
        return 1
    print(f"\n{agregados} ítem(s) agregado(s)" + (" (simulación)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
