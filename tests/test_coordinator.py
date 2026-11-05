"""Tests for MerCoordinator polling behaviour."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.mer.const import EVENT_COMMAND_RESULT
from custom_components.mer.driivz.exceptions import (
    AuthError,
    DriivzConnectionError,
    RateLimitError,
)
from custom_components.mer.driivz.models import SocketCapabilities
from tests.fake_portal import FakePortal
from tests.helpers import fast_commands, setup_integration
from tests.test_button import entity_id_for, press
from tests.test_sensor import state_by_unique_id

# Stamped onto a station detail by a test so it can tell a committed refresh from a
# discarded one; the portal hands back equal Station objects every call, so comparison
# would not show the difference.
_COMMIT_MARKER = "committed by _refresh_station"


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_idle_cycle_request_budget(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, mock_config_entry)
    mock_config_entry.runtime_data.async_add_listener(lambda: None)
    # first refresh: stations, active, wallet+history, 2 details
    assert len(portal.calls["find_stations_by_ids"]) == 1
    assert len(portal.calls["find_last_active_charge_socket"]) == 1
    assert len(portal.calls["find_wallet"]) == 1
    assert len(portal.calls["find_transactions"]) == 1
    assert len(portal.calls["find_station_by_id"]) == 2
    assert len(portal.calls["find_current_transaction_estimate"]) == 0

    await _tick(hass, freezer, 61)
    assert len(portal.calls["find_stations_by_ids"]) == 2
    assert len(portal.calls["find_last_active_charge_socket"]) == 2
    assert len(portal.calls["find_wallet"]) == 1  # not due yet
    assert len(portal.calls["find_station_by_id"]) == 2  # not due yet

    await _tick(hass, freezer, 15 * 60)
    assert len(portal.calls["find_wallet"]) == 2
    assert len(portal.calls["find_transactions"]) == 2
    assert len(portal.calls["find_station_by_id"]) == 2

    # The hourly per-charger work is staggered: both chargers are due again after an hour,
    # but only one is refreshed in this tick, and the other in the next one. This test used
    # to assert both went out together (await_count jumping 2 -> 4 in one tick), which is
    # the behaviour that made the worst-case burst 6 + 2N calls and grow without bound with
    # the number of selected chargers.
    await _tick(hass, freezer, 60 * 60)
    assert len(portal.calls["find_station_by_id"]) == 3
    assert len(portal.calls["is_subscribed_to_availability"]) == 3
    await _tick(hass, freezer, 61)
    assert len(portal.calls["find_station_by_id"]) == 4
    assert len(portal.calls["is_subscribed_to_availability"]) == 4
    assert [c[0] for c in portal.calls["find_station_by_id"]] == [
        6042,
        6041,
        6042,
        6041,
    ]
    # ...and with both freshly refreshed, the next cycle does no detail work at all.
    await _tick(hass, freezer, 61)
    assert len(portal.calls["find_station_by_id"]) == 4


async def test_optional_refresh_failure_does_not_fail_the_cycle(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A persistently broken optional endpoint must not blank every entity, or hot-loop.

    The station poll is the whole point of the integration; the hourly notify-me check is
    the newest and least-exercised call on the branch. Awaiting them in the same `try` made
    the second able to kill the first, and because the timestamp was only written on full
    success, the hourly work then retried every cycle -- a sixtyfold traffic increase
    triggered by nothing but portal flakiness.
    """
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data
    coordinator.async_add_listener(lambda: None)
    calls_after_setup = len(portal.calls["is_subscribed_to_availability"])

    # Tag every detail the portal hands back from here on, so we can see exactly when a
    # `_refresh_station` committed one.
    for station_id, detail in portal.details.items():
        portal.details[station_id] = replace(detail, model_name=_COMMIT_MARKER)
    # Break the notify-me check, then let the hourly window come round. The stagger means
    # 6042 is refreshed in this cycle and 6041 in the next one; both will fail.
    portal.fail("is_subscribed_to_availability", DriivzConnectionError("notify down"))
    await _tick(hass, freezer, 60 * 60)

    # The cycle still succeeded, and the live station data the integration exists to
    # publish is there -- including the source of the socket availability sensors.
    assert coordinator.last_update_success is True
    assert coordinator.data.station(6042) is not None
    assert coordinator.data.stations.keys() == {6042, 6041}
    # And nothing from the failed refresh was committed: `find_station_by_id` succeeded,
    # but its result is discarded rather than left ahead of the published notify state.
    assert coordinator.data.details[6042].model_name != _COMMIT_MARKER
    assert len(portal.calls["find_station_by_id"]) == 3
    assert len(portal.calls["is_subscribed_to_availability"]) == calls_after_setup + 1

    await _tick(hass, freezer, 61)  # 6041's turn, also fails
    assert len(portal.calls["is_subscribed_to_availability"]) == calls_after_setup + 2
    assert coordinator.data.details[6041].model_name != _COMMIT_MARKER

    # Both failures are stamped anyway, so the work backs off to its next window instead of
    # retrying every single cycle.
    await _tick(hass, freezer, 61)
    assert len(portal.calls["is_subscribed_to_availability"]) == calls_after_setup + 2
    assert len(portal.calls["find_stations_by_ids"]) == 4  # the poll kept running
    assert coordinator.last_update_success is True

    # When it recovers at its next window, both halves commit together as normal.
    portal.clear("is_subscribed_to_availability")
    await _tick(hass, freezer, 60 * 60)
    assert coordinator.data.details[6042].model_name == _COMMIT_MARKER
    assert coordinator.data.notify_subscriptions[6042] is True


async def test_first_cycle_does_not_come_up_half_populated(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    """The very first cycle still fails hard, so setup retries instead of degrading.

    Tolerating an optional failure is only worth it once entities exist to keep available.
    On the first cycle there are none, and entity names, device models, serial numbers and
    socket labels are all composed once when the platforms are set up -- coming up without
    a charger's detail would bake "Socket 11241" in as a socket's name for the lifetime of
    the config entry. `ConfigEntryNotReady` and a normal setup retry is the better outcome.
    """
    portal.fail("is_subscribed_to_availability", DriivzConnectionError("notify down"))
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_optional_refresh_still_reports_auth_and_rate_limit(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
    freezer: FrozenDateTimeFactory,
) -> None:
    """AuthError and RateLimitError from the optional work must reach their handlers."""
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data
    coordinator.async_add_listener(lambda: None)

    portal.fail("find_wallet", RateLimitError("429"))
    await _tick(hass, freezer, 15 * 60)  # wallet is due again
    assert coordinator.last_update_success is False
    assert coordinator._skip_next is True

    portal.fail("find_wallet", AuthError("SESSION_EXPIRED"))
    await _tick(hass, freezer, 61)  # skipped cycle, no portal contact
    await _tick(hass, freezer, 61)
    assert any(
        f["context"].get("source") == "reauth" for f in hass.config_entries.flow.async_progress()
    )


async def test_charging_cycle_fetches_session(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
) -> None:
    portal.charge(11242)
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data
    active = coordinator.data.active
    assert active is not None
    assert active.socket_id == 11242
    assert active.station_id == 6041
    assert active.socket_name == "Right"
    assert active.station_caption is not None and "Charger B" in active.station_caption
    assert active.transaction_id == 9088676
    assert active.energy_kwh == 1.606
    assert active.cost == 0.0
    assert active.started_at is not None
    assert active.started_at < dt_util.utcnow()
    assert active.duration == timedelta(milliseconds=953825)
    assert portal.calls["find_current_transaction"] == [(11242,)]
    assert portal.calls["find_current_transaction_estimate"] == [(11242,)]


async def test_rate_limit_low_skips_next_cycle(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, mock_config_entry)
    mock_config_entry.runtime_data.async_add_listener(lambda: None)
    portal.rate_limit_remaining = 1
    await _tick(hass, freezer, 61)  # this cycle runs and observes remaining=1
    assert len(portal.calls["find_stations_by_ids"]) == 2
    await _tick(hass, freezer, 61)  # skipped
    assert len(portal.calls["find_stations_by_ids"]) == 2
    portal.rate_limit_remaining = 9
    await _tick(hass, freezer, 61)  # resumes
    assert len(portal.calls["find_stations_by_ids"]) == 3
    assert mock_config_entry.runtime_data.last_update_success is True


async def test_rate_limit_error_marks_failed_then_skips(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, mock_config_entry)
    mock_config_entry.runtime_data.async_add_listener(lambda: None)
    portal.fail("find_stations_by_ids", RateLimitError("429"))
    await _tick(hass, freezer, 61)
    coordinator = mock_config_entry.runtime_data
    assert coordinator.last_update_success is False
    portal.clear("find_stations_by_ids")
    await _tick(hass, freezer, 61)  # skipped cycle: republishes cached data, no client call
    assert len(portal.calls["find_stations_by_ids"]) == 2
    # A skipped cycle republishes the last known good data without contacting the portal,
    # which the coordinator framework treats as a successful update.
    assert coordinator.last_update_success is True
    await _tick(hass, freezer, 61)
    assert coordinator.last_update_success is True


async def test_connection_error_is_update_failed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, mock_config_entry)
    mock_config_entry.runtime_data.async_add_listener(lambda: None)
    portal.fail("find_stations_by_ids", DriivzConnectionError("boom"))
    await _tick(hass, freezer, 61)
    assert mock_config_entry.runtime_data.last_update_success is False


async def test_auth_error_during_poll_starts_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, mock_config_entry)
    mock_config_entry.runtime_data.async_add_listener(lambda: None)
    portal.fail("find_stations_by_ids", AuthError("SESSION_EXPIRED"))
    await _tick(hass, freezer, 61)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"].get("source") == "reauth" for f in flows)


async def test_toggle_during_refresh_is_not_clobbered(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A toggle that lands mid-refresh must win, not be overwritten by the refresh's commit.

    `_refresh_station` fetches a station's subscription state and then writes it back into
    `self._notify`. If a toggle for the same station lands after the refresh has already
    fetched its (now-stale) answer but before that write, an unguarded refresh would
    silently overwrite the user's confirmed change.

    The toggle is started as its own task, not awaited inline: with the fix in place, it
    blocks on `_notify_lock` (held by the parked refresh) until the refresh's fetch and
    commit both finish, so awaiting it directly here would deadlock against the
    still-parked refresh. That ordering -- toggle forced to wait its turn rather than
    interleaving -- is exactly the property under test. Staggering the hourly work to one
    charger per cycle does not change it: the lock's scope is one station's fetch and
    commit, which is the whole window in which staleness can be introduced.
    """
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data
    assert coordinator.data.notify_subscriptions[6042] is True  # the now-stale answer

    held = portal.hold("is_subscribed_to_availability")

    refresh_task = asyncio.create_task(coordinator._refresh_station(6042))
    await held.reached.wait()  # the refresh is now parked inside the fetch for 6042

    toggle_task = asyncio.create_task(coordinator.async_set_availability_subscription(6042, False))
    await asyncio.sleep(0)  # let the toggle task run up to its lock-acquire attempt

    held.release()
    await refresh_task
    await toggle_task

    assert portal.calls["unsubscribe_from_availability"] == [(6042,)]
    assert coordinator._notify[6042] is False
    assert coordinator.data.notify_subscriptions[6042] is False


def capture_events(hass: HomeAssistant) -> list:
    events: list = []
    hass.bus.async_listen(EVENT_COMMAND_RESULT, lambda e: events.append(e.data))
    return events


async def test_a_command_is_recorded_published_and_fired(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    """While the press waits, each poll is published; the outcome lands on Last command."""
    await setup_integration(hass, mock_config_entry)
    fast_commands(mock_config_entry, max_polls=3)
    eid = mock_config_entry.entry_id
    events = capture_events(hass)
    portal.charge(11243)  # the charger starts at once; the next poll sees it

    await press(hass, entity_id_for(hass, f"{eid}_socket_11243_start_charge"))
    await hass.async_block_till_done()

    assert state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_status").state == "charging"
    last = state_by_unique_id(hass, "sensor", f"{eid}_account_last_command")
    assert last.state == "charging"
    assert last.attributes["command"] == "start"
    assert last.attributes["method"] == "start"
    assert last.attributes["charger"] == "Riverside - Bay 3 - Charger A"
    assert last.attributes["socket"] == "Left"
    assert [(e["command"], e["result"], e["method"]) for e in events] == [
        ("start", "charging", "start")
    ]


async def test_a_refused_start_is_recorded_and_its_capabilities_kept(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    """The capabilities read at the press grey the button out without waiting for a re-read."""
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    events = capture_events(hass)
    portal.capabilities = {
        11243: SocketCapabilities(frozenset(), {"START_CHARGE_FROM_SERVER": "INVALID_CARD"})
    }
    button = entity_id_for(hass, f"{eid}_socket_11243_start_charge")

    with pytest.raises(HomeAssistantError):
        await press(hass, button)

    assert portal.calls["start_charge"] == []
    last = state_by_unique_id(hass, "sensor", f"{eid}_account_last_command")
    assert last.state == "rejected"
    assert last.attributes["message"] == "START_NOT_ALLOWED: INVALID_CARD"
    assert [e["result"] for e in events] == ["rejected"]
    assert hass.states.get(button).state == "unavailable"
