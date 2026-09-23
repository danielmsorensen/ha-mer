"""Button platform for Mer."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import MerConfigEntry, MerCoordinator
from .driivz.exceptions import DriivzError
from .entity import MerAccountEntity, MerSocketEntity, MerStationEntity

START_CHARGE = ButtonEntityDescription(key="start_charge", translation_key="socket_start_charge")
ACCOUNT_STOP_CHARGE = ButtonEntityDescription(
    key="stop_charge", translation_key="account_stop_charge"
)
STATION_STOP_CHARGE = ButtonEntityDescription(
    key="stop_charge", translation_key="station_stop_charge"
)


async def async_setup_entry(
    hass: HomeAssistant, entry: MerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    for subentry, station in coordinator.charger_stations():
        entities: list[ButtonEntity] = [
            MerStartChargeButton(coordinator, station.id, socket.id) for socket in station.sockets
        ]
        entities.append(MerStationStopChargeButton(coordinator, station.id))
        async_add_entities(entities, config_subentry_id=subentry.subentry_id)
    async_add_entities([MerAccountStopChargeButton(coordinator)])


def _raise_command_failed(err: DriivzError) -> None:
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="command_failed",
        translation_placeholders={"error": str(err)},
    ) from err


async def _stop_active_session(coordinator: MerCoordinator, socket_id: int) -> None:
    """Stop the socket carrying the active session, then schedule a refresh."""
    try:
        await coordinator.client.stop_charge(socket_id)
    except DriivzError as err:
        _raise_command_failed(err)
    coordinator.schedule_refresh()


class MerStartChargeButton(MerSocketEntity, ButtonEntity):
    """Start a charge on this socket."""

    def __init__(self, coordinator: MerCoordinator, station_id: int, socket_id: int) -> None:
        super().__init__(coordinator, station_id, socket_id, START_CHARGE)

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.start_charge(self.socket_id)
        except DriivzError as err:
            _raise_command_failed(err)
        self.coordinator.schedule_refresh()


class MerAccountStopChargeButton(MerAccountEntity, ButtonEntity):
    """Stop the account's active charge, wherever it is running."""

    def __init__(self, coordinator: MerCoordinator) -> None:
        super().__init__(coordinator, ACCOUNT_STOP_CHARGE)

    async def async_press(self) -> None:
        active = self.coordinator.data.active
        if active is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_active_session")
        await _stop_active_session(self.coordinator, active.socket_id)


class MerStationStopChargeButton(MerStationEntity, ButtonEntity):
    """Stop the active charge, but only if it is running on this charger."""

    def __init__(self, coordinator: MerCoordinator, station_id: int) -> None:
        super().__init__(coordinator, station_id, STATION_STOP_CHARGE)

    async def async_press(self) -> None:
        active = self.coordinator.data.active
        if active is None or active.station_id != self.station_id:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="session_not_here")
        await _stop_active_session(self.coordinator, active.socket_id)
