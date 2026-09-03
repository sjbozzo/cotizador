from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .seed import load_seed_quotations


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "cotizador.db"

# El orden de las pestañas lo manda el usuario arrastrándolas; created_at sólo
# desempata cotizaciones que nunca se movieron.
_QUOTATIONS_ORDERED = "SELECT * FROM quotations ORDER BY position, created_at, rowid"


def database_path() -> Path:
    return Path(os.environ.get("COTIZADOR_DB_PATH", DEFAULT_DB_PATH)).resolve()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_database() -> None:
    with transaction() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS quotations (
                id TEXT PRIMARY KEY,
                position INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
                name TEXT NOT NULL,
                purchase_link TEXT NOT NULL DEFAULT '',
                quote_date TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                extra_fields_json TEXT NOT NULL DEFAULT '[]',
                source_file TEXT NOT NULL DEFAULT '',
                revision INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS images (
                id TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL UNIQUE,
                mime_type TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                data BLOB NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY,
                quotation_id TEXT NOT NULL REFERENCES quotations(id) ON DELETE CASCADE,
                position INTEGER NOT NULL DEFAULT 0,
                name TEXT NOT NULL,
                country TEXT NOT NULL DEFAULT '',
                photo_url TEXT NOT NULL DEFAULT '',
                image_id TEXT REFERENCES images(id) ON DELETE SET NULL,
                purchase_link TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                comment TEXT NOT NULL DEFAULT '',
                price TEXT NOT NULL DEFAULT '',
                included INTEGER NOT NULL DEFAULT 1 CHECK (included IN (0, 1)),
                extra_data_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_items_quotation_position
            ON items(quotation_id, position);

            CREATE INDEX IF NOT EXISTS idx_items_quotation_included
            ON items(quotation_id, included);

            PRAGMA user_version = 1;
            """
        )
        _migrate_quotation_positions(connection)
        _migrate_quotation_archived(connection)
        existing = connection.execute("SELECT COUNT(*) AS count FROM quotations").fetchone()["count"]
        if existing == 0:
            _insert_seed_data(connection)
        connection.execute("PRAGMA optimize")


def _migrate_quotation_positions(connection: sqlite3.Connection) -> None:
    """Agrega quotations.position a bases creadas antes del orden manual de pestañas.

    El respaldo usa el orden histórico (created_at, rowid), que es el que la app
    mostraba hasta ahora: nadie ve sus pestañas cambiar de lugar al actualizar.
    """
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(quotations)")}
    if "position" in columns:
        return
    connection.execute("ALTER TABLE quotations ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
    rows = connection.execute("SELECT id FROM quotations ORDER BY created_at, rowid").fetchall()
    for position, row in enumerate(rows):
        connection.execute("UPDATE quotations SET position = ? WHERE id = ?", (position, row["id"]))


def _migrate_quotation_archived(connection: sqlite3.Connection) -> None:
    """Agrega quotations.archived a bases creadas antes del archivado de pestañas."""
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(quotations)")}
    if "archived" in columns:
        return
    connection.execute(
        "ALTER TABLE quotations ADD COLUMN archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1))"
    )


def _insert_seed_data(connection: sqlite3.Connection) -> None:
    timestamp = now_iso()
    for position, quotation in enumerate(load_seed_quotations()):
        connection.execute(
            """
            INSERT INTO quotations (
                id, position, name, purchase_link, quote_date, description, extra_fields_json,
                source_file, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                quotation["id"],
                position,
                quotation["name"],
                quotation["purchase_link"],
                quotation["quote_date"],
                quotation["description"],
                _json_dump(quotation["extra_fields"]),
                quotation["source_file"],
                timestamp,
                timestamp,
            ),
        )
        for item in quotation["items"]:
            connection.execute(
                """
                INSERT INTO items (
                    id, quotation_id, position, name, country, photo_url, purchase_link,
                    description, comment, price, included, extra_data_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    quotation["id"],
                    item["position"],
                    item["name"],
                    item["country"],
                    item["photo"],
                    item["purchase_link"],
                    item["description"],
                    item["comment"],
                    item["price"],
                    int(item["included"]),
                    _json_dump(item["extra_data"]),
                    timestamp,
                    timestamp,
                ),
            )


def _item_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "quotation_id": row["quotation_id"],
        "position": row["position"],
        "name": row["name"],
        "country": row["country"],
        "photo": f"/api/images/{row['image_id']}" if row["image_id"] else row["photo_url"],
        "photo_url": row["photo_url"],
        "image_id": row["image_id"],
        "purchase_link": row["purchase_link"],
        "description": row["description"],
        "comment": row["comment"],
        "price": row["price"],
        "included": bool(row["included"]),
        "extra_data": json.loads(row["extra_data_json"] or "{}"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _quotation_from_row(row: sqlite3.Row, items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    quotation = {
        "id": row["id"],
        "position": row["position"],
        "archived": bool(row["archived"]),
        "name": row["name"],
        "purchase_link": row["purchase_link"],
        "quote_date": row["quote_date"],
        "description": row["description"],
        "extra_fields": json.loads(row["extra_fields_json"] or "[]"),
        "source_file": row["source_file"],
        "revision": row["revision"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if items is not None:
        quotation["items"] = items
        quotation["item_count"] = len(items)
        quotation["included_count"] = sum(1 for item in items if item["included"])
    return quotation


def list_quotations(include_items: bool = True) -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(_QUOTATIONS_ORDERED).fetchall()
        result = []
        for row in rows:
            items = None
            if include_items:
                item_rows = connection.execute(
                    "SELECT * FROM items WHERE quotation_id = ? ORDER BY position, rowid", (row["id"],)
                ).fetchall()
                items = [_item_from_row(item_row) for item_row in item_rows]
            result.append(_quotation_from_row(row, items))
        return result


def get_quotation(quotation_id: str, connection: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    owns_connection = connection is None
    connection = connection or connect()
    try:
        row = connection.execute("SELECT * FROM quotations WHERE id = ?", (quotation_id,)).fetchone()
        if not row:
            return None
        item_rows = connection.execute(
            "SELECT * FROM items WHERE quotation_id = ? ORDER BY position, rowid", (quotation_id,)
        ).fetchall()
        return _quotation_from_row(row, [_item_from_row(item_row) for item_row in item_rows])
    finally:
        if owns_connection:
            connection.close()


def create_quotation(data: dict[str, Any]) -> dict[str, Any]:
    quotation_id = data.get("id") or f"cotizacion-{uuid.uuid4().hex[:12]}"
    timestamp = now_iso()
    with transaction() as connection:
        next_position = connection.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS next_position FROM quotations"
        ).fetchone()["next_position"]
        connection.execute(
            """
            INSERT INTO quotations (
                id, position, name, purchase_link, quote_date, description, extra_fields_json,
                source_file, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', 1, ?, ?)
            """,
            (
                quotation_id,
                next_position,
                data["name"],
                data.get("purchase_link", ""),
                data["quote_date"],
                data.get("description", ""),
                _json_dump(data.get("extra_fields", [])),
                timestamp,
                timestamp,
            ),
        )
        return get_quotation(quotation_id, connection)  # type: ignore[return-value]


def update_quotation(quotation_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {
        "name": "name",
        "purchase_link": "purchase_link",
        "quote_date": "quote_date",
        "description": "description",
        "extra_fields": "extra_fields_json",
    }
    assignments = []
    values: list[Any] = []
    for key, column in allowed.items():
        if key in changes:
            assignments.append(f"{column} = ?")
            values.append(_json_dump(changes[key]) if key == "extra_fields" else changes[key])
    if not assignments:
        return get_quotation(quotation_id)
    assignments.extend(["revision = revision + 1", "updated_at = ?"])
    values.extend([now_iso(), quotation_id])
    with transaction() as connection:
        cursor = connection.execute(
            f"UPDATE quotations SET {', '.join(assignments)} WHERE id = ?", values
        )
        if cursor.rowcount == 0:
            return None
        return get_quotation(quotation_id, connection)


def reorder_quotations(order: list[str]) -> list[dict[str, Any]]:
    """Reordena las pestañas según los ids recibidos.

    No toca revision: mover una pestaña no cambia el contenido cotizado y subir
    la revisión invalidaría una vista previa de importación en curso.
    """
    with transaction() as connection:
        known = [row["id"] for row in connection.execute(_QUOTATIONS_ORDERED).fetchall()]
        unknown = [quotation_id for quotation_id in order if quotation_id not in known]
        if unknown:
            raise ValueError(f"Cotización desconocida: {unknown[0]}")
        # Las que el cliente no mencionó (creadas en otra pestaña del navegador,
        # por ejemplo) se conservan al final, en su orden actual.
        final = list(dict.fromkeys(order)) + [quotation_id for quotation_id in known if quotation_id not in set(order)]
        timestamp = now_iso()
        for position, quotation_id in enumerate(final):
            connection.execute(
                "UPDATE quotations SET position = ?, updated_at = ? WHERE id = ?",
                (position, timestamp, quotation_id),
            )
        rows = connection.execute(_QUOTATIONS_ORDERED).fetchall()
        return [_quotation_from_row(row) for row in rows]


def set_quotation_archived(quotation_id: str, archived: bool) -> dict[str, Any] | None:
    """Archiva la cotización (deja de tener pestaña) o la vuelve a mostrar.

    En ambos casos pasa al final del orden: al archivarla deja de ocupar un lugar
    entre las visibles y al restaurarla vuelve como la última pestaña, porque
    mientras estuvo guardada ese orden pudo cambiar.
    No toca revision: archivar no cambia lo cotizado, y subirla invalidaría una
    vista previa de importación en curso.
    """
    with transaction() as connection:
        if not connection.execute("SELECT 1 FROM quotations WHERE id = ?", (quotation_id,)).fetchone():
            return None
        position = connection.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS next_position FROM quotations WHERE id <> ?",
            (quotation_id,),
        ).fetchone()["next_position"]
        connection.execute(
            "UPDATE quotations SET archived = ?, position = ?, updated_at = ? WHERE id = ?",
            (int(archived), position, now_iso(), quotation_id),
        )
        return get_quotation(quotation_id, connection)


def delete_quotation(quotation_id: str) -> bool:
    with transaction() as connection:
        image_ids = [
            row["image_id"]
            for row in connection.execute(
                "SELECT image_id FROM items WHERE quotation_id = ? AND image_id IS NOT NULL", (quotation_id,)
            ).fetchall()
        ]
        cursor = connection.execute("DELETE FROM quotations WHERE id = ?", (quotation_id,))
        _remove_orphan_images(connection, image_ids)
        return cursor.rowcount > 0


def create_item(quotation_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    timestamp = now_iso()
    item_id = data.get("id") or f"item-{uuid.uuid4().hex}"
    with transaction() as connection:
        if not connection.execute("SELECT 1 FROM quotations WHERE id = ?", (quotation_id,)).fetchone():
            return None
        next_position = connection.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS next_position FROM items WHERE quotation_id = ?",
            (quotation_id,),
        ).fetchone()["next_position"]
        connection.execute(
            """
            INSERT INTO items (
                id, quotation_id, position, name, country, photo_url, image_id, purchase_link,
                description, comment, price, included, extra_data_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                quotation_id,
                data.get("position", next_position),
                data["name"],
                data.get("country", ""),
                data.get("photo_url", data.get("photo", "")),
                data.get("image_id"),
                data.get("purchase_link", ""),
                data.get("description", ""),
                data.get("comment", ""),
                data.get("price", ""),
                int(data.get("included", True)),
                _json_dump(data.get("extra_data", {})),
                timestamp,
                timestamp,
            ),
        )
        _touch_quotation(connection, quotation_id)
        row = connection.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return _item_from_row(row)


def get_item(item_id: str, connection: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    owns_connection = connection is None
    connection = connection or connect()
    try:
        row = connection.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return _item_from_row(row) if row else None
    finally:
        if owns_connection:
            connection.close()


def update_item(item_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {
        "position": "position",
        "name": "name",
        "country": "country",
        "photo_url": "photo_url",
        "image_id": "image_id",
        "purchase_link": "purchase_link",
        "description": "description",
        "comment": "comment",
        "price": "price",
        "included": "included",
        "extra_data": "extra_data_json",
    }
    assignments = []
    values: list[Any] = []
    for key, column in allowed.items():
        if key not in changes:
            continue
        value = changes[key]
        if key == "extra_data":
            value = _json_dump(value)
        elif key == "included":
            value = int(bool(value))
        assignments.append(f"{column} = ?")
        values.append(value)
    if not assignments:
        return get_item(item_id)
    assignments.append("updated_at = ?")
    values.extend([now_iso(), item_id])
    with transaction() as connection:
        previous = connection.execute("SELECT quotation_id, image_id FROM items WHERE id = ?", (item_id,)).fetchone()
        if not previous:
            return None
        connection.execute(f"UPDATE items SET {', '.join(assignments)} WHERE id = ?", values)
        _touch_quotation(connection, previous["quotation_id"])
        if "image_id" in changes and previous["image_id"] and previous["image_id"] != changes["image_id"]:
            _remove_orphan_images(connection, [previous["image_id"]])
        return get_item(item_id, connection)


class MoveError(ValueError):
    """Falla de negocio al mover un ítem entre cotizaciones."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def move_item(item_id: str, target_quotation_id: str) -> dict[str, Any]:
    """Reasigna el ítem al final de otra cotización.

    Es un UPDATE, nunca delete+create: recrear el ítem cambiaría su id y dispararía
    _remove_orphan_images, que borra la imagen cuando la fila deja de referenciarla.

    extra_data se conserva íntegro: las claves que el destino no declara quedan
    latentes y vuelven a mostrarse si el usuario define ese campo extra allí.
    """
    with transaction() as connection:
        row = connection.execute("SELECT quotation_id FROM items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise MoveError("item_not_found", "Ítem no encontrado")
        source_quotation_id = row["quotation_id"]
        if not connection.execute(
            "SELECT 1 FROM quotations WHERE id = ?", (target_quotation_id,)
        ).fetchone():
            raise MoveError("quotation_not_found", "Cotización destino no encontrada")
        if source_quotation_id == target_quotation_id:
            raise MoveError("same_quotation", "El ítem ya pertenece a esa cotización")
        next_position = connection.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS next_position FROM items WHERE quotation_id = ?",
            (target_quotation_id,),
        ).fetchone()["next_position"]
        connection.execute(
            "UPDATE items SET quotation_id = ?, position = ?, updated_at = ? WHERE id = ?",
            (target_quotation_id, next_position, now_iso(), item_id),
        )
        # Ambas: la revisión es el lock optimista que usa apply_import.
        _touch_quotation(connection, source_quotation_id)
        _touch_quotation(connection, target_quotation_id)
        return get_item(item_id, connection)  # type: ignore[return-value]


def delete_item(item_id: str) -> bool:
    with transaction() as connection:
        row = connection.execute("SELECT quotation_id, image_id FROM items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return False
        connection.execute("DELETE FROM items WHERE id = ?", (item_id,))
        _touch_quotation(connection, row["quotation_id"])
        if row["image_id"]:
            _remove_orphan_images(connection, [row["image_id"]])
        return True


def _touch_quotation(connection: sqlite3.Connection, quotation_id: str) -> None:
    connection.execute(
        "UPDATE quotations SET revision = revision + 1, updated_at = ? WHERE id = ?",
        (now_iso(), quotation_id),
    )


def _remove_orphan_images(connection: sqlite3.Connection, image_ids: list[str]) -> None:
    for image_id in set(image_ids):
        used = connection.execute("SELECT 1 FROM items WHERE image_id = ? LIMIT 1", (image_id,)).fetchone()
        if not used:
            connection.execute("DELETE FROM images WHERE id = ?", (image_id,))


def store_image(data: bytes, sha256: str, mime_type: str, width: int, height: int) -> str:
    image_id = f"img-{sha256[:20]}"
    with transaction() as connection:
        existing = connection.execute("SELECT id FROM images WHERE sha256 = ?", (sha256,)).fetchone()
        if existing:
            return existing["id"]
        connection.execute(
            "INSERT INTO images (id, sha256, mime_type, width, height, data, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (image_id, sha256, mime_type, width, height, data, now_iso()),
        )
    return image_id


def get_image(image_id: str) -> sqlite3.Row | None:
    with connect() as connection:
        return connection.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()


def image_data_uri(image_id: str) -> str:
    import base64

    row = get_image(image_id)
    if not row:
        return ""
    encoded = base64.b64encode(row["data"]).decode("ascii")
    return f"data:{row['mime_type']};base64,{encoded}"

