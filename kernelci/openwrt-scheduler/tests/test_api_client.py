"""Tests for KernelCI API client."""

from unittest.mock import AsyncMock, patch

import pytest

from openwrt_scheduler.api_client import APIError, KernelCIClient


class TestAPIError:
    """Tests for APIError exception."""

    def test_create(self):
        """Test creating APIError."""
        error = APIError(404, "Not found", {"detail": "Resource not found"})
        assert error.status_code == 404
        assert error.message == "Not found"
        assert error.details == {"detail": "Resource not found"}

    def test_str(self):
        """Test APIError string representation."""
        error = APIError(500, "Server error")
        assert "500" in str(error)
        assert "Server error" in str(error)


class TestKernelCIClient:
    """Tests for KernelCIClient."""

    @pytest.fixture
    def client(self):
        """Create a KernelCIClient instance."""
        return KernelCIClient(
            base_url="http://api.example.com",
            token="test-token",
        )

    def test_init(self, client):
        """Test client initialization."""
        assert client.base_url == "http://api.example.com"
        assert client.token == "test-token"
        assert client._client is None

    def test_init_strips_trailing_slash(self):
        """Test base_url trailing slash is stripped."""
        client = KernelCIClient(base_url="http://api.example.com/")
        assert client.base_url == "http://api.example.com"

    @pytest.mark.asyncio
    async def test_connect(self, client):
        """Test client connect creates HTTP client."""
        await client.connect()
        assert client._client is not None
        await client.close()

    @pytest.mark.asyncio
    async def test_close(self, client):
        """Test client close."""
        await client.connect()
        await client.close()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_context_manager(self, client):
        """Test client as async context manager."""
        async with client:
            assert client._client is not None
        assert client._client is None

    @pytest.mark.asyncio
    async def test_client_property_raises_when_not_connected(self, client):
        """Test client property raises when not connected."""
        with pytest.raises(RuntimeError, match="not connected"):
            _ = client.client

    @pytest.mark.asyncio
    async def test_create_node(self, client):
        """Test create_node calls correct endpoint."""
        mock_response = {"id": "node-123", "kind": "kbuild"}

        with patch.object(
            client, "_request", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await client.create_node({"kind": "kbuild", "name": "test"})

        assert result["id"] == "node-123"
        client._request.assert_called_once_with(
            "POST", "/latest/nodes", json={"kind": "kbuild", "name": "test"}
        )

    @pytest.mark.asyncio
    async def test_get_node(self, client):
        """Test get_node calls correct endpoint."""
        mock_response = {"id": "node-123", "kind": "kbuild"}

        with patch.object(
            client, "_request", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await client.get_node("node-123")

        assert result["id"] == "node-123"
        client._request.assert_called_once_with("GET", "/latest/nodes/node-123")

    @pytest.mark.asyncio
    async def test_get_node_not_found(self, client):
        """Test get_node returns None for 404."""
        with patch.object(
            client,
            "_request",
            new_callable=AsyncMock,
            side_effect=APIError(404, "Not found"),
        ):
            result = await client.get_node("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_update_node(self, client):
        """Test update_node calls correct endpoint."""
        mock_response = {"id": "node-123", "state": "done"}

        with patch.object(
            client, "_request", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await client.update_node("node-123", {"state": "done"})

        assert result["state"] == "done"
        client._request.assert_called_once_with(
            "PUT", "/latest/nodes/node-123", json={"state": "done"}
        )

    @pytest.mark.asyncio
    async def test_query_nodes(self, client):
        """Test query_nodes with filters."""
        mock_response = [{"id": "node-1"}, {"id": "node-2"}]

        with patch.object(
            client, "_request", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await client.query_nodes(kind="job", state="available", limit=10)

        assert len(result) == 2
        client._request.assert_called_once()
        call_args = client._request.call_args
        assert call_args[1]["params"]["kind"] == "job"
        assert call_args[1]["params"]["state"] == "available"
        assert call_args[1]["params"]["limit"] == 10

    @pytest.mark.asyncio
    async def test_query_nodes_dict_response(self, client):
        """Test query_nodes handles dict response with items."""
        mock_response = {"items": [{"id": "node-1"}], "total": 1}

        with patch.object(
            client, "_request", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await client.query_nodes(kind="job")

        assert len(result) == 1
        assert result[0]["id"] == "node-1"

    @pytest.mark.asyncio
    async def test_create_firmware_node(self, client):
        """Test create_firmware_node creates correct structure."""
        mock_response = {"id": "fw-123"}

        with patch.object(
            client, "create_node", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await client.create_firmware_node(
                name="openwrt-test",
                version="24.10.0",
                target="ath79",
                subtarget="generic",
                profile="tplink_archer-c7-v2",
                source="official",
                artifacts={"sysupgrade": "http://example.com/fw.bin"},
                git_commit="abc123",
            )

        assert result["id"] == "fw-123"

        # Verify node structure
        call_args = client.create_node.call_args[0][0]
        assert call_args["kind"] == "kbuild"
        assert call_args["group"] == "openwrt"
        assert call_args["state"] == "available"
        assert call_args["data"]["kernel_revision"]["branch"] == "openwrt-24.10"
        assert call_args["data"]["target"] == "ath79"

    @pytest.mark.asyncio
    async def test_create_firmware_node_snapshot(self, client):
        """Test create_firmware_node for SNAPSHOT version."""
        mock_response = {"id": "fw-123"}

        with patch.object(
            client, "create_node", new_callable=AsyncMock, return_value=mock_response
        ):
            await client.create_firmware_node(
                name="openwrt-test",
                version="SNAPSHOT",
                target="x86",
                subtarget="64",
                profile="generic",
                source="official",
                artifacts={},
            )

        call_args = client.create_node.call_args[0][0]
        assert call_args["data"]["kernel_revision"]["branch"] == "main"

    @pytest.mark.asyncio
    async def test_create_test_job(self, client):
        """Test create_test_job creates correct structure."""
        mock_parent = {
            "id": "fw-123",
            "data": {
                "kernel_revision": {
                    "tree": "openwrt",
                    "branch": "main",
                    "commit": "abc123",
                }
            },
        }
        mock_response = {"id": "job-456"}

        with patch.object(
            client, "get_node", new_callable=AsyncMock, return_value=mock_parent
        ):
            with patch.object(
                client,
                "create_node",
                new_callable=AsyncMock,
                return_value=mock_response,
            ):
                result = await client.create_test_job(
                    firmware_node_id="fw-123",
                    device_type="test-device",
                    test_plan="base",
                    tests=["test_boot", "test_network"],
                    timeout=1800,
                )

        assert result["id"] == "job-456"

        # Verify node structure
        call_args = client.create_node.call_args[0][0]
        assert call_args["kind"] == "job"
        assert call_args["parent"] == "fw-123"
        assert call_args["state"] == "available"
        assert call_args["data"]["device_type"] == "test-device"
        assert call_args["data"]["runtime"] == "labgrid"
        assert call_args["data"]["kernel_revision"]["branch"] == "main"

    @pytest.mark.asyncio
    async def test_claim_job(self, client):
        """Test claim_job updates job state."""
        mock_response = {"id": "job-123", "state": "running"}

        with patch.object(
            client, "update_node", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await client.claim_job(
                job_id="job-123",
                lab_name="test-lab",
                device_id="device-01",
            )

        assert result["state"] == "running"
        call_args = client.update_node.call_args
        assert call_args[0][1]["state"] == "running"
        assert call_args[0][1]["data"]["lab_name"] == "test-lab"

    @pytest.mark.asyncio
    async def test_complete_job(self, client):
        """Test complete_job updates job and creates test nodes."""
        mock_job = {"id": "job-123", "state": "done"}

        with patch.object(
            client, "update_node", new_callable=AsyncMock, return_value=mock_job
        ):
            with patch.object(
                client, "create_node", new_callable=AsyncMock, return_value={}
            ):
                with patch.object(
                    client, "get_node", new_callable=AsyncMock, return_value=mock_job
                ):
                    await client.complete_job(
                        job_id="job-123",
                        result="pass",
                        test_results=[
                            {"name": "test_boot", "status": "pass", "duration": 1.0},
                            {"name": "test_fail", "status": "fail", "duration": 0.5},
                        ],
                        log_url="http://example.com/logs",
                    )

        # Job should be updated
        update_call = client.update_node.call_args
        assert update_call[0][1]["state"] == "done"
        assert update_call[0][1]["result"] == "pass"

        # Test nodes should be created
        assert client.create_node.call_count == 2

    @pytest.mark.asyncio
    async def test_get_pending_jobs(self, client):
        """Test get_pending_jobs queries for available jobs."""
        mock_response = [{"id": "job-1"}, {"id": "job-2"}]

        with patch.object(
            client, "query_nodes", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await client.get_pending_jobs(device_type="test-device", limit=5)

        assert len(result) == 2
        client.query_nodes.assert_called_once()
        call_kwargs = client.query_nodes.call_args[1]
        assert call_kwargs["kind"] == "job"
        assert call_kwargs["state"] == "available"
