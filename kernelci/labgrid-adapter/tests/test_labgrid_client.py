"""Tests for labgrid coordinator client."""

import pytest

from labgrid_kci_adapter.labgrid_client import LabgridClient, Place


class TestPlace:
    """Tests for Place dataclass."""

    def test_device_type_from_tags(self):
        """Test extracting device type from tags."""
        place = Place(
            name="aparcar-openwrt_one",
            acquired=False,
            tags={"device_type": "openwrt_one"},
        )
        assert place.device_type == "openwrt_one"

    def test_device_type_from_name_simple(self):
        """Test extracting device type from simple name."""
        place = Place(name="aparcar-openwrt_one", acquired=False)
        assert place.device_type == "openwrt_one"

    def test_device_type_from_name_with_instance(self):
        """Test extracting device type from name with instance number."""
        place = Place(name="aparcar-openwrt_one-2", acquired=False)
        assert place.device_type == "openwrt_one"

    def test_device_type_from_name_complex(self):
        """Test extracting device type from complex name."""
        place = Place(name="lab-tplink_archer-c7-v2-1", acquired=False)
        assert place.device_type == "tplink_archer-c7-v2"

    def test_device_type_tags_override_name(self):
        """Test that tags override name-based extraction."""
        place = Place(
            name="aparcar-wrong_name",
            acquired=False,
            tags={"device_type": "correct_type"},
        )
        assert place.device_type == "correct_type"


class TestLabgridClientParsing:
    """Tests for LabgridClient output parsing."""

    def test_parse_places_output_simple(self):
        """Test parsing simple places output."""
        output = """Place 'aparcar-openwrt_one':
  acquired:
  tags:
    device_type: openwrt_one
"""
        client = LabgridClient()
        places = client._parse_places_output(output)

        assert len(places) == 1
        assert "aparcar-openwrt_one" in places
        place = places["aparcar-openwrt_one"]
        assert place.name == "aparcar-openwrt_one"
        assert place.acquired is False
        assert place.tags == {"device_type": "openwrt_one"}

    def test_parse_places_output_acquired(self):
        """Test parsing places output with acquired place."""
        output = """Place 'aparcar-openwrt_one':
  acquired: user/hostname
  tags:
    device_type: openwrt_one
"""
        client = LabgridClient()
        places = client._parse_places_output(output)

        place = places["aparcar-openwrt_one"]
        assert place.acquired is True
        assert place.acquired_by == "user/hostname"

    def test_parse_places_output_multiple(self):
        """Test parsing multiple places."""
        output = """Place 'aparcar-openwrt_one':
  acquired:
  tags:
    device_type: openwrt_one
Place 'aparcar-openwrt_one-2':
  acquired: user/host
  tags:
    device_type: openwrt_one
Place 'leinelab-archer-c7':
  acquired:
  tags:
    device_type: tplink_archer-c7-v2
"""
        client = LabgridClient()
        places = client._parse_places_output(output)

        assert len(places) == 3
        assert places["aparcar-openwrt_one"].acquired is False
        assert places["aparcar-openwrt_one-2"].acquired is True
        assert places["leinelab-archer-c7"].device_type == "tplink_archer-c7-v2"

    def test_parse_places_output_multiple_tags(self):
        """Test parsing places with multiple tags."""
        output = """Place 'aparcar-openwrt_one':
  acquired:
  tags:
    device_type: openwrt_one
    lab: aparcar
    features: wifi,ethernet
"""
        client = LabgridClient()
        places = client._parse_places_output(output)

        place = places["aparcar-openwrt_one"]
        assert place.tags["device_type"] == "openwrt_one"
        assert place.tags["lab"] == "aparcar"
        assert place.tags["features"] == "wifi,ethernet"

    def test_parse_places_output_empty(self):
        """Test parsing empty output."""
        client = LabgridClient()
        places = client._parse_places_output("")
        assert len(places) == 0

    def test_parse_places_output_no_tags(self):
        """Test parsing place without tags section."""
        output = """Place 'aparcar-openwrt_one':
  acquired:
"""
        client = LabgridClient()
        places = client._parse_places_output(output)

        place = places["aparcar-openwrt_one"]
        assert place.tags is None or place.tags == {}


class TestLabgridClientFiltering:
    """Tests for place filtering methods."""

    @pytest.fixture
    def client_with_places(self):
        """Create client with cached places."""
        client = LabgridClient()
        client._places_cache = {
            "aparcar-openwrt_one": Place(
                name="aparcar-openwrt_one",
                acquired=False,
                tags={"device_type": "openwrt_one"},
            ),
            "aparcar-openwrt_one-2": Place(
                name="aparcar-openwrt_one-2",
                acquired=True,
                acquired_by="user/host",
                tags={"device_type": "openwrt_one"},
            ),
            "aparcar-archer-c7": Place(
                name="aparcar-archer-c7",
                acquired=False,
                tags={"device_type": "tplink_archer-c7-v2"},
            ),
        }
        client._cache_time = float("inf")  # Never expire
        return client

    @pytest.mark.asyncio
    async def test_get_places_by_device_type(self, client_with_places):
        """Test filtering places by device type."""
        places = await client_with_places.get_places_by_device_type("openwrt_one")
        assert len(places) == 2
        assert all(p.device_type == "openwrt_one" for p in places)

    @pytest.mark.asyncio
    async def test_get_available_places(self, client_with_places):
        """Test getting only available (not acquired) places."""
        places = await client_with_places.get_available_places("openwrt_one")
        assert len(places) == 1
        assert places[0].name == "aparcar-openwrt_one"
        assert places[0].acquired is False

    @pytest.mark.asyncio
    async def test_count_available(self, client_with_places):
        """Test counting available places."""
        count = await client_with_places.count_available("openwrt_one")
        assert count == 1

        count = await client_with_places.count_available("tplink_archer-c7-v2")
        assert count == 1

        count = await client_with_places.count_available("nonexistent")
        assert count == 0
