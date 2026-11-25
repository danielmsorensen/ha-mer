"""Who is charging on a socket: the `driver_id` and `driver` socket status attributes."""

from __future__ import annotations

import asyncio

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mer.const import CONF_KNOWN_DRIVERS
from tests.fake_portal import FakePortal
from tests.helpers import setup_integration
from tests.test_sensor import state_by_unique_id


@pytest.fixture
def live(portal: FakePortal) -> FakePortal:
    portal.go_live()
    return portal


async def _settle(hass: HomeAssistant) -> None:
    for _ in range(3):
        await asyncio.sleep(0)
    await hass.async_block_till_done()


def _driver(hass: HomeAssistant, entry: MockConfigEntry, socket_id: int) -> tuple[str, str]:
    state = state_by_unique_id(hass, "sensor", f"{entry.entry_id}_socket_{socket_id}_status")
    return state.attributes["driver_id"], state.attributes["driver"]


def _detail_reads(portal: FakePortal, station_id: int) -> int:
    return sum(1 for call in portal.calls["find_station_by_id"] if call[0] == station_id)


async def test_driver_is_read_from_the_charger_detail(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    await setup_integration(hass, mock_config_entry)
    assert _driver(hass, mock_config_entry, 11241) == ("100001", None)
    assert _driver(hass, mock_config_entry, 11242) == (None, None)


async def test_known_driver_is_named(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={**mock_config_entry.options, CONF_KNOWN_DRIVERS: {"100001": "Sam"}},
    )
    await setup_integration(hass, mock_config_entry)
    assert _driver(hass, mock_config_entry, 11241) == ("100001", "Sam")


async def test_pushed_driver_shows_at_once_and_goes_when_the_socket_is_free(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, live: FakePortal
) -> None:
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    reads = _detail_reads(live, 6042)

    live.take(11243, status="CHARGING", driver="100002")
    await _settle(hass)
    assert _driver(hass, mock_config_entry, 11243) == ("100002", None)
    assert _detail_reads(live, 6042) == reads

    live.free(11243)
    await _settle(hass)
    assert _driver(hass, mock_config_entry, 11243) == (None, None)


async def test_a_fault_mid_session_keeps_the_driver(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, live: FakePortal
) -> None:
    """Pushes mostly carry no customer id; only a free socket ends the session's driver."""
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    live.take(11243, status="CHARGING", driver="100002")
    live.take(11243, status="FAULTED")
    live.take(11243, status="CHARGING")
    await _settle(hass)
    assert _driver(hass, mock_config_entry, 11243) == ("100002", None)


async def test_unpushed_driver_is_looked_up_once_per_session(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, live: FakePortal
) -> None:
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    coordinator = mock_config_entry.runtime_data
    reads = _detail_reads(live, 6042)

    # Preparing: no one has authenticated yet, so there is no one to look up, and a detail
    # read then (the hourly one) doesn't count as the session's lookup.
    live.take(11243, status="PREPARING")
    await coordinator.async_refresh()
    assert _detail_reads(live, 6042) == reads
    await coordinator._refresh_station(6042)
    reads += 1
    assert _driver(hass, mock_config_entry, 11243) == (None, None)

    live.take(11243, status="CHARGING", driver="100003", pushed=False)
    await _settle(hass)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _detail_reads(live, 6042) == reads + 1
    assert _driver(hass, mock_config_entry, 11243) == ("100003", None)

    # Found: no more lookups this session.
    await coordinator.async_refresh()
    assert _detail_reads(live, 6042) == reads + 1


async def test_a_lookup_that_finds_no_one_is_not_repeated(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, live: FakePortal
) -> None:
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    coordinator = mock_config_entry.runtime_data
    reads = _detail_reads(live, 6042)

    live.take(11243, status="CHARGING", pushed=False)
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    assert _detail_reads(live, 6042) == reads + 1
    assert _driver(hass, mock_config_entry, 11243) == (None, None)
