"""Tests for the Mer config and options flows."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mer.const import (
    CONF_BASE_URL,
    CONF_SCAN_INTERVAL,
    CONF_SEARCH,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    CONF_STATION_IDS,
    DEFAULT_BASE_URL,
    DOMAIN,
)
from custom_components.mer.driivz.exceptions import AuthError, DriivzConnectionError
from tests.helpers import setup_integration

CREDS = {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret"}


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_full_flow_creates_entry(hass: HomeAssistant, mock_client: MagicMock) -> None:
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "site"
    mock_client.login.assert_awaited_once()

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SEARCH: "netpark"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "site_select"
    options = result["data_schema"].schema[CONF_SITE_ID].config["options"]
    assert [o["value"] for o in options] == ["2877", "3796"]
    assert "8 sockets" in options[0]["label"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SITE_ID: "2877"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "stations"
    station_options = result["data_schema"].schema[CONF_STATION_IDS].config["options"]
    # 17886 belongs to site 3796 and must be filtered out
    assert sorted(o["value"] for o in station_options) == ["6041", "6042"]
    assert {o["label"] for o in station_options} == {
        "Business Durham - NETPark 3 - Explorer 1",
        "Business Durham - NETPark 4 - Explorer 2",
    }

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_STATION_IDS: ["6042", "6041"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Mer - Durham County Council - Business Durham NETPark"
    assert result["data"] == {**CREDS, CONF_BASE_URL: DEFAULT_BASE_URL}
    assert result["options"] == {
        CONF_SITE_ID: 2877,
        CONF_SITE_NAME: "Durham County Council - Business Durham NETPark",
        CONF_STATION_IDS: [6042, 6041],
        CONF_SCAN_INTERVAL: 60,
    }
    assert result["result"].unique_id == "user@example.com"


async def test_invalid_auth_and_cannot_connect(hass: HomeAssistant, mock_client: MagicMock) -> None:
    result = await _start(hass)
    mock_client.login.side_effect = AuthError("BAD_CREDENTIALS")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client.login.side_effect = DriivzConnectionError("boom")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["errors"] == {"base": "cannot_connect"}

    mock_client.login.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["step_id"] == "site"


async def test_no_site_match_shows_error(hass: HomeAssistant, mock_client: MagicMock) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SEARCH: "nowhere"}
    )
    assert result["step_id"] == "site"
    assert result["errors"] == {"base": "no_sites"}


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


async def test_options_interval(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "interval"}
    )
    assert result["step_id"] == "interval"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 120}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert mock_config_entry.options[CONF_SCAN_INTERVAL] == 120
    assert mock_config_entry.options[CONF_STATION_IDS] == [6042, 6041]  # preserved


async def test_options_change_stations(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "site"}
    )
    assert result["step_id"] == "site"
    assert (
        result["data_schema"]({})[CONF_SEARCH] == "Durham County Council - Business Durham NETPark"
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SEARCH: "Business Durham NETPark"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SITE_ID: "2877"}
    )
    assert result["step_id"] == "stations"
    assert result["data_schema"]({})[CONF_STATION_IDS] == ["6042", "6041"]  # current preselected
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_STATION_IDS: ["6042"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert mock_config_entry.options[CONF_STATION_IDS] == [6042]
    assert mock_config_entry.options[CONF_SCAN_INTERVAL] == 60  # preserved
