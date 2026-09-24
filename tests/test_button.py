"""Tests for Mer buttons."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
)

from custom_components.mer.const import DOMAIN, EVENT_COMMAND_RESULT
from custom_components.mer.driivz.exceptions import ApiError
from custom_components.mer.driivz.models import Socket, Station
from tests.helpers import setup_integration, stations_from_fixture
from tests.test_sensor import state_by_unique_id


def entity_id_for(hass: HomeAssistant, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(BUTTON_DOMAIN, DOMAIN, unique_id)
    assert entity_id is not None
    return entity_id


async def press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


@pytest.fixture(autouse=True)
def fast_command_polling():
    """Poll instantly and give up after three polls, so tests need no real waiting."""
    with (
        patch("custom_components.mer.coordinator.COMMAND_POLL_INTERVAL_SECONDS", 0),
        patch("custom_components.mer.coordinator.COMMAND_MAX_POLLS", 3),
    ):
        yield


def with_socket_status(stations: list[Station], socket_id: int, status: str) -> list[Station]:
    """The live-status stations with one socket's status changed."""
    out = []
    for station in stations:
        sockets = tuple(
            replace(sock, status=status) if sock.id == socket_id else sock
            for sock in station.sockets
        )
        out.append(replace(station, sockets=sockets))
    return out


def after_first_call(first, then):
    """Side effect returning `first` on the first call and `then` afterwards."""
    calls = {"n": 0}

    async def _side_effect(*_args, **_kwargs):
        calls["n"] += 1
        return first if calls["n"] == 1 else then

    return _side_effect


def capture_events(hass: HomeAssistant) -> list:
    events: list = []
    hass.bus.async_listen(EVENT_COMMAND_RESULT, lambda e: events.append(e.data))
    return events


async def test_start_on_free_socket_waits_for_charging(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """The press stays open, publishes each poll, and resolves once the socket charges."""
    live = stations_from_fixture("stations_by_ids.json")
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    events = capture_events(hass)
    mock_client.find_stations_by_ids.side_effect = after_first_call(
        live, with_socket_status(live, 11243, "CHARGING")
    )
    polls_before = mock_client.find_stations_by_ids.await_count

    await press(hass, entity_id_for(hass, f"{eid}_socket_11243_start_charge"))
    await hass.async_block_till_done()

    mock_client.start_charge.assert_awaited_once_with(11243)
    # first poll still available, second poll charging -> resolved on the second
    assert mock_client.find_stations_by_ids.await_count == polls_before + 2
    assert state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_status").state == "charging"
    last = state_by_unique_id(hass, "sensor", f"{eid}_account_last_command")
    assert last.state == "charging"
    assert last.attributes["command"] == "start"
    assert last.attributes["charger"] == "Business Durham - NETPark 3 - Explorer 1"
    assert last.attributes["socket"] == "Left"
    assert [e["result"] for e in events] == ["charging"]


async def test_start_on_free_socket_ready_for_cable(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Free socket goes to preparing: the charger is waiting for the cable, a positive outcome."""
    live = stations_from_fixture("stations_by_ids.json")
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    mock_client.find_stations_by_ids.return_value = with_socket_status(live, 11243, "PREPARING")
    await press(hass, entity_id_for(hass, f"{eid}_socket_11243_start_charge"))
    await hass.async_block_till_done()
    assert (
        state_by_unique_id(hass, "sensor", f"{eid}_account_last_command").state == "awaiting_cable"
    )


async def test_start_unconfirmed_raises_and_records(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Nothing changes within the timeout: the press fails with a clear message."""
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    events = capture_events(hass)
    with pytest.raises(HomeAssistantError) as excinfo:
        await press(hass, entity_id_for(hass, f"{eid}_socket_11243_start_charge"))
    assert "did not confirm" in str(excinfo.value)
    mock_client.start_charge.assert_awaited_once_with(11243)
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_last_command").state == "unconfirmed"
    assert [e["result"] for e in events] == ["unconfirmed"]


async def test_start_polling_stops_when_rate_limit_low(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Low headroom: give up polling early instead of exhausting the portal's limit."""
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    polls_before = mock_client.find_stations_by_ids.await_count
    mock_client.rate_limit_remaining = 2
    with pytest.raises(HomeAssistantError):
        await press(hass, entity_id_for(hass, f"{eid}_socket_11243_start_charge"))
    assert mock_client.find_stations_by_ids.await_count == polls_before


async def test_start_charge_rejected_raises_and_records(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    events = capture_events(hass)
    mock_client.start_charge.side_effect = ApiError("OPERATION_NOT_ALLOWED_IN_CURRENT_SOCKET_STATE")
    with pytest.raises(HomeAssistantError) as excinfo:
        await press(hass, entity_id_for(hass, f"{eid}_socket_11243_start_charge"))
    assert "OPERATION_NOT_ALLOWED_IN_CURRENT_SOCKET_STATE" in str(excinfo.value)
    last = state_by_unique_id(hass, "sensor", f"{eid}_account_last_command")
    assert last.state == "rejected"
    assert "OPERATION_NOT_ALLOWED" in last.attributes["message"]
    assert [e["result"] for e in events] == ["rejected"]
    mock_client.find_stations_by_ids.assert_awaited_once()  # no follow-up polling


async def test_stop_charge_button_waits_for_session_to_end(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket: Socket,
) -> None:
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    events = capture_events(hass)
    # The session disappears on the second poll after the stop.
    mock_client.find_last_active_charge_socket.side_effect = after_first_call(charging_socket, None)

    await press(hass, entity_id_for(hass, f"{eid}_account_stop_charge"))
    await hass.async_block_till_done()

    mock_client.stop_charge.assert_awaited_once_with(11242)
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_last_command").state == "stopped"
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_account_charging").state == "off"
    assert [e["result"] for e in events] == ["stopped"]


async def test_stop_charge_without_session_is_unavailable(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """No session: the button is unavailable, and the press service skips it."""
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    entity_id = entity_id_for(hass, f"{eid}_account_stop_charge")
    assert hass.states.get(entity_id).state == "unavailable"
    await press(hass, entity_id)
    mock_client.stop_charge.assert_not_awaited()


async def test_station_stop_charge_button_on_correct_station(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket: Socket,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Charger 6041's stop button stops the session, because it is running there."""
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    mock_client.find_last_active_charge_socket.return_value = None
    await press(hass, entity_id_for(hass, f"{eid}_station_6041_stop_charge"))
    mock_client.stop_charge.assert_awaited_once_with(11242)
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_last_command").state == "stopped"


async def test_station_stop_charge_button_on_wrong_station_unavailable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket: Socket,
) -> None:
    """Charger 6042's stop button is unavailable: the session is on 6041, not here."""
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    entity_id = entity_id_for(hass, f"{eid}_station_6042_stop_charge")
    assert hass.states.get(entity_id).state == "unavailable"
    await press(hass, entity_id)
    mock_client.stop_charge.assert_not_awaited()


async def test_station_stop_charge_button_without_session_unavailable(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """No active session anywhere: the station button is unavailable too."""
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    entity_id = entity_id_for(hass, f"{eid}_station_6041_stop_charge")
    assert hass.states.get(entity_id).state == "unavailable"
    await press(hass, entity_id)
    mock_client.stop_charge.assert_not_awaited()


async def test_buttons_unavailable_when_pressing_would_do_nothing(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket: Socket,
) -> None:
    """Buttons grey out rather than offer a press the portal would reject.

    Session on 6041's socket 11242; in the live-status fixture 11241 is in use and
    11243/11244 are free.
    """
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id

    def state(unique_id: str) -> str:
        return hass.states.get(entity_id_for(hass, unique_id)).state

    assert state(f"{eid}_account_stop_charge") != "unavailable"
    assert state(f"{eid}_station_6041_stop_charge") != "unavailable"
    assert state(f"{eid}_station_6042_stop_charge") == "unavailable"
    assert state(f"{eid}_socket_11243_start_charge") != "unavailable"  # free
    assert state(f"{eid}_socket_11241_start_charge") == "unavailable"  # in use

    # Session ends: every stop button greys out.
    mock_client.find_last_active_charge_socket.return_value = None
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert state(f"{eid}_account_stop_charge") == "unavailable"
    assert state(f"{eid}_station_6041_stop_charge") == "unavailable"
