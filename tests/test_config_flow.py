"""Tests for the Mer config, options and add-charger flows."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mer.const import (
    CONF_BASE_URL,
    CONF_SCAN_INTERVAL,
    CONF_SEARCH,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    CONF_STATION_ID,
    CONF_STATION_IDS,
    CONF_STATION_NAME,
    DEFAULT_BASE_URL,
    DOMAIN,
    SUBENTRY_TYPE_CHARGER,
)
from custom_components.mer.driivz.exceptions import AuthError, DriivzConnectionError
from tests.conftest import SITE_NAME, make_config_entry
from tests.helpers import setup_integration

CREDS = {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret"}


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def _start_add_charger(hass: HomeAssistant, entry: MockConfigEntry):
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CHARGER),
        context={"source": config_entries.SOURCE_USER},
    )


def _charger_station_ids(entry: MockConfigEntry) -> set[int]:
    return {
        int(s.data[CONF_STATION_ID])
        for s in entry.subentries.values()
        if s.subentry_type == SUBENTRY_TYPE_CHARGER
    }


# ----- initial setup: credentials only -------------------------------------


async def test_user_flow_creates_account_entry(hass: HomeAssistant, mock_client: MagicMock) -> None:
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "user@example.com"
    assert result["data"] == {**CREDS, CONF_BASE_URL: DEFAULT_BASE_URL}
    assert result["options"] == {CONF_SCAN_INTERVAL: 60}
    entry = result["result"]
    assert entry.unique_id == "user@example.com"
    assert entry.subentries == {}
    mock_client.login.assert_awaited()
    # No chargers yet: the entry loads and polls only the account.
    assert entry.state is config_entries.ConfigEntryState.LOADED
    mock_client.find_stations_by_ids.assert_not_awaited()


async def test_invalid_auth_and_cannot_connect(hass: HomeAssistant, mock_client: MagicMock) -> None:
    result = await _start(hass)
    mock_client.login.side_effect = AuthError("BAD_CREDENTIALS")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client.login.side_effect = DriivzConnectionError("boom")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_duplicate_account_aborts(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_password(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    mock_client.login.side_effect = AuthError("BAD_CREDENTIALS")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client.login.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-secret"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_PASSWORD] == "new-secret"


# ----- options: poll interval only -----------------------------------------


async def test_options_interval(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 120}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert mock_config_entry.options[CONF_SCAN_INTERVAL] == 120
    assert _charger_station_ids(mock_config_entry) == {6042, 6041}  # untouched


# ----- add charger subentry flow -------------------------------------------


async def test_add_chargers_creates_one_subentry_each(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    entry = make_config_entry([])
    await setup_integration(hass, entry)

    result = await _start_add_charger(hass, entry)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_SEARCH: "netpark"}
    )
    assert result["step_id"] == "site_select"
    options = result["data_schema"].schema[CONF_SITE_ID].config["options"]
    assert [o["value"] for o in options] == ["2877", "3796"]
    assert "8 sockets" in options[0]["label"]

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_SITE_ID: "2877"}
    )
    assert result["step_id"] == "stations"
    station_options = result["data_schema"].schema[CONF_STATION_IDS].config["options"]
    # 17886 belongs to site 3796 and must be filtered out
    assert sorted(o["value"] for o in station_options) == ["6041", "6042"]
    assert result["description_placeholders"] == {"site": SITE_NAME}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_STATION_IDS: ["6042", "6041"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Business Durham - NETPark 4 - Explorer 2 ({SITE_NAME})"
    assert result["data"] == {
        CONF_STATION_ID: 6041,
        CONF_STATION_NAME: "Business Durham - NETPark 4 - Explorer 2",
        CONF_SITE_ID: 2877,
        CONF_SITE_NAME: SITE_NAME,
    }
    assert result["unique_id"] == "station_6041"
    await hass.async_block_till_done()

    assert _charger_station_ids(entry) == {6042, 6041}
    assert {s.unique_id for s in entry.subentries.values()} == {"station_6042", "station_6041"}
    # The entry reloaded with the new chargers: their devices exist, under their subentries.
    registry = dr.async_get(hass)
    for station_id in (6042, 6041):
        device = registry.async_get_device_by_identifier(
            (DOMAIN, f"station_{station_id}"), entry.entry_id
        )
        assert device is not None
        subentry_ids = device.config_entries_subentries[entry.entry_id]
        assert len(subentry_ids) == 1
        (subentry_id,) = subentry_ids
        assert entry.subentries[subentry_id].data[CONF_STATION_ID] == station_id
    assert entry.runtime_data.station_ids == [6042, 6041]


async def test_add_charger_hides_already_added(hass: HomeAssistant, mock_client: MagicMock) -> None:
    entry = make_config_entry([6042])
    await setup_integration(hass, entry)
    result = await _start_add_charger(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_SEARCH: "netpark"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_SITE_ID: "2877"}
    )
    assert result["step_id"] == "stations"
    station_options = result["data_schema"].schema[CONF_STATION_IDS].config["options"]
    assert [o["value"] for o in station_options] == ["6041"]

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_STATION_IDS: []}
    )
    assert result["step_id"] == "stations"
    assert result["errors"] == {"base": "no_stations_selected"}


async def test_add_charger_aborts_when_all_added(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    result = await _start_add_charger(hass, mock_config_entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_SEARCH: "netpark"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_SITE_ID: "2877"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "all_stations_configured"


async def test_add_charger_no_site_match_and_login_failures(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    result = await _start_add_charger(hass, mock_config_entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_SEARCH: "nowhere"}
    )
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_sites"}

    mock_client.login.side_effect = AuthError("BAD_CREDENTIALS")
    result = await _start_add_charger(hass, mock_config_entry)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_auth"

    mock_client.login.side_effect = DriivzConnectionError("boom")
    result = await _start_add_charger(hass, mock_config_entry)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_remove_charger_subentry_removes_device_and_reloads(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    registry = dr.async_get(hass)
    entry_id = mock_config_entry.entry_id
    assert registry.async_get_device_by_identifier((DOMAIN, "station_6041"), entry_id)
    subentry = next(
        s for s in mock_config_entry.subentries.values() if s.data[CONF_STATION_ID] == 6041
    )

    hass.config_entries.async_remove_subentry(mock_config_entry, subentry.subentry_id)
    await hass.async_block_till_done()

    assert registry.async_get_device_by_identifier((DOMAIN, "station_6041"), entry_id) is None
    assert registry.async_get_device_by_identifier((DOMAIN, "station_6042"), entry_id)
    assert _charger_station_ids(mock_config_entry) == {6042}
    assert mock_config_entry.runtime_data.station_ids == [6042]
    assert hass.states.get("sensor.business_durham_netpark_4_explorer_2_status") is None
