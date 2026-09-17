"""Diagnostics support for Mer."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .coordinator import MerConfigEntry

TO_REDACT = {
    CONF_USERNAME,
    CONF_PASSWORD,
    "customer_id",
    "member_id",
    "account_number",
    "unique_id",
    "transaction_id",
}


def _plain(value: Any) -> Any:
    """Convert dataclasses/datetimes/timedeltas into JSON-friendly values."""
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _plain(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MerConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": async_redact_data(
            {
                "title": entry.title,
                "unique_id": entry.unique_id,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            TO_REDACT,
        ),
        "rate_limit_remaining": coordinator.client.rate_limit_remaining,
        "last_update_success": coordinator.last_update_success,
        "data": async_redact_data(
            {
                "customer_id": data.customer_id,
                "stations": _plain(data.stations),
                "details": _plain(data.details),
                "notify_subscriptions": _plain(data.notify_subscriptions),
                "active": _plain(data.active),
                "wallet": _plain(data.wallet),
                "last_transaction": _plain(data.last_transaction),
            },
            TO_REDACT,
        ),
    }
