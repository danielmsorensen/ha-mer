"""Start/stop a socket and wait for the outcome, through the Commands interface."""

from __future__ import annotations

from collections import deque

from custom_components.mer.commands import Cadence, Commands, View
from custom_components.mer.driivz.exceptions import (
    ApiError,
    AuthError,
    DriivzConnectionError,
    DriivzError,
    RateLimitError,
)
from custom_components.mer.driivz.models import Socket, SocketCapabilities, Station
from tests.fake_portal import FakePortal

STATION = 6042
SOCKET = 11243


def view(status: str, active_socket_id: int | None = None) -> View:
    """Riverside Bay 3 Charger A with socket 11243 in `status`."""
    station = Station(
        id=STATION,
        caption="Riverside - Bay 3 - Charger A",
        sockets=(Socket(id=SOCKET, status=status), Socket(id=11244, status="AVAILABLE")),
    )
    return View(station=station, active_socket_id=active_socket_id)


class FakeChanges:
    """Pushed views and poll results, in order; records how often each was asked."""

    def __init__(
        self,
        polls: list[View | DriivzError] | None = None,
        pushes: list[View | None] | None = None,
    ) -> None:
        self._polls = deque(polls or [])
        self._pushes = deque(pushes or [])
        self.polled = 0

    async def pushed(self, station_id: int | None, timeout: float) -> View | None:
        return self._pushes.popleft() if self._pushes else None

    async def poll(self, station_id: int | None) -> View:
        self.polled += 1
        result = self._polls.popleft()
        if isinstance(result, DriivzError):
            raise result
        return result


def make_client(remaining: int | None = 9) -> FakePortal:
    client = FakePortal()
    client.rate_limit_remaining = remaining
    return client


def commands(client: FakePortal, changes: FakeChanges, max_polls: int = 3) -> Commands:
    return Commands(client, changes, Cadence(interval_seconds=0, max_polls=max_polls))


async def test_stop_waits_until_the_session_is_gone() -> None:
    client = make_client()
    changes = FakeChanges(polls=[view("CHARGING", SOCKET), view("FINISHING")])

    outcome = await commands(client, changes).stop(STATION, SOCKET)

    assert client.calls["stop_charge"] == [(SOCKET,)]
    assert outcome.result == "stopped"
    assert changes.polled == 2


async def test_stop_unconfirmed_when_nothing_changes_in_time() -> None:
    changes = FakeChanges(polls=[view("CHARGING", SOCKET)] * 3)

    outcome = await commands(make_client(), changes, max_polls=3).stop(STATION, SOCKET)

    assert outcome.result == "unconfirmed"
    assert changes.polled == 3


async def test_a_pushed_change_resolves_without_polling() -> None:
    changes = FakeChanges(pushes=[view("FINISHING")])

    outcome = await commands(make_client(), changes).stop(STATION, SOCKET)

    assert outcome.result == "stopped"
    assert changes.polled == 0


async def test_a_push_that_settles_nothing_waits_again_without_polling() -> None:
    changes = FakeChanges(pushes=[view("CHARGING", SOCKET), view("FINISHING")])

    outcome = await commands(make_client(), changes).stop(STATION, SOCKET)

    assert outcome.result == "stopped"
    assert changes.polled == 0


async def test_polling_stops_early_at_the_rate_limit_floor() -> None:
    """Headroom 2 or less: give up rather than exhaust it; the normal poll catches up."""
    changes = FakeChanges(polls=[view("FINISHING")])

    outcome = await commands(make_client(remaining=2), changes).stop(STATION, SOCKET)

    assert outcome.result == "unconfirmed"
    assert changes.polled == 0


async def test_a_failed_poll_waits_for_the_next() -> None:
    changes = FakeChanges(polls=[DriivzError("down"), view("FINISHING")])

    outcome = await commands(make_client(), changes).stop(STATION, SOCKET)

    assert outcome.result == "stopped"
    assert changes.polled == 2


async def test_a_refused_stop_is_rejected_without_waiting() -> None:
    client = make_client()
    refusal = ApiError("STOP_FAILED")
    client.fail("stop_charge", refusal)
    changes = FakeChanges()

    outcome = await commands(client, changes).stop(STATION, SOCKET)

    assert outcome.result == "rejected"
    assert outcome.message == "STOP_FAILED"
    assert outcome.error is refusal
    assert changes.polled == 0


async def test_start_waits_for_charging() -> None:
    client = make_client()
    changes = FakeChanges(polls=[view("PREPARING"), view("CHARGING")])

    outcome = await commands(client, changes).start(STATION, SOCKET, "PREPARING")

    assert client.calls["start_charge"] == [(SOCKET,)]
    assert outcome.result == "charging"
    assert outcome.method == "start"
    assert changes.polled == 2


async def test_start_resolves_once_your_session_is_on_the_socket() -> None:
    changes = FakeChanges(polls=[view("PREPARING", active_socket_id=SOCKET)])

    outcome = await commands(make_client(), changes).start(STATION, SOCKET, "PREPARING")

    assert outcome.result == "charging"


async def test_start_on_a_free_socket_settles_when_it_waits_for_the_cable() -> None:
    changes = FakeChanges(polls=[view("AVAILABLE"), view("PREPARING")])

    outcome = await commands(make_client(), changes).start(STATION, SOCKET, "AVAILABLE")

    assert outcome.result == "awaiting_cable"


async def test_start_on_a_plugged_in_socket_still_waits_for_charging() -> None:
    changes = FakeChanges(polls=[view("PREPARING")] * 3)

    outcome = await commands(make_client(), changes).start(STATION, SOCKET, "PREPARING")

    assert outcome.result == "unconfirmed"
    assert outcome.method == "start"


def caps(allowed: set[str], denied: dict[str, str] | None = None) -> SocketCapabilities:
    return SocketCapabilities(allowed=frozenset(allowed), denied=denied or {})


async def test_start_reads_the_capabilities_for_the_last_seen_status() -> None:
    client = make_client()
    read = {SOCKET: caps({"START_CHARGE_FROM_SERVER"}), 11244: caps(set())}
    client.capabilities = read
    changes = FakeChanges(polls=[view("CHARGING")])

    outcome = await commands(client, changes).start(STATION, SOCKET, None)

    assert client.calls["find_socket_capabilities"] == [(STATION, SOCKET, "UNKNOWN")]
    assert client.calls["start_charge"] == [(SOCKET,)]
    assert outcome.method == "start"
    assert outcome.capabilities == read


async def test_start_uses_charge_now_when_only_that_is_allowed() -> None:
    client = make_client()
    client.capabilities = {SOCKET: caps({"START_CHARGE_NOW"})}
    changes = FakeChanges(polls=[view("CHARGING")])

    outcome = await commands(client, changes).start(STATION, SOCKET, "PREPARING")

    assert client.calls["start_charge_now"] == [(SOCKET,)]
    assert client.calls["start_charge"] == []
    assert (outcome.result, outcome.method) == ("charging", "charge_now")


async def test_start_refused_up_front_with_the_portals_reason() -> None:
    client = make_client()
    read = {SOCKET: caps(set(), {"START_CHARGE_FROM_SERVER": "INVALID_CARD"})}
    client.capabilities = read

    outcome = await commands(client, FakeChanges()).start(STATION, SOCKET, "AVAILABLE")

    assert client.calls["start_charge"] == []
    assert client.calls["start_charge_now"] == []
    assert outcome.result == "rejected"
    assert outcome.message == "START_NOT_ALLOWED: INVALID_CARD"
    assert outcome.method is None
    assert outcome.capabilities == read


async def test_start_sends_the_normal_start_when_capabilities_are_unreadable() -> None:
    client = make_client()
    client.fail("find_socket_capabilities", DriivzConnectionError("down"))
    changes = FakeChanges(polls=[view("CHARGING")])

    outcome = await commands(client, changes).start(STATION, SOCKET, "PREPARING")

    assert client.calls["start_charge"] == [(SOCKET,)]
    assert (outcome.result, outcome.method) == ("charging", "start")


async def test_start_rejected_when_capabilities_fail_on_auth_or_rate_limit() -> None:
    for error in (AuthError(), RateLimitError("slow down")):
        client = make_client()
        client.fail("find_socket_capabilities", error)

        outcome = await commands(client, FakeChanges()).start(STATION, SOCKET, "AVAILABLE")

        assert client.calls["start_charge"] == []
        assert outcome.result == "rejected"
        assert outcome.error is error


async def test_a_refused_start_keeps_its_method() -> None:
    client = make_client()
    client.capabilities = {SOCKET: caps({"START_CHARGE_NOW"})}
    client.fail("start_charge_now", ApiError("NOT_AUTHORIZED"))

    outcome = await commands(client, FakeChanges()).start(STATION, SOCKET, "AVAILABLE")

    assert (outcome.result, outcome.method) == ("rejected", "charge_now")
    assert outcome.message == "NOT_AUTHORIZED"
