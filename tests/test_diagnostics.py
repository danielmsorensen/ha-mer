"""Tests for diagnostics output."""

from __future__ import annotations

from datetime import timedelta
import json
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mer.diagnostics import async_get_config_entry_diagnostics
from custom_components.mer.driivz.models import Socket
from tests.helpers import setup_integration


async def test_diagnostics_redacts_secrets(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    diag = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert diag["entry"]["data"]["username"] == "**REDACTED**"
    assert diag["entry"]["data"]["password"] == "**REDACTED**"
    assert diag["entry"]["title"] == "**REDACTED**"  # the account e-mail
    assert [s["station_ids"] for s in diag["entry"]["sites"]] == [[6042, 6041]]
    assert diag["entry"]["sites"][0]["site_id"] == 2877
    assert diag["rate_limit_remaining"] == 9
    assert diag["data"]["customer_id"] == "**REDACTED**"
    assert diag["data"]["wallet"]["balance"] == 12.5
    assert diag["data"]["wallet"]["account_number"] == "**REDACTED**"
    assert diag["data"]["wallet"]["id"] == "**REDACTED**"
    assert sorted(diag["data"]["stations"]) == ["6041", "6042"]
    # Station/socket ids key a resource many people share, not a per-account handle, and are
    # exactly what a support request needs -- pin that the scoped id-redaction passes above
    # never widen to blank these out too.
    assert diag["data"]["stations"]["6041"]["id"] == 6041
    assert diag["data"]["stations"]["6042"]["sockets"][0]["id"] == 11243
    assert diag["data"]["stations"]["6042"]["sockets"][0]["status"] == "AVAILABLE"
    assert diag["data"]["active"] is None
    assert diag["data"]["last_transaction"]["id"] == "**REDACTED**"
    assert diag["data"]["last_transaction"]["energy_kwh"] == 32.408
    assert diag["data"]["last_transaction"]["station_id"] == 6041
    assert diag["data"]["notify_subscriptions"] == {"6042": True, "6041": False}

    # The whole point of redaction is that the result survives JSON serialisation for
    # download -- proving individual fields are JSON-friendly doesn't establish that the
    # payload as a whole does.
    json.dumps(diag)


async def test_diagnostics_serialises_active_session(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket: Socket,
) -> None:
    """A charge in progress carries `timedelta` fields that must not blow up diagnostics.

    This is exactly the moment someone is most likely to pull diagnostics, so it must not
    be the moment diagnostics crash.
    """
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    diag = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    active = diag["data"]["active"]
    assert active is not None
    assert active["duration"] == timedelta(milliseconds=953825).total_seconds()
    assert active["transaction_id"] == "**REDACTED**"
    assert active["socket_id"] == 11242
    assert active["station_id"] == 6041

    json.dumps(diag)
