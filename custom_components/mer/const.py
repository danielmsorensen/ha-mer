"""Constants for the Mer integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "mer"

CONF_BASE_URL = "base_url"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_SEARCH = "search"

# Each charging site with monitored chargers is a config subentry of this type on the
# account entry, so the integration page groups a site's chargers together. Its data
# holds the site id and name and the ids of the chargers monitored there.
SUBENTRY_TYPE_SITE = "site"
CONF_SITE_ID = "site_id"
CONF_SITE_NAME = "site_name"
CONF_STATION_IDS = "station_ids"
# Version 2 entries had one subentry per charger; kept for the migration only.
LEGACY_SUBENTRY_TYPE_CHARGER = "charger"
LEGACY_CONF_STATION_ID = "station_id"

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
# After a start/stop is acknowledged, the press stays open while the charger is
# polled at this cadence until the outcome is visible, or this long has passed.
COMMAND_POLL_INTERVAL_SECONDS = 5
COMMAND_TIMEOUT_SECONDS = 60
COMMAND_MAX_POLLS = COMMAND_TIMEOUT_SECONDS // COMMAND_POLL_INTERVAL_SECONDS
# Stop the follow-up polling early rather than exhaust the portal's headroom.
COMMAND_RATE_LIMIT_FLOOR = 2
# Fired with the outcome of every start/stop command, for automations to notify on.
EVENT_COMMAND_RESULT = "mer_command_result"
# While the push channel is connected, socket status arrives instantly, so the poll
# only has to cover wallet, history and details; it drops to this interval.
PUSH_POLL_INTERVAL_SECONDS = 300
PUSH_RECONNECT_MIN_SECONDS = 5
PUSH_RECONNECT_MAX_SECONDS = 300
RATE_LIMIT_SKIP_THRESHOLD = 1
RATE_LIMIT_WARN_INTERVAL = timedelta(hours=1)
