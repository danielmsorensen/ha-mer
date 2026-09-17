"""The Mer EV Charging integration."""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .api import create_client
from .const import CONF_BASE_URL, CONF_STATION_IDS, DEFAULT_BASE_URL, DOMAIN, PLATFORMS
from .coordinator import MerConfigEntry, MerCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: MerConfigEntry) -> bool:
    """Set up Mer from a config entry."""
    client = create_client(
        hass,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
    )
    coordinator = MerCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: MerConfigEntry) -> None:
    """Reload when options (interval, chargers) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MerConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: MerConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow removing station devices that are no longer configured."""
    configured = {f"station_{sid}" for sid in entry.options.get(CONF_STATION_IDS, [])}
    for domain, identifier in device_entry.identifiers:
        if domain != DOMAIN:
            continue
        if identifier.startswith("station_") and identifier not in configured:
            return True
    return False
