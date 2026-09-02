from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


FIELD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,49}$")
FIELD_TYPES = {"text", "boolean", "number", "url", "date"}
MAX_EXTRA_FIELDS = 30


def safe_link(value: str, *, allow_data_image: bool = False) -> str:
    value = value.strip()
    if not value:
        return ""
    if allow_data_image and re.match(r"^data:image/(?:webp|png|jpeg|gif);base64,", value, flags=re.I):
        return value
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("El enlace debe comenzar con http:// o https://")
    return value


def normalize_extra_fields(values: list[Any]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        if isinstance(value, str):
            key, separator, field_type = value.partition(":")
            key = key.strip()
            field_type = field_type.strip().lower() if separator else "text"
            label = key.replace("_", " ").strip().capitalize()
        elif isinstance(value, dict):
            key = str(value.get("key", "")).strip()
            label = str(value.get("label") or key.replace("_", " ").capitalize()).strip()
            field_type = str(value.get("type", "text")).strip().lower()
        else:
            raise ValueError(f"El campo extra #{index + 1} no tiene un formato válido")
        if not FIELD_KEY_RE.fullmatch(key):
            raise ValueError(f"Clave de campo extra inválida: {key!r}")
        if field_type == "string":
            field_type = "text"
        if field_type not in FIELD_TYPES:
            raise ValueError(f"Tipo no admitido para {key}: {field_type}")
        if key in seen:
            raise ValueError(f"Campo extra repetido: {key}")
        if not label or len(label) > 80:
            raise ValueError(f"Etiqueta inválida para {key}")
        seen.add(key)
        normalized.append({"key": key, "label": label, "type": field_type})
    return normalized


class ExtraFieldDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    type: Literal["text", "boolean", "number", "url", "date"] = "text"

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        value = value.strip()
        if not FIELD_KEY_RE.fullmatch(value):
            raise ValueError("Use minúsculas, números y guion bajo; debe comenzar con una letra")
        return value

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("La etiqueta es obligatoria")
        return value


class QuotationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=180)
    purchase_link: str = Field(default="", max_length=2000)
    quote_date: date = Field(default_factory=date.today)
    description: str = Field(default="", max_length=4000)
    extra_fields: list[Any] = Field(default_factory=list, max_length=MAX_EXTRA_FIELDS)

    @field_validator("name", "description")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("purchase_link")
    @classmethod
    def validate_purchase_link(cls, value: str) -> str:
        return safe_link(value)

    @field_validator("extra_fields")
    @classmethod
    def validate_extra_fields(cls, value: list[Any]) -> list[dict[str, str]]:
        return normalize_extra_fields(value)


class QuotationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=180)
    purchase_link: str | None = Field(default=None, max_length=2000)
    quote_date: date | None = None
    description: str | None = Field(default=None, max_length=4000)
    extra_fields: list[Any] | None = Field(default=None, max_length=MAX_EXTRA_FIELDS)

    @field_validator("name", "description")
    @classmethod
    def trim_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else value

    @field_validator("purchase_link")
    @classmethod
    def validate_purchase_link(cls, value: str | None) -> str | None:
        return safe_link(value) if value is not None else None

    @field_validator("extra_fields")
    @classmethod
    def validate_extra_fields(cls, value: list[Any] | None) -> list[dict[str, str]] | None:
        return normalize_extra_fields(value) if value is not None else None


class QuotationReorder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: list[str] = Field(min_length=1, max_length=500)

    @field_validator("order")
    @classmethod
    def validate_order(cls, value: list[str]) -> list[str]:
        cleaned = [entry.strip() for entry in value]
        if any(not entry or len(entry) > 200 for entry in cleaned):
            raise ValueError("Cada id de cotización debe ser un texto no vacío")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("El nuevo orden repite una cotización")
        return cleaned


class ItemBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=240)
    country: str = Field(default="", max_length=160)
    photo: str = Field(default="", max_length=6_000_000)
    purchase_link: str = Field(default="", max_length=2000)
    description: str = Field(default="", max_length=8000)
    comment: str = Field(default="", max_length=8000)
    price: str = Field(default="", max_length=300)
    included: bool = True
    extra_data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "country", "description", "comment", "price")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("purchase_link")
    @classmethod
    def validate_purchase_link(cls, value: str) -> str:
        return safe_link(value)

    @field_validator("photo")
    @classmethod
    def validate_photo(cls, value: str) -> str:
        return safe_link(value, allow_data_image=True)


class ItemCreate(ItemBase):
    pass


class ItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: int | None = Field(default=None, ge=0)
    name: str | None = Field(default=None, min_length=1, max_length=240)
    country: str | None = Field(default=None, max_length=160)
    photo: str | None = Field(default=None, max_length=6_000_000)
    purchase_link: str | None = Field(default=None, max_length=2000)
    description: str | None = Field(default=None, max_length=8000)
    comment: str | None = Field(default=None, max_length=8000)
    price: str | None = Field(default=None, max_length=300)
    included: bool | None = None
    extra_data: dict[str, Any] | None = None

    @field_validator("name", "country", "description", "comment", "price")
    @classmethod
    def trim_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else value

    @field_validator("purchase_link")
    @classmethod
    def validate_purchase_link(cls, value: str | None) -> str | None:
        return safe_link(value) if value is not None else None

    @field_validator("photo")
    @classmethod
    def validate_photo(cls, value: str | None) -> str | None:
        return safe_link(value, allow_data_image=True) if value is not None else None


class ItemMove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quotation_id: str = Field(min_length=1, max_length=200)

    @field_validator("quotation_id")
    @classmethod
    def trim_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Indica la cotización destino")
        return value


class ImportContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=2, max_length=12_000_000)


class ImportApply(ImportContent):
    expected_revision: int | None = Field(default=None, ge=1)
    adopt_extra_fields: bool = True


class ShareSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    included_ids: list[str] = Field(default_factory=list, max_length=5000)

    @model_validator(mode="after")
    def unique_ids(self) -> "ShareSelection":
        self.included_ids = list(dict.fromkeys(self.included_ids))
        return self

