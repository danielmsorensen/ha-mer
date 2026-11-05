"""Your session: whether it runs, when it ended, and what to look for next.

Pure and in-process: the coordinator feeds it what it polled, was pushed or sent, with the
time, and executes the intents it returns. See CONTEXT.md for the terms.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from .driivz.const import (
    IN_USE_STATUSES,
    STATUS_AVAILABLE,
    STATUS_CHARGING,
    STATUS_DISCHARGING,
    STATUS_FAULTED,
    STATUS_PAUSED,
    STATUS_UNAVAILABLE,
    STATUS_UNKNOWN,
)
from .driivz.models import EstimatePush, Socket, SocketPrice, Station, StatusPush

# How long a pushed end stays ended against polls still reporting the session. On
# 2026-11-02 a poll 0.1 s after the pushed end brought it back for 24 s, and that counted as
# a charge starting.
PUSHED_END_TRUST = timedelta(minutes=2)
# How recent a start of yours on the socket must be for its charging to be expected as yours.
OWN_START_WINDOW = timedelta(minutes=10)
# After your own start begins charging: when to ask the portal for the session, in seconds.
# On 2026-10-12 a poll 4 s in found nothing, and the next came 5 minutes later.
SESSION_WATCH_DELAYS_SECONDS: tuple[int, ...] = (10, 20, 30, 60, 120)

CHARGING_STATUSES = frozenset({STATUS_CHARGING, STATUS_DISCHARGING, STATUS_PAUSED})
# Pushed mid-session, these don't end it: on 2026-11-05 a socket faulted for 4 minutes while
# the portal kept the session, until it was stopped. A poll still ends it.
NOT_AN_END = frozenset({STATUS_FAULTED, STATUS_UNAVAILABLE, STATUS_UNKNOWN})


@dataclass(frozen=True, slots=True)
class ActiveSession:
    """The customer's running charge, if any."""

    socket_id: int
    station_id: int | None
    socket_name: str | None
    station_caption: str | None
    transaction_id: int | None
    started_at: datetime | None
    duration: timedelta | None
    energy_kwh: float | None
    cost: float | None
    currency: str | None
    # The portal's "estimated rate", kW at the charger. Refreshed with each meter reading
    # (every few minutes), not live; see docs/api.md.
    rate_kw: float | None = None
    # The tariff on the socket in use. The price comes from the charger's detail (for a
    # charger you have not added, fetched once per session); plan name and summary come
    # from the estimates.
    price: SocketPrice | None = None
    billing_plan: str | None = None
    tariff: str | None = None


@dataclass(frozen=True, slots=True)
class EndedSession:
    """Your most recent session once it has ended, and when the integration saw it end."""

    session: ActiveSession
    ended_at: datetime
    # Free sockets on your chargers held back since it ended: free since before it ended, or
    # the left socket until someone else has used it.
    held_back: frozenset[int] = frozenset()
    # The left socket: whether it has been free since, and whether someone else then used it.
    freed: bool = False
    reused: bool = False


# ----- observations ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Polled:
    """The portal's session (None: none) and your chargers, as just polled."""

    active: ActiveSession | None
    stations: Mapping[int, Station]


@dataclass(frozen=True, slots=True)
class StatusPushed:
    """A status push, with its charger before it (None: not yours) and your chargers after."""

    push: StatusPush
    before: Station | None
    stations: Mapping[int, Station]


@dataclass(frozen=True, slots=True)
class EstimatePushed:
    push: EstimatePush


@dataclass(frozen=True, slots=True)
class CommandStarted:
    """A start/stop is being sent; until it finishes it polls for its own outcome."""

    command: str  # "start" or "stop"
    socket_id: int
    requested_at: datetime


@dataclass(frozen=True, slots=True)
class CommandFinished:
    """A start/stop has finished, however it ended (sent from `finally`)."""

    command: str  # "start" or "stop"
    socket_id: int
    rejected: bool
    requested_at: datetime


type Observation = Polled | StatusPushed | EstimatePushed | CommandStarted | CommandFinished


# ----- intents -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PollNow:
    """Poll now. Run only after publishing: an eager poll would otherwise be overwritten."""


@dataclass(frozen=True, slots=True)
class WatchForSession:
    """Ask the portal for your session at these seconds after now; poll once it appears."""

    delays: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class RereadHistory:
    """Re-read the charge history at the next poll (the current one, on the poll path)."""


type Intent = PollNow | WatchForSession | RereadHistory


class SessionTracker:
    """Observations in, your session (`active`, `last_end`) and intents out."""

    def __init__(self) -> None:
        self.active: ActiveSession | None = None
        # Kept in memory only; after a restart the Charging sensor falls back to the history.
        self.last_end: EndedSession | None = None
        self._pushed_end: tuple[int, datetime] | None = None
        self._running: list[CommandStarted] = []
        self._last_command: CommandFinished | None = None
        # Your chargers as last observed, to tell sockets free since before an end.
        self._stations: Mapping[int, Station] = {}

    def observe(self, observation: Observation, now: datetime) -> tuple[Intent, ...]:
        match observation:
            case Polled(active, stations):
                return self._polled(self._unless_pushed_end(active, now), stations, now)
            case StatusPushed():
                return self._status_pushed(observation, now)
            case EstimatePushed(push):
                return self._estimate_pushed(push)
            case CommandStarted():
                self._running.append(observation)
            case CommandFinished(command, socket_id, _, requested_at):
                started = CommandStarted(command, socket_id, requested_at)
                if started in self._running:
                    self._running.remove(started)
                self._last_command = observation
        return ()

    def _polled(
        self, active: ActiveSession | None, stations: Mapping[int, Station], now: datetime
    ) -> tuple[Intent, ...]:
        intents = self._set_active(active, now)
        self._follow_held_back(stations)
        return intents

    def _status_pushed(self, observed: StatusPushed, now: datetime) -> tuple[Intent, ...]:
        push, active = observed.push, self.active
        intents: tuple[Intent, ...] = ()
        if self._starts_unknown_charge(observed):
            own = push.socket_id in self.own_starts(now)
            intents = (WatchForSession(SESSION_WATCH_DELAYS_SECONDS) if own else PollNow(),)
        if (
            active is not None
            and push.socket_id == active.socket_id
            and push.socket_status is not None
            and push.socket_status not in CHARGING_STATUSES | NOT_AN_END
        ):
            self._pushed_end = (active.socket_id, now)
            active = None
        elif (
            self._pushed_end is not None
            and push.socket_id == self._pushed_end[0]
            and push.socket_status in CHARGING_STATUSES
        ):
            self._pushed_end = None  # charging again: a poll may report it
        intents = self._set_active(active, now) + intents
        self._follow_held_back(observed.stations)
        return intents

    def _starts_unknown_charge(self, observed: StatusPushed) -> bool:
        """A socket on your chargers just started charging, with no session of yours known.

        It may be yours (a start that waited for the cable, or one from the app). Seen live on
        2026-10-07. A running command polls for itself.
        """
        push, before = observed.push, observed.before
        if (
            before is None
            or self.active is not None
            or self._running
            or push.socket_status not in CHARGING_STATUSES
        ):
            return False
        was = _socket_of(before, push.socket_id)
        return was is None or was.status not in CHARGING_STATUSES

    def own_starts(self, now: datetime) -> frozenset[int]:
        """Sockets with an own start: yours running, or your last command, recent and not rejected.

        Charging on them is expected to be your session.
        """
        sockets = {c.socket_id for c in self._running if c.command == "start"}
        last = self._last_command
        if (
            last is not None
            and last.command == "start"
            and not last.rejected
            and now - last.requested_at < OWN_START_WINDOW
        ):
            sockets.add(last.socket_id)
        return frozenset(sockets)

    def _estimate_pushed(self, push: EstimatePush) -> tuple[Intent, ...]:
        active = self.active
        if active is None:
            # Estimates only arrive for your sessions: fetch this one properly.
            return (PollNow(),)
        if active.socket_id != push.socket_id:
            # A second session; the portal reports only one, so polling would not help.
            return ()
        self.active = replace(
            active,
            energy_kwh=push.energy_kwh if push.energy_kwh is not None else active.energy_kwh,
            cost=push.cost if push.cost is not None else active.cost,
            currency=push.currency or active.currency,
            rate_kw=push.rate_kw if push.rate_kw is not None else active.rate_kw,
            billing_plan=push.billing_plan or active.billing_plan,
            tariff=push.tariff or active.tariff,
        )
        return ()

    def _unless_pushed_end(
        self, active: ActiveSession | None, now: datetime
    ) -> ActiveSession | None:
        """A polled session, unless a push has just ended it; the portal's lookup lags."""
        ended = self._pushed_end
        if active is None or ended is None:
            return active
        socket_id, at = ended
        if now - at >= PUSHED_END_TRUST:
            self._pushed_end = None
            return active
        return None if active.socket_id == socket_id else active

    def _set_active(self, active: ActiveSession | None, now: datetime) -> tuple[Intent, ...]:
        """Record the session that just ended, if any, and have the history re-read.

        A pushed end does not poll at once: the portal can still report the session, and that
        poll would bring it back.
        """
        before, self.active = self.active, active
        if before is None or (active is not None and active.socket_id == before.socket_id):
            return ()
        # Free in the snapshot before the end: one that frees in the observation seeing the
        # end freed after it.
        free_before = {s for s, st in _statuses(self._stations).items() if st == STATUS_AVAILABLE}
        self.last_end = EndedSession(
            before, now, held_back=frozenset(free_before - {before.socket_id})
        )
        return (RereadHistory(),)

    def _follow_held_back(self, stations: Mapping[int, Station]) -> None:
        """Release held-back sockets once taken; hold the left socket while free, until reused."""
        self._stations = stations
        ended = self.last_end
        if ended is None:
            return
        statuses = _statuses(stations)
        left = ended.session.socket_id
        # A socket not in this observation (its charger missing) keeps its place.
        held = {s for s in ended.held_back if statuses.get(s, STATUS_AVAILABLE) == STATUS_AVAILABLE}
        freed, reused = ended.freed, ended.reused
        if left in statuses:
            status = statuses[left]
            if status == STATUS_AVAILABLE:
                freed = True
            elif freed and status in IN_USE_STATUSES:
                reused = True
            if status == STATUS_AVAILABLE and not reused:
                held.add(left)
            else:
                held.discard(left)
        self.last_end = replace(ended, held_back=frozenset(held), freed=freed, reused=reused)


def _statuses(stations: Mapping[int, Station]) -> dict[int, str | None]:
    return {s.id: s.status for station in stations.values() for s in station.sockets}


def _socket_of(station: Station | None, socket_id: int | None) -> Socket | None:
    if station is None:
        return None
    return next((s for s in station.sockets if s.id == socket_id), None)
