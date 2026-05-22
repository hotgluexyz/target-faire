"""Faire target sink base class."""

import requests
from hotglue_etl_exceptions import InvalidCredentialsError, InvalidPayloadError
from hotglue_singer_sdk.exceptions import FatalAPIError, RetriableAPIError
from hotglue_singer_sdk.target_sdk.client import HotglueSink


FAIRE_DEFAULT_API_URL = "https://www.faire.com/external-api/v2"


class FaireSink(HotglueSink):
    @property
    def base_url(self) -> str:
        url = self.config.get("api_url", FAIRE_DEFAULT_API_URL)
        return url.rstrip("/") + "/"

    @property
    def http_headers(self) -> dict:
        return {"X-FAIRE-ACCESS-TOKEN": self.config.get("api_token", "")}

    def _extract_error_message(self, response: requests.Response) -> str:
        try:
            return response.json().get("message", response.text)
        except Exception:
            return response.text

    def validate_response(self, response: requests.Response) -> None:
        if response.status_code == 401:
            raise InvalidCredentialsError("Invalid API token (X-FAIRE-ACCESS-TOKEN)")
        elif response.status_code == 400:
            raise InvalidPayloadError(self._extract_error_message(response))
        elif response.status_code == 429 or 500 <= response.status_code < 600:
            raise RetriableAPIError(self.response_error_message(response), response)
        elif 400 <= response.status_code < 500:
            raise FatalAPIError(self._extract_error_message(response))
