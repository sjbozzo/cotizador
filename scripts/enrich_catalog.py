"""Completa la información faltante del catálogo y escribe la descripción de cada cotización.

Es idempotente: los campos de ítem sólo se tocan cuando están vacíos, así que
volver a ejecutarlo nunca pisa una corrección hecha a mano en la aplicación.

    uv run python -m scripts.enrich_catalog          # aplica
    uv run python -m scripts.enrich_catalog --dry-run
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from app import db


# Cada descripción dice, en dos párrafos y sin nombrar productos ni precios,
# qué resuelve esa familia y qué mirar al elegir. Los datos concretos viven en
# la tabla, que es la que cambia; el texto no debería envejecer con ella.
QUOTATION_DESCRIPTIONS: dict[str, str] = {
    "aceleradores-usb-yolo": """\
Se enchufan por USB a un equipo que ya está instalado y se hacen cargo de la inferencia, que deja de correr en la CPU. Es la forma más simple de sumar visión por computador: no hay que abrir el equipo ni tener una ranura interna libre.

Sirven para empezar con pocas cámaras y crecer sumando unidades al mismo anfitrión. A cambio, cada familia exige convertir el modelo a su propio formato, y el límite práctico suele estar en los puertos USB disponibles más que en el acelerador.""",
    "pcs-industriales-m2-ai": """\
Equipos sin ventilador, pensados para gabinete o riel DIN, con una ranura M.2 utilizable. Son el anfitrión y no el procesador de imágenes: reciben los flujos de las cámaras, los decodifican y almacenan, mientras la inferencia la ejecuta el módulo que se instala en esa ranura.

Por eso la cotización se lee de a pares: el equipo aporta CPU, red y almacenamiento; el módulo aporta cómputo. Al comparar conviene mirar primero la red y el almacenamiento, y confirmar que la ranura libre sea PCIe, del largo correcto y no esté compartida con el disco.""",
    "edge-ai-plataformas-host": """\
Placas y kits que concentran el trabajo: reciben la imagen de varias cámaras, la preparan y deciden qué se procesa. Algunas ya traen NPU propia y resuelven ellas mismas la inferencia; el resto aporta CPU, red y almacenamiento y delega el cómputo pesado en un acelerador.

Esa es la razón de la columna de ranura M.2: marca cuáles admiten sumarles un módulo sin adaptadores. Donde no la hay, el camino para escalar es un acelerador USB. Para dimensionar conviene estimar los cuadros por segundo totales, elegir cuánto cómputo hace falta y recién después el equipo capaz de alimentarlo.""",
    "edge-ai-vision": """\
El procesamiento ocurre dentro de la cámara: cada unidad corre su propio modelo y entrega resultados —detecciones, conteos, eventos— en vez de video crudo. Es la manera más directa de escalar, porque el costo de inferencia se reparte y ningún equipo central se satura.

También baja el tráfico y la latencia, ya que la red transporta metadatos en lugar de todos los cuadros. El límite es el propio módulo: el modelo tiene que caber en su memoria y actualizarlo significa desplegarlo en cada cámara y no en un solo servidor.""",
    "edge-ai-aceleradores-m2": """\
Son los que efectivamente procesan las imágenes, pero no funcionan solos: se instalan en la ranura M.2 de un equipo anfitrión y desde ahí ejecutan la red neuronal, mientras el anfitrión captura, decodifica y almacena.

Ese reparto permite pasar de una cámara a varias sin cambiar de equipo. Como van por PCIe dentro del gabinete, no ocupan un puerto externo ni sobresalen, que es lo que se busca en una instalación fija o sellada. Antes de comprar hay que verificar tres cosas en el anfitrión: el largo de la ranura, que sea PCIe y no sólo SATA, y cómo se disipa el calor.""",
    "raspberry-pi5-carcasas-industriales": """\
Estas carcasas no procesan nada: protegen al equipo que sí lo hace. En terreno —polvo, humedad, sol, vibración— un nodo de visión falla por temperatura o por suciedad mucho antes que por falta de cómputo.

Pesan dos criterios. La refrigeración: con la CPU y el acelerador trabajando de forma continua conviene aluminio con disipación pasiva y contacto térmico con la tapa antes que un ventilador, que se ensucia y se detiene. Y el espacio interno, que define si entra el módulo acelerador sin mecanizar nada. Los gabinetes sellados dan la mejor protección, pero exigen mecanizado, prensaestopas y un puente térmico armado a medida.""",
}


# Columnas que la cotización debe tener; se agregan sólo si faltan.
QUOTATION_FIELDS: dict[str, list[dict[str, str]]] = {
    "pcs-industriales-m2-ai": [
        {"key": "procesador", "label": "Procesador", "type": "text"},
        {"key": "ram", "label": "Memoria RAM", "type": "text"},
    ],
    "edge-ai-plataformas-host": [
        {"key": "rendimiento_tops", "label": "Rendimiento (TOPS)", "type": "text"},
    ],
}


# Procesador y memoria de cada PC industrial (fichas de fabricante, agosto 2026).
PC_SPECS: dict[str, dict[str, str]] = {
    "pcs-industriales-m2-ai-54d7a15184d3": {  # Qotom Q10722C
        "procesador": "Intel Celeron N5105 (4 núcleos)",
        "ram": "1× DDR4 SO-DIMM, hasta 16 GB",
    },
    "pcs-industriales-m2-ai-be64c4e25ae2": {  # JIERUICC GT1100
        "procesador": "Intel N100 (4 núcleos)",
        "ram": "1× DDR5 SO-DIMM (máximo no publicado)",
    },
    "pcs-industriales-m2-ai-e0cb90b44003": {  # HYSTOU H3-J5005-2L
        "procesador": "Intel Pentium Silver J5005 (4 núcleos)",
        "ram": "1× DDR4 SO-DIMM, hasta 32 GB",
    },
    "pcs-industriales-m2-ai-e4c5045b0202": {  # Qotom Q10922G4
        "procesador": "Intel N100 (4 núcleos)",
        "ram": "1× DDR5 SO-DIMM, hasta 16 GB",
    },
    "pcs-industriales-m2-ai-46b932e4b21c": {  # Qotom Q10922X
        "procesador": "Intel N100 (4 núcleos)",
        "ram": "1× DDR5 SO-DIMM, hasta 16 GB",
    },
    "pcs-industriales-m2-ai-cb6566fc7717": {  # Jetway BFDADN1-N97-IT
        "procesador": "Intel N97 (4 núcleos)",
        "ram": "1× DDR5-4800 SO-DIMM, hasta 32 GB",
    },
    "pcs-industriales-m2-ai-486d2af1d71e": {  # AAEON BOXER-6711-ADN N200
        "procesador": "Intel N200 (4 núcleos)",
        "ram": "1× DDR5-4800 SO-DIMM, hasta 32 GB",
    },
    "pcs-industriales-m2-ai-126afd1b979a": {  # Advantech UNO-2372V3
        "procesador": "Intel N250 (4 núcleos)",
        "ram": "1× DDR5 SO-DIMM, hasta 16 GB",
    },
    "pcs-industriales-m2-ai-a508fdf239a6": {  # AAEON BOXER-6751-ADP i3
        "procesador": "Intel Core i3-1215UE",
        "ram": "1× DDR4 SO-DIMM, hasta 32 GB",
    },
    "pcs-industriales-m2-ai-017ffe051d31": {  # AAEON BOXER-6646-ADP i3
        "procesador": "Intel Core i3-1220PE",
        "ram": "2× DDR5 SO-DIMM, hasta 64 GB",
    },
    "pcs-industriales-m2-ai-caf156f7ad98": {  # Advantech MIC-770 V3
        "procesador": "Socket LGA1700 · Core i3/i5/i7/i9 12ª–14ª gen",
        "ram": "2× DDR5-4800 SO-DIMM, hasta 128 GB",
    },
    "pcs-industriales-m2-ai-53290b1d9b7a": {  # AAEON BOXER-6751-ADP i5
        "procesador": "Intel Core i5-1245UE",
        "ram": "1× DDR4 SO-DIMM, hasta 32 GB",
    },
    "pcs-industriales-m2-ai-636b8b386e17": {  # Neousys POC-764VR
        "procesador": "Intel Core i3-N305 (8 núcleos)",
        "ram": "1× DDR5-4800 SO-DIMM, hasta 16 GB",
    },
    "pcs-industriales-m2-ai-0f2e682ad21e": {  # AAEON BOXER-6646-ADP i5
        "procesador": "Intel Core i5-1250PE",
        "ram": "2× DDR5 SO-DIMM, hasta 64 GB",
    },
    "pcs-industriales-m2-ai-4a47ce42d979": {  # AAEON BOXER-6751-ADP i7
        "procesador": "Intel Core i7-1265UE",
        "ram": "1× DDR4 SO-DIMM, hasta 32 GB",
    },
    "pcs-industriales-m2-ai-9dd3d987f2d7": {  # AAEON BOXER-6646-ADP i7
        "procesador": "Intel Core i7-1270PE",
        "ram": "2× DDR5 SO-DIMM, hasta 64 GB",
    },
}


# Rendimiento declarado de la NPU de cada plataforma host.
HOST_TOPS: dict[str, str] = {
    "edge-ai-6de0ef4d579b": "67 TOPS (INT8)",  # NVIDIA Jetson Orin Nano Super Developer Kit
    "edge-ai-86b6b9ca63dd": "NPU ~11 TOPS (Core Ultra 5 125H; 34 TOPS de plataforma)",  # MINISFORUM / Intel M1 Pro-125H
    "edge-ai-c234dfdebb24": "NPU 16 TOPS (Ryzen 7 8845HS)",  # MINISFORUM / AMD UM880 Plus
    "edge-ai-2213db96b3a9": "~1,4 TOPS (DPU B3136, INT8)",  # AMD Kria KV260 Vision AI Starter Kit
    "edge-ai-3e25822028d2": "12 TOPS (QCS6490)",  # Qualcomm RB3 Gen 2 Core Kit
    "edge-ai-34d18a47cbc8": "12 TOPS (QCS6490)",  # Radxa Dragon Q6A
    "edge-ai-67f82452da6b": "7,9 TOPS",  # Synaptics Astra Machina SL1680
    "edge-ai-64bb7f082210": "1 TOPS (NPU Torq / Coral)",  # Synaptics Astra Machina SL2619
    "edge-ai-767160c84ede": "4 TOPS (AM67A, 2× C7x+MMA)",  # BeagleBoard BeagleY-AI
    "edge-ai-0e9df9290aa4": "6 TOPS (RK3588, NPU de 3 núcleos)",  # Radxa ROCK 5B
    "edge-ai-f50c96264c80": "Sin NPU (N100: CPU + iGPU)",  # Radxa / Intel X4
    "edge-ai-21a50c81549c": "8 TOPS (INT8)",  # Orange Pi AIPro 8T
    "edge-ai-9ad781da53f3": "3,2 TOPS (A311D2)",  # Khadas VIM4
    "edge-ai-227a3c03eca9": "0,5 TOPS (Ethos-U65)",  # NXP FRDM-i.MX93
    "edge-ai-16b4ed7f75d1": "4 TOPS (TH1520, INT8)",  # Sipeed LicheePi 4A
    "edge-ai-44a0bec4b3f5": "6 TOPS (KPU)",  # Canaan / CanMV K230 Development Board
    "edge-ai-f9f91e6a1b55": "0,5 TOPS (SG2000)",  # Milk-V Duo S
    "edge-ai-dbf93825e398": "10 TOPS (BPU Sunrise X5)",  # D-Robotics RDK X5
    "edge-ai-c30e756f843c": "8 TOPS (C7x+MMA)",  # Texas Instruments TDA4VM Starter Kit J721EXSKG01EVM
    "edge-ai-6776a4c683ab": "DRP-AI clase 1 TOPS/W (sin TOPS publicado)",  # Renesas RZ/V2L Evaluation Kit
    "edge-ai-05708b8a6ab8": "6 TOPS (RK3588)",  # FriendlyELEC NanoPC-T6-LTS
    "edge-ai-e2e88e2d95e8": "6 TOPS (RK3588S)",  # Firefly ROC-RK3588S-PC
    "edge-ai-aeff99c095d6": "6 TOPS (RK3588)",  # ArmSoM Sige7
    "edge-ai-595bae0754bc": "3 TOPS (RK3399Pro)",  # ASUS Tinker Edge R
    "edge-ai-9afb45d74429": "0,6 TOPS (600 GOPS, Neural-ART)",  # STMicroelectronics STM32N6570-DK
    "edge-ai-b35b123ec0b9": "0,5 TOPS (RV1106)",  # Luckfox Pico Ultra
    "edge-ai-9220b6717c81": "Ethos-U55 + NNLite (sin TOPS publicado)",  # Infineon PSoC Edge E84 AI Evaluation Kit
    "edge-ai-89177dab6618": "2× Ethos-U55 (sin TOPS publicado)",  # Alif Semiconductor Ensemble E8 Development Kit
}


# Sin evidencia pública de YOLO: los Gyrfalcon Lightspeeur documentan VGG, SSD,
# ResNet y MobileNet pero no YOLO, y del DRAX no hay ficha ni SDK a la vista.
WITHOUT_YOLO_EVIDENCE: dict[str, str] = {
    "aceleradores-usb-yolo-a3fb3a755e7a": "Orange Pi AI Stick Lite",
    "aceleradores-usb-yolo-1bdde8251fdc": "Gyrfalcon Plai Plug SPR2803S",
    "aceleradores-usb-yolo-11ef0306232d": "Firefly GTI USB Dongle",
    "aceleradores-usb-yolo-3c7df8a104ef": "DeepVision DRAX REV A0 USB-C",
}


# Sólo se completan campos vacíos. País y descripción faltaban en toda la tabla
# original de aceleradores USB; el ranking se cerró siguiendo el criterio ya
# presente en la fuente (utilidad real para correr YOLO hoy). Los ítems dados de
# baja por no tener evidencia de YOLO ya no aparecen acá.
ITEM_FILLS: dict[str, dict[str, Any]] = {
    "aceleradores-usb-yolo-b99c645c140c": {  # Google Coral USB Accelerator
        "country": "EE. UU.",
        "description": "Acelerador USB con Edge TPU de Google; se conecta a un PC o a una Raspberry Pi.",
    },
    "aceleradores-usb-yolo-89cba8631cbc": {  # Intel Neural Compute Stick 2
        "country": "EE. UU.",
        "description": "Stick USB con VPU Movidius Myriad X; corre modelos convertidos con OpenVINO.",
    },
    "aceleradores-usb-yolo-eceb704c15df": {  # Intel Movidius Neural Compute Stick
        "country": "EE. UU.",
        "description": "Primera generación del stick USB de Intel, con VPU Myriad 2.",
        "purchase_link": "https://www.intel.com/content/www/us/en/products/sku/125743/intel-movidius-neural-compute-stick/specifications.html",
        "extra_data": {"ranking_recomendado": 12},
    },
    "aceleradores-usb-yolo-5fa822e8dbfa": {  # RK1808 AI Compute Stick
        "country": "China",
        "description": "Stick USB con NPU Rockchip RK1808; los modelos se convierten con el toolkit RKNN.",
    },
    "aceleradores-usb-yolo-1a5feae23d24": {  # Kneron KNEO AI Dongle KL520
        "country": "EE. UU.",
        "description": "Dongle USB con NPU Kneron KL520 para visión embebida.",
        "extra_data": {"ranking_recomendado": 6},
    },
    "aceleradores-usb-yolo-32596d173e89": {  # Kneron KNEO STEM KL720
        "country": "EE. UU.",
        "description": "Dongle USB con NPU Kneron KL720, el hermano mayor del KL520.",
        "extra_data": {"ranking_recomendado": 3},
    },
    "aceleradores-usb-yolo-2cdf105c39ab": {  # Bitmain Sophon Neural Network Stick
        "country": "China",
        "description": "Stick USB con chip BM1880 de Bitmain para inferencia en el borde.",
        "extra_data": {"ranking_recomendado": 8},
    },
    "aceleradores-usb-yolo-909075e491eb": {  # Hailo-8 USB Stick / USB Accelerator
        "country": "Israel",
        "description": "Versión USB del Hailo-8, el acelerador más potente de esta tabla.",
    },
    # Estos dos llegaron desde la cotización de aceleradores externos y traían
    # sólo la columna "interfaz": es el mismo hardware que su fila gemela, por eso
    # comparten datos técnicos y ranking, y cambia el proveedor en "Referencia".
    "edge-ai-f9fef5a6ee7a": {  # Kneron KL720 USB Dongle (Mouser)
        "extra_data": {
            "acelerador": "Kneron KL720",
            "rendimiento_aproximado": "~1.4 TOPS",
            "compatibilidad_yolo": "Sí",
            "referencia": "Mouser",
            "ranking_recomendado": 3,
        },
    },
    "edge-ai-653c10aa38cc": {  # Google Coral USB Accelerator (Mouser)
        "extra_data": {
            "acelerador": "Google Edge TPU",
            "rendimiento_aproximado": "4 TOPS",
            "compatibilidad_yolo": "Sí, mediante TensorFlow Lite INT8",
            "referencia": "Mouser",
            "ranking_recomendado": 2,
        },
    },
    # Tarjeta PCIe: como cualquier acelerador de esta tabla, necesita un anfitrión.
    "edge-ai-f8e4685c2788": {  # BrainChip AKD1000 PCIe Development Board
        "extra_data": {"requiere_host": True},
    },
}


def _is_empty(value: Any) -> bool:
    return value is None or value == ""


def _item_fills() -> dict[str, dict[str, Any]]:
    """Los rellenos escritos a mano más las especificaciones investigadas."""
    fills: dict[str, dict[str, Any]] = {key: dict(value) for key, value in ITEM_FILLS.items()}
    for item_id, specs in PC_SPECS.items():
        extra = fills.setdefault(item_id, {}).setdefault("extra_data", {})
        extra.update(specs)
    for item_id, tops in HOST_TOPS.items():
        extra = fills.setdefault(item_id, {}).setdefault("extra_data", {})
        extra["rendimiento_tops"] = tops
    return fills


def enrich(dry_run: bool = False) -> int:
    db.init_database()
    changes = 0

    for quotation_id, description in QUOTATION_DESCRIPTIONS.items():
        quotation = db.get_quotation(quotation_id)
        if not quotation:
            print(f"  ! cotización ausente, se omite: {quotation_id}")
            continue
        if quotation["description"].strip() == description.strip():
            continue
        print(f"  descripción · {quotation['name']}")
        changes += 1
        if not dry_run:
            db.update_quotation(quotation_id, {"description": description})

    for quotation_id, definitions in QUOTATION_FIELDS.items():
        quotation = db.get_quotation(quotation_id)
        if not quotation:
            print(f"  ! cotización ausente, se omite: {quotation_id}")
            continue
        existing = {field["key"] for field in quotation["extra_fields"]}
        missing = [field for field in definitions if field["key"] not in existing]
        if missing:
            print(f"  columnas · {quotation['name']} · {', '.join(field['key'] for field in missing)}")
            changes += 1
            if not dry_run:
                db.update_quotation(quotation_id, {"extra_fields": list(quotation["extra_fields"]) + missing})

    # Sin rastro público de YOLO no se quedan en el catálogo.
    for item_id, name in WITHOUT_YOLO_EVIDENCE.items():
        if not db.get_item(item_id):
            continue
        print(f"  baja (sin evidencia de YOLO) · {name}")
        changes += 1
        if not dry_run:
            db.delete_item(item_id)

    for item_id, fills in _item_fills().items():
        item = db.get_item(item_id)
        if not item:
            print(f"  ! ítem ausente, se omite: {item_id}")
            continue
        updates: dict[str, Any] = {}
        for field, value in fills.items():
            if field == "extra_data":
                extra = dict(item["extra_data"])
                missing = {key: new for key, new in value.items() if _is_empty(extra.get(key))}
                if missing:
                    extra.update(missing)
                    updates["extra_data"] = extra
            elif _is_empty(item[field]):
                updates[field] = value
        if not updates:
            continue
        print(f"  {item['name']} · {', '.join(sorted(updates))}")
        changes += 1
        if not dry_run:
            db.update_item(item_id, updates)

    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="muestra los cambios sin escribirlos")
    args = parser.parse_args()
    print(f"Base de datos: {db.database_path()}")
    changes = enrich(dry_run=args.dry_run)
    verb = "pendientes" if args.dry_run else "aplicados"
    print(f"{changes} cambio(s) {verb}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
