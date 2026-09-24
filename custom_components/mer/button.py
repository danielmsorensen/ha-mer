"""Button platform for Mer."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import COMMAND_TIMEOUT_SECONDS, DOMAIN
from .coordinator import CommandUnconfirmed, MerConfigEntry, MerCoordinator
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


async def _run(
    coordinator: MerCoordinator, command: str, station_id: int | None, socket_id: int
) -> None:
    """Send a command and hold the press open until the charger shows the outcome.

    The frontend shows a spinner while this runs, then a tick, or a red cross with the
    message when it raises. A rejection and a timeout both raise; the coordinator has
    already recorded either on the "Last command" sensor and fired the event.
    """
    try:
        if command == "start":
            assert station_id is not None
            await coordinator.async_start_charge(station_id, socket_id)
        else:
            await coordinator.async_stop_charge(station_id, socket_id)
    except DriivzError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="command_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    except CommandUnconfirmed as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="command_unconfirmed",
            translation_placeholders={"seconds": str(COMMAND_TIMEOUT_SECONDS)},
        ) from err


class MerStartChargeButton(MerSocketEntity, ButtonEntity):
    """Start a charge on this socket."""

    def __init__(self, coordinator: MerCoordinator, station_id: int, socket_id: int) -> None:
        super().__init__(coordinator, station_id, socket_id, START_CHARGE)

    @property
    def available(self) -> bool:
        """Greyed out unless the socket is free or plugged in and waiting.

        Home Assistant has no disabled state for a button; unavailable is the closest.
        The UI greys it out, and the button.press service skips unavailable entities
        (with a warning in the log), so automations cannot press it either.
        """
        socket = self.socket
        return super().available and socket is not None and socket.can_start

    async def async_press(self) -> None:
        await _run(self.coordinator, "start", self.station_id, self.socket_id)


class MerAccountStopChargeButton(MerAccountEntity, ButtonEntity):
    """Stop the account's active charge, wherever it is running."""

    def __init__(self, coordinator: MerCoordinator) -> None:
        super().__init__(coordinator, ACCOUNT_STOP_CHARGE)

    @property
    def available(self) -> bool:
        """Greyed out while there is no session of yours to stop."""
        return super().available and self.coordinator.data.active is not None

    async def async_press(self) -> None:
        active = self.coordinator.data.active
        if active is None:
            # Guard against the session ending between the availability check and the press.
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_active_session")
        await _run(self.coordinator, "stop", active.station_id, active.socket_id)


class MerStationStopChargeButton(MerStationEntity, ButtonEntity):
    """Stop the active charge, but only if it is running on this charger."""

    def __init__(self, coordinator: MerCoordinator, station_id: int) -> None:
        super().__init__(coordinator, station_id, STATION_STOP_CHARGE)

    @property
    def available(self) -> bool:
        """Greyed out unless your session is running on this charger."""
        active = self.coordinator.data.active
        return super().available and active is not None and active.station_id == self.station_id

    async def async_press(self) -> None:
        active = self.coordinator.data.active
        if active is None or active.station_id != self.station_id:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="session_not_here")
        await _run(self.coordinator, "stop", self.station_id, active.socket_id)
