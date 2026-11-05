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
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MerConfigEntry, MerCoordinator, MerData
from .entity import MerAccountEntity, MerStationEntity, session_view, socket_label


@dataclass(frozen=True, kw_only=True)
class MerAccountBinaryDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[MerData], bool]
    attributes_fn: Callable[[MerData], dict[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class MerStationBinaryDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[MerData, int], bool]
    attributes_fn: Callable[[MerData, int], dict[str, Any]] | None = None


def _charging_attributes(data: MerData) -> dict[str, Any]:
    """Where your session is running; once it ends, where it ran and when it ended.

    `socket_reused_at` says when someone else started using the socket you left after
    you unplugged, so an automation can leave it out of offers until it frees after that. The end is
    the moment the integration saw it; after a restart, the charge history's last
    session stands in (without its socket, which the history does not record).
    """
    if data.active is not None:
        return {
            **session_view(data, data.active),
            "ended_at": None,
            "socket_reused_at": None,
        }
    ended = data.last_session_end
    if ended is not None:
        return {
            **session_view(data, ended.session),
            "ended_at": ended.ended_at.isoformat(),
            "socket_reused_at": ended.reused_at.isoformat() if ended.reused_at else None,
        }
    last = data.last_transaction
    return {
        "charger": last.display_name if last else None,
        "socket": None,
        "station_id": last.station_id if last else None,
        "socket_id": None,
        "ended_at": last.stopped_at.isoformat() if last and last.stopped_at else None,
        "socket_reused_at": None,
    }


def _session_here_attributes(data: MerData, station_id: int) -> dict[str, Any]:
    """Which of this charger's sockets your session is on."""
    active = data.session_on_station(station_id)
    if active is None:
        return {"socket": None}
    socket = data.socket(station_id, active.socket_id)
    return {"socket": socket_label(socket) if socket else active.socket_name}


ACCOUNT_BINARY_SENSORS: tuple[MerAccountBinaryDescription, ...] = (
    MerAccountBinaryDescription(
        key="charging",
        translation_key="account_charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        is_on_fn=lambda data: data.active is not None,
        attributes_fn=_charging_attributes,
    ),
    # Whether the portal's push channel is connected. It carries charger and socket
    # status and session estimates the moment they change; everything else is polled.
    # Off means status also arrives by the normal poll only.
    MerAccountBinaryDescription(
        key="live_status",
        translation_key="account_live_status",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda data: data.push_connected,
        attributes_fn=lambda data: {
            "connected_since": (
                data.push_connected_since.isoformat() if data.push_connected_since else None
            ),
        },
    ),
)

STATION_BINARY_SENSORS: tuple[MerStationBinaryDescription, ...] = (
    MerStationBinaryDescription(
        key="session_here",
        translation_key="station_session_here",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        is_on_fn=lambda data, station_id: data.session_on_station(station_id) is not None,
        attributes_fn=_session_here_attributes,
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
        async_add_entities(entities, config_subentry_id=subentry.subentry_id)
    async_add_entities(MerAccountBinarySensor(coordinator, d) for d in ACCOUNT_BINARY_SENSORS)


class MerAccountBinarySensor(MerAccountEntity, BinarySensorEntity):
    entity_description: MerAccountBinaryDescription

    @property
    def is_on(self) -> bool:
        return self.entity_description.is_on_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator.data)


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
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator.data, self.station_id)
