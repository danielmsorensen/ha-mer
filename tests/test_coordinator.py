"""Tests for MerCoordinator polling behaviour."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.mer.driivz.exceptions import (
    AuthError,
    DriivzConnectionError,
    RateLimitError,
)
from custom_components.mer.driivz.models import Socket
from tests.helpers import setup_integration


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_idle_cycle_request_budget(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, mock_config_entry)
    mock_config_entry.runtime_data.async_add_listener(lambda: None)
    # first refresh: stations, active, wallet+history, 2 details
    assert mock_client.find_stations_by_ids.await_count == 1
    assert mock_client.find_last_active_charge_socket.await_count == 1
    assert mock_client.find_wallet.await_count == 1
    assert mock_client.find_transactions.await_count == 1
    assert mock_client.find_station_by_id.await_count == 2
    assert mock_client.find_current_transaction_estimate.await_count == 0

    await _tick(hass, freezer, 61)
    assert mock_client.find_stations_by_ids.await_count == 2
    assert mock_client.find_last_active_charge_socket.await_count == 2
    assert mock_client.find_wallet.await_count == 1  # not due yet
    assert mock_client.find_station_by_id.await_count == 2  # not due yet

    await _tick(hass, freezer, 15 * 60)
    assert mock_client.find_wallet.await_count == 2
    assert mock_client.find_transactions.await_count == 2
    assert mock_client.find_station_by_id.await_count == 2

    await _tick(hass, freezer, 60 * 60)
    assert mock_client.find_station_by_id.await_count == 4


async def test_charging_cycle_fetches_session(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket: Socket,
) -> None:
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data
    active = coordinator.data.active
    assert active is not None
    assert active.socket_id == 11242
    assert active.station_id == 6041
    assert active.socket_name == "Right"
    assert active.station_caption is not None and "Explorer 2" in active.station_caption
    assert active.transaction_id == 9088676
    assert active.energy_kwh == 1.606
    assert active.cost == 0.0
    assert active.started_at is not None
    assert active.started_at < dt_util.utcnow()
    assert active.duration == timedelta(milliseconds=953825)
    mock_client.find_current_transaction.assert_awaited_once_with(11242)
    mock_client.find_current_transaction_estimate.assert_awaited_once_with(11242)


async def test_rate_limit_low_skips_next_cycle(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, mock_config_entry)
    mock_config_entry.runtime_data.async_add_listener(lambda: None)
    mock_client.rate_limit_remaining = 1
    await _tick(hass, freezer, 61)  # this cycle runs and observes remaining=1
    assert mock_client.find_stations_by_ids.await_count == 2
    await _tick(hass, freezer, 61)  # skipped
    assert mock_client.find_stations_by_ids.await_count == 2
    mock_client.rate_limit_remaining = 9
    await _tick(hass, freezer, 61)  # resumes
    assert mock_client.find_stations_by_ids.await_count == 3
    assert mock_config_entry.runtime_data.last_update_success is True


async def test_rate_limit_error_marks_failed_then_skips(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, mock_config_entry)
    mock_config_entry.runtime_data.async_add_listener(lambda: None)
    mock_client.find_stations_by_ids.side_effect = RateLimitError("429")
    await _tick(hass, freezer, 61)
    coordinator = mock_config_entry.runtime_data
    assert coordinator.last_update_success is False
    mock_client.find_stations_by_ids.side_effect = None
    await _tick(hass, freezer, 61)  # skipped cycle: republishes cached data, no client call
    assert mock_client.find_stations_by_ids.await_count == 2
    # A skipped cycle republishes the last known good data without contacting the portal,
    # which the coordinator framework treats as a successful update.
    assert coordinator.last_update_success is True
    await _tick(hass, freezer, 61)
    assert coordinator.last_update_success is True


async def test_connection_error_is_update_failed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, mock_config_entry)
    mock_config_entry.runtime_data.async_add_listener(lambda: None)
    mock_client.find_stations_by_ids.side_effect = DriivzConnectionError("boom")
    await _tick(hass, freezer, 61)
    assert mock_config_entry.runtime_data.last_update_success is False


async def test_auth_error_during_poll_starts_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, mock_config_entry)
    mock_config_entry.runtime_data.async_add_listener(lambda: None)
    mock_client.find_stations_by_ids.side_effect = AuthError("SESSION_EXPIRED")
    await _tick(hass, freezer, 61)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"].get("source") == "reauth" for f in flows)


async def test_toggle_during_refresh_is_not_clobbered(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A toggle that lands mid-refresh must win, not be overwritten by the refresh's commit.

    `_refresh_details` snapshots `self._notify` into a local dict, fetches every station's
    subscription state one await at a time, then assigns the whole dict back. If a toggle
    for the same station lands after the refresh has already fetched its (now-stale) answer
    but before that trailing assignment, an unguarded refresh would silently overwrite the
    user's confirmed change. Station 6042 is first in `station_ids`, so its fetch is the one
    parked here while the toggle runs.

    The toggle is started as its own task, not awaited inline: with the fix in place, it
    blocks on `_notify_lock` (held by the parked refresh) until the refresh's fetch-and-commit
    loop finishes, so awaiting it directly here would deadlock against the still-parked
    refresh. That ordering -- toggle forced to wait its turn rather than interleaving -- is
    exactly the property under test.
    """
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data
    assert coordinator.data.notify_subscriptions[6042] is True  # the now-stale answer

    parked = asyncio.Event()
    release = asyncio.Event()
    blocked_once = False

    async def blocking_is_subscribed(station_id: int) -> bool:
        nonlocal blocked_once
        if not blocked_once and station_id == 6042:
            blocked_once = True
            parked.set()
            await release.wait()
        return station_id == 6042

    mock_client.is_subscribed_to_availability.side_effect = blocking_is_subscribed

    refresh_task = asyncio.create_task(coordinator._refresh_details(dt_util.utcnow()))
    await parked.wait()  # the refresh is now parked inside the fetch for 6042

    toggle_task = asyncio.create_task(coordinator.async_set_availability_subscription(6042, False))
    await asyncio.sleep(0)  # let the toggle task run up to its lock-acquire attempt

    release.set()
    await refresh_task
    await toggle_task

    mock_client.unsubscribe_from_availability.assert_awaited_once_with(6042)
    assert coordinator._notify[6042] is False
    assert coordinator.data.notify_subscriptions[6042] is False

    # flush the scheduled refresh so no timer is left pending at teardown
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_get_station_merges_detail_and_live(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data
    station = coordinator.get_station(6041)
    assert station is not None
    assert station.model_name == "Eve Double Pro-line"
    assert station.status == "CHARGING"
    socket = coordinator.get_socket(6041, 11241)
    assert socket is not None and socket.name == "Left" and socket.status == "CHARGING"
    assert coordinator.get_socket(6041, 1) is None
    assert coordinator.get_station(999) is None
    assert [s.id for s in coordinator.configured_stations()] == [6042, 6041]
