"""Tests for Mer sensors."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mer.const import DOMAIN
from custom_components.mer.driivz.models import Socket, SocketPrice
from tests.helpers import setup_integration, station_from_fixture


def state_by_unique_id(hass: HomeAssistant, platform: str, unique_id: str):
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id is not None, f"no {platform} entity with unique_id {unique_id}"
    state = hass.states.get(entity_id)
    assert state is not None
    return state


async def test_station_and_socket_sensors(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id

    status = state_by_unique_id(hass, "sensor", f"{eid}_station_6042_status")
    assert status.state == "available"
    assert status.attributes["device_class"] == "enum"
    assert "charging" in status.attributes["options"]
    assert status.name == "Business Durham - NETPark 3 - Explorer 1 Status"

    charging = state_by_unique_id(hass, "sensor", f"{eid}_station_6041_status")
    assert charging.state == "charging"

    left = state_by_unique_id(hass, "sensor", f"{eid}_socket_11241_status")
    assert left.state == "charging"
    assert left.name == "Business Durham - NETPark 4 - Explorer 2 Left status"
    right = state_by_unique_id(hass, "sensor", f"{eid}_socket_11242_status")
    assert right.state == "available"

    price = state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_price")
    assert price.state == "0.0"
    assert price.attributes["unit_of_measurement"] == "GBP/kWh"

    left_status = state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_status")
    assert left_status.attributes["max_power_kw"] == 7.0


async def test_unknown_status_maps_to_unknown_option(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    stations = mock_client.find_stations_by_ids.return_value
    weird = stations[0]
    stations[0] = type(weird).from_dict(
        {
            "id": weird.id,
            "caption": weird.caption,
            "stationStatusId": "SOMETHING_NEW",
            "stationSockets": [{"id": 11243, "socketStatusId": "WEIRD"}, {"id": 11244}],
        }
    )
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    assert state_by_unique_id(hass, "sensor", f"{eid}_station_6042_status").state == "unknown"
    assert state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_status").state == "unknown"


async def test_station_missing_from_poll_is_unavailable(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    mock_client.find_stations_by_ids.return_value = [
        s for s in mock_client.find_stations_by_ids.return_value if s.id != 6042
    ]
    coordinator = mock_config_entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert (
        state_by_unique_id(hass, "sensor", f"{eid}_station_6042_status").state == STATE_UNAVAILABLE
    )
    assert (
        state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_status").state == STATE_UNAVAILABLE
    )
    assert state_by_unique_id(hass, "sensor", f"{eid}_station_6041_status").state == "charging"


def test_socket_label_fallbacks() -> None:
    from custom_components.mer.entity import socket_label

    assert socket_label(Socket(id=1, name="Left")) == "Left"
    assert socket_label(Socket(id=1, identity_key="2")) == "Socket 2"
    assert socket_label(Socket(id=77)) == "Socket 77"


def _price(currency: str | None) -> SocketPrice:
    return SocketPrice(
        billing_plan_id=None,
        billing_plan_code=None,
        kwh_price=0.3,
        plug_in_minute_rate=None,
        transaction_fee=None,
        currency=currency,
    )


def test_socket_price_unit_follows_tariff_currency() -> None:
    """The price sensor's unit must track the socket's own tariff currency.

    This portal serves both GBP (UK) and EUR (Republic of Ireland) tenants, so a
    hardcoded unit would mislabel an Irish driver's price as pounds.
    """
    from custom_components.mer.sensor import SOCKET_SENSORS

    price_description = next(d for d in SOCKET_SENSORS if d.key == "price")
    assert price_description.unit_fn is not None

    gbp_socket = Socket(id=11243, prices=(_price("GBP"),))
    assert price_description.unit_fn(gbp_socket) == "GBP/kWh"

    eur_socket = Socket(id=99, prices=(_price("EUR"),))
    assert price_description.unit_fn(eur_socket) == "EUR/kWh"

    no_tariff_socket = Socket(id=100)
    assert price_description.unit_fn(no_tariff_socket) == "GBP/kWh"


async def test_aggregate_count_sensors(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    # Explorer 1: 2 available; Explorer 2: 1 charging + 1 available
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_available_sockets").state == "3"
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_sockets_in_use").state == "1"
    aggregate = state_by_unique_id(hass, "sensor", f"{eid}_account_available_sockets")
    assert aggregate.name == "Mer account Available sockets"


async def test_account_sensors_idle(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    assert (
        state_by_unique_id(hass, "sensor", f"{eid}_account_active_session").state == "unavailable"
    )
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_active_energy").state == "unavailable"
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_active_cost").state == "unavailable"
    assert (
        state_by_unique_id(hass, "sensor", f"{eid}_account_active_started").state == "unavailable"
    )
    assert (
        state_by_unique_id(hass, "sensor", f"{eid}_account_active_duration").state == "unavailable"
    )
    wallet = state_by_unique_id(hass, "sensor", f"{eid}_account_wallet_balance")
    assert wallet.state == "12.5"
    assert wallet.attributes["unit_of_measurement"] == "GBP"
    assert wallet.attributes["device_class"] == "monetary"
    last_energy = state_by_unique_id(hass, "sensor", f"{eid}_account_last_energy")
    assert last_energy.state == "32.408"
    assert last_energy.attributes["unit_of_measurement"] == "kWh"
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_last_cost").state == "0.0"
    last_started = state_by_unique_id(hass, "sensor", f"{eid}_account_last_started")
    assert last_started.state == "2026-09-16T10:34:26+00:00"
    assert last_started.attributes["device_class"] == "timestamp"
    assert last_started.attributes["station"] == "Business Durham - NETPark 4 - Explorer 2"

    # No session anywhere: session sensors are unavailable, not unknown, since there is
    # nothing to report rather than a missing value.
    for station_id in (6042, 6041):
        for key in ("session_energy", "session_cost", "session_started", "session_duration"):
            assert (
                state_by_unique_id(hass, "sensor", f"{eid}_station_{station_id}_{key}").state
                == "unavailable"
            )


async def test_account_sensors_charging(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket: Socket,
    freezer: FrozenDateTimeFactory,
) -> None:
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    now = dt_util.utcnow()
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    expected_started = (now - timedelta(milliseconds=953622)).isoformat(timespec="seconds")

    session = state_by_unique_id(hass, "sensor", f"{eid}_account_active_session")
    assert session.state == "Business Durham - NETPark 4 - Explorer 2 Right"
    assert session.attributes["charger"] == "Business Durham - NETPark 4 - Explorer 2"
    assert session.attributes["socket"] == "Right"
    assert session.attributes["socket_id"] == 11242
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_active_energy").state == "1.606"
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_active_cost").state == "0.0"
    assert (
        state_by_unique_id(hass, "sensor", f"{eid}_account_active_started").state
        == expected_started
    )
    active_duration = state_by_unique_id(hass, "sensor", f"{eid}_account_active_duration")
    # 953.825 s, displayed in hours by default.
    assert float(active_duration.state) == pytest.approx(953.825 / 3600)
    assert active_duration.attributes["unit_of_measurement"] == "h"

    # The charger it is actually running on (6041) mirrors the same session...
    energy = state_by_unique_id(hass, "sensor", f"{eid}_station_6041_session_energy")
    assert energy.state == "1.606"
    assert energy.attributes["unit_of_measurement"] == "kWh"
    cost = state_by_unique_id(hass, "sensor", f"{eid}_station_6041_session_cost")
    assert cost.state == "0.0"
    assert cost.attributes["unit_of_measurement"] == "GBP"
    started = state_by_unique_id(hass, "sensor", f"{eid}_station_6041_session_started")
    assert started.state == expected_started
    duration = state_by_unique_id(hass, "sensor", f"{eid}_station_6041_session_duration")
    assert float(duration.state) == pytest.approx(953.825 / 3600)
    assert duration.attributes["unit_of_measurement"] == "h"

    # ...while the charger with no session of yours (6042) reports its session sensors
    # as unavailable rather than unknown.
    for key in ("session_energy", "session_cost", "session_started", "session_duration"):
        assert (
            state_by_unique_id(hass, "sensor", f"{eid}_station_6042_{key}").state == "unavailable"
        )


async def test_socket_status_marks_my_session(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket: Socket,
) -> None:
    """The charging socket is only *mine* when the account's active session is on it."""
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    # The live-status fixture and the active-session fixture were captured at different
    # moments, so 11242's status here is not "charging"; the attribute is what is under
    # test, and it follows the active session, not the socket status.
    mine = state_by_unique_id(hass, "sensor", f"{eid}_socket_11242_status")
    assert mine.attributes["my_session"] is True
    other = state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_status")
    assert other.attributes["my_session"] is False


async def test_price_from_tariff_without_kwh_component(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """The live portal now sends flat-rate tariffs as fixPrice with no kwhPrice at all.

    That must read as 0 per kWh, not unknown, with the other tariff parts as attributes.
    """
    live_price = {
        "billingPlanId": 3465,
        "billingPlanCode": "DCC IP",
        "currency": "GBP",
        "fixPrice": 0.0,
        "futureReservationFee": 0.0,
    }
    detail = station_from_fixture("station_6042.json")
    live_detail = replace(
        detail,
        sockets=tuple(
            replace(sock, prices=(SocketPrice.from_dict(live_price),)) for sock in detail.sockets
        ),
    )
    original = mock_client.find_station_by_id.side_effect
    mock_client.find_station_by_id.side_effect = lambda station_id, billing_plan_id=None: (
        live_detail if station_id == 6042 else original(station_id)
    )
    await setup_integration(hass, mock_config_entry)
    price = state_by_unique_id(hass, "sensor", f"{mock_config_entry.entry_id}_socket_11243_price")
    assert price.state == "0.0"
    assert price.attributes["unit_of_measurement"] == "GBP/kWh"
    assert price.attributes["billing_plan"] == "DCC IP"
    assert price.attributes["fixed_price"] == 0.0


async def test_every_entity_has_an_icon(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """No entity may fall back to the frontend's generic eye icon.

    An entity has an icon if its device class gives one, or icons.json defines one
    for its translation key.
    """
    import json
    from pathlib import Path

    from homeassistant.helpers import entity_registry as er

    icons = json.loads(
        (Path(__file__).parent.parent / "custom_components/mer/icons.json").read_text()
    )["entity"]
    await setup_integration(hass, mock_config_entry)
    registry = er.async_get(hass)
    missing = []
    for entry in er.async_entries_for_config_entry(registry, mock_config_entry.entry_id):
        state = hass.states.get(entry.entity_id)
        has_device_class = state is not None and "device_class" in state.attributes
        in_icons = entry.translation_key in icons.get(entry.domain, {})
        if not (has_device_class or in_icons):
            missing.append(entry.entity_id)
    assert missing == []


async def test_poll_diagnostics(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Last poll and requests remaining, and both stay visible when a poll fails."""
    from custom_components.mer.driivz.exceptions import DriivzConnectionError

    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    last_poll = state_by_unique_id(hass, "sensor", f"{eid}_account_last_poll")
    assert last_poll.state not in ("unknown", "unavailable")
    assert last_poll.attributes["poll_interval_seconds"] == 60
    assert last_poll.attributes["last_poll_succeeded"] is True
    assert last_poll.attributes["last_error"] is None
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_requests_remaining").state == "9"

    good = last_poll.state
    mock_client.find_stations_by_ids.side_effect = DriivzConnectionError("portal down")
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    last_poll = state_by_unique_id(hass, "sensor", f"{eid}_account_last_poll")
    assert last_poll.state == good  # still the last successful poll, and still shown
    assert last_poll.attributes["last_poll_succeeded"] is False
    assert "portal down" in last_poll.attributes["last_error"]
    # Other account sensors go unavailable as before.
    assert (
        state_by_unique_id(hass, "sensor", f"{eid}_account_wallet_balance").state == "unavailable"
    )


async def test_active_session_price_on_own_charger(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket: Socket,
) -> None:
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    price = state_by_unique_id(hass, "sensor", f"{eid}_account_active_price")
    assert price.state == "0.0"
    assert price.attributes["unit_of_measurement"] == "GBP/kWh"
    assert price.attributes["billing_plan"] == "Durham County Council - Netpark IP"


async def test_session_on_charger_not_added(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """A public charger has no device, but the account's session sensors still work."""
    public = Socket.from_dict(
        {
            "id": 15028,
            "stationId": 17886,
            "name": "CCS",
            "socketStatusId": "CHARGING",
            "stationCaption": "(MER-FS-ABT0105) Business Durham NETPark - Expansion Space Car Park",
        }
    )
    mock_client.find_last_active_charge_socket.return_value = public
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    session = state_by_unique_id(hass, "sensor", f"{eid}_account_active_session")
    assert session.state.startswith("Business Durham NETPark - Expansion Space Car Park")
    assert session.attributes["socket"] == "CCS"
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_active_energy").state == "1.606"
    # The price came from that charger's detail, fetched once for the session.
    price = state_by_unique_id(hass, "sensor", f"{eid}_account_active_price")
    assert price.state not in ("unknown", "unavailable")
    fetched = [c.args[0] for c in mock_client.find_station_by_id.await_args_list]
    assert fetched.count(17886) == 1
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    fetched = [c.args[0] for c in mock_client.find_station_by_id.await_args_list]
    assert fetched.count(17886) == 1  # cached for the session
