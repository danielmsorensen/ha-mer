"""An in-memory Driivz portal: the test adapter for `DriivzDriverClient`.

Holds chargers, sockets, your session and the account in one place, answers the client's
calls from that state, and pushes status changes while live. Verbs keep chargers and your
session consistent; a test that needs the portal to lag assigns state directly and says so.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
import json
from typing import Any

from aiohttp import WSMsgType

from custom_components.mer.driivz.exceptions import DriivzConnectionError
from custom_components.mer.driivz.models import (
    ActiveTransaction,
    Bounds,
    SessionEstimate,
    Site,
    Socket,
    SocketCapabilities,
    Station,
    Transaction,
    Wallet,
)
from tests.helpers import load_json_fixture, station_from_fixture, stations_from_fixture


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


@dataclass
class Hold:
    """Parks the next call to one method until released."""

    reached: asyncio.Event = field(default_factory=asyncio.Event)
    released: asyncio.Event = field(default_factory=asyncio.Event)

    def release(self) -> None:
        self.released.set()


class FakePortal:
    """The portal as the fixtures describe it: Charger A free, Charger B's Left in use."""

    def __init__(self) -> None:
        # Live statuses (findStationsByIds) for every charger, the public one included.
        self.stations: dict[int, Station] = {
            s.id: s for s in stations_from_fixture("stations_by_ids.json")
        }
        public = station_from_fixture("station_17886.json")
        self.stations[public.id] = public
        # Details (findStationById); their socket statuses follow `stations`.
        self.details: dict[int, Station] = {
            6042: station_from_fixture("station_6042.json"),
            6041: station_from_fixture("station_6041.json"),
            public.id: public,
        }
        self.sites = [Site.from_dict(s) for s in load_json_fixture("sites_in_bounds.json")["data"]]
        self.stations_in_bounds = stations_from_fixture("stations_in_bounds.json")
        # Unknown by default, so availability falls back to the socket status.
        self.capabilities: dict[int, SocketCapabilities] = {}
        # Like the real account: 6042 is subscribed via the Mer app, 6041 is not.
        self.subscribed: set[int] = {6042}
        self.wallet = Wallet.from_dict(load_json_fixture("wallet.json")["data"])
        self.transactions = [
            Transaction.from_dict(t) for t in load_json_fixture("transactions.json")["data"]
        ]
        # Your session: what findLastActiveChargeSocket reports, with its transaction.
        self.session: Socket | None = None
        self.transaction = ActiveTransaction.from_dict(
            load_json_fixture("transaction_start_time.json")["data"]
        )
        self.estimate = SessionEstimate.from_dict(
            load_json_fixture("transaction_estimate.json")["data"]
        )
        self.rate_limit_remaining: int | None = 9
        self.logged_in = False
        self.live = False
        self.websockets: list[FakeWebSocket] = []
        self.calls: defaultdict[str, list[tuple[Any, ...]]] = defaultdict(list)
        self._failures: dict[str, tuple[BaseException, int | None]] = {}
        self._holds: dict[str, Hold] = {}

    # ----- test controls --------------------------------------------------

    def fail(self, name: str, error: BaseException, times: int | None = None) -> None:
        """Make calls to `name` raise `error`: the next `times` calls, or until `clear`."""
        self._failures[name] = (error, times)

    def clear(self, name: str) -> None:
        self._failures.pop(name, None)

    def hold(self, name: str) -> Hold:
        """Park the next call to `name` until the returned hold is released."""
        self._holds[name] = Hold()
        return self._holds[name]

    def go_live(self) -> None:
        """Accept push connections; verbs then push what they change."""
        self.live = True

    @property
    def ws(self) -> FakeWebSocket:
        """The latest push connection."""
        return self.websockets[-1]

    def push(self, payload: dict[str, Any]) -> None:
        """A raw message on the latest push connection."""
        self.ws.push(payload)

    # ----- verbs: the portal's state changes ------------------------------

    def charge(self, socket_id: int) -> None:
        """Your session starts charging on this socket."""
        self._set(socket_id, "CHARGING")
        station = self._station_of(socket_id)
        detail = self.details.get(station.id, station)
        socket = next((s for s in detail.sockets if s.id == socket_id), None) or Socket(socket_id)
        self.session = replace(
            socket, station_id=station.id, station_caption=detail.caption, status="CHARGING"
        )

    def await_cable(self, socket_id: int) -> None:
        """Your start was accepted; the charger waits for the cable."""
        self._set(socket_id, "PREPARING")

    def stop(self, socket_id: int) -> None:
        """The charge on this socket stops; your session ends if it was yours."""
        self._set(socket_id, "FINISHING")
        self._end(socket_id)

    def free(self, *socket_ids: int) -> None:
        """The sockets are free; your session ends if it ran on one."""
        for socket_id in socket_ids:
            self._set(socket_id, "AVAILABLE")
            self._end(socket_id)

    def take(self, *socket_ids: int, status: str = "OCCUPIED") -> None:
        """Someone else uses these sockets."""
        for socket_id in socket_ids:
            self._set(socket_id, status)

    def _end(self, socket_id: int) -> None:
        if self.session is not None and self.session.id == socket_id:
            self.session = None

    def _station_of(self, socket_id: int) -> Station:
        return next(
            st for st in self.stations.values() if any(s.id == socket_id for s in st.sockets)
        )

    def _set(self, socket_id: int, status: str) -> None:
        # The charger takes the socket's status, as status pushes report it.
        station = self._station_of(socket_id)
        self.stations[station.id] = replace(
            station,
            status=status,
            sockets=tuple(
                replace(s, status=status) if s.id == socket_id else s for s in station.sockets
            ),
        )
        if self.live and self.websockets:
            self.push(status_push(station.id, socket_id, status))

    # ----- the client's interface -----------------------------------------

    async def _call(self, name: str, *args: Any) -> None:
        self.calls[name].append(args)
        if (hold := self._holds.pop(name, None)) is not None:
            hold.reached.set()
            await hold.released.wait()
        if name in self._failures:
            error, times = self._failures[name]
            if times is not None:
                if times <= 1:
                    del self._failures[name]
                else:
                    self._failures[name] = (error, times - 1)
            raise error

    async def login(self) -> None:
        self.logged_in = False
        await self._call("login")
        self.logged_in = True

    async def connect_push(self) -> FakeWebSocket:
        if not self.logged_in:
            await self.login()
        await self._call("connect_push")
        if not self.live:
            raise DriivzConnectionError("no push in tests")
        self.websockets.append(FakeWebSocket())
        return self.ws

    async def find_sites_in_bounds(self, bounds: Bounds) -> list[Site]:
        await self._call("find_sites_in_bounds", bounds)
        return list(self.sites)

    async def find_stations_in_bounds(self, bounds: Bounds) -> list[Station]:
        await self._call("find_stations_in_bounds", bounds)
        return list(self.stations_in_bounds)

    async def find_stations_by_ids(self, ids: Iterable[int]) -> list[Station]:
        wanted = [int(i) for i in ids]
        await self._call("find_stations_by_ids", wanted)
        return [s for s in self.stations.values() if s.id in wanted]

    async def find_station_by_id(
        self, station_id: int, billing_plan_id: int | None = None
    ) -> Station:
        await self._call("find_station_by_id", station_id, billing_plan_id)
        detail = self.details[station_id]
        live = self.stations.get(station_id)
        return detail.with_live(live) if live is not None else detail

    async def find_last_active_charge_socket(self) -> Socket | None:
        await self._call("find_last_active_charge_socket")
        return self.session

    async def find_current_transaction(self, socket_id: int) -> ActiveTransaction | None:
        await self._call("find_current_transaction", socket_id)
        return self.transaction

    async def find_current_transaction_estimate(self, socket_id: int) -> SessionEstimate | None:
        await self._call("find_current_transaction_estimate", socket_id)
        return self.estimate

    async def start_charge(self, socket_id: int) -> None:
        await self._call("start_charge", socket_id)

    async def start_charge_now(self, socket_id: int) -> None:
        await self._call("start_charge_now", socket_id)

    async def find_socket_capabilities(
        self, station_id: int, socket_id: int, socket_status: str
    ) -> dict[int, SocketCapabilities]:
        await self._call("find_socket_capabilities", station_id, socket_id, socket_status)
        sockets = {s.id for s in self.stations[station_id].sockets}
        return {sid: caps for sid, caps in self.capabilities.items() if sid in sockets}

    async def stop_charge(self, socket_id: int) -> None:
        await self._call("stop_charge", socket_id)

    async def find_wallet(self) -> Wallet:
        await self._call("find_wallet")
        return self.wallet

    async def find_transactions(
        self, customer_id: int, start: datetime, end: datetime
    ) -> list[Transaction]:
        await self._call("find_transactions", customer_id, start, end)
        return list(self.transactions)

    async def is_subscribed_to_availability(self, station_id: int) -> bool:
        await self._call("is_subscribed_to_availability", station_id)
        return station_id in self.subscribed

    async def subscribe_to_availability(self, station_id: int) -> None:
        await self._call("subscribe_to_availability", station_id)
        self.subscribed.add(station_id)

    async def unsubscribe_from_availability(self, station_id: int) -> None:
        await self._call("unsubscribe_from_availability", station_id)
        self.subscribed.discard(station_id)
