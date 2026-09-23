"""Switch platform for Mer."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import MerConfigEntry, MerCoordinator
from .driivz.exceptions import DriivzError
from .entity import MerStationEntity

NOTIFY_AVAILABLE = SwitchEntityDescription(
    key="notify_available",
    translation_key="notify_available",
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: MerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    for subentry, station in coordinator.charger_stations():
        async_add_entities(
            [MerNotifyAvailableSwitch(coordinator, station.id)],
            config_subentry_id=subentry.subentry_id,
        )


class MerNotifyAvailableSwitch(MerStationEntity, SwitchEntity):
    """Whether Mer will tell the driver when this charger frees up.

    This mirrors the Mer app's own "notify me when available" toggle, which the app
    exposes but does not reliably deliver on. Home Assistant owns both the toggle and,
    via an automation on the socket availability binary sensor, the actual alert.
    """

    def __init__(self, coordinator: MerCoordinator, station_id: int) -> None:
        super().__init__(coordinator, station_id, NOTIFY_AVAILABLE)

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.notify_subscriptions.get(self.station_id, False)

    async def async_turn_on(self, **kwargs: object) -> None:
        await self._set_subscription(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self._set_subscription(False)

    async def _set_subscription(self, subscribed: bool) -> None:
        try:
            await self.coordinator.async_set_availability_subscription(self.station_id, subscribed)
        except DriivzError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
