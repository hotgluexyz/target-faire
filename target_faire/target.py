"""Faire target class."""

from hotglue_singer_sdk import typing as th
from hotglue_singer_sdk.helpers.capabilities import AlertingLevel
from hotglue_singer_sdk.target_sdk.target import TargetHotglue

from target_faire.client import FAIRE_DEFAULT_API_URL
from target_faire.sinks import FulfillmentsSink


class TargetFaire(TargetHotglue):
    """Singer target for Faire."""

    SINK_TYPES = [FulfillmentsSink]
    name = "target-faire"
    alerting_level = AlertingLevel.ERROR

    config_jsonschema = th.PropertiesList(
        th.Property("api_token", th.StringType, required=True),
        th.Property(
            "api_url",
            th.StringType,
            required=False,
            default=FAIRE_DEFAULT_API_URL,
        ),
    ).to_dict()


if __name__ == "__main__":
    TargetFaire.cli()
