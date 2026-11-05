"""Session tracker: observations in, your session and intents out. No Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.mer.driivz.models import EstimatePush, Socket, Station, StatusPush
from custom_components.mer.session_tracker import (
    OWN_START_WINDOW,
    PUSHED_END_TRUST,
    SESSION_WATCH_DELAYS_SECONDS,
    ActiveSession,
    CommandFinished,
    CommandStarted,
    EstimatePushed,
    Observation,
    Polled,
    PollNow,
    RereadHistory,
    SessionTracker,
    StatusPushed,
    WatchForSession,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
CHARGER_A, LEFT_A, RIGHT_A = 6042, 11243, 11244
CHARGER_B, LEFT_B, RIGHT_B = 6041, 11241, 11242


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def session(socket_id: int = RIGHT_B, station_id: int = CHARGER_B, **kw: object) -> ActiveSession:
    fields: dict[str, object] = {
        "socket_name": None,
        "station_caption": None,
        "transaction_id": None,
        "started_at": None,
        "duration": None,
        "energy_kwh": 1.0,
        "cost": 0.5,
        "currency": "GBP",
    }
    fields.update(kw)
    return ActiveSession(socket_id=socket_id, station_id=station_id, **fields)  # type: ignore[arg-type]


def chargers(**statuses: str) -> dict[int, Station]:
    """Both chargers, every socket AVAILABLE unless named, e.g. chargers(RIGHT_B="CHARGING")."""
    names = {"LEFT_A": LEFT_A, "RIGHT_A": RIGHT_A, "LEFT_B": LEFT_B, "RIGHT_B": RIGHT_B}
    status = {names[k]: v for k, v in statuses.items()}
    return {
        station_id: Station(
            id=station_id,
            caption=f"Charger {station_id}",
            sockets=tuple(
                Socket(id=s, station_id=station_id, status=status.get(s, "AVAILABLE"))
                for s in sockets
            ),
        )
        for station_id, sockets in ((CHARGER_A, (LEFT_A, RIGHT_A)), (CHARGER_B, (LEFT_B, RIGHT_B)))
    }


def pushed(
    socket_id: int, status: str, before: dict[int, Station], station_id: int = CHARGER_B
) -> StatusPushed:
    """A status push on one of your chargers, given the chargers as they were before it."""
    push = StatusPush(station_id, None, socket_id, status, False, False)
    after = dict(before)
    if station_id in before:
        after[station_id] = before[station_id].with_push(push)
    return StatusPushed(push, before.get(station_id), after)


def estimate(socket_id: int = RIGHT_B, **kw: object) -> EstimatePushed:
    fields: dict[str, object] = {"energy_kwh": None, "cost": None, "currency": None}
    fields.update(kw)
    return EstimatePushed(EstimatePush(socket_id=socket_id, **fields))  # type: ignore[arg-type]


def own_start(socket_id: int = RIGHT_B, rejected: bool = False, at_s: float = 0) -> list[Step]:
    return [
        Step(CommandStarted("start", socket_id, at(at_s)), at_s),
        Step(CommandFinished("start", socket_id, rejected, at(at_s)), at_s),
    ]


@dataclass
class Step:
    observation: Observation
    seconds: float = 0


def run(steps: list[Step]) -> tuple[SessionTracker, tuple[object, ...]]:
    """Feed every step; return the tracker and the last step's intents."""
    tracker = SessionTracker()
    intents: tuple[object, ...] = ()
    for step in steps:
        intents = tracker.observe(step.observation, at(step.seconds))
    return tracker, intents


CHARGING_B = chargers(RIGHT_B="CHARGING")
IDLE = chargers()
WATCH = WatchForSession(SESSION_WATCH_DELAYS_SECONDS)
WINDOW = OWN_START_WINDOW.total_seconds()
TRUST = PUSHED_END_TRUST.total_seconds()

# (case, steps, intents of the last step, socket of your session after it)
CASES: list[tuple[str, list[Step], tuple[object, ...], int | None]] = [
    # --- polls --------------------------------------------------------------
    ("first poll, no session", [Step(Polled(None, IDLE))], (), None),
    ("first poll, a session", [Step(Polled(session(), CHARGING_B))], (), RIGHT_B),
    (
        "poll finds the session gone: history re-read",
        [Step(Polled(session(), CHARGING_B)), Step(Polled(None, IDLE), 60)],
        (RereadHistory(),),
        None,
    ),
    (
        "poll finds a session on another socket: the first ended",
        [Step(Polled(session(), CHARGING_B)), Step(Polled(session(LEFT_A, CHARGER_A), IDLE), 60)],
        (RereadHistory(),),
        LEFT_A,
    ),
    # --- pushed end (2026-11-02) ---------------------------------------------
    (
        "push ends the session: history re-read at the next poll, no poll now",
        [Step(Polled(session(), CHARGING_B)), Step(pushed(RIGHT_B, "FINISHING", CHARGING_B), 6)],
        (RereadHistory(),),
        None,
    ),
    (
        "a lagging poll does not bring a pushed end back",
        [
            Step(Polled(session(), CHARGING_B)),
            Step(pushed(RIGHT_B, "FINISHING", CHARGING_B), 6),
            Step(Polled(session(), CHARGING_B), 6.1),
        ],
        (),
        None,
    ),
    (
        "after the trust window the poll is believed",
        [
            Step(Polled(session(), CHARGING_B)),
            Step(pushed(RIGHT_B, "FINISHING", CHARGING_B), 6),
            Step(Polled(session(), CHARGING_B), 6 + TRUST),
        ],
        (),
        RIGHT_B,
    ),
    (
        "charging again by push: the poll is believed",
        [
            Step(Polled(session(), CHARGING_B)),
            Step(pushed(RIGHT_B, "FINISHING", CHARGING_B), 6),
            Step(pushed(RIGHT_B, "CHARGING", chargers(RIGHT_B="FINISHING")), 10),
            Step(Polled(session(), CHARGING_B), 11),
        ],
        (),
        RIGHT_B,
    ),
    (
        "a pushed end does not hide a session on another socket",
        [
            Step(Polled(session(), CHARGING_B)),
            Step(pushed(RIGHT_B, "FINISHING", CHARGING_B), 6),
            Step(Polled(session(LEFT_A, CHARGER_A), IDLE), 10),
        ],
        (),
        LEFT_A,
    ),
    (
        "a push still charging does not end it",
        [Step(Polled(session(), CHARGING_B)), Step(pushed(RIGHT_B, "PAUSED", CHARGING_B), 6)],
        (),
        RIGHT_B,
    ),
    (
        "a fault does not end it: the portal keeps the session (2026-11-05)",
        [Step(Polled(session(), CHARGING_B)), Step(pushed(RIGHT_B, "FAULTED", CHARGING_B), 6)],
        (),
        RIGHT_B,
    ),
    (
        "out of service does not end it",
        [Step(Polled(session(), CHARGING_B)), Step(pushed(RIGHT_B, "UNAVAILABLE", CHARGING_B), 6)],
        (),
        RIGHT_B,
    ),
    (
        "after a fault, finishing ends it",
        [
            Step(Polled(session(), CHARGING_B)),
            Step(pushed(RIGHT_B, "FAULTED", CHARGING_B), 6),
            Step(pushed(RIGHT_B, "FINISHING", chargers(RIGHT_B="FAULTED")), 250),
        ],
        (RereadHistory(),),
        None,
    ),
    (
        "a push without a status does not end it",
        [Step(Polled(session(), CHARGING_B)), Step(pushed(RIGHT_B, None, CHARGING_B), 6)],  # type: ignore[arg-type]
        (),
        RIGHT_B,
    ),
    (
        "an end pushed for a public charger, not one of yours",
        [
            Step(Polled(session(15028, 17886), IDLE)),
            Step(pushed(15028, "FINISHING", IDLE, station_id=17886), 6),
        ],
        (RereadHistory(),),
        None,
    ),
    # --- a socket starting to charge (2026-10-07) -----------------------------
    (
        "your charger starts charging, no session known: poll now",
        [Step(Polled(None, IDLE)), Step(pushed(RIGHT_B, "CHARGING", IDLE), 30)],
        (PollNow(),),
        None,
    ),
    (
        "already charging: paused to charging is not a start",
        [Step(Polled(None, CHARGING_B)), Step(pushed(RIGHT_B, "PAUSED", CHARGING_B), 30)],
        (),
        None,
    ),
    (
        "a session is known: no poll",
        [Step(Polled(session(), IDLE)), Step(pushed(LEFT_A, "CHARGING", IDLE, CHARGER_A), 30)],
        (),
        RIGHT_B,
    ),
    (
        "a command is running: it polls for itself",
        [
            Step(Polled(None, IDLE)),
            Step(CommandStarted("start", RIGHT_B, at(0))),
            Step(pushed(RIGHT_B, "CHARGING", IDLE)),
        ],
        (),
        None,
    ),
    (
        "not one of your chargers: no poll",
        [Step(Polled(None, IDLE)), Step(pushed(15028, "CHARGING", IDLE, station_id=17886))],
        (),
        None,
    ),
    (
        "your charger goes to something other than charging: no poll",
        [Step(Polled(None, IDLE)), Step(pushed(RIGHT_B, "PREPARING", IDLE))],
        (),
        None,
    ),
    # --- your own start (2026-10-12) ------------------------------------------
    (
        "your recent start begins charging: watch for the session",
        [Step(Polled(None, IDLE)), *own_start(), Step(pushed(RIGHT_B, "PAUSED", IDLE), 40)],
        (WATCH,),
        None,
    ),
    (
        "a rejected start is not yours: one poll",
        [
            Step(Polled(None, IDLE)),
            *own_start(rejected=True),
            Step(pushed(RIGHT_B, "CHARGING", IDLE), 40),
        ],
        (PollNow(),),
        None,
    ),
    (
        "a start too long ago is not yours: one poll",
        [Step(Polled(None, IDLE)), *own_start(), Step(pushed(RIGHT_B, "CHARGING", IDLE), WINDOW)],
        (PollNow(),),
        None,
    ),
    (
        "a start on another socket is not yours: one poll",
        [Step(Polled(None, IDLE)), *own_start(LEFT_B), Step(pushed(RIGHT_B, "CHARGING", IDLE), 40)],
        (PollNow(),),
        None,
    ),
    (
        "a stop is not a start: one poll",
        [
            Step(Polled(None, IDLE)),
            Step(CommandStarted("stop", RIGHT_B, at(0))),
            Step(CommandFinished("stop", RIGHT_B, False, at(0))),
            Step(pushed(RIGHT_B, "CHARGING", IDLE), 40),
        ],
        (PollNow(),),
        None,
    ),
    (
        "a stop after your start: no longer yours, one poll",
        [
            Step(Polled(None, IDLE)),
            *own_start(),
            Step(CommandStarted("stop", RIGHT_B, at(20)), 20),
            Step(CommandFinished("stop", RIGHT_B, False, at(20)), 20),
            Step(pushed(RIGHT_B, "CHARGING", IDLE), 40),
        ],
        (PollNow(),),
        None,
    ),
    # --- estimates ------------------------------------------------------------
    (
        "an estimate for a session not known yet: poll now",
        [Step(Polled(None, IDLE)), Step(estimate(15028))],
        (PollNow(),),
        None,
    ),
    (
        "an estimate for a second session: ignored",
        [Step(Polled(session(), CHARGING_B)), Step(estimate(LEFT_A))],
        (),
        RIGHT_B,
    ),
]


@pytest.mark.parametrize(
    ("steps", "intents", "socket"), [c[1:] for c in CASES], ids=[c[0] for c in CASES]
)
def test_observations(steps: list[Step], intents: tuple[object, ...], socket: int | None) -> None:
    tracker, last = run(steps)
    assert last == intents
    assert (tracker.active.socket_id if tracker.active else None) == socket


def test_end_is_recorded_once_with_when_it_was_seen() -> None:
    tracker, _ = run(
        [
            Step(Polled(session(), CHARGING_B)),
            Step(pushed(RIGHT_B, "FINISHING", CHARGING_B), 6),
            Step(Polled(None, chargers(RIGHT_B="FINISHING")), 60),
        ]
    )
    assert tracker.last_end is not None
    assert (tracker.last_end.session, tracker.last_end.ended_at) == (session(), at(6))


def test_no_end_without_a_session() -> None:
    tracker, _ = run([Step(Polled(None, IDLE)), Step(Polled(None, IDLE), 60)])
    assert tracker.last_end is None


def polls(*snapshots: dict[int, Station]) -> list[Step]:
    """Charging on Charger B Right, then a poll per snapshot with no session."""
    steps = [Step(Polled(session(), CHARGING_B))]
    return steps + [Step(Polled(None, s), i + 1) for i, s in enumerate(snapshots)]


OTHERS = {LEFT_A, RIGHT_A, LEFT_B}

# (case, steps, held back after them). 2026-10-15: after your session ends, sockets already
# free are held back until they are next taken, and the left socket until someone else used it.
HELD_BACK: list[tuple[str, list[Step], set[int]]] = [
    ("already free when it ended", polls(chargers(RIGHT_B="FINISHING")), OTHERS),
    (
        "taken since",
        polls(chargers(RIGHT_B="FINISHING"), chargers(RIGHT_B="FINISHING", LEFT_A="OCCUPIED")),
        {RIGHT_A, LEFT_B},
    ),
    (
        "taken and freed again",
        polls(
            chargers(RIGHT_B="FINISHING"),
            chargers(RIGHT_B="FINISHING", LEFT_A="OCCUPIED"),
            chargers(RIGHT_B="FINISHING"),
        ),
        {RIGHT_A, LEFT_B},
    ),
    (
        "freed in the poll that saw the end",
        [
            Step(Polled(session(), chargers(RIGHT_B="CHARGING", LEFT_A="OCCUPIED"))),
            Step(Polled(None, chargers(RIGHT_B="FINISHING")), 1),
        ],
        {RIGHT_A, LEFT_B},
    ),
    ("left socket unplugged", polls(chargers(RIGHT_B="FINISHING"), chargers()), {RIGHT_B, *OTHERS}),
    (
        "left socket reused",
        polls(chargers(), chargers(RIGHT_B="PREPARING")),
        OTHERS,
    ),
    (
        "left socket freed after reuse",
        polls(chargers(), chargers(RIGHT_B="PREPARING"), chargers()),
        OTHERS,
    ),
    (
        "left socket in use without freeing: still you",
        polls(chargers(RIGHT_B="FINISHING"), chargers(RIGHT_B="OCCUPIED"), chargers()),
        {RIGHT_B, *OTHERS},
    ),
    (
        "left socket faulted: not a use",
        polls(chargers(), chargers(RIGHT_B="FAULTED"), chargers()),
        {RIGHT_B, *OTHERS},
    ),
    (
        "left socket by push",
        [
            Step(Polled(session(), CHARGING_B)),
            Step(pushed(RIGHT_B, "AVAILABLE", CHARGING_B), 1),
        ],
        {RIGHT_B, *OTHERS},
    ),
    (
        "left socket reused, by push",
        [
            Step(Polled(session(), CHARGING_B)),
            Step(pushed(RIGHT_B, "AVAILABLE", CHARGING_B), 1),
            Step(pushed(RIGHT_B, "PREPARING", IDLE), 2),
            Step(pushed(RIGHT_B, "AVAILABLE", chargers(RIGHT_B="PREPARING")), 3),
        ],
        OTHERS,
    ),
    (
        "on a charger not polled",
        [Step(Polled(session(15028, 17886), IDLE)), Step(Polled(None, IDLE), 1)],
        {LEFT_A, RIGHT_A, LEFT_B, RIGHT_B},
    ),
]


@pytest.mark.parametrize(
    ("steps", "held_back"), [c[1:] for c in HELD_BACK], ids=[c[0] for c in HELD_BACK]
)
def test_held_back(steps: list[Step], held_back: set[int]) -> None:
    tracker, _ = run(steps)
    assert tracker.last_end is not None
    assert tracker.last_end.held_back == held_back


def test_estimate_updates_your_session_keeping_what_it_lacks() -> None:
    tracker, _ = run(
        [
            Step(Polled(session(rate_kw=1.8, tariff="flat"), CHARGING_B)),
            Step(estimate(energy_kwh=3.27, rate_kw=6.55, billing_plan="PAYG")),
        ]
    )
    assert tracker.active == session(
        energy_kwh=3.27, rate_kw=6.55, billing_plan="PAYG", tariff="flat"
    )


# (case, steps, seconds after T0 to ask at, own starts then)
OWN_STARTS: list[tuple[str, list[Step], float, set[int]]] = [
    ("running", [Step(CommandStarted("start", RIGHT_B, at(0)))], 30, {RIGHT_B}),
    ("finished", own_start(), 30, {RIGHT_B}),
    ("rejected", own_start(rejected=True), 30, set()),
    ("too long ago", own_start(), WINDOW, set()),
    (
        "stopped since",
        [
            *own_start(),
            Step(CommandStarted("stop", RIGHT_B, at(20)), 20),
            Step(CommandFinished("stop", RIGHT_B, False, at(20)), 20),
        ],
        30,
        set(),
    ),
    ("a stop running", [Step(CommandStarted("stop", RIGHT_B, at(0)))], 30, set()),
    (
        "running on one socket, finished on another",
        [*own_start(LEFT_A), Step(CommandStarted("start", RIGHT_B, at(10)), 10)],
        30,
        {LEFT_A, RIGHT_B},
    ),
]


@pytest.mark.parametrize(
    ("steps", "seconds", "sockets"), [c[1:] for c in OWN_STARTS], ids=[c[0] for c in OWN_STARTS]
)
def test_own_starts(steps: list[Step], seconds: float, sockets: set[int]) -> None:
    """Your start on a socket, running or recent and not rejected: its charging is yours."""
    tracker, _ = run(steps)
    assert tracker.own_starts(at(seconds)) == sockets
