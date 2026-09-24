"""The Mer EV Charging integration."""

from __future__ import annotations

import logging
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .api import create_client
from .const import (
    CONF_BASE_URL,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    CONF_STATION_IDS,
    DEFAULT_BASE_URL,
    DOMAIN,
    LEGACY_CONF_STATION_ID,
    LEGACY_SUBENTRY_TYPE_CHARGER,
    PLATFORMS,
    SUBENTRY_TYPE_SITE,
)
from .coordinator import MerConfigEntry, MerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: MerConfigEntry) -> bool:
    """Set up Mer from a config entry."""
    # Registered before any awaiting, deliberately. The listener fires on options
    # changes and on every site subentry added, changed or removed, which is what
    # brings a newly added charger's entities up. If a change lands while this setup
    # is already past reading the subentries, registering first means it queues
    # another reload, which waits for this setup to finish and starts over.
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
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
    _async_remove_unmonitored_chargers(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Cancelled automatically when the entry unloads.
    entry.async_create_background_task(hass, coordinator.async_run_push(), "mer push channel")
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


def _async_remove_unmonitored_chargers(
    hass: HomeAssistant, entry: MerConfigEntry, coordinator: MerCoordinator
) -> None:
    """Delete devices of chargers unticked in a site's "Change chargers".

    Deleting a whole site subentry has HA remove its devices itself; unticking one
    charger leaves the subentry in place, so its device is removed here.
    """
    registry = dr.async_get(hass)
    monitored = {f"station_{sid}" for sid in coordinator.station_ids}
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        for domain, identifier in device.identifiers:
            if (
                domain == DOMAIN
                and identifier.startswith("station_")
                and identifier not in monitored
            ):
                registry.async_remove_device(device.id)
                break


async def _async_entry_updated(hass: HomeAssistant, entry: MerConfigEntry) -> None:
    """Reload when options or the site subentries change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MerConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: MerConfigEntry) -> bool:
    """Migrate older entries.

    Version 2 had one subentry per charger; version 3 has one per site, listing its
    chargers, so the integration page groups them. Chargers keep their device
    identifiers, so entity ids and history survive; the devices are recreated under
    the new site subentries on the first setup.
    """
    if entry.version > 3 or entry.version < 2:
        # Version 1 (site and chargers in options) never existed outside development.
        return False
    if entry.version == 2:
        legacy = [
            s for s in entry.subentries.values() if s.subentry_type == LEGACY_SUBENTRY_TYPE_CHARGER
        ]
        sites: dict[int, dict[str, Any]] = {}
        for subentry in legacy:
            site_id = int(subentry.data[CONF_SITE_ID])
            site = sites.setdefault(
                site_id,
                {
                    CONF_SITE_ID: site_id,
                    CONF_SITE_NAME: subentry.data.get(CONF_SITE_NAME) or f"Site {site_id}",
                    CONF_STATION_IDS: [],
                },
            )
            site[CONF_STATION_IDS].append(int(subentry.data[LEGACY_CONF_STATION_ID]))
        for subentry in legacy:
            hass.config_entries.async_remove_subentry(entry, subentry.subentry_id)
        for site in sites.values():
            hass.config_entries.async_add_subentry(
                entry,
                ConfigSubentry(
                    data=MappingProxyType(site),
                    subentry_type=SUBENTRY_TYPE_SITE,
                    title=site[CONF_SITE_NAME],
                    unique_id=f"site_{site[CONF_SITE_ID]}",
                ),
            )
        hass.config_entries.async_update_entry(entry, version=3)
        _LOGGER.info("Migrated Mer entry to one subentry per site (%d sites)", len(sites))
    return True
