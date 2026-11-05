"""Run the "offer a free charger" blueprint against the integration and check what it sends."""

from __future__ import annotations

from contextlib import suppress
from datetime import timedelta
from pathlib import Path
import shutil
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.automation import DOMAIN as AUTOMATION_DOMAIN
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.mer.const import DOMAIN
from custom_components.mer.driivz.exceptions import ApiError
from tests.conftest import make_config_entry
from tests.fake_portal import FakePortal
from tests.helpers import fast_commands, setup_integration

BLUEPRINT = (
    Path(__file__).parent.parent / "blueprints" / "automation" / "mer" / "offer_free_charger.yaml"
)
WORK = {"latitude": 51.5, "longitude": -0.12}
AWAY = {"latitude": 52.5, "longitude": -1.5}


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
    fast_commands(mock_config_entry)
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


async def leave_and_arrive(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, tracker: str = "person.driver"
) -> None:
    """Leave the zone and come back after the 2-minute bounce window."""
    hass.states.async_set(tracker, "not_home", AWAY)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=3))
    hass.states.async_set(tracker, "Work", WORK)
    await hass.async_block_till_done()


async def run_by_hand(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        AUTOMATION_DOMAIN, "trigger", {"entity_id": "automation.offer"}, blocking=True
    )
    await hass.async_block_till_done()


async def test_preview_sends_the_offer_for_the_first_free_socket_in_order(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
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
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    portal.take(11241, 11242, 11243, 11244)
    sent = await setup_blueprint(hass, mock_config_entry, [11242, 11243])
    await run_by_hand(hass)
    (notice,) = sent
    assert notice["title"] == "No Mer charger free"
    assert notice["data"]["car_ui"] is True


async def test_socket_freeing_up_only_notifies_in_the_zone(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
    freezer: FrozenDateTimeFactory,
) -> None:
    portal.take(11243, 11244, 11242)
    sent = await setup_blueprint(hass, mock_config_entry, [11243])
    coordinator = mock_config_entry.runtime_data

    hass.states.async_set("person.driver", "not_home", AWAY)
    await hass.async_block_till_done()
    assert [n["message"] for n in sent] == ["clear_notification"]  # leaving clears it
    sent.clear()
    portal.free(11243)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert sent == []  # away: no offer, and no "nothing free" either

    portal.take(11243)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=3))
    hass.states.async_set("person.driver", "Work", WORK)
    # Walking back into the zone is itself an arrival, with the socket taken.
    assert [n["title"] for n in sent] == ["No Mer charger free"]
    portal.free(11243)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    arrival, offer = sent
    assert offer["title"] == "Mer charger free"
    assert offer["message"] == "Riverside - Bay 3 - Charger A"
    assert offer["data"]["actions"][0]["title"] == "Start Left"
    assert offer["data"]["tag"] == arrival["data"]["tag"]  # replaces the earlier notice


async def tap_start(hass: HomeAssistant, sent: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tap Start on the first offer; return what was sent from then on."""
    before = len(sent)
    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": "MER_START_CHARGE", "action_data": sent[0]["data"]["action_data"]},
    )
    await hass.async_block_till_done()
    return sent[before:]


async def test_tapping_start_presses_the_socket_button_and_says_so(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    """No result from the charger within the wait: say the start was sent anyway."""
    sent = await setup_blueprint(hass, mock_config_entry, [11243])
    await run_by_hand(hass)
    starting, outcome = await tap_start(hass, sent)
    assert portal.calls["start_charge"] == [(11243,)]
    assert starting["title"] == "Starting Riverside - Bay 3 - Charger A Left"
    assert starting["data"]["car_ui"] is True  # replaces the offer in the car too
    assert "alert_once" not in starting["data"]  # alerts, so it shows in the car
    assert outcome["title"] == "Start sent to Riverside - Bay 3 - Charger A Left"


async def test_start_confirmed_charging_dismisses_the_notification(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    sent = await setup_blueprint(hass, mock_config_entry, [11243])
    await run_by_hand(hass)
    portal.charge(11243)
    after = await tap_start(hass, sent)
    assert after[-1]["message"] == "clear_notification"


async def test_start_waiting_for_the_cable_says_plug_in(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    sent = await setup_blueprint(hass, mock_config_entry, [11243])
    await run_by_hand(hass)
    portal.await_cable(11243)
    after = await tap_start(hass, sent)
    assert after[-1]["title"] == "Plug in"
    assert after[-1]["message"] == "Riverside - Bay 3 - Charger A Left is waiting for your cable."


async def test_start_refused_says_why(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    from custom_components.mer.driivz.exceptions import ApiError

    sent = await setup_blueprint(hass, mock_config_entry, [11243])
    await run_by_hand(hass)
    portal.fail("start_charge", ApiError("NOT_AUTHORIZED"))
    after = await tap_start(hass, sent)
    assert after[-1]["title"] == "Couldn't start Riverside - Bay 3 - Charger A Left"
    assert "NOT_AUTHORIZED" in after[-1]["message"]


async def test_device_tracker_works_as_the_tracker(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A car's tracker instead of a person: arriving in the zone sends the offer."""
    sent = await setup_blueprint(hass, mock_config_entry, [11243], tracker="device_tracker.car")
    await leave_and_arrive(hass, freezer, "device_tracker.car")
    (offer,) = sent
    assert offer["title"] == "Mer charger free"
    assert offer["data"]["actions"][0]["title"] == "Start Left"


async def test_text_tap_target_and_extra_data_are_options(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
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
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    portal.take(11243)
    sent = await setup_blueprint(
        hass, mock_config_entry, [11243], none_title="All taken", none_message="Try later"
    )
    await run_by_hand(hass)
    (notice,) = sent
    assert (notice["title"], notice["message"]) == ("All taken", "Try later")
    assert "clickAction" not in notice["data"]  # no tap target set: the app opens as usual


async def test_offered_socket_taken_offers_the_next_quietly(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    sent = await setup_blueprint(hass, mock_config_entry, [11243, 11244])
    coordinator = mock_config_entry.runtime_data
    await run_by_hand(hass)
    assert sent[-1]["data"]["actions"][0]["title"] == "Start Left"

    portal.take(11243)  # someone takes Left
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    update = sent[-1]
    assert update["data"]["actions"][0]["title"] == "Start Right"
    assert update["data"]["alert_once"] is True
    assert update["data"]["push"] == {"interruption-level": "passive"}

    portal.take(11244)  # and Right
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert sent[-1]["title"] == "No Mer charger free"
    assert sent[-1]["data"]["alert_once"] is True


async def test_taking_a_socket_not_on_offer_changes_nothing(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    sent = await setup_blueprint(hass, mock_config_entry, [11243, 11244])
    await run_by_hand(hass)
    portal.take(11244)  # Right, not offered
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert len(sent) == 1


async def test_your_own_start_does_not_rewrite_the_notification(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    """Tapping Start takes the socket; "Starting..." must not be replaced by the next offer."""
    sent = await setup_blueprint(hass, mock_config_entry, [11243, 11244])
    await run_by_hand(hass)
    portal.await_cable(11243)
    await tap_start(hass, sent)
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert sent[-1]["title"] == "Plug in"  # not an offer for Right


async def add_other_account(hass: HomeAssistant) -> MockConfigEntry:
    """A second Mer account; added last, its entities come first in expand()."""
    other = make_config_entry([6042, 6041], username="other@example.com")
    await setup_integration(hass, other)
    fast_commands(other)
    return other


async def press_start(hass: HomeAssistant, entry: MockConfigEntry, socket_id: int) -> None:
    button = er.async_get(hass).async_get_entity_id(
        "button", DOMAIN, f"{entry.entry_id}_socket_{socket_id}_start_charge"
    )
    with suppress(HomeAssistantError):  # unconfirmed by the charger, but recorded
        await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    await hass.async_block_till_done()


async def test_tap_outcome_reads_this_accounts_last_command(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    """Two accounts: the outcome comes from the tapped socket's account, not the other's."""
    sent = await setup_blueprint(hass, mock_config_entry, [11243])
    other = await add_other_account(hass)
    await press_start(hass, other, 11244)  # the other account's own last command
    await run_by_hand(hass)
    portal.await_cable(11243)
    after = await tap_start(hass, sent)
    assert after[-1]["title"] == "Plug in"


async def test_another_accounts_start_is_not_your_own(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    """Two accounts: a start by the other one still moves your offer on."""
    sent = await setup_blueprint(hass, mock_config_entry, [11243, 11244])
    other = await add_other_account(hass)
    await run_by_hand(hass)
    await press_start(hass, other, 11243)  # the other account takes Left
    portal.take(11243)
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert sent[-1]["data"]["actions"][0]["title"] == "Start Right"


async def test_your_actions_run_when_charging_starts_on_a_chosen_socket(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
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
    portal.charge(11242)
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert len(hooked) == 1
    assert sent[-1]["message"] == "clear_notification"


async def test_your_actions_skip_a_charge_on_another_socket(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
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
    portal.charge(11242)
    await mock_config_entry.runtime_data.async_refresh()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))  # wait times out
    await hass.async_block_till_done()
    assert hooked == []


async def stop_charging_on_charger_b_right(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> tuple[list[dict[str, Any]], Any]:
    """Charge on Charger B Right (11242), then stop; return what's sent and a poller."""
    sent = await setup_blueprint(hass, mock_config_entry, [11242, 11241, 11243, 11244])
    coordinator = mock_config_entry.runtime_data

    async def poll() -> None:
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    portal.charge(11242)
    await poll()
    portal.stop(11242)  # you stop
    await poll()
    portal.free(11242)  # and unplug: Right is free
    await poll()
    sent.clear()
    return sent, poll


async def test_after_you_stop_held_back_sockets_are_not_offered(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
) -> None:
    """Seen live on 2026-10-12: stopping to go home offered a charger already free.

    The sockets' `held_back` decides; its rules are tested on the session tracker.
    """
    sent, poll = await stop_charging_on_charger_b_right(hass, mock_config_entry, portal)
    await run_by_hand(hass)  # Charger A was already free; Right is the one you left
    assert [n["title"] for n in sent] == ["No Mer charger free"]
    sent.clear()

    portal.free(11241)  # Charger B Left frees up: not held back
    await poll()
    (offer,) = sent
    assert offer["message"] == "Riverside - Bay 4 - Charger B"
    assert offer["data"]["actions"][0]["title"] == "Start Left"


async def test_a_held_back_socket_taken_changes_nothing(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
) -> None:
    """Never on offer, so taking it isn't news. `held_back` is read from before it was taken,
    which releases it (2026-10-15)."""
    sent, poll = await stop_charging_on_charger_b_right(hass, mock_config_entry, portal)
    portal.take(11243)  # one already free is taken
    await poll()
    portal.take(11242)  # and someone takes yours
    await poll()
    assert sent == []


async def test_your_conditions_gate_real_offers_but_not_a_preview(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
    freezer: FrozenDateTimeFactory,
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
        await leave_and_arrive(hass, freezer)

    await arrive()
    assert [n["message"] for n in sent] == ["clear_notification"]  # leaving; no offer
    await run_by_hand(hass)
    assert sent[-1]["title"] == "Mer charger free"  # a preview skips your conditions

    sent.clear()
    hass.states.async_set("input_boolean.car_needs_charge", "on")
    await arrive()
    assert sent[-1]["title"] == "Mer charger free"


def android_tap(offer: dict[str, Any]) -> dict[str, Any]:
    """What the Android app sends back for a tap: the notification's own fields as strings,
    and no action_data (captured live on 2026-10-07)."""
    return {
        "action": "MER_START_CHARGE",
        "action_1_key": "MER_START_CHARGE",
        "action_1_title": offer["data"]["actions"][0]["title"],
        "title": offer["title"],
        "message": offer["message"],
        "tag": offer["data"]["tag"],
        "car_ui": "true",
        "channel": "Mer charger",
        "device_id": "0123456789abcdef",
    }


async def test_android_tap_without_action_data_starts_the_offered_socket(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    sent = await setup_blueprint(hass, mock_config_entry, [11243, 11244])
    await run_by_hand(hass)
    hass.bus.async_fire("mobile_app_notification_action", android_tap(sent[0]))
    await hass.async_block_till_done()
    assert portal.calls["start_charge"] == [(11243,)]
    assert sent[1]["title"] == "Starting Riverside - Bay 3 - Charger A Left"


async def test_repeat_taps_press_once(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    """One tap reported by both the car and the phone, then impatient taps: one start."""
    sent = await setup_blueprint(hass, mock_config_entry, [11243])
    await run_by_hand(hass)
    for _ in range(3):
        hass.bus.async_fire("mobile_app_notification_action", android_tap(sent[0]))
    await hass.async_block_till_done()
    assert portal.calls["start_charge"] == [(11243,)]


async def test_android_tap_on_an_outdated_offer_says_too_late(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    """The notification showed Left, but Left has gone: don't start Right instead."""
    sent = await setup_blueprint(hass, mock_config_entry, [11243, 11244])
    await run_by_hand(hass)
    tap = android_tap(sent[0])
    portal.take(11243)
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    hass.bus.async_fire("mobile_app_notification_action", tap)
    await hass.async_block_till_done()
    assert portal.calls["start_charge"] == []
    assert sent[-1]["title"] == "Too late"
    assert sent[-1]["message"] == "Riverside - Bay 3 - Charger A Left is no longer free."


async def test_tap_on_another_automations_notification_is_ignored(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    sent = await setup_blueprint(hass, mock_config_entry, [11243])
    await run_by_hand(hass)
    tap = {**android_tap(sent[0]), "tag": "mer_free_charger_automation.someone_else"}
    hass.bus.async_fire("mobile_app_notification_action", tap)
    await hass.async_block_till_done()
    assert portal.calls["start_charge"] == []


async def test_a_less_preferred_socket_freeing_up_does_not_alert_again(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    """Charger A Left is on offer; Right freeing up leaves the offer as it is."""
    portal.take(11244)
    sent = await setup_blueprint(hass, mock_config_entry, [11243, 11244])
    await run_by_hand(hass)
    assert len(sent) == 1
    portal.free(11244)  # Right frees up
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert len(sent) == 1


async def test_offer_while_charging_offers_on_arrival_during_a_charge(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A second car, a friend, or a shared account charging elsewhere."""
    # Watching the session's socket (11242) too, so "When charging starts" doesn't wait
    # for it on the frozen clock.
    sent = await setup_blueprint(hass, mock_config_entry, [11243, 11242], offer_while_charging=True)
    portal.charge(11242)
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    await leave_and_arrive(hass, freezer)
    assert sent[-1]["title"] == "Mer charger free"
    assert sent[-1]["message"] == "Riverside - Bay 3 - Charger A"


async def test_without_the_option_no_offer_while_charging(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
    freezer: FrozenDateTimeFactory,
) -> None:
    sent = await setup_blueprint(hass, mock_config_entry, [11243, 11242])
    portal.charge(11242)
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    sent.clear()
    await leave_and_arrive(hass, freezer)
    assert [n["message"] for n in sent] == ["clear_notification"]  # leaving; no offer


async def test_offer_while_charging_moves_the_offer_on_when_a_charge_starts(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
) -> None:
    """Instead of clearing, the notification quietly offers the next free socket."""
    sent = await setup_blueprint(hass, mock_config_entry, [11243, 11244], offer_while_charging=True)
    portal.charge(11242)
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    update = sent[-1]
    assert update["title"] == "Mer charger free"
    assert update["data"]["actions"][0]["title"] == "Start Left"
    assert update["data"]["alert_once"] is True


async def test_offer_while_charging_offers_already_free_sockets_after_a_stop(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
) -> None:
    """Swapping cars: what was already free, and the socket just left, stay on offer."""
    sent = await setup_blueprint(hass, mock_config_entry, [11242, 11243], offer_while_charging=True)
    coordinator = mock_config_entry.runtime_data
    portal.charge(11242)
    await coordinator.async_refresh()
    portal.free(11242)  # stopped and unplugged
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    sent.clear()
    await run_by_hand(hass)
    (offer,) = sent
    assert offer["message"] == "Riverside - Bay 4 - Charger B"  # the socket just left


async def test_bouncing_back_into_the_zone_is_not_an_arrival(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    portal: FakePortal,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Seen live on 2026-11-02: leaving after a stop, the location bounced back into the
    zone for 10 s and the "arrival" offered the socket just unplugged."""
    sent = await setup_blueprint(hass, mock_config_entry, [11243])
    hass.states.async_set("person.driver", "not_home", AWAY)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set("person.driver", "Work", WORK)
    await hass.async_block_till_done()
    assert [n["message"] for n in sent] == ["clear_notification"]  # leaving; no offer


async def blip_unavailable(hass: HomeAssistant, entry: MockConfigEntry, portal: FakePortal) -> None:
    """A failed poll makes every entity unavailable, as a restart or reload does; then back."""
    coordinator = entry.runtime_data
    portal.fail("find_stations_by_ids", ApiError("DOWN"))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    portal.clear("find_stations_by_ids")
    await coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_coming_back_from_unavailable_is_not_a_charge_starting(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    """Seen live on 2026-11-05: each restart during a charge ran the "when charging" actions."""
    hooked: list[ServiceCall] = []

    async def hook(call: ServiceCall) -> None:
        hooked.append(call)

    hass.services.async_register("test", "charging_hook", hook)
    portal.charge(11242)
    await setup_blueprint(
        hass, mock_config_entry, [11242], when_charging=[{"action": "test.charging_hook"}]
    )
    charging = "binary_sensor.mer_account_charging"
    portal.fail("find_stations_by_ids", ApiError("DOWN"))
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(charging).state == "unavailable"
    await blip_unavailable(hass, mock_config_entry, portal)
    assert hass.states.get(charging).state == "on"
    assert hooked == []


async def test_coming_back_from_unavailable_changes_no_offer(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    """Unavailable isn't taken, and back isn't freed: no rewrite, no second alert."""
    sent = await setup_blueprint(hass, mock_config_entry, [11243])
    await run_by_hand(hass)
    await blip_unavailable(hass, mock_config_entry, portal)
    assert len(sent) == 1


async def test_a_charger_back_in_service_is_offered(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, portal: FakePortal
) -> None:
    """The portal's own UNAVAILABLE status (out of service) is a real state: back is freed."""
    portal.take(11243, status="UNAVAILABLE")
    sent = await setup_blueprint(hass, mock_config_entry, [11243])
    assert hass.states.get(socket_status_entity(hass, mock_config_entry.entry_id, 11243)).state == (
        "unavailable"
    )
    portal.free(11243)
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    (offer,) = sent
    assert offer["message"] == "Riverside - Bay 3 - Charger A"
