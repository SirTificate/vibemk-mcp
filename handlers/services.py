"""
Service management handlers
"""

import time
import urllib.parse
from typing import Any, Dict, List, Optional

from api.exceptions import CheckMKError
from handlers.base import BaseHandler

# Keyed by Optional[int]: Method 3's column-array shape can leave state
# genuinely absent (short row -> None), and that None is looked up as-is rather
# than substituted, so the lookup key really can be None, not just int.
_STATUS_MAP: Dict[Optional[int], str] = {0: "OK", 1: "WARNING", 2: "CRITICAL", 3: "UNKNOWN"}
_STATUS_ICONS: Dict[int, str] = {0: "✅", 1: "⚠️", 2: "🔴"}

_MAX_SERVICES_DISPLAYED = 50
_PLUGIN_OUTPUT_PREVIEW_LENGTH = 50

_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600
_SECONDS_PER_DAY = 86400

# Column positions in Method 3's array-shaped rows: ["host_name", "description",
# "state", "plugin_output", "last_check", "last_state_change"]
_COL_STATE = 2
_COL_PLUGIN_OUTPUT = 3
_COL_LAST_CHECK = 4
_COL_LAST_STATE_CHANGE = 5
_MIN_COLUMNS_FOR_STATE = _COL_STATE + 1


class ServiceHandler(BaseHandler):
    """Handle service management operations"""

    async def handle(self, tool_name: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Handle service-related tool calls"""

        try:
            if tool_name == "vibemk_get_checkmk_services":
                response = await self._get_services(arguments)
            elif tool_name == "vibemk_get_service_status":
                response = await self._get_service_status(arguments)
            else:
                response = self.error_response("Unknown tool", f"Tool '{tool_name}' is not supported")

        except CheckMKError as e:
            return self.error_response("CheckMK API Error", str(e))
        except Exception as e:
            self.logger.exception("Error in %s", tool_name)
            return self.error_response("Unexpected Error", str(e))
        else:
            return response

    async def _get_services(self, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get list of services with optional host filtering - IMPROVED VERSION"""
        host_name = arguments.get("host_name")

        # Method 1: If specific host is requested, use the show_service action (best method)
        if host_name:
            response = self._services_for_host_via_show_service(host_name)
            if response is not None:
                return response

        # Method 2: Fallback to domain-types collection (for all services or if host-specific failed)
        return self._services_via_collection(host_name)

    def _services_for_host_via_show_service(self, host_name: str) -> Optional[List[Dict[str, Any]]]:
        """Method 1: the show_service action for a single host (best method)"""
        # The try wraps the call *and* every bit of the processing below, so a
        # malformed response falls through to Method 2 instead of aborting
        # _get_services entirely.
        try:
            result = self.client.post(f"objects/host/{host_name}/actions/show_service/invoke", data={})
            self.logger.debug("Host services API result: %s", result)

            if not result.get("success"):
                return None

            services_data = result.get("data", {})
            if not isinstance(services_data, dict):
                return None

            services = services_data.get("value", [])
            if not isinstance(services, list):
                return None

            service_list = []
            for service in services[:_MAX_SERVICES_DISPLAYED]:
                if not isinstance(service, dict):
                    continue

                extensions = service.get("extensions", {})
                description = extensions.get("description", "Unknown")
                state = extensions.get("state")
                status = _STATUS_MAP.get(state, f"UNKNOWN({state})")
                plugin_output = (
                    extensions.get("plugin_output", "")[:_PLUGIN_OUTPUT_PREVIEW_LENGTH] + "..."
                    if len(extensions.get("plugin_output", "")) > _PLUGIN_OUTPUT_PREVIEW_LENGTH
                    else extensions.get("plugin_output", "No output")
                )

                service_list.append(f"🔧 **{description}**\n   Status: {status}\n   Output: {plugin_output}")

            if not service_list:
                return [{"type": "text", "text": f"📭 No services found for host {host_name}"}]

            return [
                {
                    "type": "text",
                    "text": (
                        f"🔧 **Services for Host: {host_name}** "
                        f"({len(services)} total, showing first {len(service_list)}):\n\n" + "\n\n".join(service_list)
                    ),
                }
            ]
        except Exception as e:
            self.logger.debug("Host services action failed: %s", e)
            return None

    def _services_via_collection(self, host_name: Optional[str]) -> List[Dict[str, Any]]:
        """Method 2: the domain-types service collection (all services, or one host)"""
        # This is the last fallback, so the try has to wrap the processing
        # below too: an exception there must still produce the specific
        # "Service retrieval failed" message, not propagate past _get_services
        # into handle()'s generic catch-all.
        try:
            # Request the state/plugin_output columns explicitly — without them the
            # collection endpoint omits 'state' and every service shows as UNKNOWN.
            params: Dict[str, Any] = {"columns": ["host_name", "description", "state", "plugin_output"]}
            if host_name:
                params["host_name"] = host_name

            result = self.client.get("domain-types/service/collections/all", params=params)

            if not result.get("success"):
                return self.error_response("Failed to retrieve services")

            services = result["data"].get("value", [])
            if not services:
                return [{"type": "text", "text": "📭 No services found"}]

            service_list = []
            for service in services[:_MAX_SERVICES_DISPLAYED]:
                service_host = service.get("extensions", {}).get("host_name", "Unknown")
                description = service.get("extensions", {}).get("description", "Unknown")
                state = service.get("extensions", {}).get("state")
                status = _STATUS_MAP.get(state, f"UNKNOWN({state})")
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
            self.logger.debug("Service collection fallback failed: %s", e)
            return self.error_response(
                "Service retrieval failed", "Could not retrieve services using any available method"
            )

    async def _get_service_status(self, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get service status information using documented CheckMK REST API"""
        host_name = arguments.get("host_name")
        service_description = arguments.get("service_description")

        if not host_name or not service_description:
            return self.error_response("Missing parameters", "host_name and service_description are required")

        self.logger.debug("Getting service status for: %s/%s", host_name, service_description)

        for fallback in (
            self._service_status_via_show_service,
            self._service_status_via_direct_object,
            self._service_status_via_query_api,
            self._service_status_via_legacy_query,
        ):
            response = fallback(host_name, service_description)
            if response is not None:
                return response

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

    def _service_status_via_show_service(
        self, host_name: str, service_description: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Method 1: the documented CheckMK show_service action (OFFICIAL API)

        Unlike the other three fallbacks, a well-formed but unsuccessful or
        unexpectedly shaped response is treated as terminal here — it returns an
        error instead of falling through to the next method.
        """
        # The try wraps the call *and* every bit of response processing below,
        # not just the request: a malformed response (an unhashable or
        # otherwise unexpected 'state', a shape nothing here anticipates) has
        # to fall through to the next method exactly like a request failure
        # does, rather than aborting the whole four-method chain. The explicit
        # "unsuccessful" / "wrong shape" returns just below are not exceptions
        # -- they stay terminal on purpose (see the docstring above).
        try:
            endpoint = f"objects/host/{host_name}/actions/show_service/invoke"
            params = {"service_description": service_description}

            result = self.client.get(endpoint, params=params)
            self.logger.debug("CheckMK show_service API result: %s", result)

            if not result.get("success"):
                error_data = result.get("data", {})
                return self.error_response("API call failed", f"show_service action failed: {error_data}")

            data = result.get("data", {})
            if not (isinstance(data, dict) and "extensions" in data):
                return self.error_response(
                    "Unexpected response format",
                    f"Expected extensions in response, got: "
                    f"{list(data.keys()) if isinstance(data, dict) else type(data)}",
                )

            extensions = data["extensions"]
            state = extensions.get("state")
            description = extensions.get("description", service_description)

            if state is None:
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

            host_name_from_api = extensions.get("host_name", host_name)
            last_check = extensions.get("last_check")
            state_type = extensions.get("state_type")

            status_text = _STATUS_MAP.get(state, f"UNKNOWN({state})")
            status_icon = _STATUS_ICONS.get(state, "❓")
            last_check_text = self._format_last_check(last_check)
        except Exception as e:
            self.logger.debug("CheckMK show_service API failed: %s", e)
            return None
        else:
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

    @staticmethod
    def _format_last_check(last_check: Any) -> str:
        """Format a Unix timestamp as a human-relative 'time ago' string"""
        if not last_check:
            return "Unknown"

        try:
            time_diff = int(time.time() - last_check)
        except (ValueError, TypeError):
            return str(last_check)

        if time_diff < _SECONDS_PER_MINUTE:
            return f"{time_diff}s ago"
        if time_diff < _SECONDS_PER_HOUR:
            return f"{time_diff // _SECONDS_PER_MINUTE}m ago"
        if time_diff < _SECONDS_PER_DAY:
            return f"{time_diff // _SECONDS_PER_HOUR}h ago"
        return f"{time_diff // _SECONDS_PER_DAY}d ago"

    def _service_status_via_direct_object(
        self, host_name: str, service_description: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Method 2: fall back to the direct service object endpoint"""
        # See _service_status_via_show_service: the try has to cover response
        # processing too, not just the request, so a malformed response falls
        # through to Method 3 instead of aborting the whole chain.
        try:
            encoded_service = urllib.parse.quote(service_description, safe="")
            result = self.client.get(f"objects/service/{host_name}/{encoded_service}")
            self.logger.debug("Direct service API result: %s", result)

            if not result.get("success"):
                return None

            data = result.get("data", {})
            if not (isinstance(data, dict) and "extensions" in data):
                return None

            extensions = data["extensions"]
            state = extensions.get("state")
            if state is None:
                return None

            status = _STATUS_MAP.get(state, f"UNKNOWN({state})")
        except Exception as e:
            self.logger.debug("Direct service API failed: %s", e)
            return None
        else:
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

    def _service_status_via_query_api(self, host_name: str, service_description: str) -> Optional[List[Dict[str, Any]]]:
        """Method 3: the CheckMK query API with explicit columns and a query filter"""
        # See _service_status_via_show_service: the try covers the calls to
        # the formatting helpers below too, so an exception raised while
        # building either response shape falls through to Method 4 instead of
        # aborting the whole chain.
        try:
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
            self.logger.debug("Correct service query format result: %s", result)

            if not result.get("success"):
                return None

            services_data = result.get("data", {})
            if not services_data.get("value"):
                return None

            service_data = services_data["value"][0]

            response: Optional[List[Dict[str, Any]]]
            if isinstance(service_data, list) and len(service_data) >= _MIN_COLUMNS_FOR_STATE:
                response = self._service_status_from_columns(host_name, service_description, service_data)
            elif isinstance(service_data, dict):
                response = self._service_status_from_dict(host_name, service_description, service_data)
            else:
                response = None
        except Exception as e:
            self.logger.debug("Correct service query format failed: %s", e)
            return None
        else:
            return response

    def _service_status_from_columns(
        self, host_name: str, service_description: str, service_data: List[Any]
    ) -> List[Dict[str, Any]]:
        """Format Method 3's column-array response shape"""
        state = service_data[_COL_STATE] if len(service_data) > _COL_STATE else None
        plugin_output = service_data[_COL_PLUGIN_OUTPUT] if len(service_data) > _COL_PLUGIN_OUTPUT else "No output"
        last_check = service_data[_COL_LAST_CHECK] if len(service_data) > _COL_LAST_CHECK else "Never"
        last_state_change = (
            service_data[_COL_LAST_STATE_CHANGE] if len(service_data) > _COL_LAST_STATE_CHANGE else "Unknown"
        )

        status = _STATUS_MAP.get(state, f"UNKNOWN({state})")

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

    def _service_status_from_dict(
        self, host_name: str, service_description: str, service_data: Dict[str, Any]
    ) -> Optional[List[Dict[str, Any]]]:
        """Format Method 3's dict-object response shape"""
        extensions = service_data.get("extensions", {})
        state = extensions.get("state")
        if state is None:
            return None

        status = _STATUS_MAP.get(state, f"UNKNOWN({state})")
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

    def _service_status_via_legacy_query(
        self, host_name: str, service_description: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Method 4: the older string-encoded query format, as a last resort"""
        # See _service_status_via_show_service: the try covers response
        # processing too. This is the last fallback, so an uncaught exception
        # here would otherwise skip the "all methods failed" summary entirely
        # and surface as a generic error instead.
        try:
            query_data = {
                "query": (
                    '{"op": "and", "expr": ['
                    f'{{"op": "=", "left": "host_name", "right": "{host_name}"}}, '
                    f'{{"op": "=", "left": "description", "right": "{service_description}"}}'
                    "]}"
                )
            }
            result = self.client.get("domain-types/service/collections/all", params=query_data)
            self.logger.debug("Service collection query result: %s", result)

            if not result.get("success"):
                return None

            services = result["data"].get("value", [])
            if not services:
                return None

            extensions = services[0].get("extensions", {})
            state = extensions.get("state")
            if state is None:
                return None

            status = _STATUS_MAP.get(state, f"UNKNOWN({state})")
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
            self.logger.debug("Service collection query failed: %s", e)
            return None
