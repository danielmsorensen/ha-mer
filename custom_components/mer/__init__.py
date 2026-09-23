"""The Mer EV Charging integration."""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .api import create_client
from .const import CONF_BASE_URL, DEFAULT_BASE_URL, DOMAIN, PLATFORMS
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
    _async_register_account_device(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Fires on options changes and on every subentry added or removed: chargers are
    # subentries, so this is what brings a newly added charger's entities up.
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


def _async_register_account_device(hass: HomeAssistant, entry: MerConfigEntry) -> None:
    """Create the account device ahead of the entities that attach to it.

    Charger devices link to it via `via_device_id`, which must already resolve in
    the registry when the platforms compose their device info.
    """
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"account_{entry.entry_id}")},
        name="Mer account",
        manufacturer="Mer",
        model="Driver account",
    )


async def _async_entry_updated(hass: HomeAssistant, entry: MerConfigEntry) -> None:
    """Reload when options or the set of charger subentries change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MerConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
