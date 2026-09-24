"""Tests for integration setup and unload."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mer.const import (
    CONF_STATION_IDS,
    DOMAIN,
    SUBENTRY_TYPE_SITE,
)
from custom_components.mer.driivz.exceptions import AuthError, DriivzConnectionError
from tests.conftest import SITE_ID, SITE_NAME, make_config_entry
from tests.helpers import setup_integration
from tests.test_sensor import state_by_unique_id


async def test_setup_and_unload(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    mock_client.login.assert_awaited_once()
    mock_client.find_stations_by_ids.assert_awaited_with([6042, 6041])
    coordinator = mock_config_entry.runtime_data
    assert set(coordinator.data.stations) == {6042, 6041}
    assert coordinator.data.details[6042].sockets[0].name == "Left"
    assert coordinator.data.customer_id == 123456

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_without_chargers_polls_account_only(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    entry = make_config_entry([])
    await setup_integration(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    mock_client.find_stations_by_ids.assert_not_awaited()
    mock_client.find_station_by_id.assert_not_awaited()
    eid = entry.entry_id
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_wallet_balance").state == "12.5"
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_available_sockets").state == "0"
    registry = dr.async_get(hass)
    assert registry.async_get_device_by_identifier((DOMAIN, f"account_{eid}"), eid) is not None
    assert len(dr.async_entries_for_config_entry(registry, eid)) == 1


async def test_setup_auth_error_triggers_reauth(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    mock_client.login.side_effect = AuthError("BAD_CREDENTIALS")
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"].get("source") == "reauth" for f in flows)


async def test_setup_connection_error_retries(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    mock_client.login.side_effect = DriivzConnectionError("boom")
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_devices_hang_off_the_account(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    registry = dr.async_get(hass)
    entry_id = mock_config_entry.entry_id
    station = registry.async_get_device_by_identifier((DOMAIN, "station_6042"), entry_id)
    account = registry.async_get_device_by_identifier((DOMAIN, f"account_{entry_id}"), entry_id)
    assert account is not None
    assert station is not None
    assert station.name == "Business Durham - NETPark 3 - Explorer 1"
    assert station.model == "Eve Double Pro-line"
    assert station.via_device_id == account.id
    # No site device any more: account plus one device per charger.
    assert len(dr.async_entries_for_config_entry(registry, entry_id)) == 3
    assert registry.async_get_device_by_identifier((DOMAIN, "site_2877"), entry_id) is None


async def test_subentry_added_during_setup_is_picked_up(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """A subentry change that lands while setup is running must still be picked up.

    The update listener is registered before setup awaits anything, so the late
    change queues one more reload instead of being lost. This was the bug seen live
    where a newly added charger had a card but no device.
    """
    entry = make_config_entry([6042])
    entry.add_to_hass(hass)
    setup_started = asyncio.Event()
    release = asyncio.Event()

    async def slow_wallet():
        setup_started.set()
        await release.wait()
        return mock_client.find_wallet.return_value

    mock_client.find_wallet.side_effect = slow_wallet

    setup_task = hass.async_create_task(hass.config_entries.async_setup(entry.entry_id))
    await setup_started.wait()
    # Setup is mid-way through its first refresh, having already read the subentries.
    (site,) = entry.subentries.values()
    hass.config_entries.async_update_subentry(
        entry, site, data={**site.data, CONF_STATION_IDS: [6042, 6041]}
    )
    release.set()
    await setup_task
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.station_ids == [6042, 6041]
    registry = dr.async_get(hass)
    for station_id in (6042, 6041):
        assert registry.async_get_device_by_identifier(
            (DOMAIN, f"station_{station_id}"), entry.entry_id
        ), f"station {station_id} has no device"


async def test_migrates_per_charger_subentries_to_one_per_site(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """Version 2 had a subentry per charger; they merge into one for their site."""
    legacy = [
        ConfigSubentryData(
            data={
                "station_id": station_id,
                "station_name": name,
                "site_id": SITE_ID,
                "site_name": SITE_NAME,
            },
            subentry_type="charger",
            title=f"{name} ({SITE_NAME})",
            unique_id=f"station_{station_id}",
        )
        for station_id, name in ((6042, "Explorer 1"), (6041, "Explorer 2"))
    ]
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="user@example.com",
        unique_id="user@example.com",
        data=make_config_entry([]).data,
        options={"scan_interval": 60},
        subentries_data=legacy,
    )
    await setup_integration(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 3
    (site,) = entry.subentries.values()
    assert site.subentry_type == SUBENTRY_TYPE_SITE
    assert site.title == SITE_NAME
    assert site.unique_id == f"site_{SITE_ID}"
    assert site.data[CONF_STATION_IDS] == [6042, 6041]
    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier((DOMAIN, "station_6041"), entry.entry_id)
    assert device is not None
    assert device.config_entries_subentries[entry.entry_id] == {site.subentry_id}
