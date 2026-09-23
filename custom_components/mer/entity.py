"""Base entities and device helpers for Mer."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MerCoordinator
from .driivz.const import STATUS_UNKNOWN, STATUSES
from .driivz.models import Socket, Station

STATUS_OPTIONS: list[str] = [status.lower() for status in STATUSES]


def status_option(status: str | None) -> str:
    """Map a portal status to an enum option, falling back to unknown."""
    value = (status or STATUS_UNKNOWN).lower()
    return value if value in STATUS_OPTIONS else STATUS_UNKNOWN.lower()


def socket_label(socket: Socket) -> str:
    """Human label for a socket used in entity names."""
    if socket.name:
        return socket.name
    if socket.identity_key:
        return f"Socket {socket.identity_key}"
    return f"Socket {socket.id}"


class MerEntity(CoordinatorEntity[MerCoordinator]):
    """Common base: entity names are composed from device name + translated name."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MerCoordinator, description: EntityDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description

    @property
    def entry_id(self) -> str:
        return self.coordinator.config_entry.entry_id

    def account_identifier(self) -> tuple[str, str]:
        return (DOMAIN, f"account_{self.entry_id}")


class MerAccountEntity(MerEntity):
    """Entity attached to the account device."""

    def __init__(self, coordinator: MerCoordinator, description: EntityDescription) -> None:
        super().__init__(coordinator, description)
        self._attr_unique_id = f"{self.entry_id}_account_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={self.account_identifier()},
            name="Mer account",
            manufacturer="Mer",
            model="Driver account",
        )


class MerStationEntity(MerEntity):
    """Entity attached to a charger device, which hangs off the account device."""

    def __init__(
        self, coordinator: MerCoordinator, station_id: int, description: EntityDescription
    ) -> None:
        super().__init__(coordinator, description)
        self.station_id = station_id
        self._attr_unique_id = f"{self.entry_id}_station_{station_id}_{description.key}"
        station = coordinator.get_station(station_id)
        account_device = dr.async_get(coordinator.hass).async_get_device_by_identifier(
            self.account_identifier(), self.entry_id
        )
        base_url = coordinator.config_entry.data.get("base_url", "")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"station_{station_id}")},
            name=station.display_name if station else f"Charger {station_id}",
            manufacturer="Mer",
            model=station.model_name if station else None,
            serial_number=station.identity_key if station else None,
            via_device_id=account_device.id if account_device else None,
            configuration_url=f"{base_url}/findCharger" if base_url else None,
        )

    @property
    def station(self) -> Station | None:
        return self.coordinator.get_station(self.station_id)

    @property
    def available(self) -> bool:
        return super().available and self.station is not None


class MerSocketEntity(MerStationEntity):
    """Entity attached to a charger device but describing one socket."""

    def __init__(
        self,
        coordinator: MerCoordinator,
        station_id: int,
        socket_id: int,
        description: EntityDescription,
    ) -> None:
        super().__init__(coordinator, station_id, description)
        self.socket_id = socket_id
        self._attr_unique_id = f"{self.entry_id}_socket_{socket_id}_{description.key}"
        socket = coordinator.get_socket(station_id, socket_id)
        label = socket_label(socket) if socket else f"Socket {socket_id}"
        self._attr_translation_placeholders = {"socket": label}

    @property
    def socket(self) -> Socket | None:
        return self.coordinator.get_socket(self.station_id, self.socket_id)

    @property
    def available(self) -> bool:
        return super().available and self.socket is not None
