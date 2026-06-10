"""Map unified Products records to Faire API payloads."""

import uuid
from typing import Any, Optional

from hotglue_etl_exceptions import InvalidPayloadError


def is_faire_variant_id(value: Any) -> bool:
    """Return True if value is an existing Faire variant id (``po_...``)."""
    return bool(value) and str(value).startswith("po_")


def is_faire_product_id(value: Any) -> bool:
    """Return True if value is an existing Faire product id (``p_...``, not ``po_...``)."""
    if not value:
        return False
    candidate = str(value)
    return candidate.startswith("p_") and not is_faire_variant_id(candidate)


def is_faire_managed_id(value: Any) -> bool:
    """Return True if value is a Faire-assigned product or variant id."""
    return is_faire_product_id(value) or is_faire_variant_id(value)


def _dollars_to_minor(value: Any) -> Optional[int]:
    """Convert a dollar amount to minor currency units (cents), or None if absent."""
    if value in (None, ""):
        return None
    return round(float(value) * 100)


def _variant_price_minor(
    variant: dict,
    product_level: Any,
    *,
    cents_key: str,
    dollars_key: str,
    label: str,
) -> int:
    """Resolve a variant price in minor units from cent or dollar fields."""
    if variant.get(cents_key) not in (None, ""):
        return int(variant[cents_key])
    minor = _dollars_to_minor(variant.get(dollars_key))
    if minor is not None:
        return minor
    minor = _dollars_to_minor(product_level)
    if minor is not None:
        return minor
    raise InvalidPayloadError(
        f"Variant is missing {label} price ({dollars_key} or {cents_key})"
    )


def product_idempotence_token(record: dict) -> str:
    """Derive a stable product idempotence token for Faire create requests."""
    if record.get("idempotence_token") not in (None, ""):
        return str(record["idempotence_token"])
    if record.get("sku") not in (None, ""):
        return str(record["sku"])

    record_id = record.get("id")
    if record_id and not is_faire_product_id(record_id):
        return str(record_id)

    skus = [v.get("sku") for v in record.get("variants") or [] if v.get("sku")]
    if len(skus) == 1:
        return skus[0]
    if skus:
        return "|".join(sorted(skus))

    return str(uuid.uuid4())


def variant_idempotence_token(variant: dict) -> str:
    """Derive a stable variant idempotence token for Faire create requests."""
    if variant.get("idempotence_token") not in (None, ""):
        return str(variant["idempotence_token"])
    if variant.get("sku") not in (None, ""):
        return str(variant["sku"])

    variant_id = variant.get("id")
    if variant_id and not is_faire_variant_id(variant_id):
        return str(variant_id)

    return str(uuid.uuid4())


def resolve_taxonomy_type_id(
    record: dict,
    default_taxonomy_type_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve the Faire taxonomy type id (``tt_...``) when present on the record or config."""
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
    return str(resolved) if resolved else None


def taxonomy_type_payload(
    record: dict,
    default_taxonomy_type_id: Optional[str] = None,
) -> Optional[dict]:
    """Return a Faire ``taxonomy_type`` object, or None when no id is available."""
    taxonomy_id = resolve_taxonomy_type_id(record, default_taxonomy_type_id)
    return {"id": taxonomy_id} if taxonomy_id else None


def build_variant_option_sets(variants: list) -> list:
    """Build Faire ``variant_option_sets`` from unified variant options."""
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
    """Map one unified variant to a Faire API variant payload."""
    wholesale = _variant_price_minor(
        variant, record.get("cost"),
        cents_key="wholesale_price_cents", dollars_key="cost", label="wholesale",
    )
    retail = _variant_price_minor(
        variant, record.get("price"),
        cents_key="retail_price_cents", dollars_key="price", label="retail",
    )

    faire_variant = {
        "idempotence_token": variant_idempotence_token(variant),
        "options": variant.get("options") or [],
        "prices": [{
            "geo_constraint": {"country": country},
            "wholesale_price": {"amount_minor": wholesale, "currency": currency},
            "retail_price": {"amount_minor": retail, "currency": currency},
        }],
    }

    sku = variant.get("sku")
    if sku:
        faire_variant["sku"] = sku

    if variant.get("available_quantity") not in (None, ""):
        faire_variant["available_quantity"] = int(variant["available_quantity"])

    return faire_variant


def normalize_variants(record: dict) -> list:
    """Return variant list from the record, synthesizing one when omitted."""
    variants = record.get("variants") or []
    if variants:
        return variants

    sku = record.get("sku") or record.get("id")
    if is_faire_managed_id(sku):
        sku = None

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
