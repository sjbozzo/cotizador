from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from .schemas import (
    ImportApply,
    ImportContent,
    ItemCreate,
    ItemMove,
    ItemUpdate,
    QuotationArchive,
    QuotationCreate,
    QuotationReorder,
    QuotationUpdate,
)
from .services.exports import (
    build_bom_payload,
    build_multi_share_payload,
    build_share_payload,
    filename_slug,
    payload_json,
    render_bom_html,
    render_standalone_html,
    share_filename,
)
from .services.excel import build_workbook
from .services.images import InvalidImage, process_photo
from .services.imports import ImportProblem, apply_import, preview_import
from .services.pdf import build_pdf
from .services.prompts import build_formatting_prompt


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_database()
    yield


APP_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="Cotizador Edge AI",
    version="1.0.0",
    description="Cotizaciones técnicas editables con importación y exportación segura.",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response


def _quotation_or_404(quotation_id: str) -> dict:
    quotation = db.get_quotation(quotation_id)
    if not quotation:
        raise HTTPException(status_code=404, detail="Cotización no encontrada")
    return quotation


def _unique(values: list[str]) -> list[str]:
    """Conserva el orden elegido y descarta repeticiones."""
    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def _model_changes(model) -> dict:
    return model.model_dump(mode="json", exclude_unset=True)


def _asset_version() -> str:
    """Huella de los estáticos para invalidar la caché del navegador en cada cambio."""
    stamps = []
    for name in ("app.js", "styles.css"):
        try:
            stamps.append(int((APP_DIR / "static" / name).stat().st_mtime))
        except OSError:
            stamps.append(0)
    return str(max(stamps))


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"app_name": "Cotizador Edge AI", "asset_version": _asset_version()},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/quotations")
def quotations() -> list[dict]:
    return db.list_quotations(include_items=True)


@app.post("/api/quotations", status_code=status.HTTP_201_CREATED)
def add_quotation(payload: QuotationCreate) -> dict:
    return db.create_quotation(payload.model_dump(mode="json"))


@app.post("/api/quotations/reorder")
def reorder_quotations(payload: QuotationReorder) -> list[dict]:
    try:
        return db.reorder_quotations(payload.order)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/api/quotations/{quotation_id}")
def edit_quotation(quotation_id: str, payload: QuotationUpdate) -> dict:
    quotation = db.update_quotation(quotation_id, _model_changes(payload))
    if not quotation:
        raise HTTPException(status_code=404, detail="Cotización no encontrada")
    return quotation


@app.post("/api/quotations/{quotation_id}/archive")
def archive_quotation(quotation_id: str, payload: QuotationArchive) -> dict:
    quotation = db.set_quotation_archived(quotation_id, payload.archived)
    if not quotation:
        raise HTTPException(status_code=404, detail="Cotización no encontrada")
    return quotation


@app.delete("/api/quotations/{quotation_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_quotation(quotation_id: str) -> Response:
    if not db.delete_quotation(quotation_id):
        raise HTTPException(status_code=404, detail="Cotización no encontrada")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/quotations/{quotation_id}/items", status_code=status.HTTP_201_CREATED)
def add_item(quotation_id: str, payload: ItemCreate) -> dict:
    data = payload.model_dump(mode="json")
    photo = data.pop("photo")
    try:
        data.update(process_photo(photo))
    except InvalidImage as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    item = db.create_item(quotation_id, data)
    if not item:
        raise HTTPException(status_code=404, detail="Cotización no encontrada")
    return item


@app.patch("/api/items/{item_id}")
def edit_item(item_id: str, payload: ItemUpdate) -> dict:
    data = _model_changes(payload)
    if "photo" in data:
        try:
            data.update(process_photo(data.pop("photo")))
        except InvalidImage as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    item = db.update_item(item_id, data)
    if not item:
        raise HTTPException(status_code=404, detail="Ítem no encontrado")
    return item


_MOVE_ERROR_STATUS = {
    "item_not_found": 404,
    "quotation_not_found": 404,
    "same_quotation": 409,
}


@app.post("/api/items/{item_id}/move")
def move_item(item_id: str, payload: ItemMove) -> dict:
    try:
        return db.move_item(item_id, payload.quotation_id)
    except db.MoveError as exc:
        raise HTTPException(status_code=_MOVE_ERROR_STATUS.get(exc.code, 409), detail=str(exc)) from exc


@app.delete("/api/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_item(item_id: str) -> Response:
    if not db.delete_item(item_id):
        raise HTTPException(status_code=404, detail="Ítem no encontrado")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/images/{image_id}")
def image(image_id: str) -> Response:
    row = db.get_image(image_id)
    if not row:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")
    return Response(
        content=row["data"],
        media_type=row["mime_type"],
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/api/quotations/{quotation_id}/export.pdf")
def export_pdf(
    quotation_id: str,
    scope: Annotated[str, Query(pattern="^(included|all)$")] = "included",
    item_id: Annotated[list[str] | None, Query()] = None,
) -> Response:
    quotation = _quotation_or_404(quotation_id)
    content = build_pdf(quotation, scope=scope, item_ids=set(item_id) if item_id else None)
    filename = f"{filename_slug(quotation['name'])}.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/export.xlsx")
def export_excel_bundle(
    quotation_id: Annotated[list[str], Query(min_length=1)],
    scope: Annotated[str, Query(pattern="^(included|all)$")] = "all",
) -> Response:
    quotations = [_quotation_or_404(identifier) for identifier in _unique(quotation_id)]
    content = build_workbook(quotations, scope=scope)
    stem = filename_slug(quotations[0]["name"]) if len(quotations) == 1 else "cotizaciones"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{stem}.xlsx"'},
    )


@app.get("/api/export.html")
def export_html_bundle(
    quotation_id: Annotated[list[str], Query(min_length=1)],
    scope: Annotated[str, Query(pattern="^(included|all)$")] = "all",
) -> Response:
    quotations = [_quotation_or_404(identifier) for identifier in _unique(quotation_id)]
    payload = build_multi_share_payload(quotations, scope=scope)
    content = render_standalone_html(payload)
    filename = share_filename(payload, "-compartir.html")
    return Response(
        content=content,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/export-bom.html")
def export_bom() -> Response:
    quotations = [quotation for quotation in db.list_quotations(include_items=True) if not quotation["archived"]]
    if not quotations:
        raise HTTPException(status_code=404, detail="No hay cotizaciones activas para exportar")
    payload = build_bom_payload(quotations)
    return Response(
        content=render_bom_html(payload),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="BOM.html"'},
    )


@app.get("/api/quotations/{quotation_id}/export.html")
def export_html(
    quotation_id: str,
    scope: Annotated[str, Query(pattern="^(included|all)$")] = "all",
    item_id: Annotated[list[str] | None, Query()] = None,
) -> Response:
    quotation = _quotation_or_404(quotation_id)
    payload = build_share_payload(quotation, scope=scope, item_ids=set(item_id) if item_id else None)
    content = render_standalone_html(payload)
    filename = f"{filename_slug(quotation['name'])}-compartir.html"
    return Response(
        content=content,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/quotations/{quotation_id}/export.json")
def export_json(
    quotation_id: str,
    scope: Annotated[str, Query(pattern="^(included|all)$")] = "included",
    item_id: Annotated[list[str] | None, Query()] = None,
) -> Response:
    quotation = _quotation_or_404(quotation_id)
    payload = build_share_payload(quotation, scope=scope, item_ids=set(item_id) if item_id else None)
    filename = f"{filename_slug(quotation['name'])}.json"
    return Response(
        content=payload_json(payload),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/quotations/{quotation_id}/formatting-prompt")
def formatting_prompt(quotation_id: str) -> dict[str, str]:
    quotation = _quotation_or_404(quotation_id)
    return {"prompt": build_formatting_prompt(quotation)}


@app.post("/api/quotations/{quotation_id}/import/preview")
def import_preview(quotation_id: str, payload: ImportContent) -> dict:
    quotation = _quotation_or_404(quotation_id)
    try:
        return preview_import(quotation, payload.content)
    except (ImportProblem, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/quotations/{quotation_id}/import/apply")
def import_apply(quotation_id: str, payload: ImportApply) -> dict:
    _quotation_or_404(quotation_id)
    try:
        return apply_import(
            quotation_id,
            payload.content,
            payload.expected_revision,
            adopt_extra_fields=payload.adopt_extra_fields,
        )
    except (ImportProblem, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.exception_handler(404)
async def not_found(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": exc.detail})
