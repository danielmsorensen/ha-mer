"""Tests for MerCoordinator polling behaviour."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
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
    assert active.socket_id == 11241
    assert active.station_id == 6041
    assert active.energy_kwh == 12.345
    assert active.cost == 0.0
    assert active.started_at is not None
    mock_client.find_current_transaction_start_time.assert_awaited_once_with(11241)
    mock_client.find_current_transaction_estimate.assert_awaited_once_with(11241)


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
    await _tick(hass, freezer, 61)  # skipped cycle, still failed
    assert mock_client.find_stations_by_ids.await_count == 2
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
