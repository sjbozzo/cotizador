from app.seed import load_seed_quotations


def test_sources_are_curated_into_nine_quotations():
    quotations = load_seed_quotations()
    counts = {quotation["id"]: len(quotation["items"]) for quotation in quotations}
    assert counts == {
        "aceleradores-usb-yolo": 12,
        "pcs-industriales-m2-ai": 16,
        "edge-ai-plataformas-host": 28,
        "edge-ai-vision": 7,
        "edge-ai-aceleradores-externos": 4,
        "edge-ai-aceleradores-m2": 7,
        "raspberry-pi5-carcasas-industriales": 7,
        "raspberry-pi5-gabinetes-ip-hailo": 4,
        "raspberry-pi5-carcasas-hailo": 11,
    }
    assert sum(counts.values()) == 96


def test_m2_semantics_are_not_mixed_between_hosts_and_accelerators():
    quotations = {quotation["id"]: quotation for quotation in load_seed_quotations()}
    hosts = quotations["edge-ai-plataformas-host"]
    accelerators = quotations["edge-ai-aceleradores-m2"]
    assert {field["key"] for field in hosts["extra_fields"]} == {"espacio_m2", "subtipo"}
    assert {field["key"] for field in accelerators["extra_fields"]} == {"requiere_host"}
    assert all("espacio_m2" not in item["extra_data"] for item in accelerators["items"])
    assert all(item["extra_data"]["requiere_host"] is True for item in accelerators["items"])


def test_placeholder_svg_is_not_treated_as_a_real_product_photo():
    quotations = {quotation["id"]: quotation for quotation in load_seed_quotations()}
    items = quotations["raspberry-pi5-carcasas-hailo"]["items"]
    akasa = next(item for item in items if item["name"].startswith("Akasa A-RA19"))
    assert akasa["photo"] == ""

