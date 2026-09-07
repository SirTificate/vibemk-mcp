"""
Tests for transport-level behaviour of the CheckMK client.

Covers the scheme chosen for a bare host name and the ability to send
per-request headers, which CheckMK's optimistic locking (If-Match) needs.
"""

from unittest.mock import MagicMock, patch

import pytest

from api.client import CheckMKClient
from config import CheckMKConfig


def make_config(**overrides) -> CheckMKConfig:
    values = {
        "server_url": "https://checkmk.example.com",
        "site": "test",
        "username": "automation",
        "password": "secret",
        "verify_ssl": True,
    }
    values.update(overrides)
    return CheckMKConfig(**values)


class TestUrlScheme:
    """Credentials travel in a Basic auth header, so the default must be TLS."""

    def test_bare_hostname_defaults_to_https(self):
        config = make_config(server_url="checkmk.example.com")

        assert config.server_url == "https://checkmk.example.com"

    def test_bare_hostname_with_port_defaults_to_https(self):
        config = make_config(server_url="checkmk.example.com:8080")

        assert config.server_url == "https://checkmk.example.com:8080"

    def test_explicit_http_is_preserved(self):
        config = make_config(server_url="http://localhost:8080")

        assert config.server_url == "http://localhost:8080"

    def test_explicit_https_is_preserved(self):
        config = make_config(server_url="https://checkmk.example.com")

        assert config.server_url == "https://checkmk.example.com"

    def test_trailing_slash_is_stripped(self):
        config = make_config(server_url="checkmk.example.com/")

        assert config.server_url == "https://checkmk.example.com"


class TestPerRequestHeaders:
    """CheckMK requires If-Match on many mutating endpoints."""

    @pytest.fixture
    def client(self):
        return CheckMKClient(make_config(), skip_url_detection=True)

    def sent_headers(self, mock_urlopen) -> dict:
        request = mock_urlopen.call_args[0][0]
        # urllib capitalises header names when they are added to a Request.
        return {key.lower(): value for key, value in request.header_items()}

    @patch("urllib.request.urlopen")
    def test_delete_forwards_custom_headers(self, mock_urlopen, client):
        mock_urlopen.return_value.__enter__.return_value = MagicMock(status=204, headers={}, read=lambda: b"{}")

        client.delete("objects/folder_config/test", headers={"If-Match": '"abc123"'})

        assert self.sent_headers(mock_urlopen)["if-match"] == '"abc123"'

    @patch("urllib.request.urlopen")
    def test_post_forwards_custom_headers(self, mock_urlopen, client):
        mock_urlopen.return_value.__enter__.return_value = MagicMock(status=200, headers={}, read=lambda: b"{}")

        client.post("objects/host_config/x/actions/move/invoke", data={}, headers={"If-Match": '"etag"'})

        assert self.sent_headers(mock_urlopen)["if-match"] == '"etag"'

    @patch("urllib.request.urlopen")
    def test_delete_still_works_without_headers(self, mock_urlopen, client):
        mock_urlopen.return_value.__enter__.return_value = MagicMock(status=204, headers={}, read=lambda: b"{}")

        result = client.delete("objects/host_config/test")

        assert result["success"] is True
        assert "if-match" not in self.sent_headers(mock_urlopen)


class TestUserAgent:
    def test_user_agent_reports_the_server_version(self):
        from config import MCPConfig

        client = CheckMKClient(make_config(), skip_url_detection=True)

        assert client.headers["User-Agent"] == f"vibeMK/{MCPConfig().version}"


class TestVersionConsistency:
    def test_advertised_version_matches_package_metadata(self):
        import pathlib
        import re

        from config import MCPConfig

        pyproject = (pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
        declared = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE).group(1)

        assert MCPConfig().version == declared
