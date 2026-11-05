"""Tests for Mer buttons."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

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

from custom_components.mer.const import DOMAIN
from custom_components.mer.driivz.exceptions import ApiError
from custom_components.mer.driivz.models import Socket, Station
from tests.helpers import fast_commands, setup_integration, stations_from_fixture
from tests.test_sensor import state_by_unique_id


def entity_id_for(hass: HomeAssistant, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(BUTTON_DOMAIN, DOMAIN, unique_id)
    assert entity_id is not None
    return entity_id


async def press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


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


async def test_a_rejected_press_fails_with_the_portals_reason(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    mock_client.start_charge.side_effect = ApiError("NOT_AUTHORIZED")
    with pytest.raises(HomeAssistantError) as excinfo:
        await press(hass, entity_id_for(hass, f"{eid}_socket_11243_start_charge"))
    assert excinfo.value.translation_key == "command_failed"
    assert excinfo.value.translation_placeholders == {"error": "NOT_AUTHORIZED"}


async def test_an_unconfirmed_press_fails_saying_how_long_it_waited(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    fast_commands(mock_config_entry, interval_seconds=0.01, max_polls=2)
    eid = mock_config_entry.entry_id
    with pytest.raises(HomeAssistantError) as excinfo:
        await press(hass, entity_id_for(hass, f"{eid}_socket_11243_start_charge"))
    assert excinfo.value.translation_key == "command_unconfirmed"
    assert excinfo.value.translation_placeholders == {"seconds": "0.02"}


async def test_account_stop_stops_your_sessions_socket(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket: Socket,
) -> None:
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    fast_commands(mock_config_entry)
    eid = mock_config_entry.entry_id
    mock_client.find_last_active_charge_socket.return_value = None
    await press(hass, entity_id_for(hass, f"{eid}_account_stop_charge"))
    mock_client.stop_charge.assert_awaited_once_with(11242)


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
    fast_commands(mock_config_entry)
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


async def test_refresh_now_polls_immediately(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    polls = mock_client.find_stations_by_ids.await_count
    await press(hass, entity_id_for(hass, f"{eid}_account_refresh"))
    await hass.async_block_till_done()
    assert mock_client.find_stations_by_ids.await_count == polls + 1


async def test_command_and_poll_entities_are_diagnostic(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    registry = er.async_get(hass)
    eid = mock_config_entry.entry_id
    for domain, key in (
        ("sensor", "account_last_command"),
        ("sensor", "account_last_poll"),
        ("sensor", "account_requests_remaining"),
        ("binary_sensor", "account_live_status"),
        ("button", "account_refresh"),
    ):
        entity_id = registry.async_get_entity_id(domain, DOMAIN, f"{eid}_{key}")
        assert registry.async_get(entity_id).entity_category == "diagnostic", key


def caps(allowed: set[str], denied: dict[str, str] | None = None):
    from custom_components.mer.driivz.models import SocketCapabilities

    return SocketCapabilities(allowed=frozenset(allowed), denied=denied or {})


async def test_start_button_availability_follows_capabilities(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Read at setup and after a status change; a free socket with no start allowed greys out."""
    mock_client.find_socket_capabilities.return_value = {
        11243: caps(set(), {"START_CHARGE_NOW": "INVALID_CARD"}),
        11244: caps({"START_CHARGE_FROM_SERVER"}),
    }
    await setup_integration(hass, mock_config_entry)
    await hass.async_block_till_done()
    eid = mock_config_entry.entry_id
    state = lambda uid: hass.states.get(entity_id_for(hass, uid)).state  # noqa: E731
    assert mock_client.find_socket_capabilities.await_count >= 1
    assert state(f"{eid}_socket_11243_start_charge") == "unavailable"  # free, but not for you
    assert state(f"{eid}_socket_11244_start_charge") != "unavailable"


async def test_start_button_follows_status_over_a_stale_state_denial(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Capabilities read while the socket was still busy must not grey out a free socket.

    The portal can still deny a start for the socket's state a few seconds after the
    socket shows free; that answer would otherwise stick until the socket changes again.
    """
    mock_client.find_socket_capabilities.return_value = {
        11243: caps(
            set(),
            {
                "START_CHARGE_FROM_SERVER": "OPERATION_NOT_ALLOWED_IN_CURRENT_SOCKET_STATE",
                "START_CHARGE_NOW": "INVALID_CARD",
            },
        ),
    }
    await setup_integration(hass, mock_config_entry)
    await hass.async_block_till_done()
    eid = mock_config_entry.entry_id
    uid = f"{eid}_socket_11243_start_charge"
    assert hass.states.get(entity_id_for(hass, uid)).state != "unavailable"  # free: AVAILABLE

    mock_client.find_stations_by_ids.return_value = with_socket_status(
        stations_from_fixture("stations_by_ids.json"), 11243, "CHARGING"
    )
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id_for(hass, uid)).state == "unavailable"


async def test_refresh_now_rereads_start_capabilities(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    mock_client.find_socket_capabilities.return_value = {
        11244: caps(set(), {"START_CHARGE_FROM_SERVER": "NO_PERMISSION"}),
    }
    await setup_integration(hass, mock_config_entry)
    await hass.async_block_till_done()
    eid = mock_config_entry.entry_id
    uid = f"{eid}_socket_11244_start_charge"
    assert hass.states.get(entity_id_for(hass, uid)).state == "unavailable"

    mock_client.find_socket_capabilities.return_value = {
        11244: caps({"START_CHARGE_FROM_SERVER"}),
    }
    await press(hass, entity_id_for(hass, f"{eid}_account_refresh"))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id_for(hass, uid)).state != "unavailable"
