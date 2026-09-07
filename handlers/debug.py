"""
Debug handler for CheckMK API analysis
"""

from typing import Any, Dict, List

from api.exceptions import CheckMKError
from handlers.base import BaseHandler


class DebugHandler(BaseHandler):
    """Handle debug operations for CheckMK API analysis"""

    async def handle(self, tool_name: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Handle debug-related tool calls"""

        try:
            if tool_name == "vibemk_debug_api_endpoints":
                response = await self._debug_api_endpoints(arguments)
            elif tool_name == "vibemk_debug_permissions":
                response = await self._debug_permissions(arguments)
            else:
                response = self.error_response("Unknown tool", f"Tool '{tool_name}' is not supported")

        except CheckMKError as e:
            return self.error_response("CheckMK API Error", str(e))
        except Exception as e:
            self.logger.exception("Error in %s", tool_name)
            return self.error_response("Unexpected Error", str(e))
        else:
            return response

    async def _debug_api_endpoints(self, _arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Debug available API endpoints and their structure"""

        # Test various API endpoints to understand structure
        endpoints_to_test = [
            "domain-types",
            "domain-types/host",
            "domain-types/host/collections/all",
            "domain-types/service",
            "domain-types/service/collections/all",
            "objects",
        ]

        results = [self._probe_endpoint_structure(endpoint) for endpoint in endpoints_to_test]

        return [{"type": "text", "text": ("🔍 **CheckMK API Endpoints Debug**\\n\\n" + "\\n\\n".join(results))}]

    def _probe_endpoint_structure(self, endpoint: str) -> str:
        """Fetch one endpoint and describe its response shape, for _debug_api_endpoints"""
        try:
            result = self.client.get(endpoint)
        except Exception as e:
            return f"💥 **{endpoint}**\\n   Exception: {e}"

        if not result.get("success", False):
            error_info = result.get("data", {})
            return f"❌ **{endpoint}**\\n   Error: {error_info}"

        data = result.get("data", {})
        if not isinstance(data, dict):
            return f"✅ **{endpoint}**\\n   Data type: {type(data)}\\n   Content: {str(data)[:200]}..."

        keys = list(data.keys())
        first_items: List[Any] = []

        # Get sample data
        if "value" in data and isinstance(data["value"], list) and data["value"]:
            first_items = data["value"][:2]  # First 2 items
        elif "domain_type" in data and isinstance(data["domain_type"], list) and data["domain_type"]:
            first_items = data["domain_type"][:2]

        return f"✅ **{endpoint}**\\n   Keys: {keys}\\n   Sample: {str(first_items)[:200]}..."

    async def _debug_permissions(self, _arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Debug automation user permissions"""

        results = []

        # Test basic API access
        basic_tests = [
            ("API Version", "version"),
            ("Domain Types", "domain-types"),
            ("User Info", "objects/user_config"),
        ]

        for description, endpoint in basic_tests:
            results.append(f"\\n🧪 **{description}**")
            try:
                result = self.client.get(endpoint)
                success = result.get("success", False)

                if success:
                    results.append("   ✅ Access granted")
                else:
                    error_data = result.get("data", {})
                    results.append(f"   ❌ Access denied: {error_data}")

            except Exception as e:
                results.append(f"   💥 Exception: {e}")

        # Test monitoring-specific permissions
        monitoring_tests = [
            ("Host Monitoring", "domain-types/host/collections/all"),
            ("Service Monitoring", "domain-types/service/collections/all"),
            ("Host Config", "domain-types/host_config/collections/all"),
        ]

        for description, endpoint in monitoring_tests:
            results.append(f"\\n🔒 **{description} Permissions**")
            try:
                result = self.client.get(endpoint)
                success = result.get("success", False)

                if success:
                    data = result.get("data", {})
                    if "value" in data:
                        count = len(data["value"]) if isinstance(data["value"], list) else "unknown"
                        results.append(f"   ✅ Access granted - {count} items")
                    else:
                        structure = list(data.keys()) if isinstance(data, dict) else type(data)
                        results.append(f"   ✅ Access granted - structure: {structure}")
                else:
                    error_data = result.get("data", {})
                    error_title = (
                        error_data.get("title", "Unknown error") if isinstance(error_data, dict) else str(error_data)
                    )
                    results.append(f"   ❌ Access issue: {error_title}")

                    # Check for permission-related errors
                    if any(
                        word in str(error_data).lower()
                        for word in ["permission", "forbidden", "unauthorized", "access"]
                    ):
                        results.append("   🚫 Likely permission issue detected")

            except Exception as e:
                results.append(f"   💥 Exception: {e}")

        return [{"type": "text", "text": ("🔐 **Permissions Debug**\\n" + "\\n".join(results))}]
