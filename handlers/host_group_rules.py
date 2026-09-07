"""
Specialized handler for host group and contact group rules
"""

from typing import Any, Dict, List, Tuple

from api.exceptions import CheckMKError
from handlers.base import BaseHandler


class HostGroupRulesHandler(BaseHandler):
    """Handle host grouping and contact assignment rules"""

    async def handle(self, tool_name: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Handle host group rule tool calls"""

        try:
            if tool_name == "vibemk_find_host_grouping_rulesets":
                return await self._find_host_grouping_rulesets(arguments)
            if tool_name == "vibemk_create_host_contactgroup_rule":
                return await self._create_host_contactgroup_rule(arguments)
            if tool_name == "vibemk_create_host_hostgroup_rule":
                return await self._create_host_hostgroup_rule(arguments)
            if tool_name == "vibemk_get_example_rule_structures":
                return await self._get_example_rule_structures(arguments)
            return self.error_response("Unknown tool", f"Tool '{tool_name}' is not supported")

        except CheckMKError as e:
            return self.error_response("CheckMK API Error", str(e))
        except Exception as e:
            self.logger.exception("Error in %s", tool_name)
            return self.error_response("Unexpected Error", str(e))

    async def _find_host_grouping_rulesets(self, _arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Find all rulesets related to host grouping and contact assignment"""

        results = ["🔍 **Host Grouping and Contact Assignment Rulesets**\\n"]

        all_rulesets, search_errors = self._search_grouping_rulesets()
        results.extend(search_errors)

        for header, bucket in self._categorize_rulesets(all_rulesets):
            if bucket:
                results.append(header)
                for ruleset_id, info in bucket.items():
                    results.append(f"   • **{ruleset_id}**: {info['title']}")

        results.append(f"\\n📊 **Summary:** Found {len(all_rulesets)} relevant rulesets")

        return [{"type": "text", "text": "\\n".join(results)}]

    def _search_grouping_rulesets(self) -> Tuple[Dict[str, Dict[str, str]], List[str]]:
        """Search rulesets for host-grouping-related terms.

        Returns the matching rulesets keyed by id, plus any per-search-term error
        messages. This mirrors the original inline try/except exactly: an error
        raised while searching or filtering for one term is recorded and the loop
        moves on to the next term, rather than aborting the whole search.
        """
        search_terms = ["contact", "group", "host", "notification", "assignment"]
        all_rulesets: Dict[str, Dict[str, str]] = {}
        errors: List[str] = []

        for search_term in search_terms:
            try:
                result = self.client.get("domain-types/ruleset/collections/all", params={"search": search_term})

                if result.get("success"):
                    rulesets = result["data"].get("value", [])

                    for ruleset in rulesets:
                        if isinstance(ruleset, dict):
                            ruleset_id = ruleset.get("id", "Unknown")
                            title = ruleset.get("title", "No title")

                            # Filter for host-related grouping rules
                            if any(
                                keyword in ruleset_id.lower()
                                for keyword in ["host", "contact", "group", "notification"]
                            ):
                                all_rulesets[ruleset_id] = {
                                    "title": title,
                                    "id": ruleset_id,
                                    "search_term": search_term,
                                }
            except Exception as e:
                errors.append(f"Search for '{search_term}' failed: {e}")

        return all_rulesets, errors

    @staticmethod
    def _categorize_rulesets(
        all_rulesets: Dict[str, Dict[str, str]],
    ) -> List[Tuple[str, Dict[str, Dict[str, str]]]]:
        """Group found rulesets into the four display buckets, in display order."""
        contact_rules: Dict[str, Dict[str, str]] = {}
        host_group_rules: Dict[str, Dict[str, str]] = {}
        notification_rules: Dict[str, Dict[str, str]] = {}
        other_rules: Dict[str, Dict[str, str]] = {}

        for ruleset_id, info in all_rulesets.items():
            if "contact" in ruleset_id.lower():
                contact_rules[ruleset_id] = info
            elif "hostgroup" in ruleset_id.lower() or "host_group" in ruleset_id.lower():
                host_group_rules[ruleset_id] = info
            elif "notification" in ruleset_id.lower():
                notification_rules[ruleset_id] = info
            else:
                other_rules[ruleset_id] = info

        return [
            ("\\n📞 **Contact Group Assignment Rules:**", contact_rules),
            ("\\n🏠 **Host Group Assignment Rules:**", host_group_rules),
            ("\\n📨 **Notification Rules:**", notification_rules),
            ("\\n🔧 **Other Related Rules:**", other_rules),
        ]

    async def _create_host_contactgroup_rule(self, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Create a rule to assign contact groups to hosts using the corrected format"""
        contact_groups = arguments.get("contact_groups", [])
        host_conditions = arguments.get("host_conditions", {})
        comment = arguments.get("comment", "Host contact group assignment")
        folder = arguments.get("folder", "/")

        if not contact_groups:
            return self.error_response("Missing parameter", "contact_groups list is required")

        # Use the working ruleset name we discovered
        working_ruleset = "host_contactgroups"

        # Convert folder path: "/" -> "~", "/hosts/linux" -> "~hosts~linux"
        if folder.startswith("/"):
            api_folder = "~" + folder[1:].replace("/", "~") if folder != "/" else "~"
        else:
            api_folder = "~" + folder.replace("/", "~")

        # Format contact groups as single string if only one, otherwise as Python list string
        if isinstance(contact_groups, list):
            if len(contact_groups) == 1:
                # Single contact group -> use as string
                value_raw = f"'{contact_groups[0]}'"
            else:
                # Multiple contact groups -> use Python list format
                formatted_groups = "', '".join(contact_groups)
                value_raw = f"['{formatted_groups}']"
        else:
            # Already a string
            value_raw = f"'{contact_groups}'"

        # Build rule data structure using the corrected format we discovered
        rule_data: Dict[str, Any] = {
            "properties": {"disabled": False},
            "value_raw": value_raw,
            "conditions": host_conditions if host_conditions else {},
            "ruleset": working_ruleset,
            "folder": api_folder,
        }

        # Add comment to properties if provided
        if comment:
            rule_data["properties"]["comment"] = comment

        try:
            result = self.client.post("domain-types/rule/collections/all", data=rule_data)

            if result.get("success"):
                rule_id = result["data"].get("id", "unknown")
                conditions_text = host_conditions if host_conditions else "None (applies to all hosts)"
                return [
                    {
                        "type": "text",
                        "text": (
                            f"✅ **Host Contact Group Rule Created Successfully**\\n\\n"
                            f"Ruleset: {working_ruleset}\\n"
                            f"Rule ID: {rule_id}\\n"
                            f"Contact Groups: {contact_groups}\\n"
                            f"Folder: {folder}\\n"
                            f"Comment: {comment}\\n\\n"
                            f"📝 **Conditions:** {conditions_text}\\n\\n"
                            f"⚠️ **Remember to activate changes!**"
                        ),
                    }
                ]
            error_data = result.get("data", {})
            return [
                {
                    "type": "text",
                    "text": (
                        f"❌ **Rule Creation Failed**\\n\\n"
                        f"Ruleset: {working_ruleset}\\n"
                        f"Error: {error_data.get('title', 'Unknown error')}\\n"
                        f"Details: {error_data.get('detail', '')}\\n\\n"
                        f"**Debug - Rule Data:** {rule_data}"
                    ),
                }
            ]
        except Exception as e:
            return self.error_response("Rule creation failed", f"Could not create contact group rule: {e!s}")

    async def _create_host_hostgroup_rule(self, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Create a rule to assign hosts to host groups"""
        host_groups = arguments.get("host_groups", [])
        host_conditions = arguments.get("host_conditions", {})
        comment = arguments.get("comment", "Host group assignment")
        folder = arguments.get("folder", "/")

        if not host_groups:
            return self.error_response("Missing parameter", "host_groups list is required")

        # Find the correct ruleset for host group assignment
        hostgroup_ruleset_candidates = ["host_groups", "hostgroups", "host_group_assignment", "host_grouping"]

        working_ruleset = None

        for candidate in hostgroup_ruleset_candidates:
            try:
                result = self.client.get(f"objects/ruleset/{candidate}")
                if result.get("success"):
                    working_ruleset = candidate
                    break
            except Exception:
                continue

        if not working_ruleset:
            return [
                {
                    "type": "text",
                    "text": (
                        "❌ **Host Group Ruleset Not Found**\\n\\n"
                        "Could not find a working ruleset for host group assignment.\\n\\n"
                        "**Tried rulesets:**\\n"
                        + "\\n".join([f"• {rs}" for rs in hostgroup_ruleset_candidates])
                        + "\\n\\n"
                        "**Recommendation:**\\n"
                        "1. Use 'find_host_grouping_rulesets' to find available rulesets\\n"
                        "2. Check existing host group rules in CheckMK GUI"
                    ),
                }
            ]

        # Build rule data structure for host groups according to CMDBsyncer working implementation
        rule_data = {
            "ruleset": working_ruleset,
            "folder": folder,
            "properties": {"disabled": False, "description": "Host group assignment via vibeMK", "comment": comment},
            "value_raw": host_groups,
        }

        # Add host conditions if provided (flat structure, not under extensions)
        if host_conditions:
            rule_data["conditions"] = host_conditions

        try:
            result = self.client.post("domain-types/rule/collections/all", data=rule_data)

            if result.get("success"):
                conditions_text = host_conditions if host_conditions else "None (applies to all hosts)"
                return [
                    {
                        "type": "text",
                        "text": (
                            f"✅ **Host Group Assignment Rule Created**\\n\\n"
                            f"Ruleset: {working_ruleset}\\n"
                            f"Host Groups: {', '.join(host_groups)}\\n"
                            f"Folder: {folder}\\n"
                            f"Comment: {comment}\\n\\n"
                            f"📝 **Conditions:** {conditions_text}\\n\\n"
                            f"⚠️ **Remember to activate changes!**"
                        ),
                    }
                ]
            error_data = result.get("data", {})
            return [
                {
                    "type": "text",
                    "text": (
                        f"❌ **Rule Creation Failed**\\n\\n"
                        f"Ruleset: {working_ruleset}\\n"
                        f"Error: {error_data.get('title', 'Unknown error')}\\n"
                        f"Details: {error_data.get('detail', '')}\\n\\n"
                        f"**Rule Data:** {rule_data}"
                    ),
                }
            ]
        except Exception as e:
            return self.error_response("Rule creation failed", f"Could not create host group rule: {e!s}")

    async def _get_example_rule_structures(self, _arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Show example rule structures for host grouping"""

        examples = [
            "📚 **Example Rule Structures for Host Grouping**\\n",
            "\\n🔧 **1. Host Contact Group Assignment (Swagger Format)**",
            "```json",
            "{",
            '  "extensions": {',
            '    "ruleset": "host_contactgroups",',
            '    "folder": "/",',
            '    "properties": {',
            '      "comment": "Critical hosts to admin teams"',
            "    },",
            '    "value_raw": ["admins", "network-team"],',
            '    "conditions": {',
            '      "host_tags": [',
            "        {",
            '          "key": "criticality",',
            '          "operator": "is",',
            '          "value": "critical"',
            "        }",
            "      ]",
            "    }",
            "  }",
            "}",
            "```",
            "\\n🏠 **2. Host Group Assignment (Swagger Format)**",
            "```json",
            "{",
            '  "extensions": {',
            '    "ruleset": "host_groups",',
            '    "folder": "/",',
            '    "properties": {',
            '      "comment": "Database servers to appropriate groups"',
            "    },",
            '    "value_raw": ["database-servers", "production"],',
            '    "conditions": {',
            '      "host_name": {',
            '        "match_on": ["db.*"],',
            '        "operator": "match_regex"',
            "      }",
            "    }",
            "  }",
            "}",
            "```",
            "\\n🔍 **3. Advanced Host Conditions (Swagger Format)**",
            "```json",
            "{",
            '  "extensions": {',
            '    "ruleset": "host_contactgroups",',
            '    "folder": "/",',
            '    "value_raw": ["web-admins"],',
            '    "conditions": {',
            '      "host_name": {',
            '        "match_on": ["web[0-9]+"],',
            '        "operator": "match_regex"',
            "      },",
            '      "host_tags": [',
            "        {",
            '          "key": "environment",',
            '          "operator": "is",',
            '          "value": "production"',
            "        },",
            "        {",
            '          "key": "location",',
            '          "operator": "is",',
            '          "value": "datacenter-1"',
            "        }",
            "      ],",
            '      "host_label_groups": [',
            "        {",
            '          "label_group": [',
            "            {",
            '              "operator": "and",',
            '              "label": "application:wordpress"',
            "            }",
            "          ]",
            "        }",
            "      ]",
            "    }",
            "  }",
            "}",
            "```",
            "\\n💡 **4. Simple All-Hosts Rule (Swagger Format)**",
            "```json",
            "{",
            '  "extensions": {',
            '    "ruleset": "host_contactgroups",',
            '    "folder": "/",',
            '    "properties": {',
            '      "comment": "Default contact group for all hosts"',
            "    },",
            '    "value_raw": ["monitoring-team"]',
            "  }",
            "}",
            "```",
            "\\n🎯 **Usage Tips (Updated for Swagger Format):**",
            "• All rule data must be under `extensions` object",
            "• Use `conditions` with proper operator format (match_on, operator)",
            "• `value_raw` contains the actual rule values (contact groups, host groups)",
            "• Empty conditions = rule applies to all hosts",
            "• Multiple groups can be assigned in one rule",
            "• Use `folder_index` for rule positioning (0 = top)",
            "• Remember to activate changes after creating rules",
        ]

        return [{"type": "text", "text": "\\n".join(examples)}]
