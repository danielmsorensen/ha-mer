"""Tests for the notify-me-when-available switch."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.switch import SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
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
from tests.fake_portal import FakePortal
from tests.helpers import setup_integration
from tests.test_sensor import state_by_unique_id


def entity_id_for(hass: HomeAssistant, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(SWITCH_DOMAIN, DOMAIN, unique_id)
    assert entity_id is not None
    return entity_id


async def turn_on(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


async def turn_off(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


async def test_initial_state_reflects_subscription(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    """6042 is subscribed via the app, 6041 is not."""
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    assert (
        state_by_unique_id(hass, "switch", f"{eid}_station_6042_notify_available").state == STATE_ON
    )
    assert (
        state_by_unique_id(hass, "switch", f"{eid}_station_6041_notify_available").state
        == STATE_OFF
    )


async def test_turn_on_subscribes_and_stays_on(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    entity_id = entity_id_for(hass, f"{eid}_station_6041_notify_available")
    await turn_on(hass, entity_id)
    assert portal.calls["subscribe_to_availability"] == [(6041,)]
    assert hass.states.get(entity_id).state == STATE_ON


async def test_turn_off_unsubscribes(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    entity_id = entity_id_for(hass, f"{eid}_station_6042_notify_available")
    await turn_off(hass, entity_id)
    assert portal.calls["unsubscribe_from_availability"] == [(6042,)]
    assert hass.states.get(entity_id).state == STATE_OFF


async def test_subscribe_error_raises_command_failed(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    entity_id = entity_id_for(hass, f"{eid}_station_6041_notify_available")
    portal.fail("subscribe_to_availability", ApiError("STATION_NOT_FOUND"))
    with pytest.raises(HomeAssistantError) as excinfo:
        await turn_on(hass, entity_id)
    assert "STATION_NOT_FOUND" in str(excinfo.value)
    # the failed call must not have left the switch showing a change that never happened
    assert hass.states.get(entity_id).state == STATE_OFF


async def test_unsubscribe_error_raises_command_failed(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    entity_id = entity_id_for(hass, f"{eid}_station_6042_notify_available")
    portal.fail("unsubscribe_from_availability", ApiError("STATION_NOT_FOUND"))
    with pytest.raises(HomeAssistantError) as excinfo:
        await turn_off(hass, entity_id)
    assert "STATION_NOT_FOUND" in str(excinfo.value)
    # the failed call must not have left the switch showing a change that never happened
    assert hass.states.get(entity_id).state == STATE_ON


async def test_toggle_keeps_poll_schedule(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A toggle must neither push the next poll back nor trigger an extra one."""
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    entity_id = entity_id_for(hass, f"{eid}_station_6041_notify_available")
    polls = len(portal.calls["find_stations_by_ids"])

    async def advance(seconds: int) -> None:
        freezer.tick(timedelta(seconds=seconds))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    await advance(40)
    await turn_on(hass, entity_id)
    await advance(6)  # past any follow-up refresh delay
    await advance(12)  # 58 s after setup: the 60 s poll is not due yet
    assert len(portal.calls["find_stations_by_ids"]) == polls, "toggle triggered a poll"
    await advance(3)  # 61 s: the poll scheduled at setup has run
    assert len(portal.calls["find_stations_by_ids"]) == polls + 1, "toggle delayed the poll"
