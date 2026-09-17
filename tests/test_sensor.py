"""Tests for Mer sensors."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mer.const import DOMAIN
from custom_components.mer.driivz.models import Socket
from tests.helpers import setup_integration


def state_by_unique_id(hass: HomeAssistant, platform: str, unique_id: str):
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id is not None, f"no {platform} entity with unique_id {unique_id}"
    state = hass.states.get(entity_id)
    assert state is not None
    return state


async def test_station_and_socket_sensors(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id

    status = state_by_unique_id(hass, "sensor", f"{eid}_station_6042_status")
    assert status.state == "available"
    assert status.attributes["device_class"] == "enum"
    assert "charging" in status.attributes["options"]
    assert status.name == "Business Durham - NETPark 3 - Explorer 1 Status"

    charging = state_by_unique_id(hass, "sensor", f"{eid}_station_6041_status")
    assert charging.state == "charging"

    identity = state_by_unique_id(hass, "sensor", f"{eid}_station_6042_identity_key")
    assert identity.state == "MER-FS-AD00137"
    registry_entry = er.async_get(hass).async_get(identity.entity_id)
    assert (
        registry_entry is not None
        and registry_entry.entity_category == er.EntityCategory.DIAGNOSTIC
    )

    left = state_by_unique_id(hass, "sensor", f"{eid}_socket_11241_status")
    assert left.state == "charging"
    assert left.name == "Business Durham - NETPark 4 - Explorer 2 Left status"
    right = state_by_unique_id(hass, "sensor", f"{eid}_socket_11242_status")
    assert right.state == "available"

    price = state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_price")
    assert price.state == "0.0"
    assert price.attributes["unit_of_measurement"] == "GBP/kWh"

    power = state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_max_power")
    assert power.state == "7.0"
    assert power.attributes["unit_of_measurement"] == "kW"
    assert power.attributes["device_class"] == "power"


async def test_unknown_status_maps_to_unknown_option(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    stations = mock_client.find_stations_by_ids.return_value
    weird = stations[0]
    stations[0] = type(weird).from_dict(
        {
            "id": weird.id,
            "caption": weird.caption,
            "stationStatusId": "SOMETHING_NEW",
            "stationSockets": [{"id": 11243, "socketStatusId": "WEIRD"}, {"id": 11244}],
        }
    )
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    assert state_by_unique_id(hass, "sensor", f"{eid}_station_6042_status").state == "unknown"
    assert state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_status").state == "unknown"


async def test_station_missing_from_poll_is_unavailable(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    mock_client.find_stations_by_ids.return_value = [
        s for s in mock_client.find_stations_by_ids.return_value if s.id != 6042
    ]
    coordinator = mock_config_entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert (
        state_by_unique_id(hass, "sensor", f"{eid}_station_6042_status").state == STATE_UNAVAILABLE
    )
    assert (
        state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_status").state == STATE_UNAVAILABLE
    )
    assert state_by_unique_id(hass, "sensor", f"{eid}_station_6041_status").state == "charging"


def test_socket_label_fallbacks() -> None:
    from custom_components.mer.entity import socket_label

    assert socket_label(Socket(id=1, name="Left")) == "Left"
    assert socket_label(Socket(id=1, identity_key="2")) == "Socket 2"
    assert socket_label(Socket(id=77)) == "Socket 77"
