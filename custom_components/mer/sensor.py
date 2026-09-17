"""Sensor platform for Mer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import ActiveSession, MerConfigEntry, MerCoordinator, MerData
from .driivz.models import Socket, Station, Transaction, clean_caption
from .entity import (
    STATUS_OPTIONS,
    MerAccountEntity,
    MerSiteEntity,
    MerSocketEntity,
    MerStationEntity,
    socket_label,
    status_option,
)


@dataclass(frozen=True, kw_only=True)
class MerStationSensorDescription(SensorEntityDescription):
    """Sensor reading a Station."""

    value_fn: Callable[[Station], StateType]


@dataclass(frozen=True, kw_only=True)
class MerSocketSensorDescription(SensorEntityDescription):
    """Sensor reading a Socket."""

    value_fn: Callable[[Socket], StateType]
    unit_fn: Callable[[Socket], str | None] | None = None


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
        key="identity_key",
        translation_key="identity_key",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda station: station.identity_key,
    ),
)

SOCKET_SENSORS: tuple[MerSocketSensorDescription, ...] = (
    MerSocketSensorDescription(
        key="status",
        translation_key="socket_status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUS_OPTIONS,
        value_fn=lambda socket: status_option(socket.status),
    ),
    MerSocketSensorDescription(
        key="price",
        translation_key="socket_price",
        suggested_display_precision=2,
        value_fn=lambda socket: socket.price_per_kwh,
        unit_fn=_socket_price_unit,
    ),
    MerSocketSensorDescription(
        key="max_power",
        translation_key="socket_max_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda socket: socket.max_power_kw,
    ),
)


@dataclass(frozen=True, kw_only=True)
class MerSiteSensorDescription(SensorEntityDescription):
    """Sensor aggregating the configured stations."""

    value_fn: Callable[[list[Station]], StateType]


SITE_SENSORS: tuple[MerSiteSensorDescription, ...] = (
    MerSiteSensorDescription(
        key="available_sockets",
        translation_key="site_available_sockets",
        icon="mdi:ev-plug-type2",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda stations: sum(
            1 for station in stations for socket in station.sockets if socket.is_available
        ),
    ),
    MerSiteSensorDescription(
        key="sockets_in_use",
        translation_key="site_sockets_in_use",
        icon="mdi:ev-plug-type2",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda stations: sum(
            1 for station in stations for socket in station.sockets if socket.is_in_use
        ),
    ),
)


@dataclass(frozen=True, kw_only=True)
class MerAccountSensorDescription(SensorEntityDescription):
    """Sensor reading coordinator-wide data."""

    value_fn: Callable[[MerCoordinator, MerData], StateType | datetime]
    unit_fn: Callable[[MerData], str | None] | None = None
    attributes_fn: Callable[[MerCoordinator, MerData], dict[str, str]] | None = None


@dataclass(frozen=True, kw_only=True)
class MerStationSessionSensorDescription(SensorEntityDescription):
    """Sensor mirroring the account's active session onto the charger it is running on."""

    session_fn: Callable[[ActiveSession | None], StateType | datetime]
    unit_fn: Callable[[ActiveSession | None, MerData], str | None] | None = None


def _active_station_name(coordinator: MerCoordinator, data: MerData) -> str | None:
    if data.active is None:
        return None
    if data.active.station_caption:
        return clean_caption(data.active.station_caption)
    if data.active.station_id is not None:
        station = coordinator.get_station(data.active.station_id)
        if station is not None:
            return station.display_name
    return f"Socket {data.active.socket_id}"


def _active_attributes(coordinator: MerCoordinator, data: MerData) -> dict[str, str]:
    if data.active is None or data.active.station_id is None:
        return {}
    socket = coordinator.get_socket(data.active.station_id, data.active.socket_id)
    return {"socket": socket_label(socket)} if socket else {}


def _last(data: MerData) -> Transaction | None:
    return data.last_transaction


def _currency(data: MerData) -> str:
    if data.wallet and data.wallet.currency:
        return data.wallet.currency
    if data.last_transaction and data.last_transaction.currency:
        return data.last_transaction.currency
    return "GBP"


def _active_session_for(data: MerData, station_id: int) -> ActiveSession | None:
    """Return the active session only when it is running on this station."""
    if data.active is not None and data.active.station_id == station_id:
        return data.active
    return None


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


def _session_duration(session: ActiveSession | None) -> float | None:
    return session.duration.total_seconds() if session and session.duration is not None else None


def _session_currency(session: ActiveSession | None, data: MerData) -> str:
    if session and session.currency:
        return session.currency
    return _currency(data)


ACCOUNT_SENSORS: tuple[MerAccountSensorDescription, ...] = (
    MerAccountSensorDescription(
        key="active_station",
        translation_key="active_station",
        icon="mdi:ev-station",
        value_fn=_active_station_name,
        attributes_fn=_active_attributes,
    ),
    MerAccountSensorDescription(
        key="active_started",
        translation_key="active_started",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda _c, data: _session_started(data.active),
    ),
    MerAccountSensorDescription(
        key="active_energy",
        translation_key="active_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda _c, data: _session_energy(data.active),
    ),
    MerAccountSensorDescription(
        key="active_cost",
        translation_key="active_cost",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        value_fn=lambda _c, data: _session_cost(data.active),
        unit_fn=lambda data: _session_currency(data.active, data),
    ),
    MerAccountSensorDescription(
        key="active_duration",
        translation_key="active_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda _c, data: _session_duration(data.active),
    ),
    MerAccountSensorDescription(
        key="active_socket",
        translation_key="active_socket",
        value_fn=lambda _c, data: data.active.socket_name if data.active else None,
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
        session_fn=_session_duration,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: MerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Create sensors for every configured station and its sockets."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for station in coordinator.configured_stations():
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
    entities.extend(MerSiteSensor(coordinator, description) for description in SITE_SENSORS)
    entities.extend(MerAccountSensor(coordinator, description) for description in ACCOUNT_SENSORS)
    async_add_entities(entities)


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


class MerSiteSensor(MerSiteEntity, SensorEntity):
    """A sensor aggregating all configured chargers at the site."""

    entity_description: MerSiteSensorDescription

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.coordinator.configured_stations())


class MerAccountSensor(MerAccountEntity, SensorEntity):
    """A sensor about the driver's account or session."""

    entity_description: MerAccountSensorDescription

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
