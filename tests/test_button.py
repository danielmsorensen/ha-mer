"""Tests for Mer buttons."""

from __future__ import annotations

from datetime import timedelta
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
    async_fire_time_changed,
)

from custom_components.mer.const import DOMAIN
from custom_components.mer.driivz.exceptions import ApiError
from custom_components.mer.driivz.models import Socket
from tests.helpers import setup_integration


def entity_id_for(hass: HomeAssistant, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(BUTTON_DOMAIN, DOMAIN, unique_id)
    assert entity_id is not None
    return entity_id


async def press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


async def test_start_charge_button(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    entity_id = entity_id_for(hass, f"{eid}_socket_11243_start_charge")
    polls_before = mock_client.find_stations_by_ids.await_count
    await press(hass, entity_id)
    mock_client.start_charge.assert_awaited_once_with(11243)
    # the refresh is scheduled ~5 s later, then debounced by the coordinator (~10 s)
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert mock_client.find_stations_by_ids.await_count == polls_before + 1


async def test_start_charge_rejected_raises(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    mock_client.start_charge.side_effect = ApiError("OPERATION_NOT_ALLOWED_IN_CURRENT_SOCKET_STATE")
    with pytest.raises(HomeAssistantError) as excinfo:
        await press(hass, entity_id_for(hass, f"{eid}_socket_11243_start_charge"))
    assert "OPERATION_NOT_ALLOWED_IN_CURRENT_SOCKET_STATE" in str(excinfo.value)


async def test_stop_charge_button(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket: Socket,
    freezer: FrozenDateTimeFactory,
) -> None:
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    await press(hass, entity_id_for(hass, f"{eid}_account_stop_charge"))
    mock_client.stop_charge.assert_awaited_once_with(11242)
    # flush the scheduled refresh so no timer is left pending at teardown
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_stop_charge_without_session_raises(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    with pytest.raises(HomeAssistantError):
        await press(hass, entity_id_for(hass, f"{eid}_account_stop_charge"))
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
    await press(hass, entity_id_for(hass, f"{eid}_station_6041_stop_charge"))
    mock_client.stop_charge.assert_awaited_once_with(11242)
    # flush the scheduled refresh so no timer is left pending at teardown
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_station_stop_charge_button_on_wrong_station_raises(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket: Socket,
) -> None:
    """Charger 6042's stop button raises, because the session is on 6041, not here."""
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    with pytest.raises(HomeAssistantError):
        await press(hass, entity_id_for(hass, f"{eid}_station_6042_stop_charge"))
    mock_client.stop_charge.assert_not_awaited()


async def test_station_stop_charge_button_without_session_raises(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """No active session anywhere: the station button raises too."""
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    with pytest.raises(HomeAssistantError):
        await press(hass, entity_id_for(hass, f"{eid}_station_6041_stop_charge"))
    mock_client.stop_charge.assert_not_awaited()
