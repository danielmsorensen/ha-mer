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


async def test_any_available_lists_free_sockets_with_start_buttons(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    # Platforms set up concurrently, so at the very first state write the button
    # entities may not be registered yet; the attribute fills in on the next poll.
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    state = state_by_unique_id(hass, "binary_sensor", f"{eid}_account_any_available")
    free = state.attributes["available_sockets"]
    # Explorer 1: both free; Explorer 2: Left charging, Right free.
    assert [(f["charger"], f["socket"]) for f in free] == [
        ("Business Durham - NETPark 3 - Explorer 1", "Left"),
        ("Business Durham - NETPark 3 - Explorer 1", "Right"),
        ("Business Durham - NETPark 4 - Explorer 2", "Right"),
    ]
    assert free[0]["station_id"] == 6042
    assert free[0]["socket_id"] == 11243
    assert (
        free[0]["start_button"] == "button.business_durham_netpark_3_explorer_1_left_start_charge"
    )
    assert hass.states.get(free[0]["start_button"]) is not None


def test_availability_states_have_descriptive_names() -> None:
    """On/Off would say nothing; the translations name both states."""
    import json
    from pathlib import Path

    strings = json.loads(
        (Path(__file__).parent.parent / "custom_components/mer/strings.json").read_text()
    )["entity"]["binary_sensor"]
    for key in ("socket_available", "account_any_available"):
        assert set(strings[key]["state"]) == {"on", "off"}


async def test_socket_available_names_its_charger_socket_and_start_button(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Blueprints work from these attributes instead of parsing entity ids."""
    await setup_integration(hass, mock_config_entry)
    await mock_config_entry.runtime_data.async_refresh()  # buttons registered by now
    await hass.async_block_till_done()
    eid = mock_config_entry.entry_id
    state = state_by_unique_id(hass, "binary_sensor", f"{eid}_socket_11243_available")
    assert state.attributes["charger"] == "Business Durham - NETPark 3 - Explorer 1"
    assert state.attributes["socket"] == "Left"
    assert (
        state.attributes["start_button"]
        == "button.business_durham_netpark_3_explorer_1_left_start_charge"
    )


async def test_session_here_names_the_socket(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket: Socket,
) -> None:
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    here = state_by_unique_id(hass, "binary_sensor", f"{eid}_station_6041_session_here")
    assert here.state == "on"
    assert here.attributes["socket"] == "Right"
    elsewhere = state_by_unique_id(hass, "binary_sensor", f"{eid}_station_6042_session_here")
    assert elsewhere.attributes["socket"] is None
