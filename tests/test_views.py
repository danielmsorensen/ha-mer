"""Pure tests: entity values and attributes composed from one MerData snapshot."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from custom_components.mer.binary_sensor import ACCOUNT_BINARY_SENSORS, STATION_BINARY_SENSORS
from custom_components.mer.coordinator import MerData
from custom_components.mer.driivz.const import (
    DENIED_FOR_SOCKET_STATE,
    OP_START_CHARGE,
    OP_START_CHARGE_NOW,
)
from custom_components.mer.driivz.models import SocketCapabilities, Transaction
from custom_components.mer.entity import (
    START_CHARGE_KEY,
    session_view,
    socket_unique_id,
    socket_view,
)
from custom_components.mer.sensor import ACCOUNT_SENSORS, AGGREGATE_SENSORS, SOCKET_SENSORS
from custom_components.mer.session_tracker import ActiveSession, EndedSession
from tests.helpers import load_json_fixture, station_from_fixture, stations_from_fixture

ENDED_AT = datetime(2026, 10, 1, tzinfo=UTC)


def button(socket_id: int) -> str:
    return f"button.start_{socket_id}"


def described(descriptions: tuple[Any, ...], key: str) -> Any:
    return next(d for d in descriptions if d.key == key)


def session(**changes: Any) -> ActiveSession:
    """Your session on Charger B Right (6041/11242)."""
    base = ActiveSession(
        socket_id=11242,
        station_id=6041,
        socket_name="Right",
        station_caption="(MER-FS-EX00002) Riverside - Bay 4 - Charger B",
        transaction_id=1,
        started_at=None,
        duration=timedelta(minutes=5),
        energy_kwh=1.5,
        cost=0.0,
        currency="GBP",
    )
    return replace(base, **changes)


def snapshot(**changes: Any) -> MerData:
    """Charger A (6042): both free. Charger B (6041): Left charging, Right free."""
    data = MerData(
        station_ids=(6042, 6041),
        stations={s.id: s for s in stations_from_fixture("stations_by_ids.json")},
        details={
            6042: station_from_fixture("station_6042.json"),
            6041: station_from_fixture("station_6041.json"),
        },
        last_transaction=Transaction.from_dict(load_json_fixture("transactions.json")["data"][0]),
    )
    return replace(data, **changes)


# ----- lookups -----------------------------------------------------------


def test_station_merges_detail_and_live() -> None:
    data = snapshot()
    station = data.station(6041)
    assert station is not None
    assert station.model_name == "Eve Double Pro-line"
    assert station.status == "CHARGING"
    socket = data.socket(6041, 11241)
    assert socket is not None and socket.name == "Left" and socket.status == "CHARGING"
    assert data.socket(6041, 1) is None
    assert data.station(999) is None
    assert [s.id for s in data.configured_stations()] == [6042, 6041]


def test_start_allowed_follows_status_then_capabilities() -> None:
    free = snapshot().socket(6042, 11243)
    charging = snapshot().socket(6041, 11241)
    assert free is not None and charging is not None
    assert snapshot().start_allowed(free) is True  # unknown capabilities: status decides
    assert snapshot().start_allowed(charging) is False
    refused = SocketCapabilities(
        allowed=frozenset(), denied={OP_START_CHARGE: "CARD", OP_START_CHARGE_NOW: "CARD"}
    )
    assert snapshot(capabilities={11243: refused}).start_allowed(free) is False
    stale = SocketCapabilities(
        allowed=frozenset(), denied={OP_START_CHARGE: DENIED_FOR_SOCKET_STATE}
    )
    assert snapshot(capabilities={11243: stale}).start_allowed(free) is True


def test_snapshot_is_immutable() -> None:
    with pytest.raises(AttributeError):
        snapshot().active = session()  # type: ignore[misc]


# ----- views -------------------------------------------------------------


def test_socket_unique_id_format() -> None:
    assert socket_unique_id("entry", 11243, START_CHARGE_KEY) == "entry_socket_11243_start_charge"


def test_socket_view() -> None:
    data = snapshot()
    station = data.station(6042)
    socket = data.socket(6042, 11243)
    assert station is not None and socket is not None
    assert socket_view(station, socket, button) == {
        "charger": "Riverside - Bay 3 - Charger A",
        "socket": "Left",
        "station_id": 6042,
        "socket_id": 11243,
        "start_button": "button.start_11243",
    }


def test_session_view_prefers_session_caption_and_live_socket_label() -> None:
    assert session_view(snapshot(), session(socket_name=None)) == {
        "charger": "Riverside - Bay 4 - Charger B",
        "socket": "Right",
        "station_id": 6041,
        "socket_id": 11242,
    }


def test_session_view_on_a_charger_you_have_not_added() -> None:
    public = session(station_id=17886, socket_id=5, socket_name=None, station_caption=None)
    assert session_view(snapshot(), public) == {
        "charger": None,
        "socket": "Socket 5",
        "station_id": 17886,
        "socket_id": 5,
    }


# ----- socket sensors ----------------------------------------------------


def test_socket_status_attributes() -> None:
    data = snapshot(active=session())
    attributes = described(SOCKET_SENSORS, "status").attributes_fn
    station = data.station(6041)
    assert station is not None
    mine = attributes(data, station, data.socket(6041, 11242), button)
    assert mine == {
        "charger": "Riverside - Bay 4 - Charger B",
        "socket": "Right",
        "station_id": 6041,
        "socket_id": 11242,
        "start_button": "button.start_11242",
        "my_session": True,
        "held_back": False,
        "own_start": False,
        "max_power_kw": 7.0,
        "connector": "TYPE_2_MENNEKES",
    }
    other = attributes(data, station, data.socket(6041, 11241), button)
    assert other["my_session"] is False


def test_socket_status_held_back_and_own_start() -> None:
    """From the tracker's state in the snapshot; held back never while a session runs."""
    ended = EndedSession(session(), ENDED_AT, held_back=frozenset({11241}))
    data = snapshot(last_session_end=ended, own_starts=frozenset({11242}))
    attributes = described(SOCKET_SENSORS, "status").attributes_fn
    station = data.station(6041)
    assert station is not None
    left = attributes(data, station, data.socket(6041, 11241), button)
    right = attributes(data, station, data.socket(6041, 11242), button)
    assert (left["held_back"], left["own_start"]) == (True, False)
    assert (right["held_back"], right["own_start"]) == (False, True)
    charging = replace(data, active=session())
    assert attributes(charging, station, charging.socket(6041, 11241), button)["held_back"] is False


# ----- account sensors ---------------------------------------------------


def test_free_sockets_list() -> None:
    attributes = described(AGGREGATE_SENSORS, "available_sockets").attributes_fn(snapshot(), button)
    assert attributes["total_sockets"] == 4
    assert attributes["chargers"] == 2
    assert [(f["charger"], f["socket"]) for f in attributes["available_sockets"]] == [
        ("Riverside - Bay 3 - Charger A", "Left"),
        ("Riverside - Bay 3 - Charger A", "Right"),
        ("Riverside - Bay 4 - Charger B", "Right"),
    ]
    assert attributes["available_sockets"][0] == {
        "charger": "Riverside - Bay 3 - Charger A",
        "socket": "Left",
        "station_id": 6042,
        "socket_id": 11243,
        "start_button": "button.start_11243",
    }


def test_socket_counts() -> None:
    data = snapshot()
    assert described(AGGREGATE_SENSORS, "available_sockets").value_fn(data) == 3
    assert described(AGGREGATE_SENSORS, "sockets_in_use").value_fn(data) == 1


def test_active_session_name_and_attributes() -> None:
    data = snapshot(active=session())
    description = described(ACCOUNT_SENSORS, "active_session")
    assert description.value_fn(data) == "Riverside - Bay 4 - Charger B Right"
    assert description.attributes_fn(data, button) == {
        "charger": "Riverside - Bay 4 - Charger B",
        "socket": "Right",
        "station_id": 6041,
        "socket_id": 11242,
    }
    idle = snapshot()
    assert description.value_fn(idle) is None
    assert description.attributes_fn(idle, button) == {}


# ----- binary sensors ----------------------------------------------------


def charging_attributes(data: MerData) -> dict[str, Any]:
    return described(ACCOUNT_BINARY_SENSORS, "charging").attributes_fn(data)


def test_charging_attributes_while_charging() -> None:
    assert charging_attributes(snapshot(active=session())) == {
        "charger": "Riverside - Bay 4 - Charger B",
        "socket": "Right",
        "station_id": 6041,
        "socket_id": 11242,
        "ended_at": None,
    }


def test_charging_attributes_after_an_end_seen() -> None:
    ended = EndedSession(session(), ENDED_AT, freed=True, reused=True)
    assert charging_attributes(snapshot(last_session_end=ended)) == {
        "charger": "Riverside - Bay 4 - Charger B",
        "socket": "Right",
        "station_id": 6041,
        "socket_id": 11242,
        "ended_at": ENDED_AT.isoformat(),
    }


def test_charging_attributes_fall_back_to_history() -> None:
    data = snapshot()
    assert data.last_transaction is not None and data.last_transaction.stopped_at is not None
    assert charging_attributes(data) == {
        "charger": "Riverside - Bay 4 - Charger B",
        "socket": None,
        "station_id": 6041,
        "socket_id": None,
        "ended_at": data.last_transaction.stopped_at.isoformat(),
    }
    assert charging_attributes(snapshot(last_transaction=None))["charger"] is None


def test_live_status_from_snapshot() -> None:
    since = ENDED_AT
    description = described(ACCOUNT_BINARY_SENSORS, "live_status")
    connected = snapshot(push_connected=True, push_connected_since=since)
    assert description.is_on_fn(connected) is True
    assert description.attributes_fn(connected) == {"connected_since": since.isoformat()}
    assert description.is_on_fn(snapshot()) is False
    assert description.attributes_fn(snapshot()) == {"connected_since": None}


def test_session_here() -> None:
    description = described(STATION_BINARY_SENSORS, "session_here")
    data = snapshot(active=session())
    assert description.is_on_fn(data, 6041) is True
    assert description.attributes_fn(data, 6041) == {"socket": "Right"}
    assert description.is_on_fn(data, 6042) is False
    assert description.attributes_fn(data, 6042) == {"socket": None}


def test_session_here_socket_missing_from_charger_keeps_session_name() -> None:
    description = described(STATION_BINARY_SENSORS, "session_here")
    gone = snapshot(active=session(socket_id=1, socket_name=None))
    assert description.attributes_fn(gone, 6041) == {"socket": None}
    named = snapshot(active=session(socket_id=1, socket_name="Far"))
    assert description.attributes_fn(named, 6041) == {"socket": "Far"}
