"""Tests for Mer binary sensors."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mer.driivz.models import Socket
from tests.helpers import setup_integration
from tests.test_sensor import state_by_unique_id


async def test_socket_available_and_account_any_available(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    assert (
        state_by_unique_id(hass, "binary_sensor", f"{eid}_socket_11243_available").state == STATE_ON
    )
    assert (
        state_by_unique_id(hass, "binary_sensor", f"{eid}_socket_11241_available").state
        == STATE_OFF
    )
    assert (
        state_by_unique_id(hass, "binary_sensor", f"{eid}_account_any_available").state == STATE_ON
    )
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_account_charging").state == STATE_OFF

    # everything occupied -> nothing available
    stations = mock_client.find_stations_by_ids.return_value
    busy = []
    for station in stations:
        raw = {
            "id": station.id,
            "caption": station.caption,
            "stationStatusId": "OCCUPIED",
            "stationSockets": [{"id": s.id, "socketStatusId": "OCCUPIED"} for s in station.sockets],
        }
        busy.append(type(station).from_dict(raw))
    mock_client.find_stations_by_ids.return_value = busy
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert (
        state_by_unique_id(hass, "binary_sensor", f"{eid}_account_any_available").state == STATE_OFF
    )
    assert (
        state_by_unique_id(hass, "binary_sensor", f"{eid}_socket_11243_available").state
        == STATE_OFF
    )


async def test_account_charging_on_when_session_active(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket: Socket,
) -> None:
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_account_charging").state == STATE_ON


async def test_session_here_on_station_of_active_session(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket: Socket,
) -> None:
    """The active session is on socket 11242, station 6041 (see fixtures/last_active_charging.json).

    session_here must be on for the charger the driver is actually plugged into (6041) and off
    for the other configured charger (6042), never a site- or account-wide signal.
    """
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    assert (
        state_by_unique_id(hass, "binary_sensor", f"{eid}_station_6041_session_here").state
        == STATE_ON
    )
    assert (
        state_by_unique_id(hass, "binary_sensor", f"{eid}_station_6042_session_here").state
        == STATE_OFF
    )


async def test_session_here_off_for_both_when_idle(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    assert (
        state_by_unique_id(hass, "binary_sensor", f"{eid}_station_6041_session_here").state
        == STATE_OFF
    )
    assert (
        state_by_unique_id(hass, "binary_sensor", f"{eid}_station_6042_session_here").state
        == STATE_OFF
    )
