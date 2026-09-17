"""Constants for the Mer integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "mer"

CONF_BASE_URL = "base_url"
CONF_SITE_ID = "site_id"
CONF_SITE_NAME = "site_name"
CONF_STATION_IDS = "station_ids"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_SEARCH = "search"

DEFAULT_BASE_URL = "https://driver.uk.mer.eco"
DEFAULT_SCAN_INTERVAL = 60
MIN_SCAN_INTERVAL = 30
MAX_SCAN_INTERVAL = 600

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.SWITCH,
]

WALLET_REFRESH = timedelta(minutes=15)
DETAIL_REFRESH = timedelta(hours=1)
HISTORY_LOOKBACK = timedelta(days=30)
REFRESH_AFTER_COMMAND_SECONDS = 5
RATE_LIMIT_SKIP_THRESHOLD = 1
RATE_LIMIT_WARN_INTERVAL = timedelta(hours=1)
