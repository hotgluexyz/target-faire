"""Faire target sink classes."""

from typing import Any, Dict, List, Optional, Tuple

from hotglue_etl_exceptions import InvalidPayloadError

from target_faire.client import FaireSink
from target_faire import product_mapping as pm

# Fields accepted on PATCH /products/{product_id}/variants/{variant_id}
# (see https://developers.faire.com/docs#/paths/products-product_id--variants--variant_id/patch).
_VARIANT_PATCH_KEYS = frozenset({
    "name",
    "sale_state",
    "lifecycle_state",
    "idempotence_token",
    "sku",
    "available_quantity",
    "backordered_until",
    "wholesale_price_cents",
    "retail_price_cents",
    "tariff_code",
    "images",
    "options",
    "prices",
    "variant_preorder_details",
    "measurements",
    "gtin",
    "orderability_type",
    "case_measurements",
})


class FulfillmentsSink(FaireSink):
    """Sends order fulfillment/shipment data back to Faire.

    Accepts records following the unified SalesOrder shape. The `order_id`
    and `tracking_number` fields are required; `carrier` and `shipping_cost_cents`
    are optional.

    Faire API: POST /external-api/v2/orders/{order_id}/shipments
    """

    name = "Fulfillments"

    def preprocess_record(self, record: dict, context: dict) -> dict:
        order_id = record.get("order_id") or record.get("id")
        if not order_id:
            raise InvalidPayloadError("Record is missing required field: order_id")

        tracking_code = record.get("tracking_number") or record.get("tracking_code")
        if not tracking_code:
            raise InvalidPayloadError("Record is missing required field: tracking_number")
        carrier = record.get("carrier")

        if record.get("shipping_cost_cents") not in (None, ""):
            cost_minor = int(record["shipping_cost_cents"])
        elif record.get("total_shipping") not in (None, ""):
            cost_minor = round(float(record["total_shipping"]) * 100)
        else:
            cost_minor = 0

        shipment = {
            "tracking_code": tracking_code,
            "carrier": carrier,
            "maker_cost": {
                "amount_minor": cost_minor,
                "currency": record.get("currency") or "USD",
            },
        }

        item_ids = record.get("item_ids")
        if item_ids and isinstance(item_ids, list):
            shipment["item_ids"] = item_ids

        return {"order_id": order_id, "shipment": shipment}

    # Faire 400 when an order is past the point where shipments can be added or updated.
    _SHIPMENT_PAYMENT_INITIATED_ERR = "as payment is already initiated"

    def upsert_record(self, record: dict, context: dict):
        state_updates = {}
        order_id = record["order_id"]

        try:
            response = self.request_api(
                "POST",
                endpoint=f"orders/{order_id}/shipments",
                request_data={"shipments": [record["shipment"]]},
            )
        except InvalidPayloadError as exc:
            if self._SHIPMENT_PAYMENT_INITIATED_ERR in str(exc).lower():
                self.logger.warning(
                    "Skipping shipment for order %s: payment already initiated",
                    order_id,
                )
                return order_id, True, {"existing": True}
            raise

        try:
            shipments = response.json().get("shipments", [])
        except Exception:
            shipments = []
        last = shipments[-1] if shipments else None
        shipment_id = last.get("id") if isinstance(last, dict) else None
        self.logger.info(f"Shipped order {order_id}, shipment_id={shipment_id}")
        return shipment_id, True, state_updates


class ProductsSink(FaireSink):
    """Creates or updates products in Faire.

    Accepts records following the unified Products shape. New products are
    created via POST /products; existing Faire products (ids matching
    ``p_[0-9a-z]{10}``) are updated via PATCH /products/{id}.

    Faire API:
      - POST /external-api/v2/products
      - PATCH /external-api/v2/products/{product_id}
    """

    name = "Products"

    def preprocess_record(self, record: dict, context: dict) -> dict:
        default_taxonomy = self.config.get("default_taxonomy_type_id")
        product_id = record.get("id")
        if pm.is_faire_product_id(product_id):
            payload = self.clean_payload({
                "name": record.get("name"),
                "description": record.get("description"),
                "short_description": record.get("short_description"),
                "unit_multiplier": record.get("unit_multiplier"),
                "minimum_order_quantity": record.get("minimum_order_quantity"),
                "made_in_country": record.get("made_in_country"),
                "preorderable": record.get("preorderable"),
                "lifecycle_state": record.get("lifecycle_state"),
                # Only send taxonomy when present on the record; do not apply the
                # config default on PATCH or partial updates would overwrite it.
                "taxonomy_type": pm.taxonomy_type_payload(record),
            })
            if not payload:
                raise InvalidPayloadError(
                    "Update record has no fields to send for product "
                    f"{product_id}"
                )
            return {
                "action": "update",
                "product_id": str(product_id),
                "payload": payload,
            }

        # name is required for create
        name = record.get("name")
        if not name:
            raise InvalidPayloadError("Record is missing required field: name")

        currency = record.get("currency") or "USD"
        country = record.get("country") or "USA"
        variants = pm.normalize_variants(record)
        faire_variants = [
            pm.build_faire_variant(variant, record, currency, country)
            for variant in variants
        ]

        payload = self.clean_payload({
            "idempotence_token": pm.product_idempotence_token(record),
            "name": name,
            "description": record.get("description"),
            "short_description": record.get("short_description"),
            "unit_multiplier": record.get("unit_multiplier", 1),
            "minimum_order_quantity": record.get("minimum_order_quantity", 1),
            "made_in_country": record.get("made_in_country"),
            "preorderable": record.get("preorderable"),
            "lifecycle_state": record.get("lifecycle_state"),
            "taxonomy_type": pm.taxonomy_type_payload(record, default_taxonomy),
            "variant_option_sets": pm.build_variant_option_sets(variants),
            "variants": faire_variants,
        })
        return {"action": "create", "payload": payload}

    def upsert_record(self, record: dict, context: dict):
        state_updates = {}
        action = record["action"]
        payload = record["payload"]

        if action == "update":
            product_id = record["product_id"]
            response = self.request_api(
                "PATCH",
                endpoint=f"products/{product_id}",
                request_data=payload,
            )
            result = response.json()
            self.logger.info(f"Updated product {product_id}")
            return result.get("id", product_id), True, state_updates

        response = self.request_api("POST", endpoint="products", request_data=payload)
        result = response.json()
        product_id = result.get("id")
        self.logger.info(f"Created product {product_id}")
        return product_id, True, state_updates


class ProductVariantsSink(FaireSink):
    """Updates an existing Faire product variant.

    Resolves ``product_id`` and ``variant_id`` from ``product_id``, ``variant_id``,
    and ``id`` when they match Faire id formats. If the variant id is missing (or
    ``id`` is null / not a Faire variant id), looks up both ids using ``sku``
    via ``GET /products?sku=``.

    When ``variant_id`` is known but ``product_id`` is missing, ``sku`` is used
    to list products and find the product that contains that variant.

    Faire API: ``PATCH /external-api/v2/products/{product_id}/variants/{variant_id}``
    (see Faire developer docs).
    """

    name = "ProductVariants"

    def _products_from_list_response(self, response) -> List[dict]:
        try:
            data = response.json()
        except Exception:
            return []
        products = data.get("products")
        if isinstance(products, list):
            return [p for p in products if isinstance(p, dict)]
        return []

    def _fetch_product_document(self, product_id: str) -> Optional[dict]:
        response = self.request_api("GET", endpoint=f"products/{product_id}")
        try:
            data = response.json()
        except Exception:
            return None
        if isinstance(data.get("product"), dict):
            return data["product"]
        if isinstance(data, dict) and pm.is_faire_product_id(data.get("id")):
            return data
        return None

    def _variants_for_product(self, product: dict) -> List[dict]:
        variants = product.get("variants")
        if isinstance(variants, list) and variants:
            return [v for v in variants if isinstance(v, dict)]
        pid = product.get("id")
        if pm.is_faire_product_id(pid):
            full = self._fetch_product_document(str(pid))
            if full:
                v2 = full.get("variants")
                if isinstance(v2, list):
                    return [v for v in v2 if isinstance(v, dict)]
        return []

    def _lookup_product_variant_by_sku(self, sku: str) -> Tuple[Optional[str], Optional[str]]:
        """Return ``(product_id, variant_id)`` for an exact SKU match, or ``(None, None)``."""
        sku_key = str(sku).strip()
        if not sku_key:
            return None, None
        response = self.request_api(
            "GET",
            endpoint="products",
            params={"sku": sku_key, "limit": 50},
        )
        for product in self._products_from_list_response(response):
            for variant in self._variants_for_product(product):
                if str(variant.get("sku", "")).strip() == sku_key:
                    pid, vid = product.get("id"), variant.get("id")
                    if pm.is_faire_product_id(pid) and pm.is_faire_variant_id(vid):
                        return str(pid), str(vid)
        return None, None

    def _product_id_containing_variant(self, variant_id: str, sku: str) -> Optional[str]:
        """Return product id for ``variant_id`` using a SKU list lookup."""
        sku_key = str(sku).strip()
        if not sku_key or not pm.is_faire_variant_id(variant_id):
            return None
        response = self.request_api(
            "GET",
            endpoint="products",
            params={"sku": sku_key, "limit": 50},
        )
        for product in self._products_from_list_response(response):
            for variant in self._variants_for_product(product):
                if variant.get("id") == variant_id:
                    pid = product.get("id")
                    if pm.is_faire_product_id(pid):
                        return str(pid)
        return None

    def _resolve_patch_targets(self, record: dict) -> Tuple[str, str]:
        product_id = record.get("product_id")
        variant_id = record.get("variant_id")
        rid = record.get("id")

        if variant_id in (None, "") and rid not in (None, ""):
            if pm.is_faire_variant_id(rid):
                variant_id = rid
        if product_id in (None, "") and rid not in (None, ""):
            if pm.is_faire_product_id(rid):
                product_id = rid

        sku_raw = record.get("sku")
        sku = str(sku_raw).strip() if sku_raw not in (None, "") else None

        if sku and (
            not pm.is_faire_variant_id(variant_id)
            or not pm.is_faire_product_id(product_id)
        ):
            lp, lv = self._lookup_product_variant_by_sku(sku)
            if not pm.is_faire_variant_id(variant_id) and lv:
                variant_id = lv
            if not pm.is_faire_product_id(product_id) and lp:
                product_id = lp

        if pm.is_faire_variant_id(variant_id) and not pm.is_faire_product_id(product_id):
            if sku:
                lp = self._product_id_containing_variant(str(variant_id), sku)
                if lp:
                    product_id = lp

        if not pm.is_faire_product_id(product_id):
            raise InvalidPayloadError(
                "Record needs a Faire product_id (or id in product id format), "
                "or a sku that matches a Faire variant"
            )
        if not pm.is_faire_variant_id(variant_id):
            raise InvalidPayloadError(
                "Record needs a Faire variant id on id/variant_id, "
                "or a sku that matches a Faire variant"
            )
        return str(product_id), str(variant_id)

    def _variant_patch_payload(self, record: dict) -> dict:
        body: Dict[str, Any] = {
            k: record[k] for k in _VARIANT_PATCH_KEYS if k in record and k != "prices"
        }
        if "prices" in record:
            body["prices"] = record["prices"]
        if "available_quantity" in body:
            body["available_quantity"] = int(body["available_quantity"])

        currency = record.get("currency") or "USD"
        country = record.get("country") or "USA"
        if "prices" not in body:
            has_flat_cents = (
                record.get("wholesale_price_cents") not in (None, "")
                or record.get("retail_price_cents") not in (None, "")
            )
            if not has_flat_cents:
                vslice = {
                    "cost": record.get("cost"),
                    "price": record.get("price"),
                    "wholesale_price_cents": record.get("wholesale_price_cents"),
                    "retail_price_cents": record.get("retail_price_cents"),
                }
                if any(v not in (None, "") for v in vslice.values()):
                    body["prices"] = pm.faire_variant_prices_array(
                        vslice, record, currency, country
                    )

        return self.clean_payload(body)

    def preprocess_record(self, record: dict, context: dict) -> dict:
        product_id, variant_id = self._resolve_patch_targets(record)
        payload = self._variant_patch_payload(record)
        if not payload:
            raise InvalidPayloadError(
                f"No variant fields to PATCH for product {product_id} variant {variant_id}"
            )
        return {
            "product_id": product_id,
            "variant_id": variant_id,
            "payload": payload,
        }

    def upsert_record(self, record: dict, context: dict):
        state_updates = {}
        product_id = record["product_id"]
        variant_id = record["variant_id"]
        response = self.request_api(
            "PATCH",
            endpoint=f"products/{product_id}/variants/{variant_id}",
            request_data=record["payload"],
        )
        try:
            result = response.json()
        except Exception:
            result = {}
        vid = result.get("id", variant_id)
        self.logger.info(
            "Updated product %s variant %s",
            product_id,
            vid,
        )
        return vid, True, state_updates
