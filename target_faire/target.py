"""Faire target class."""

from hotglue_singer_sdk import typing as th
from hotglue_singer_sdk.helpers.capabilities import AlertingLevel
from hotglue_singer_sdk.target_sdk.target import TargetHotglue

from target_faire.client import FAIRE_DEFAULT_API_URL
from target_faire.sinks import FulfillmentsSink, ProductVariantsSink, ProductsSink


class TargetFaire(TargetHotglue):
    """Singer target for Faire."""

    SINK_TYPES = [FulfillmentsSink, ProductsSink, ProductVariantsSink]
    name = "target-faire"
    alerting_level = AlertingLevel.ERROR

    config_jsonschema = th.PropertiesList(
        th.Property("api_key", th.StringType, required=True),
        th.Property(
            "api_url",
            th.StringType,
            required=False,
            default=FAIRE_DEFAULT_API_URL,
        ),
        th.Property(
            "default_taxonomy_type_id",
            th.StringType,
            required=False,
            description="Optional fallback Faire taxonomy type id (tt_...) when a Products "
            "record has no category.id or taxonomy_type.id",
        ),
    ).to_dict()


if __name__ == "__main__":
    TargetFaire.cli()
