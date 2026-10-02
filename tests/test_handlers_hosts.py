"""
Tests for Host Handler
"""

import pytest

from api.exceptions import CheckMKAPIError
from handlers.hosts import HostHandler
from tests.test_live_smoke import host_names


class TestHostHandler:
    """Test Host Handler functionality"""

    @pytest.fixture
    def host_handler(self, mock_checkmk_client):
        """Create host handler with mocked client"""
        return HostHandler(mock_checkmk_client)

    @pytest.mark.asyncio
    async def test_get_checkmk_hosts_success(self, host_handler, mock_checkmk_responses):
        """Test successful hosts retrieval"""
        # Setup mock
        host_handler.client.get.return_value = mock_checkmk_responses["hosts"]

        # Execute
        result = await host_handler.handle("vibemk_get_checkmk_hosts", {})

        # Verify
        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert "test-server-01" in result[0]["text"]
        assert "🖥️" in result[0]["text"]  # Actual emoji used in implementation

    @pytest.mark.asyncio
    async def test_get_checkmk_hosts_with_filter(self, host_handler, mock_checkmk_responses):
        """Test hosts retrieval with name filter"""
        # Setup mock
        host_handler.client.get.return_value = mock_checkmk_responses["hosts"]

        # Execute with filter
        result = await host_handler.handle("vibemk_get_checkmk_hosts", {"host_name_filter": "test-server"})

        # Verify
        assert len(result) == 1
        assert "test-server-01" in result[0]["text"]
        # Hosts come from the Monitoring collection: the Setup/WATO host_config
        # endpoint returns an empty list for non-administrative accounts. State is
        # only included when requested explicitly, hence the columns parameter.
        host_handler.client.get.assert_called_with(
            "domain-types/host/collections/all", params={"columns": ["name", "state"]}
        )

    @pytest.mark.asyncio
    async def test_get_host_status_success(self, host_handler, mock_checkmk_responses):
        """Test successful host status retrieval"""
        # Setup mock
        host_handler.client.get.return_value = mock_checkmk_responses["host_status"]

        # Execute
        result = await host_handler.handle("vibemk_get_host_status", {"host_name": "test-server-01"})

        # Verify
        assert len(result) == 1
        assert "🟢 **UP**" in result[0]["text"]
        assert "test-server-01" in result[0]["text"]
        assert "Hard State: 0" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_get_host_status_down(self, host_handler):
        """Test host status when host is DOWN"""
        # Setup mock for DOWN host
        down_response = {
            "success": True,
            "data": {
                "extensions": {
                    "name": "test-server-01",
                    "state": 1,
                    "hard_state": 1,
                    "state_type": 1,
                    "plugin_output": "CRITICAL - Host unreachable",
                    "last_check": 1640995200,
                    "has_been_checked": True,
                }
            },
        }
        host_handler.client.get.return_value = down_response

        # Execute
        result = await host_handler.handle("vibemk_get_host_status", {"host_name": "test-server-01"})

        # Verify
        assert "🔴 **DOWN**" in result[0]["text"]
        assert "Hard State: 1" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_create_host_success(self, host_handler):
        """Test successful host creation"""
        # Setup mocks - first check for existing host (should fail), then create succeeds
        check_response = {"success": False, "data": {}}  # Host doesn't exist
        create_response = {"success": True, "data": {"id": "new-test-server"}}

        host_handler.client.get.return_value = check_response
        host_handler.client.post.return_value = create_response

        # Execute
        result = await host_handler.handle(
            "vibemk_create_host",
            {
                "host_name": "new-test-server",
                "folder": "/servers",
                "attributes": {"ipaddress": "192.168.1.101", "alias": "New Test Server"},
            },
        )

        # Verify
        assert len(result) == 1
        assert "✅" in result[0]["text"]
        assert "new-test-server" in result[0]["text"]

        # Verify API calls - first GET to check existence, then POST to create
        host_handler.client.get.assert_called_with("objects/host_config/new-test-server")
        host_handler.client.post.assert_called_once()
        call_args = host_handler.client.post.call_args
        assert call_args[0][0] == "domain-types/host_config/collections/all"
        assert "new-test-server" in str(call_args[1]["data"])

    @pytest.mark.asyncio
    async def test_create_host_missing_parameters(self, host_handler):
        """Test host creation with missing required parameters"""
        # Setup mock for host existence check - host doesn't exist
        host_handler.client.get.return_value = {"success": False, "data": {}}

        # Execute without required parameters. Only exercised for "must not
        # raise" here; the assertions below are against the invalid-parameter
        # call, so the response isn't captured.
        await host_handler.handle(
            "vibemk_create_host",
            {
                "host_name": "new-server"
                # Missing folder and attributes - should use defaults
            },
        )

        # The current implementation provides defaults for folder ("/") and attributes ({})
        # So this should actually succeed, not fail
        # Let's test with completely invalid parameters instead
        result_invalid = await host_handler.handle(
            "vibemk_create_host",
            {
                # Missing host_name which is truly required
                "folder": "/test"
            },
        )

        # Verify error response for missing host_name
        assert len(result_invalid) == 1
        assert "❌" in result_invalid[0]["text"]
        assert "host_name" in result_invalid[0]["text"].lower()

    @pytest.mark.asyncio
    async def test_delete_host_success(self, host_handler):
        """Test successful host deletion"""
        # Setup mock
        delete_response = {"success": True, "data": {}}
        host_handler.client.delete.return_value = delete_response

        # Execute
        result = await host_handler.handle("vibemk_delete_host", {"host_name": "test-server-01"})

        # Verify
        assert len(result) == 1
        assert "✅" in result[0]["text"]
        assert "deleted" in result[0]["text"].lower()

        # Verify API call - handler uses host_config endpoint
        host_handler.client.delete.assert_called_with("objects/host_config/test-server-01")

    @pytest.mark.asyncio
    async def test_move_host_success(self, host_handler):
        """Test successful host move operation"""
        # Setup mock
        move_response = {"success": True, "data": {}}
        host_handler.client.post.return_value = move_response

        # Execute
        result = await host_handler.handle(
            "vibemk_move_host", {"host_name": "test-server-01", "target_folder": "/production/servers"}
        )

        # Verify
        assert len(result) == 1
        assert "✅" in result[0]["text"]
        assert "moved" in result[0]["text"].lower()

    @pytest.mark.asyncio
    async def test_api_error_handling(self, host_handler):
        """Test API error handling"""
        # Setup mock to raise API error
        host_handler.client.get.side_effect = CheckMKAPIError("API Error", 500)

        # Execute
        result = await host_handler.handle("vibemk_get_host_status", {"host_name": "test-server-01"})

        # Verify error handling - handler provides generic failure message, not exact API error
        assert len(result) == 1
        assert "❌" in result[0]["text"]
        assert "Host Status Retrieval Failed" in result[0]["text"]  # Handler's generic error message
        assert "test-server-01" in result[0]["text"]  # Should contain the host name

    @pytest.mark.asyncio
    async def test_host_not_found(self, host_handler, mock_checkmk_responses):
        """Test handling when host is not found"""
        # Setup mock for 404 response
        host_handler.client.get.return_value = mock_checkmk_responses["error_404"]

        # Execute
        result = await host_handler.handle("vibemk_get_host_status", {"host_name": "nonexistent-host"})

        # Verify
        assert len(result) == 1
        assert "❌" in result[0]["text"]
        assert "not found" in result[0]["text"].lower()

    @pytest.mark.asyncio
    async def test_invalid_tool_name(self, host_handler):
        """Test handling of invalid tool names"""
        # Execute with invalid tool name
        result = await host_handler.handle("invalid_tool_name", {})

        # Verify error response
        assert len(result) == 1
        assert "❌" in result[0]["text"]
        assert "Unknown tool" in result[0]["text"]


class TestEffectiveAttributes:
    """CheckMK resolves inheritance itself.

    The handler used to fetch the folder separately and merge by hand, building
    the folder path with "/" — but CheckMK addresses folders with "~", so
    `objects/folder_config//servers/linux` answers 404 and every host in a
    nested folder failed. Verified against 2.4.0p2 CRE, where passing
    effective_attributes=true returns 27 already-resolved attributes.
    """

    @pytest.fixture
    def handler(self, mock_checkmk_client):
        return HostHandler(mock_checkmk_client)

    @pytest.mark.asyncio
    async def test_checkmk_resolves_the_inheritance(self, handler):
        handler.client.get.return_value = {
            "success": True,
            "status": 200,
            "headers": {},
            "data": {
                "extensions": {
                    "folder": "/servers/linux",
                    "attributes": {"alias": "set on the host"},
                    "effective_attributes": {"alias": "set on the host", "site": "inherited from folder"},
                }
            },
        }

        result = await handler.handle("vibemk_get_host_effective_attributes", {"host_name": "web01"})

        text = result[0]["text"]
        assert "inherited from folder" in text
        assert "set on the host" in text

    @pytest.mark.asyncio
    async def test_the_folder_is_not_fetched_separately(self, handler):
        handler.client.get.return_value = {
            "success": True,
            "status": 200,
            "headers": {},
            "data": {"extensions": {"folder": "/servers/linux", "attributes": {}, "effective_attributes": {}}},
        }

        await handler.handle("vibemk_get_host_effective_attributes", {"host_name": "web01"})

        paths = [c.args[0] for c in handler.client.get.call_args_list if c.args]
        assert not [p for p in paths if "folder_config" in p], paths

    @pytest.mark.asyncio
    async def test_checkmk_is_asked_for_the_effective_attributes(self, handler):
        handler.client.get.return_value = {
            "success": True,
            "status": 200,
            "headers": {},
            "data": {"extensions": {"folder": "/", "attributes": {}, "effective_attributes": {}}},
        }

        await handler.handle("vibemk_get_host_effective_attributes", {"host_name": "web01"})

        params = handler.client.get.call_args.kwargs.get("params", {})
        assert params.get("effective_attributes") == "true", params


class TestListingHostsReadsItsArguments:
    """vibemk_get_checkmk_hosts declared `folder` and `effective_attributes`,
    its description promised folder filtering, and the handler's signature was
    `_arguments` — it read neither. The same shape as the `position` bug on
    rule creation: advertised, accepted, ignored, success reported.

    CheckMK serves a folder-scoped listing of its own,
    GET /objects/folder_config/{folder}/collections/hosts, which takes
    effective_attributes. Folder paths go over the wire with ~ separators.
    """

    @pytest.fixture
    def host_handler(self, mock_checkmk_client):
        return HostHandler(mock_checkmk_client)

    @pytest.fixture
    def listing(self, mock_checkmk_client):
        mock_checkmk_client.get.return_value = {
            "success": True,
            "status": 200,
            "headers": {},
            "data": {"value": [{"id": "web01", "extensions": {"name": "web01", "state": 0}}]},
        }
        return mock_checkmk_client

    @pytest.mark.asyncio
    async def test_without_a_folder_the_monitoring_collection_is_used(self, host_handler, listing):
        # Unchanged behaviour: it carries live state, which the Setup view does not.
        await host_handler.handle("vibemk_get_checkmk_hosts", {})

        assert listing.get.call_args.args[0] == "domain-types/host/collections/all"

    @pytest.mark.asyncio
    async def test_a_folder_narrows_the_listing(self, host_handler, listing):
        await host_handler.handle("vibemk_get_checkmk_hosts", {"folder": "/servers/linux"})

        assert listing.get.call_args.args[0] == "objects/folder_config/~servers~linux/collections/hosts"

    @pytest.mark.asyncio
    async def test_the_root_folder_is_addressable(self, host_handler, listing):
        await host_handler.handle("vibemk_get_checkmk_hosts", {"folder": "/"})

        assert listing.get.call_args.args[0] == "objects/folder_config/~/collections/hosts"

    @pytest.mark.asyncio
    async def test_effective_attributes_are_requested_when_asked_for(self, host_handler, listing):
        await host_handler.handle(
            "vibemk_get_checkmk_hosts", {"folder": "/servers/linux", "effective_attributes": True}
        )

        assert listing.get.call_args.kwargs["params"]["effective_attributes"] == "true"

    @pytest.mark.asyncio
    async def test_effective_attributes_are_not_requested_otherwise(self, host_handler, listing):
        await host_handler.handle("vibemk_get_checkmk_hosts", {"folder": "/servers/linux"})

        assert "effective_attributes" not in listing.get.call_args.kwargs.get("params", {})


@pytest.mark.asyncio
async def test_the_live_smoke_test_reads_every_listed_host_name(mock_checkmk_client):
    """The smoke test takes its host from this listing. A CheckMK host name need
    not be a domain name, and when the parser assumed one, every host and service
    check skipped silently on a site without dotted names."""
    mock_checkmk_client.get.return_value = {
        "success": True,
        "data": {
            "value": [
                {"id": "web01", "extensions": {"state": 0}},
                {"id": "db01.example.com", "extensions": {"state": 1}},
            ]
        },
    }

    answer = await HostHandler(mock_checkmk_client).handle("vibemk_get_checkmk_hosts", {})

    assert host_names(answer[0]["text"]) == ["web01", "db01.example.com"]
