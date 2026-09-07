"""
Connection and diagnostics handlers
"""

import json
import urllib.error
import urllib.request
from http.client import IncompleteRead
from typing import Any, Dict, List, Optional

from api.exceptions import CheckMKError
from handlers.base import BaseHandler


class ConnectionHandler(BaseHandler):
    """Handle connection and diagnostic operations"""

    async def handle(self, tool_name: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Handle connection-related tool calls"""

        try:
            if tool_name == "vibemk_debug_checkmk_connection":
                response = await self._debug_connection()
            elif tool_name == "vibemk_debug_url_detection":
                response = await self._debug_url_detection()
            elif tool_name == "vibemk_test_direct_url":
                response = await self._test_direct_url(arguments.get("test_url"))
            elif tool_name == "vibemk_test_all_endpoints":
                response = await self._test_all_endpoints()
            elif tool_name == "vibemk_get_checkmk_version":
                response = await self._get_version()
            else:
                response = self.error_response("Unknown tool", f"Tool '{tool_name}' is not supported")

        except CheckMKError as e:
            return self.error_response("CheckMK API Error", str(e))
        except Exception as e:
            self.logger.exception("Error in %s", tool_name)
            return self.error_response("Unexpected Error", str(e))
        else:
            return response

    async def _debug_connection(self) -> List[Dict[str, Any]]:
        """Debug CheckMK connection"""
        try:
            result = self.client.get("version")
        except Exception as e:
            return self.error_response("Connection Failed", f"Error: {e}")
        else:
            if result.get("success"):
                data = result["data"]
                return [
                    {
                        "type": "text",
                        "text": (
                            f"✅ **CheckMK Connection Successful**\n\n"
                            f"🌐 Server: {self.client.config.server_url}\n"
                            f"🏢 Site: {self.client.config.site}\n"
                            f"👤 User: {self.client.config.username}\n"
                            f"🔒 SSL Verify: {self.client.config.verify_ssl}\n"
                            f"🔗 API Base URL: {self.client.api_base_url}\n"
                            f"📊 Version: {data.get('versions', {}).get('checkmk', 'Unknown')}\n"
                            f"📦 Edition: {data.get('edition', 'Unknown')}"
                        ),
                    }
                ]

            return self.error_response("Connection Failed", f"API Base URL: {self.client.api_base_url}")

    async def _debug_url_detection(self) -> List[Dict[str, Any]]:
        """Show URL detection debug information"""
        debug_info = self.client.get_debug_results()

        return [
            {
                "type": "text",
                "text": (
                    f"🔍 **URL Detection Debug Results**\n\n"
                    f"🌐 Server URL: {self.client.config.server_url}\n"
                    f"🏢 Site: {self.client.config.site}\n"
                    f"🔗 Selected API URL: {self.client.api_base_url}\n\n"
                    f"**Test Results:**\n" + "\n".join(debug_info)
                ),
            }
        ]

    async def _test_direct_url(self, test_url: Optional[str]) -> List[Dict[str, Any]]:
        """Test a specific URL directly"""
        if not test_url:
            return self.error_response("Missing URL", "test_url parameter is required")

        try:
            # Reaches into the client's SSL context so this raw urlopen() call gets the
            # exact same TLS behaviour (verify_ssl, custom CA, ...) as requests made
            # through the client's own request path.
            req = urllib.request.Request(test_url, headers=self.client.headers)
            with urllib.request.urlopen(
                req, context=self.client._ssl_context, timeout=self.client.config.timeout  # noqa: SLF001
            ) as response:
                response_data = response.read().decode()

                try:
                    parsed_data = json.loads(response_data) if response_data else {}
                except json.JSONDecodeError:
                    parsed_data = {"raw": response_data}

                return [
                    {
                        "type": "text",
                        "text": (
                            f"✅ **Direct URL Test Successful**\n\n"
                            f"URL: {test_url}\n"
                            f"Status: {response.status}\n"
                            f"Response: {json.dumps(parsed_data, indent=2)}"
                        ),
                    }
                ]

        except urllib.error.HTTPError as e:
            try:
                # e.read() is a live socket read (HTTPError only raises on the
                # status line; the body is fetched here), so this must also
                # cover transport failures, not just a malformed/missing body.
                error_data = json.loads(e.read().decode())
            except (ValueError, AttributeError, OSError, IncompleteRead):
                # ValueError covers a non-JSON error body (json.JSONDecodeError is a
                # subclass); AttributeError covers HTTPError.read() when the response
                # has no body to read (e.fp is None), which real error responses hit.
                # OSError/IncompleteRead cover a socket failure while reading the
                # body (ConnectionResetError, TimeoutError, ssl.SSLError are all
                # OSError subclasses; IncompleteRead is not).
                error_data = {"error": e.reason}

            return [
                {
                    "type": "text",
                    "text": (
                        f"❌ **HTTP Error {e.code}**\n\n"
                        f"URL: {test_url}\n"
                        f"Error: {e.reason}\n"
                        f"Response: {json.dumps(error_data, indent=2)}"
                    ),
                }
            ]

        except Exception as e:
            return [
                {
                    "type": "text",
                    "text": (f"❌ **Request Failed**\n\nURL: {test_url}\nError: {e}"),
                }
            ]

    async def _test_all_endpoints(self) -> List[Dict[str, Any]]:
        """Test all major API endpoints"""
        endpoints = [
            ("version", "Version info"),
            ("domain-types/host_config/collections/all", "Host Configs"),
            ("domain-types/service/collections/all", "Services"),
            ("domain-types/folder_config/collections/all", "Folders"),
            ("domain-types/downtime/collections/all", "Downtimes"),
            ("domain-types/acknowledge/collections/all", "Acknowledgments"),
            ("domain-types/activation_run/collections/all", "Activations"),
            ("domain-types/user_config/collections/all", "Users"),
            ("domain-types/host_group_config/collections/all", "Host Groups"),
            ("domain-types/service_group_config/collections/all", "Service Groups"),
        ]

        results = []
        for endpoint, desc in endpoints:
            try:
                result = self.client.get(endpoint)
                status = "✅" if result.get("success") else "❌"
                results.append(f"{status} {endpoint} - {desc} (HTTP {result.get('status', 'unknown')})")
            except Exception as e:
                results.append(f"❌ {endpoint} - {desc} (Error: {e})")

        return [{"type": "text", "text": "🧪 **API Endpoint Test Results**\n\n" + "\n".join(results)}]

    async def _get_version(self) -> List[Dict[str, Any]]:
        """Get CheckMK version information"""
        result = self.client.get("version")

        if result.get("success"):
            data = result["data"]
            return [
                {
                    "type": "text",
                    "text": (
                        f"📋 **CheckMK Version Information**\n\n"
                        f"Version: {data.get('versions', {}).get('checkmk', 'Unknown')}\n"
                        f"Edition: {data.get('edition', 'Unknown')}\n"
                        f"Site: {self.client.config.site}\n"
                        f"Server: {self.client.config.server_url}"
                    ),
                }
            ]

        return self.error_response("Version Error", "Could not retrieve version information")
