"""Tests standard target features using the built-in SDK tests library."""

import json
import os
import pytest
from typing import Dict, Any

from hotglue_singer_sdk.testing import get_standard_target_tests

from target_faire.target import TargetFaire

SECRETS_FILE = os.path.join(os.path.dirname(__file__), "../../.secrets/config.json")


@pytest.fixture
def config() -> Dict[str, Any]:
    if not os.path.exists(SECRETS_FILE):
        pytest.skip("No .secrets/config.json found")
    with open(SECRETS_FILE) as f:
        return json.load(f)


def test_standard_target_tests(config):
    """Run standard target tests from the SDK."""
    tests = get_standard_target_tests(TargetFaire, config=config)
    for test in tests:
        test()
