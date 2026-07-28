"""Faire target sink base class."""

from typing import Any, Mapping, Optional, Sequence, Tuple, Union

import requests
from hotglue_etl_exceptions import InvalidCredentialsError, InvalidPayloadError
from hotglue_singer_sdk.exceptions import FatalAPIError, RetriableAPIError
from hotglue_singer_sdk.target_sdk.client import HotglueBatchSink, HotglueSink


FAIRE_DEFAULT_API_URL = "https://www.faire.com/external-api/v2"


class FaireHttpMixin:
    """Shared HTTP configuration and response handling for Faire sinks."""

    @property
    def base_url(self) -> str:
        """Return the configured Faire API base URL."""
        url = self.config.get("api_url", FAIRE_DEFAULT_API_URL)
        return url.rstrip("/") + "/"

    @property
    def http_headers(self) -> dict:
        """Return auth headers for Faire API requests."""
        return {"X-FAIRE-ACCESS-TOKEN": self.config.get("api_key", "")}

    def _extract_error_message(self, response: requests.Response) -> str:
        """Parse a human-readable error message from a Faire API response."""
        try:
            json_data = response.json()
            return f"[{json_data['status_type']}] {json_data.get('message', response.text)}"
        except Exception:
            return response.text

    def validate_response(self, response: requests.Response) -> None:
        """Raise SDK exceptions for non-success Faire API responses."""
        if response.status_code == 401:
            raise InvalidCredentialsError("Invalid API token (X-FAIRE-ACCESS-TOKEN)")
        elif response.status_code == 400:
            raise InvalidPayloadError(self._extract_error_message(response))
        elif response.status_code == 429 or 500 <= response.status_code < 600:
            raise RetriableAPIError(self.response_error_message(response), response)
        elif 400 <= response.status_code < 500:
            raise FatalAPIError(self._extract_error_message(response))

    def faire_request(
        self,
        http_method: str,
        endpoint: str,
        *,
        request_data: Optional[dict] = None,
        params: Optional[Union[Mapping[str, Any], Sequence[Tuple[str, str]]]] = None,
        allowed_statuses: Optional[Sequence[int]] = None,
    ) -> requests.Response:
        """Send a Faire API request with retry and Faire-specific validation."""
        allowed = set(allowed_statuses or ())

        def _do() -> requests.Response:
            response = requests.request(
                http_method,
                self.url(endpoint),
                headers=self.default_headers,
                params=params,
                json=request_data,
            )
            if response.status_code in allowed:
                return response
            self.validate_response(response)
            return response

        return self.request_decorator(_do)()


class FaireSink(FaireHttpMixin, HotglueSink):
    """Base class for per-record Faire target sinks."""


class FaireBatchSink(FaireHttpMixin, HotglueBatchSink):
    """Base class for batched Faire target sinks."""
