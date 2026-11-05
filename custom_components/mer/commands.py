"""Start or stop a socket and wait until the charger shows the outcome.

Pure of Home Assistant: the Driivz client is the port to the portal, and `Changes` the port
to what the integration sees (pushed or polled). See CONTEXT.md for the terms.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import Any, Protocol

from .driivz.client import DriivzDriverClient
from .driivz.const import (
    START_METHOD_CHARGE_NOW,
    START_METHOD_NORMAL,
    STATUS_AVAILABLE,
    STATUS_PREPARING,
    STATUS_UNKNOWN,
)
from .driivz.exceptions import ApiError, AuthError, DriivzError, RateLimitError
from .driivz.models import Socket, SocketCapabilities, Station
from .session_tracker import CHARGING_STATUSES

# Outcomes of a command, also the states of the "Last command" sensor.
RESULT_CHARGING = "charging"
RESULT_AWAITING_CABLE = "awaiting_cable"
RESULT_STOPPED = "stopped"
RESULT_REJECTED = "rejected"
RESULT_UNCONFIRMED = "unconfirmed"
COMMAND_RESULTS: tuple[str, ...] = (
    RESULT_CHARGING,
    RESULT_AWAITING_CABLE,
    RESULT_STOPPED,
    RESULT_REJECTED,
    RESULT_UNCONFIRMED,
)
COMMAND_START = "start"
COMMAND_STOP = "stop"

# Follow-up requests (command polls, the session watch, capability reads) stop at this
# rate-limit headroom, leaving it to the normal poll.
RATE_LIMIT_FLOOR = 2

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Cadence:
    """How often a command's outcome is checked, and how many times before giving up."""

    interval_seconds: float = 5
    max_polls: int = 12

    @property
    def timeout_seconds(self) -> float:
        return self.interval_seconds * self.max_polls


@dataclass(frozen=True, slots=True)
class View:
    """A charger (None: unknown) and your session's socket, as the integration sees them."""

    station: Station | None
    active_socket_id: int | None

    def socket(self, socket_id: int) -> Socket | None:
        if self.station is None:
            return None
        return next((s for s in self.station.sockets if s.id == socket_id), None)


class Changes(Protocol):
    """What the integration sees change while a command waits."""

    async def pushed(self, station_id: int | None, timeout: float) -> View | None:
        """The view after a pushed change within `timeout`; None if none (or not connected)."""

    async def poll(self, station_id: int | None) -> View:
        """Poll the charger and your session, publish them, and return the view.

        Raises `DriivzError`.
        """


@dataclass(frozen=True, slots=True)
class Outcome:
    """How a command ended. Rejected: `error` is the portal's refusal, nothing was waited for."""

    result: str
    # For a start: "start" (the normal one) or "charge_now"; None if none was picked.
    method: str | None = None
    message: str | None = None
    error: DriivzError | None = None
    # Capabilities read for a start (the whole charger's), for the caller to keep.
    capabilities: Mapping[int, SocketCapabilities] = field(default_factory=dict)


type Rule = Callable[[View], str | None]


@dataclass(frozen=True, slots=True)
class CommandResult:
    """How the most recent command ended, as recorded on Last command and the event."""

    command: str  # "start" or "stop"
    result: str  # one of COMMAND_RESULTS
    station_id: int | None
    station_name: str | None
    socket_id: int
    socket_name: str | None
    requested_at: datetime
    finished_at: datetime
    message: str | None = None
    # For a start: "start" (the normal one) or "charge_now".
    method: str | None = None

    def as_event_data(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "result": self.result,
            "station_id": self.station_id,
            "station_name": self.station_name,
            "socket_id": self.socket_id,
            "socket_name": self.socket_name,
            "requested_at": self.requested_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "message": self.message,
            "method": self.method,
        }


class CommandUnconfirmed(Exception):
    """The portal accepted the command but the charger did not show the outcome in time."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"No outcome within {seconds:g} s")
        self.seconds = seconds


class Commands:
    """Start or stop a socket; returns the outcome."""

    def __init__(
        self, client: DriivzDriverClient, changes: Changes, cadence: Cadence | None = None
    ) -> None:
        self._client = client
        self._changes = changes
        self.cadence = cadence or Cadence()

    async def start(self, station_id: int, socket_id: int, status_before: str | None) -> Outcome:
        """Start a charge with the method the socket's capabilities allow.

        `status_before`: the socket's status as last seen (None: unknown). Refused up front,
        with the portal's reason and nothing sent, when the capabilities allow neither start.
        Unreadable capabilities send the normal start and leave the portal the last word.
        """
        read: Mapping[int, SocketCapabilities] = {}
        method: str | None = None
        try:
            read = await self._read_capabilities(station_id, socket_id, status_before)
            caps = read.get(socket_id)
            method = START_METHOD_NORMAL if caps is None else caps.start_method
            if caps is not None and method is None:
                raise ApiError("START_NOT_ALLOWED", caps.start_refusal)
            if method == START_METHOD_CHARGE_NOW:
                await self._client.start_charge_now(socket_id)
            else:
                await self._client.start_charge(socket_id)
        except DriivzError as err:
            return _rejected(err, method, read)
        # A free socket accepts the start and then waits for the cable: positive here.
        rule = _started(socket_id, expect_cable=status_before == STATUS_AVAILABLE)
        return Outcome(await self._wait(station_id, rule), method, capabilities=read)

    async def stop(self, station_id: int | None, socket_id: int) -> Outcome:
        try:
            await self._client.stop_charge(socket_id)
        except DriivzError as err:
            return _rejected(err)
        return Outcome(await self._wait(station_id, _stopped(socket_id)))

    async def _read_capabilities(
        self, station_id: int, socket_id: int, status: str | None
    ) -> Mapping[int, SocketCapabilities]:
        try:
            return await self._client.find_socket_capabilities(
                station_id, socket_id, status or STATUS_UNKNOWN
            )
        except AuthError, RateLimitError:
            raise
        except DriivzError as err:
            _LOGGER.debug("Could not read socket capabilities (%s); sending the normal start", err)
            return {}

    async def _wait(self, station_id: int | None, rule: Rule) -> str:
        """Check `rule` on each pushed change, else on a poll, until it decides or time is up.

        A push costs no request. Polling stops early at the rate-limit floor; the normal poll
        then picks the outcome up.
        """
        for _ in range(self.cadence.max_polls):
            view = await self._changes.pushed(station_id, self.cadence.interval_seconds)
            if view is None:
                remaining = self._client.rate_limit_remaining
                if remaining is not None and remaining <= RATE_LIMIT_FLOOR:
                    _LOGGER.debug(
                        "Stopping command polling early; rate limit remaining %s", remaining
                    )
                    break
                try:
                    view = await self._changes.poll(station_id)
                except DriivzError as err:
                    _LOGGER.debug("Command polling failed (%s); waiting for the next poll", err)
                    continue
            if (result := rule(view)) is not None:
                return result
        return RESULT_UNCONFIRMED


def _rejected(
    err: DriivzError,
    method: str | None = None,
    capabilities: Mapping[int, SocketCapabilities] | None = None,
) -> Outcome:
    return Outcome(RESULT_REJECTED, method, str(err), err, capabilities or {})


def _started(socket_id: int, *, expect_cable: bool) -> Rule:
    def rule(view: View) -> str | None:
        socket = view.socket(socket_id)
        if view.active_socket_id == socket_id or (socket and socket.status in CHARGING_STATUSES):
            return RESULT_CHARGING
        if expect_cable and socket and socket.status == STATUS_PREPARING:
            return RESULT_AWAITING_CABLE
        return None

    return rule


def _stopped(socket_id: int) -> Rule:
    def rule(view: View) -> str | None:
        socket = view.socket(socket_id)
        still_charging = socket is not None and socket.status in CHARGING_STATUSES
        if view.active_socket_id != socket_id and not still_charging:
            return RESULT_STOPPED
        return None

    return rule
