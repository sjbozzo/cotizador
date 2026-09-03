"""Reorganiza el catálogo en cinco categorías (pedido del usuario, 2026-09-03).

  1. Aceleradores USB            2. Aceleradores M.2 (PCIe 3.0)
  3. PC industriales con ranura M.2   4. PC con NPU (incluye Jetson)   5. Microcomputadores

Máximo 8 ítems por categoría; se conservan sí o sí Raspberry Pi 5, Hailo-8, Qotom,
Seeed reComputer y AMD Kria. Nada se borra: lo que sale de las cinco categorías va a
una cotización archivada «Reserva», y las cotizaciones vaciadas se archivan.

    uv run python -m scripts.reorganizar_2026_09 --dry-run
    uv run python -m scripts.reorganizar_2026_09
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from app import db
from app.schemas import ItemCreate, normalize_extra_fields
from app.services.images import InvalidImage, process_photo

USB, M2, PC, NPU, SBC, RESERVA = (
    "aceleradores-usb-yolo",
    "edge-ai-aceleradores-m2",
    "pcs-industriales-m2-ai",
    "edge-ai-plataformas-host",
    "microcomputadores",
    "reserva-fuera-de-categorias",
)
COMMON = [
    {"key": "disponibilidad_chile", "label": "Disponibilidad en Chile", "type": "text"},
    {"key": "facilidad_programacion", "label": "Facilidad de programación (1-5)", "type": "number"},
]

CATEGORIES: dict[str, dict] = {
    USB: {
        "name": "1 · Aceleradores USB",
        "description": (
            "Se enchufan por USB a un equipo que ya existe y se hacen cargo de la inferencia. Es la forma más simple de sumar "
            "visión por computador sin abrir el equipo.\n\nSirven para empezar con pocas cámaras; el límite práctico está en el "
            "ancho de banda USB y en el ecosistema de software de cada familia."
        ),
        "fields": [
            {"key": "acelerador", "label": "Acelerador", "type": "text"},
            {"key": "rendimiento_aproximado", "label": "Rendimiento aprox.", "type": "text"},
            {"key": "compatibilidad_yolo", "label": "Compatibilidad YOLO", "type": "text"},
            {"key": "referencia", "label": "Referencia", "type": "text"},
            {"key": "ranking_recomendado", "label": "Ranking recomendado", "type": "number"},
        ],
        # Nombre (o fragmento único) de los ítems que se quedan, en orden.
        "keep": [
            "AAEON / UP Hailo-10H USB", "ASUS UGen300", "Google Coral USB Accelerator", "Intel Neural Compute Stick 2",
            "RK1808 AI Compute Stick", "Kneron KNEO STEM KL720", "Kneron KNEO AI Dongle KL520", "Intel Movidius Neural Compute Stick",
        ],
    },
    M2: {
        "name": "2 · Aceleradores M.2 (PCIe 3.0)",
        "description": (
            "Módulos que se instalan en la ranura M.2 del equipo anfitrión y ejecutan la red neuronal mientras el anfitrión "
            "captura, decodifica y almacena. Van por PCIe dentro del gabinete: no ocupan puertos ni sobresalen.\n\nAntes de "
            "comprar hay que confirmar en el anfitrión el largo de la ranura (2242 o 2280), que sea PCIe y no sólo SATA, y cómo "
            "se disipa el calor del módulo."
        ),
        "fields": [
            {"key": "tops", "label": "TOPS", "type": "text"},
            {"key": "formato", "label": "Formato M.2", "type": "text"},
            {"key": "requiere_host", "label": "Requiere equipo host", "type": "boolean"},
        ],
        "keep": [
            "Hailo-8 M.2 26 TOPS (Unistorm", "Hailo-8 M.2 M-key 2280 (HM218B1C2FAE)", "Hailo-8L M.2 B+M 2280", "Hailo-8L M.2 A+E 2230",
            "Raspberry Pi AI HAT+ 26 TOPS", "Raspberry Pi AI HAT+ 13 TOPS", "DEEPX DX-M1", "Axelera Embedded 110m",
        ],
    },
    PC: {
        "name": "3 · PC industriales con ranura M.2",
        "description": (
            "Equipos x86 sin ventilador, pensados para gabinete o riel DIN, con una ranura M.2 M-key PCIe que queda libre para "
            "el acelerador porque el sistema puede ir a SATA, mSATA o eMMC. Son el anfitrión, no el procesador de imágenes.\n\n"
            "Para un ambiente con polvo importa que la carcasa sea sellada (sin ranuras de ventilación) y que la ranura M.2 sea "
            "PCIe (x1 alcanza para un Hailo-8) del largo del módulo."
        ),
        "fields": [
            {"key": "espacio_m2", "label": "Espacio para M.2", "type": "boolean"},
            {"key": "procesador", "label": "Procesador", "type": "text"},
            {"key": "ram", "label": "Memoria RAM", "type": "text"},
            {"key": "m2_acelerador", "label": "M.2 para acelerador", "type": "text"},
        ],
        "keep": [
            "Topton X2B / X2E N150", "Topton X2F N100 (PCIe x2,", "KingnovyPC X2E N150", "Qotom Q10922X",
            "Partaker / Inctel C11", "Kingdel NC690", "Mini PC firewall N100 4× i226-V", "MeLE Quieter 4C",
        ],
    },
    NPU: {
        "name": "4 · PC con NPU",
        "description": (
            "Equipos y kits que traen su propia NPU y resuelven la inferencia sin acelerador aparte: reciben las cámaras, "
            "decodifican y corren YOLO en el mismo chip. La cifra de TOPS orienta cuántas cámaras y qué modelo pueden atender.\n\n"
            "Conviene estimar primero los cuadros por segundo totales (cámaras × fps) y después elegir el equipo."
        ),
        "fields": [
            {"key": "rendimiento_tops", "label": "Rendimiento (TOPS)", "type": "text"},
            {"key": "espacio_m2", "label": "Ranura M.2 utilizable", "type": "boolean"},
            {"key": "subtipo", "label": "Subtipo", "type": "text"},
        ],
        "keep": [
            "NVIDIA Jetson Orin Nano Super", "Seeed reComputer Industrial R2135-12 (sin", "AMD Kria KV260", "Orange Pi AIPro 8T",
            "D-Robotics RDK X5", "Radxa ROCK 5B", "Radxa Dragon Q6A", "MINISFORUM / AMD UM880 Plus",
        ],
    },
    SBC: {
        "name": "5 · Microcomputadores",
        "description": (
            "Computadores de placa única para armar el nodo con un acelerador aparte (AI HAT+ o módulo M.2) o para tareas "
            "livianas. Lo que cuenta es la decodificación de video, la red y que exista carcasa de aluminio.\n\nLa Raspberry Pi 5 "
            "con AI HAT+ es el camino más documentado y el único con stock local en Chile."
        ),
        "fields": [
            {"key": "rendimiento_tops", "label": "Rendimiento (TOPS)", "type": "text"},
            {"key": "espacio_m2", "label": "Ranura M.2 utilizable", "type": "boolean"},
        ],
        "keep": [
            "Raspberry Pi 5 (8 GB)", "Radxa / Intel X4", "FriendlyELEC NanoPC-T6-LTS", "Khadas VIM4",
            "ArmSoM Sige7", "Firefly ROC-RK3588S-PC", "Sipeed LicheePi 4A", "BeagleBoard BeagleY-AI",
        ],
    },
}

NEW_ITEMS: dict[str, list[dict]] = {
    PC: [
        {
            "name": "Topton X2B / X2E N150 (4× 2,5GbE, negro, fanless)",
            "price": "≈US$131 (AliExpress, ≈$140.000 CLP, sep. 2026)",
            "country": "China",
            "purchase_link": "https://www.toptonpc.com/product/topton-intel-n150-n100-4x2-5g-lan-fanless-mini-pc-i226-v-2com-ddr4-nvme-efficient-cooling-solid-firewall-pc-industrial-computer/",
            "description": "Mini PC firewall fanless de Topton con Intel N150 (o N100), chasis de aluminio negro mate con aletas, 4× Intel i226-V 2,5GbE, 1× DDR4/DDR5 SO-DIMM, M.2 2280 M-key NVMe PCIe 3.0 x1 (o SATA) y bahía SATA 2,5 pulgadas independiente.",
            "comment": "Primera compra propuesta: el M.2 2280 queda libre para el Hailo-8 (PCIe x1: no baja los TOPS, sólo el ancho de banda y algo de FPS con muchos flujos) y el sistema va al SSD SATA. Comprar una unidad y validar: lspci (Hailo Technologies), hailortcli scan y 24-48 h de YOLO con vigilancia de temperatura; el BIOS de estos equipos puede dar sorpresas. Riesgo: posventa débil del vendedor.",
            "included": True,
            "extra_data": {"espacio_m2": True, "procesador": "Intel N150 (o N100), 4 núcleos", "ram": "1× DDR4/DDR5 SO-DIMM (según variante)", "m2_acelerador": "Sí: M-key 2280 PCIe 3.0 x1 (o SATA), libre con el SO en la bahía SATA 2,5\"", "disponibilidad_chile": "AliExpress (tienda Topton, listado activo, ≈US$131 con seguimiento de precio); Topton también vende directo. Sin venta local.", "facilidad_programacion": 5},
        },
        {
            "name": "Topton X2F N100 (PCIe x2, 4× 2,5GbE, fanless)",
            "price": "≈US$129 barebone (AliExpress / Topton, 2026)",
            "country": "China",
            "purchase_link": "https://www.toptonpc.com/",
            "description": "Familia Topton N100 fanless con 4× Intel i226-V 2,5GbE, M.2 2280 NVMe eléctricamente PCIe x2 y SATA independiente; el hardware X2F está documentado por el proyecto coreboot con M.2 NVMe PCIe x2 + SATA funcionales.",
            "comment": "Mejor match técnico si se van a procesar muchas cámaras: alimenta mejor al Hailo-8 que un M.2 x1, aunque el N100 sea algo más viejo que el N150. Una revisión de 2026 reporta tres meses de uso continuo con el barebone en torno a US$129. Misma validación que el X2B antes de comprar en volumen.",
            "included": True,
            "extra_data": {"espacio_m2": True, "procesador": "Intel N100 (4 núcleos)", "ram": "1× SO-DIMM DDR5 (según variante)", "m2_acelerador": "Sí: M-key 2280 NVMe PCIe x2, libre con el SO en SATA", "disponibilidad_chile": "AliExpress (Topton) y tienda Topton; sin venta local.", "facilidad_programacion": 5},
        },
        {
            "name": "KingnovyPC X2E N150 (4× 2,5GbE, fanless, 3 años de garantía)",
            "price": "≈US$130–150 (AliExpress, tienda Kingnovy, sep. 2026)",
            "country": "China",
            "purchase_link": "https://www.kingnovypc.com/",
            "description": "Mini PC fanless 0 dB de Kingnovy con Intel N150/N100, carcasa de aluminio sólido con aletas, 4× Intel i226-V 2,5GbE, M.2 2280 NVMe PCIe 3.0 x1, SATA 2,5 pulgadas independiente, watchdog, auto power-on, RTC, PXE, operación declarada 7×24 y certificaciones CE/FCC/RoHS.",
            "comment": "Segundo fabricante para no depender sólo de Topton: Kingnovy declara 3 años de garantía, soporte técnico de por vida y venta de una unidad como muestra, lo que pesa más para una integración que un vendedor sin posventa. Misma arquitectura (M.2 x1 para el Hailo, SO en SATA).",
            "included": True,
            "extra_data": {"espacio_m2": True, "procesador": "Intel N150 / N100 (4 núcleos)", "ram": "1× SO-DIMM (según variante)", "m2_acelerador": "Sí: M-key 2280 NVMe PCIe 3.0 x1, libre con el SO en SATA 2,5\"", "disponibilidad_chile": "AliExpress (tienda oficial Kingnovy) y kingnovypc.com; sin venta local.", "facilidad_programacion": 5},
        },
    ],
    M2: [
        {
            "name": "Hailo-8 M.2 26 TOPS (Unistorm, Amazon, envío a Chile)",
            "price": "US$214,99 (Amazon.com, ≈$201.000 CLP con destino Chile, sep. 2026)",
            "country": "Israel (chip Hailo); vendedor Unistorm",
            "purchase_link": "https://www.amazon.com/dp/B0FBFTW5QJ",
            "description": "Módulo Hailo-8 de 26 TOPS INT8 en M.2 M-key 2280 (placa de 22×80 mm con extensiones quebrables a 2242 y 2260), PCIe Gen3 x4, ~2,5 W típicos; Linux y Windows con HailoRT.",
            "comment": "Es el Hailo-8 de Amazon que sí se puede comprar desde Chile: con destino Chile la ficha muestra envío gratis y estaba en stock (sep. 2026). El listado de Waveshare (B0D928WG5L) dice 'no se envía a tu ubicación' con Chile seleccionado. Entra en la ranura M-key 2280 de los PC de la tabla 3 (Topton, Kingnovy, Qotom, Partaker); si el PC sólo tiene 2242 se quiebra la extensión. Variante 2242 del mismo vendedor: B0FWXF4XK1, mismo precio.",
            "included": True,
            "extra_data": {"tops": "26 TOPS INT8", "formato": "M.2 M-key 2280 (quebrable a 2242/2260), PCIe Gen3 x4", "requiere_host": True, "disponibilidad_chile": "Amazon.com: en stock y con envío gratis a Chile (AmazonGlobal) al seleccionar Chile como destino (sep. 2026).", "facilidad_programacion": 5},
        },
    ],
    SBC: [
        {
            "name": "Raspberry Pi 5 (8 GB)",
            "price": "US$80 oficial; ≈$95.000–110.000 CLP en Chile (sep. 2026)",
            "country": "Reino Unido",
            "purchase_link": "https://www.raspberrypi.com/products/raspberry-pi-5/",
            "description": "Placa única con Broadcom BCM2712 (4× Cortex-A76 a 2,4 GHz), 8 GB LPDDR4X, Gigabit Ethernet, 2× USB 3.0, decodificación H.265 por hardware (H.264 por CPU) y conector PCIe 2.0 x1 para el AI HAT+ o un módulo M.2.",
            "comment": "El camino más documentado para el nodo: Pi 5 + AI HAT+ (13 o 26 TOPS) + carcasa de aluminio con disipación pasiva (Akasa A-RA19-M1B o KKSB para AI HAT+). Se compra en Chile (MCI Electronics, Altronics, raspberrypi.cl). Límite: sin ranura M.2 libre cuando se usa el HAT, y la decodificación H.264 va por CPU.",
            "included": True,
            "extra_data": {"rendimiento_tops": "Sin NPU (con AI HAT+: 13 o 26 TOPS)", "espacio_m2": True, "disponibilidad_chile": "Venta local: MCI Electronics, Altronics y raspberrypi.cl con stock; también MercadoLibre Chile.", "facilidad_programacion": 5},
        },
    ],
}


def find_item(fragment: str) -> dict | None:
    fragment_low = fragment.lower()
    for quotation in db.list_quotations(include_items=True, include_archived=True) if "include_archived" in db.list_quotations.__code__.co_varnames else db.list_quotations(include_items=True):
        for item in quotation["items"]:
            if fragment_low in item["name"].lower():
                return item
    return None


def ensure_quotation(quotation_id: str, name: str, description: str, fields: list[dict], dry_run: bool) -> None:
    fields_all = normalize_extra_fields(fields + COMMON)
    existing = db.get_quotation(quotation_id)
    if existing:
        # Conserva columnas que ya tenía y no están en la lista (siguen mostrándose).
        known = {field["key"] for field in fields_all}
        extra = [field for field in existing["extra_fields"] if field["key"] not in known]
        print(f"  cotización · {name} (actualizar)")
        if not dry_run:
            db.update_quotation(quotation_id, {"name": name, "description": description, "extra_fields": fields_all + extra})
            if existing.get("archived"):
                db.set_quotation_archived(quotation_id, False)
        return
    print(f"  cotización · {name} (crear)")
    if not dry_run:
        db.create_quotation({"id": quotation_id, "name": name, "quote_date": date.today().isoformat(), "description": description, "extra_fields": fields_all})


def create_items(quotation_id: str, items: list[dict], dry_run: bool) -> None:
    for data in items:
        if find_item(data["name"][:28]):
            continue
        print(f"  agregar · {data['name']}")
        if dry_run:
            continue
        validated = ItemCreate(**{**data, "photo": ""}).model_dump(mode="json")
        photo = validated.pop("photo")
        try:
            validated.update(process_photo(photo))
        except InvalidImage:
            validated.update({"photo_url": "", "image_id": None})
        db.create_item(quotation_id, validated)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    dry_run = args.dry_run
    db.init_database()
    print(f"Base de datos: {db.database_path()}")

    # 1) Las cinco categorías y la reserva.
    for quotation_id, spec in CATEGORIES.items():
        ensure_quotation(quotation_id, spec["name"], spec["description"], spec["fields"], dry_run)
    ensure_quotation(RESERVA, "Reserva · fuera de las cinco categorías", "Ítems que salieron de las cinco categorías al reordenar el catálogo (2026-09-03). No se borró nada: se pueden volver a mover.", [], dry_run)

    # 2) Ítems nuevos.
    for quotation_id, items in NEW_ITEMS.items():
        create_items(quotation_id, items, dry_run)

    # 3) Mover lo que se queda a su categoría, en el orden pedido.
    kept_ids: set[str] = set()
    for quotation_id, spec in CATEGORIES.items():
        for fragment in spec["keep"]:
            item = find_item(fragment)
            if not item:
                print(f"  ! no encontrado: {fragment}")
                continue
            kept_ids.add(item["id"])
            if item["quotation_id"] != quotation_id:
                print(f"  mover · {item['name'][:60]} -> {spec['name']}")
                if not dry_run:
                    db.move_item(item["id"], quotation_id)

    # 4) Todo lo demás va a la reserva; las cotizaciones vaciadas se archivan.
    for quotation in db.list_quotations(include_items=True):
        if quotation["id"] in (RESERVA,):
            continue
        for item in quotation["items"]:
            if item["id"] in kept_ids:
                continue
            print(f"  reserva · {item['name'][:60]}  (desde {quotation['name'][:30]})")
            if not dry_run:
                db.move_item(item["id"], RESERVA)
        if quotation["id"] not in CATEGORIES and not quotation.get("archived"):
            print(f"  archivar · {quotation['name']}")
            if not dry_run:
                db.set_quotation_archived(quotation["id"], True)
    if not dry_run:
        db.set_quotation_archived(RESERVA, True)
        db.reorder_quotations([USB, M2, PC, NPU, SBC])

    print("\nResultado:")
    for quotation in db.list_quotations(include_items=True):
        print(f"  {quotation['name']:40} {quotation['item_count']:2} ítems")
    return 0


if __name__ == "__main__":
    sys.exit(main())
