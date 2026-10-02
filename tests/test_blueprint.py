"""Run the "offer a free charger" blueprint against the integration and check what it sends."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.components.automation import DOMAIN as AUTOMATION_DOMAIN
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
