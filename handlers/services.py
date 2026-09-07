"""
Service management handlers
"""

from typing import Any, Dict, List

from api.exceptions import CheckMKError
from handlers.base import BaseHandler


class ServiceHandler(BaseHandler):
    """Handle service management operations"""

    async def handle(self, tool_name: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Handle service-related tool calls"""

        try:
            if tool_name == "vibemk_get_checkmk_services":
                return await self._get_services(arguments)
            elif tool_name == "vibemk_get_service_status":
                return await self._get_service_status(arguments)
            else:
                return self.error_response("Unknown tool", f"Tool '{tool_name}' is not supported")

        except CheckMKError as e:
            return self.error_response("CheckMK API Error", str(e))
        except Exception as e:
            self.logger.exception(f"Error in {tool_name}")
            return self.error_response("Unexpected Error", str(e))

    async def _get_services(self, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get list of services with optional host filtering - IMPROVED VERSION"""
        host_name = arguments.get("host_name")

        # Method 1: If specific host is requested, use the show_service action (best method)
        if host_name:
            try:
                result = self.client.post(f"objects/host/{host_name}/actions/show_service/invoke", data={})
                self.logger.debug(f"Host services API result: {result}")

                if result.get("success"):
                    services_data = result.get("data", {})

                    # Handle different response structures
                    if isinstance(services_data, dict):
                        services = services_data.get("value", [])
                        if isinstance(services, list):
                            service_list = []
                            status_map = {0: "OK", 1: "WARNING", 2: "CRITICAL", 3: "UNKNOWN"}

                            for service in services[:50]:  # Limit display
                                if isinstance(service, dict):
                                    extensions = service.get("extensions", {})
                                    description = extensions.get("description", "Unknown")
                                    state = extensions.get("state")
                                    status = status_map.get(state, f"UNKNOWN({state})")
                                    plugin_output = (
                                        extensions.get("plugin_output", "")[:50] + "..."
                                        if len(extensions.get("plugin_output", "")) > 50
                                        else extensions.get("plugin_output", "No output")
                                    )

                                    service_list.append(
                                        f"🔧 **{description}**\n   Status: {status}\n   Output: {plugin_output}"
                                    )

                            if service_list:
                                return [
                                    {
                                        "type": "text",
                                        "text": (
                                            f"🔧 **Services for Host: {host_name}** ({len(services)} total, showing first {len(service_list)}):\n\n"
                                            + "\n\n".join(service_list)
                                        ),
                                    }
                                ]
                            else:
                                return [{"type": "text", "text": f"📭 No services found for host {host_name}"}]
            except Exception as e:
                self.logger.debug(f"Host services action failed: {e}")

        # Method 2: Fallback to domain-types collection (for all services or if host-specific failed)
        try:
            # Request the state/plugin_output columns explicitly — without them the
            # collection endpoint omits 'state' and every service shows as UNKNOWN.
            params = {"columns": ["host_name", "description", "state", "plugin_output"]}
            if host_name:
                params["host_name"] = host_name

            result = self.client.get("domain-types/service/collections/all", params=params)

            if not result.get("success"):
                return self.error_response("Failed to retrieve services")

            services = result["data"].get("value", [])
            if not services:
                return [{"type": "text", "text": "📭 No services found"}]

            service_list = []
            status_map = {0: "OK", 1: "WARNING", 2: "CRITICAL", 3: "UNKNOWN"}

            for service in services[:50]:  # Limit display
                service_host = service.get("extensions", {}).get("host_name", "Unknown")
                description = service.get("extensions", {}).get("description", "Unknown")
                state = service.get("extensions", {}).get("state")
                status = status_map.get(state, f"UNKNOWN({state})")
                service_list.append(f"🔧 {service_host}/{description} (Status: {status})")

            return [
                {
                    "type": "text",
                    "text": (
                        f"🔧 **CheckMK Services** ({len(services)} total, showing first {len(service_list)}):\n\n"
                        + "\n".join(service_list)
                    ),
                }
            ]
        except Exception as e:
            self.logger.debug(f"Service collection fallback failed: {e}")
            return self.error_response(
                "Service retrieval failed", "Could not retrieve services using any available method"
            )

    async def _get_service_status(self, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get service status information using documented CheckMK REST API"""
        host_name = arguments.get("host_name")
        service_description = arguments.get("service_description")

        if not host_name or not service_description:
            return self.error_response("Missing parameters", "host_name and service_description are required")

        self.logger.debug(f"Getting service status for: {host_name}/{service_description}")

        # Method 1: Use documented CheckMK show_service action (OFFICIAL API)
        try:
            endpoint = f"objects/host/{host_name}/actions/show_service/invoke"
            params = {"service_description": service_description}

            result = self.client.get(endpoint, params=params)
            self.logger.debug(f"CheckMK show_service API result: {result}")

            if result.get("success"):
                data = result.get("data", {})

                if isinstance(data, dict) and "extensions" in data:
                    extensions = data["extensions"]

                    # Extract monitoring information using documented API response
                    state = extensions.get("state")
                    description = extensions.get("description", service_description)
                    host_name_from_api = extensions.get("host_name", host_name)
                    last_check = extensions.get("last_check")
                    state_type = extensions.get("state_type")

                    if state is not None:
                        # Map numeric state to status text
                        status_map = {0: "OK", 1: "WARNING", 2: "CRITICAL", 3: "UNKNOWN"}
                        status_text = status_map.get(state, f"UNKNOWN({state})")

                        # Choose appropriate icon
                        if state == 0:
                            status_icon = "✅"
                        elif state == 1:
                            status_icon = "⚠️"
                        elif state == 2:
                            status_icon = "🔴"
                        else:
                            status_icon = "❓"

                        # Format last check time if available
                        last_check_text = "Unknown"
                        if last_check:
                            import time

                            try:
                                current_time = time.time()
                                time_diff = int(current_time - last_check)
                                if time_diff < 60:
                                    last_check_text = f"{time_diff}s ago"
                                elif time_diff < 3600:
                                    last_check_text = f"{time_diff // 60}m ago"
                                elif time_diff < 86400:
                                    last_check_text = f"{time_diff // 3600}h ago"
                                else:
                                    last_check_text = f"{time_diff // 86400}d ago"
                            except (ValueError, TypeError):
                                last_check_text = str(last_check)

                        return [
                            {
                                "type": "text",
                                "text": (
                                    f"{status_icon} **Service Status: {host_name_from_api}/{description}**\n\n"
                                    f"**Status:** {status_text}\n"
                                    f"**State Code:** {state}\n"
                                    f"**Last Check:** {last_check_text}\n"
                                    f"**State Type:** {'Hard' if state_type == 1 else 'Soft'}\n\n"
                                    f"✅ **Live monitoring data from CheckMK REST API**"
                                ),
                            }
                        ]
                    else:
                        return [
                            {
                                "type": "text",
                                "text": (
                                    f"📊 **Service Found: {host_name}/{description}**\n\n"
                                    f"❌ **No state information available**\n"
                                    f"Available fields: {list(extensions.keys())}"
                                ),
                            }
                        ]
                else:
                    return self.error_response(
                        "Unexpected response format",
                        f"Expected extensions in response, got: {list(data.keys()) if isinstance(data, dict) else type(data)}",
                    )
            else:
                error_data = result.get("data", {})
                return self.error_response("API call failed", f"show_service action failed: {error_data}")

        except Exception as e:
            self.logger.debug(f"CheckMK show_service API failed: {e}")

        # Method 2: Fallback to direct service object API
        try:
            import urllib.parse

            encoded_service = urllib.parse.quote(service_description, safe="")
            result = self.client.get(f"objects/service/{host_name}/{encoded_service}")
            self.logger.debug(f"Direct service API result: {result}")

            if result.get("success"):
                data = result.get("data", {})

                if isinstance(data, dict) and "extensions" in data:
                    extensions = data["extensions"]
                    state = extensions.get("state")

                    if state is not None:
                        status_map = {0: "OK", 1: "WARNING", 2: "CRITICAL", 3: "UNKNOWN"}
                        status = status_map.get(state, f"UNKNOWN({state})")

                        return [
                            {
                                "type": "text",
                                "text": (
                                    f"📊 **Service Status: {host_name}/{service_description}** (Fallback API)\n\n"
                                    f"Status: {status}\n"
                                    f"State Code: {state}\n\n"
                                    f"⚠️ **Note:** Using fallback API, limited monitoring information available"
                                ),
                            }
                        ]
        except Exception as e:
            self.logger.debug(f"Direct service API failed: {e}")

        # Method 3: Try correct CheckMK Query API format for services (based on cURL example)
        try:
            # Use proper CheckMK API query format with columns and query parameters
            params = {
                "columns": ["host_name", "description", "state", "plugin_output", "last_check", "last_state_change"],
                "query": {
                    "op": "and",
                    "expr": [
                        {"op": "=", "left": "host_name", "right": host_name},
                        {"op": "=", "left": "description", "right": service_description},
                    ],
                },
            }

            result = self.client.get("domain-types/service/collections/all", params=params)
            self.logger.debug(f"Correct service query format result: {result}")

            if result.get("success"):
                services_data = result.get("data", {})
                if "value" in services_data and services_data["value"]:
                    service_data = services_data["value"][0]

                    # Parse service data based on columns order
                    if isinstance(service_data, list) and len(service_data) >= 3:
                        returned_host = service_data[0] if len(service_data) > 0 else "Unknown"
                        returned_desc = service_data[1] if len(service_data) > 1 else "Unknown"
                        state = service_data[2] if len(service_data) > 2 else None
                        plugin_output = service_data[3] if len(service_data) > 3 else "No output"
                        last_check = service_data[4] if len(service_data) > 4 else "Never"
                        last_state_change = service_data[5] if len(service_data) > 5 else "Unknown"

                        status_map = {0: "OK", 1: "WARNING", 2: "CRITICAL", 3: "UNKNOWN"}
                        status = status_map.get(state, f"UNKNOWN({state})")

                        return [
                            {
                                "type": "text",
                                "text": (
                                    f"📊 **Service Status: {host_name}/{service_description}** (Correct API Query)\n\n"
                                    f"Status: {status}\n"
                                    f"Output: {plugin_output}\n"
                                    f"Last Check: {last_check}\n"
                                    f"Last State Change: {last_state_change}\n\n"
                                    f"🔍 **Debug Info:**\n"
                                    f"Raw State: {state}\n"
                                    f"Query Result: {service_data}\n"
                                    f"✅ **Data Source:** Correct CheckMK Query API"
                                ),
                            }
                        ]
                    elif isinstance(service_data, dict):
                        # Handle dict response format
                        extensions = service_data.get("extensions", {})
                        state = extensions.get("state")

                        if state is not None:
                            status_map = {0: "OK", 1: "WARNING", 2: "CRITICAL", 3: "UNKNOWN"}
                            status = status_map.get(state, f"UNKNOWN({state})")

                            plugin_output = extensions.get("plugin_output", "No output available")
                            last_check = extensions.get("last_check", "Never")

                            return [
                                {
                                    "type": "text",
                                    "text": (
                                        f"📊 **Service Status: {host_name}/{service_description}** (Dict Format)\n\n"
                                        f"Status: {status}\n"
                                        f"Output: {plugin_output}\n"
                                        f"Last Check: {last_check}\n\n"
                                        f"🔍 **Debug Info:**\n"
                                        f"Raw State: {state}\n"
                                        f"Extensions: {list(extensions.keys())}"
                                    ),
                                }
                            ]
        except Exception as e:
            self.logger.debug(f"Correct service query format failed: {e}")

        # Method 4: Try old format as fallback
        try:
            query_data = {
                "query": f'{{"op": "and", "expr": [{{"op": "=", "left": "host_name", "right": "{host_name}"}}, {{"op": "=", "left": "description", "right": "{service_description}"}}]}}'
            }
            result = self.client.get("domain-types/service/collections/all", params=query_data)
            self.logger.debug(f"Service collection query result: {result}")

            if result.get("success"):
                services = result["data"].get("value", [])
                if services:
                    service = services[0]
                    extensions = service.get("extensions", {})
                    state = extensions.get("state")

                    if state is not None:
                        status_map = {0: "OK", 1: "WARNING", 2: "CRITICAL", 3: "UNKNOWN"}
                        status = status_map.get(state, f"UNKNOWN({state})")

                        plugin_output = extensions.get("plugin_output", "No output available")
                        last_check = extensions.get("last_check", "Never")

                        return [
                            {
                                "type": "text",
                                "text": (
                                    f"📊 **Service Status: {host_name}/{service_description}** (Fallback Query)\n\n"
                                    f"Status: {status}\n"
                                    f"Output: {plugin_output}\n"
                                    f"Last Check: {last_check}\n\n"
                                    f"🔍 **Debug Info:**\n"
                                    f"Raw State: {state}\n"
                                    f"Extensions: {list(extensions.keys())}"
                                ),
                            }
                        ]
        except Exception as e:
            self.logger.debug(f"Service collection query failed: {e}")

        # If all methods failed, return comprehensive error information
        return [
            {
                "type": "text",
                "text": (
                    f"❌ **Service Status Retrieval Failed**\n\n"
                    f"Service: {host_name}/{service_description}\n\n"
                    f"**Tried Methods:**\n"
                    f"1️⃣ Direct service object API (objects/service/)\n"
                    f"2️⃣ LiveStatus query (real-time data)\n"
                    f"3️⃣ Domain-type service collection query\n\n"
                    f"**Possible Issues:**\n"
                    f"• Service not found in monitoring system\n"
                    f"• Service description name mismatch\n"
                    f"• CheckMK API version compatibility\n"
                    f"• Monitoring data not yet available\n\n"
                    f"**Recommendation:**\n"
                    f"Verify the service exists in CheckMK GUI and is being monitored."
                ),
            }
        ]
