"""Run the "offer a free charger" blueprint against the integration and check what it sends."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import shutil
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.components.automation import DOMAIN as AUTOMATION_DOMAIN
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.mer.const import DOMAIN
from tests.helpers import setup_integration, stations_from_fixture

BLUEPRINT = (
    Path(__file__).parent.parent / "blueprints" / "automation" / "mer" / "offer_free_charger.yaml"
)
WORK = {"latitude": 51.5, "longitude": -0.12}
AWAY = {"latitude": 52.5, "longitude": -1.5}


@pytest.fixture(autouse=True)
def fast_command_polling():
    with (
        patch("custom_components.mer.coordinator.COMMAND_POLL_INTERVAL_SECONDS", 0),
        patch("custom_components.mer.coordinator.COMMAND_MAX_POLLS", 1),
    ):
        yield


def socket_status_entity(hass: HomeAssistant, entry_id: str, socket_id: int) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry_id}_socket_{socket_id}_status"
    )
    assert entity_id is not None
    return entity_id


async def setup_blueprint(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    socket_order: list[int],
    tracker: str = "person.driver",
    **inputs: Any,
) -> list[dict[str, Any]]:
    """Integration, a Work zone, a person, a phone, and an automation from the blueprint."""
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id

    target = Path(hass.config.path("blueprints", "automation", "mer", "offer_free_charger.yaml"))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(BLUEPRINT, target)

    assert await async_setup_component(
        hass, "zone", {"zone": [{"name": "Work", "radius": 200, **WORK}]}
    )
    hass.states.async_set("person.driver", "Work", WORK)

    phone_entry = MockConfigEntry(domain="mobile_app")
    phone_entry.add_to_hass(hass)
    phone = dr.async_get(hass).async_get_or_create(
        config_entry_id=phone_entry.entry_id,
        identifiers={("mobile_app", "test-phone")},
        name="Test Phone",
    )
    sent: list[dict[str, Any]] = []

    async def capture(call: ServiceCall) -> None:
        sent.append(dict(call.data))

    hass.services.async_register("notify", "mobile_app_test_phone", capture)

    assert await async_setup_component(
        hass,
        AUTOMATION_DOMAIN,
        {
            AUTOMATION_DOMAIN: {
                "id": "offer",
                "alias": "Offer",
                "use_blueprint": {
                    "path": "mer/offer_free_charger.yaml",
                    "input": {
                        "tracker": tracker,
                        "zone": "zone.work",
                        "sockets": [socket_status_entity(hass, eid, s) for s in socket_order],
                        "notify_device": phone.id,
                        "freed_for": {"seconds": 0},
                        **inputs,
                    },
                },
            }
        },
    )
    await hass.async_block_till_done()
    return sent


async def run_by_hand(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        AUTOMATION_DOMAIN, "trigger", {"entity_id": "automation.offer"}, blocking=True
    )
    await hass.async_block_till_done()


def busy(stations, *socket_ids: int, status: str = "OCCUPIED"):
    return [
        replace(
            station,
            sockets=tuple(
                replace(s, status=status) if s.id in socket_ids else s for s in station.sockets
            ),
        )
        for station in stations
    ]


async def test_preview_sends_the_offer_for_the_first_free_socket_in_order(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    # Fixture: Charger A (6042) Left and Right free; Charger B (6041) Left charging, Right free.
    sent = await setup_blueprint(hass, mock_config_entry, [11241, 11242, 11243, 11244])
    await run_by_hand(hass)
    (offer,) = sent
    assert offer["title"] == "Mer charger free"
    assert offer["message"] == "Riverside - Bay 4 - Charger B"
    (action,) = offer["data"]["actions"]
    assert action == {"action": "MER_START_CHARGE", "title": "Start Right"}
    assert offer["data"]["action_data"]["start_button"].endswith("_right_start_charge")
    assert offer["data"]["action_data"]["name"] == "Riverside - Bay 4 - Charger B Right"
    # Shown in Android Auto, delivered at once, on its own channel.
    assert offer["data"]["car_ui"] is True
    assert offer["data"]["channel"] == "Mer charger"
    assert offer["data"]["ttl"] == 0


async def test_preview_says_nothing_free_when_all_taken(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    mock_client.find_stations_by_ids.return_value = busy(
        stations_from_fixture("stations_by_ids.json"), 11241, 11242, 11243, 11244
    )
    sent = await setup_blueprint(hass, mock_config_entry, [11242, 11243])
    await run_by_hand(hass)
    (notice,) = sent
    assert notice["title"] == "No Mer charger free"
    assert notice["data"]["car_ui"] is True


async def test_socket_freeing_up_only_notifies_in_the_zone(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    live = stations_from_fixture("stations_by_ids.json")
    mock_client.find_stations_by_ids.return_value = busy(live, 11243, 11244, 11242)
    sent = await setup_blueprint(hass, mock_config_entry, [11243])
    coordinator = mock_config_entry.runtime_data

    hass.states.async_set("person.driver", "not_home", AWAY)
    await hass.async_block_till_done()
    assert [n["message"] for n in sent] == ["clear_notification"]  # leaving clears it
    sent.clear()
    mock_client.find_stations_by_ids.return_value = busy(live, 11244, 11242)  # 11243 frees
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert sent == []  # away: no offer, and no "nothing free" either

    mock_client.find_stations_by_ids.return_value = busy(live, 11243, 11244, 11242)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    hass.states.async_set("person.driver", "Work", WORK)
    # Walking back into the zone is itself an arrival, with the socket taken.
    assert [n["title"] for n in sent] == ["No Mer charger free"]
    mock_client.find_stations_by_ids.return_value = busy(live, 11244, 11242)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    arrival, offer = sent
    assert offer["title"] == "Mer charger free"
    assert offer["message"] == "Riverside - Bay 3 - Charger A"
    assert offer["data"]["actions"][0]["title"] == "Start Left"
    assert offer["data"]["tag"] == arrival["data"]["tag"]  # replaces the earlier notice


async def test_tapping_start_presses_the_socket_button(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    sent = await setup_blueprint(hass, mock_config_entry, [11243])
    await run_by_hand(hass)
    action_data = sent[0]["data"]["action_data"]
    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": "MER_START_CHARGE", "action_data": action_data},
    )
    await hass.async_block_till_done()
    mock_client.start_charge.assert_awaited_once_with(11243)
    assert sent[-1]["message"] == "Starting Riverside - Bay 3 - Charger A Left..."
    assert sent[-1]["data"]["car_ui"] is True  # replaces the offer in the car too


async def test_device_tracker_works_as_the_tracker(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """A car's tracker instead of a person: arriving in the zone sends the offer."""
    sent = await setup_blueprint(hass, mock_config_entry, [11243], tracker="device_tracker.car")
    hass.states.async_set("device_tracker.car", "not_home", AWAY)
    await hass.async_block_till_done()
    hass.states.async_set("device_tracker.car", "Work", WORK)
    await hass.async_block_till_done()
    (offer,) = sent
    assert offer["title"] == "Mer charger free"
    assert offer["data"]["actions"][0]["title"] == "Start Left"


async def test_text_tap_target_and_extra_data_are_options(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    sent = await setup_blueprint(
        hass,
        mock_config_entry,
        [11243],
        free_title="{{ socket }} is free",
        free_message="At {{ charger }}",
        tap_opens="/dashboard-ev/0",
        extra_data={"notification_icon": "mdi:ev-station"},
    )
    await run_by_hand(hass)
    (offer,) = sent
    assert offer["title"] == "Left is free"
    assert offer["message"] == "At Riverside - Bay 3 - Charger A"
    assert offer["data"]["clickAction"] == offer["data"]["url"] == "/dashboard-ev/0"
    assert offer["data"]["notification_icon"] == "mdi:ev-station"
    assert offer["data"]["sticky"] is True
    assert offer["data"]["push"] == {"interruption-level": "time-sensitive"}


async def test_nothing_free_text_is_an_option(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    mock_client.find_stations_by_ids.return_value = busy(
        stations_from_fixture("stations_by_ids.json"), 11243
    )
    sent = await setup_blueprint(
        hass, mock_config_entry, [11243], none_title="All taken", none_message="Try later"
    )
    await run_by_hand(hass)
    (notice,) = sent
    assert (notice["title"], notice["message"]) == ("All taken", "Try later")
    assert "clickAction" not in notice["data"]  # no tap target set: the app opens as usual


async def test_offered_socket_taken_offers_the_next_quietly(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    live = stations_from_fixture("stations_by_ids.json")
    sent = await setup_blueprint(hass, mock_config_entry, [11243, 11244])
    coordinator = mock_config_entry.runtime_data
    await run_by_hand(hass)
    assert sent[-1]["data"]["actions"][0]["title"] == "Start Left"

    mock_client.find_stations_by_ids.return_value = busy(live, 11243)  # someone takes Left
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    update = sent[-1]
    assert update["data"]["actions"][0]["title"] == "Start Right"
    assert update["data"]["alert_once"] is True
    assert update["data"]["push"] == {"interruption-level": "passive"}

    mock_client.find_stations_by_ids.return_value = busy(live, 11243, 11244)  # and Right
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert sent[-1]["title"] == "No Mer charger free"
    assert sent[-1]["data"]["alert_once"] is True


async def test_taking_a_socket_not_on_offer_changes_nothing(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    live = stations_from_fixture("stations_by_ids.json")
    sent = await setup_blueprint(hass, mock_config_entry, [11243, 11244])
    await run_by_hand(hass)
    mock_client.find_stations_by_ids.return_value = busy(live, 11244)  # Right, not offered
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert len(sent) == 1


async def test_your_own_start_does_not_rewrite_the_notification(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Tapping Start takes the socket; "Starting..." must not be replaced by the next offer."""
    live = stations_from_fixture("stations_by_ids.json")
    sent = await setup_blueprint(hass, mock_config_entry, [11243, 11244])
    await run_by_hand(hass)
    mock_client.find_stations_by_ids.return_value = busy(live, 11243, status="PREPARING")
    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": "MER_START_CHARGE", "action_data": sent[0]["data"]["action_data"]},
    )
    await hass.async_block_till_done()
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert sent[-1]["message"] == "Starting Riverside - Bay 3 - Charger A Left..."


async def test_your_actions_run_when_charging_starts_on_a_chosen_socket(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket,
) -> None:
    hooked: list[ServiceCall] = []

    async def hook(call: ServiceCall) -> None:
        hooked.append(call)

    hass.services.async_register("test", "charging_hook", hook)
    # The session fixture runs on 11242 (Charger B Right).
    sent = await setup_blueprint(
        hass,
        mock_config_entry,
        [11242],
        when_charging=[{"action": "test.charging_hook"}],
    )
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert len(hooked) == 1
    assert sent[-1]["message"] == "clear_notification"


async def test_your_actions_skip_a_charge_on_another_socket(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket,
) -> None:
    hooked: list[ServiceCall] = []

    async def hook(call: ServiceCall) -> None:
        hooked.append(call)

    hass.services.async_register("test", "charging_hook", hook)
    await setup_blueprint(
        hass,
        mock_config_entry,
        [11243],
        when_charging=[{"action": "test.charging_hook"}],
    )
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await mock_config_entry.runtime_data.async_refresh()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))  # wait times out
    await hass.async_block_till_done()
    assert hooked == []


async def test_charger_you_just_left_is_not_offered(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket,
) -> None:
    """Unplugging from Charger B Right after arriving: offer Charger A instead."""
    sent = await setup_blueprint(hass, mock_config_entry, [11242, 11243])
    coordinator = mock_config_entry.runtime_data
    mock_client.find_last_active_charge_socket.return_value = charging_socket  # on 11242
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    mock_client.find_last_active_charge_socket.return_value = None  # unplugged
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    sent.clear()
    await run_by_hand(hass)
    (offer,) = sent
    assert offer["message"] == "Riverside - Bay 3 - Charger A"


async def test_your_conditions_gate_real_offers_but_not_a_preview(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    hass.states.async_set("input_boolean.car_needs_charge", "off")
    sent = await setup_blueprint(
        hass,
        mock_config_entry,
        [11243],
        only_if=[
            {"condition": "state", "entity_id": "input_boolean.car_needs_charge", "state": "on"}
        ],
    )

    async def arrive() -> None:
        hass.states.async_set("person.driver", "not_home", AWAY)
        await hass.async_block_till_done()
        hass.states.async_set("person.driver", "Work", WORK)
        await hass.async_block_till_done()

    await arrive()
    assert [n["message"] for n in sent] == ["clear_notification"]  # leaving; no offer
    await run_by_hand(hass)
    assert sent[-1]["title"] == "Mer charger free"  # a preview skips your conditions

    sent.clear()
    hass.states.async_set("input_boolean.car_needs_charge", "on")
    await arrive()
    assert sent[-1]["title"] == "Mer charger free"
