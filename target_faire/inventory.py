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


def patch_inventories_with_retry(
    sink: FaireBatchSink,
    inventories: List[dict],
) -> Tuple[Optional[requests.Response], Dict[str, str]]:
    """PATCH inventories, peeling bad SKUs from 400/404 errors until success."""
    return _request_inventories_with_retry(
        sink,
        "PATCH",
        inventories=inventories,
    )


def peel_skus_from_error(response: requests.Response, skus: List[str]) -> List[str]:
    """Return SKUs named in a Faire 400/404 inventory error."""
    if response.status_code not in (400, 404):
        return []
    try:
        message = response.json().get("message", "")
    except Exception:
        return []

    sku_set = set(skus)
    if message in sku_set:
        return [message]

    bracket_match = re.search(r"\[([^\]]+)\]", message)
    if bracket_match and bracket_match.group(1) in sku_set:
        return [bracket_match.group(1)]

    colon_match = re.search(r":\s*(.+)\s*$", message)
    if colon_match:
        suffix = colon_match.group(1).strip()
        if suffix in sku_set:
            return [suffix]
        matched = [
            part.strip()
            for part in suffix.split(",")
            if part.strip() in sku_set
        ]
        if matched:
            return matched

    token_matches = [
        sku
        for sku in skus
        if re.search(rf"(?<!\w){re.escape(sku)}(?!\w)", message)
    ]
    if len(token_matches) == 1:
        return token_matches

    return []


def _inventory_request(
    sink: FaireBatchSink,
    http_method: str,
    *,
    params: Optional[dict] = None,
    request_data: Optional[dict] = None,
) -> requests.Response:
    """Send a GET or PATCH inventory-by-skus request, tolerating 400/404."""
    return sink.faire_request(
        http_method,
        INVENTORY_BY_SKUS_ENDPOINT,
        params=params,
        request_data=request_data,
        allowed_statuses=(400, 404),
    )


def _request_inventories_with_retry(
    sink: FaireBatchSink,
    http_method: str,
    *,
    skus: Optional[List[str]] = None,
    inventories: Optional[List[dict]] = None,
) -> Tuple[Optional[requests.Response], Dict[str, str]]:
    """GET or PATCH inventories, peeling bad SKUs from 400/404 errors."""
    if http_method == "GET":
        remaining = list(skus or [])
        payload_key = None
    else:
        remaining = list(inventories or [])
        payload_key = "inventories"

    failed_by_sku: Dict[str, str] = {}

    while remaining:
        if http_method == "GET":
            response = _inventory_request(sink, "GET", params={"skus": remaining})
            remaining_skus = remaining
        else:
            response = _inventory_request(
                sink,
                "PATCH",
                request_data={payload_key: remaining},
            )
            remaining_skus = [item["sku"] for item in remaining]

        if response.status_code == 200:
            return response, failed_by_sku

        bad_skus = peel_skus_from_error(response, remaining_skus)
        error = sink._extract_error_message(response)
        if not bad_skus:
            for sku in remaining_skus:
                failed_by_sku[sku] = error
            return None, failed_by_sku

        bad_sku_set = set(bad_skus)
        for bad_sku in bad_skus:
            failed_by_sku[bad_sku] = error
        if http_method == "GET":
            remaining = [sku for sku in remaining if sku not in bad_sku_set]
        else:
            remaining = [item for item in remaining if item["sku"] not in bad_sku_set]

    return None, failed_by_sku


def fetch_inventory_by_skus(
    sink: FaireBatchSink,
    skus: List[str],
) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """GET current inventory levels for the given SKUs."""
    if not skus:
        return {}, {}

    response, failed_by_sku = _request_inventories_with_retry(sink, "GET", skus=skus)
    if response is None:
        return {}, failed_by_sku

    return response.json().get("inventories") or {}, failed_by_sku


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
    current, get_failures = fetch_inventory_by_skus(sink, list(by_sku))
    results: Dict[str, dict] = {}
    patch_items: List[dict] = []

    for sku, record in by_sku.items():
        if sku in get_failures:
            results[sku] = _result_item(
                record,
                success=False,
                error=get_failures[sku],
            )
            continue
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
