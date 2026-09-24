"""Binary sensor platform for Mer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import MerConfigEntry, MerCoordinator, MerData
from .driivz.models import Socket
from .entity import MerAccountEntity, MerSocketEntity, MerStationEntity, socket_label


@dataclass(frozen=True, kw_only=True)
class MerSocketBinaryDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[Socket], bool]


@dataclass(frozen=True, kw_only=True)
class MerAccountBinaryDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[MerCoordinator, MerData], bool]
    attributes_fn: Callable[[MerCoordinator, MerData], dict[str, Any]] | None = None


def _available_sockets(coordinator: MerCoordinator, _data: MerData) -> dict[str, Any]:
    """List every free socket with the entity id of its start-charge button.

    This is what lets an automation do more than "something is free": it can name
    the free sockets in a notification, or press the first one's button.
    """
    registry = er.async_get(coordinator.hass)
    entry_id = coordinator.config_entry.entry_id
    free: list[dict[str, Any]] = []
    for station in coordinator.configured_stations():
        for socket in station.sockets:
            if not socket.is_available:
                continue
            free.append(
                {
                    "charger": station.display_name,
                    "socket": socket_label(socket),
                    "station_id": station.id,
                    "socket_id": socket.id,
                    "start_button": registry.async_get_entity_id(
                        "button", DOMAIN, f"{entry_id}_socket_{socket.id}_start_charge"
                    ),
                }
            )
    return {"available_sockets": free}


@dataclass(frozen=True, kw_only=True)
class MerStationBinaryDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[MerData, int], bool]


SOCKET_BINARY_SENSORS: tuple[MerSocketBinaryDescription, ...] = (
    MerSocketBinaryDescription(
        key="available",
        translation_key="socket_available",
        is_on_fn=lambda socket: socket.is_available,
    ),
)

ACCOUNT_BINARY_SENSORS: tuple[MerAccountBinaryDescription, ...] = (
    # Aggregate over every charger you added: the single "is anything free" signal.
    MerAccountBinaryDescription(
        key="any_available",
        translation_key="account_any_available",
        is_on_fn=lambda coordinator, _d: any(
            socket.is_available
            for station in coordinator.configured_stations()
            for socket in station.sockets
        ),
        attributes_fn=_available_sockets,
    ),
    MerAccountBinaryDescription(
        key="charging",
        translation_key="account_charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        is_on_fn=lambda _c, data: data.active is not None,
    ),
    # Whether the portal's push channel is connected. It carries charger and socket
    # status and session estimates the moment they change; everything else is polled.
    # Off means status also arrives by the normal poll only.
    MerAccountBinaryDescription(
        key="live_status",
        translation_key="account_live_status",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda coordinator, _d: coordinator.push_connected,
        attributes_fn=lambda coordinator, _d: {
            "connected_since": (
                coordinator.push_connected_since.isoformat()
                if coordinator.push_connected_since
                else None
            ),
        },
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
    for subentry, station in coordinator.charger_stations():
        entities: list[BinarySensorEntity] = []
        entities.extend(
            MerStationBinarySensor(coordinator, station.id, description)
            for description in STATION_BINARY_SENSORS
        )
        for socket in station.sockets:
            entities.extend(
                MerSocketBinarySensor(coordinator, station.id, socket.id, description)
                for description in SOCKET_BINARY_SENSORS
            )
        async_add_entities(entities, config_subentry_id=subentry.subentry_id)
    async_add_entities(MerAccountBinarySensor(coordinator, d) for d in ACCOUNT_BINARY_SENSORS)


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

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Name the charger and socket, and point at this socket's start button.

        Lets an automation or blueprint work from the availability sensor alone: it
        can say "Explorer 2 Left is free" and press the right button without knowing
        anything about how entities are named.
        """
        station, socket = self.station, self.socket
        if station is None or socket is None:
            return None
        return {
            "charger": station.display_name,
            "socket": socket_label(socket),
            "start_button": er.async_get(self.hass).async_get_entity_id(
                "button", DOMAIN, f"{self.entry_id}_socket_{self.socket_id}_start_charge"
            ),
        }


class MerAccountBinarySensor(MerAccountEntity, BinarySensorEntity):
    entity_description: MerAccountBinaryDescription

    @property
    def is_on(self) -> bool:
        return self.entity_description.is_on_fn(self.coordinator, self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator, self.coordinator.data)


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

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Which of this charger's sockets your session is on."""
        active = self.coordinator.data.active
        if active is None or active.station_id != self.station_id:
            return {"socket": None}
        socket = self.coordinator.get_socket(self.station_id, active.socket_id)
        return {"socket": socket_label(socket) if socket else active.socket_name}
