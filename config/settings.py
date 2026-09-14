"""
Configuration management for vibeMK

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

import os
from dataclasses import dataclass
from typing import Optional, Tuple

from config.version import __version__


def _safe_bool(value: str, default: bool) -> bool:
    """Parse an environment variable as a bool, falling back to `default`.

    Falls back on empty and on any value that isn't a recognized spelling,
    rather than treating an unrecognized value as False.
    """
    if not value:
        return default
    lower_value = value.lower()
    if lower_value in ("true", "1", "yes", "on"):
        return True
    if lower_value in ("false", "0", "no", "off"):
        return False
    return default  # Return default for invalid values


def _safe_int(value: str, default: int) -> int:
    """Parse an environment variable as an int, falling back to `default`."""
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass(repr=False)
class CheckMKConfig:
    """CheckMK server configuration"""

    server_url: str
    site: str
    username: str
    password: str
    verify_ssl: bool = True
    timeout: int = 30
    max_retries: int = 3
    debug: bool = False

    def __post_init__(self) -> None:
        """Post-initialization validation and normalization"""
        # Validate required fields
        if not self.server_url:
            msg = "CHECKMK_SERVER_URL is required"
            raise ValueError(msg)
        if not self.username:
            msg = "CHECKMK_USERNAME is required"
            raise ValueError(msg)
        if not self.password:
            msg = "CHECKMK_PASSWORD is required"
            raise ValueError(msg)
        if not self.site:
            msg = "CHECKMK_SITE is required"
            raise ValueError(msg)

        # Validate numeric fields
        if self.timeout <= 0:
            msg = "timeout must be positive"
            raise ValueError(msg)
        if self.max_retries < 0:
            msg = "max_retries must be non-negative"
            raise ValueError(msg)

        # Normalize URL
        self.server_url = self._normalize_url(self.server_url)

    def _normalize_url(self, url: str) -> str:
        """Normalize server URL"""
        if not url:
            return url

        # Assume TLS when no scheme is given: credentials travel in a Basic
        # auth header, so defaulting to plain HTTP would put them on the wire
        # in the clear. An explicit http:// is still honoured.
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        # Remove trailing slash for consistency
        if url.endswith("/") and len(url) > 1:
            url = url.rstrip("/")

        return url

    def __repr__(self) -> str:
        """String representation with masked password"""
        return (
            f"CheckMKConfig("
            f"server_url='{self.server_url}', "
            f"site='{self.site}', "
            f"username='{self.username}', "
            f"password='***', "
            f"verify_ssl={self.verify_ssl}, "
            f"timeout={self.timeout}, "
            f"max_retries={self.max_retries}, "
            f"debug={self.debug})"
        )

    @classmethod
    def from_env(cls) -> "CheckMKConfig":
        """Load configuration from environment variables"""
        # Handle required fields
        server_url = os.environ.get("CHECKMK_SERVER_URL")
        if not server_url:
            msg = "CHECKMK_SERVER_URL is required"
            raise ValueError(msg)

        site = os.environ.get("CHECKMK_SITE")
        if not site:
            msg = "CHECKMK_SITE is required"
            raise ValueError(msg)

        username = os.environ.get("CHECKMK_USERNAME")
        if not username:
            msg = "CHECKMK_USERNAME is required"
            raise ValueError(msg)

        password = os.environ.get("CHECKMK_PASSWORD")
        if not password:
            msg = "CHECKMK_PASSWORD is required"
            raise ValueError(msg)

        return cls(
            server_url=server_url,
            site=site,
            username=username,
            password=password,
            verify_ssl=_safe_bool(os.environ.get("CHECKMK_VERIFY_SSL", ""), True),  # Default True for security
            timeout=_safe_int(os.environ.get("CHECKMK_TIMEOUT", ""), 30),
            max_retries=_safe_int(os.environ.get("CHECKMK_MAX_RETRIES", ""), 3),
            debug=_safe_bool(os.environ.get("CHECKMK_DEBUG", ""), False),
        )

    def validate(self) -> None:
        """Validate configuration (called automatically in __post_init__)"""
        # Validation now happens in __post_init__


@dataclass
class MCPConfig:
    """MCP server configuration"""

    name: str = "vibemk"
    version: str = __version__
    protocol_version: str = "2024-11-05"  # Keep stable version for now
    supported_protocol_versions: Tuple[str, ...] = ("2024-11-05",)

    def __post_init__(self) -> None:
        """Post-initialization validation"""
        if not self.name or self.name.strip() == "":
            msg = "name cannot be empty"
            raise ValueError(msg)
        if not self.version or self.version.strip() == "":
            msg = "version cannot be empty"
            raise ValueError(msg)

    def negotiate_protocol_version(self, requested: Optional[str]) -> str:
        """Return a protocol version this server actually speaks.

        The MCP specification requires the server to answer initialize with a
        version it supports. Echoing the client's string instead claims support
        for anything a client cares to name.
        """
        if requested in self.supported_protocol_versions:
            return requested
        return self.protocol_version

    @property
    def server_name(self) -> str:
        """Alias for name for backward compatibility"""
        return self.name

    @property
    def server_version(self) -> str:
        """Alias for version for backward compatibility"""
        return self.version
