"""Map unified Products records to Faire API payloads."""

import uuid
from typing import Any, Optional

from hotglue_etl_exceptions import InvalidPayloadError


def is_faire_product_id(value: Any) -> bool:
    return bool(value) and str(value).startswith("p_")


def dollars_to_minor(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    return round(float(value) * 100)


def variant_wholesale_cents(variant: dict, product_cost: Any = None) -> int:
    if variant.get("wholesale_price_cents") not in (None, ""):
        return int(variant["wholesale_price_cents"])
    cost = dollars_to_minor(variant.get("cost"))
    if cost is not None:
        return cost
    product_cost_minor = dollars_to_minor(product_cost)
    if product_cost_minor is not None:
        return product_cost_minor
    raise InvalidPayloadError(
        "Variant is missing wholesale price (cost or wholesale_price_cents)"
    )


def variant_retail_cents(variant: dict, product_price: Any = None) -> int:
    if variant.get("retail_price_cents") not in (None, ""):
        return int(variant["retail_price_cents"])
    price = dollars_to_minor(variant.get("price"))
    if price is not None:
        return price
    product_price_minor = dollars_to_minor(product_price)
    if product_price_minor is not None:
        return product_price_minor
    raise InvalidPayloadError(
        "Variant is missing retail price (price or retail_price_cents)"
    )


def idempotence_token(record_id: Any, faire_prefix: str) -> str:
    if record_id and not str(record_id).startswith(faire_prefix):
        return str(record_id)
    return str(uuid.uuid4())


def taxonomy_type_id(record: dict, default_taxonomy_type_id: Optional[str] = None) -> str:
    taxonomy = record.get("taxonomy_type") or {}
    category = record.get("category") or {}
    categories = record.get("categories") or []
    first_category = categories[0] if categories else {}

    resolved = (
        taxonomy.get("id")
        or category.get("id")
        or (first_category.get("id") if isinstance(first_category, dict) else None)
        or default_taxonomy_type_id
    )
    if not resolved:
        raise InvalidPayloadError(
            "Record is missing taxonomy type id (category.id, taxonomy_type.id, "
            "or default_taxonomy_type_id in config)"
        )
    return str(resolved)


def build_variant_option_sets(variants: list) -> list:
    option_sets = {}
    for variant in variants:
        for option in variant.get("options") or []:
            name = option.get("name")
            value = option.get("value")
            if name and value:
                option_sets.setdefault(name, set()).add(value)
    return [{"name": name, "values": sorted(values)} for name, values in option_sets.items()]


def build_faire_variant(
    variant: dict,
    record: dict,
    currency: str,
    country: str,
) -> dict:
    sku = variant.get("sku")
    if not sku:
        raise InvalidPayloadError("Variant is missing required field: sku")

    wholesale = variant_wholesale_cents(variant, record.get("cost"))
    retail = variant_retail_cents(variant, record.get("price"))

    faire_variant = {
        "idempotence_token": idempotence_token(variant.get("id"), "po_"),
        "sku": sku,
        "options": variant.get("options") or [],
        "prices": [{
            "geo_constraint": {"country": country},
            "wholesale_price": {"amount_minor": wholesale, "currency": currency},
            "retail_price": {"amount_minor": retail, "currency": currency},
        }],
    }

    if variant.get("available_quantity") not in (None, ""):
        faire_variant["available_quantity"] = int(variant["available_quantity"])

    return faire_variant


def normalize_variants(record: dict) -> list:
    variants = record.get("variants") or []
    if variants:
        return variants

    sku = record.get("sku") or record.get("id")
    if not sku or is_faire_product_id(sku):
        raise InvalidPayloadError(
            "Record is missing variants and has no sku for a default variant"
        )

    return [{
        "id": record.get("id"),
        "sku": sku,
        "price": record.get("price"),
        "cost": record.get("cost"),
        "wholesale_price_cents": record.get("wholesale_price_cents"),
        "retail_price_cents": record.get("retail_price_cents"),
        "available_quantity": record.get("available_quantity"),
        "options": [],
    }]
