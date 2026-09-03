from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from xml.etree import ElementTree

from PIL import Image
from pypdf import PdfReader

from app.services.excel import quotation_sheet


def _embedded_payload(html: str) -> dict:
    match = re.search(r'<script id="quotation-data" type="text/plain">([^<]+)</script>', html)
    assert match, "el HTML exportado no trae el bloque quotation-data"
    return json.loads(base64.b64decode(match.group(1).strip()))


def test_tops_dynamic_field_is_exported_immediately_after_price():
    quotation = {
        "name": "Aceleradores M.2",
        "extra_fields": [
            {"key": "formato", "label": "Formato M.2", "type": "text"},
            {"key": "tops", "label": "TOPS", "type": "text"},
            {"key": "pagina_fabricante", "label": "Página del fabricante", "type": "url"},
        ],
    }
    item = {
        "included": True,
        "name": "Módulo",
        "price": "US$100",
        "description": "Descripción",
        "comment": "Comentario",
        "country": "Chile",
        "purchase_link": "https://example.com/comprar",
        "extra_data": {
            "formato": "M.2 2280",
            "tops": "10 TOPS INT8",
            "pagina_fabricante": "https://example.com/fabricante",
        },
    }

    _name, rows, widths = quotation_sheet(quotation, [item])

    assert rows[0][:4] == ["Incluir", "Nombre", "Precio", "TOPS"]
    assert rows[1][:4] == ["Sí", "Módulo", "US$100", "10 TOPS INT8"]
    assert len(rows[0]) == len(rows[1]) == len(widths)


def test_initial_api_loads_all_curated_data(client):
    response = client.get("/api/quotations")
    assert response.status_code == 200
    quotations = response.json()
    assert len(quotations) == 9
    assert sum(quotation["item_count"] for quotation in quotations) == 96
    assert all(quotation["included_count"] == quotation["item_count"] for quotation in quotations)


def test_quotation_and_item_crud_with_dynamic_fields(client):
    created = client.post(
        "/api/quotations",
        json={
            "name": "Prueba industrial",
            "quote_date": "2026-08-30",
            "purchase_link": "https://example.com/catalogo",
            "description": "Cotización de prueba",
            "extra_fields": ["incluye_ventilador:boolean", {"key": "tops", "label": "TOPS", "type": "number"}],
        },
    )
    assert created.status_code == 201
    quotation = created.json()
    assert [field["key"] for field in quotation["extra_fields"]] == ["incluye_ventilador", "tops"]

    item_response = client.post(
        f"/api/quotations/{quotation['id']}/items",
        json={
            "name": "Nodo de prueba",
            "country": "Chile",
            "photo": "",
            "purchase_link": "https://example.com/nodo",
            "description": "Equipo demostrativo",
            "comment": "Sin observaciones",
            "price": "$100.000 CLP",
            "included": True,
            "extra_data": {"incluye_ventilador": False, "tops": 12.5},
        },
    )
    assert item_response.status_code == 201
    item = item_response.json()
    assert item["extra_data"]["tops"] == 12.5

    updated = client.patch(f"/api/items/{item['id']}", json={"included": False, "comment": "Descartado"})
    assert updated.status_code == 200
    assert updated.json()["included"] is False
    assert updated.json()["comment"] == "Descartado"

    assert client.delete(f"/api/items/{item['id']}").status_code == 204
    assert client.delete(f"/api/quotations/{quotation['id']}").status_code == 204


def test_uploaded_photo_is_compressed_to_webp(client):
    quotation = client.get("/api/quotations").json()[0]
    source = io.BytesIO()
    Image.new("RGB", (1800, 900), "#cc5533").save(source, format="PNG")
    data_uri = "data:image/png;base64," + base64.b64encode(source.getvalue()).decode("ascii")
    response = client.post(
        f"/api/quotations/{quotation['id']}/items",
        json={"name": "Producto con foto", "photo": data_uri, "extra_data": {}},
    )
    assert response.status_code == 201
    item = response.json()
    assert item["image_id"].startswith("img-")
    assert item["photo"].startswith("/api/images/")
    image_response = client.get(item["photo"])
    assert image_response.status_code == 200
    assert image_response.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(image_response.content)) as rendered:
        assert rendered.format == "WEBP"
        assert max(rendered.size) == 1280


def test_exports_create_standalone_html_json_and_readable_pdf(client):
    quotation = client.get("/api/quotations").json()[0]
    html_response = client.get(f"/api/quotations/{quotation['id']}/export.html")
    assert html_response.status_code == 200
    assert "attachment" in html_response.headers["content-disposition"]
    assert 'id="quotation-data"' in html_response.text

    json_response = client.get(f"/api/quotations/{quotation['id']}/export.json")
    payload = json_response.json()
    assert payload["format"] == "cotizador-share"
    assert len(payload["items"]) == quotation["included_count"]

    pdf_response = client.get(f"/api/quotations/{quotation['id']}/export.pdf")
    assert pdf_response.status_code == 200
    assert pdf_response.content.startswith(b"%PDF")
    reader = PdfReader(io.BytesIO(pdf_response.content))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert quotation["items"][0]["name"] in extracted
    assert "Página 1" in extracted


def test_shared_selection_round_trip_previews_then_applies(client):
    quotation = client.get("/api/quotations").json()[1]
    export = client.get(f"/api/quotations/{quotation['id']}/export.json?scope=all").json()
    export["items"][0]["included"] = False
    content = json.dumps(export, ensure_ascii=False)

    preview_response = client.post(
        f"/api/quotations/{quotation['id']}/import/preview", json={"content": content}
    )
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["source_kind"] == "share"
    assert preview["summary"]["exclude"] == 1

    apply_response = client.post(
        f"/api/quotations/{quotation['id']}/import/apply",
        json={"content": content, "expected_revision": preview["target_revision"]},
    )
    assert apply_response.status_code == 200
    changed = apply_response.json()["quotation"]
    assert next(item for item in changed["items"] if item["id"] == export["items"][0]["id"])["included"] is False


def test_ai_json_can_update_and_add_after_preview(client):
    quotation = client.get("/api/quotations").json()[2]
    existing = quotation["items"][0]
    payload = {
        "format": "cotizador-ai-import",
        "version": 1,
        "quotation": {"id": quotation["id"]},
        "items": [
            {"id": existing["id"], "name": existing["name"], "comentario": "Confirmado por proveedor"},
            {
                "nombre": "Nuevo host de laboratorio",
                "pais_origen": "Chile",
                "precio": "$250.000 CLP",
                "link_compra": "https://example.com/host",
                "incluir": True,
                "campos_extra": {"espacio_m2": "Sí", "subtipo": "Mini PC"},
            },
        ],
    }
    content = json.dumps(payload, ensure_ascii=False)
    preview = client.post(f"/api/quotations/{quotation['id']}/import/preview", json={"content": content}).json()
    assert preview["summary"]["update"] == 1
    assert preview["summary"]["add"] == 1
    applied = client.post(
        f"/api/quotations/{quotation['id']}/import/apply",
        json={"content": content, "expected_revision": preview["target_revision"]},
    )
    assert applied.status_code == 200
    items = applied.json()["quotation"]["items"]
    assert next(item for item in items if item["id"] == existing["id"])["comment"] == "Confirmado por proveedor"
    new_item = next(item for item in items if item["name"] == "Nuevo host de laboratorio")
    assert new_item["extra_data"] == {"espacio_m2": True, "subtipo": "Mini PC"}


def test_html_import_reads_only_declarative_payload(client):
    quotation = client.get("/api/quotations").json()[0]
    payload = client.get(f"/api/quotations/{quotation['id']}/export.json?scope=all").json()
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    html = f'<html><script>throw new Error("must not run")</script><script id="quotation-data" type="text/plain">{encoded}</script></html>'
    response = client.post(f"/api/quotations/{quotation['id']}/import/preview", json={"content": html})
    assert response.status_code == 200
    assert response.json()["source_kind"] == "share"


def test_prompt_contains_exact_dynamic_schema(client):
    quotation = client.get("/api/quotations").json()[0]
    response = client.get(f"/api/quotations/{quotation['id']}/formatting-prompt")
    assert response.status_code == 200
    prompt = response.json()["prompt"]
    assert 'format="cotizador-ai-import"' in prompt
    assert "compatibilidad_yolo: text" in prompt
    assert "Devuelve ÚNICAMENTE JSON válido" in prompt


def test_unsafe_links_are_rejected(client):
    quotation = client.get("/api/quotations").json()[0]
    response = client.post(
        f"/api/quotations/{quotation['id']}/items",
        json={"name": "Ataque", "purchase_link": "javascript:alert(1)", "photo": "", "extra_data": {}},
    )
    assert response.status_code == 422


def test_export_excel_writes_one_readable_sheet_per_quotation(client):
    quotations = client.get("/api/quotations").json()
    first, second = quotations[0], quotations[1]
    response = client.get(
        "/api/export.xlsx",
        params={"quotation_id": [first["id"], second["id"]], "scope": "all"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "cotizaciones.xlsx" in response.headers["content-disposition"]

    book = zipfile.ZipFile(io.BytesIO(response.content))
    assert book.testzip() is None
    for part in ("[Content_Types].xml", "xl/workbook.xml", "xl/styles.xml", "xl/worksheets/sheet2.xml"):
        assert part in book.namelist()
        ElementTree.fromstring(book.read(part))

    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    names = [sheet.get("name") for sheet in ElementTree.fromstring(book.read("xl/workbook.xml")).iter(f"{namespace}sheet")]
    assert names == [first["name"][:31], second["name"][:31]]
    sheet = ElementTree.fromstring(book.read("xl/worksheets/sheet1.xml"))
    rows = list(sheet.iter(f"{namespace}row"))
    assert len(rows) == first["item_count"] + 1
    header = ["".join(node.text or "" for node in cell.iter(f"{namespace}t")) for cell in rows[0]]
    assert header[:6] == ["Incluir", "Nombre", "Precio", "Descripción", "Comentario", "País de origen"]


def test_export_excel_honours_the_included_scope(client):
    quotation = client.get("/api/quotations").json()[0]
    client.patch(f"/api/items/{quotation['items'][0]['id']}", json={"included": False})
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

    def sheet_rows(scope: str) -> int:
        content = client.get(
            "/api/export.xlsx", params={"quotation_id": quotation["id"], "scope": scope}
        ).content
        sheet = ElementTree.fromstring(zipfile.ZipFile(io.BytesIO(content)).read("xl/worksheets/sheet1.xml"))
        return len(list(sheet.iter(f"{namespace}row"))) - 1

    assert sheet_rows("all") == quotation["item_count"]
    assert sheet_rows("included") == quotation["item_count"] - 1


def test_exported_share_html_only_saves_and_never_calls_the_server(client):
    quotation = client.get("/api/quotations").json()[0]
    html = client.get(f"/api/quotations/{quotation['id']}/export.html").text
    assert 'id="saveHtml"' in html
    assert ">Guardar<" in html
    assert "showSaveFilePicker" in html
    for gone in ('id="exportJson"', 'id="shareFile"', "navigator.share", "Exportar JSON", "fetch("):
        assert gone not in html
    assert "compartir.html" in html
    assert 'id="saveExcel"' in html and "excelBlob" in html


def test_exported_html_reuses_the_application_look_and_reads_like_it(client):
    quotation = client.get("/api/quotations").json()[0]
    html = client.get(f"/api/quotations/{quotation['id']}/export.html").text
    # La hoja de la aplicación viaja incrustada: mismo aspecto y sin pedir red.
    assert "--accent:#9ae000" in html
    assert 'class="app-header"' in html and 'class="tabs-shell"' in html
    assert "https://" not in html.split("<script id=\"quotation-data\"")[0]
    # Ficha de sólo lectura y encabezados que ordenan.
    assert 'id="itemModal"' in html
    assert "control.readOnly = true" in html
    assert "aria-sort" in html


def test_export_html_bundle_carries_every_chosen_quotation(client):
    quotations = client.get("/api/quotations").json()
    first, second = quotations[0], quotations[1]
    response = client.get(
        "/api/export.html",
        params={"quotation_id": [first["id"], second["id"]], "scope": "all"},
    )
    assert response.status_code == 200
    assert "cotizaciones-compartir.html" in response.headers["content-disposition"]
    payload = _embedded_payload(response.text)
    assert [entry["id"] for entry in payload["quotations"]] == [first["id"], second["id"]]
    assert payload["scope"] == "all"
    assert len(payload["items"]) == first["item_count"] + second["item_count"]
    assert {item["quotation_id"] for item in payload["items"]} == {first["id"], second["id"]}


def test_export_html_scope_included_leaves_the_discarded_out(client):
    quotation = client.get("/api/quotations").json()[0]
    discarded = quotation["items"][0]
    client.patch(f"/api/items/{discarded['id']}", json={"included": False})

    everything = _embedded_payload(
        client.get("/api/export.html", params={"quotation_id": quotation["id"], "scope": "all"}).text
    )
    only_included = _embedded_payload(
        client.get("/api/export.html", params={"quotation_id": quotation["id"], "scope": "included"}).text
    )
    assert len(everything["items"]) == quotation["item_count"]
    assert len(only_included["items"]) == quotation["item_count"] - 1
    assert only_included["scope"] == "selection"
    assert discarded["id"] not in {item["id"] for item in only_included["items"]}


def test_export_html_requires_an_existing_quotation(client):
    assert client.get("/api/export.html", params={"quotation_id": "no-existe"}).status_code == 404
    assert client.get("/api/export.html").status_code == 422


def test_bundle_import_only_touches_the_quotation_it_goes_into(client):
    quotations = client.get("/api/quotations").json()
    first, second = quotations[0], quotations[1]
    html = client.get(
        "/api/export.html",
        params={"quotation_id": [first["id"], second["id"]], "scope": "all"},
    ).text
    payload = _embedded_payload(html)
    for item in payload["items"]:
        item["included"] = False
    content = json.dumps(payload, ensure_ascii=False)

    preview = client.post(
        f"/api/quotations/{second['id']}/import/preview", json={"content": content}
    ).json()
    assert preview["source_kind"] == "share"
    assert preview["source_quotation_id"] == second["id"]
    # Sólo cuenta los ítems de la cotización de destino: los de la otra ni se miran.
    assert preview["summary"]["exclude"] == second["item_count"]
    assert preview["summary"]["unmatched"] == 0

    applied = client.post(
        f"/api/quotations/{second['id']}/import/apply",
        json={"content": content, "expected_revision": preview["target_revision"]},
    )
    assert applied.status_code == 200
    assert applied.json()["applied"] == second["item_count"]
    assert client.get("/api/quotations").json()[0]["included_count"] == first["included_count"]


def test_browser_saved_share_html_round_trips_into_import(client):
    quotation = client.get("/api/quotations").json()[1]
    html = client.get(f"/api/quotations/{quotation['id']}/export.html?scope=all").text
    match = re.search(r'<script id="quotation-data" type="text/plain">([^<]+)</script>', html)
    assert match
    payload = json.loads(base64.b64decode(match.group(1).strip()))
    payload["items"][0]["included"] = False
    resaved = base64.b64encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).decode()
    saved_html = "<!doctype html>\n" + html[: match.start(1)] + resaved + html[match.end(1) :]

    preview = client.post(
        f"/api/quotations/{quotation['id']}/import/preview", json={"content": saved_html}
    ).json()
    assert preview["source_kind"] == "share"
    assert preview["summary"]["exclude"] == 1
    applied = client.post(
        f"/api/quotations/{quotation['id']}/import/apply",
        json={"content": saved_html, "expected_revision": preview["target_revision"]},
    )
    assert applied.status_code == 200
    assert applied.json()["applied"] == 1


def test_exported_json_can_be_imported_into_a_different_quotation(client):
    quotations = client.get("/api/quotations").json()
    source = next(q for q in quotations if q["id"] == "raspberry-pi5-carcasas-hailo")
    target = next(q for q in quotations if q["id"] == "raspberry-pi5-gabinetes-ip-hailo")
    exported = client.get(f"/api/quotations/{source['id']}/export.json?scope=all").text

    preview = client.post(
        f"/api/quotations/{target['id']}/import/preview", json={"content": exported}
    ).json()
    assert preview["source_kind"] == "copy"
    assert preview["summary"]["add"] == source["item_count"]
    assert preview["summary"]["unmatched"] == 0
    assert any("provienen" in warning for warning in preview["warnings"])

    applied = client.post(
        f"/api/quotations/{target['id']}/import/apply",
        json={"content": exported, "expected_revision": preview["target_revision"]},
    )
    assert applied.status_code == 200
    assert applied.json()["applied"] == source["item_count"]
    merged = applied.json()["quotation"]
    assert merged["item_count"] == target["item_count"] + source["item_count"]
    # Los ids del origen nunca se reusan: items.id es clave primaria global.
    source_ids = {item["id"] for item in source["items"]}
    assert not source_ids & {item["id"] for item in merged["items"]}
    copied = next(item for item in merged["items"] if item["name"] == source["items"][0]["name"])
    assert copied["photo"] == source["items"][0]["photo"]


def test_importing_the_same_export_twice_updates_instead_of_duplicating(client):
    quotations = client.get("/api/quotations").json()
    source = next(q for q in quotations if q["id"] == "edge-ai-aceleradores-m2")
    target = next(q for q in quotations if q["id"] == "edge-ai-vision")
    exported = client.get(f"/api/quotations/{source['id']}/export.json?scope=all").text

    for _ in range(2):
        preview = client.post(
            f"/api/quotations/{target['id']}/import/preview", json={"content": exported}
        ).json()
        client.post(
            f"/api/quotations/{target['id']}/import/apply",
            json={"content": exported, "expected_revision": preview["target_revision"]},
        )
    final = client.get("/api/quotations").json()
    merged = next(q for q in final if q["id"] == target["id"])
    assert merged["item_count"] == target["item_count"] + source["item_count"]


def test_copying_between_quotations_adopts_missing_extra_fields(client):
    quotations = client.get("/api/quotations").json()
    source = next(q for q in quotations if q["id"] == "aceleradores-usb-yolo")
    target = next(q for q in quotations if q["id"] == "edge-ai-vision")
    source_keys = {field["key"] for field in source["extra_fields"]}
    target_keys = {field["key"] for field in target["extra_fields"]}
    assert source_keys - target_keys

    exported = client.get(f"/api/quotations/{source['id']}/export.json?scope=all").text
    preview = client.post(
        f"/api/quotations/{target['id']}/import/preview", json={"content": exported}
    ).json()
    assert {field["key"] for field in preview["new_extra_fields"]} == source_keys - target_keys

    applied = client.post(
        f"/api/quotations/{target['id']}/import/apply",
        json={"content": exported, "expected_revision": preview["target_revision"]},
    ).json()
    adopted = {field["key"] for field in applied["quotation"]["extra_fields"]}
    assert source_keys <= adopted
    # Un campo declarado number debe llegar tipado, no como texto.
    copied = next(
        item for item in applied["quotation"]["items"] if item["name"] == "Google Coral USB Accelerator"
    )
    assert isinstance(copied["extra_data"]["ranking_recomendado"], int)


def test_copying_can_skip_adopting_extra_fields(client):
    quotations = client.get("/api/quotations").json()
    source = next(q for q in quotations if q["id"] == "aceleradores-usb-yolo")
    target = next(q for q in quotations if q["id"] == "edge-ai-vision")
    exported = client.get(f"/api/quotations/{source['id']}/export.json?scope=all").text
    preview = client.post(
        f"/api/quotations/{target['id']}/import/preview", json={"content": exported}
    ).json()
    applied = client.post(
        f"/api/quotations/{target['id']}/import/apply",
        json={
            "content": exported,
            "expected_revision": preview["target_revision"],
            "adopt_extra_fields": False,
        },
    ).json()
    assert {f["key"] for f in applied["quotation"]["extra_fields"]} == {
        f["key"] for f in target["extra_fields"]
    }
    # Los valores viajan igual aunque el campo no se declare.
    copied = next(
        item for item in applied["quotation"]["items"] if item["name"] == "Google Coral USB Accelerator"
    )
    assert copied["extra_data"]["acelerador"]


def test_ai_import_merges_extra_data_instead_of_replacing_it(client):
    quotation = client.get("/api/quotations").json()[1]
    existing = quotation["items"][0]
    assert existing["extra_data"]
    kept_key = sorted(existing["extra_data"])[0]
    payload = {
        "format": "cotizador-ai-import",
        "items": [{"id": existing["id"], "name": existing["name"], "campos_extra": {"nota_ai": "revisado"}}],
    }
    content = json.dumps(payload, ensure_ascii=False)
    preview = client.post(
        f"/api/quotations/{quotation['id']}/import/preview", json={"content": content}
    ).json()
    applied = client.post(
        f"/api/quotations/{quotation['id']}/import/apply",
        json={"content": content, "expected_revision": preview["target_revision"]},
    ).json()
    updated = next(item for item in applied["quotation"]["items"] if item["id"] == existing["id"])
    assert updated["extra_data"]["nota_ai"] == "revisado"
    assert updated["extra_data"][kept_key] == existing["extra_data"][kept_key]


def test_item_moves_between_quotations(client):
    quotations = client.get("/api/quotations").json()
    source = quotations[0]
    target = quotations[1]
    item = source["items"][0]

    moved = client.post(f"/api/items/{item['id']}/move", json={"quotation_id": target["id"]})
    assert moved.status_code == 200
    assert moved.json()["quotation_id"] == target["id"]
    assert moved.json()["id"] == item["id"]

    after = client.get("/api/quotations").json()
    new_source = next(q for q in after if q["id"] == source["id"])
    new_target = next(q for q in after if q["id"] == target["id"])
    assert new_source["item_count"] == source["item_count"] - 1
    assert new_target["item_count"] == target["item_count"] + 1
    assert new_target["items"][-1]["id"] == item["id"]
    # Ambas revisiones suben: es el lock optimista del import.
    assert new_source["revision"] > source["revision"]
    assert new_target["revision"] > target["revision"]
    # extra_data viaja intacto aunque el destino no declare esas claves.
    assert next(i for i in new_target["items"] if i["id"] == item["id"])["extra_data"] == item["extra_data"]


def test_move_rejects_unknown_and_identical_targets(client):
    quotations = client.get("/api/quotations").json()
    item = quotations[0]["items"][0]
    assert client.post(f"/api/items/{item['id']}/move", json={"quotation_id": "no-existe"}).status_code == 404
    assert (
        client.post(f"/api/items/{item['id']}/move", json={"quotation_id": quotations[0]["id"]}).status_code
        == 409
    )
    assert client.post("/api/items/no-existe/move", json={"quotation_id": quotations[0]["id"]}).status_code == 404
    assert client.post(f"/api/items/{item['id']}/move", json={"quotation_id": ""}).status_code == 422
    assert (
        client.post(
            f"/api/items/{item['id']}/move", json={"quotation_id": quotations[1]["id"], "position": 0}
        ).status_code
        == 422
    )


def test_moved_item_keeps_its_stored_image(client):
    quotations = client.get("/api/quotations").json()
    source, target = quotations[0], quotations[1]
    image = io.BytesIO()
    Image.new("RGB", (200, 120), "#123456").save(image, format="PNG")
    data_uri = "data:image/png;base64," + base64.b64encode(image.getvalue()).decode("ascii")
    item = client.post(
        f"/api/quotations/{source['id']}/items",
        json={"name": "Con foto para mover", "photo": data_uri, "extra_data": {}},
    ).json()
    assert item["image_id"]

    moved = client.post(f"/api/items/{item['id']}/move", json={"quotation_id": target["id"]}).json()
    assert moved["image_id"] == item["image_id"]
    assert client.get(f"/api/images/{item['image_id']}").status_code == 200


def test_quotation_tabs_can_be_reordered_and_the_order_persists(client):
    original = [quotation["id"] for quotation in client.get("/api/quotations").json()]
    assert [quotation["position"] for quotation in client.get("/api/quotations").json()] == list(
        range(len(original))
    )

    shuffled = original[2:] + original[:2]
    response = client.post("/api/quotations/reorder", json={"order": shuffled})
    assert response.status_code == 200
    assert [quotation["id"] for quotation in response.json()] == shuffled

    after = client.get("/api/quotations").json()
    assert [quotation["id"] for quotation in after] == shuffled
    assert [quotation["position"] for quotation in after] == list(range(len(shuffled)))
    # Mover una pestaña no toca el contenido: la revisión es el lock del import.
    revisions = {quotation["id"]: quotation["revision"] for quotation in after}
    assert set(revisions.values()) == {1}


def test_reorder_rejects_unknown_or_repeated_quotations(client):
    known = [quotation["id"] for quotation in client.get("/api/quotations").json()]
    assert client.post("/api/quotations/reorder", json={"order": ["no-existe"]}).status_code == 404
    assert client.post("/api/quotations/reorder", json={"order": [known[0], known[0]]}).status_code == 422
    assert client.post("/api/quotations/reorder", json={"order": []}).status_code == 422
    # Un orden parcial es válido: lo omitido conserva su orden actual, al final.
    partial = client.post("/api/quotations/reorder", json={"order": [known[-1]]})
    assert partial.status_code == 200
    assert [quotation["id"] for quotation in partial.json()] == [known[-1], *known[:-1]]


def test_archived_quotation_leaves_the_tabs_and_comes_back_last(client):
    before = [quotation["id"] for quotation in client.get("/api/quotations").json()]
    target = before[0]

    archived = client.post(f"/api/quotations/{target}/archive", json={"archived": True})
    assert archived.status_code == 200
    assert archived.json()["archived"] is True
    # Archivar no cambia lo cotizado: la revisión es el lock del import.
    assert archived.json()["revision"] == 1
    # Queda al final del orden, detrás de todas las visibles.
    assert archived.json()["position"] == len(before)

    listed = client.get("/api/quotations").json()
    assert [quotation["id"] for quotation in listed if not quotation["archived"]] == before[1:]
    assert [quotation["id"] for quotation in listed if quotation["archived"]] == [target]

    restored = client.post(f"/api/quotations/{target}/archive", json={"archived": False})
    assert restored.status_code == 200
    assert restored.json()["archived"] is False
    # Vuelve como la última pestaña, no al lugar que ocupaba antes.
    assert [quotation["id"] for quotation in client.get("/api/quotations").json()] == [*before[1:], target]


def test_archive_keeps_the_items_and_rejects_an_unknown_quotation(client):
    quotation = client.get("/api/quotations").json()[0]
    client.post(f"/api/quotations/{quotation['id']}/archive", json={"archived": True})
    stored = client.get("/api/quotations").json()
    archived = next(entry for entry in stored if entry["id"] == quotation["id"])
    assert archived["item_count"] == quotation["item_count"]
    assert client.get(f"/api/quotations/{quotation['id']}/export.json").status_code == 200

    assert client.post("/api/quotations/no-existe/archive", json={"archived": True}).status_code == 404
    assert client.post(f"/api/quotations/{quotation['id']}/archive", json={}).status_code == 422


def test_new_quotation_is_added_at_the_end_of_the_tabs(client):
    before = client.get("/api/quotations").json()
    created = client.post("/api/quotations", json={"name": "Última", "quote_date": "2026-08-30"}).json()
    assert created["position"] == len(before)
    assert client.get("/api/quotations").json()[-1]["id"] == created["id"]


def test_shared_html_and_pdf_carry_the_quotation_description(client):
    quotation = client.get("/api/quotations").json()[0]
    client.patch(
        f"/api/quotations/{quotation['id']}",
        json={"description": "Cómo estos equipos procesan muchas imágenes."},
    )
    html = client.get(f"/api/quotations/{quotation['id']}/export.html").text
    assert 'id="intro"' in html
    payload = json.loads(
        base64.b64decode(
            re.search(r'<script id="quotation-data" type="text/plain">([^<]+)</script>', html).group(1)
        )
    )
    assert payload["quotation"]["description"] == "Cómo estos equipos procesan muchas imágenes."

    pdf = client.get(f"/api/quotations/{quotation['id']}/export.pdf")
    extracted = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "procesan muchas imágenes" in extracted
