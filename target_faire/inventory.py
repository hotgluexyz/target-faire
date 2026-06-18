"""Helpers for Faire product-inventory by-SKU batch updates."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import requests

from hotglue_etl_exceptions import InvalidPayloadError

if TYPE_CHECKING:
    from target_faire.client import FaireBatchSink

INVENTORY_BY_SKUS_ENDPOINT = "product-inventory/by-skus"
QUANTITY_MODE_ON_HAND = "on_hand"
QUANTITY_MODE_AVAILABLE = "available"


def normalize_inventory_record(record: dict) -> dict:
    """Validate a ProductVariants record and resolve the quantity update mode."""
    sku = record.get("sku")
    if sku in (None, ""):
        raise InvalidPayloadError("Record is missing required field: sku")

    on_hand = record.get("on_hand_quantity")
    available = record.get("available_quantity")
    if on_hand not in (None, ""):
        try:
            quantity = int(on_hand)
        except (TypeError, ValueError) as exc:
            raise InvalidPayloadError(
                f"Invalid on_hand_quantity for sku {sku}: {on_hand!r}"
            ) from exc
        mode = QUANTITY_MODE_ON_HAND
    elif available not in (None, ""):
        try:
            quantity = int(available)
        except (TypeError, ValueError) as exc:
            raise InvalidPayloadError(
                f"Invalid available_quantity for sku {sku}: {available!r}"
            ) from exc
        mode = QUANTITY_MODE_AVAILABLE
    else:
        raise InvalidPayloadError(
            "Record is missing required field: on_hand_quantity or available_quantity"
        )

    return {
        "sku": str(sku).strip(),
        "mode": mode,
        "quantity": quantity,
    }


def dedupe_records_by_sku(records: List[dict]) -> List[dict]:
    """Return one record per SKU, keeping the last update for each SKU."""
    by_sku: Dict[str, dict] = {}
    for record in records:
        by_sku[record["sku"]] = record
    return list(by_sku.values())


def _quantity(inventory_entry: dict, field: str) -> int:
    """Extract an integer quantity from a nested Faire inventory field."""
    value = (inventory_entry.get(field) or {}).get("quantity")
    return int(value or 0)


def build_patch_item(record: dict, current_inventory: dict) -> dict:
    """Map a normalized record to a Faire PATCH inventory item."""
    if record["mode"] == QUANTITY_MODE_ON_HAND:
        on_hand = record["quantity"]
    else:
        on_hand = record["quantity"] + _quantity(current_inventory, "committed_quantity")
    return {"sku": record["sku"], "on_hand_quantity": on_hand}


def fetch_inventory_by_skus(sink: FaireBatchSink, skus: List[str]) -> Dict[str, dict]:
    """GET current inventory levels for the given SKUs."""
    if not skus:
        return {}
    response = sink.faire_request(
        "GET",
        INVENTORY_BY_SKUS_ENDPOINT,
        params={"skus": skus},
    )
    return response.json().get("inventories") or {}


def patch_inventories(
    sink: FaireBatchSink,
    inventories: List[dict],
) -> requests.Response:
    """PATCH inventory levels, returning 400/404 responses for batch error handling."""
    return sink.faire_request(
        "PATCH",
        INVENTORY_BY_SKUS_ENDPOINT,
        request_data={"inventories": inventories},
        allowed_statuses=(400, 404),
    )


def peel_sku_from_error(response: requests.Response, skus: List[str]) -> Optional[str]:
    """Return a SKU named in a Faire 400/404 inventory error, if unambiguous."""
    if response.status_code not in (400, 404):
        return None
    try:
        message = response.json().get("message", "")
    except Exception:
        return None

    sku_set = set(skus)
    if message in sku_set:
        return message

    bracket_match = re.search(r"\[([^\]]+)\]", message)
    if bracket_match and bracket_match.group(1) in sku_set:
        return bracket_match.group(1)

    return None


def patch_inventories_with_retry(
    sink: FaireBatchSink,
    inventories: List[dict],
) -> Tuple[Optional[requests.Response], Dict[str, str]]:
    """PATCH inventories, peeling unknown SKUs from 404 errors until success."""
    remaining = list(inventories)
    failed_by_sku: Dict[str, str] = {}

    while remaining:
        response = patch_inventories(sink, remaining)
        if response.status_code == 200:
            return response, failed_by_sku

        bad_sku = peel_sku_from_error(response, [item["sku"] for item in remaining])
        if not bad_sku:
            error = sink._extract_error_message(response)
            for item in remaining:
                failed_by_sku[item["sku"]] = error
            return None, failed_by_sku

        failed_by_sku[bad_sku] = sink._extract_error_message(response)
        remaining = [item for item in remaining if item["sku"] != bad_sku]

    return None, failed_by_sku


def _result_item(
    record: dict,
    *,
    success: bool,
    error: Optional[str] = None,
    superseded: bool = False,
) -> dict:
    """Build a single per-record sync result entry."""
    item = {"record": record, "success": success}
    if error:
        item["error"] = error
    if superseded:
        item["superseded"] = True
    return item


def sync_inventory_by_skus(sink: FaireBatchSink, records: List[dict]) -> dict:
    """Run GET preflight, build PATCH payloads, and update inventory in batch."""
    deduped = dedupe_records_by_sku(records)
    if not deduped:
        return {"items": []}

    by_sku = {record["sku"]: record for record in deduped}
    current = fetch_inventory_by_skus(sink, list(by_sku))
    results: Dict[str, dict] = {}
    patch_items: List[dict] = []

    for sku, record in by_sku.items():
        if sku not in current:
            results[sku] = _result_item(
                record,
                success=False,
                error=f"SKU not found in Faire: {sku}",
            )
            continue
        patch_items.append(build_patch_item(record, current[sku]))

    response, patch_failures = patch_inventories_with_retry(sink, patch_items)
    response_inventories = (
        response.json().get("inventories") or {} if response is not None else {}
    )

    for sku, record in by_sku.items():
        if sku in results:
            continue
        if sku in patch_failures:
            results[sku] = _result_item(record, success=False, error=patch_failures[sku])
            continue

        inventory = response_inventories.get(sku, current[sku])
        sink.logger.info(
            "Updated inventory for sku %s (requested %s=%s, result on_hand=%s, available=%s)",
            sku,
            record["mode"],
            record["quantity"],
            _quantity(inventory, "on_hand_quantity"),
            _quantity(inventory, "available_quantity"),
        )
        results[sku] = {
            "record": record,
            "success": True,
            "inventory": inventory,
        }

    last_index_by_sku = {record["sku"]: index for index, record in enumerate(records)}
    items: List[dict] = []
    for index, record in enumerate(records):
        sku = record["sku"]
        sku_result = results[sku]
        if index != last_index_by_sku[sku]:
            items.append(_result_item(
                record,
                success=sku_result["success"],
                error=sku_result.get("error"),
                superseded=True,
            ))
            continue
        items.append({**sku_result, "record": record})

    return {"items": items}
