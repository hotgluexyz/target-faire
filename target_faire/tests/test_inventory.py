"""Tests for inventory batch helpers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import requests

from target_faire.inventory import (
    peel_sku_from_error,
    sync_inventory_by_skus,
)


def _response(status_code: int, payload: dict) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response._content = json.dumps(payload).encode()
    return response


@pytest.mark.parametrize(
    ("message", "skus", "expected"),
    [
        ("BAD-SKU", ["BAD-SKU", "GOOD-SKU"], "BAD-SKU"),
        ("Skus match multiple product variations: BAD-SKU", ["BAD-SKU"], "BAD-SKU"),
        ("[BAD-SKU]", ["BAD-SKU"], "BAD-SKU"),
        ("unknown", ["BAD-SKU"], None),
    ],
)
def test_peel_sku_from_error(message, skus, expected):
    result = peel_sku_from_error(_response(400, {"message": message}), skus)
    assert result == expected


def test_sync_inventory_by_skus_peels_ambiguous_get_sku():
    sink = MagicMock()
    sink._extract_error_message.side_effect = (
        lambda response: response.json()["message"]
    )

    good_sku = "GOOD-SKU"
    bad_sku = "BAD-SKU"
    get_responses = [
        _response(400, {"message": f"Skus match multiple product variations: {bad_sku}"}),
        _response(
            200,
            {
                "inventories": {
                    good_sku: {"on_hand_quantity": {"quantity": 1}},
                }
            },
        ),
    ]
    patch_response = _response(
        200,
        {
            "inventories": {
                good_sku: {"on_hand_quantity": {"quantity": 5}},
            }
        },
    )

    sink.faire_request.side_effect = [*get_responses, patch_response]

    result = sync_inventory_by_skus(
        sink,
        [
            {"sku": good_sku, "mode": "on_hand", "quantity": 5},
            {"sku": bad_sku, "mode": "on_hand", "quantity": 9},
        ],
    )

    by_sku = {item["record"]["sku"]: item for item in result["items"]}
    assert by_sku[good_sku]["success"] is True
    assert by_sku[bad_sku]["success"] is False
    assert "multiple product variations" in by_sku[bad_sku]["error"]
