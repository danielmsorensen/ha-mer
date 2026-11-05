"""Tests for the portal's push channel (websocket) handling."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
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
from tests.fake_portal import FakeMessage, FakePortal, estimate_push, status_push
from tests.helpers import fast_commands, setup_integration
from tests.test_button import entity_id_for, press
from tests.test_sensor import state_by_unique_id


@pytest.fixture
def live(portal: FakePortal) -> FakePortal:
    """The portal with its push channel up."""
    portal.go_live()
    return portal


async def _settle(hass: HomeAssistant) -> None:
    # Let the background task pick the connection up and drain any queued pushes.
    for _ in range(3):
        await asyncio.sleep(0)
    await hass.async_block_till_done()


async def test_connected_channel_slows_polling_and_reports_live(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    live: FakePortal,
) -> None:
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    coordinator = mock_config_entry.runtime_data
    assert coordinator.data.push_connected is True
    assert coordinator.update_interval == timedelta(seconds=PUSH_POLL_INTERVAL_SECONDS)
    eid = mock_config_entry.entry_id
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_account_live_status").state == "on"


async def test_status_push_updates_socket_without_polling(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    live: FakePortal,
) -> None:
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    eid = mock_config_entry.entry_id
    polls = len(live.calls["find_stations_by_ids"])
    assert state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_status").state == "available"

    live.take(11243)
    await _settle(hass)

    assert state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_status").state == "occupied"
    assert state_by_unique_id(hass, "sensor", f"{eid}_station_6042_status").state == "occupied"
    # The other socket on the charger is untouched, and no request was spent.
    assert state_by_unique_id(hass, "sensor", f"{eid}_socket_11244_status").state == "available"
    assert len(live.calls["find_stations_by_ids"]) == polls


async def test_push_for_other_station_is_ignored(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    live: FakePortal,
) -> None:
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    eid = mock_config_entry.entry_id
    before = {s.entity_id: s.state for s in hass.states.async_all()}
    live.push(status_push(20359, 99999, "FAULTED"))
    live.push({"@c": "SomethingElseDtoImp", "stationId": 6042})
    live.ws.queue.put_nowait(FakeMessage("not json"))
    await _settle(hass)
    assert {s.entity_id: s.state for s in hass.states.async_all()} == before
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_account_live_status").state == "on"


async def test_estimate_push_updates_running_session(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    live: FakePortal,
) -> None:
    live.charge(11242)
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    eid = mock_config_entry.entry_id
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_active_energy").state == "1.606"

    live.push(estimate_push(11242, 3.27, 0.0))
    await _settle(hass)
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_active_energy").state == "3.27"
    assert state_by_unique_id(hass, "sensor", f"{eid}_station_6041_session_energy").state == "3.27"


async def test_session_ending_push_clears_active_session(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    live: FakePortal,
) -> None:
    live.charge(11242)
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    eid = mock_config_entry.entry_id
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_account_charging").state == "on"

    live.stop(11242)
    await _settle(hass)
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_account_charging").state == "off"
    assert (
        state_by_unique_id(hass, "binary_sensor", f"{eid}_station_6041_session_here").state == "off"
    )


async def test_disconnect_restores_polling_and_catches_up(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    live: FakePortal,
) -> None:
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    coordinator = mock_config_entry.runtime_data
    eid = mock_config_entry.entry_id
    polls = len(live.calls["find_stations_by_ids"])
    # Reconnect attempts after the drop fail and back off; teardown cancels the task.
    live.fail("connect_push", DriivzConnectionError("down"))
    live.ws.disconnect()
    await _settle(hass)
    assert coordinator.data.push_connected is False
    assert coordinator.update_interval == timedelta(seconds=60)
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_account_live_status").state == "off"
    assert len(live.calls["find_stations_by_ids"]) == polls + 1  # the catch-up poll


async def test_start_press_resolves_on_push_without_polling(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    live: FakePortal,
) -> None:
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    eid = mock_config_entry.entry_id
    polls = len(live.calls["find_stations_by_ids"])
    fast_commands(mock_config_entry, interval_seconds=2, max_polls=2)
    pressing = hass.async_create_task(
        press(hass, entity_id_for(hass, f"{eid}_socket_11243_start_charge"))
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    live.charge(11243)
    await asyncio.wait_for(pressing, timeout=5)
    assert live.calls["start_charge"] == [(11243,)]
    assert len(live.calls["find_stations_by_ids"]) == polls  # resolved by push alone
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_last_command").state == "charging"


async def test_channel_recovers_after_failed_reconnect(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    live: FakePortal,
) -> None:
    """Drop, one failed reconnect, then success: polling returns to 5 min and pushes apply."""
    with patch("custom_components.mer.coordinator.PUSH_RECONNECT_MIN_SECONDS", 0):
        await setup_integration(hass, mock_config_entry)
        await _settle(hass)
        coordinator = mock_config_entry.runtime_data
        eid = mock_config_entry.entry_id
        assert coordinator.data.push_connected is True

        live.fail("connect_push", DriivzConnectionError("still down"), times=1)
        live.ws.disconnect()
        for _ in range(10):
            await asyncio.sleep(0)
        await hass.async_block_till_done()

    assert coordinator.data.push_connected is True
    assert len(live.calls["connect_push"]) == 3  # first, failed retry, recovery
    assert coordinator.update_interval == timedelta(seconds=PUSH_POLL_INTERVAL_SECONDS)
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_account_live_status").state == "on"
    # The new connection delivers pushes like the first did.
    live.take(11244)
    await _settle(hass)
    assert state_by_unique_id(hass, "sensor", f"{eid}_socket_11244_status").state == "occupied"


async def test_silent_channel_is_dropped_and_reopened_with_fresh_login(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    live: FakePortal,
    freezer: FrozenDateTimeFactory,
) -> None:
    """TCP up but nothing arriving: treat as dead, log in again, reconnect.

    This is the "Live updates on, but nothing has changed for an hour" failure.
    """
    with patch("custom_components.mer.coordinator.PUSH_RECONNECT_MIN_SECONDS", 0):
        await setup_integration(hass, mock_config_entry)
        await _settle(hass)
        first = live.ws
        logins_before = len(live.calls["login"])
        coordinator = mock_config_entry.runtime_data
        assert coordinator.data.push_connected is True

        # A push just inside the timeout keeps the channel: the watchdog is re-armed.
        freezer.tick(timedelta(seconds=PUSH_SILENCE_TIMEOUT_SECONDS - 5))
        async_fire_time_changed(hass)
        live.take(11243)
        await _settle(hass)
        freezer.tick(timedelta(seconds=PUSH_SILENCE_TIMEOUT_SECONDS - 5))
        async_fire_time_changed(hass)
        await _settle(hass)
        assert len(live.calls["connect_push"]) == 1
        assert first.closed is False

        # Then nothing for the full timeout: dropped, fresh login, second connection.
        freezer.tick(timedelta(seconds=PUSH_SILENCE_TIMEOUT_SECONDS + 1))
        async_fire_time_changed(hass)
        await _settle(hass)
        await _settle(hass)
        assert first.closed is True
        assert len(live.calls["login"]) == logins_before + 1
        assert len(live.calls["connect_push"]) == 2
        assert coordinator.data.push_connected is True

        live.take(11244)
        await _settle(hass)
        eid = mock_config_entry.entry_id
        assert state_by_unique_id(hass, "sensor", f"{eid}_socket_11244_status").state == "occupied"
        assert coordinator.last_push_at is not None


async def test_frequent_pushes_do_not_starve_the_poll(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    live: FakePortal,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Live failure: estimates every ~45 s kept pushing the 5-minute poll back forever.

    Energy updated from the pushes while the session duration, which only a poll
    refreshes, froze for over an hour.
    """
    live.charge(11242)
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    polls = len(live.calls["find_stations_by_ids"])

    # Six pushes 60 s apart: more than one poll interval of constant push traffic.
    for i in range(6):
        freezer.tick(timedelta(seconds=60))
        live.push(estimate_push(11242, 2.0 + i, 0.0))
        async_fire_time_changed(hass)
        await _settle(hass)

    assert len(live.calls["find_stations_by_ids"]) >= polls + 1, "poll was starved"


async def test_live_status_reports_connected_since(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    live: FakePortal,
) -> None:
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    eid = mock_config_entry.entry_id
    state = state_by_unique_id(hass, "binary_sensor", f"{eid}_account_live_status")
    assert state.name == "Mer account Live status"
    assert state.attributes["connected_since"] is not None


async def test_second_session_estimates_do_not_trigger_polls(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    live: FakePortal,
) -> None:
    """Two charges on one account: the portal reports one; the other's estimates are ignored."""
    live.charge(11242)
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    eid = mock_config_entry.entry_id
    polls = len(live.calls["find_stations_by_ids"])
    for _ in range(3):
        live.push(estimate_push(11243, 5.0, 0.0))  # a socket on the other charger
        await _settle(hass)
    assert len(live.calls["find_stations_by_ids"]) == polls
    # The known session is untouched.
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_active_energy").state == "1.606"


async def test_estimate_push_updates_charging_rate(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    live: FakePortal,
) -> None:
    live.charge(11242)
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    eid = mock_config_entry.entry_id
    rate = state_by_unique_id(hass, "sensor", f"{eid}_account_active_rate")
    assert rate.state == "1.801"  # from the polled estimate fixture
    assert rate.attributes["unit_of_measurement"] == "kW"
    push = estimate_push(11242, 3.27, 0.0)
    push["rateEstimation"] = 6.552
    live.push(push)
    await _settle(hass)
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_active_rate").state == "6.552"
    assert state_by_unique_id(hass, "sensor", f"{eid}_station_6041_session_rate").state == "6.552"
    assert (
        state_by_unique_id(hass, "sensor", f"{eid}_station_6042_session_rate").state
        == "unavailable"
    )


async def test_public_charger_session_start_and_end_by_push(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    live: FakePortal,
) -> None:
    """Start picked up from the first estimate, end from the socket's status, off-site too."""
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    eid = mock_config_entry.entry_id
    lookups = len(live.calls["find_last_active_charge_socket"])

    live.charge(15028)  # the public charger's status push is not for your chargers
    live.push(estimate_push(15028, 0.5, 0.0))
    await _settle(hass)
    await asyncio.sleep(0)
    await hass.async_block_till_done()
    assert len(live.calls["find_last_active_charge_socket"]) > lookups
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_account_charging").state == "on"

    live.stop(15028)
    await _settle(hass)
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_account_charging").state == "off"


async def test_poll_now_finds_the_session(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    live: FakePortal,
) -> None:
    """A socket starting to charge polls at once (2026-10-07), not at the next poll.

    Which pushes ask for a poll is in test_session_tracker.
    """
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    eid = mock_config_entry.entry_id
    # Past the refresh debounce, so the requested poll runs at once, inside the handler.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await _settle(hass)
    live.charge(11242)
    await _settle(hass)
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_account_charging").state == "on"


async def test_watch_for_session_runs_after_your_start(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    live: FakePortal,
) -> None:
    """Your start waits for the cable, then charges: the portal is asked until it reports it.

    Seen live on 2026-10-12. When the tracker asks for a watch is in test_session_tracker.
    """
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    eid = mock_config_entry.entry_id
    fast_commands(mock_config_entry, interval_seconds=2, max_polls=2)
    pressing = hass.async_create_task(
        press(hass, entity_id_for(hass, f"{eid}_socket_11242_start_charge"))
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    live.await_cable(11242)
    await asyncio.wait_for(pressing, timeout=5)
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_last_command").state == (
        "awaiting_cable"
    )

    # The session lookup lags the charge: it reports it from the third ask.
    asks = 0
    lookup = live.find_last_active_charge_socket

    async def lagging() -> Socket | None:
        nonlocal asks
        asks += 1
        found = await lookup()
        return found if asks >= 3 else None

    live.find_last_active_charge_socket = lagging  # type: ignore[method-assign]
    with patch("custom_components.mer.session_tracker.SESSION_WATCH_DELAYS_SECONDS", (0, 0, 0, 0)):
        live.charge(11242)
        await _settle(hass)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))  # debounce
        await _settle(hass)
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_account_charging").state == "on"
    assert asks >= 3


async def test_pushed_end_is_not_undone_by_a_lagging_poll(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    live: FakePortal,
) -> None:
    """Seen live on 2026-11-02: after a stop, a poll still reported the session for 24 s.

    Bringing it back counted as a charge starting and ran "When charging starts" actions.
    """
    live.charge(11242)
    await setup_integration(hass, mock_config_entry)
    await _settle(hass)
    eid = mock_config_entry.entry_id

    def charging() -> str:
        return state_by_unique_id(hass, "binary_sensor", f"{eid}_account_charging").state

    changes: list[str] = []
    hass.bus.async_listen(
        "state_changed",
        lambda event: (
            changes.append(event.data["new_state"].state)
            if event.data["entity_id"].endswith("_charging")
            else None
        ),
    )

    session = live.session
    live.stop(11242)
    await _settle(hass)
    live.session = session  # the session lookup lags the stop
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert charging() == "off"
    assert changes == ["off"]

    live.charge(11242)  # genuinely charging again
    await _settle(hass)
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert charging() == "on"
