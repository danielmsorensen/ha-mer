"""Tests for the portal's push channel (websocket) handling."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import WSMsgType
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.mer.const import (
    PUSH_POLL_INTERVAL_SECONDS,
    PUSH_SILENCE_TIMEOUT_SECONDS,
)
from custom_components.mer.driivz.exceptions import DriivzConnectionError
from custom_components.mer.driivz.models import Socket
from tests.helpers import setup_integration
from tests.test_button import entity_id_for, press
from tests.test_sensor import state_by_unique_id


class FakeMessage:
    def __init__(self, data: str) -> None:
        self.type = WSMsgType.TEXT
        self.data = data


class FakeWebSocket:
    """Stands in for aiohttp's websocket: messages are fed in, the loop drains them."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[FakeMessage | None] = asyncio.Queue()
        self.closed = False

    def push(self, payload: dict[str, Any]) -> None:
        self.queue.put_nowait(FakeMessage(json.dumps(payload)))

    def disconnect(self) -> None:
        self.queue.put_nowait(None)

    async def receive(self) -> FakeMessage:
        item = await self.queue.get()
        if item is None:
            closed = FakeMessage("")
            closed.type = WSMsgType.CLOSED
            return closed
        return item

    async def close(self) -> None:
        # Like aiohttp: a pending receive() returns CLOSED once the socket is closed.
        self.closed = True
        self.queue.put_nowait(None)


def status_push(station_id: int, socket_id: int, status: str, **flags: bool) -> dict[str, Any]:
    """A StationStatusSummary message shaped like the live capture in docs/api.md."""
    return {
        "@c": "StationStatusSummaryDtoImp",
        "stationId": station_id,
        "stationStatusDto": {"stationStatus": status},
        "stationSocketId": socket_id,
        "stationSocketStatusDto": {
            "socketStatus": status,
            "approveStartChargePending": flags.get("start_pending", False),
            "stopChargePending": flags.get("stop_pending", False),
        },
        "socketStatuses": {},
        "allSocketStatus": [],
    }


def estimate_push(socket_id: int, kwh: float, cost: float) -> dict[str, Any]:
    return {
        "@c": "BillingChargingEstimationMessageImp",
        "stationSocketId": socket_id,
        "totalKw": kwh,
        "cost": cost,
        "currency": "GBP",
        "tocSoc": 100.0,
    }


@pytest.fixture
def fake_ws(mock_client: MagicMock) -> FakeWebSocket:
    """Make the client's push connection return a controllable fake websocket."""
    ws = FakeWebSocket()
    mock_client.connect_push = AsyncMock(return_value=ws)
    return ws


async def _settle(hass: HomeAssistant) -> None:
    # Let the background task pick the connection up and drain any queued pushes.
    for _ in range(3):
        await asyncio.sleep(0)
    await hass.async_block_till_done()


async def test_connected_channel_slows_polling_and_reports_live(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    fake_ws: FakeWebSocket,
) -> None:
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    coordinator = mock_config_entry.runtime_data
    assert coordinator.push_connected is True
    assert coordinator.update_interval == timedelta(seconds=PUSH_POLL_INTERVAL_SECONDS)
    eid = mock_config_entry.entry_id
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_account_live_updates").state == "on"


async def test_status_push_updates_socket_without_polling(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    fake_ws: FakeWebSocket,
) -> None:
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    eid = mock_config_entry.entry_id
    polls = mock_client.find_stations_by_ids.await_count
    assert state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_status").state == "available"

    fake_ws.push(status_push(6042, 11243, "CHARGING"))
    await _settle(hass)

    assert state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_status").state == "charging"
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_socket_11243_available").state == "off"
    assert state_by_unique_id(hass, "sensor", f"{eid}_station_6042_status").state == "charging"
    # The other socket on the charger is untouched, and no request was spent.
    assert state_by_unique_id(hass, "sensor", f"{eid}_socket_11244_status").state == "available"
    assert mock_client.find_stations_by_ids.await_count == polls


async def test_push_for_other_station_is_ignored(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    fake_ws: FakeWebSocket,
) -> None:
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    eid = mock_config_entry.entry_id
    before = {s.entity_id: s.state for s in hass.states.async_all()}
    fake_ws.push(status_push(20359, 99999, "FAULTED"))
    fake_ws.push({"@c": "SomethingElseDtoImp", "stationId": 6042})
    fake_ws.queue.put_nowait(FakeMessage("not json"))
    await _settle(hass)
    assert {s.entity_id: s.state for s in hass.states.async_all()} == before
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_account_live_updates").state == "on"


async def test_estimate_push_updates_running_session(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    fake_ws: FakeWebSocket,
    charging_socket: Socket,
) -> None:
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    eid = mock_config_entry.entry_id
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_active_energy").state == "1.606"

    fake_ws.push(estimate_push(11242, 3.27, 0.0))
    await _settle(hass)
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_active_energy").state == "3.27"
    assert state_by_unique_id(hass, "sensor", f"{eid}_station_6041_session_energy").state == "3.27"


async def test_session_ending_push_clears_active_session(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    fake_ws: FakeWebSocket,
    charging_socket: Socket,
) -> None:
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    eid = mock_config_entry.entry_id
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_account_charging").state == "on"

    fake_ws.push(status_push(6041, 11242, "FINISHING"))
    await _settle(hass)
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_account_charging").state == "off"
    assert (
        state_by_unique_id(hass, "binary_sensor", f"{eid}_station_6041_session_here").state == "off"
    )


async def test_disconnect_restores_polling_and_catches_up(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    fake_ws: FakeWebSocket,
) -> None:
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    coordinator = mock_config_entry.runtime_data
    eid = mock_config_entry.entry_id
    polls = mock_client.find_stations_by_ids.await_count
    # Reconnect attempts after the drop fail and back off; teardown cancels the task.
    mock_client.connect_push.side_effect = DriivzConnectionError("down")
    fake_ws.disconnect()
    await _settle(hass)
    assert coordinator.push_connected is False
    assert coordinator.update_interval == timedelta(seconds=60)
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_account_live_updates").state == "off"
    assert mock_client.find_stations_by_ids.await_count == polls + 1  # the catch-up poll


async def test_start_press_resolves_on_push_without_polling(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    fake_ws: FakeWebSocket,
) -> None:
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    eid = mock_config_entry.entry_id
    polls = mock_client.find_stations_by_ids.await_count
    with (
        patch("custom_components.mer.coordinator.COMMAND_POLL_INTERVAL_SECONDS", 2),
        patch("custom_components.mer.coordinator.COMMAND_MAX_POLLS", 2),
    ):
        pressing = hass.async_create_task(
            press(hass, entity_id_for(hass, f"{eid}_socket_11243_start_charge"))
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        fake_ws.push(status_push(6042, 11243, "CHARGING"))
        await asyncio.wait_for(pressing, timeout=5)
    mock_client.start_charge.assert_awaited_once_with(11243)
    assert mock_client.find_stations_by_ids.await_count == polls  # resolved by push alone
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_last_command").state == "charging"


async def test_channel_recovers_after_failed_reconnect(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    fake_ws: FakeWebSocket,
) -> None:
    """Drop, one failed reconnect, then success: polling returns to 5 min and pushes apply."""
    second_ws = FakeWebSocket()
    mock_client.connect_push.side_effect = [fake_ws, DriivzConnectionError("still down"), second_ws]
    with patch("custom_components.mer.coordinator.PUSH_RECONNECT_MIN_SECONDS", 0):
        await setup_integration(hass, mock_config_entry)
        await _settle(hass)
        coordinator = mock_config_entry.runtime_data
        eid = mock_config_entry.entry_id
        assert coordinator.push_connected is True

        fake_ws.disconnect()
        for _ in range(10):
            await asyncio.sleep(0)
        await hass.async_block_till_done()

    assert coordinator.push_connected is True
    assert mock_client.connect_push.await_count == 3  # first, failed retry, recovery
    assert coordinator.update_interval == timedelta(seconds=PUSH_POLL_INTERVAL_SECONDS)
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_account_live_updates").state == "on"
    # The new connection delivers pushes like the first did.
    second_ws.push(status_push(6042, 11244, "OCCUPIED"))
    await _settle(hass)
    assert state_by_unique_id(hass, "sensor", f"{eid}_socket_11244_status").state == "occupied"


async def test_silent_channel_is_dropped_and_reopened_with_fresh_login(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    fake_ws: FakeWebSocket,
    freezer: FrozenDateTimeFactory,
) -> None:
    """TCP up but nothing arriving: treat as dead, log in again, reconnect.

    This is the "Live updates on, but nothing has changed for an hour" failure.
    """
    second_ws = FakeWebSocket()
    mock_client.connect_push.side_effect = [fake_ws, second_ws]
    with patch("custom_components.mer.coordinator.PUSH_RECONNECT_MIN_SECONDS", 0):
        await setup_integration(hass, mock_config_entry)
        await _settle(hass)
        logins_before = mock_client.login.await_count
        coordinator = mock_config_entry.runtime_data
        assert coordinator.push_connected is True

        # A push just inside the timeout keeps the channel: the watchdog is re-armed.
        freezer.tick(timedelta(seconds=PUSH_SILENCE_TIMEOUT_SECONDS - 5))
        async_fire_time_changed(hass)
        fake_ws.push(status_push(6042, 11243, "OCCUPIED"))
        await _settle(hass)
        freezer.tick(timedelta(seconds=PUSH_SILENCE_TIMEOUT_SECONDS - 5))
        async_fire_time_changed(hass)
        await _settle(hass)
        assert mock_client.connect_push.await_count == 1
        assert fake_ws.closed is False

        # Then nothing for the full timeout: dropped, fresh login, second connection.
        freezer.tick(timedelta(seconds=PUSH_SILENCE_TIMEOUT_SECONDS + 1))
        async_fire_time_changed(hass)
        await _settle(hass)
        await _settle(hass)
        assert fake_ws.closed is True
        assert mock_client.login.await_count == logins_before + 1
        assert mock_client.connect_push.await_count == 2
        assert coordinator.push_connected is True

        second_ws.push(status_push(6042, 11244, "OCCUPIED"))
        await _settle(hass)
        eid = mock_config_entry.entry_id
        assert state_by_unique_id(hass, "sensor", f"{eid}_socket_11244_status").state == "occupied"
        assert coordinator.last_push_at is not None


async def test_frequent_pushes_do_not_starve_the_poll(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    fake_ws: FakeWebSocket,
    charging_socket: Socket,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Live failure: estimates every ~45 s kept pushing the 5-minute poll back forever.

    Energy updated from the pushes while the session duration, which only a poll
    refreshes, froze for over an hour.
    """
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    polls = mock_client.find_stations_by_ids.await_count

    # Six pushes 60 s apart: more than one poll interval of constant push traffic.
    for i in range(6):
        freezer.tick(timedelta(seconds=60))
        fake_ws.push(estimate_push(11242, 2.0 + i, 0.0))
        async_fire_time_changed(hass)
        await _settle(hass)

    assert mock_client.find_stations_by_ids.await_count >= polls + 1, "poll was starved"
