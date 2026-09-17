"""Sensor platform for Mer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import MerConfigEntry, MerCoordinator
from .driivz.models import Socket, Station
from .entity import STATUS_OPTIONS, MerSocketEntity, MerStationEntity, status_option


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
        for socket in station.sockets:
            entities.extend(
                MerSocketSensor(coordinator, station.id, socket.id, description)
                for description in SOCKET_SENSORS
            )
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
