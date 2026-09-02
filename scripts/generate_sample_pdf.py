from pathlib import Path

from app.db import get_quotation, init_database
from app.services.pdf import build_pdf


def main() -> None:
    init_database()
    quotation = get_quotation("aceleradores-usb-yolo")
    if not quotation:
        raise RuntimeError("No se encontró la cotización de ejemplo")
    output = Path("output/pdf/cotizacion-edge-ai-ejemplo.pdf")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(build_pdf(quotation))
    print(output.resolve())


if __name__ == "__main__":
    main()
