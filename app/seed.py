from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "source_html"
SEED_DATE = date(2026, 8, 30).isoformat()


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "_", ascii_value.lower()).strip("_")


def _stable_item_id(quotation_id: str, name: str) -> str:
    digest = hashlib.sha1(f"{quotation_id}:{name}".encode("utf-8")).hexdigest()[:12]
    return f"{quotation_id}-{digest}"


def _field(key: str, label: str, field_type: str = "text") -> dict[str, str]:
    return {"key": key, "label": label, "type": field_type}


def _quotation(
    quotation_id: str,
    name: str,
    source_file: str,
    extra_fields: list[dict[str, str]],
    items: Iterable[dict[str, Any]],
    description: str = "",
) -> dict[str, Any]:
    normalized_items = []
    for position, raw_item in enumerate(items):
        item = {
            "id": raw_item.get("id") or _stable_item_id(quotation_id, raw_item["name"]),
            "name": raw_item["name"].strip(),
            "country": raw_item.get("country", "").strip(),
            "photo": raw_item.get("photo", "").strip(),
            "purchase_link": raw_item.get("purchase_link", "").strip(),
            "description": raw_item.get("description", "").strip(),
            "comment": raw_item.get("comment", "").strip(),
            "price": raw_item.get("price", "").strip(),
            "included": bool(raw_item.get("included", True)),
            "extra_data": raw_item.get("extra_data", {}),
            "position": position,
        }
        normalized_items.append(item)
    return {
        "id": quotation_id,
        "name": name,
        "purchase_link": "",
        "quote_date": SEED_DATE,
        "description": description,
        "extra_fields": extra_fields,
        "source_file": source_file,
        "items": normalized_items,
    }


class _FirstTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict[str, str]]] = []
        self._row: list[dict[str, str]] | None = None
        self._cell: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "tr":
            self._row = []
        elif tag in {"th", "td"} and self._row is not None:
            self._cell = {"text": "", "href": ""}
        elif tag == "a" and self._cell is not None:
            self._cell["href"] = attrs_dict.get("href") or ""

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag in {"th", "td"} and self._row is not None and self._cell is not None:
            self._cell["text"] = " ".join(self._cell["text"].split())
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


class _RaspberryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.group = "base"
        self.rows: list[tuple[str, dict[str, str]]] = []

    def handle_comment(self, data: str) -> None:
        marker = data.strip()
        if marker == "HAILO_IP_ROWS_START":
            self.group = "ip"
        elif marker == "HAILO_SPECIFIC_ROWS_START":
            self.group = "hailo"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "tr":
            return
        values = {key.removeprefix("data-"): value or "" for key, value in attrs if key.startswith("data-")}
        if values.get("name"):
            self.rows.append((self.group, values))


def _extract_js_array(source: str, variable: str) -> list[dict[str, Any]]:
    match = re.search(rf"const\s+{re.escape(variable)}\s*=\s*(\[.*?\]);", source, flags=re.S)
    if not match:
        raise ValueError(f"No se encontró el arreglo JavaScript {variable}")
    payload = match.group(1)
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        quoted_keys = re.sub(r"(?m)(^\s*)([A-Za-z_$][\w$]*)(\s*:)", r'\1"\2"\3', payload)
        return json.loads(quoted_keys)


USB_ACCELERATOR_PHOTOS = {
    'Bitmain Sophon Neural Network Stick': "https://sophon-file.sophon.cn/sophon-prod-s3/assets/images/products/nns/top-mobile.jpg",
    'DeepVision DRAX REV A0 USB-C': "https://deepvision.io/wp-content/uploads/usbmodule1.png",
    'Firefly GTI USB Dongle': "https://wiki.t-firefly.com/en/NCCS1/_images/dongle.png",
    'Google Coral USB Accelerator': "https://www.cnx-software.com/wp-content/uploads/2019/03/Coral-USB-Accelerator.jpg",
    'Gyrfalcon Plai Plug SPR2803S': "https://www.cnx-software.com/wp-content/uploads/2019/06/2803-Plai-Plug.jpg",
    'Hailo-8 USB Stick / USB Accelerator': "https://www.waveshare.com/img/devkit/accBoard/Hailo-8/Hailo-8-details-1.jpg",
    'Intel Movidius Neural Compute Stick': "https://media.rs-online.com/w_800/F1393655-01.jpg",
    'Intel Neural Compute Stick 2': "https://media.rs-online.com/R1811851-02.jpg",
    'Kneron KNEO AI Dongle KL520': "https://www.sparkfun.com/media/catalog/product/1/8/18140-Kneron_AI_Dongle.jpeg",
    'Kneron KNEO STEM KL720': "https://kneo.kneron.com/images/thumbs/0000014_kneo-stem-kl720-ai-dongle_550.png",
    'Orange Pi AI Stick Lite': "https://www.cnx-software.com/wp-content/uploads/2019/10/Orange-Pi-AI-Stick-Lite.jpg",
    'RK1808 AI Compute Stick': "https://media-cdn.seeedstudio.com/media/catalog/product/r/k/rk1808_ai_002_1.jpg",
}


def _parse_usb_accelerators() -> dict[str, Any]:
    filename = "aceleradores_usb_yolo.html"
    source = (SOURCE_DIR / filename).read_text(encoding="utf-8")
    parser = _FirstTableParser()
    parser.feed(source)
    if len(parser.rows) != 13:
        raise ValueError(f"{filename}: se esperaban 12 productos y se encontraron {len(parser.rows) - 1}")

    ranking_match = re.search(r"<h2>Ranking recomendado</h2>\s*<ol>(.*?)</ol>", source, flags=re.S | re.I)
    ranking_names = re.findall(r"<li>(.*?)</li>", ranking_match.group(1), flags=re.S | re.I) if ranking_match else []
    ranking = {_key(re.sub(r"<.*?>", "", name)): index + 1 for index, name in enumerate(ranking_names)}

    items = []
    for row in parser.rows[1:]:
        _, name, accelerator, performance, compatibility, price, comment, reference = row
        normalized_name = _key(name["text"])
        recommendation_rank = next(
            (rank for ranked_name, rank in ranking.items() if ranked_name in normalized_name or normalized_name in ranked_name),
            None,
        )
        items.append(
            {
                "name": name["text"],
                "photo": USB_ACCELERATOR_PHOTOS.get(name["text"], ""),
                "price": price["text"],
                "purchase_link": reference["href"],
                "comment": comment["text"],
                "extra_data": {
                    "acelerador": accelerator["text"],
                    "rendimiento_aproximado": performance["text"],
                    "compatibilidad_yolo": compatibility["text"],
                    "referencia": reference["text"],
                    "ranking_recomendado": recommendation_rank,
                },
            }
        )
    return _quotation(
        "aceleradores-usb-yolo",
        "Aceleradores USB para correr YOLO por menos de $100.000 CLP",
        filename,
        [
            _field("acelerador", "Acelerador"),
            _field("rendimiento_aproximado", "Rendimiento aprox."),
            _field("compatibilidad_yolo", "Compatibilidad YOLO"),
            _field("referencia", "Referencia"),
            _field("ranking_recomendado", "Ranking recomendado", "number"),
        ],
        items,
        "Dispositivos USB o tipo dongle para acelerar redes de visión artificial bajo el presupuesto indicado en la fuente.",
    )


def _parse_industrial_pcs() -> dict[str, Any]:
    filename = "pcs_industriales_m2_ai.html"
    source = (SOURCE_DIR / filename).read_text(encoding="utf-8")
    products = _extract_js_array(source, "products")
    if len(products) != 16:
        raise ValueError(f"{filename}: se esperaban 16 productos y se encontraron {len(products)}")
    items = [
        {
            "name": product["nombre_producto"],
            "photo": product["foto"],
            "price": product["precio_promedio"],
            "country": product["pais_origen"],
            "purchase_link": product["link_compra"],
            "description": product["descripcion"],
            "comment": product["comentario"],
            "extra_data": {"espacio_m2": product["espacio_m2"].strip().lower() in {"sí", "si", "true"}},
        }
        for product in products
    ]
    return _quotation(
        "pcs-industriales-m2-ai",
        "PC industriales con M.2 para acelerador AI",
        filename,
        [_field("espacio_m2", "Espacio para M.2", "boolean")],
        items,
        "Equipos industriales fanless con una ranura M.2 utilizable para aceleración de IA.",
    )


def _parse_edge_ai() -> list[dict[str, Any]]:
    filename = "tabla-edge-ai-minimalista.html"
    source = (SOURCE_DIR / filename).read_text(encoding="utf-8")
    products = _extract_js_array(source, "products")
    if len(products) != 46:
        raise ValueError(f"{filename}: se esperaban 46 productos y se encontraron {len(products)}")
    set_match = re.search(r"const\s+m2AcceleratorIds\s*=\s*new\s+Set\((\[.*?\])\);", source, flags=re.S)
    if not set_match:
        raise ValueError(f"{filename}: falta la clasificación explícita de aceleradores M.2")
    accelerator_ids = set(json.loads(set_match.group(1)))

    def common_item(product: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": _stable_item_id("edge-ai", product["id"]),
            "name": product["name"],
            "photo": product["imageUrl"],
            "price": product["averagePrice"],
            "country": product["originCountry"],
            "purchase_link": product["purchaseUrl"],
            "description": product["description"],
            "comment": product["comment"],
        }

    host_ids = {
        "nvidia-jetson-orin-nano-super", "intel-minisforum-m1-pro-125h", "amd-minisforum-um880-plus",
        "amd-kria-kv260", "qualcomm-rb3-gen2-core", "radxa-dragon-q6a", "synaptics-astra-sl1680",
        "synaptics-astra-sl2619", "beagleboard-beagley-ai", "radxa-rock-5b", "radxa-x4",
        "orange-pi-aipro-8t", "khadas-vim4", "nxp-frdm-imx93", "sipeed-licheepi-4a",
        "canaan-canmv-k230", "milk-v-duo-s", "d-robotics-rdk-x5", "ti-tda4vm-j721e",
        "renesas-rz-v2l-evk", "friendlyelec-nanopc-t6-lts", "firefly-roc-rk3588s-pc",
        "armsom-sige7", "asus-tinker-edge-r", "st-stm32n6570-dk", "luckfox-pico-ultra",
        "infineon-psoc-edge-e84", "alif-dk-e8",
    }
    camera_ids = {
        "sipeed-maixcam", "luxonis-oak-d-lite", "raspberry-pi-ai-camera", "openmv-n6", "openmv-ae3",
        "seeed-grove-vision-ai-v2", "m5stack-unitv2",
    }
    non_m2_interfaces = {
        "hailo-ai-hat-plus": "HAT+",
        "google-coral-usb": "USB",
        "kneron-kl720-usb": "USB",
        "brainchip-akd1000-pcie": "PCIe",
    }
    hosts = []
    cameras = []
    external_accelerators = []
    m2_accelerators = []
    for product in products:
        item = common_item(product)
        if product["id"] in accelerator_ids:
            item["extra_data"] = {"requiere_host": True}
            m2_accelerators.append(item)
        elif product["id"] in host_ids:
            description = product["description"].lower()
            if "mini pc" in description:
                subtype = "Mini PC"
            elif "placa única" in description:
                subtype = "SBC"
            elif "equipo completo" in description:
                subtype = "Equipo completo"
            else:
                subtype = "Kit / placa de evaluación"
            item["extra_data"] = {"espacio_m2": bool(product["supportsM2"]), "subtipo": subtype}
            hosts.append(item)
        elif product["id"] in camera_ids:
            item["extra_data"] = {}
            cameras.append(item)
        elif product["id"] in non_m2_interfaces:
            item["extra_data"] = {"interfaz": non_m2_interfaces[product["id"]]}
            external_accelerators.append(item)
        else:
            raise ValueError(f"{filename}: producto sin clasificación semántica: {product['id']}")
    actual_counts = (len(hosts), len(cameras), len(external_accelerators), len(m2_accelerators))
    if actual_counts != (28, 7, 4, 7):
        raise ValueError(f"{filename}: clasificación inesperada {actual_counts}")
    return [
        _quotation(
            "edge-ai-plataformas-host",
            "Edge AI — Plataformas host",
            filename,
            [
                _field("espacio_m2", "Ranura M.2 utilizable", "boolean"),
                _field("subtipo", "Subtipo"),
            ],
            hosts,
            "Computadores, SBC y kits que pueden actuar como plataforma anfitriona Edge AI. El campo M.2 indica una ranura utilizable en el host.",
        ),
        _quotation(
            "edge-ai-vision",
            "Edge AI — Cámaras y visión integrada",
            filename,
            [],
            cameras,
            "Cámaras y módulos de visión con procesamiento de IA incorporado.",
        ),
        _quotation(
            "edge-ai-aceleradores-externos",
            "Edge AI — Aceleradores USB, HAT y PCIe",
            filename,
            [_field("interfaz", "Interfaz")],
            external_accelerators,
            "Aceleradores que se conectan mediante USB, HAT+ o PCIe; se separaron de los módulos M.2.",
        ),
        _quotation(
            "edge-ai-aceleradores-m2",
            "Edge AI — Aceleradores M.2",
            filename,
            [_field("requiere_host", "Requiere equipo host", "boolean")],
            m2_accelerators,
            "Módulos de aceleración M.2 para instalar en un equipo anfitrión. El booleano M.2 original se omitió porque describía ranuras, no el formato del módulo.",
        ),
    ]


def _parse_raspberry_cases() -> list[dict[str, Any]]:
    filename = "raspberry_pi_5_industrial_fanless.html"
    source = (SOURCE_DIR / filename).read_text(encoding="utf-8")
    parser = _RaspberryParser()
    parser.feed(source)
    counts = {group: sum(1 for current, _ in parser.rows if current == group) for group in ("base", "ip", "hailo")}
    if counts != {"base": 7, "ip": 4, "hailo": 11}:
        raise ValueError(f"{filename}: grupos inesperados {counts}")

    def to_item(values: dict[str, str]) -> dict[str, Any]:
        photo = values["image"]
        # The source's only data URI is a generated SVG placeholder, not a product photo.
        if photo.startswith("data:image/svg+xml"):
            photo = ""
        return {
            "name": values["name"],
            "photo": photo,
            "price": values["price"],
            "country": values["country"],
            "purchase_link": values["link"],
            "description": values["description"],
            "comment": values["comment"],
            "extra_data": {"espacio_m2": values["m2"].strip().lower() in {"sí", "si", "true"}},
        }

    grouped = {
        group: [to_item(values) for current, values in parser.rows if current == group]
        for group in ("base", "ip", "hailo")
    }
    extra_fields = [_field("espacio_m2", "Espacio para M.2", "boolean")]
    return [
        _quotation(
            "raspberry-pi5-carcasas-industriales",
            "Raspberry Pi 5 — Carcasas industriales y fanless",
            filename,
            extra_fields,
            grouped["base"],
            "Carcasas industriales o de refrigeración pasiva para Raspberry Pi 5.",
        ),
        _quotation(
            "raspberry-pi5-gabinetes-ip-hailo",
            "Raspberry Pi 5 — Gabinetes IP para Hailo",
            filename,
            extra_fields,
            grouped["ip"],
            "Gabinetes con protección ambiental que requieren adaptación o mecanizado para Raspberry Pi 5 y Hailo.",
        ),
        _quotation(
            "raspberry-pi5-carcasas-hailo",
            "Raspberry Pi 5 — Carcasas y kits para Hailo",
            filename,
            extra_fields,
            grouped["hailo"],
            "Carcasas y kits con compatibilidad específica o cercana para AI HAT+, AI Kit y módulos Hailo; algunas opciones usan refrigeración activa.",
        ),
    ]


def load_seed_quotations() -> list[dict[str, Any]]:
    """Parse and curate the bundled source HTML into distinct quotations."""
    quotations = [
        _parse_usb_accelerators(),
        _parse_industrial_pcs(),
        *_parse_edge_ai(),
        *_parse_raspberry_cases(),
    ]
    if len(quotations) != 9:
        raise AssertionError("La carga inicial debe producir nueve cotizaciones")
    return quotations


if __name__ == "__main__":
    data = load_seed_quotations()
    print(json.dumps({"quotations": data}, ensure_ascii=False, indent=2))
