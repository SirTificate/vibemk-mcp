"""
Tool-to-handler registry for vibeMK

Owns the mapping from an MCP tool name to the handler that serves it. Knows
nothing about the JSON-RPC protocol or about transport.
"""

from typing import Any, Dict, FrozenSet, Optional

from api import CheckMKClient
from handlers.acknowledgements import AcknowledgementHandler
from handlers.configuration import ConfigurationHandler
from handlers.connection import ConnectionHandler
from handlers.debug import DebugHandler
from handlers.discovery import DiscoveryHandler
from handlers.downtimes import DowntimeHandler
from handlers.folders import FolderHandler
from handlers.groups import GroupsHandler
from handlers.host_group_rules import HostGroupRulesHandler
from handlers.hosts import HostHandler
from handlers.metrics import MetricsHandler
from handlers.monitoring import MonitoringHandler
from handlers.notifications import NotificationHandler
from handlers.passwords import PasswordsHandler
from handlers.rules import RulesHandler
from handlers.rulesets import RulesetsHandler
from handlers.service_groups import ServiceGroupHandler
from handlers.services import ServiceHandler
from handlers.tags import TagsHandler
from handlers.timeperiods import TimePeriodsHandler
from handlers.user_roles import UserRolesHandler
from handlers.users import UserHandler


class ToolRegistry:
    """Maps MCP tool names to the handler instances that serve them."""

    # Dict[str, Any] rather than Dict[str, BaseHandler]: AcknowledgementHandler and
    # DiscoveryHandler do not inherit BaseHandler (unlike the other 20 handlers), so
    # mypy rejects the narrower annotation. Fixing that inheritance belongs to a later
    # task in this hardening plan, not to this extraction.
    def __init__(self, handlers: Dict[str, Any]) -> None:
        self._handlers = handlers

    @classmethod
    def from_client(cls, client: CheckMKClient) -> "ToolRegistry":
        """Build every handler against one CheckMK client."""
        connection_handler = ConnectionHandler(client)
        host_handler = HostHandler(client)
        service_handler = ServiceHandler(client)
        monitoring_handler = MonitoringHandler(client)
        notification_handler = NotificationHandler(client)
        configuration_handler = ConfigurationHandler(client)
        folder_handler = FolderHandler(client)
        metrics_handler = MetricsHandler(client)
        user_handler = UserHandler(client)
        user_roles_handler = UserRolesHandler(client)
        groups_handler = GroupsHandler(client)
        rules_handler = RulesHandler(client)
        rulesets_handler = RulesetsHandler(client)
        tags_handler = TagsHandler(client)
        timeperiods_handler = TimePeriodsHandler(client)
        passwords_handler = PasswordsHandler(client)
        debug_handler = DebugHandler(client)
        host_group_rules_handler = HostGroupRulesHandler(client)
        downtime_handler = DowntimeHandler(client)
        acknowledgement_handler = AcknowledgementHandler(client)
        discovery_handler = DiscoveryHandler(client)
        service_group_handler = ServiceGroupHandler(client)

        return cls(
            {
                # Connection tools
                "vibemk_debug_checkmk_connection": connection_handler,
                "vibemk_debug_url_detection": connection_handler,
                "vibemk_test_direct_url": connection_handler,
                "vibemk_test_all_endpoints": connection_handler,
                "vibemk_get_checkmk_version": connection_handler,
                # Host management tools
                "vibemk_get_checkmk_hosts": host_handler,
                "vibemk_get_host_status": host_handler,
                "vibemk_get_host_details": host_handler,
                "vibemk_get_host_config": host_handler,
                "vibemk_create_host": host_handler,
                "vibemk_bulk_create_hosts": host_handler,
                "vibemk_update_host": host_handler,
                "vibemk_delete_host": host_handler,
                "vibemk_move_host": host_handler,
                "vibemk_bulk_update_hosts": host_handler,
                "vibemk_create_cluster_host": host_handler,
                "vibemk_validate_host_config": host_handler,
                "vibemk_compare_host_states": host_handler,
                "vibemk_get_host_effective_attributes": host_handler,
                # Service management tools
                "vibemk_get_checkmk_services": service_handler,
                "vibemk_get_service_status": service_handler,
                # Monitoring and problems
                "vibemk_get_current_problems": monitoring_handler,
                "vibemk_acknowledge_problem": monitoring_handler,
                "vibemk_schedule_downtime": monitoring_handler,
                "vibemk_get_downtimes": monitoring_handler,
                "vibemk_reschedule_check": monitoring_handler,
                "vibemk_get_comments": monitoring_handler,
                "vibemk_add_comment": monitoring_handler,
                # Configuration management
                "vibemk_activate_changes": configuration_handler,
                "vibemk_get_pending_changes": configuration_handler,
                # Folder management
                "vibemk_get_folders": folder_handler,
                "vibemk_create_folder": folder_handler,
                "vibemk_delete_folder": folder_handler,
                "vibemk_update_folder": folder_handler,
                "vibemk_move_folder": folder_handler,
                "vibemk_get_folder_hosts": folder_handler,
                # Metrics and performance data (RRD access)
                "vibemk_get_host_metrics": metrics_handler,
                "vibemk_get_service_metrics": metrics_handler,
                "vibemk_get_custom_graph": metrics_handler,
                "vibemk_search_metrics": metrics_handler,
                "vibemk_list_available_metrics": metrics_handler,
                # User management
                "vibemk_get_users": user_handler,
                "vibemk_create_user": user_handler,
                "vibemk_update_user": user_handler,
                "vibemk_delete_user": user_handler,
                "vibemk_get_contact_groups": user_handler,
                "vibemk_create_contact_group": user_handler,
                "vibemk_update_contact_group": user_handler,
                "vibemk_delete_contact_group": user_handler,
                "vibemk_add_user_to_group": user_handler,
                "vibemk_remove_user_from_group": user_handler,
                # User roles management
                "vibemk_list_user_roles": user_roles_handler,
                "vibemk_show_user_role": user_roles_handler,
                "vibemk_create_user_role": user_roles_handler,
                "vibemk_update_user_role": user_roles_handler,
                "vibemk_delete_user_role": user_roles_handler,
                # Group management (host and service groups)
                "vibemk_get_host_groups": groups_handler,
                "vibemk_create_host_group": groups_handler,
                "vibemk_update_host_group": groups_handler,
                "vibemk_delete_host_group": groups_handler,
                "vibemk_get_service_groups": groups_handler,
                # Rule management
                "vibemk_get_rulesets": rules_handler,
                "vibemk_get_ruleset": rules_handler,
                "vibemk_create_rule": rules_handler,
                "vibemk_update_rule": rules_handler,
                "vibemk_delete_rule": rules_handler,
                "vibemk_move_rule": rules_handler,
                # Ruleset discovery and search
                "vibemk_search_rulesets": rulesets_handler,
                "vibemk_show_ruleset": rulesets_handler,
                "vibemk_list_rulesets": rulesets_handler,
                # Tag management (host tags)
                "vibemk_get_host_tags": tags_handler,
                "vibemk_create_host_tag": tags_handler,
                "vibemk_update_host_tag": tags_handler,
                "vibemk_delete_host_tag": tags_handler,
                # Time period management
                "vibemk_get_timeperiods": timeperiods_handler,
                "vibemk_create_timeperiod": timeperiods_handler,
                "vibemk_update_timeperiod": timeperiods_handler,
                "vibemk_delete_timeperiod": timeperiods_handler,
                # Password management
                "vibemk_get_passwords": passwords_handler,
                "vibemk_create_password": passwords_handler,
                "vibemk_update_password": passwords_handler,
                "vibemk_delete_password": passwords_handler,
                # Notification rules
                "vibemk_get_notification_rules": notification_handler,
                "vibemk_get_notification_rule": notification_handler,
                "vibemk_create_notification_rule": notification_handler,
                "vibemk_update_notification_rule": notification_handler,
                "vibemk_delete_notification_rule": notification_handler,
                # Debug tools
                "vibemk_debug_api_endpoints": debug_handler,
                "vibemk_debug_permissions": debug_handler,
                # Host group rules
                "vibemk_find_host_grouping_rulesets": host_group_rules_handler,
                "vibemk_create_host_contactgroup_rule": host_group_rules_handler,
                "vibemk_create_host_hostgroup_rule": host_group_rules_handler,
                "vibemk_get_example_rule_structures": host_group_rules_handler,
                # Downtime management
                "vibemk_schedule_host_downtime": downtime_handler,
                "vibemk_schedule_service_downtime": downtime_handler,
                "vibemk_list_downtimes": downtime_handler,
                "vibemk_get_active_downtimes": downtime_handler,
                "vibemk_delete_downtime": downtime_handler,
                "vibemk_check_host_downtime_status": downtime_handler,
                # Acknowledgement management
                "vibemk_acknowledge_host_problem": acknowledgement_handler,
                "vibemk_acknowledge_service_problem": acknowledgement_handler,
                "vibemk_list_acknowledgements": acknowledgement_handler,
                "vibemk_remove_acknowledgement": acknowledgement_handler,
                # Discovery management
                "vibemk_start_service_discovery": discovery_handler,
                "vibemk_start_bulk_discovery": discovery_handler,
                "vibemk_get_discovery_status": discovery_handler,
                "vibemk_get_bulk_discovery_status": discovery_handler,
                "vibemk_wait_for_discovery": discovery_handler,
                "vibemk_get_discovery_background_job": discovery_handler,
                # Service group management
                "vibemk_create_service_group": service_group_handler,
                "vibemk_list_service_groups": service_group_handler,
                "vibemk_get_service_group": service_group_handler,
                "vibemk_update_service_group": service_group_handler,
                "vibemk_delete_service_group": service_group_handler,
                "vibemk_bulk_create_service_groups": service_group_handler,
                "vibemk_bulk_update_service_groups": service_group_handler,
                "vibemk_bulk_delete_service_groups": service_group_handler,
            }
        )

    def handler_for(self, tool_name: str) -> Optional[Any]:
        """Return the handler for a tool, or None when it is not registered."""
        return self._handlers.get(tool_name)

    def tool_names(self) -> FrozenSet[str]:
        """Every tool name this registry can route."""
        return frozenset(self._handlers)
