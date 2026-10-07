"""Tests for Mer binary sensors."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mer.driivz.const import PUSH_STATION_STATUS
from custom_components.mer.driivz.models import Socket
from tests.helpers import setup_integration
from tests.test_sensor import state_by_unique_id


async def test_socket_available_and_account_counts(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    assert state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_status").state == "available"
    assert state_by_unique_id(hass, "sensor", f"{eid}_socket_11241_status").state == "charging"
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_available_sockets").state == "3"
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
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_available_sockets").state == "0"
    assert state_by_unique_id(hass, "sensor", f"{eid}_station_6042_available_sockets").state == "0"
    assert state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_status").state == "occupied"


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


async def test_available_sockets_lists_free_sockets_with_start_buttons(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    # Platforms set up concurrently, so at the very first state write the button
    # entities may not be registered yet; the attribute fills in on the next poll.
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    state = state_by_unique_id(hass, "sensor", f"{eid}_account_available_sockets")
    assert state.attributes["total_sockets"] == 4
    assert state.attributes["chargers"] == 2
    free = state.attributes["available_sockets"]
    # Charger A: both free; Charger B: Left charging, Right free.
    assert [(f["charger"], f["socket"]) for f in free] == [
        ("Riverside - Bay 3 - Charger A", "Left"),
        ("Riverside - Bay 3 - Charger A", "Right"),
        ("Riverside - Bay 4 - Charger B", "Right"),
    ]
    assert free[0]["station_id"] == 6042
    assert free[0]["socket_id"] == 11243
    assert free[0]["start_button"] == "button.riverside_bay_3_charger_a_left_start_charge"
    assert hass.states.get(free[0]["start_button"]) is not None


async def test_socket_status_names_its_charger_socket_and_start_button(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Blueprints work from these attributes instead of parsing entity ids."""
    await setup_integration(hass, mock_config_entry)
    await mock_config_entry.runtime_data.async_refresh()  # buttons registered by now
    await hass.async_block_till_done()
    eid = mock_config_entry.entry_id
    state = state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_status")
    assert state.attributes["charger"] == "Riverside - Bay 3 - Charger A"
    assert state.attributes["socket"] == "Left"
    assert state.attributes["start_button"] == "button.riverside_bay_3_charger_a_left_start_charge"


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


async def test_charger_available_sockets_count(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    # Charger A: both free; Charger B: Left charging, Right free.
    charger_a = state_by_unique_id(hass, "sensor", f"{eid}_station_6042_available_sockets")
    assert charger_a.state == "2"
    assert charger_a.attributes["total_sockets"] == 2
    assert state_by_unique_id(hass, "sensor", f"{eid}_station_6041_available_sockets").state == "1"


async def test_charging_says_where_and_then_where_it_ended(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket: Socket,
) -> None:
    """Lets an automation leave out the charger you have just unplugged from."""
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    charging = lambda: state_by_unique_id(hass, "binary_sensor", f"{eid}_account_charging")  # noqa: E731
    # Before any session is seen: the charge history's last one, without a socket.
    attrs = charging().attributes
    assert (attrs["charger"], attrs["station_id"], attrs["socket_id"]) == (
        "Riverside - Bay 4 - Charger B",
        6041,
        None,
    )
    assert attrs["ended_at"] is not None

    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    attrs = charging().attributes
    assert charging().state == STATE_ON
    assert (attrs["socket"], attrs["station_id"], attrs["socket_id"]) == ("Right", 6041, 11242)
    assert attrs["ended_at"] is None

    history_reads = mock_client.find_transactions.await_count
    mock_client.find_last_active_charge_socket.return_value = None
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    attrs = charging().attributes
    assert charging().state == STATE_OFF
    assert (attrs["socket"], attrs["station_id"], attrs["socket_id"]) == ("Right", 6041, 11242)
    assert attrs["ended_at"] is not None
    # The history is re-read in the same poll, so "Last session" catches up at once.
    assert mock_client.find_transactions.await_count == history_reads + 1


async def test_pushed_session_end_is_recorded_and_history_follows(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket: Socket,
) -> None:
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data
    mock_client.find_last_active_charge_socket.return_value = None
    history_reads = mock_client.find_transactions.await_count
    coordinator._handle_push_text(
        json.dumps(
            {
                "@c": PUSH_STATION_STATUS,
                "stationId": 6041,
                "stationSocketId": 11242,
                "stationSocketStatusDto": {"socketStatus": "AVAILABLE"},
            }
        )
    )
    await hass.async_block_till_done()
    eid = mock_config_entry.entry_id
    attrs = state_by_unique_id(hass, "binary_sensor", f"{eid}_account_charging").attributes
    assert attrs["socket_id"] == 11242 and attrs["ended_at"] is not None
    # No poll straight away (the portal may still report the session); the next one
    # re-reads the history.
    assert mock_client.find_transactions.await_count == history_reads
    await coordinator.async_refresh()
    assert mock_client.find_transactions.await_count == history_reads + 1
