"""Data update coordinator for the Mer integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import contextlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import json
import logging
from typing import Any

from aiohttp import WSMsgType
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    COMMAND_MAX_POLLS,
    COMMAND_POLL_INTERVAL_SECONDS,
    COMMAND_RATE_LIMIT_FLOOR,
    CONF_SCAN_INTERVAL,
    CONF_STATION_IDS,
    DEFAULT_SCAN_INTERVAL,
    DETAIL_REFRESH,
    DOMAIN,
    EVENT_COMMAND_RESULT,
    HISTORY_LOOKBACK,
    PUSH_POLL_INTERVAL_SECONDS,
    PUSH_RECONNECT_MAX_SECONDS,
    PUSH_RECONNECT_MIN_SECONDS,
    PUSH_SILENCE_TIMEOUT_SECONDS,
    RATE_LIMIT_SKIP_THRESHOLD,
    RATE_LIMIT_WARN_INTERVAL,
    REFRESH_AFTER_COMMAND_SECONDS,
    SESSION_TICK_SECONDS,
    SUBENTRY_TYPE_SITE,
    WALLET_REFRESH,
)
from .driivz.client import DriivzDriverClient
from .driivz.const import (
    STATUS_AVAILABLE,
    STATUS_CHARGING,
    STATUS_DISCHARGING,
    STATUS_PAUSED,
    STATUS_PREPARING,
)
from .driivz.exceptions import AuthError, DriivzError, RateLimitError
from .driivz.models import (
    EstimatePush,
    Socket,
    Station,
    StatusPush,
    Transaction,
    Wallet,
    parse_push,
)

# Socket statuses that mean a charge is in progress.
_CHARGING_STATUSES = frozenset({STATUS_CHARGING, STATUS_DISCHARGING, STATUS_PAUSED})

# Outcomes of a start/stop command, also the states of the "Last command" sensor.
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

_LOGGER = logging.getLogger(__name__)


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


@dataclass(frozen=True, slots=True)
class CommandResult:
    """How the most recent start/stop command ended."""

    command: str  # "start" or "stop"
    result: str  # one of COMMAND_RESULTS
    station_id: int | None
    station_name: str | None
    socket_id: int
    socket_name: str | None
    requested_at: datetime
    finished_at: datetime
    message: str | None = None

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
        }


class CommandUnconfirmed(Exception):
    """The portal accepted the command but the charger did not show the outcome in time."""


@dataclass(slots=True)
class MerData:
    """Everything the entities read."""

    stations: dict[int, Station] = field(default_factory=dict)
    details: dict[int, Station] = field(default_factory=dict)
    notify_subscriptions: dict[int, bool] = field(default_factory=dict)
    active: ActiveSession | None = None
    wallet: Wallet | None = None
    last_transaction: Transaction | None = None
    customer_id: int | None = None
    last_command: CommandResult | None = None


class MerCoordinator(DataUpdateCoordinator[MerData]):
    """Polls the Driivz portal for the configured chargers and the account."""

    config_entry: MerConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: MerConfigEntry, client: DriivzDriverClient
    ) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self._poll_interval = timedelta(seconds=interval)
        # Push channel state. `_push_event` wakes the command waiter on every pushed
        # change for one of our chargers.
        self.push_connected = False
        self._push_event = asyncio.Event()
        self.last_push_at: datetime | None = None
        # Ticks the running session's duration between polls; see _sync_session_ticker.
        self._cancel_session_ticker: CALLBACK_TYPE | None = None
        # One subentry per charging site, listing its monitored chargers. Any change
        # reloads the entry (see __init__), so these are fixed for the coordinator's life.
        self.site_subentries: list[ConfigSubentry] = [
            subentry
            for subentry in entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_SITE
        ]
        self._station_subentry: dict[int, ConfigSubentry] = {
            int(station_id): subentry
            for subentry in self.site_subentries
            for station_id in subentry.data[CONF_STATION_IDS]
        }
        self.station_ids: list[int] = list(self._station_subentry)
        self._details: dict[int, Station] = {}
        self._notify: dict[int, bool] = {}
        self._notify_lock = asyncio.Lock()
        self._wallet: Wallet | None = None
        self._last_transaction: Transaction | None = None
        self._customer_id: int | None = None
        self._last_command: CommandResult | None = None
        # Per station, not one timestamp for the whole set: the hourly detail work is
        # staggered so at most one charger is refreshed per cycle. See `_stations_due`.
        self._station_refreshed: dict[int, datetime] = {}
        self._wallet_refreshed: datetime | None = None
        self._skip_next = False
        self._rate_warned_at: datetime | None = None

    # ----- lifecycle -----------------------------------------------------

    async def _async_setup(self) -> None:
        try:
            await self.client.login()
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except DriivzError as err:
            raise UpdateFailed(f"Login failed: {err}") from err

    async def _async_update_data(self) -> MerData:
        if self._skip_next:
            self._skip_next = False
            if self.data is not None:
                # A skipped cycle deliberately republishes the last known good data instead
                # of contacting the portal. Returning without raising marks the coordinator
                # successful again (last_update_success -> True) even though nothing was
                # re-fetched this cycle; that is intentional -- one cycle of stale data is
                # preferable to blanking every entity to unavailable just because we chose
                # to be polite to a nearly-exhausted rate limit. Because we hand back the
                # very same MerData object, nothing downstream may mutate coordinator.data
                # in place; treat it as immutable.
                _LOGGER.debug("Skipping one poll to respect the portal rate limit")
                return self.data
        now = dt_util.utcnow()
        try:
            stations: dict[int, Station] = {}
            if self.station_ids:
                found = await self.client.find_stations_by_ids(self.station_ids)
                stations = {s.id: s for s in found}
            active = await self._fetch_active()
            if self._due(self._wallet_refreshed, WALLET_REFRESH, now):
                await self._refresh_optional("account", self._refresh_account(now))
                self._wallet_refreshed = now
            for station_id in self._stations_due(now):
                await self._refresh_optional(
                    f"charger {station_id} detail", self._refresh_station(station_id)
                )
                self._station_refreshed[station_id] = now
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except RateLimitError as err:
            self._skip_next = True
            raise UpdateFailed(f"Rate limited: {err}") from err
        except DriivzError as err:
            raise UpdateFailed(str(err)) from err
        self._check_rate_limit(now)
        return MerData(
            stations=stations,
            details=dict(self._details),
            notify_subscriptions=dict(self._notify),
            active=active,
            wallet=self._wallet,
            last_transaction=self._last_transaction,
            customer_id=self._customer_id,
            last_command=self._last_command,
        )

    @staticmethod
    def _due(last: datetime | None, every: timedelta, now: datetime) -> bool:
        return last is None or now - last >= every

    async def _fetch_active(self) -> ActiveSession | None:
        socket = await self.client.find_last_active_charge_socket()
        if socket is None:
            return None
        transaction = await self.client.find_current_transaction(socket.id)
        estimate = await self.client.find_current_transaction_estimate(socket.id)
        now = dt_util.utcnow()
        return ActiveSession(
            socket_id=socket.id,
            station_id=socket.station_id,
            socket_name=socket.name,
            station_caption=socket.station_caption,
            transaction_id=transaction.transaction_id if transaction else None,
            started_at=transaction.started_at(now) if transaction else None,
            duration=(estimate.duration if estimate else None)
            or (transaction.elapsed if transaction else None),
            energy_kwh=estimate.energy_kwh if estimate else None,
            cost=estimate.cost if estimate else None,
            currency=(estimate.currency if estimate else None)
            or (self._wallet.currency if self._wallet else None),
        )

    async def _refresh_optional(self, what: str, work: Coroutine[Any, Any, None]) -> None:
        """Await one of the slower periodic refreshes without letting it fail the cycle.

        The per-minute station poll is what this integration exists for, so a persistently
        failing endpoint in the slower optional work -- wallet, history, station detail or
        the newest and least-exercised of them, the notify-me subscription check -- must
        not take every entity unavailable, including the socket availability sensors that
        are still being fetched perfectly well.

        `AuthError` and `RateLimitError` still propagate, because those mean something the
        coordinator itself has to act on (reauthenticate, or skip the next cycle) and only
        its own handlers in `_async_update_data` can do that. Every other `DriivzError` is
        logged at debug and swallowed; the caller then stamps this work's timestamp anyway,
        so it backs off to its next window instead of retrying every single cycle and
        turning hourly traffic into per-minute traffic.

        Nothing is swallowed on the very first cycle, though. There are no entities yet to
        keep available, so tolerance buys nothing there, and coming up half-populated would
        permanently bake missing socket names, device models and serial numbers into the
        entity and device registries -- those are composed once, when the platforms are set
        up. Failing instead gives `ConfigEntryNotReady` and an ordinary setup retry.
        """
        try:
            await work
        except (AuthError, RateLimitError):
            raise
        except DriivzError as err:
            if self.data is None:
                raise
            _LOGGER.debug(
                "Mer %s refresh failed (%s: %s); keeping the last known values "
                "until its next scheduled window",
                what,
                type(err).__name__,
                err,
            )

    def _stations_due(self, now: datetime) -> list[int]:
        """Which chargers' hourly detail refresh should run this cycle.

        The first cycle primes every configured charger. Entity names, device models,
        serial numbers and socket labels are all composed once, when the platforms are set
        up, from whatever detail is cached at that moment -- a charger left undetailed on
        the first cycle would keep "Socket 11241" as its socket's name for the lifetime of
        the config entry.

        After that, at most one charger per cycle. Refreshing all of them in the same tick
        is what makes the worst-case burst grow with the number of selected chargers
        (6 + 2N calls: 22 at eight chargers), against an `X-Rate-Limit-Remaining` observed
        at 9 at rest and a refill window nobody has measured. One per cycle keeps the
        steady-state worst case a constant 8 however many chargers are selected, and each
        charger is still refreshed once an hour -- just not all in the same tick.
        """
        due = [
            station_id
            for station_id in self.station_ids
            if self._due(self._station_refreshed.get(station_id), DETAIL_REFRESH, now)
        ]
        return due if not self._station_refreshed else due[:1]

    async def _refresh_account(self, now: datetime) -> None:
        """Fetch wallet and history, then commit both atomically.

        Every call below must succeed before any `self.*` attribute is written, so a
        client error partway through never leaves the cached wallet/customer id/last
        transaction ahead of the last successfully published cycle. The `_wallet_refreshed`
        stamp is set by the caller, which stamps on a swallowed failure too.
        """
        wallet = await self.client.find_wallet()
        customer_id = wallet.customer_id
        last_transaction = self._last_transaction
        if customer_id is not None:
            transactions = await self.client.find_transactions(
                customer_id, now - HISTORY_LOOKBACK, now
            )
            last_transaction = transactions[0] if transactions else None
        self._wallet = wallet
        self._customer_id = customer_id
        self._last_transaction = last_transaction

    async def _refresh_station(self, station_id: int) -> None:
        """Fetch one charger's detail and subscription state, then commit both atomically.

        Both client calls happen before either `self.*` assignment, so a failure in the
        subscription check -- the newest and flakiest of the two -- cannot leave the
        details cache ahead of the last published data.

        `_notify_lock` spans the subscription fetch *and* its commit, so this can never
        interleave with `async_set_availability_subscription`. Locking only the assignment
        would not be enough: the staleness this guards against is introduced while
        *fetching* the answer, not while writing it back, so a toggle that lands after the
        fetch has read a now-stale value but before the commit must be blocked out
        entirely, not merely raced at the assignment.

        Staggering does not weaken that: the lock's scope is per call, and holding it
        around one station's fetch-and-commit is exactly the window it needs to cover. The
        `self._details` write is deliberately outside the lock -- the lock exists for
        `_notify`, which is the only state a toggle also writes -- and it happens after the
        lock is released so that the whole method still commits nothing until both calls
        have succeeded.
        """
        detail = await self.client.find_station_by_id(station_id)
        async with self._notify_lock:
            subscribed = await self.client.is_subscribed_to_availability(station_id)
            self._notify = {**self._notify, station_id: subscribed}
        self._details = {**self._details, station_id: detail}

    def _check_rate_limit(self, now: datetime) -> None:
        remaining = self.client.rate_limit_remaining
        if remaining is None or remaining > RATE_LIMIT_SKIP_THRESHOLD:
            return
        self._skip_next = True
        if self._rate_warned_at is None or now - self._rate_warned_at >= RATE_LIMIT_WARN_INTERVAL:
            _LOGGER.warning(
                "Mer portal rate limit nearly exhausted (%s remaining); skipping next poll",
                remaining,
            )
            self._rate_warned_at = now

    # ----- accessors -----------------------------------------------------

    def get_station(self, station_id: int) -> Station | None:
        """Return the station with detail fields and live statuses merged."""
        if self.data is None:
            return None
        live = self.data.stations.get(station_id)
        detail = self.data.details.get(station_id)
        if live is None:
            return None
        return detail.with_live(live) if detail else live

    def get_socket(self, station_id: int, socket_id: int) -> Socket | None:
        station = self.get_station(station_id)
        if station is None:
            return None
        return next((s for s in station.sockets if s.id == socket_id), None)

    def configured_stations(self) -> list[Station]:
        """Configured stations that are present in the latest data."""
        stations = (self.get_station(sid) for sid in self.station_ids)
        return [s for s in stations if s is not None]

    def charger_stations(self) -> list[tuple[ConfigSubentry, Station]]:
        """Each monitored station with its site's subentry, skipping any the portal did not return.

        Platforms add a charger's entities under its site's subentry id, so the
        integration page groups them by site and deleting a site removes its chargers.
        """
        pairs: list[tuple[ConfigSubentry, Station]] = []
        for station_id, subentry in self._station_subentry.items():
            station = self.get_station(station_id)
            if station is None:
                _LOGGER.warning(
                    "Mer charger %s at %s was not returned by the portal; skipping its entities",
                    station_id,
                    subentry.title,
                )
                continue
            pairs.append((subentry, station))
        return pairs

    async def async_set_availability_subscription(self, station_id: int, subscribed: bool) -> None:
        """Subscribe or unsubscribe this charger, then publish the confirmed result.

        The client call happens first and is not retried or reverted here: if it raises
        `DriivzError`, it propagates to the caller (the switch entity) before anything is
        touched, so the cache and published data are left exactly as they were. Only once
        the portal has confirmed the change do we update `self._notify` and publish a
        fresh `MerData` via `async_set_updated_data`, so the switch reflects the confirmed
        value immediately rather than waiting up to an hour for the next detail refresh.

        Both the client call and the cache write happen under `_notify_lock`, the same
        lock `_refresh_station` holds across its subscription fetch and commit. That
        serializes the two writers completely: a toggle either finishes entirely before a
        refresh's subscription fetch starts, or waits for it to finish first, so the
        refresh's commit can never overwrite a toggle that landed while it was fetching.
        """
        async with self._notify_lock:
            if subscribed:
                await self.client.subscribe_to_availability(station_id)
            else:
                await self.client.unsubscribe_from_availability(station_id)
            self._notify[station_id] = subscribed
        if self.data is not None:
            notify = dict(self.data.notify_subscriptions)
            notify[station_id] = subscribed
            self.async_set_updated_data(replace(self.data, notify_subscriptions=notify))
        self.schedule_refresh()

    def schedule_refresh(self) -> None:
        """Refresh shortly after a start/stop command."""

        async def _refresh(_now: datetime) -> None:
            await self.async_request_refresh()

        async_call_later(self.hass, REFRESH_AFTER_COMMAND_SECONDS, _refresh)

    # ----- start / stop with confirmation ---------------------------------

    async def async_start_charge(self, station_id: int, socket_id: int) -> CommandResult:
        """Start a charge and wait until the charger shows the outcome.

        Raises `DriivzError` if the portal rejects the command and `CommandUnconfirmed`
        if it accepted it but nothing visible happened within the timeout. Either way
        the outcome is recorded as the last command and fired as an event.
        """
        socket_before = self.get_socket(station_id, socket_id)
        # Plugged in already: expect charging. Free socket: the charger accepts the
        # command and then waits for the cable, which is the positive outcome here.
        expect_cable = socket_before is not None and socket_before.status == STATUS_AVAILABLE

        def done(station: Station | None, active_socket_id: int | None) -> str | None:
            socket = _socket_of(station, socket_id)
            if active_socket_id == socket_id or (socket and socket.status in _CHARGING_STATUSES):
                return RESULT_CHARGING
            if expect_cable and socket and socket.status == STATUS_PREPARING:
                return RESULT_AWAITING_CABLE
            return None

        return await self._run_command("start", station_id, socket_id, done)

    async def async_stop_charge(self, station_id: int | None, socket_id: int) -> CommandResult:
        """Stop the charge on a socket and wait until the session is gone."""

        def done(station: Station | None, active_socket_id: int | None) -> str | None:
            socket = _socket_of(station, socket_id)
            still_charging = socket is not None and socket.status in _CHARGING_STATUSES
            if active_socket_id != socket_id and not still_charging:
                return RESULT_STOPPED
            return None

        return await self._run_command("stop", station_id, socket_id, done)

    async def _run_command(
        self,
        command: str,
        station_id: int | None,
        socket_id: int,
        done: Callable[[Station | None, int | None], str | None],
    ) -> CommandResult:
        requested_at = dt_util.utcnow()
        try:
            if command == "start":
                await self.client.start_charge(socket_id)
            else:
                await self.client.stop_charge(socket_id)
        except DriivzError as err:
            self._record_command(
                command, RESULT_REJECTED, station_id, socket_id, requested_at, str(err)
            )
            raise
        result = await self._await_outcome(station_id, socket_id, done)
        record = self._record_command(command, result, station_id, socket_id, requested_at)
        if result == RESULT_UNCONFIRMED:
            raise CommandUnconfirmed
        return record

    async def _await_outcome(
        self,
        station_id: int | None,
        socket_id: int,
        done: Callable[[Station | None, int | None], str | None],
    ) -> str:
        """Wait until `done` says so, or time runs out.

        With the push channel connected, each pushed change for one of our chargers
        wakes this immediately and the check runs on the data it already updated, so
        no request is spent. Otherwise, or when the push channel is quiet, the charger
        and the active session are polled; every poll is published to the entities, so
        status, availability and the session sensors update live while the button shows
        its spinner. Polling stops early when the portal's rate-limit headroom gets low;
        the normal refresh then picks the outcome up.
        """
        for _ in range(COMMAND_MAX_POLLS):
            woken_by_push = await self._wait_for_push(COMMAND_POLL_INTERVAL_SECONDS)
            if woken_by_push and self.push_connected:
                merged = self.get_station(station_id) if station_id is not None else None
                active = self.data.active if self.data else None
                if (result := done(merged, active.socket_id if active else None)) is not None:
                    return result
                continue
            remaining = self.client.rate_limit_remaining
            if remaining is not None and remaining <= COMMAND_RATE_LIMIT_FLOOR:
                _LOGGER.debug("Stopping command polling early; rate limit remaining %s", remaining)
                break
            try:
                station = None
                if station_id is not None:
                    found = await self.client.find_stations_by_ids([station_id])
                    station = found[0] if found else None
                active = await self._fetch_active()
            except DriivzError as err:
                _LOGGER.debug("Command polling failed (%s); waiting for the next poll", err)
                continue
            self._publish_poll(station, active)
            merged = self.get_station(station_id) if station_id is not None else None
            if (result := done(merged, active.socket_id if active else None)) is not None:
                return result
        return RESULT_UNCONFIRMED

    async def _wait_for_push(self, timeout: float) -> bool:
        """Sleep up to `timeout` seconds, returning early (True) on a pushed change."""
        try:
            await asyncio.wait_for(self._push_event.wait(), timeout)
        except TimeoutError:
            return False
        self._push_event.clear()
        return True

    def _publish(self, data: MerData) -> None:
        """Hand new data to the entities without disturbing the poll schedule.

        `async_set_updated_data` would also push the next scheduled poll back by a full
        interval. With the push channel delivering an estimate every 45 s or so, that
        starved the 5-minute poll completely: energy kept updating from pushes while
        everything only a poll provides (session duration, wallet, history) froze.
        Seen live on 2026-09-24.
        """
        self.data = data
        self.async_update_listeners()

    def _publish_poll(self, station: Station | None, active: ActiveSession | None) -> None:
        if self.data is None:
            return
        stations = dict(self.data.stations)
        if station is not None:
            stations[station.id] = station
        self._publish(replace(self.data, stations=stations, active=active))

    def _record_command(
        self,
        command: str,
        result: str,
        station_id: int | None,
        socket_id: int,
        requested_at: datetime,
        message: str | None = None,
    ) -> CommandResult:
        station = self.get_station(station_id) if station_id is not None else None
        socket = _socket_of(station, socket_id)
        record = CommandResult(
            command=command,
            result=result,
            station_id=station_id,
            station_name=station.display_name if station else None,
            socket_id=socket_id,
            socket_name=socket.name if socket else None,
            requested_at=requested_at,
            finished_at=dt_util.utcnow(),
            message=message,
        )
        self._last_command = record
        if self.data is not None:
            self._publish(replace(self.data, last_command=record))
        self.hass.bus.async_fire(EVENT_COMMAND_RESULT, record.as_event_data())
        return record

    # ----- push channel -----------------------------------------------------

    async def async_run_push(self) -> None:
        """Keep the portal websocket open for as long as the entry is loaded.

        Runs as a config-entry background task. Connected, socket status changes for
        our chargers and estimates for the running session are applied the moment they
        arrive and the poll drops to a slow safety net. Any failure reconnects with
        backoff; while disconnected the poll runs at its configured interval again.
        """
        backoff = PUSH_RECONNECT_MIN_SECONDS
        fresh_login = False
        while True:
            try:
                if fresh_login:
                    # The last connection went silent: its server-side session is the
                    # likely casualty, so do not reuse the cookie that produced it.
                    await self.client.login()
                    fresh_login = False
                ws = await self.client.connect_push()
            except Exception as err:
                _LOGGER.debug("Mer push channel unavailable (%s); retry in %ss", err, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, PUSH_RECONNECT_MAX_SECONDS)
                continue
            backoff = PUSH_RECONNECT_MIN_SECONDS
            self._set_push_connected(True)
            # Silence watchdog. A live channel is never quiet for long: the whole
            # network's status changes come through it. If nothing arrives for the
            # timeout, TCP is up but nothing is behind it; close the socket so the
            # receive below returns, and reconnect after a fresh login.
            silent = False

            @callback
            def _on_silence(_now: datetime, _ws: Any = ws) -> None:
                nonlocal silent
                silent = True
                self.hass.async_create_task(_ws.close())

            cancel_watchdog = async_call_later(self.hass, PUSH_SILENCE_TIMEOUT_SECONDS, _on_silence)
            try:
                while True:
                    message = await ws.receive()
                    if silent or message.type in (
                        WSMsgType.CLOSE,
                        WSMsgType.CLOSING,
                        WSMsgType.CLOSED,
                        WSMsgType.ERROR,
                    ):
                        break
                    if message.type != WSMsgType.TEXT:
                        continue
                    cancel_watchdog()
                    cancel_watchdog = async_call_later(
                        self.hass, PUSH_SILENCE_TIMEOUT_SECONDS, _on_silence
                    )
                    self.last_push_at = dt_util.utcnow()
                    self._handle_push_text(message.data)
            except Exception as err:
                _LOGGER.debug("Mer push channel error: %s", err)
            finally:
                cancel_watchdog()
                with contextlib.suppress(Exception):
                    await ws.close()
                self._set_push_connected(False)
            if silent:
                _LOGGER.warning(
                    "Mer live updates silent for %ss; reconnecting with a fresh login",
                    PUSH_SILENCE_TIMEOUT_SECONDS,
                )
                fresh_login = True
            _LOGGER.debug("Mer push channel closed; reconnecting in %ss", backoff)
            await asyncio.sleep(backoff)

    def _set_push_connected(self, connected: bool) -> None:
        if connected == self.push_connected:
            return
        self.push_connected = connected
        self.update_interval = (
            timedelta(seconds=PUSH_POLL_INTERVAL_SECONDS) if connected else self._poll_interval
        )
        _LOGGER.info(
            "Mer live updates %s; polling every %ss",
            "connected" if connected else "disconnected",
            int(self.update_interval.total_seconds()),
        )
        if self.data is not None:
            # Re-publishing applies the new interval to the next scheduled poll and lets
            # the "Live updates" sensor follow the connection state.
            self.async_set_updated_data(self.data)
        if not connected:
            # Whatever changed while the channel was down is caught up now, not in 5 min.
            self.hass.async_create_task(self.async_request_refresh())

    def _handle_push_text(self, text: str) -> None:
        try:
            push = parse_push(json.loads(text))
        except ValueError:
            return
        if push is None or self.data is None:
            return
        if isinstance(push, StatusPush):
            self._apply_status_push(push)
        else:
            self._apply_estimate_push(push)

    def _apply_status_push(self, push: StatusPush) -> None:
        assert self.data is not None
        if push.station_id not in self.data.stations:
            return  # the broadcast covers every charger on the network
        stations = dict(self.data.stations)
        stations[push.station_id] = stations[push.station_id].with_push(push)
        active = self.data.active
        # A session whose socket left the charging states is over; the next poll fills
        # in the history, but the entities should not claim you are charging meanwhile.
        if (
            active is not None
            and push.socket_id == active.socket_id
            and push.socket_status is not None
            and push.socket_status not in _CHARGING_STATUSES
        ):
            active = None
        self._publish(replace(self.data, stations=stations, active=active))
        self._push_event.set()

    def _apply_estimate_push(self, push: EstimatePush) -> None:
        assert self.data is not None
        active = self.data.active
        if active is None or active.socket_id != push.socket_id:
            # A session we do not know about yet (started at the charger, or by the
            # app): fetch it properly rather than guess at it.
            if any(
                push.socket_id == s.id for st in self.data.stations.values() for s in st.sockets
            ):
                self.hass.async_create_task(self.async_request_refresh())
            return
        updated = replace(
            active,
            energy_kwh=push.energy_kwh if push.energy_kwh is not None else active.energy_kwh,
            cost=push.cost if push.cost is not None else active.cost,
            currency=push.currency or active.currency,
        )
        self._publish(replace(self.data, active=updated))
        self._push_event.set()

    # ----- session duration ticker ----------------------------------------------

    @callback
    def async_update_listeners(self) -> None:
        """Every publish (poll, push, command result) also re-evaluates the ticker."""
        self._sync_session_ticker()
        super().async_update_listeners()

    @callback
    def _sync_session_ticker(self) -> None:
        """Run a ticker while a session with a known start is in the data, else stop it.

        Called from `async_update_listeners`, so after every publish. Registering it as
        a listener instead would keep the coordinator polling after the entities are
        gone. The poll is the source of truth: it
        re-reads the duration from the portal every cycle, which corrects any drift,
        and when a poll, a push or a stop command shows the session gone the ticker
        stops with it, so the duration never runs on after the charge has ended.
        """
        active = self.data.active if self.data else None
        wanted = active is not None and active.started_at is not None
        if wanted and self._cancel_session_ticker is None:
            self._cancel_session_ticker = async_track_time_interval(
                self.hass,
                self._tick_session,
                timedelta(seconds=SESSION_TICK_SECONDS),
                cancel_on_shutdown=True,
            )
        elif not wanted and self._cancel_session_ticker is not None:
            self._cancel_session_ticker()
            self._cancel_session_ticker = None

    @callback
    def _tick_session(self, now: datetime) -> None:
        active = self.data.active if self.data else None
        if active is None or active.started_at is None:
            return
        self._publish(replace(self.data, active=replace(active, duration=now - active.started_at)))

    async def async_shutdown(self) -> None:
        if self._cancel_session_ticker is not None:
            self._cancel_session_ticker()
            self._cancel_session_ticker = None
        await super().async_shutdown()


def _socket_of(station: Station | None, socket_id: int) -> Socket | None:
    if station is None:
        return None
    return next((s for s in station.sockets if s.id == socket_id), None)


type MerConfigEntry = ConfigEntry[MerCoordinator]
