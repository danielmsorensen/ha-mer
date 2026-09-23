"""Data update coordinator for the Mer integration."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_SCAN_INTERVAL,
    CONF_STATION_ID,
    DEFAULT_SCAN_INTERVAL,
    DETAIL_REFRESH,
    DOMAIN,
    HISTORY_LOOKBACK,
    RATE_LIMIT_SKIP_THRESHOLD,
    RATE_LIMIT_WARN_INTERVAL,
    REFRESH_AFTER_COMMAND_SECONDS,
    SUBENTRY_TYPE_CHARGER,
    WALLET_REFRESH,
)
from .driivz.client import DriivzDriverClient
from .driivz.exceptions import AuthError, DriivzError, RateLimitError
from .driivz.models import Socket, Station, Transaction, Wallet

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
        # One charger subentry per monitored station. Adding or removing one reloads
        # the entry (see __init__), so this list is fixed for the coordinator's life.
        self.charger_subentries: list[ConfigSubentry] = [
            subentry
            for subentry in entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_CHARGER
        ]
        self.station_ids: list[int] = [
            int(subentry.data[CONF_STATION_ID]) for subentry in self.charger_subentries
        ]
        self._details: dict[int, Station] = {}
        self._notify: dict[int, bool] = {}
        self._notify_lock = asyncio.Lock()
        self._wallet: Wallet | None = None
        self._last_transaction: Transaction | None = None
        self._customer_id: int | None = None
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
        """Each charger subentry with its station, skipping any the portal did not return.

        Platforms add a charger's entities under its subentry id, so that removing the
        subentry removes exactly that charger's device and entities.
        """
        pairs: list[tuple[ConfigSubentry, Station]] = []
        for subentry in self.charger_subentries:
            station = self.get_station(int(subentry.data[CONF_STATION_ID]))
            if station is None:
                _LOGGER.warning(
                    "Mer charger %s (%s) was not returned by the portal; skipping its entities",
                    subentry.data[CONF_STATION_ID],
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


type MerConfigEntry = ConfigEntry[MerCoordinator]
