"""
Downtime management handlers for CheckMK maintenance scheduling
"""

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from api.exceptions import CheckMKError
from handlers.base import BaseHandler


class DowntimeHandler(BaseHandler):
    """Handle downtime operations for hosts and services"""

    _MAX_HOUR = 23
    _MAX_MINUTE = 59
    _MAX_LISTED_DOWNTIMES = 30

    async def handle(self, tool_name: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Handle downtime-related tool calls"""

        try:
            if tool_name == "vibemk_schedule_host_downtime":
                return await self._schedule_host_downtime(arguments)
            if tool_name == "vibemk_schedule_service_downtime":
                return await self._schedule_service_downtime(arguments)
            if tool_name == "vibemk_list_downtimes":
                return await self._list_downtimes(arguments)
            if tool_name == "vibemk_delete_downtime":
                return await self._delete_downtime(arguments)
            if tool_name == "vibemk_get_active_downtimes":
                return await self._get_active_downtimes(arguments)
            if tool_name == "vibemk_check_host_downtime_status":
                return await self._check_host_downtime_status(arguments)
            return self.error_response("Unknown tool", f"Tool '{tool_name}' is not supported")

        except CheckMKError as e:
            return self.error_response("CheckMK API Error", str(e))
        except Exception as e:
            self.logger.exception("Error in %s", tool_name)
            return self.error_response("Unexpected Error", str(e))

    async def _schedule_host_downtime(self, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Schedule downtime for a host using CheckMK API"""
        host_name = arguments.get("host_name")
        if not host_name:
            return self.error_response("Missing parameter", "host_name is required")

        # Parse time parameters - support both string durations and explicit times
        start_time = arguments.get("start_time")
        end_time = arguments.get("end_time")
        duration = arguments.get("duration", "60m")  # Default 1 hour, support string format
        comment = arguments.get("comment", "Scheduled maintenance")
        # Convert duration to minutes if it's a string
        if isinstance(duration, str):
            duration_minutes = self._parse_time_delta(duration)
        else:
            duration_minutes = int(duration) if duration else 60

        # Parse timestamps
        downtime_times = self._parse_downtime_times(start_time, end_time, duration_minutes)

        # Check for existing downtimes (optional - based on working example)
        existing_downtimes = await self._get_current_downtimes(host_name, [], comment)
        force = arguments.get("force", False)

        if existing_downtimes and not force:
            self.logger.info("Host %s already has downtime with comment '%s'", host_name, comment)
            response = "⚠️ **Downtime Already Exists**\n\n"
            response += f"**Host:** {host_name}\n"
            response += f"**Existing Comment:** {comment}\n"
            response += "**Status:** Not creating duplicate downtime\n\n"
            response += "💡 **Tip:** Use `force=true` parameter to create anyway, or use different comment"
            return [{"type": "text", "text": response}]

        # Build downtime request data with correct CheckMK format
        downtime_data = {
            "downtime_type": "host",
            "host_name": host_name,
            "start_time": downtime_times["start_time"],
            "end_time": downtime_times["end_time"],
            "comment": comment,
        }

        self.logger.debug("Scheduling host downtime with data: %s", downtime_data)

        # Schedule the downtime
        result = self.client.post("domain-types/downtime/collections/host", data=downtime_data)

        # Check for successful creation (expect 204 No Content or 200 OK)
        success = result.get("success") or (result.get("status_code") in [200, 204])

        if success:
            downtime_info = result.get("data", {})
            downtime_id = downtime_info.get("id", "Unknown")

            # Verify downtime creation with retry logic (based on working example)
            verified = await self._verify_downtime_creation(host_name, comment, max_retries=5)

            response = "✅ **Host Downtime Scheduled Successfully**\n\n"
            response += f"**Host:** {host_name}\n"
            if downtime_id != "Unknown":
                response += f"**Downtime ID:** {downtime_id}\n"
            response += f"**Start:** {downtime_times['start_time']}\n"
            response += f"**End:** {downtime_times['end_time']}\n"
            response += f"**Duration:** {duration_minutes} minutes\n"
            response += f"**Comment:** {comment}\n"
            response += f"**Verification:** {'✅ Confirmed' if verified else '⚠️ Pending (may take a moment)'}\n"
            response += "\n💡 **Tip:** Use `vibemk_list_downtimes` to view all active downtimes"

            return [{"type": "text", "text": response}]

        error_data = result.get("data", {})
        return self.error_response(
            "Failed to schedule host downtime",
            f"Could not schedule downtime for host '{host_name}': {error_data.get('title', str(error_data))}",
        )

    def _format_services_already_scheduled(
        self, host_name: str, service_descriptions: List[str], comment: str
    ) -> List[Dict[str, Any]]:
        """Response for when every requested service already has a matching downtime."""
        response = "⚠️ **All Services Already Have Downtime**\n\n"
        response += f"**Host:** {host_name}\n"
        response += f"**Services:** {', '.join(service_descriptions)}\n"
        response += f"**Existing Comment:** {comment}\n"
        response += "**Status:** Not creating duplicate downtimes\n\n"
        response += "💡 **Tip:** Use `force=true` parameter to create anyway, or use different comment"
        return [{"type": "text", "text": response}]

    def _format_service_downtime_success(
        self,
        host_name: str,
        services_to_schedule: List[str],
        service_descriptions: List[str],
        details: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Success response for a scheduled service downtime.

        `details` carries downtime_id, downtime_times, duration_minutes,
        comment and verified — grouped into one dict to keep the argument
        count down.
        """
        downtime_id = details["downtime_id"]
        downtime_times = details["downtime_times"]
        verified = details["verified"]

        response = "✅ **Service Downtime Scheduled Successfully**\n\n"
        response += f"**Host:** {host_name}\n"
        response += f"**Services:** {', '.join(services_to_schedule)}\n"
        if downtime_id != "Unknown":
            response += f"**Downtime ID:** {downtime_id}\n"
        response += f"**Start:** {downtime_times['start_time']}\n"
        response += f"**End:** {downtime_times['end_time']}\n"
        response += f"**Duration:** {details['duration_minutes']} minutes\n"
        response += f"**Comment:** {details['comment']}\n"
        response += f"**Verification:** {'✅ Confirmed' if verified else '⚠️ Pending (may take a moment)'}\n"

        if len(services_to_schedule) != len(service_descriptions):
            skipped = [s for s in service_descriptions if s not in services_to_schedule]
            response += f"**Skipped (existing):** {', '.join(skipped)}\n"

        response += "\n💡 **Tip:** Use `vibemk_list_downtimes` to view all active downtimes"
        return [{"type": "text", "text": response}]

    async def _schedule_service_downtime(self, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Schedule downtime for service(s) on a host using CheckMK API"""
        host_name = arguments.get("host_name")

        # Accept both singular and plural forms
        service_descriptions = arguments.get("service_descriptions", [])
        if not service_descriptions:
            service_description = arguments.get("service_description")
            if service_description:
                service_descriptions = [service_description]

        if not host_name:
            return self.error_response("Missing parameter", "host_name is required")
        if not service_descriptions:
            return self.error_response("Missing parameter", "service_description or service_descriptions is required")

        # Ensure service_descriptions is a list
        if isinstance(service_descriptions, str):
            service_descriptions = [service_descriptions]

        # Parse time parameters - support both string durations and explicit times
        start_time = arguments.get("start_time")
        end_time = arguments.get("end_time")
        duration = arguments.get("duration", "60m")  # Default 1 hour, support string format
        comment = arguments.get("comment", "Scheduled service maintenance")
        # Convert duration to minutes if it's a string
        if isinstance(duration, str):
            duration_minutes = self._parse_time_delta(duration)
        else:
            duration_minutes = int(duration) if duration else 60

        # Parse timestamps
        downtime_times = self._parse_downtime_times(start_time, end_time, duration_minutes)

        # Check for existing downtimes for services (based on working example)
        existing_downtimes = await self._get_current_downtimes(host_name, service_descriptions, comment)
        force = arguments.get("force", False)

        # Filter out services that already have downtimes (unless force=true)
        if not force:
            services_to_schedule = [s for s in service_descriptions if s not in existing_downtimes]
            if not services_to_schedule:
                return self._format_services_already_scheduled(host_name, service_descriptions, comment)
        else:
            services_to_schedule = service_descriptions

        # Build downtime request data with correct CheckMK format
        downtime_data = {
            "downtime_type": "service",
            "host_name": host_name,
            "service_descriptions": services_to_schedule,
            "start_time": downtime_times["start_time"],
            "end_time": downtime_times["end_time"],
            "comment": comment,
        }

        self.logger.debug("Scheduling service downtime with data: %s", downtime_data)

        # Schedule the downtime
        result = self.client.post("domain-types/downtime/collections/service", data=downtime_data)

        # Check for successful creation (expect 204 No Content or 200 OK)
        success = result.get("success") or (result.get("status_code") in [200, 204])

        if success:
            downtime_info = result.get("data", {})
            downtime_id = downtime_info.get("id", "Unknown")

            # Verify downtime creation with retry logic for services
            verified = await self._verify_downtime_creation(
                host_name, comment, max_retries=5, services=services_to_schedule
            )

            return self._format_service_downtime_success(
                host_name,
                services_to_schedule,
                service_descriptions,
                {
                    "downtime_id": downtime_id,
                    "downtime_times": downtime_times,
                    "duration_minutes": duration_minutes,
                    "comment": comment,
                    "verified": verified,
                },
            )

        error_data = result.get("data", {})
        return self.error_response(
            "Failed to schedule service downtime",
            f"Could not schedule downtime for services on '{host_name}': {error_data.get('title', str(error_data))}",
        )

    async def _list_downtimes(self, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """List all downtimes or filter by host/service"""
        host_name = arguments.get("host_name")  # Optional filter
        service_description = arguments.get("service_description")  # Optional filter
        show_only_active = arguments.get("active_only", True)  # Show only active by default

        self.logger.debug("Listing downtimes - host: %s, service: %s", host_name, service_description)

        # Get all downtimes
        result = self.client.get("domain-types/downtime/collections/all")

        if not result.get("success"):
            error_data = result.get("data", {})
            return self.error_response(
                "Failed to retrieve downtimes",
                f"Could not get downtime list: {error_data.get('title', str(error_data))}",
            )

        downtimes = result["data"].get("value", [])

        # Filter downtimes if requested
        filtered_downtimes = []
        for downtime in downtimes:
            extensions = downtime.get("extensions", {})

            # Filter by host if specified
            if host_name and extensions.get("host_name") != host_name:
                continue

            # Filter by service if specified
            if service_description and extensions.get("service_description") != service_description:
                continue

            # Filter by active status if requested
            if show_only_active and extensions.get("is_pending", 0) == 1:
                continue

            filtered_downtimes.append(downtime)

        return [{"type": "text", "text": self._format_downtimes_list(filtered_downtimes, host_name)}]

    def _resolve_delete_target(
        self, arguments: Dict[str, Any]
    ) -> Tuple[Optional[str], List[str], Optional[str], Optional[List[Dict[str, Any]]]]:
        """Work out which host/services a delete request targets.

        Returns (host_name, service_descriptions, comment, error). error is
        None on success; the first three values should be ignored when it isn't.
        """
        downtime_id = arguments.get("downtime_id")
        host_name = arguments.get("host_name")
        service_descriptions = arguments.get("service_descriptions", [])
        service_description = arguments.get("service_description")
        comment = arguments.get("comment")

        # Convert single service to list
        if service_description and not service_descriptions:
            service_descriptions = [service_description]

        if not downtime_id:
            if not host_name:
                return None, [], None, self.error_response("Missing parameter", "host_name or downtime_id is required")
            return host_name, service_descriptions, comment, None

        # downtime_id was given: get the specific downtime details first
        self.logger.debug("Getting downtime details for ID: %s", downtime_id)

        # Get all downtimes to find the specific one and extract required info
        list_result = self.client.get("domain-types/downtime/collections/all")
        if not list_result.get("success"):
            return (
                None,
                [],
                None,
                self.error_response("Failed to get downtime info", "Could not retrieve downtime details"),
            )

        downtimes = list_result["data"].get("value", [])
        target_downtime = None
        for downtime in downtimes:
            if str(downtime.get("id")) == str(downtime_id):
                target_downtime = downtime
                break

        if not target_downtime:
            return None, [], None, self.error_response("Downtime not found", f"No downtime found with ID {downtime_id}")

        # Extract information from the specific downtime for query-based deletion
        extensions = target_downtime.get("extensions", {})
        host_name = extensions.get("host_name")
        service_desc = extensions.get("service_description")
        comment = comment or extensions.get("comment")  # Use existing comment if not provided
        is_service_raw = extensions.get("is_service", 0)
        is_service = is_service_raw in {1, "yes"}

        if is_service and service_desc:
            service_descriptions = [service_desc]

        if not host_name:
            return None, [], None, self.error_response("Invalid downtime", "Could not determine host name for downtime")

        return host_name, service_descriptions, comment, None

    def _build_delete_query(
        self, host_name: str, service_descriptions: List[str], comment: Optional[str], is_service: bool
    ) -> Dict[str, str]:
        """Build the query-based delete request body (working CheckMK format)."""
        query_filters = []

        if is_service:
            if len(service_descriptions) > 1:
                # Multiple services - use OR filter
                service_filter_parts = [
                    f'{{"op": "~", "left": "service_description", "right": "{s}"}}' for s in service_descriptions
                ]
                query_filters.append(f'{{"op": "or", "expr": [{", ".join(service_filter_parts)}]}}')
            else:
                # Single service
                query_filters.append(
                    f'{{"op": "~", "left": "service_description", "right": "{service_descriptions[0]}"}}'
                )

        # Add host name filter
        query_filters.append(f'{{"op": "~", "left": "host_name", "right": "{host_name}"}}')

        # Add comment filter if provided
        if comment:
            query_filters.append(f'{{"op": "~", "left": "comment", "right": "{comment}"}}')

        return {"delete_type": "query", "query": f'{{"op": "and", "expr": [{", ".join(query_filters)}]}}'}

    def _format_delete_success(
        self, item: str, is_service: bool, downtime_id: Any, comment: Optional[str]
    ) -> List[Dict[str, Any]]:
        """Success response for a completed downtime deletion."""
        downtime_type = "Service" if is_service else "Host"
        response = "✅ **Downtime Deleted Successfully**\n\n"
        if downtime_id:
            response += f"**Downtime ID:** {downtime_id}\n"
        response += f"**Type:** {downtime_type} downtime\n"
        response += f"**Target:** {item}\n"
        if comment:
            response += f"**Comment:** {comment}\n"
        response += "**Status:** Removed from monitoring schedule\n"
        response += "\n💡 **Tip:** Use `vibemk_list_downtimes` to view remaining active downtimes"
        return [{"type": "text", "text": response}]

    async def _delete_downtime(self, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Delete downtimes using query-based deletion (based on working CheckMK example)"""
        # Support both specific downtime_id deletion and bulk deletion by criteria
        downtime_id = arguments.get("downtime_id")
        host_name, service_descriptions, comment, error = self._resolve_delete_target(arguments)
        if error:
            return error
        assert host_name  # narrowed by _resolve_delete_target's own guards above

        # Check existing downtimes before deletion (based on working example)
        existing_downtimes = await self._get_current_downtimes(host_name, service_descriptions, comment)
        is_service = len(service_descriptions) > 0

        if not existing_downtimes:
            item = f"{host_name}/[{', '.join(service_descriptions)}]" if is_service else host_name
            return [
                {
                    "type": "text",
                    "text": f"📋 **No Matching Downtimes**\n\n'{item}' has no downtimes with comment '{comment}'",
                }
            ]

        delete_data = self._build_delete_query(host_name, service_descriptions, comment, is_service)
        self.logger.debug("Deleting downtime with query data: %s", delete_data)

        result = self.client.post("domain-types/downtime/actions/delete/invoke", data=delete_data)

        item = f"{host_name}/[{', '.join(service_descriptions)}]" if is_service else host_name
        if result.get("success"):
            return self._format_delete_success(item, is_service, downtime_id, comment)

        error_data = result.get("data", {})
        error_detail = error_data.get("title", str(error_data))
        return self.error_response(
            "Failed to delete downtime",
            f"Could not delete downtime for '{item}' with comment '{comment}': {error_detail}",
        )

    async def _get_active_downtimes(self, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get only currently active downtimes"""
        host_name = arguments.get("host_name")  # Optional filter

        # Get all downtimes first
        result = self.client.get("domain-types/downtime/collections/all")

        if not result.get("success"):
            error_data = result.get("data", {})
            return self.error_response(
                "Failed to retrieve active downtimes",
                f"Could not get active downtimes: {error_data.get('title', str(error_data))}",
            )

        all_downtimes = result["data"].get("value", [])

        # Filter for active downtimes only
        active_downtimes = []
        now = datetime.now(timezone.utc).timestamp()

        for downtime in all_downtimes:
            extensions = downtime.get("extensions", {})

            # Skip pending downtimes
            if extensions.get("is_pending", 0) == 1:
                continue

            # Check if downtime is currently active (between start_time and end_time)
            start_time = self._timestamp_to_unix(extensions.get("start_time", 0))
            end_time = self._timestamp_to_unix(extensions.get("end_time", 0))

            # Filter by host if specified
            if start_time <= now <= end_time and (not host_name or extensions.get("host_name") == host_name):
                active_downtimes.append(downtime)

        return [{"type": "text", "text": self._format_active_downtimes(active_downtimes, host_name)}]

    def _parse_downtime_times(
        self, start_time: Optional[str], end_time: Optional[str], duration_minutes: int
    ) -> Dict[str, str]:
        """Parse downtime start/end expressions into the UTC timestamps CheckMK expects.

        Accepts ISO-8601 (with or without offset), natural language ("22:00
        tomorrow"), and relative offsets ("+2h"). Expressions that carry no
        offset are read as local time and converted to UTC, so the timestamps
        sent to CheckMK always denote the instant the user meant.
        """
        fallback_minutes = duration_minutes or 30

        # --- start ---
        start_dt: Optional[datetime]
        if not start_time or start_time in ("now", ""):
            start_dt = datetime.now(timezone.utc)
        elif start_time.startswith("+"):
            start_dt = datetime.now(timezone.utc) + timedelta(minutes=self._parse_time_delta(start_time))
        else:
            start_dt = self._parse_time_expression(start_time)
            if start_dt is None:
                self.logger.warning("Could not parse start_time %r, using now", start_time)
                start_dt = datetime.now(timezone.utc)

        # --- end ---
        end_dt: Optional[datetime]
        if not end_time:
            end_dt = start_dt + timedelta(minutes=fallback_minutes)
        elif end_time.startswith("+"):
            end_dt = start_dt + timedelta(minutes=self._parse_time_delta(end_time))
        else:
            end_dt = self._parse_time_expression(end_time)
            if end_dt is None:
                self.logger.warning("Could not parse end_time %r, using start + duration", end_time)
                end_dt = start_dt + timedelta(minutes=fallback_minutes)

        # Both values are timezone-aware, so this comparison is always well-defined.
        if end_dt <= start_dt:
            self.logger.warning("End time is not after start time, extending by %d minutes", fallback_minutes)
            end_dt = start_dt + timedelta(minutes=fallback_minutes)

        return {
            "start_time": self._to_utc_z(start_dt),
            "end_time": self._to_utc_z(end_dt),
        }

    @staticmethod
    def _to_utc_z(value: datetime) -> str:
        """Render an aware datetime in the UTC 'Z' format CheckMK accepts."""
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _parse_time_expression(self, time_str: str) -> Optional[datetime]:
        """Parse a single time expression into an aware datetime, or None.

        ISO-8601 is tried first: an explicit date must never be reinterpreted
        by the natural-language patterns, which only understand times of day.
        """
        return self._parse_iso_datetime(time_str) or self._parse_natural_time(time_str)

    @staticmethod
    def _parse_iso_datetime(time_str: str) -> Optional[datetime]:
        """Parse an ISO-8601 timestamp into an aware datetime, or None.

        A value without an offset is read as local time.
        """
        if not time_str:
            return None
        candidate = time_str.strip()
        if candidate.endswith(("Z", "z")):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
        # A bare date ("2026-12-24") is a valid ISO value but almost never what
        # a downtime request means, so leave it to the natural-language pass.
        if parsed.tzinfo is None:
            return parsed.astimezone()
        return parsed

    def _parse_natural_time(self, time_str: str) -> Optional[datetime]:
        """
        Parse natural language time expressions into aware datetimes (local zone).

        Supports formats like:
        - "22:00 today" / "22:00" (today at specified time)
        - "14:00 tomorrow" / "tomorrow at 14:00"
        - "monday at 09:00" / "next monday at 09:00"
        - "2024-08-23 at 22:00" (specific date)
        - "in 2 hours" / "in 30 minutes"

        Returns None when nothing matches, so callers can fall back.

        Note: PLR0911/PLR0912 are deferred for this method (see pyproject.toml) —
        the five patterns below are independent, early-returning alternatives, and
        this parsing logic is covered by tests/test_handlers_downtimes.py's timezone
        regression tests, so it is deliberately not restructured in this pass.
        """
        if not time_str:
            return None

        time_str = time_str.strip().lower()
        now = datetime.now().astimezone()
        weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

        def local(value: datetime) -> datetime:
            """Attach the local zone to a naive datetime."""
            return value.astimezone() if value.tzinfo is None else value

        # Pattern 1: explicit calendar date, e.g. "2026-12-24 at 22:00".
        # Checked first so a named date is never reduced to a time of day.
        match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[\sT]+(?:at\s+)?(\d{1,2}):(\d{2}))?", time_str)
        if match:
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            hour = int(match.group(4)) if match.group(4) else now.hour
            minute = int(match.group(5)) if match.group(5) else now.minute
            try:
                # Constructed naive on purpose: local() immediately attaches the local zone.
                naive = datetime(year, month, day, hour, minute)  # noqa: DTZ001
                return local(naive)
            except ValueError:
                pass  # Invalid date, fall through

        # Pattern 2: "in X hours/minutes"
        match = re.search(r"in\s+(\d+)\s+(hour|hours|minute|minutes|min)", time_str)
        if match:
            amount = int(match.group(1))
            if "hour" in match.group(2):
                return now + timedelta(hours=amount)
            return now + timedelta(minutes=amount)

        # Pattern 3: "HH:MM tomorrow" / "tomorrow at HH:MM" / bare "tomorrow"
        if "tomorrow" in time_str:
            match = re.search(r"(\d{1,2}):(\d{2})", time_str)
            hour, minute = (int(match.group(1)), int(match.group(2))) if match else (now.hour, now.minute)
            if 0 <= hour <= self._MAX_HOUR and 0 <= minute <= self._MAX_MINUTE:
                tomorrow = now + timedelta(days=1)
                return tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # Pattern 4: weekday names, e.g. "monday at 09:00", "next tuesday at 14:30"
        for index, day_name in enumerate(weekdays):
            if day_name not in time_str:
                continue
            match = re.search(rf"(?:next\s+)?{day_name}(?:\s+at\s+(\d{{1,2}}):(\d{{2}}))?", time_str)
            if not match:
                continue
            days_ahead = index - now.weekday()
            if days_ahead <= 0 or "next" in time_str:
                days_ahead += 7
            target_date = now + timedelta(days=days_ahead)
            if match.group(1) and match.group(2):
                hour, minute = int(match.group(1)), int(match.group(2))
                if 0 <= hour <= self._MAX_HOUR and 0 <= minute <= self._MAX_MINUTE:
                    target_date = target_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
            return target_date

        # Pattern 5: bare time of day, e.g. "22:00", "at 22:00", "22:00 today".
        # Last resort: only reached when no date and no day name was named.
        match = re.search(r"(?:at\s+)?(\d{1,2}):(\d{2})", time_str)
        if match:
            hour, minute = int(match.group(1)), int(match.group(2))
            if 0 <= hour <= self._MAX_HOUR and 0 <= minute <= self._MAX_MINUTE:
                target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                # A time that already passed today means the next occurrence.
                if target_time <= now and "today" not in time_str:
                    target_time += timedelta(days=1)
                return target_time

        # If no pattern matched, return None to use fallback parsing
        return None

    def _parse_time_delta(self, delta_str: str) -> int:
        """Parse relative time string like '+1h', '+30m', '1h30m' into minutes"""
        delta_str = delta_str.strip("+")
        total_minutes = 0

        # Handle complex formats like "1h30m", "2d4h", etc.
        # Extract all time components
        time_pattern = r"(\d+)([dhm])"
        matches = re.findall(time_pattern, delta_str.lower())

        if matches:
            for raw_value, unit in matches:
                value = int(raw_value)
                if unit == "m":
                    total_minutes += value
                elif unit == "h":
                    total_minutes += value * 60
                elif unit == "d":
                    total_minutes += value * 24 * 60
            return total_minutes

        # Fallback to simple format
        if delta_str.endswith("m"):
            return int(delta_str[:-1])
        if delta_str.endswith("h"):
            return int(delta_str[:-1]) * 60
        if delta_str.endswith("d"):
            return int(delta_str[:-1]) * 24 * 60
        # Default to minutes if no unit specified
        try:
            return int(delta_str)
        except ValueError:
            return 60  # Default 1 hour

    def _format_timestamp(self, timestamp: Any) -> str:
        """Format timestamp to readable string, handling both Unix timestamps and ISO format.

        Displayed in local time deliberately (this is for humans reading the
        output, unlike the UTC-normalized scheduling path above).
        """
        if not timestamp:
            return "Unknown"

        try:
            # If it's already a string, try to parse as ISO format
            if isinstance(timestamp, str):
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                return dt.strftime("%Y-%m-%d %H:%M")
            # If it's a number, treat as Unix timestamp
            if isinstance(timestamp, (int, float)):
                dt = datetime.fromtimestamp(timestamp)  # noqa: DTZ006 - local time is intentional here
                return dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            return "Unknown"
        else:
            return "Unknown"

    def _timestamp_to_unix(self, timestamp: Any) -> float:
        """Convert timestamp to Unix timestamp for comparison"""
        if not timestamp:
            return 0.0

        try:
            # If it's already a string, try to parse as ISO format
            if isinstance(timestamp, str):
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                return dt.timestamp()
            # If it's a number, return as-is
            if isinstance(timestamp, (int, float)):
                return float(timestamp)
        except (ValueError, TypeError):
            return 0.0
        else:
            return 0.0

    def _get_time_only(self, timestamp: Any) -> str:
        """Extract time-only format (HH:MM) from various timestamp formats"""
        try:
            if isinstance(timestamp, str):
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                return dt.strftime("%H:%M")
            if isinstance(timestamp, (int, float)):
                dt = datetime.fromtimestamp(timestamp)  # noqa: DTZ006 - local time is intentional here
                return dt.strftime("%H:%M")
            # Fallback: try to extract from formatted string
            timestamp_str = self._format_timestamp(timestamp)
            return timestamp_str.split()[-1] if " " in timestamp_str else timestamp_str[:5]
        except (ValueError, TypeError, AttributeError):
            return str(timestamp)[:5]  # Return first 5 chars as fallback

    def _format_host_downtimes_section(self, host_downtimes: List[Dict[str, Any]]) -> str:
        """HOST DOWNTIMES section: these suppress all host and service alerts."""
        response = "🏠 **HOST DOWNTIMES** (suppress ALL alerts for these hosts)\n"
        response += "=" * 60 + "\n\n"

        host_grouped: Dict[str, List[Dict[str, Any]]] = {}
        for downtime in host_downtimes:
            extensions = downtime.get("extensions", {})
            host_name = extensions.get("host_name", "Unknown")
            host_grouped.setdefault(host_name, []).append(downtime)

        for host_name, host_dt_list in sorted(host_grouped.items()):
            count_label = "host downtime" if len(host_dt_list) == 1 else "host downtimes"
            response += f"**{host_name}** ({len(host_dt_list)} {count_label})\n"
            for downtime in host_dt_list:
                extensions = downtime.get("extensions", {})
                downtime_id = downtime.get("id", "Unknown")
                comment = extensions.get("comment", "No comment")

                start_time_only = self._get_time_only(extensions.get("start_time", 0))
                end_time_only = self._get_time_only(extensions.get("end_time", 0))

                response += f"  • Downtime #{downtime_id}: {start_time_only} - {end_time_only}\n"
                response += f'    Comment: "{comment}"\n'
                response += "    **Effect**: Host DOWN/UNREACHABLE + ALL service alerts suppressed\n\n"
        return response

    def _format_service_downtimes_section(self, service_downtimes: List[Dict[str, Any]]) -> str:
        """SERVICE DOWNTIMES section: these suppress only specific service alerts."""
        response = "🔧 **SERVICE DOWNTIMES** (suppress only specific service alerts)\n"
        response += "=" * 60 + "\n\n"

        service_grouped: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        for downtime in service_downtimes:
            extensions = downtime.get("extensions", {})
            host_name = extensions.get("host_name", "Unknown")
            service_description = extensions.get("service_description", "Unknown Service")
            service_grouped.setdefault(host_name, {}).setdefault(service_description, []).append(downtime)

        for host_name, services_dict in sorted(service_grouped.items()):
            service_label = "service" if len(services_dict) == 1 else "services"
            response += f"**{host_name}** ({len(services_dict)} {service_label} with downtimes)\n"

            for service_name, service_dt_list in sorted(services_dict.items()):
                downtime_label = "downtime" if len(service_dt_list) == 1 else "downtimes"
                response += f"  📋 **{service_name}** ({len(service_dt_list)} {downtime_label})\n"

                for downtime in service_dt_list:
                    extensions = downtime.get("extensions", {})
                    downtime_id = downtime.get("id", "Unknown")
                    comment = extensions.get("comment", "No comment")

                    start_time_only = self._get_time_only(extensions.get("start_time", 0))
                    end_time_only = self._get_time_only(extensions.get("end_time", 0))

                    response += f"    • Downtime #{downtime_id}: {start_time_only} - {end_time_only}\n"
                    response += f'      Comment: "{comment}"\n'
                    response += f"      **Effect**: Only '{service_name}' alerts suppressed, host alerts STILL FIRE\n\n"

            response += "\n"  # Extra space between hosts
        return response

    def _format_downtimes_list(self, downtimes: List[Dict[str, Any]], host_filter: Optional[str] = None) -> str:
        """Format downtimes list for display, clearly distinguishing host downtimes vs service downtimes"""
        filter_text = f" for host '{host_filter}'" if host_filter else ""
        if not downtimes:
            return f"📋 **No Downtimes Found**\n\nNo active downtimes{filter_text}"

        response = f"📋 **Downtimes List{filter_text}**\n\n"

        # Separate host downtimes from service downtimes
        host_downtimes = []
        service_downtimes = []

        for downtime in downtimes[: self._MAX_LISTED_DOWNTIMES]:  # Increased limit for better visibility
            extensions = downtime.get("extensions", {})
            # Handle different API response formats for is_service
            is_service_raw = extensions.get("is_service", 0)
            is_service = is_service_raw in {1, "yes", "1"}

            if is_service:
                service_downtimes.append(downtime)
            else:
                host_downtimes.append(downtime)

        # Show summary counts
        total_count = len(host_downtimes) + len(service_downtimes)
        response += f"Found {total_count} downtimes: "
        response += f"**{len(host_downtimes)} Host-Level** + **{len(service_downtimes)} Service-Level**\n\n"

        if host_downtimes:
            response += self._format_host_downtimes_section(host_downtimes)

        if service_downtimes:
            response += self._format_service_downtimes_section(service_downtimes)

        # Show additional context if needed
        if len(downtimes) > self._MAX_LISTED_DOWNTIMES:
            remaining = len(downtimes) - self._MAX_LISTED_DOWNTIMES
            response += f"... and {remaining} more downtimes (showing first {self._MAX_LISTED_DOWNTIMES})\n\n"

        # Add helpful footer explaining the distinction
        response += "💡 **Key Distinction:**\n"
        response += "   • **Host Downtimes**: Suppress both host alerts (DOWN/UNREACHABLE) AND all service alerts\n"
        response += (
            "   • **Service Downtimes**: Suppress only the specific service alerts, host alerts continue to fire\n"
        )

        response += "💡 **Tip:** Use `vibemk_delete_downtime` with downtime ID to cancel a downtime"
        return response

    def _format_active_downtimes(
        self, active_downtimes: List[Dict[str, Any]], host_filter: Optional[str] = None
    ) -> str:
        """Format currently active downtimes"""
        if not active_downtimes:
            filter_text = f" on host '{host_filter}'" if host_filter else ""
            return f"🟢 **No Active Downtimes**\n\nNo downtimes are currently active{filter_text}"

        filter_text = f" on host '{host_filter}'" if host_filter else ""
        response = f"🔴 **Currently Active Downtimes{filter_text}**\n\n"
        response += f"Found {len(active_downtimes)} active downtimes:\n\n"

        for i, downtime in enumerate(active_downtimes, 1):
            extensions = downtime.get("extensions", {})

            downtime_id = downtime.get("id", "Unknown")
            host_name = extensions.get("host_name", "Unknown")
            comment = extensions.get("comment", "No comment")
            is_service = extensions.get("is_service", 0) == 1

            # Calculate remaining time
            end_time = extensions.get("end_time", 0)
            now = datetime.now(timezone.utc).timestamp()

            # Handle different timestamp formats from CheckMK API
            if isinstance(end_time, str):
                try:
                    # Try parsing ISO format timestamp
                    end_time_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                    end_time = end_time_dt.timestamp()
                except (ValueError, AttributeError):
                    # Fallback: assume it's already a Unix timestamp string
                    try:
                        end_time = float(end_time)
                    except (ValueError, TypeError):
                        end_time = now  # Default to now if parsing fails

            remaining_minutes = max(0, int((end_time - now) / 60))

            response += f"**{i}. Downtime #{downtime_id}**\n"
            response += f"   🏠 Host: {host_name}\n"
            response += f"   🔧 Type: {'Service' if is_service else 'Host'}\n"
            response += f"   ⏰ Remaining: {remaining_minutes} minutes\n"
            response += f"   💬 Comment: {comment}\n"

            if is_service and extensions.get("service_description"):
                response += f"   🔧 Service: {extensions.get('service_description')}\n"

            response += "\n"

        response += "💡 **Info:** These downtimes are currently suppressing alerts"
        return response

    def _downtime_status_error(self, message: str) -> Dict[str, Any]:
        """Build the failure shape returned by _get_host_downtime_status."""
        return {
            "has_host_downtime": False,
            "has_service_downtimes": False,
            "host_downtime_count": 0,
            "service_downtime_count": 0,
            "active_host_downtimes": [],
            "active_service_downtimes": [],
            "error": message,
        }

    async def _get_host_downtime_status(self, host_name: str) -> Dict[str, Any]:
        """
        Get detailed downtime status for a specific host using precise CheckMK queries.
        This method properly distinguishes between host-level and service-level downtimes
        using the CheckMK Livestatus query format for maximum accuracy.

        Returns:
            Dict containing:
            - has_host_downtime: bool - True if the host object itself has a downtime
            - has_service_downtimes: bool - True if any services on the host have downtimes
            - host_downtime_count: int - Number of host-level downtimes
            - service_downtime_count: int - Number of service-level downtimes
            - active_host_downtimes: List[Dict] - Active host downtimes
            - active_service_downtimes: List[Dict] - Active service downtimes
        """
        # Query 1: Get host-level downtimes only (is_service = 0)
        host_query = {
            "op": "and",
            "expr": [
                {"op": "=", "left": "host_name", "right": host_name},
                {"op": "=", "left": "is_service", "right": "0"},
            ],
        }

        # Query 2: Get service-level downtimes only (is_service = 1)
        service_query = {
            "op": "and",
            "expr": [
                {"op": "=", "left": "host_name", "right": host_name},
                {"op": "=", "left": "is_service", "right": "1"},
            ],
        }

        host_params = {"query": json.dumps(host_query)}
        service_params = {"query": json.dumps(service_query)}

        try:
            # Get host downtimes
            host_result = self.client.get("domain-types/downtime/collections/all", params=host_params)
            if not host_result.get("success"):
                title = host_result.get("data", {}).get("title", "Unknown error")
                message = f"Failed to query host downtimes: {title}"
                self.logger.error("Error getting host downtime status for %s: %s", host_name, message)
                return self._downtime_status_error(message)

            # Get service downtimes
            service_result = self.client.get("domain-types/downtime/collections/all", params=service_params)
            if not service_result.get("success"):
                title = service_result.get("data", {}).get("title", "Unknown error")
                message = f"Failed to query service downtimes: {title}"
                self.logger.error("Error getting host downtime status for %s: %s", host_name, message)
                return self._downtime_status_error(message)

            # Process results
            host_downtimes = host_result["data"].get("value", [])
            service_downtimes = service_result["data"].get("value", [])

            now = datetime.now(timezone.utc).timestamp()

            # Filter for currently active downtimes
            active_host_downtimes = [d for d in host_downtimes if self._is_downtime_active(d, now)]
            active_service_downtimes = [d for d in service_downtimes if self._is_downtime_active(d, now)]

            return {
                "has_host_downtime": len(active_host_downtimes) > 0,
                "has_service_downtimes": len(active_service_downtimes) > 0,
                "host_downtime_count": len(active_host_downtimes),
                "service_downtime_count": len(active_service_downtimes),
                "active_host_downtimes": active_host_downtimes,
                "active_service_downtimes": active_service_downtimes,
                "total_host_downtimes": len(host_downtimes),
                "total_service_downtimes": len(service_downtimes),
            }

        except Exception as e:
            self.logger.exception("Error getting host downtime status for %s", host_name)
            return self._downtime_status_error(str(e))

    def _is_downtime_active(self, downtime: Dict[str, Any], current_timestamp: float) -> bool:
        """
        Check if a downtime is currently active based on its start and end times.

        Args:
            downtime: Downtime object from CheckMK API
            current_timestamp: Current Unix timestamp to compare against

        Returns:
            True if the downtime is currently active, False otherwise
        """
        try:
            extensions = downtime.get("extensions", {})
            start_time = extensions.get("start_time", 0)
            end_time = extensions.get("end_time", 0)

            # Handle different timestamp formats from CheckMK API
            if isinstance(start_time, str):
                try:
                    start_time_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                    start_time = start_time_dt.timestamp()
                except (ValueError, AttributeError):
                    try:
                        start_time = float(start_time)
                    except (ValueError, TypeError):
                        return False

            if isinstance(end_time, str):
                try:
                    end_time_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                    end_time = end_time_dt.timestamp()
                except (ValueError, AttributeError):
                    try:
                        end_time = float(end_time)
                    except (ValueError, TypeError):
                        return False

            # Check if current time is within the downtime window
            return bool(start_time <= current_timestamp <= end_time)
        except Exception as e:
            self.logger.warning("Error checking downtime active status: %s", e)
            return False

    def _format_downtime_status_summary(self, status: Dict[str, Any]) -> str:
        """Host- and service-level downtime status lines."""
        if status["has_host_downtime"]:
            summary = (
                f"🏠 **Host Object Downtime:** ✅ **YES** - Host is covered by "
                f"{status['host_downtime_count']} active host-level downtime(s)\n"
            )
        else:
            summary = "🏠 **Host Object Downtime:** ❌ **NO** - Host object has no active downtimes\n"

        if status["has_service_downtimes"]:
            summary += (
                f"🔧 **Service Downtimes:** ✅ **YES** - {status['service_downtime_count']} "
                f"service(s) on this host have active downtimes\n"
            )
        else:
            summary += "🔧 **Service Downtimes:** ❌ **NO** - No services on this host have active downtimes\n"
        return summary

    def _format_downtime_status_interpretation(self, status: Dict[str, Any]) -> str:
        """The critical host-vs-service alerting distinction, spelled out."""
        if status["has_host_downtime"]:
            return (
                "🎯 **CRITICAL DISTINCTION:** This host HAS host-level downtimes.\n"
                "   ✅ Host alerts (host DOWN, UNREACHABLE) are SUPPRESSED\n"
                "   ✅ All service alerts on this host are also SUPPRESSED\n"
                "   📊 Alert Status: HOST and SERVICE alerts both suppressed\n"
            )
        if status["has_service_downtimes"]:
            return (
                "⚠️ **CRITICAL DISTINCTION:** This host has NO host-level downtimes.\n"
                "   ❌ Host alerts (host DOWN, UNREACHABLE) will FIRE normally\n"
                "   ✅ Only specific service alerts are suppressed by service downtimes\n"
                "   📊 Alert Status: HOST alerts ACTIVE, some SERVICE alerts suppressed\n"
                "   💡 To suppress host alerts, you need a separate HOST downtime\n"
            )
        return (
            "🔴 **NO DOWNTIMES:** This host has neither host nor service downtimes.\n"
            "   ❌ Host alerts (host DOWN, UNREACHABLE) will FIRE normally\n"
            "   ❌ All service alerts will FIRE normally\n"
            "   📊 Alert Status: ALL alerts are ACTIVE\n"
        )

    def _format_downtime_status_details(self, status: Dict[str, Any]) -> str:
        """List the individual active downtimes backing the summary above."""
        details = ""
        if status["active_host_downtimes"]:
            details += "📋 **Active Host Downtimes:**\n"
            for downtime in status["active_host_downtimes"]:
                extensions = downtime.get("extensions", {})
                comment = extensions.get("comment", "No comment")
                details += f"   • Downtime #{downtime.get('id')}: {comment}\n"
            details += "\n"

        if status["active_service_downtimes"]:
            details += "📋 **Active Service Downtimes:**\n"
            for downtime in status["active_service_downtimes"]:
                extensions = downtime.get("extensions", {})
                service = extensions.get("service_description", "Unknown")
                comment = extensions.get("comment", "No comment")
                details += f"   • Service '{service}' (#{downtime.get('id')}): {comment}\n"
            details += "\n"
        return details

    def _format_host_downtime_status(self, host_name: str, status: Dict[str, Any]) -> str:
        """Full downtime-status report for one host."""
        response = f"🔍 **Downtime Status for Host: {host_name}**\n\n"
        response += self._format_downtime_status_summary(status)
        response += "\n"
        response += self._format_downtime_status_interpretation(status)
        response += "\n"
        response += self._format_downtime_status_details(status)
        response += "💡 **Usage:** This tool helps distinguish between host-level and service-level downtimes\n"
        response += "   for proper alerting analysis and downtime troubleshooting."
        return response

    async def _check_host_downtime_status(self, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Check the downtime status for a specific host, properly distinguishing between
        host-level downtimes and service-level downtimes.
        """
        host_name = arguments.get("host_name")
        if not host_name:
            return self.error_response("Missing parameter", "host_name is required")

        # Get detailed downtime status
        status = await self._get_host_downtime_status(host_name)

        if "error" in status:
            return self.error_response("Error checking downtime status", status["error"])

        return [{"type": "text", "text": self._format_host_downtime_status(host_name, status)}]

    async def has_host_level_downtime(self, host_name: str) -> bool:
        """
        Simple method to check if a host has HOST-level downtimes (not service downtimes).
        This can be used by other handlers that need to know if host alerts are suppressed.

        Args:
            host_name: Name of the host to check

        Returns:
            True if the host has active HOST-level downtimes, False otherwise
        """
        try:
            # Use precise query to get only host-level downtimes
            host_query = {
                "op": "and",
                "expr": [
                    {"op": "=", "left": "host_name", "right": host_name},
                    {"op": "=", "left": "is_service", "right": "0"},
                ],
            }

            host_params = {"query": json.dumps(host_query)}
            host_result = self.client.get("domain-types/downtime/collections/all", params=host_params)

            if not host_result.get("success"):
                self.logger.warning("Failed to query host downtimes for %s", host_name)
                return False

            host_downtimes = host_result["data"].get("value", [])
            current_time = datetime.now(timezone.utc).timestamp()

            # Check if any host downtime is currently active
            return any(self._is_downtime_active(downtime, current_time) for downtime in host_downtimes)
        except Exception:
            self.logger.exception("Error checking host-level downtime for %s", host_name)
            return False

    async def _get_current_downtimes(
        self, host_name: str, service_descriptions: List[str], comment: Optional[str] = None
    ) -> List[str]:
        """Get current downtimes for a host/services, based on working CheckMK example"""
        filters = []
        is_service = len(service_descriptions) > 0

        if is_service:
            # Handle list of service descriptions with proper filtering
            if len(service_descriptions) > 1:
                # Create OR filter for multiple services
                service_filters = [
                    f'{{"op": "~", "left": "service_description", "right": "{s}"}}' for s in service_descriptions
                ]
                filters.append(f'{{"op": "or", "expr": [{", ".join(service_filters)}]}}')
            else:
                # Single service filter
                filters.append(f'{{"op": "~", "left": "service_description", "right": "{service_descriptions[0]}"}}')
            filters.append('{"op": "=", "left": "is_service", "right": "1"}')
        else:
            # Host downtime filter
            filters.append('{"op": "=", "left": "is_service", "right": "0"}')

        # Add host name filter
        filters.append(f'{{"op": "~", "left": "host_name", "right": "{host_name}"}}')

        # Add comment filter if provided
        if comment:
            filters.append(f'{{"op": "~", "left": "comment", "right": "{comment}"}}')

        # Build query parameters
        query = f'{{"op": "and", "expr": [{", ".join(filters)}]}}'
        params = {"query": query}

        try:
            # Query existing downtimes
            result = self.client.get("domain-types/downtime/collections/all", params=params)

            if not result.get("success"):
                self.logger.warning("Failed to query existing downtimes: %s", result.get("data", {}))
                return []

            downtimes = result["data"].get("value", [])

            if is_service:
                # Return list of service descriptions that already have downtimes
                existing_services = []
                for dt in downtimes:
                    extensions = dt.get("extensions", {})
                    service_desc = extensions.get("service_description")
                    if service_desc and service_desc not in existing_services:
                        existing_services.append(service_desc)
                return existing_services
            # For hosts, return ["HOST"] if downtime exists, empty list if not
            return ["HOST"] if len(downtimes) > 0 else []

        except Exception as e:
            self.logger.warning("Error querying existing downtimes: %s", e)
            return []

    async def _verify_downtime_creation(
        self, host_name: str, comment: str, max_retries: int = 5, services: Optional[List[str]] = None
    ) -> bool:
        """
        Verify downtime creation with retry logic (based on working CheckMK example).
        Retry up to max_retries times at 5-second intervals.
        """
        is_service = services and len(services) > 0

        for retry in range(max_retries):
            try:
                # Enhanced query parameters based on working example
                query_filters = []

                # Host name filter
                query_filters.append(f'{{"op": "=", "left": "host_name", "right": "{host_name}"}}')

                # Comment filter
                if comment:
                    query_filters.append(f'{{"op": "=", "left": "comment", "right": "{comment}"}}')

                # Type filter (based on working example pattern)
                if is_service:
                    query_filters.append('{"op": "=", "left": "type", "right": "3"}')  # Service downtime type
                else:
                    query_filters.append('{"op": "=", "left": "type", "right": "2"}')  # Host downtime type

                # Build query
                query = f'{{"op": "and", "expr": [{", ".join(query_filters)}]}}'

                # Enhanced parameters based on working example
                params = {"host_name": host_name, "query": query}

                # Add service-specific parameters
                if is_service:
                    params["downtime_type"] = "service"
                else:
                    params["downtime_type"] = "host"

                # Add site_id if available (based on working example)
                if hasattr(self.client, "config") and hasattr(self.client.config, "site"):
                    params["site_id"] = self.client.config.site

                self.logger.debug("Verification attempt %d/%d with params: %s", retry + 1, max_retries, params)

                # Check if downtime was created
                result = self.client.get("domain-types/downtime/collections/all", params=params)

                if result.get("success"):
                    downtimes = result["data"].get("value", [])
                    if len(downtimes) > 0:
                        self.logger.info("Downtime verification successful after %d attempts", retry + 1)
                        return True

                # Wait 5 seconds before next retry (except on last attempt)
                if retry < max_retries - 1:
                    self.logger.debug("Verification attempt %d failed, retrying in 5 seconds...", retry + 1)
                    await asyncio.sleep(5)

            except Exception as e:
                self.logger.warning("Error during verification attempt %d: %s", retry + 1, e)
                if retry < max_retries - 1:
                    await asyncio.sleep(5)

        self.logger.warning("Downtime verification failed after %d attempts", max_retries)
        return False
