"""Data update coordinator for the Mer integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_SCAN_INTERVAL,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    CONF_STATION_IDS,
    DEFAULT_SCAN_INTERVAL,
    DETAIL_REFRESH,
    DOMAIN,
    HISTORY_LOOKBACK,
    RATE_LIMIT_SKIP_THRESHOLD,
    RATE_LIMIT_WARN_INTERVAL,
    REFRESH_AFTER_COMMAND_SECONDS,
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
        self.station_ids: list[int] = [int(i) for i in entry.options.get(CONF_STATION_IDS, [])]
        self.site_id: int | None = entry.options.get(CONF_SITE_ID)
        self.site_name: str = entry.options.get(CONF_SITE_NAME) or "Mer site"
        self._details: dict[int, Station] = {}
        self._wallet: Wallet | None = None
        self._last_transaction: Transaction | None = None
        self._customer_id: int | None = None
        self._details_refreshed: datetime | None = None
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
            stations = {s.id: s for s in await self.client.find_stations_by_ids(self.station_ids)}
            active = await self._fetch_active()
            if self._due(self._wallet_refreshed, WALLET_REFRESH, now):
                await self._refresh_account(now)
            if self._due(self._details_refreshed, DETAIL_REFRESH, now):
                await self._refresh_details(now)
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

    async def _refresh_account(self, now: datetime) -> None:
        """Fetch wallet and history, then commit both atomically.

        Every call below must succeed before any `self.*` attribute is written, so a
        client error partway through never leaves the cached wallet/customer id/last
        transaction ahead of the last successfully published cycle.
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
        self._wallet_refreshed = now

    async def _refresh_details(self, now: datetime) -> None:
        """Fetch every configured station's details, then commit them atomically.

        Built up in a local dict so a client error partway through the loop leaves
        `self._details` untouched rather than half-updated.
        """
        details = dict(self._details)
        for station_id in self.station_ids:
            details[station_id] = await self.client.find_station_by_id(station_id)
        self._details = details
        self._details_refreshed = now

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

    def schedule_refresh(self) -> None:
        """Refresh shortly after a start/stop command."""

        async def _refresh(_now: datetime) -> None:
            await self.async_request_refresh()

        async_call_later(self.hass, REFRESH_AFTER_COMMAND_SECONDS, _refresh)


type MerConfigEntry = ConfigEntry[MerCoordinator]
