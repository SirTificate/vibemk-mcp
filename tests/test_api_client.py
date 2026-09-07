"""
Tests for CheckMK API Client
"""

import urllib.error
from email.message import Message
from unittest.mock import MagicMock, patch

import pytest

from api.client import CheckMKClient
from api.exceptions import CheckMKAPIError, CheckMKAuthenticationError, CheckMKConnectionError, CheckMKError


class TestCheckMKClient:
    """Test CheckMK API Client functionality"""

    def test_client_initialization(self, mock_config):
        """Test client initialization with config"""
        # Skip URL detection for testing
        client = CheckMKClient(mock_config, skip_url_detection=True)

        assert client.config == mock_config
        assert client.api_base_url.startswith("http://test-checkmk.local:8080")
        assert "Authorization" in client.headers
        assert client.headers["Content-Type"] == "application/json"
        assert client.headers["Accept"] == "application/json"

    def test_successful_get_request(self, mock_checkmk_client, mock_checkmk_responses):
        """Test successful GET request"""
        # Setup mock response
        mock_checkmk_client.get.return_value = mock_checkmk_responses["version"]

        result = mock_checkmk_client.get("version")

        assert result["success"] is True
        assert "data" in result
        assert result["data"]["site"] == "test_site"

    def test_successful_post_request(self, mock_checkmk_client, sample_host_data):
        """Test successful POST request"""
        # Setup mock response
        expected_response = {"success": True, "data": {"id": sample_host_data["host_name"]}}
        mock_checkmk_client.post.return_value = expected_response

        result = mock_checkmk_client.post("domain-types/host/collections/all", data=sample_host_data)

        assert result["success"] is True
        assert result["data"]["id"] == sample_host_data["host_name"]

    def test_authentication_error(self, mock_config):
        """Test authentication error handling"""
        with patch("urllib.request.urlopen") as mock_urlopen:
            # Mock 401 authentication error for the actual request
            error = urllib.error.HTTPError(url="test", code=401, msg="Unauthorized", hdrs=Message(), fp=None)
            # Replacing .read() is the point of the test double; strict mode's
            # objection to overwriting a method is intentional here.
            error.read = MagicMock(  # type: ignore[method-assign]
                return_value=b'{"title": "Unauthorized", "detail": "Invalid credentials"}'
            )

            mock_urlopen.side_effect = error

            # Skip URL detection for testing
            client = CheckMKClient(mock_config, skip_url_detection=True)

            with pytest.raises(CheckMKAuthenticationError):
                client.get("version")

    def test_connection_error(self, mock_config):
        """Test connection error handling"""
        with patch("urllib.request.urlopen") as mock_urlopen:
            # Mock connection error for the request
            mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

            # Skip URL detection for testing
            client = CheckMKClient(mock_config, skip_url_detection=True)

            with pytest.raises(CheckMKConnectionError):
                client.get("version")

    def test_retry_mechanism(self, mock_config):
        """Test retry mechanism on temporary failures"""
        mock_config.max_retries = 2

        with patch("urllib.request.urlopen") as mock_urlopen:
            # Create a proper context manager mock for successful response
            mock_success_response = MagicMock()
            mock_success_response.status = 200
            mock_success_response.read.return_value = b'{"success": true, "data": {}}'
            mock_success_response.__enter__.return_value = mock_success_response
            mock_success_response.__exit__.return_value = False

            mock_error = urllib.error.HTTPError("test", 500, "Server Error", Message(), None)

            mock_urlopen.side_effect = [
                mock_error,  # First request fails
                mock_error,  # Retry fails
                mock_success_response,  # Final retry succeeds
            ]

            # Skip URL detection for testing
            client = CheckMKClient(mock_config, skip_url_detection=True)
            result = client.get("version")
            assert result["success"] is True
            assert mock_urlopen.call_count == 3  # 1 original + 2 retries

    def test_error_body_socket_failure_raises_checkmk_error_not_raw_oserror(self, mock_config):
        """error.read() is a live socket read of the HTTP error body, not a
        parse of already-buffered data: urlopen raises HTTPError as soon as
        the status line comes back, and the body is fetched here. A transport
        failure during that read (ConnectionResetError, a OSError subclass)
        must still be turned into a CheckMKError, not escape as a raw OSError
        that no handler's `except CheckMKError` would catch.
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            error = urllib.error.HTTPError(url="test", code=401, msg="Unauthorized", hdrs=Message(), fp=None)
            # Replacing .read() is the point of the test double; strict mode's
            # objection to overwriting a method is intentional here.
            error.read = MagicMock(side_effect=ConnectionResetError("Connection reset by peer"))  # type: ignore[method-assign]

            mock_urlopen.side_effect = error

            client = CheckMKClient(mock_config, skip_url_detection=True)

            with pytest.raises(CheckMKError):
                client.get("version")

    def test_5xx_retry_still_runs_when_error_body_read_fails(self, mock_config):
        """A ConnectionResetError while reading the error body must not skip
        the 5xx retry that sits right below the read -- that transient-error
        retry is precisely the case a reset socket represents.
        """
        mock_config.max_retries = 1

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_success_response = MagicMock()
            mock_success_response.status = 200
            mock_success_response.read.return_value = b'{"success": true, "data": {}}'
            mock_success_response.__enter__.return_value = mock_success_response
            mock_success_response.__exit__.return_value = False

            mock_error = urllib.error.HTTPError(
                url="test", code=503, msg="Service Unavailable", hdrs=Message(), fp=None
            )
            mock_error.read = MagicMock(side_effect=ConnectionResetError("Connection reset by peer"))  # type: ignore[method-assign]

            mock_urlopen.side_effect = [mock_error, mock_success_response]

            client = CheckMKClient(mock_config, skip_url_detection=True)
            result = client.get("version")

            assert result["success"] is True
            assert mock_urlopen.call_count == 2  # 1 original + 1 retry

    def test_url_encoding(self, mock_checkmk_client):
        """Test proper URL encoding for parameters"""
        # Setup mock
        mock_checkmk_client.get.return_value = {"success": True, "data": {}}

        # Test with special characters
        params = {"host_name": "test-server with spaces", "service": "CPU%usage"}
        mock_checkmk_client.get("objects/host/test", params=params)

        # Verify the call was made (parameters would be URL-encoded internally)
        mock_checkmk_client.get.assert_called_with("objects/host/test", params=params)

    def test_json_parsing_error(self, mock_config):
        """Test handling of invalid JSON responses"""
        with patch("urllib.request.urlopen") as mock_urlopen:
            # Mock invalid JSON response
            mock_invalid_response = MagicMock()
            mock_invalid_response.read.return_value = b'{"invalid": json}'
            mock_invalid_response.status = 200
            mock_invalid_response.__enter__.return_value = mock_invalid_response
            mock_invalid_response.__exit__.return_value = None

            mock_urlopen.return_value = mock_invalid_response

            # Skip URL detection for testing
            client = CheckMKClient(mock_config, skip_url_detection=True)

            with pytest.raises(CheckMKAPIError, match="Invalid JSON response"):
                client.get("version")

    def test_timeout_handling(self, mock_config):
        """Test timeout handling"""
        mock_config.timeout = 1  # Very short timeout

        with patch("urllib.request.urlopen") as mock_urlopen:
            # Mock timeout during request
            mock_urlopen.side_effect = TimeoutError("Request timed out")

            # Skip URL detection for testing
            client = CheckMKClient(mock_config, skip_url_detection=True)

            with pytest.raises(CheckMKConnectionError, match="timeout"):
                client.get("version")
