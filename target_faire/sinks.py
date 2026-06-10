"""Faire target sink classes."""

from hotglue_etl_exceptions import InvalidPayloadError

from target_faire.client import FaireSink
from target_faire import product_mapping as pm


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

    def upsert_record(self, record: dict, context: dict):
        state_updates = {}
        order_id = record["order_id"]

        response = self.request_api(
            "POST",
            endpoint=f"orders/{order_id}/shipments",
            request_data={"shipments": [record["shipment"]]},
        )

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
