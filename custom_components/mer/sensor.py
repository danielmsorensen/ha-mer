"""Sensor platform for Mer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import DOMAIN
from .coordinator import (
    COMMAND_RESULTS,
    ActiveSession,
    MerConfigEntry,
    MerCoordinator,
    MerData,
)
from .driivz.models import Socket, Station, Transaction, clean_caption
from .entity import (
    STATUS_OPTIONS,
    MerAccountEntity,
    MerSocketEntity,
    MerStationEntity,
    socket_label,
    status_option,
)


@dataclass(frozen=True, kw_only=True)
class MerStationSensorDescription(SensorEntityDescription):
    """Sensor reading a Station."""

    value_fn: Callable[[Station], StateType]
    attributes_fn: Callable[[Station], dict[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class MerSocketSensorDescription(SensorEntityDescription):
    """Sensor reading a Socket."""

    value_fn: Callable[[Socket], StateType]
    unit_fn: Callable[[Socket], str | None] | None = None
    attributes_fn: Callable[[MerSocketSensor, Socket], dict[str, Any]] | None = None


def _socket_status_attributes(entity: MerSocketSensor, socket: Socket) -> dict[str, Any]:
    """Name the socket, point at its start button, and say whether it is your session.

    The status alone says "charging" for anyone's car; `my_session` says it is yours.
    `charger`, `socket` and `start_button` let an automation or blueprint work from this
    sensor alone, without knowing how entities are named.
    """
    station = entity.station
    return {
        "charger": station.display_name if station else None,
        "socket": socket_label(socket),
        "start_button": er.async_get(entity.hass).async_get_entity_id(
            "button", DOMAIN, f"{entity.entry_id}_socket_{socket.id}_start_charge"
        ),
        "my_session": entity.coordinator.data.session_on_socket(socket.id) is not None,
        "max_power_kw": socket.max_power_kw,
        "connector": socket.socket_type,
    }


def _socket_price_attributes(_entity: MerSocketSensor, socket: Socket) -> dict[str, Any]:
    """The rest of the driver's tariff on this socket, beside the per-kWh price."""
    if not socket.prices:
        return {}
    price = socket.prices[0]
    return {
        "billing_plan": price.billing_plan_code,
        "fixed_price": price.fix_price,
        "per_minute_rate": price.plug_in_minute_rate,
        "transaction_fee": price.transaction_fee,
    }


def _socket_price_unit(socket: Socket) -> str:
    """Return the tariff currency's unit, falling back to the tenant's default."""
    currency = socket.prices[0].currency if socket.prices else None
    return f"{currency}/kWh" if currency else "GBP/kWh"


STATION_SENSORS: tuple[MerStationSensorDescription, ...] = (
    MerStationSensorDescription(
        key="status",
        translation_key="station_status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUS_OPTIONS,
        value_fn=lambda station: status_option(station.status),
    ),
    MerStationSensorDescription(
        key="available_sockets",
        translation_key="station_available_sockets",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda station: sum(1 for s in station.sockets if s.is_available),
        attributes_fn=lambda station: {"total_sockets": len(station.sockets)},
    ),
)

SOCKET_SENSORS: tuple[MerSocketSensorDescription, ...] = (
    MerSocketSensorDescription(
        key="status",
        translation_key="socket_status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUS_OPTIONS,
        value_fn=lambda socket: status_option(socket.status),
        attributes_fn=_socket_status_attributes,
    ),
    MerSocketSensorDescription(
        key="price",
        translation_key="socket_price",
        suggested_display_precision=2,
        value_fn=lambda socket: socket.price_per_kwh,
        unit_fn=_socket_price_unit,
        attributes_fn=_socket_price_attributes,
    ),
)


@dataclass(frozen=True, kw_only=True)
class MerAccountSensorDescription(SensorEntityDescription):
    """Sensor reading coordinator-wide data."""

    value_fn: Callable[[MerCoordinator, MerData], StateType | datetime]
    unit_fn: Callable[[MerData], str | None] | None = None
    attributes_fn: Callable[[MerCoordinator, MerData], dict[str, Any]] | None = None
    # Describes the active session: unavailable, not unknown, while there is none.
    requires_session: bool = False
    # Stays available when polls fail: it is how you find out that they are failing.
    always_available: bool = False


def _count_sockets(coordinator: MerCoordinator, predicate: Callable[[Socket], bool]) -> int:
    """Count sockets across every configured charger that satisfy the predicate."""
    return sum(
        1
        for station in coordinator.configured_stations()
        for socket in station.sockets
        if predicate(socket)
    )


# Aggregates over the chargers you added, on the account device: the "is anything free
# at work" view this integration exists for.
def _free_sockets(coordinator: MerCoordinator, _data: MerData) -> dict[str, Any]:
    """Every free socket, in charger order, with the entity id of its start button.

    Enough for an automation to name the free sockets or press the first one's
    button without knowing any entity names, plus the totals the count is out of.
    """
    registry = er.async_get(coordinator.hass)
    entry_id = coordinator.config_entry.entry_id
    stations = coordinator.configured_stations()
    free: list[dict[str, Any]] = [
        {
            "charger": station.display_name,
            "socket": socket_label(socket),
            "station_id": station.id,
            "socket_id": socket.id,
            "start_button": registry.async_get_entity_id(
                "button", DOMAIN, f"{entry_id}_socket_{socket.id}_start_charge"
            ),
        }
        for station in stations
        for socket in station.sockets
        if socket.is_available
    ]
    return {
        "available_sockets": free,
        "total_sockets": sum(len(station.sockets) for station in stations),
        "chargers": len(stations),
    }


AGGREGATE_SENSORS: tuple[MerAccountSensorDescription, ...] = (
    MerAccountSensorDescription(
        key="available_sockets",
        translation_key="account_available_sockets",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator, _d: _count_sockets(coordinator, lambda s: s.is_available),
        attributes_fn=_free_sockets,
    ),
    MerAccountSensorDescription(
        key="sockets_in_use",
        translation_key="account_sockets_in_use",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator, _d: _count_sockets(coordinator, lambda s: s.is_in_use),
    ),
)


@dataclass(frozen=True, kw_only=True)
class MerStationSessionSensorDescription(SensorEntityDescription):
    """Sensor mirroring the account's active session onto the charger it is running on."""

    session_fn: Callable[[ActiveSession | None], StateType | datetime]
    unit_fn: Callable[[ActiveSession | None, MerData], str | None] | None = None


def _active_charger_name(coordinator: MerCoordinator, data: MerData) -> str | None:
    if data.active is None:
        return None
    if data.active.station_caption:
        return clean_caption(data.active.station_caption)
    if data.active.station_id is not None:
        station = coordinator.get_station(data.active.station_id)
        if station is not None:
            return station.display_name
    return None


def _active_socket_name(coordinator: MerCoordinator, data: MerData) -> str | None:
    if data.active is None:
        return None
    if data.active.station_id is not None:
        socket = coordinator.get_socket(data.active.station_id, data.active.socket_id)
        if socket is not None:
            return socket_label(socket)
    return data.active.socket_name or f"Socket {data.active.socket_id}"


def _active_session_name(coordinator: MerCoordinator, data: MerData) -> str | None:
    """Where the session is running, as "<charger> <socket>"."""
    if data.active is None:
        return None
    charger = _active_charger_name(coordinator, data)
    socket = _active_socket_name(coordinator, data)
    return f"{charger} {socket}" if charger else socket


def _active_attributes(coordinator: MerCoordinator, data: MerData) -> dict[str, Any]:
    if data.active is None:
        return {}
    return {
        "charger": _active_charger_name(coordinator, data),
        "socket": _active_socket_name(coordinator, data),
        "station_id": data.active.station_id,
        "socket_id": data.active.socket_id,
    }


def _last(data: MerData) -> Transaction | None:
    return data.last_transaction


def _currency(data: MerData) -> str:
    if data.wallet and data.wallet.currency:
        return data.wallet.currency
    if data.last_transaction and data.last_transaction.currency:
        return data.last_transaction.currency
    return "GBP"


def _active_session_for(data: MerData, station_id: int) -> ActiveSession | None:
    """Return your session running on this station, if any."""
    return data.session_on_station(station_id)


# The account sensors read `data.active` unconditionally; the charger-device sensors read
# `_active_session_for(data, station_id)`, which is `None` unless the session belongs to that
# station. Both variants then share these five functions to pull a value out of whichever
# session (or `None`) they were handed, so the two sensor families never duplicate the logic
# for what a field means -- only how the session is looked up differs.
def _session_energy(session: ActiveSession | None) -> float | None:
    return session.energy_kwh if session else None


def _session_cost(session: ActiveSession | None) -> float | None:
    return session.cost if session else None


def _session_started(session: ActiveSession | None) -> datetime | None:
    return session.started_at if session else None


def _session_rate(session: ActiveSession | None) -> float | None:
    return session.rate_kw if session else None


def _session_duration(session: ActiveSession | None) -> float | None:
    return session.duration.total_seconds() if session and session.duration is not None else None


def _session_currency(session: ActiveSession | None, data: MerData) -> str:
    if session and session.currency:
        return session.currency
    return _currency(data)


ACCOUNT_SENSORS: tuple[MerAccountSensorDescription, ...] = (
    MerAccountSensorDescription(
        key="active_session",
        requires_session=True,
        translation_key="active_session",
        value_fn=_active_session_name,
        attributes_fn=_active_attributes,
    ),
    MerAccountSensorDescription(
        key="active_started",
        requires_session=True,
        translation_key="active_started",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda _c, data: _session_started(data.active),
    ),
    MerAccountSensorDescription(
        key="active_energy",
        requires_session=True,
        translation_key="active_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda _c, data: _session_energy(data.active),
    ),
    MerAccountSensorDescription(
        key="active_cost",
        requires_session=True,
        translation_key="active_cost",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        value_fn=lambda _c, data: _session_cost(data.active),
        unit_fn=lambda data: _session_currency(data.active, data),
    ),
    MerAccountSensorDescription(
        key="active_duration",
        requires_session=True,
        translation_key="active_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        # Shown in hours by default; HA converts, and users can still pick another unit.
        # No display precision, so the frontend's duration formatting shows h and min.
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        value_fn=lambda _c, data: _session_duration(data.active),
    ),
    MerAccountSensorDescription(
        key="active_rate",
        requires_session=True,
        translation_key="active_rate",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=2,
        value_fn=lambda _c, data: _session_rate(data.active),
    ),
    MerAccountSensorDescription(
        key="last_energy",
        translation_key="last_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda _c, data: _last(data).energy_kwh if _last(data) else None,
    ),
    MerAccountSensorDescription(
        key="last_cost",
        translation_key="last_cost",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        value_fn=lambda _c, data: _last(data).cost if _last(data) else None,
        unit_fn=_currency,
    ),
    MerAccountSensorDescription(
        key="last_started",
        translation_key="last_started",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda _c, data: _last(data).started_at if _last(data) else None,
        attributes_fn=lambda _c, data: (
            {"station": _last(data).display_name} if _last(data) else {}
        ),
    ),
    MerAccountSensorDescription(
        key="last_command",
        translation_key="last_command",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=list(COMMAND_RESULTS),
        value_fn=lambda _c, data: data.last_command.result if data.last_command else None,
        attributes_fn=lambda _c, data: (
            {
                "command": data.last_command.command,
                "charger": data.last_command.station_name,
                "socket": data.last_command.socket_name,
                "requested_at": data.last_command.requested_at.isoformat(),
                "finished_at": data.last_command.finished_at.isoformat(),
                "message": data.last_command.message,
            }
            if data.last_command
            else {}
        ),
    ),
    MerAccountSensorDescription(
        key="last_poll",
        translation_key="last_poll",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        always_available=True,
        value_fn=lambda coordinator, _d: coordinator.last_poll_at,
        attributes_fn=lambda coordinator, _d: {
            "poll_interval_seconds": (
                int(coordinator.update_interval.total_seconds())
                if coordinator.update_interval
                else None
            ),
            "last_poll_succeeded": coordinator.last_update_success,
            "last_error": (
                None
                if coordinator.last_update_success or coordinator.last_exception is None
                else str(coordinator.last_exception)
            ),
        },
    ),
    MerAccountSensorDescription(
        key="requests_remaining",
        translation_key="requests_remaining",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        always_available=True,
        value_fn=lambda coordinator, _d: coordinator.client.rate_limit_remaining,
    ),
    MerAccountSensorDescription(
        key="wallet_balance",
        translation_key="wallet_balance",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        value_fn=lambda _c, data: data.wallet.balance if data.wallet else None,
        unit_fn=_currency,
    ),
)


STATION_SESSION_SENSORS: tuple[MerStationSessionSensorDescription, ...] = (
    MerStationSessionSensorDescription(
        key="session_energy",
        translation_key="station_session_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        session_fn=_session_energy,
    ),
    MerStationSessionSensorDescription(
        key="session_cost",
        translation_key="station_session_cost",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        session_fn=_session_cost,
        unit_fn=_session_currency,
    ),
    MerStationSessionSensorDescription(
        key="session_rate",
        translation_key="station_session_rate",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=2,
        session_fn=_session_rate,
    ),
    MerStationSessionSensorDescription(
        key="session_started",
        translation_key="station_session_started",
        device_class=SensorDeviceClass.TIMESTAMP,
        session_fn=_session_started,
    ),
    MerStationSessionSensorDescription(
        key="session_duration",
        translation_key="station_session_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        # Shown in hours by default; HA converts, and users can still pick another unit.
        # No display precision, so the frontend's duration formatting shows h and min.
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        session_fn=_session_duration,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: MerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Create sensors for every configured charger (under its subentry) and the account."""
    coordinator = entry.runtime_data
    for subentry, station in coordinator.charger_stations():
        entities: list[SensorEntity] = []
        entities.extend(
            MerStationSensor(coordinator, station.id, description)
            for description in STATION_SENSORS
        )
        entities.extend(
            MerStationSessionSensor(coordinator, station.id, description)
            for description in STATION_SESSION_SENSORS
        )
        for socket in station.sockets:
            entities.extend(
                MerSocketSensor(coordinator, station.id, socket.id, description)
                for description in SOCKET_SENSORS
            )
        async_add_entities(entities, config_subentry_id=subentry.subentry_id)
    async_add_entities(
        MerAccountSensor(coordinator, description)
        for description in (*AGGREGATE_SENSORS, *ACCOUNT_SENSORS)
    )


class MerStationSensor(MerStationEntity, SensorEntity):
    """A sensor describing a charger."""

    entity_description: MerStationSensorDescription

    def __init__(
        self, coordinator: MerCoordinator, station_id: int, description: MerStationSensorDescription
    ) -> None:
        super().__init__(coordinator, station_id, description)

    @property
    def native_value(self) -> StateType:
        station = self.station
        return self.entity_description.value_fn(station) if station else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        station = self.station
        if self.entity_description.attributes_fn is None or station is None:
            return None
        return self.entity_description.attributes_fn(station)


class MerStationSessionSensor(MerStationEntity, SensorEntity):
    """Mirrors the account's active session onto the charger device it is running on."""

    entity_description: MerStationSessionSensorDescription

    def __init__(
        self,
        coordinator: MerCoordinator,
        station_id: int,
        description: MerStationSessionSensorDescription,
    ) -> None:
        super().__init__(coordinator, station_id, description)

    def _session(self) -> ActiveSession | None:
        return _active_session_for(self.coordinator.data, self.station_id)

    @property
    def available(self) -> bool:
        """Unavailable, not unknown, while none of your sessions runs on this charger.

        "Unknown" reads as a fault; there is simply nothing to report until you charge
        here. "My session here" on the same device says why.
        """
        return super().available and self._session() is not None

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.session_fn(self._session())

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.entity_description.unit_fn is not None:
            return self.entity_description.unit_fn(self._session(), self.coordinator.data)
        return self.entity_description.native_unit_of_measurement


class MerSocketSensor(MerSocketEntity, SensorEntity):
    """A sensor describing one socket."""

    entity_description: MerSocketSensorDescription

    def __init__(
        self,
        coordinator: MerCoordinator,
        station_id: int,
        socket_id: int,
        description: MerSocketSensorDescription,
    ) -> None:
        super().__init__(coordinator, station_id, socket_id, description)

    @property
    def native_value(self) -> StateType:
        socket = self.socket
        return self.entity_description.value_fn(socket) if socket else None

    @property
    def native_unit_of_measurement(self) -> str | None:
        socket = self.socket
        if self.entity_description.unit_fn is not None and socket is not None:
            return self.entity_description.unit_fn(socket)
        return self.entity_description.native_unit_of_measurement

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        socket = self.socket
        if self.entity_description.attributes_fn is None or socket is None:
            return None
        return self.entity_description.attributes_fn(self, socket)


class MerAccountSensor(MerAccountEntity, SensorEntity):
    """A sensor about the driver's account or session."""

    entity_description: MerAccountSensorDescription

    @property
    def available(self) -> bool:
        if self.entity_description.always_available:
            return self.coordinator.data is not None
        if not super().available:
            return False
        return (
            not self.entity_description.requires_session or self.coordinator.data.active is not None
        )

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self.coordinator, self.coordinator.data)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.entity_description.unit_fn is not None:
            return self.entity_description.unit_fn(self.coordinator.data)
        return self.entity_description.native_unit_of_measurement

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator, self.coordinator.data)
