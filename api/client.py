"""
CheckMK API Client with automatic URL detection and robust error handling

Copyright (C) 2024 Andre <andre@example.com>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.
"""

import base64
import http
import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from http.client import HTTPMessage
from typing import IO, Any, Dict, List, Optional

from api.exceptions import (
    CheckMKAPIError,
    CheckMKAuthenticationError,
    CheckMKConnectionError,
    CheckMKError,
    CheckMKNotFoundError,
    CheckMKPermissionError,
)
from config import CheckMKConfig, MCPConfig

logger = logging.getLogger(__name__)

# HTTP status codes that are worth retrying: transient server-side failures.
_RETRYABLE_STATUS_CODES = (500, 502, 503, 504)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects during URL detection.

    A bare `/cmk/` path on a CheckMK behind SSO/Apache answers `/version` with a
    302 to a login page. urllib follows redirects by default, so that 302 used to
    be accepted as a working API root — and every authenticated call afterwards
    hit the login HTML and failed with "Invalid JSON response". Returning None
    here lets the 3xx surface as an HTTPError so the pattern is rejected.
    """

    def redirect_request(
        self,
        _req: urllib.request.Request,
        _fp: IO[bytes],
        _code: int,
        _msg: str,
        _headers: HTTPMessage,
        _newurl: str,
    ) -> Optional[urllib.request.Request]:
        return None


@dataclass(frozen=True)
class _RequestOptions:
    """Bundles the optional knobs `CheckMKClient.request` accepts.

    Grouping these keeps `request` and `_handle_http_error` (which re-issues a
    request with an incremented retry_count via dataclasses.replace) down to a
    handful of parameters each.
    """

    data: Optional[Dict[str, Any]] = None
    params: Optional[Dict[str, Any]] = None
    custom_headers: Optional[Dict[str, str]] = None
    retry_count: int = 0
    use_api_prefix: bool = True


class CheckMKClient:
    """CheckMK REST API client with automatic URL detection"""

    def __init__(self, config: CheckMKConfig, skip_url_detection: bool = False) -> None:
        self.config = config
        self._setup_headers()
        self._ssl_context = self._create_ssl_context()

        if skip_url_detection:
            # For testing - use first pattern without detection
            self.api_base_url = f"{self.config.server_url}/cmk/check_mk/api/1.0"
        else:
            self.api_base_url = self._detect_api_url()

    def _setup_headers(self) -> None:
        """Setup HTTP headers for authentication"""
        credentials = f"{self.config.username}:{self.config.password}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        self.headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"vibeMK/{MCPConfig().version}",
        }

    def _create_ssl_context(self) -> Optional[ssl.SSLContext]:
        """Create SSL context based on configuration"""
        if not self.config.verify_ssl:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            return ssl_context
        return None

    def _detect_api_url(self) -> str:
        """Detect correct CheckMK API URL by testing different patterns"""
        debug_results = []

        # The site-specific path (/<site>/check_mk/api/1.0) is the canonical
        # CheckMK URL scheme — test it first. The bare /cmk/ pattern is gone: on
        # this and most installs it 302-redirects to login and only caused false
        # positives.
        test_patterns = [
            f"{self.config.server_url}/{self.config.site}/check_mk/api/1.0",
            f"{self.config.server_url}/check_mk/api/1.0",
            f"{self.config.server_url}/api/1.0",
            f"{self.config.server_url}/{self.config.site}/cmk/check_mk/api/1.0",
        ]

        # Don't follow redirects (so a 3xx counts as failure) and cap detection
        # time so a slow/unreachable server can't stall startup for minutes.
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self._ssl_context),
            _NoRedirectHandler,
        )
        detect_timeout = min(self.config.timeout, 10)

        for base_url in test_patterns:
            try:
                test_url = f"{base_url}/version"
                debug_results.append(f"Testing: {test_url}")

                req = urllib.request.Request(test_url, headers=self.headers)
                with opener.open(req, timeout=detect_timeout) as response:
                    if response.status == http.HTTPStatus.OK:
                        debug_results.append(f"SUCCESS: {base_url}")
                        self._debug_results = debug_results
                        logger.info("Detected API URL: %s", base_url)
                        return base_url
                    debug_results.append(f"HTTP {response.status}: {base_url}")

            except urllib.error.HTTPError as e:
                debug_results.append(f"HTTP {e.code} ({e.reason}): {base_url}")
            except Exception as e:
                debug_results.append(f"ERROR ({e}): {base_url}")

        # Store debug results for troubleshooting
        self._debug_results = debug_results
        fallback_url = f"{self.config.server_url}/{self.config.site}/check_mk/api/1.0"
        debug_results.append(f"FALLBACK: {fallback_url}")
        logger.warning("Using fallback API URL: %s", fallback_url)
        return fallback_url

    def get_debug_results(self) -> List[str]:
        """Get URL detection debug results"""
        return getattr(self, "_debug_results", ["No debug info available"])

    def _build_url(self, endpoint: str, use_api_prefix: bool) -> str:
        """Build the request URL for an endpoint."""
        if use_api_prefix:
            return f"{self.api_base_url}/{endpoint}"
        # For CheckMK View API and other non-REST endpoints
        return f"{self.config.server_url}/{self.config.site}/{endpoint}"

    def _encode_query_params(self, params: Optional[Dict[str, Any]]) -> str:
        """Encode request parameters, honoring CheckMK's repeated-key and JSON conventions."""
        if not params:
            return ""

        url_params: List[str] = []
        for key, value in params.items():
            if key == "columns" and isinstance(value, list):
                # Multiple columns parameters: columns=col1&columns=col2
                url_params.extend(f"columns={urllib.parse.quote(str(col))}" for col in value)
            elif key == "query" and isinstance(value, dict):
                # JSON query parameter: query={"op": "=", ...}
                query_json = json.dumps(value)
                url_params.append(f"query={urllib.parse.quote(query_json)}")
            else:
                # Standard parameter encoding
                url_params.append(f"{key}={urllib.parse.quote(str(value))}")

        return "&".join(url_params)

    def request(
        self,
        endpoint: str,
        method: str = "GET",
        options: Optional[_RequestOptions] = None,
    ) -> Dict[str, Any]:
        """Make HTTP request to CheckMK API with retry logic"""
        opts = options or _RequestOptions()

        url = self._build_url(endpoint, opts.use_api_prefix)
        query_string = self._encode_query_params(opts.params)
        if query_string:
            url += f"?{query_string}"

        try:
            # Merge default headers with custom headers
            request_headers = self.headers.copy()
            if opts.custom_headers:
                request_headers.update(opts.custom_headers)

            payload = json.dumps(opts.data).encode() if method in ("POST", "PUT", "PATCH") and opts.data else None
            req = urllib.request.Request(url, data=payload, headers=request_headers, method=method)

            logger.debug("%s %s", method, url)

            with urllib.request.urlopen(req, context=self._ssl_context, timeout=self.config.timeout) as response:
                response_data = response.read().decode()

                try:
                    parsed_data = json.loads(response_data) if response_data else {}
                except json.JSONDecodeError as e:
                    msg = f"Invalid JSON response: {e}"
                    raise CheckMKAPIError(msg, response.status, {"raw": response_data}) from e

                result = {
                    "status": response.status,
                    "data": parsed_data,
                    "success": True,
                    "raw_content": response_data,  # Keep raw content for view API parsing
                    "headers": dict(response.headers),  # Include response headers for ETag support
                }

                logger.debug("Response: %s", response.status)
                return result

        except urllib.error.HTTPError as e:
            return self._handle_http_error(e, endpoint, method, opts)
        except (CheckMKAPIError, CheckMKConnectionError, CheckMKError):
            # Don't catch and re-wrap our own exceptions
            raise
        except Exception as e:
            return self._handle_general_error(e)

    def _handle_http_error(
        self,
        error: urllib.error.HTTPError,
        endpoint: str,
        method: str,
        options: _RequestOptions,
    ) -> Dict[str, Any]:
        """Handle HTTP errors with appropriate exceptions and retries"""

        # Reading an HTTPError's body is best effort. It is a lazy socket read, so
        # it can fail transport-wise; the payload need not be JSON; and when urllib
        # built the error without a file object the stdlib raises a different type
        # per Python version -- KeyError on 3.9, nothing at all on 3.13. Two
        # attempts to enumerate the types were both wrong, so this catches broadly
        # on purpose: a failure here must never preempt the error handling below,
        # including the retry for transient status codes.
        try:
            error_data = json.loads(error.read().decode())
        except Exception:  # deliberate, see above
            error_data = {"error": error.reason}

        # Retry logic for transient errors (500, 502, 503, 504)
        if error.code in _RETRYABLE_STATUS_CODES and options.retry_count < self.config.max_retries:
            time.sleep(2**options.retry_count)  # Exponential backoff
            return self.request(endpoint, method, replace(options, retry_count=options.retry_count + 1))

        # Map HTTP status codes to custom exceptions
        if error.code == http.HTTPStatus.UNAUTHORIZED:
            msg = f"Authentication failed: {error.reason}"
            raise CheckMKAuthenticationError(msg, error.code, error_data)
        if error.code == http.HTTPStatus.FORBIDDEN:
            msg = f"Permission denied: {error.reason}"
            raise CheckMKPermissionError(msg, error.code, error_data)
        if error.code == http.HTTPStatus.NOT_FOUND:
            msg = f"Resource not found: {error.reason}"
            raise CheckMKNotFoundError(msg, error.code, error_data)
        msg = f"HTTP {error.code}: {error.reason}"
        raise CheckMKAPIError(msg, error.code, error_data)

    def _handle_general_error(self, error: Exception) -> Dict[str, Any]:
        """Handle general connection errors"""

        # Handle timeout errors specifically - don't retry timeouts
        if isinstance(error, (TimeoutError, OSError)) and "timeout" in str(error).lower():
            msg = f"Request timeout: {error}"
            raise CheckMKConnectionError(msg)

        # Handle TimeoutError specifically (might not contain "timeout" in message)
        if isinstance(error, TimeoutError):
            msg = f"Request timeout: {error}"
            raise CheckMKConnectionError(msg)

        # Handle URL errors (connection refused, DNS issues, etc.)
        if isinstance(error, urllib.error.URLError):
            msg = f"Connection error: {error}"
            raise CheckMKConnectionError(msg)

        # Don't retry StopIteration errors (from test mocks)
        if isinstance(error, StopIteration):
            msg = "Mock iteration exhausted"
            raise CheckMKConnectionError(msg)

        # Do NOT retry other (e.g. read) errors here: the request already
        # connected, and re-issuing it with a full timeout each time turned a
        # transient read hiccup into a multi-minute hang. Transient server-side
        # 5xx are still retried in _handle_http_error.
        msg = f"Connection failed: {error}"
        raise CheckMKConnectionError(msg)

    # Convenience methods
    def get(
        self, endpoint: str, params: Optional[Dict[str, Any]] = None, use_api_prefix: bool = True
    ) -> Dict[str, Any]:
        """GET request with optional non-API endpoints"""
        return self.request(endpoint, "GET", _RequestOptions(params=params, use_api_prefix=use_api_prefix))

    def post(
        self,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """POST request with optional custom headers"""
        return self.request(endpoint, "POST", _RequestOptions(data=data, custom_headers=headers))

    def put(
        self, endpoint: str, data: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """PUT request with optional custom headers"""
        return self.request(endpoint, "PUT", _RequestOptions(data=data, custom_headers=headers))

    def delete(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """DELETE request with optional parameters and custom headers"""
        return self.request(endpoint, "DELETE", _RequestOptions(params=params, custom_headers=headers))

    def patch(self, endpoint: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """PATCH request"""
        return self.request(endpoint, "PATCH", _RequestOptions(data=data))
