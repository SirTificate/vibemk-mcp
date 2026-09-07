"""
Debug handler for CheckMK API analysis
"""

import urllib.parse
from typing import Any, Dict, List, Tuple

from api.exceptions import CheckMKError
from handlers.base import BaseHandler

# Field-name fragments that look like monitoring/state data, used to flag
# promising keys while probing an unfamiliar endpoint's response shape.
_MONITORING_FIELD_WORDS = ["state", "status", "check", "plugin", "last"]


class DebugHandler(BaseHandler):
    """Handle debug operations for CheckMK API analysis"""

    async def handle(self, tool_name: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Handle debug-related tool calls"""

        try:
            if tool_name == "vibemk_debug_api_endpoints":
                response = await self._debug_api_endpoints(arguments)
            elif tool_name == "vibemk_debug_host_data_structure":
                response = await self._debug_host_data_structure(arguments)
            elif tool_name == "vibemk_debug_service_data_structure":
                response = await self._debug_service_data_structure(arguments)
            elif tool_name == "vibemk_debug_permissions":
                response = await self._debug_permissions(arguments)
            elif tool_name == "vibemk_test_all_host_endpoints":
                response = await self._test_all_host_endpoints(arguments)
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

    async def _test_all_host_endpoints(self, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Test all possible host-related endpoints for a specific host"""
        host_name = arguments.get("host_name", "www.google.de")

        # Test various host endpoints
        host_endpoints = [
            f"objects/host/{host_name}",
            f"objects/host_config/{host_name}",
            "domain-types/host/collections/all",
            "domain-types/host_config/collections/all",
            f"objects/host/{host_name}/actions/show_service/invoke",
        ]

        # Test with different query parameters
        query_variations: List[Dict[str, Any]] = [
            {},
            {"host_name": host_name},
            {"q": f"hosts.name = '{host_name}'"},
            {"query": f'{{"op": "=", "left": "name", "right": "{host_name}"}}'},
        ]

        results = []

        for endpoint in host_endpoints:
            results.append(f"\\n🎯 **Testing endpoint: {endpoint}**")

            # Test GET requests
            for i, params in enumerate(query_variations):
                results.extend(self._probe_host_endpoint_query(endpoint, i, params))

        return [{"type": "text", "text": (f"🔍 **Host Endpoints Test for: {host_name}**\\n" + "\\n".join(results))}]

    def _probe_host_endpoint_query(self, endpoint: str, index: int, params: Dict[str, Any]) -> List[str]:
        """Test one endpoint with one query variation, for _test_all_host_endpoints"""
        # Actions endpoints are POSTed to; everything else is a GET. Decided up
        # front (rather than inside the try) so the exception handler always has
        # a real verb to report, even if the request itself blows up.
        method = "POST" if "actions" in endpoint else "GET"

        try:
            if method == "POST":
                result = self.client.post(endpoint, data=params)
            else:
                result = self.client.get(endpoint, params=params)
        except Exception as e:
            return [f"   💥 {method} Query {index + 1}: Exception: {e}"]

        if not result.get("success", False):
            error_data = result.get("data", {})
            return [f"   ❌ {method} Query {index + 1}: {error_data}"]

        data = result.get("data", {})
        if not isinstance(data, dict):
            return [f"   ✅ {method} Query {index + 1}: Data type: {type(data)}"]

        return self._describe_host_endpoint_data(method, index, data)

    def _describe_host_endpoint_data(self, method: str, index: int, data: Dict[str, Any]) -> List[str]:
        """Describe one successful host-endpoint response, for _test_all_host_endpoints"""
        lines = []
        keys = list(data.keys())

        # Look for monitoring data indicators
        monitoring_indicators = []
        if "extensions" in data:
            ext_keys = list(data["extensions"].keys()) if isinstance(data["extensions"], dict) else []
            monitoring_indicators.extend(
                [k for k in ext_keys if any(word in k.lower() for word in _MONITORING_FIELD_WORDS)]
            )

        if "value" in data and isinstance(data["value"], list):
            value_count = len(data["value"])
            if data["value"] and isinstance(data["value"][0], dict):
                sample_keys = list(data["value"][0].keys())
                monitoring_indicators.extend(
                    [k for k in sample_keys if any(word in k.lower() for word in _MONITORING_FIELD_WORDS)]
                )
            lines.append(
                f"   ✅ {method} Query {index + 1}: {value_count} items, Keys: {keys}, Sample: {sample_keys[:5]}"
            )
        else:
            lines.append(f"   ✅ {method} Query {index + 1}: Keys: {keys}")

        if monitoring_indicators:
            lines.append(f"      🎯 Monitoring data found: {monitoring_indicators}")

            # If we found actual monitoring data, show sample
            if "state" in str(data.get("extensions", "")):
                lines.append(f"      📊 Sample data: {str(data)[:300]}...")
            elif data.get("value"):
                sample_item = data["value"][0]
                if "extensions" in sample_item:
                    lines.append(f"      📊 Sample extensions: {str(sample_item['extensions'])[:200]}...")

        return lines

    async def _debug_host_data_structure(self, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Analyze the actual data structure returned by host APIs"""
        host_name = arguments.get("host_name", "www.google.de")

        # Test the most promising endpoints
        test_scenarios: List[Tuple[str, str, str, Dict[str, Any]]] = [
            ("GET all hosts", "domain-types/host_config/collections/all", "GET", {}),
            ("GET all monitoring hosts", "domain-types/host/collections/all", "GET", {}),
            ("GET specific host config", f"objects/host_config/{host_name}", "GET", {}),
            ("GET specific host object", f"objects/host/{host_name}", "GET", {}),
            ("POST show services", f"objects/host/{host_name}/actions/show_service/invoke", "POST", {}),
        ]

        results = []
        for description, endpoint, method, data in test_scenarios:
            results.extend(self._probe_host_data_scenario(description, endpoint, method, data, host_name))

        return [
            {"type": "text", "text": (f"🔬 **Host Data Structure Analysis for: {host_name}**\\n" + "\\n".join(results))}
        ]

    def _probe_host_data_scenario(
        self, description: str, endpoint: str, method: str, data: Dict[str, Any], host_name: str
    ) -> List[str]:
        """Run one test scenario and describe its response, for _debug_host_data_structure"""
        lines = [f"\\n🧪 **{description}**"]

        try:
            result = self.client.post(endpoint, data=data) if method == "POST" else self.client.get(endpoint)
        except Exception as e:
            lines.append(f"   💥 Exception: {e}")
            return lines

        if not result.get("success", False):
            error_data = result.get("data", {})
            lines.append(f"   ❌ Failed: {error_data}")
            return lines

        response_data = result.get("data", {})

        # Deep analysis of the response structure
        lines.append("   ✅ Success!")
        lines.append(f"   📋 Response type: {type(response_data)}")
        lines.extend(self._describe_host_response(response_data, host_name))

        return lines

    def _describe_host_response(self, response_data: Any, host_name: str) -> List[str]:
        """Describe a scenario's response body, for _debug_host_data_structure"""
        if not isinstance(response_data, dict):
            return [f"   🔍 Response: {str(response_data)[:300]}..."]

        lines = [f"   🔑 Top-level keys: {list(response_data.keys())}"]

        # Look for our target host
        if "value" in response_data and isinstance(response_data["value"], list):
            lines.extend(self._describe_host_list(response_data["value"], host_name))
        elif response_data.get("id") == host_name:
            # Direct host object
            lines.append("   🎯 Direct host object found")
            lines.append(f"   🔑 Host keys: {list(response_data.keys())}")

            if "extensions" in response_data:
                ext = response_data["extensions"]
                lines.append(f"   🔧 Extensions: {str(ext)[:400]}...")
        else:
            lines.append(f"   🔍 Response structure: {str(response_data)[:300]}...")

        return lines

    def _describe_host_list(self, items: List[Any], host_name: str) -> List[str]:
        """Search a list response for the target host, for _debug_host_data_structure"""
        lines = [f"   📊 Found {len(items)} items"]

        # Find our specific host
        target_host = None
        for item in items:
            if isinstance(item, dict) and item.get("id") == host_name:
                target_host = item
                break

        if target_host:
            lines.append(f"   🎯 Found target host: {host_name}")
            lines.append(f"   🔑 Host keys: {list(target_host.keys())}")
            lines.extend(self._describe_host_extensions(target_host))
        else:
            lines.append(f"   ❌ Host {host_name} not found in response")
            if items:
                sample_ids = [item.get("id", "no-id") for item in items[:3] if isinstance(item, dict)]
                lines.append(f"   📝 Sample IDs: {sample_ids}")

        return lines

    def _describe_host_extensions(self, target_host: Dict[str, Any]) -> List[str]:
        """Describe a target host's extensions and state fields, for _debug_host_data_structure"""
        if "extensions" not in target_host:
            return ["   ⚠️ No extensions found"]

        ext = target_host["extensions"]
        lines = [f"   🔧 Extensions keys: {list(ext.keys()) if isinstance(ext, dict) else type(ext)}"]

        # Look for state/status information
        state_words = [*_MONITORING_FIELD_WORDS, "output"]
        state_fields = (
            [k for k in ext if any(word in k.lower() for word in state_words)] if isinstance(ext, dict) else []
        )

        if state_fields:
            lines.append(f"   📈 State/monitoring fields: {state_fields}")
            for field in state_fields:
                value = ext.get(field)
                lines.append(f"      {field}: {value} ({type(value)})")
        else:
            lines.append("   ⚠️ No obvious state fields found")
            # Show all extension data for analysis
            lines.append(f"   🔍 All extensions: {str(ext)[:400]}...")

        return lines

    async def _debug_service_data_structure(self, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Analyze service data structure for debugging"""
        host_name = arguments.get("host_name", "www.google.de")
        service_name = arguments.get("service_name", "Check_MK")

        # Test service-related endpoints
        encoded_service = urllib.parse.quote(service_name, safe="")

        test_scenarios: List[Tuple[str, str, str, Dict[str, Any]]] = [
            ("GET all services", "domain-types/service/collections/all", "GET", {}),
            ("GET host services", "domain-types/service/collections/all", "GET", {"host_name": host_name}),
            ("GET specific service", f"objects/service/{host_name}/{encoded_service}", "GET", {}),
            ("POST show host services", f"objects/host/{host_name}/actions/show_service/invoke", "POST", {}),
        ]

        results = []
        for description, endpoint, method, params in test_scenarios:
            results.extend(self._probe_service_data_scenario(description, endpoint, method, params))

        return [{"type": "text", "text": ("🔬 **Service Data Structure Analysis**\\n" + "\\n".join(results))}]

    def _probe_service_data_scenario(
        self, description: str, endpoint: str, method: str, params: Dict[str, Any]
    ) -> List[str]:
        """Run one test scenario and describe its response, for _debug_service_data_structure"""
        lines = [f"\\n🧪 **{description}**"]

        try:
            if method == "POST":
                result = self.client.post(endpoint, data=params)
            else:
                result = self.client.get(endpoint, params=params)
        except Exception as e:
            lines.append(f"   💥 Exception: {e}")
            return lines

        if not result.get("success", False):
            error_data = result.get("data", {})
            lines.append(f"   ❌ Failed: {error_data}")
            return lines

        response_data = result.get("data", {})
        lines.append("   ✅ Success!")

        if isinstance(response_data, dict) and "value" in response_data:
            lines.extend(self._describe_service_list(response_data["value"]))
        else:
            lines.append(f"   🔍 Response: {str(response_data)[:300]}...")

        return lines

    def _describe_service_list(self, services: List[Any]) -> List[str]:
        """Describe the first service in a service collection response"""
        lines = [f"   📊 Found {len(services)} services"]

        if not services:
            return lines

        # Analyze first service
        first_service = services[0]
        if not isinstance(first_service, dict):
            return lines

        lines.append(f"   🔑 Service keys: {list(first_service.keys())}")

        if "extensions" not in first_service:
            return lines

        ext = first_service["extensions"]
        lines.append(f"   🔧 Extensions keys: {list(ext.keys()) if isinstance(ext, dict) else type(ext)}")

        # Look for state information
        if isinstance(ext, dict):
            state_info = {}
            for key in ["state", "description", "host_name", "plugin_output", "last_check"]:
                if key in ext:
                    state_info[key] = ext[key]
            lines.append(f"   📈 State info: {state_info}")

        return lines

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
