"""Binary sensor platform for Mer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MerConfigEntry, MerCoordinator, MerData
from .driivz.models import Socket, Station
from .entity import MerAccountEntity, MerSiteEntity, MerSocketEntity, MerStationEntity


@dataclass(frozen=True, kw_only=True)
class MerSocketBinaryDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[Socket], bool]


@dataclass(frozen=True, kw_only=True)
class MerSiteBinaryDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[list[Station]], bool]


@dataclass(frozen=True, kw_only=True)
class MerAccountBinaryDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[MerData], bool]


@dataclass(frozen=True, kw_only=True)
class MerStationBinaryDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[MerData, int], bool]


SOCKET_BINARY_SENSORS: tuple[MerSocketBinaryDescription, ...] = (
    MerSocketBinaryDescription(
        key="available",
        translation_key="socket_available",
        icon="mdi:ev-station",
        is_on_fn=lambda socket: socket.is_available,
    ),
)

SITE_BINARY_SENSORS: tuple[MerSiteBinaryDescription, ...] = (
    MerSiteBinaryDescription(
        key="any_available",
        translation_key="site_any_available",
        icon="mdi:ev-station",
        is_on_fn=lambda stations: any(
            socket.is_available for station in stations for socket in station.sockets
        ),
    ),
)

ACCOUNT_BINARY_SENSORS: tuple[MerAccountBinaryDescription, ...] = (
    MerAccountBinaryDescription(
        key="charging",
        translation_key="account_charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        is_on_fn=lambda data: data.active is not None,
    ),
)

STATION_BINARY_SENSORS: tuple[MerStationBinaryDescription, ...] = (
    MerStationBinaryDescription(
        key="session_here",
        translation_key="station_session_here",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        is_on_fn=lambda data, station_id: data.active is not None
        and data.active.station_id == station_id,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: MerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    for station in coordinator.configured_stations():
        entities.extend(
            MerStationBinarySensor(coordinator, station.id, description)
            for description in STATION_BINARY_SENSORS
        )
        for socket in station.sockets:
            entities.extend(
                MerSocketBinarySensor(coordinator, station.id, socket.id, description)
                for description in SOCKET_BINARY_SENSORS
            )
    entities.extend(MerSiteBinarySensor(coordinator, d) for d in SITE_BINARY_SENSORS)
    entities.extend(MerAccountBinarySensor(coordinator, d) for d in ACCOUNT_BINARY_SENSORS)
    async_add_entities(entities)


class MerSocketBinarySensor(MerSocketEntity, BinarySensorEntity):
    entity_description: MerSocketBinaryDescription

    def __init__(
        self,
        coordinator: MerCoordinator,
        station_id: int,
        socket_id: int,
        description: MerSocketBinaryDescription,
    ) -> None:
        super().__init__(coordinator, station_id, socket_id, description)

    @property
    def is_on(self) -> bool | None:
        socket = self.socket
        return self.entity_description.is_on_fn(socket) if socket else None


class MerSiteBinarySensor(MerSiteEntity, BinarySensorEntity):
    entity_description: MerSiteBinaryDescription

    @property
    def is_on(self) -> bool:
        return self.entity_description.is_on_fn(self.coordinator.configured_stations())


class MerAccountBinarySensor(MerAccountEntity, BinarySensorEntity):
    entity_description: MerAccountBinaryDescription

    @property
    def is_on(self) -> bool:
        return self.entity_description.is_on_fn(self.coordinator.data)


class MerStationBinarySensor(MerStationEntity, BinarySensorEntity):
    """Whether the customer's active session (if any) is on this charger."""

    entity_description: MerStationBinaryDescription

    def __init__(
        self,
        coordinator: MerCoordinator,
        station_id: int,
        description: MerStationBinaryDescription,
    ) -> None:
        super().__init__(coordinator, station_id, description)

    @property
    def is_on(self) -> bool:
        return self.entity_description.is_on_fn(self.coordinator.data, self.station_id)
