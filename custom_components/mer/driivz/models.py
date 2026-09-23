"""Dataclass models for Driivz driver-portal responses."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import re
from typing import Any, ClassVar

from .const import IN_USE_STATUSES, STATUS_AVAILABLE, STATUS_UNKNOWN

_RESTRICTED_PREFIX = "[RESTRICTED ACCESS]"
_CODE_RE = re.compile(r"\(\s*MER-[A-Z0-9-]+\s*\)")
_SPACES_RE = re.compile(r"\s+")


def clean_caption(caption: str) -> str:
    """Return a human-friendly charger name from a portal caption."""
    text = caption.replace(_RESTRICTED_PREFIX, "")
    text = _CODE_RE.sub("", text)
    return _SPACES_RE.sub(" ", text).strip(" -")


def ms_to_datetime(value: Any) -> datetime | None:
    """Convert epoch milliseconds to an aware UTC datetime."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _elapsed(data: Mapping[str, Any]) -> timedelta | None:
    """Elapsed milliseconds, under either of the two spellings the portal uses."""
    for key in ("txDuration", "duration"):
        value = _float(data.get(key))
        if value is not None:
            return timedelta(milliseconds=value)
    return None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def _str(value: Any) -> str | None:
    return str(value) if value is not None else None


@dataclass(frozen=True, slots=True)
class Bounds:
    """A latitude/longitude bounding box in portal field naming."""

    north_east_lat: float
    north_east_lng: float
    south_west_lat: float
    south_west_lng: float

    UK: ClassVar[Bounds]

    @classmethod
    def around(cls, latitude: float, longitude: float, delta: float = 0.003) -> Bounds:
        """Return a small box centred on a coordinate."""
        return cls(
            round(latitude + delta, 6),
            round(longitude + delta, 6),
            round(latitude - delta, 6),
            round(longitude - delta, 6),
        )

    def to_dict(self) -> dict[str, float]:
        """Return the portal's `filterByBounds` payload."""
        return {
            "northEastLat": self.north_east_lat,
            "northEastLng": self.north_east_lng,
            "southWestLat": self.south_west_lat,
            "southWestLng": self.south_west_lng,
        }


Bounds.UK = Bounds(61.0, 2.0, 49.5, -9.0)


@dataclass(frozen=True, slots=True)
class Site:
    """A charging site (car park) from findSitesInBounds."""

    id: int
    name: str
    status: str
    socket_count: int
    access_level: str | None
    latitude: float | None
    longitude: float | None
    charging_speed: str | None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Site:
        return cls(
            id=int(data["id"]),
            name=str(data.get("dn") or ""),
            status=str(data.get("ss") or STATUS_UNKNOWN),
            socket_count=_int(data.get("ns")) or 0,
            access_level=_str(data.get("sal")),
            latitude=_float(data.get("latitude")),
            longitude=_float(data.get("longitude")),
            charging_speed=_str(data.get("scs")),
        )


@dataclass(frozen=True, slots=True)
class SocketPrice:
    """Tariff for one socket under one billing plan."""

    billing_plan_id: int | None
    billing_plan_code: str | None
    kwh_price: float | None
    plug_in_minute_rate: float | None
    transaction_fee: float | None
    currency: str | None
    fix_price: float | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SocketPrice:
        return cls(
            billing_plan_id=_int(data.get("billingPlanId")),
            billing_plan_code=_str(data.get("billingPlanCode")),
            kwh_price=_float(data.get("kwhPrice")),
            plug_in_minute_rate=_float(data.get("plugInMinuteRate")),
            transaction_fee=_float(data.get("transactionFee")),
            currency=_str(data.get("currency") or data.get("billingSpCurrencyCurrency")),
            fix_price=_float(data.get("fixPrice")),
        )

    @property
    def energy_price(self) -> float:
        """Price per kWh; a tariff with no `kwhPrice` has no energy component.

        The portal omits tariff components that are not in use: a flat-rate plan
        (observed live in September 2026) arrives with `fixPrice` and no `kwhPrice`
        at all, where older responses carried `kwhPrice: 0`. Either way nothing is
        charged per kWh, so absence is 0 rather than unknown.
        """
        return self.kwh_price if self.kwh_price is not None else 0.0


@dataclass(frozen=True, slots=True)
class Socket:
    """One connector on a station."""

    id: int
    station_id: int | None = None
    name: str | None = None
    identity_key: str | None = None
    status: str = STATUS_UNKNOWN
    max_power_kw: float | None = None
    socket_type: str | None = None
    voltage_type: str | None = None
    station_caption: str | None = None
    prices: tuple[SocketPrice, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Socket:
        return cls(
            id=int(data["id"]),
            station_id=_int(data.get("stationId")),
            name=_str(data.get("name")),
            identity_key=_str(data.get("identityKey")),
            status=str(data.get("socketStatusId") or STATUS_UNKNOWN),
            max_power_kw=_float(data.get("maximumPower")),
            socket_type=_str(data.get("stationModelSocketSocketTypeId")),
            voltage_type=_str(data.get("stationModelSocketVoltageType")),
            # Only findLastActiveChargeSocket's superset payload carries this.
            station_caption=_str(data.get("stationCaption")),
            prices=tuple(SocketPrice.from_dict(p) for p in data.get("socketPrices") or []),
        )

    @property
    def price_per_kwh(self) -> float | None:
        """Per-kWh price of the driver's tariff; None only when no tariff was returned."""
        return self.prices[0].energy_price if self.prices else None

    @property
    def is_available(self) -> bool:
        return self.status == STATUS_AVAILABLE

    @property
    def is_in_use(self) -> bool:
        return self.status in IN_USE_STATUSES


@dataclass(frozen=True, slots=True)
class Station:
    """A charger (station) with its sockets."""

    id: int
    caption: str
    status: str = STATUS_UNKNOWN
    latitude: float | None = None
    longitude: float | None = None
    site_id: int | None = None
    site_name: str | None = None
    identity_key: str | None = None
    model_name: str | None = None
    owner_name: str | None = None
    access_level: str | None = None
    coming_soon: bool = False
    sockets: tuple[Socket, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Station:
        return cls(
            id=int(data["id"]),
            caption=str(data.get("caption") or ""),
            status=str(data.get("stationStatusId") or STATUS_UNKNOWN),
            latitude=_float(data.get("latitude")),
            longitude=_float(data.get("longitude")),
            site_id=_int(data.get("siteId")),
            site_name=_str(data.get("siteName") or data.get("siteDisplayName")),
            identity_key=_str(data.get("identityKey")),
            model_name=_str(data.get("stationModelName")),
            owner_name=_str(data.get("stationOwnerName")),
            access_level=_str(data.get("siteStationAccessLevel")),
            coming_soon=bool(data.get("comingSoon", False)),
            sockets=tuple(Socket.from_dict(s) for s in data.get("stationSockets") or []),
        )

    @property
    def display_name(self) -> str:
        return clean_caption(self.caption) or f"Station {self.id}"

    @property
    def is_restricted(self) -> bool:
        return _RESTRICTED_PREFIX in self.caption

    def with_live(self, live: Station) -> Station:
        """Return this (detailed) station with statuses taken from a live short DTO."""
        live_sockets = {s.id: s for s in live.sockets}
        merged = [
            replace(s, status=live_sockets[s.id].status) if s.id in live_sockets else s
            for s in self.sockets
        ]
        known = {s.id for s in merged}
        merged.extend(s for s in live.sockets if s.id not in known)
        return replace(self, status=live.status, sockets=tuple(merged))


@dataclass(frozen=True, slots=True)
class Wallet:
    """The customer's wallet/account summary."""

    id: int | None
    customer_id: int | None
    member_id: int | None
    account_number: int | None
    balance: float | None
    currency: str | None
    timezone: str | None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Wallet:
        return cls(
            id=_int(data.get("id")),
            customer_id=_int(data.get("customerDetailId")),
            member_id=_int(data.get("customerDetailMemberId")),
            account_number=_int(data.get("accountNumber")),
            balance=_float(data.get("accountBalance")),
            currency=_str(data.get("currency") or data.get("billingSpCurrencyCurrency")),
            timezone=_str(data.get("timezoneZoneId")),
        )


@dataclass(frozen=True, slots=True)
class ActiveTransaction:
    """The running transaction, as findCurrentTransactionStartTime reports it."""

    transaction_id: int | None
    elapsed: timedelta | None
    boost_enabled: bool
    started_on: datetime | None = None

    # Mer sends none of these -- the payload carries only elapsed milliseconds -- but
    # another Driivz tenant might, and honouring one costs nothing. Read here rather than
    # in a standalone parser so the tolerance actually runs on the live path.
    _START_KEYS: ClassVar[tuple[str, ...]] = (
        "startOn",
        "startTime",
        "startedOn",
        "transactionStartTime",
    )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ActiveTransaction:
        started_on: datetime | None = None
        for key in cls._START_KEYS:
            if data.get(key) is not None:
                started_on = ms_to_datetime(data[key])
                break
        return cls(
            transaction_id=_int(data.get("transactionId")),
            elapsed=_elapsed(data),
            boost_enabled=bool(data.get("boostEnabled", False)),
            started_on=started_on,
        )

    def started_at(self, now: datetime) -> datetime | None:
        """When the charge started.

        `findCurrentTransactionStartTime` returns no timestamp on Mer -- only how many
        milliseconds the transaction has been running -- so the start is normally derived
        from `now`. An absolute epoch, if one was present in the payload, wins.
        """
        if self.started_on is not None:
            return self.started_on
        return now - self.elapsed if self.elapsed is not None else None


@dataclass(frozen=True, slots=True)
class Transaction:
    """A completed charge transaction from the driver's history."""

    id: int
    station_id: int | None
    caption: str
    site_name: str | None
    started_at: datetime | None
    stopped_at: datetime | None
    duration_s: int | None
    energy_kwh: float | None
    cost: float | None
    currency: str | None
    billing_plan_name: str | None
    billing_status: str | None
    socket_type: str | None
    start_initiator: str | None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Transaction:
        energy_wh = _float(data.get("totalEnergy"))
        return cls(
            id=int(data["id"]),
            station_id=_int(data.get("stationId")),
            caption=str(data.get("caption") or ""),
            site_name=_str(data.get("siteName")),
            started_at=ms_to_datetime(data.get("startOn")),
            stopped_at=ms_to_datetime(data.get("stoppedOn")),
            duration_s=_int(data.get("durationTime")),
            energy_kwh=round(energy_wh / 1000, 3) if energy_wh is not None else None,
            cost=_float(data.get("cost")),
            currency=_str(data.get("currency")),
            billing_plan_name=_str(data.get("billingPlanName")),
            billing_status=_str(data.get("chargeTransactionBillingStatus")),
            socket_type=_str(data.get("socketType")),
            start_initiator=_str(data.get("startInitiator")),
        )

    @property
    def display_name(self) -> str:
        return clean_caption(self.caption)


@dataclass(frozen=True, slots=True)
class SessionEstimate:
    """Live energy/cost estimate of the running transaction (field names tolerant)."""

    energy_kwh: float | None
    cost: float | None
    currency: str | None
    duration: timedelta | None = None
    rate_estimation: float | None = None

    # findCurrentTransactionBillingChargingEstimation's `totalKw` is named as though it were
    # power, but it is kWh delivered: a live session showed 1.606 over 953 s on a 7.4 kW socket,
    # which is a ~6 kW average draw -- consistent with energy, not an instantaneous reading.
    _ENERGY_KEYS: ClassVar[tuple[tuple[str, float], ...]] = (
        ("totalKw", 1.0),
        ("energyKwh", 1.0),
        ("totalEnergyKwh", 1.0),
        ("energy", 1.0),
        ("totalEnergy", 1000.0),
        ("energyConsumed", 1000.0),
    )
    _COST_KEYS: ClassVar[tuple[str, ...]] = ("cost", "totalCost", "estimatedCost", "price")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SessionEstimate:
        energy: float | None = None
        for key, divisor in cls._ENERGY_KEYS:
            value = _float(data.get(key))
            if value is not None:
                energy = round(value / divisor, 3)
                break
        cost: float | None = None
        for key in cls._COST_KEYS:
            cost = _float(data.get(key))
            if cost is not None:
                break
        return cls(
            energy_kwh=energy,
            cost=cost,
            currency=_str(data.get("currency")),
            duration=_elapsed(data),
            rate_estimation=_float(data.get("rateEstimation")),
        )
