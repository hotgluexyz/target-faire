"""Faire target sink classes."""

from hotglue_etl_exceptions import InvalidPayloadError

from target_faire.client import FaireSink


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
                "currency": record.get("currency", "USD"),
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
