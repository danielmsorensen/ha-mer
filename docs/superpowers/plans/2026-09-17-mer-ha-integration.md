# Mer (Driivz) Home Assistant Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A HACS custom integration `mer` that logs in to the Mer UK Driivz driver portal, exposes per-socket availability for chosen chargers, starts and stops charges, and shows live/last session and wallet data.

**Architecture:** An HA-agnostic client package `custom_components/mer/driivz/` (aiohttp, dataclass models, typed exceptions) is wrapped by one `DataUpdateCoordinator` per config entry. A three-step config flow (credentials → site search → charger multi-select) stores credentials in entry data and site/chargers/interval in options. Entities are thin readers of coordinator data; buttons call the client then schedule a refresh.

**Tech Stack:** Python 3.14, Home Assistant 2026.9.2, aiohttp 3.14, pytest + pytest-homeassistant-custom-component 0.13.365 + aioresponses 0.7.9, ruff, GitHub Actions (hassfest, HACS action).

**Spec:** `docs/superpowers/specs/2026-09-17-mer-ha-integration-design.md`

## Global Constraints

- Domain is `mer`; integration folder `custom_components/mer/`; `manifest.json` has `"iot_class": "cloud_polling"`, `"requirements": []`, `"config_flow": true`.
- Default base URL `https://driver.uk.mer.eco`; every base-URL usage goes through `CONF_BASE_URL` / `DEFAULT_BASE_URL`.
- Portal headers on every API call: `X-CSRF-TOKEN`, `X-APP-TYPE: WEB`, `X-JSON-TYPES: None`, `X-Ajax-call: true`. Login body is form-encoded: `username`, `password`, `_spring_security_remember_me=true`, `_csrf`.
- JSON-body endpoints: `findSitesInBounds`, `findStationsInBounds`, `findStationsByIds`, `findDriverChargeTransactionLogByView`. Form-body: everything else. `findStationById` is GET with query params.
- Nothing under `custom_components/mer/driivz/` may import `homeassistant`.
- Polling default 60 s, options range 30–600. Wallet + history every 15 min. Station details every 60 min. Skip one cycle when `X-Rate-Limit-Remaining` ≤ 1.
- Never run a real `start_charge`/`stop_charge` against the live portal during development without explicit user confirmation.
- Home Assistant core requires POSIX and cannot be imported on native Windows Python (`fcntl`).
  All local tests and linting therefore run inside WSL2 Ubuntu 26.04 (Python 3.14.4) through the
  repo wrappers `scripts/test`, `scripts/lint` and `scripts/format`, invoked from the repo root
  `D:\GitHub\Personal\ha-mer` in Git Bash. The WSL virtualenv lives at `~/.venvs/ha-mer`
  (outside the NTFS mount); `scripts/bootstrap-dev` documents how it is created without sudo.
  Never add a Windows `fcntl` shim, and never run `pytest` directly on Windows Python.
- Commit after every task with a conventional-commit message ending in `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Test data in `tests/fixtures/` is sanitised: customer id `123456`, member id `123123`, account number `1123456`, wallet id `228000`, no emails/addresses.

---

## File structure

| Path | Responsibility |
|---|---|
| `custom_components/mer/driivz/const.py` | Endpoint paths, header names, status enums, auth error types |
| `custom_components/mer/driivz/exceptions.py` | `DriivzError`, `DriivzConnectionError`, `AuthError`, `RateLimitError`, `ApiError` |
| `custom_components/mer/driivz/models.py` | `Bounds`, `Site`, `SocketPrice`, `Socket`, `Station`, `Wallet`, `Transaction`, `SessionEstimate`, `clean_caption`, `ms_to_datetime`, `parse_start_time` |
| `custom_components/mer/driivz/client.py` | `DriivzDriverClient` (csrf, login, request envelope, retry, typed methods) |
| `custom_components/mer/const.py` | `DOMAIN`, CONF keys, defaults, platforms, timing constants |
| `custom_components/mer/api.py` | `create_client(hass, username, password, base_url)` (single patch point for tests) |
| `custom_components/mer/coordinator.py` | `ActiveSession`, `MerData`, `MerCoordinator`, `MerConfigEntry` |
| `custom_components/mer/__init__.py` | setup/unload, reload-on-options, `async_remove_config_entry_device` |
| `custom_components/mer/config_flow.py` | user → site → site_select → stations; reauth; options (interval, site/stations) |
| `custom_components/mer/entity.py` | `MerEntity`, `MerStationEntity`, `MerSocketEntity`, `MerSiteEntity`, `MerAccountEntity`, device-info helpers |
| `custom_components/mer/sensor.py` | Station, socket, site and account sensors |
| `custom_components/mer/binary_sensor.py` | Socket available, site any-available, account charging |
| `custom_components/mer/button.py` | Socket start-charge, account stop-charge |
| `custom_components/mer/diagnostics.py` | Redacted diagnostics |
| `custom_components/mer/strings.json`, `translations/en.json` | UI text (kept identical) |
| `tests/conftest.py`, `tests/helpers.py`, `tests/fixtures/*` | Fixtures, mock client, setup helper |
| `docs/api.md`, `README.md`, `LICENSE`, `hacs.json`, `.github/workflows/ci.yml` | Docs and release plumbing |

---

### Task 1: Repository scaffold, tooling and CI

**Files:**
- Create: `pyproject.toml`, `requirements_test.txt`, `hacs.json`, `custom_components/mer/manifest.json`, `custom_components/mer/const.py`, `custom_components/mer/__init__.py` (placeholder), `custom_components/mer/driivz/__init__.py`, `tests/__init__.py`, `tests/conftest.py`, `tests/helpers.py`, `tests/test_manifest.py`, `.github/workflows/ci.yml`

**Interfaces:**
- Produces: `custom_components.mer.const.DOMAIN == "mer"`; `tests.helpers.load_fixture(name) -> str`, `tests.helpers.load_json_fixture(name) -> dict`.

- [ ] **Step 1: Create the WSL dev environment and the wrapper scripts**

Home Assistant cannot be imported on native Windows Python (`homeassistant.runner` imports the
POSIX-only `fcntl`), so the local test environment lives in WSL2 Ubuntu 26.04 (Python 3.14.4).
That distro has no `pip`, no `ensurepip` and no passwordless `sudo`, so pip is bootstrapped from a
PyPI wheel fetched with Windows pip. This has already been done and verified once on this machine;
`scripts/bootstrap-dev` records the procedure so it is reproducible.

Create `requirements_test.txt`:

```
pytest-homeassistant-custom-component==0.13.365
aioresponses==0.7.9
ruff==0.14.0
```

Create four executable scripts in `scripts/`. Each of `test`, `lint` and `format` derives the repo
root from its own location, converts the Git Bash path (`/d/...`) to a WSL path (`/mnt/d/...`) with
`sed -E 's#^/([a-zA-Z])/#/mnt//#'`, and execs the WSL venv's tool via
`wsl.exe -d "$DISTRO" -- bash -lc "cd '<repo_wsl>' && \"\$HOME/.venvs/ha-mer/bin/<tool>\" ..."`:

- `scripts/test` — passes its arguments through to `pytest`.
- `scripts/lint` — read-only: `ruff check .` then `ruff format --check .`.
- `scripts/format` — `ruff format .`.
- `scripts/bootstrap-dev` — creates `~/.venvs/ha-mer` with `python3 -m venv --without-pip`, fetches
  a pip wheel with Windows `python -m pip download pip --no-deps -d <tmp>`, bootstraps it with
  `python3 <wheel>/pip --python ~/.venvs/ha-mer/bin/python install pip setuptools wheel` (the
  `--python` flag must precede the `install` subcommand), then installs `requirements_test.txt` and
  prints the Home Assistant version.

Honour `HA_MER_VENV` (default `$HOME/.venvs/ha-mer`) and `HA_MER_WSL_DISTRO` (default `Ubuntu`).

Verify:

```bash
scripts/bootstrap-dev
```

Expected: ends by printing Home Assistant `2026.9.2`. If the pin resolves to a different version,
keep it and set `hacs.json`'s `homeassistant` key to that minor line.

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "ha-mer"
version = "0.1.0"
description = "Home Assistant integration for Mer (Driivz) EV chargers"
requires-python = ">=3.14"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
asyncio_mode = "auto"
addopts = "-p no:cacheprovider"

[tool.ruff]
target-version = "py314"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "RUF"]
ignore = ["RUF012"]

[tool.ruff.lint.isort]
force-sort-within-sections = true
known-first-party = ["custom_components"]
```

- [ ] **Step 3: Write integration metadata**

`custom_components/mer/manifest.json`:

```json
{
  "domain": "mer",
  "name": "Mer EV Charging",
  "codeowners": ["@danielmsorensen"],
  "config_flow": true,
  "documentation": "https://github.com/danielmsorensen/ha-mer",
  "integration_type": "hub",
  "iot_class": "cloud_polling",
  "issue_tracker": "https://github.com/danielmsorensen/ha-mer/issues",
  "requirements": [],
  "version": "0.1.0"
}
```

(Replace `danielmsorensen` with the actual GitHub username when the remote is created; grep for it in the final task.)

`hacs.json`:

```json
{
  "name": "Mer EV Charging",
  "render_readme": true,
  "homeassistant": "2026.9.0"
}
```

`custom_components/mer/const.py`:

```python
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

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]

WALLET_REFRESH = timedelta(minutes=15)
DETAIL_REFRESH = timedelta(hours=1)
HISTORY_LOOKBACK = timedelta(days=30)
REFRESH_AFTER_COMMAND_SECONDS = 5
RATE_LIMIT_SKIP_THRESHOLD = 1
RATE_LIMIT_WARN_INTERVAL = timedelta(hours=1)
```

`custom_components/mer/__init__.py` (placeholder, replaced in Task 7):

```python
"""The Mer EV Charging integration."""
```

`custom_components/mer/driivz/__init__.py`:

```python
"""HA-agnostic client for Driivz-based driver portals (e.g. Mer UK)."""
```

- [ ] **Step 4: Write test helpers and conftest**

`tests/__init__.py`: empty file.

`tests/helpers.py`:

```python
"""Shared helpers for tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    """Return the text of a fixture file."""
    return (FIXTURES / name).read_text(encoding="utf-8")


def load_json_fixture(name: str) -> Any:
    """Return the parsed JSON of a fixture file."""
    return json.loads(load_fixture(name))
```

`tests/conftest.py`:

```python
"""Pytest configuration for the Mer integration tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom integrations in every test."""
    return
```

- [ ] **Step 5: Write the first failing test**

`tests/test_manifest.py`:

```python
"""Sanity checks on integration metadata."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.mer.const import DOMAIN

MANIFEST = Path("custom_components/mer/manifest.json")


def test_manifest_matches_domain() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["domain"] == DOMAIN
    assert manifest["config_flow"] is True
    assert manifest["iot_class"] == "cloud_polling"
    assert manifest["requirements"] == []
    assert manifest["version"]
```

- [ ] **Step 6: Run tests and ruff**

Run: `scripts/test -q` → Expected: `1 passed`.
Run: `scripts/lint` → Expected: no errors (run `scripts/format` first if needed).

- [ ] **Step 7: Add CI workflow**

`.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  hassfest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: home-assistant/actions/hassfest@master

  hacs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hacs/action@main
        with:
          category: integration

  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.14"
      - run: python -m pip install -r requirements_test.txt
      - run: ruff check . && ruff format --check .
      - run: python -m pytest -q
```

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "chore: scaffold integration, test tooling and CI

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Client constants, exceptions, models and fixtures

**Files:**
- Create: `custom_components/mer/driivz/const.py`, `custom_components/mer/driivz/exceptions.py`, `custom_components/mer/driivz/models.py`
- Create fixtures: `tests/fixtures/login.html`, `map.html`, `login_success.json`, `login_failure.json`, `error_insufficient_permissions.json`, `sites_in_bounds.json`, `stations_in_bounds.json`, `stations_by_ids.json`, `station_6042.json`, `station_6041.json`, `station_17886.json`, `last_active_idle.json`, `last_active_charging.json`, `transaction_start_time.json`, `transaction_estimate.json`, `transactions.json`, `wallet.json`, `start_charge_pending.json`, `start_charge_rejected.json`
- Test: `tests/driivz/__init__.py`, `tests/driivz/test_models.py`

**Interfaces:**
- Produces: everything in `models.py` and `exceptions.py` below; later tasks import these names exactly.

- [ ] **Step 1: Write `driivz/const.py`**

```python
"""Constants for the Driivz driver-portal client."""

from __future__ import annotations

DEFAULT_BASE_URL = "https://driver.uk.mer.eco"

HEADER_CSRF = "X-CSRF-TOKEN"
HEADER_APP_TYPE = "X-APP-TYPE"
HEADER_JSON_TYPES = "X-JSON-TYPES"
HEADER_AJAX = "X-Ajax-call"
HEADER_RATE_LIMIT_REMAINING = "X-Rate-Limit-Remaining"

PATH_LOGIN = "login"
PATH_LOGOUT = "logout"
PATH_MAP = "findCharger"
PATH_FIND_SITES_IN_BOUNDS = "stationFacade/findSitesInBounds"
PATH_FIND_STATIONS_IN_BOUNDS = "stationFacade/findStationsInBounds"
PATH_FIND_STATIONS_BY_IDS = "stationFacade/findStationsByIds"
PATH_FIND_STATION_BY_ID = "stationFacade/findStationById"
PATH_LAST_ACTIVE_SOCKET = "stationFacade/findLastActiveChargeSocket"
PATH_TRANSACTION_START_TIME = "stationFacade/findCurrentTransactionStartTime"
PATH_TRANSACTION_ESTIMATE = "stationFacade/findCurrentTransactionBillingChargingEstimation"
PATH_START_CHARGE = "stationFacade/startChargeNow"
PATH_STOP_CHARGE = "stationFacade/stopCharge"
PATH_WALLET = "billingFacade/findCustomerDetailWalletByCustomerId"
PATH_TRANSACTIONS = "customerFacade/findDriverChargeTransactionLogByView"

STATUS_AVAILABLE = "AVAILABLE"
STATUS_OCCUPIED = "OCCUPIED"
STATUS_CHARGING = "CHARGING"
STATUS_DISCHARGING = "DISCHARGING"
STATUS_PAUSED = "PAUSED"
STATUS_PREPARING = "PREPARING"
STATUS_FINISHING = "FINISHING"
STATUS_RESERVED = "RESERVED"
STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_FAULTED = "FAULTED"
STATUS_UNKNOWN = "UNKNOWN"

STATUSES: tuple[str, ...] = (
    STATUS_AVAILABLE,
    STATUS_OCCUPIED,
    STATUS_CHARGING,
    STATUS_DISCHARGING,
    STATUS_PAUSED,
    STATUS_PREPARING,
    STATUS_FINISHING,
    STATUS_RESERVED,
    STATUS_UNAVAILABLE,
    STATUS_FAULTED,
    STATUS_UNKNOWN,
)

IN_USE_STATUSES: frozenset[str] = frozenset(
    {
        STATUS_OCCUPIED,
        STATUS_CHARGING,
        STATUS_DISCHARGING,
        STATUS_PAUSED,
        STATUS_PREPARING,
        STATUS_FINISHING,
    }
)

AUTH_ERROR_TYPES: frozenset[str] = frozenset(
    {
        "ACCESS_DENIED",
        "AUTHENTICATION_REQUIRED",
        "BAD_CREDENTIALS",
        "LOGIN_PAGE",
        "PASSWORD_EXPIRES",
        "SESSION_EXPIRED",
        "USER_IS_NOT_ACTIVE",
        "USER_NOT_LOGGED_IN",
    }
)

OPERATION_PENDING = "PENDING"
```

- [ ] **Step 2: Write `driivz/exceptions.py`**

```python
"""Exceptions raised by the Driivz client."""

from __future__ import annotations


class DriivzError(Exception):
    """Base class for all client errors."""


class DriivzConnectionError(DriivzError):
    """Network failure or non-JSON/unexpected response."""


class AuthError(DriivzError):
    """Login failed or the session is no longer authenticated."""

    def __init__(self, error_type: str = "AUTH_FAILED") -> None:
        super().__init__(error_type)
        self.error_type = error_type


class RateLimitError(DriivzError):
    """The portal rejected the request because of rate limiting."""


class ApiError(DriivzError):
    """The portal answered with success=false or rejected an operation."""

    def __init__(self, error_type: str, message_key: str | None = None) -> None:
        super().__init__(f"{error_type}: {message_key}" if message_key else error_type)
        self.error_type = error_type
        self.message_key = message_key
```

- [ ] **Step 3: Write the fixtures**

`tests/fixtures/login.html`:

```html
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/>
<meta name="_csrf" content="4236635a-aea4-47ba-a1e6-64c6d5988dff"/>
<meta name="_csrf_parameterName" content="_csrf"/>
<meta name="_csrf_header" content="X-CSRF-TOKEN"/>
<title>Mer - sign in</title></head>
<body><form class="sky-form boxed mainInnerContent" id="loginForm"></form></body></html>
```

`tests/fixtures/map.html`: same as `login.html` but with `content="38724756-f55f-4ce6-af0f-13b795f84006"` on the `_csrf` meta and `<title>Mer - Find A charge point</title>`.

`tests/fixtures/login_success.json`:

```json
{"errors":[],"success":true,"targetUrl":"/findCharger"}
```

`tests/fixtures/login_failure.json`:

```json
{"errors":[{"errorType":"BAD_CREDENTIALS","messageKey":"enum.ErrorType.BAD_CREDENTIALS"}],"success":false,"errorType":"BAD_CREDENTIALS"}
```

`tests/fixtures/error_insufficient_permissions.json`:

```json
{"errors":[{"errorType":"INSUFFICIENT_PERMISSIONS","messageKey":"Message ID: 85A05724-D2 Error cause: INSUFFICIENT_PERMISSIONS"}],"success":false}
```

`tests/fixtures/sites_in_bounds.json`:

```json
{"errors":[],"success":true,"data":[
{"id":2877,"deleted":false,"dirty":false,"managed":true,"fast":false,"siteId":2877,"dn":"Durham County Council - Business Durham NETPark","latitude":54.67043,"longitude":-1.45045,"ss":"AVAILABLE","im":true,"ns":8,"sal":"PUBLIC","hg":true,"imn":false,"mfr":false,"isn":false,"isf":false,"scs":"SEMI_FAST","cs":false},
{"id":3796,"deleted":false,"dirty":false,"managed":true,"fast":true,"siteId":3796,"dn":"Business Durham NETPark - Expansion Space Car Park","latitude":54.6724801,"longitude":-1.4539477,"ss":"AVAILABLE","im":true,"ns":14,"sal":"PUBLIC","hg":true,"imn":false,"mfr":false,"isn":false,"isf":true,"scs":"FAST","cs":false},
{"id":1359,"deleted":false,"dirty":false,"managed":true,"fast":false,"siteId":1359,"dn":"Norton Hall","latitude":54.592408,"longitude":-1.310479,"ss":"AVAILABLE","im":true,"ns":2,"sal":"PUBLIC","hg":true,"imn":false,"mfr":false,"isn":false,"isf":false,"scs":"SEMI_FAST","cs":false}
]}
```

`tests/fixtures/stations_in_bounds.json` (short DTOs as returned by the map query):

```json
{"errors":[],"success":true,"data":[
{"id":6042,"caption":"(MER-FS-AD00137) Business Durham - NETPark 3 - Explorer 1","latitude":54.67043,"longitude":-1.45045,"stationStatusId":"AVAILABLE","comingSoon":false,"isManaged":true,"deleted":false,"dirty":false,"canRegisterForNotifyWhenAvailable":false,"chargingSpeedId":"SLOW","overrideNextMaintenanceRecurrence":false,"stationSockets":[{"id":11243,"blocked":false,"deleted":false,"dirty":false,"rfidCardEnrollmentPending":false,"socketTariffsAreDirty":false},{"id":11244,"blocked":false,"deleted":false,"dirty":false,"rfidCardEnrollmentPending":false,"socketTariffsAreDirty":false}]},
{"id":6041,"caption":"(MER-FS-AD01372) Business Durham - NETPark 4 - Explorer 2","latitude":54.67043,"longitude":-1.45045,"stationStatusId":"CHARGING","comingSoon":false,"isManaged":true,"deleted":false,"dirty":false,"canRegisterForNotifyWhenAvailable":false,"chargingSpeedId":"SLOW","overrideNextMaintenanceRecurrence":false,"stationSockets":[{"id":11241,"blocked":false,"deleted":false,"dirty":false,"rfidCardEnrollmentPending":false,"socketTariffsAreDirty":false},{"id":11242,"blocked":false,"deleted":false,"dirty":false,"rfidCardEnrollmentPending":false,"socketTariffsAreDirty":false}]},
{"id":17886,"caption":"(MER-FS-ABT0105) Business Durham NETPark - Expansion Space Car Park","latitude":54.6724801,"longitude":-1.4539477,"stationStatusId":"AVAILABLE","comingSoon":false,"isManaged":true,"deleted":false,"dirty":false,"canRegisterForNotifyWhenAvailable":false,"chargingSpeedId":"FAST","overrideNextMaintenanceRecurrence":false,"stationSockets":[{"id":15028,"blocked":false,"deleted":false,"dirty":false,"rfidCardEnrollmentPending":false,"socketTariffsAreDirty":false}]}
]}
```

`tests/fixtures/stations_by_ids.json` (the polling call; includes socket status and power):

```json
{"errors":[],"success":true,"data":[
{"id":6042,"caption":"(MER-FS-AD00137) Business Durham - NETPark 3 - Explorer 1","latitude":54.67043,"longitude":-1.45045,"stationStatusId":"AVAILABLE","comingSoon":false,"isManaged":true,"deleted":false,"dirty":false,"stationSockets":[{"id":11243,"socketStatusId":"AVAILABLE","maximumPower":7,"blocked":false,"deleted":false,"dirty":false},{"id":11244,"socketStatusId":"AVAILABLE","maximumPower":7,"blocked":false,"deleted":false,"dirty":false}]},
{"id":6041,"caption":"(MER-FS-AD01372) Business Durham - NETPark 4 - Explorer 2","latitude":54.67043,"longitude":-1.45045,"stationStatusId":"CHARGING","comingSoon":false,"isManaged":true,"deleted":false,"dirty":false,"stationSockets":[{"id":11241,"socketStatusId":"CHARGING","maximumPower":7,"blocked":false,"deleted":false,"dirty":false},{"id":11242,"socketStatusId":"AVAILABLE","maximumPower":7,"blocked":false,"deleted":false,"dirty":false}]}
]}
```

`tests/fixtures/station_6042.json` (full detail; the shape captured live from `findStationById`):

```json
{"errors":[],"success":true,"data":{
"addressAddress1":"Discovery Centre NETPark,","addressCity":"Sedgefield, Stockton-on-Tees","addressCountryId":234,"addressCountryIso2Code":"GB","addressCountryIso3Code":"GBR","addressCountryName":"United Kingdom","addressZipCode":"TS21 3FD",
"canRegisterForNotifyWhenAvailable":false,"caption":"(MER-FS-AD00137) Business Durham - NETPark 3 - Explorer 1","chargingSpeedId":"SLOW","comingSoon":false,"deleted":false,"dirty":false,"id":6042,"identityKey":"MER-FS-AD00137","inMaintenance":false,"isManaged":true,"latitude":54.67043,"longitude":-1.45045,"markForReplacement":false,"offline":false,"openingTimes":[],"overrideNextMaintenanceRecurrence":false,"propertyId":2488,"showExternalCoupons":false,
"siteDisplayName":"Durham County Council - Business Durham NETPark","siteHasGate":false,"siteId":2877,"siteName":"Durham County Council - Business Durham NETPark","siteStationAccessLevel":"PUBLIC","stationAccessLevelId":"PUBLIC","stationModelInstructionsVideoUrl":"gwDTMkE7Uq4","stationModelName":"Eve Double Pro-line","stationOwnerId":141,"stationOwnerName":"Durham County Council",
"stationSockets":[
{"blocked":false,"deleted":false,"dirty":false,"hasTeslaAdapter":false,"id":11243,"identityKey":"1","inMaintenance":false,"maximumPower":7,"name":"Left","reserved":false,"rfidCardEnrollmentPending":false,"showExternalCoupons":false,"siteDisplayName":"Durham County Council - Business Durham NETPark","socketPrices":[{"billingPlanCode":"DCC IP","billingPlanId":3465,"billingSpCurrencyCurrency":"GBP","billingSpCurrencyId":2,"currency":"GBP","deleted":false,"dirty":false,"futureReservationFee":0,"kwhPrice":0,"plugInMinuteRate":0,"socketType":"TYPE_2_MENNEKES","stationId":6042,"stationSocketId":11243,"transactionFee":0}],"socketStatusId":"AVAILABLE","socketTariffsAreDirty":false,"stationId":6042,"stationInMaintenance":false,"stationIsManaged":true,"stationModelSocketChargingInstructions":"--lang=en\n1.| Swipe to Start Charge;2.| Connect Cable","stationModelSocketChargingMode":"MODE3","stationModelSocketMaximumPower":22,"stationModelSocketSocketTypeId":"TYPE_2_MENNEKES","stationModelSocketVoltageType":"AC","teslaInMaintenance":false},
{"blocked":false,"deleted":false,"dirty":false,"hasTeslaAdapter":false,"id":11244,"identityKey":"2","inMaintenance":false,"maximumPower":7,"name":"Right","reserved":false,"rfidCardEnrollmentPending":false,"showExternalCoupons":false,"siteDisplayName":"Durham County Council - Business Durham NETPark","socketPrices":[{"billingPlanCode":"DCC IP","billingPlanId":3465,"billingSpCurrencyCurrency":"GBP","billingSpCurrencyId":2,"currency":"GBP","deleted":false,"dirty":false,"futureReservationFee":0,"kwhPrice":0,"plugInMinuteRate":0,"socketType":"TYPE_2_MENNEKES","stationId":6042,"stationSocketId":11244,"transactionFee":0}],"socketStatusId":"AVAILABLE","socketTariffsAreDirty":false,"stationId":6042,"stationInMaintenance":false,"stationIsManaged":true,"stationModelSocketChargingInstructions":"--lang=en\n1.| Swipe to Start Charge;2.| Connect Cable","stationModelSocketChargingMode":"MODE3","stationModelSocketMaximumPower":22,"stationModelSocketSocketTypeId":"TYPE_2_MENNEKES","stationModelSocketVoltageType":"AC","teslaInMaintenance":false}
],"stationStatusId":"AVAILABLE"}}
```

`tests/fixtures/station_6041.json`: copy of `station_6042.json` with `id` 6041, `identityKey` `MER-FS-AD01372`, caption `(MER-FS-AD01372) Business Durham - NETPark 4 - Explorer 2`, sockets `11241` ("Left", `socketStatusId` `CHARGING`) and `11242` ("Right", `AVAILABLE`), `stationId` 6041 everywhere, `stationStatusId` `CHARGING`.

`tests/fixtures/station_17886.json`: copy of `station_6042.json` with `id` 17886, `identityKey` `MER-FS-ABT0105`, caption `(MER-FS-ABT0105) Business Durham NETPark - Expansion Space Car Park`, `siteId` 3796, `siteName`/`siteDisplayName` `Business Durham NETPark - Expansion Space Car Park`, `stationModelName` `Alpitronic HYC50`, one socket `15028` ("CCS", `AVAILABLE`, `maximumPower` 50, `stationModelSocketSocketTypeId` `TYPE_COMBO_GERMANY`, `stationModelSocketVoltageType` `DC`, `kwhPrice` 0.76).

`tests/fixtures/last_active_idle.json`:

```json
{"errors":[],"success":true}
```

`tests/fixtures/last_active_charging.json` (synthetic shape: a socket DTO; confirm against a real session in Task 15):

```json
{"errors":[],"success":true,"data":{"id":11241,"stationId":6041,"name":"Left","identityKey":"1","socketStatusId":"CHARGING","maximumPower":7,"stationModelSocketSocketTypeId":"TYPE_2_MENNEKES","stationModelSocketVoltageType":"AC"}}
```

`tests/fixtures/transaction_start_time.json` (synthetic; epoch ms):

```json
{"errors":[],"success":true,"data":1789554866000}
```

`tests/fixtures/transaction_estimate.json` (synthetic; `totalEnergy` in Wh like history rows):

```json
{"errors":[],"success":true,"data":{"totalEnergy":12345,"cost":0,"currency":"GBP"}}
```

`tests/fixtures/transactions.json` (real shape, sanitised account number):

```json
{"errors":[],"success":true,"data":[
{"billCorrupted":false,"billingPlanDisplayCode":"Durham County Council - Netpark IP","billingPlanName":"Durham County Council - Netpark IP","caption":"(MER-FS-AD01372) Business Durham - NETPark 4 - Explorer 2","chargeTransactionBillingStatus":"FINAL_COST","cost":0,"currency":"GBP","deleted":false,"dirty":false,"duration":"04:53:21","durationTime":17601,"id":9084600,"siteName":"Durham County Council - Business Durham NETPark","socketType":"TYPE_2_MENNEKES","startAccountNumber":1123456,"startCardCardType":"VIRTUAL","startInitiator":"MOBILE","startOn":1789554866000,"stationId":6041,"stoppedOn":1789572467000,"totalEnergy":32408},
{"billCorrupted":false,"billingPlanDisplayCode":"Durham County Council - Netpark IP","billingPlanName":"Durham County Council - Netpark IP","caption":"(MER-FS-AD01372) Business Durham - NETPark 4 - Explorer 2","chargeTransactionBillingStatus":"FINAL_COST","cost":0,"currency":"GBP","deleted":false,"dirty":false,"duration":"04:48:30","durationTime":17310,"id":9075809,"siteName":"Durham County Council - Business Durham NETPark","socketType":"TYPE_2_MENNEKES","startAccountNumber":1123456,"startCardCardType":"VIRTUAL","startInitiator":"STATION","startOn":1789370485000,"stationId":6041,"stoppedOn":1789387795000,"totalEnergy":21808}
]}
```

`tests/fixtures/wallet.json`:

```json
{"errors":[],"success":true,"data":{"accountBalance":12.5,"accountNumber":1123456,"billable":true,"billingCycleTerm":"MONTHLY","billingSpCurrencyCurrency":"GBP","billingSpCurrencyId":2,"currency":"GBP","customerDetailAccountType":"PRIVATE","customerDetailId":123456,"customerDetailMemberId":123123,"customerDetailUserStatus":"ACTIVE","deleted":false,"dirty":false,"id":228000,"serviceProviderId":7,"serviceProviderName":"Mer","timezoneZoneId":"Europe/London","walletType":"CUSTOMER"}}
```

`tests/fixtures/start_charge_pending.json`:

```json
{"errors":[],"success":true,"data":{"operationStatus":"PENDING"}}
```

`tests/fixtures/start_charge_rejected.json`:

```json
{"errors":[],"success":true,"data":{"operationStatus":"REJECTED"}}
```

- [ ] **Step 4: Write the failing model tests**

`tests/driivz/__init__.py`: empty.

`tests/driivz/test_models.py`:

```python
"""Tests for Driivz models."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.mer.driivz.models import (
    Bounds,
    SessionEstimate,
    Site,
    Socket,
    Station,
    Transaction,
    Wallet,
    clean_caption,
    ms_to_datetime,
    parse_start_time,
)
from tests.helpers import load_json_fixture


def test_clean_caption_strips_prefix_and_code() -> None:
    assert (
        clean_caption("[RESTRICTED ACCESS] (MER-FS-AD00457) Business Durham - NETPark 1 - Plexus")
        == "Business Durham - NETPark 1 - Plexus"
    )
    assert (
        clean_caption("Kings College London - Great Dover Street Apartments (MER-FS-AC00264)")
        == "Kings College London - Great Dover Street Apartments"
    )
    assert clean_caption("GB*B3V*EMERUKAD00057*1") == "GB*B3V*EMERUKAD00057*1"


def test_ms_to_datetime() -> None:
    assert ms_to_datetime(1789554866000) == datetime(2026, 9, 16, 10, 34, 26, tzinfo=UTC)
    assert ms_to_datetime(None) is None
    assert ms_to_datetime("bad") is None


def test_parse_start_time_accepts_int_or_dict() -> None:
    expected = datetime(2026, 9, 16, 10, 34, 26, tzinfo=UTC)
    assert parse_start_time(1789554866000) == expected
    assert parse_start_time({"startOn": 1789554866000}) == expected
    assert parse_start_time({"startTime": 1789554866000}) == expected
    assert parse_start_time(None) is None
    assert parse_start_time({}) is None


def test_bounds() -> None:
    b = Bounds.around(54.67043, -1.45045)
    assert b.to_dict() == {
        "northEastLat": 54.67343,
        "northEastLng": -1.44745,
        "southWestLat": 54.66743,
        "southWestLng": -1.45345,
    }
    assert Bounds.UK.to_dict()["northEastLat"] == 61.0


def test_site_from_dict() -> None:
    raw = load_json_fixture("sites_in_bounds.json")["data"][0]
    site = Site.from_dict(raw)
    assert site.id == 2877
    assert site.name == "Durham County Council - Business Durham NETPark"
    assert site.status == "AVAILABLE"
    assert site.socket_count == 8
    assert site.access_level == "PUBLIC"
    assert site.latitude == 54.67043
    assert site.charging_speed == "SEMI_FAST"


def test_station_detail_from_dict() -> None:
    raw = load_json_fixture("station_6042.json")["data"]
    station = Station.from_dict(raw)
    assert station.id == 6042
    assert station.display_name == "Business Durham - NETPark 3 - Explorer 1"
    assert station.is_restricted is False
    assert station.site_id == 2877
    assert station.identity_key == "MER-FS-AD00137"
    assert station.model_name == "Eve Double Pro-line"
    assert station.owner_name == "Durham County Council"
    assert station.status == "AVAILABLE"
    assert [s.name for s in station.sockets] == ["Left", "Right"]
    left = station.sockets[0]
    assert left.id == 11243
    assert left.station_id == 6042
    assert left.identity_key == "1"
    assert left.max_power_kw == 7
    assert left.socket_type == "TYPE_2_MENNEKES"
    assert left.voltage_type == "AC"
    assert left.price_per_kwh == 0
    assert left.prices[0].billing_plan_id == 3465
    assert left.prices[0].currency == "GBP"
    assert left.is_available is True
    assert left.is_in_use is False


def test_station_short_from_dict_defaults() -> None:
    raw = load_json_fixture("stations_in_bounds.json")["data"][0]
    station = Station.from_dict(raw)
    assert station.site_id is None
    assert station.sockets[0].status == "UNKNOWN"
    assert station.sockets[0].name is None
    assert station.sockets[0].price_per_kwh is None


def test_station_with_live_merges_status() -> None:
    detail = Station.from_dict(load_json_fixture("station_6042.json")["data"])
    live_raw = dict(load_json_fixture("stations_by_ids.json")["data"][0])
    live_raw["stationStatusId"] = "CHARGING"
    live_raw["stationSockets"][1]["socketStatusId"] = "CHARGING"
    merged = detail.with_live(Station.from_dict(live_raw))
    assert merged.status == "CHARGING"
    assert merged.sockets[0].name == "Left"
    assert merged.sockets[0].status == "AVAILABLE"
    assert merged.sockets[1].name == "Right"
    assert merged.sockets[1].status == "CHARGING"
    assert merged.sockets[1].is_in_use is True
    assert merged.model_name == "Eve Double Pro-line"


def test_socket_restricted_flag() -> None:
    station = Station.from_dict(
        {"id": 1, "caption": "[RESTRICTED ACCESS] (MER-FS-X) Foo", "stationStatusId": "AVAILABLE"}
    )
    assert station.is_restricted is True
    assert station.display_name == "Foo"
    assert station.sockets == ()


def test_wallet_from_dict() -> None:
    wallet = Wallet.from_dict(load_json_fixture("wallet.json")["data"])
    assert wallet.id == 228000
    assert wallet.customer_id == 123456
    assert wallet.member_id == 123123
    assert wallet.account_number == 1123456
    assert wallet.balance == 12.5
    assert wallet.currency == "GBP"
    assert wallet.timezone == "Europe/London"


def test_transaction_from_dict() -> None:
    tx = Transaction.from_dict(load_json_fixture("transactions.json")["data"][0])
    assert tx.id == 9084600
    assert tx.station_id == 6041
    assert tx.display_name == "Business Durham - NETPark 4 - Explorer 2"
    assert tx.started_at == datetime(2026, 9, 16, 10, 34, 26, tzinfo=UTC)
    assert tx.stopped_at == datetime(2026, 9, 16, 15, 27, 47, tzinfo=UTC)
    assert tx.duration_s == 17601
    assert tx.energy_kwh == 32.408
    assert tx.cost == 0
    assert tx.currency == "GBP"
    assert tx.billing_plan_name == "Durham County Council - Netpark IP"


def test_session_estimate_variants() -> None:
    est = SessionEstimate.from_dict({"totalEnergy": 12345, "cost": 1.5, "currency": "GBP"})
    assert est.energy_kwh == 12.345
    assert est.cost == 1.5
    assert est.currency == "GBP"
    est2 = SessionEstimate.from_dict({"energyKwh": 3.2, "totalCost": "0.80"})
    assert est2.energy_kwh == 3.2
    assert est2.cost == 0.8
    assert est2.currency is None
    est3 = SessionEstimate.from_dict({})
    assert est3.energy_kwh is None
    assert est3.cost is None
    assert est3.raw == {}


def test_socket_from_dict_minimal() -> None:
    socket = Socket.from_dict({"id": 5})
    assert socket.status == "UNKNOWN"
    assert socket.prices == ()
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `scripts/test tests/driivz/test_models.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'custom_components.mer.driivz.models'`.

- [ ] **Step 6: Write `driivz/models.py`**

```python
"""Dataclass models for Driivz driver-portal responses."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import re
from typing import Any, ClassVar

from .const import IN_USE_STATUSES, STATUS_AVAILABLE, STATUS_UNKNOWN

_RESTRICTED_PREFIX = "[RESTRICTED ACCESS]"
_CODE_RE = re.compile(r"\(\s*MER-[A-Z0-9-]+\s*\)")
_SPACES_RE = re.compile(r"\s+")


def clean_caption(caption: str) -> str:
    """Return a human-friendly charger name from a portal caption."""
    text = caption.replace(_RESTRICTED_PREFIX, "")
    text = _CODE_RE.sub("", text)
    return _SPACES_RE.sub(" ", text).strip(" -")


def ms_to_datetime(value: Any) -> datetime | None:
    """Convert epoch milliseconds to an aware UTC datetime."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def parse_start_time(data: Any) -> datetime | None:
    """Parse the payload of findCurrentTransactionStartTime (int ms or a dict)."""
    if isinstance(data, Mapping):
        for key in ("startOn", "startTime", "startedOn", "transactionStartTime"):
            if data.get(key) is not None:
                return ms_to_datetime(data[key])
        return None
    return ms_to_datetime(data)


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def _str(value: Any) -> str | None:
    return str(value) if value is not None else None


@dataclass(frozen=True, slots=True)
class Bounds:
    """A latitude/longitude bounding box in portal field naming."""

    north_east_lat: float
    north_east_lng: float
    south_west_lat: float
    south_west_lng: float

    UK: ClassVar[Bounds]

    @classmethod
    def around(cls, latitude: float, longitude: float, delta: float = 0.003) -> Bounds:
        """Return a small box centred on a coordinate."""
        return cls(
            round(latitude + delta, 6),
            round(longitude + delta, 6),
            round(latitude - delta, 6),
            round(longitude - delta, 6),
        )

    def to_dict(self) -> dict[str, float]:
        """Return the portal's `filterByBounds` payload."""
        return {
            "northEastLat": self.north_east_lat,
            "northEastLng": self.north_east_lng,
            "southWestLat": self.south_west_lat,
            "southWestLng": self.south_west_lng,
        }


Bounds.UK = Bounds(61.0, 2.0, 49.5, -9.0)


@dataclass(frozen=True, slots=True)
class Site:
    """A charging site (car park) from findSitesInBounds."""

    id: int
    name: str
    status: str
    socket_count: int
    access_level: str | None
    latitude: float | None
    longitude: float | None
    charging_speed: str | None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Site:
        return cls(
            id=int(data["id"]),
            name=str(data.get("dn") or ""),
            status=str(data.get("ss") or STATUS_UNKNOWN),
            socket_count=_int(data.get("ns")) or 0,
            access_level=_str(data.get("sal")),
            latitude=_float(data.get("latitude")),
            longitude=_float(data.get("longitude")),
            charging_speed=_str(data.get("scs")),
        )


@dataclass(frozen=True, slots=True)
class SocketPrice:
    """Tariff for one socket under one billing plan."""

    billing_plan_id: int | None
    billing_plan_code: str | None
    kwh_price: float | None
    plug_in_minute_rate: float | None
    transaction_fee: float | None
    currency: str | None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SocketPrice:
        return cls(
            billing_plan_id=_int(data.get("billingPlanId")),
            billing_plan_code=_str(data.get("billingPlanCode")),
            kwh_price=_float(data.get("kwhPrice")),
            plug_in_minute_rate=_float(data.get("plugInMinuteRate")),
            transaction_fee=_float(data.get("transactionFee")),
            currency=_str(data.get("currency") or data.get("billingSpCurrencyCurrency")),
        )


@dataclass(frozen=True, slots=True)
class Socket:
    """One connector on a station."""

    id: int
    station_id: int | None = None
    name: str | None = None
    identity_key: str | None = None
    status: str = STATUS_UNKNOWN
    max_power_kw: float | None = None
    socket_type: str | None = None
    voltage_type: str | None = None
    prices: tuple[SocketPrice, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Socket:
        return cls(
            id=int(data["id"]),
            station_id=_int(data.get("stationId")),
            name=_str(data.get("name")),
            identity_key=_str(data.get("identityKey")),
            status=str(data.get("socketStatusId") or STATUS_UNKNOWN),
            max_power_kw=_float(data.get("maximumPower")),
            socket_type=_str(data.get("stationModelSocketSocketTypeId")),
            voltage_type=_str(data.get("stationModelSocketVoltageType")),
            prices=tuple(SocketPrice.from_dict(p) for p in data.get("socketPrices") or []),
        )

    @property
    def price_per_kwh(self) -> float | None:
        return self.prices[0].kwh_price if self.prices else None

    @property
    def is_available(self) -> bool:
        return self.status == STATUS_AVAILABLE

    @property
    def is_in_use(self) -> bool:
        return self.status in IN_USE_STATUSES


@dataclass(frozen=True, slots=True)
class Station:
    """A charger (station) with its sockets."""

    id: int
    caption: str
    status: str = STATUS_UNKNOWN
    latitude: float | None = None
    longitude: float | None = None
    site_id: int | None = None
    site_name: str | None = None
    identity_key: str | None = None
    model_name: str | None = None
    owner_name: str | None = None
    access_level: str | None = None
    coming_soon: bool = False
    sockets: tuple[Socket, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Station:
        return cls(
            id=int(data["id"]),
            caption=str(data.get("caption") or ""),
            status=str(data.get("stationStatusId") or STATUS_UNKNOWN),
            latitude=_float(data.get("latitude")),
            longitude=_float(data.get("longitude")),
            site_id=_int(data.get("siteId")),
            site_name=_str(data.get("siteName") or data.get("siteDisplayName")),
            identity_key=_str(data.get("identityKey")),
            model_name=_str(data.get("stationModelName")),
            owner_name=_str(data.get("stationOwnerName")),
            access_level=_str(data.get("siteStationAccessLevel")),
            coming_soon=bool(data.get("comingSoon", False)),
            sockets=tuple(Socket.from_dict(s) for s in data.get("stationSockets") or []),
        )

    @property
    def display_name(self) -> str:
        return clean_caption(self.caption) or f"Station {self.id}"

    @property
    def is_restricted(self) -> bool:
        return _RESTRICTED_PREFIX in self.caption

    def with_live(self, live: Station) -> Station:
        """Return this (detailed) station with statuses taken from a live short DTO."""
        live_sockets = {s.id: s for s in live.sockets}
        merged = [
            replace(s, status=live_sockets[s.id].status) if s.id in live_sockets else s
            for s in self.sockets
        ]
        known = {s.id for s in merged}
        merged.extend(s for s in live.sockets if s.id not in known)
        return replace(self, status=live.status, sockets=tuple(merged))


@dataclass(frozen=True, slots=True)
class Wallet:
    """The customer's wallet/account summary."""

    id: int | None
    customer_id: int | None
    member_id: int | None
    account_number: int | None
    balance: float | None
    currency: str | None
    timezone: str | None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Wallet:
        return cls(
            id=_int(data.get("id")),
            customer_id=_int(data.get("customerDetailId")),
            member_id=_int(data.get("customerDetailMemberId")),
            account_number=_int(data.get("accountNumber")),
            balance=_float(data.get("accountBalance")),
            currency=_str(data.get("currency") or data.get("billingSpCurrencyCurrency")),
            timezone=_str(data.get("timezoneZoneId")),
        )


@dataclass(frozen=True, slots=True)
class Transaction:
    """A completed charge transaction from the driver's history."""

    id: int
    station_id: int | None
    caption: str
    site_name: str | None
    started_at: datetime | None
    stopped_at: datetime | None
    duration_s: int | None
    energy_kwh: float | None
    cost: float | None
    currency: str | None
    billing_plan_name: str | None
    billing_status: str | None
    socket_type: str | None
    start_initiator: str | None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Transaction:
        energy_wh = _float(data.get("totalEnergy"))
        return cls(
            id=int(data["id"]),
            station_id=_int(data.get("stationId")),
            caption=str(data.get("caption") or ""),
            site_name=_str(data.get("siteName")),
            started_at=ms_to_datetime(data.get("startOn")),
            stopped_at=ms_to_datetime(data.get("stoppedOn")),
            duration_s=_int(data.get("durationTime")),
            energy_kwh=round(energy_wh / 1000, 3) if energy_wh is not None else None,
            cost=_float(data.get("cost")),
            currency=_str(data.get("currency")),
            billing_plan_name=_str(data.get("billingPlanName")),
            billing_status=_str(data.get("chargeTransactionBillingStatus")),
            socket_type=_str(data.get("socketType")),
            start_initiator=_str(data.get("startInitiator")),
        )

    @property
    def display_name(self) -> str:
        return clean_caption(self.caption)


@dataclass(frozen=True, slots=True)
class SessionEstimate:
    """Live energy/cost estimate of the running transaction (field names tolerant)."""

    energy_kwh: float | None
    cost: float | None
    currency: str | None
    raw: dict[str, Any] = field(default_factory=dict)

    _ENERGY_KEYS: ClassVar[tuple[tuple[str, float], ...]] = (
        ("energyKwh", 1.0),
        ("totalEnergyKwh", 1.0),
        ("energy", 1.0),
        ("totalEnergy", 1000.0),
        ("energyConsumed", 1000.0),
    )
    _COST_KEYS: ClassVar[tuple[str, ...]] = ("cost", "totalCost", "estimatedCost", "price")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SessionEstimate:
        energy: float | None = None
        for key, divisor in cls._ENERGY_KEYS:
            value = _float(data.get(key))
            if value is not None:
                energy = round(value / divisor, 3)
                break
        cost: float | None = None
        for key in cls._COST_KEYS:
            cost = _float(data.get(key))
            if cost is not None:
                break
        return cls(
            energy_kwh=energy,
            cost=cost,
            currency=_str(data.get("currency")),
            raw=dict(data),
        )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `scripts/test tests/driivz/test_models.py -q`
Expected: all PASS. If `ms_to_datetime(1789554866000)` differs by the local timezone, the implementation is wrong: it must pass `tz=UTC`.

- [ ] **Step 8: Lint and commit**

```bash
scripts/format && scripts/lint
git add -A
git commit -m "feat(driivz): add constants, exceptions, models and sanitised fixtures

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Client core — CSRF, login, request envelope, retry

**Files:**
- Create: `custom_components/mer/driivz/client.py`
- Test: `tests/driivz/test_client_core.py`

**Interfaces:**
- Consumes: `driivz.const`, `driivz.exceptions`, `driivz.models` (Task 2).
- Produces: `extract_csrf(html) -> str | None`; `DriivzDriverClient(session, username, password, base_url=DEFAULT_BASE_URL)` with `login()`, `logged_in`, `rate_limit_remaining`, `_request(method, path, *, json=None, data=None, params=None) -> Any` (returns the envelope's `data`), `_form(mapping) -> dict[str, str]`.

- [ ] **Step 1: Write the failing tests**

`tests/driivz/test_client_core.py`:

```python
"""Tests for DriivzDriverClient login and request plumbing."""

from __future__ import annotations

import aiohttp
from aioresponses import aioresponses
import pytest
from yarl import URL

from custom_components.mer.driivz.client import DriivzDriverClient, extract_csrf
from custom_components.mer.driivz.exceptions import (
    ApiError,
    AuthError,
    DriivzConnectionError,
    RateLimitError,
)
from tests.helpers import load_fixture, load_json_fixture

BASE = "https://driver.uk.mer.eco"
LOGIN_URL = f"{BASE}/login"
MAP_URL = f"{BASE}/findCharger"
WALLET_PATH = "billingFacade/findCustomerDetailWalletByCustomerId"
WALLET_URL = f"{BASE}/{WALLET_PATH}"


@pytest.fixture
async def session():
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as s:
        yield s


@pytest.fixture
def client(session: aiohttp.ClientSession) -> DriivzDriverClient:
    return DriivzDriverClient(session, "user@example.com", "secret")


def mock_login(m: aioresponses, *, success: bool = True) -> None:
    m.get(LOGIN_URL, body=load_fixture("login.html"), content_type="text/html")
    name = "login_success.json" if success else "login_failure.json"
    m.post(LOGIN_URL, payload=load_json_fixture(name))
    if success:
        m.get(MAP_URL, body=load_fixture("map.html"), content_type="text/html")


def test_extract_csrf() -> None:
    assert extract_csrf(load_fixture("login.html")) == "4236635a-aea4-47ba-a1e6-64c6d5988dff"
    assert extract_csrf('<meta content="abc" name="_csrf">') == "abc"
    assert extract_csrf("<html></html>") is None


async def test_login_posts_form_with_csrf_and_headers(client: DriivzDriverClient) -> None:
    with aioresponses() as m:
        mock_login(m)
        await client.login()
        post = m.requests[("POST", URL(LOGIN_URL))][0]
    assert client.logged_in is True
    assert post.kwargs["data"] == {
        "username": "user@example.com",
        "password": "secret",
        "_spring_security_remember_me": "true",
        "_csrf": "4236635a-aea4-47ba-a1e6-64c6d5988dff",
    }
    headers = post.kwargs["headers"]
    assert headers["X-CSRF-TOKEN"] == "4236635a-aea4-47ba-a1e6-64c6d5988dff"
    assert headers["X-APP-TYPE"] == "WEB"
    assert headers["X-Ajax-call"] == "true"
    assert headers["X-JSON-TYPES"] == "None"
    # token is refreshed from the map page after login
    assert client._csrf == "38724756-f55f-4ce6-af0f-13b795f84006"  # noqa: SLF001


async def test_login_failure_raises_auth_error(client: DriivzDriverClient) -> None:
    with aioresponses() as m:
        mock_login(m, success=False)
        with pytest.raises(AuthError) as excinfo:
            await client.login()
    assert excinfo.value.error_type == "BAD_CREDENTIALS"
    assert client.logged_in is False


async def test_login_without_csrf_meta_is_connection_error(client: DriivzDriverClient) -> None:
    with aioresponses() as m:
        m.get(LOGIN_URL, body="<html>maintenance</html>", content_type="text/html")
        with pytest.raises(DriivzConnectionError):
            await client.login()


async def test_request_returns_data_and_tracks_rate_limit(client: DriivzDriverClient) -> None:
    with aioresponses() as m:
        mock_login(m)
        m.post(
            WALLET_URL,
            payload=load_json_fixture("wallet.json"),
            headers={"X-Rate-Limit-Remaining": "7"},
        )
        await client.login()
        data = await client._request("POST", WALLET_PATH, data={})  # noqa: SLF001
    assert data["customerDetailId"] == 123456
    assert client.rate_limit_remaining == 7


async def test_request_maps_success_false_to_api_error(client: DriivzDriverClient) -> None:
    with aioresponses() as m:
        mock_login(m)
        m.post(WALLET_URL, payload=load_json_fixture("error_insufficient_permissions.json"))
        await client.login()
        with pytest.raises(ApiError) as excinfo:
            await client._request("POST", WALLET_PATH, data={})  # noqa: SLF001
    assert excinfo.value.error_type == "INSUFFICIENT_PERMISSIONS"
    assert "85A05724" in (excinfo.value.message_key or "")


async def test_request_relogins_once_on_403(client: DriivzDriverClient) -> None:
    with aioresponses() as m:
        mock_login(m)
        m.post(WALLET_URL, status=403)
        mock_login(m)  # second login
        m.post(WALLET_URL, payload=load_json_fixture("wallet.json"))
        await client.login()
        data = await client._request("POST", WALLET_PATH, data={})  # noqa: SLF001
        login_posts = m.requests[("POST", URL(LOGIN_URL))]
    assert data["id"] == 228000
    assert len(login_posts) == 2


async def test_request_gives_up_after_second_auth_failure(client: DriivzDriverClient) -> None:
    with aioresponses() as m:
        mock_login(m)
        m.post(WALLET_URL, status=403)
        mock_login(m)
        m.post(WALLET_URL, status=403)
        await client.login()
        with pytest.raises(AuthError):
            await client._request("POST", WALLET_PATH, data={})  # noqa: SLF001


async def test_request_html_page_is_auth_error_when_not_logged_in(
    client: DriivzDriverClient,
) -> None:
    with aioresponses() as m:
        m.post(WALLET_URL, body="<html>login</html>", content_type="text/html")
        with pytest.raises(AuthError):
            await client._request("POST", WALLET_PATH, data={})  # noqa: SLF001


async def test_request_429_is_rate_limit_error(client: DriivzDriverClient) -> None:
    with aioresponses() as m:
        mock_login(m)
        m.post(WALLET_URL, status=429)
        await client.login()
        with pytest.raises(RateLimitError):
            await client._request("POST", WALLET_PATH, data={})  # noqa: SLF001


async def test_request_network_error_is_connection_error(client: DriivzDriverClient) -> None:
    with aioresponses() as m:
        mock_login(m)
        m.post(WALLET_URL, exception=aiohttp.ClientConnectionError("boom"))
        await client.login()
        with pytest.raises(DriivzConnectionError):
            await client._request("POST", WALLET_PATH, data={})  # noqa: SLF001


async def test_json_body_sets_json_kwarg(client: DriivzDriverClient) -> None:
    url = f"{BASE}/stationFacade/findStationsByIds"
    with aioresponses() as m:
        mock_login(m)
        m.post(url, payload=load_json_fixture("stations_by_ids.json"))
        await client.login()
        await client._request(  # noqa: SLF001
            "POST", "stationFacade/findStationsByIds", json={"filterByIds": [1]}
        )
        call = m.requests[("POST", URL(url))][0]
    assert call.kwargs["json"] == {"filterByIds": [1]}
    assert call.kwargs["data"] is None


def test_form_stringifies_values() -> None:
    assert DriivzDriverClient._form({"stationSocketId": 11243, "x": "y", "n": None}) == {  # noqa: SLF001
        "stationSocketId": "11243",
        "x": "y",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `scripts/test tests/driivz/test_client_core.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'custom_components.mer.driivz.client'`.

- [ ] **Step 3: Write `driivz/client.py` (core part)**

```python
"""Async client for a Driivz driver portal (Mer UK)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import json as jsonlib
import logging
import re
from typing import Any

import aiohttp

from .const import (
    AUTH_ERROR_TYPES,
    DEFAULT_BASE_URL,
    HEADER_AJAX,
    HEADER_APP_TYPE,
    HEADER_CSRF,
    HEADER_JSON_TYPES,
    HEADER_RATE_LIMIT_REMAINING,
    PATH_LOGIN,
    PATH_MAP,
)
from .exceptions import ApiError, AuthError, DriivzConnectionError, RateLimitError

_LOGGER = logging.getLogger(__name__)

_CSRF_META_RE = re.compile(r"<meta\s+[^>]*name=[\"']_csrf[\"'][^>]*>", re.IGNORECASE)
_CONTENT_RE = re.compile(r"content=[\"']([^\"']+)[\"']", re.IGNORECASE)


def extract_csrf(html: str) -> str | None:
    """Return the `_csrf` meta token from a portal HTML page."""
    match = _CSRF_META_RE.search(html)
    if not match:
        return None
    content = _CONTENT_RE.search(match.group(0))
    return content.group(1) if content else None


def _error_type(payload: Mapping[str, Any]) -> str | None:
    errors = payload.get("errors") or []
    if errors and isinstance(errors[0], Mapping) and errors[0].get("errorType"):
        return str(errors[0]["errorType"])
    if payload.get("errorType"):
        return str(payload["errorType"])
    return None


def _message_key(payload: Mapping[str, Any]) -> str | None:
    errors = payload.get("errors") or []
    if errors and isinstance(errors[0], Mapping) and errors[0].get("messageKey"):
        return str(errors[0]["messageKey"])
    return None


class DriivzDriverClient:
    """Talks to the driver portal's JSON facades using a cookie session."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._base_url = base_url.rstrip("/")
        self._csrf: str | None = None
        self._logged_in = False
        self._login_lock = asyncio.Lock()
        self._rate_limit_remaining: int | None = None

    @property
    def logged_in(self) -> bool:
        return self._logged_in

    @property
    def rate_limit_remaining(self) -> int | None:
        return self._rate_limit_remaining

    # ----- plumbing -----------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self._base_url}/{path.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        headers = {
            HEADER_APP_TYPE: "WEB",
            HEADER_JSON_TYPES: "None",
            HEADER_AJAX: "true",
            "Accept": "application/json, text/javascript, */*",
        }
        if self._csrf:
            headers[HEADER_CSRF] = self._csrf
        return headers

    @staticmethod
    def _form(values: Mapping[str, Any]) -> dict[str, str]:
        """aiohttp form fields must be strings; drop None values."""
        return {key: str(value) for key, value in values.items() if value is not None}

    def _track_rate_limit(self, response: aiohttp.ClientResponse) -> None:
        value = response.headers.get(HEADER_RATE_LIMIT_REMAINING)
        if value is None:
            return
        try:
            self._rate_limit_remaining = int(value)
        except ValueError:
            pass

    async def _fetch_csrf(self, path: str) -> str:
        try:
            async with self._session.get(self._url(path), headers=self._headers()) as response:
                self._track_rate_limit(response)
                if response.status >= 400:
                    raise DriivzConnectionError(f"HTTP {response.status} fetching {path}")
                html = await response.text()
        except aiohttp.ClientError as err:
            raise DriivzConnectionError(str(err)) from err
        token = extract_csrf(html)
        if not token:
            raise DriivzConnectionError(f"No CSRF token found on {path}")
        self._csrf = token
        return token

    async def _send(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        data: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Perform one HTTP call and return the parsed JSON envelope."""
        try:
            async with self._session.request(
                method,
                self._url(path),
                json=json,
                data=None if data is None else self._form(data),
                params=params,
                headers=self._headers(),
            ) as response:
                self._track_rate_limit(response)
                if response.status == 429:
                    raise RateLimitError("Rate limited by portal")
                if response.status in (401, 403):
                    raise AuthError(f"HTTP_{response.status}")
                if response.status >= 400:
                    raise DriivzConnectionError(f"HTTP {response.status} for {path}")
                text = await response.text()
        except aiohttp.ClientError as err:
            raise DriivzConnectionError(str(err)) from err
        try:
            payload = jsonlib.loads(text)
        except ValueError as err:
            if "<html" in text.lower():
                raise AuthError("LOGIN_PAGE") from err
            raise DriivzConnectionError(f"Non-JSON response from {path}") from err
        if not isinstance(payload, dict):
            raise DriivzConnectionError(f"Unexpected response shape from {path}")
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        data: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> Any:
        """Call a facade, unwrap the envelope, re-login once if the session expired."""
        for attempt in (0, 1):
            try:
                payload = await self._send(method, path, json=json, data=data, params=params)
            except AuthError:
                if attempt == 1 or not self._logged_in:
                    raise
                _LOGGER.debug("Session rejected for %s, logging in again", path)
                await self.login()
                continue
            if payload.get("success", False):
                return payload.get("data")
            error_type = _error_type(payload) or "UNKNOWN_ERROR"
            if error_type in AUTH_ERROR_TYPES:
                if attempt == 1 or not self._logged_in:
                    raise AuthError(error_type)
                await self.login()
                continue
            raise ApiError(error_type, _message_key(payload))
        raise AuthError("LOGIN_FAILED")  # pragma: no cover

    # ----- authentication ------------------------------------------------

    async def login(self) -> None:
        """Authenticate with username/password and prime the CSRF token."""
        async with self._login_lock:
            self._logged_in = False
            token = await self._fetch_csrf(PATH_LOGIN)
            payload = await self._send(
                "POST",
                PATH_LOGIN,
                data={
                    "username": self._username,
                    "password": self._password,
                    "_spring_security_remember_me": "true",
                    "_csrf": token,
                },
            )
            if not payload.get("success", False):
                raise AuthError(_error_type(payload) or "LOGIN_FAILED")
            self._logged_in = True
            await self._fetch_csrf(PATH_MAP)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `scripts/test tests/driivz/test_client_core.py -q`
Expected: 13 passed.

Pitfall: aioresponses matches the exact URL; `params` must be `None` (not `{}`) when unused, otherwise aiohttp appends `?` and the mock misses.

- [ ] **Step 5: Lint and commit**

```bash
scripts/format && scripts/lint
git add -A
git commit -m "feat(driivz): client login, CSRF handling and request envelope with re-login

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Client typed methods (queries, session, commands, wallet, history)

**Files:**
- Modify: `custom_components/mer/driivz/client.py` (append methods and imports)
- Test: `tests/driivz/test_client_methods.py`

**Interfaces:**
- Consumes: `_request`, `_form` from Task 3; models from Task 2.
- Produces (all `async`, on `DriivzDriverClient`):
  - `find_sites_in_bounds(bounds: Bounds) -> list[Site]`
  - `find_stations_in_bounds(bounds: Bounds) -> list[Station]`
  - `find_stations_by_ids(ids: Iterable[int]) -> list[Station]`
  - `find_station_by_id(station_id: int, billing_plan_id: int | None = None) -> Station`
  - `find_last_active_charge_socket() -> Socket | None`
  - `find_current_transaction_start_time(socket_id: int) -> datetime | None`
  - `find_current_transaction_estimate(socket_id: int) -> SessionEstimate | None`
  - `start_charge(socket_id: int) -> None` (raises `ApiError` unless `operationStatus == "PENDING"`)
  - `stop_charge(socket_id: int) -> None`
  - `find_wallet() -> Wallet`
  - `find_transactions(customer_id: int, start: datetime, end: datetime) -> list[Transaction]` (newest first)

- [ ] **Step 1: Write the failing tests**

`tests/driivz/test_client_methods.py`:

```python
"""Tests for the typed DriivzDriverClient methods."""

from __future__ import annotations

from datetime import UTC, datetime
import re

import aiohttp
from aioresponses import aioresponses
import pytest
from yarl import URL

from custom_components.mer.driivz.client import DriivzDriverClient
from custom_components.mer.driivz.exceptions import ApiError
from custom_components.mer.driivz.models import Bounds
from tests.helpers import load_fixture, load_json_fixture

BASE = "https://driver.uk.mer.eco"


@pytest.fixture
async def client():
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as session:
        c = DriivzDriverClient(session, "user@example.com", "secret")
        with aioresponses() as m:
            m.get(f"{BASE}/login", body=load_fixture("login.html"), content_type="text/html")
            m.post(f"{BASE}/login", payload=load_json_fixture("login_success.json"))
            m.get(f"{BASE}/findCharger", body=load_fixture("map.html"), content_type="text/html")
            await c.login()
        yield c


def url(path: str) -> str:
    return f"{BASE}/{path}"


async def test_find_sites_in_bounds(client: DriivzDriverClient) -> None:
    path = "stationFacade/findSitesInBounds"
    with aioresponses() as m:
        m.post(url(path), payload=load_json_fixture("sites_in_bounds.json"))
        sites = await client.find_sites_in_bounds(Bounds.UK)
        call = m.requests[("POST", URL(url(path)))][0]
    assert [s.id for s in sites] == [2877, 3796, 1359]
    assert call.kwargs["json"] == {"filterByBounds": Bounds.UK.to_dict(), "filterByIsManaged": True}


async def test_find_stations_in_bounds(client: DriivzDriverClient) -> None:
    path = "stationFacade/findStationsInBounds"
    with aioresponses() as m:
        m.post(url(path), payload=load_json_fixture("stations_in_bounds.json"))
        stations = await client.find_stations_in_bounds(Bounds.around(54.67, -1.45))
    assert [s.id for s in stations] == [6042, 6041, 17886]
    assert stations[1].status == "CHARGING"


async def test_find_stations_by_ids(client: DriivzDriverClient) -> None:
    path = "stationFacade/findStationsByIds"
    with aioresponses() as m:
        m.post(url(path), payload=load_json_fixture("stations_by_ids.json"))
        stations = await client.find_stations_by_ids([6042, 6041])
        call = m.requests[("POST", URL(url(path)))][0]
    assert call.kwargs["json"] == {"filterByIds": [6042, 6041]}
    assert stations[0].sockets[0].status == "AVAILABLE"
    assert stations[1].sockets[0].status == "CHARGING"


async def test_find_stations_by_ids_empty_makes_no_request(client: DriivzDriverClient) -> None:
    with aioresponses() as m:
        assert await client.find_stations_by_ids([]) == []
        assert not m.requests


async def test_find_station_by_id(client: DriivzDriverClient) -> None:
    with aioresponses() as m:
        m.get(
            re.compile(re.escape(url("stationFacade/findStationById")) + r"\?.*"),
            payload=load_json_fixture("station_6042.json"),
        )
        station = await client.find_station_by_id(6042, billing_plan_id=3465)
        (key, calls), = m.requests.items()
    assert key[0] == "GET"
    assert calls[0].kwargs["params"] == {"stationId": "6042", "billingPlanId": "3465"}
    assert station.sockets[0].name == "Left"


async def test_find_station_by_id_missing_is_api_error(client: DriivzDriverClient) -> None:
    with aioresponses() as m:
        m.get(
            re.compile(re.escape(url("stationFacade/findStationById")) + r"\?.*"),
            payload={"errors": [], "success": True},
        )
        with pytest.raises(ApiError) as excinfo:
            await client.find_station_by_id(1)
    assert excinfo.value.error_type == "STATION_NOT_FOUND"


async def test_last_active_socket_idle_and_charging(client: DriivzDriverClient) -> None:
    path = "stationFacade/findLastActiveChargeSocket"
    with aioresponses() as m:
        m.post(url(path), payload=load_json_fixture("last_active_idle.json"))
        m.post(url(path), payload=load_json_fixture("last_active_charging.json"))
        assert await client.find_last_active_charge_socket() is None
        active = await client.find_last_active_charge_socket()
        call = m.requests[("POST", URL(url(path)))][0]
    assert active is not None
    assert active.id == 11241
    assert active.station_id == 6041
    assert active.status == "CHARGING"
    assert call.kwargs["json"] is None


async def test_transaction_start_time_and_estimate(client: DriivzDriverClient) -> None:
    start_path = "stationFacade/findCurrentTransactionStartTime"
    est_path = "stationFacade/findCurrentTransactionBillingChargingEstimation"
    with aioresponses() as m:
        m.post(url(start_path), payload=load_json_fixture("transaction_start_time.json"))
        m.post(url(est_path), payload=load_json_fixture("transaction_estimate.json"))
        started = await client.find_current_transaction_start_time(11241)
        estimate = await client.find_current_transaction_estimate(11241)
        start_call = m.requests[("POST", URL(url(start_path)))][0]
        est_call = m.requests[("POST", URL(url(est_path)))][0]
    assert started == datetime(2026, 9, 16, 10, 34, 26, tzinfo=UTC)
    assert start_call.kwargs["data"] == {"stationSocketId": "11241"}
    assert est_call.kwargs["data"] == {"socketId": "11241"}
    assert estimate is not None
    assert estimate.energy_kwh == 12.345
    assert estimate.cost == 0


async def test_estimate_without_data_is_none(client: DriivzDriverClient) -> None:
    est_path = "stationFacade/findCurrentTransactionBillingChargingEstimation"
    with aioresponses() as m:
        m.post(url(est_path), payload={"errors": [], "success": True})
        assert await client.find_current_transaction_estimate(1) is None


async def test_start_charge_pending_ok(client: DriivzDriverClient) -> None:
    path = "stationFacade/startChargeNow"
    with aioresponses() as m:
        m.post(url(path), payload=load_json_fixture("start_charge_pending.json"))
        await client.start_charge(11243)
        call = m.requests[("POST", URL(url(path)))][0]
    assert call.kwargs["data"] == {"stationSocketId": "11243"}


async def test_start_charge_rejected_raises(client: DriivzDriverClient) -> None:
    path = "stationFacade/startChargeNow"
    with aioresponses() as m:
        m.post(url(path), payload=load_json_fixture("start_charge_rejected.json"))
        with pytest.raises(ApiError) as excinfo:
            await client.start_charge(11243)
    assert excinfo.value.error_type == "REJECTED"


async def test_start_charge_envelope_error_raises(client: DriivzDriverClient) -> None:
    path = "stationFacade/startChargeNow"
    with aioresponses() as m:
        m.post(url(path), payload=load_json_fixture("error_insufficient_permissions.json"))
        with pytest.raises(ApiError) as excinfo:
            await client.start_charge(11243)
    assert excinfo.value.error_type == "INSUFFICIENT_PERMISSIONS"


async def test_stop_charge(client: DriivzDriverClient) -> None:
    path = "stationFacade/stopCharge"
    with aioresponses() as m:
        m.post(url(path), payload={"errors": [], "success": True})
        m.post(url(path), payload=load_json_fixture("start_charge_rejected.json"))
        await client.stop_charge(11241)
        with pytest.raises(ApiError):
            await client.stop_charge(11241)
        call = m.requests[("POST", URL(url(path)))][0]
    assert call.kwargs["data"] == {"stationSocketId": "11241"}


async def test_find_wallet(client: DriivzDriverClient) -> None:
    path = "billingFacade/findCustomerDetailWalletByCustomerId"
    with aioresponses() as m:
        m.post(url(path), payload=load_json_fixture("wallet.json"))
        wallet = await client.find_wallet()
    assert wallet.customer_id == 123456
    assert wallet.balance == 12.5


async def test_find_transactions_sorted_newest_first(client: DriivzDriverClient) -> None:
    path = "customerFacade/findDriverChargeTransactionLogByView"
    raw = load_json_fixture("transactions.json")
    raw["data"].reverse()  # oldest first from the server
    start = datetime(2026, 8, 17, tzinfo=UTC)
    end = datetime(2026, 9, 17, tzinfo=UTC)
    with aioresponses() as m:
        m.post(url(path), payload=raw)
        txs = await client.find_transactions(123456, start, end)
        call = m.requests[("POST", URL(url(path)))][0]
    assert [t.id for t in txs] == [9084600, 9075809]
    assert call.kwargs["json"] == {
        "filterByStartedOnFrom": int(start.timestamp() * 1000),
        "filterByStartedOnTo": int(end.timestamp() * 1000),
        "filterByMemberId": 123456,
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `scripts/test tests/driivz/test_client_methods.py -q`
Expected: FAIL with `AttributeError: 'DriivzDriverClient' object has no attribute 'find_sites_in_bounds'` (and similar).

- [ ] **Step 3: Add imports and methods to `driivz/client.py`**

Extend the import block:

```python
from collections.abc import Iterable, Mapping
from datetime import datetime
```

```python
from .const import (
    AUTH_ERROR_TYPES,
    DEFAULT_BASE_URL,
    HEADER_AJAX,
    HEADER_APP_TYPE,
    HEADER_CSRF,
    HEADER_JSON_TYPES,
    HEADER_RATE_LIMIT_REMAINING,
    OPERATION_PENDING,
    PATH_FIND_SITES_IN_BOUNDS,
    PATH_FIND_STATION_BY_ID,
    PATH_FIND_STATIONS_BY_IDS,
    PATH_FIND_STATIONS_IN_BOUNDS,
    PATH_LAST_ACTIVE_SOCKET,
    PATH_LOGIN,
    PATH_MAP,
    PATH_START_CHARGE,
    PATH_STOP_CHARGE,
    PATH_TRANSACTION_ESTIMATE,
    PATH_TRANSACTION_START_TIME,
    PATH_TRANSACTIONS,
    PATH_WALLET,
)
from .models import (
    Bounds,
    SessionEstimate,
    Site,
    Socket,
    Station,
    Transaction,
    Wallet,
    parse_start_time,
)
```

Add a module-level helper after `_message_key`:

```python
def _to_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)
```

Append these methods to the class (after `login`):

```python
    # ----- stations and sites -------------------------------------------

    async def find_sites_in_bounds(self, bounds: Bounds) -> list[Site]:
        data = await self._request(
            "POST",
            PATH_FIND_SITES_IN_BOUNDS,
            json={"filterByBounds": bounds.to_dict(), "filterByIsManaged": True},
        )
        return [Site.from_dict(item) for item in data or []]

    async def find_stations_in_bounds(self, bounds: Bounds) -> list[Station]:
        data = await self._request(
            "POST",
            PATH_FIND_STATIONS_IN_BOUNDS,
            json={"filterByBounds": bounds.to_dict(), "filterByIsManaged": True},
        )
        return [Station.from_dict(item) for item in data or []]

    async def find_stations_by_ids(self, ids: Iterable[int]) -> list[Station]:
        id_list = [int(i) for i in ids]
        if not id_list:
            return []
        data = await self._request(
            "POST", PATH_FIND_STATIONS_BY_IDS, json={"filterByIds": id_list}
        )
        return [Station.from_dict(item) for item in data or []]

    async def find_station_by_id(
        self, station_id: int, billing_plan_id: int | None = None
    ) -> Station:
        params = {"stationId": str(station_id)}
        if billing_plan_id is not None:
            params["billingPlanId"] = str(billing_plan_id)
        data = await self._request("GET", PATH_FIND_STATION_BY_ID, params=params)
        if not isinstance(data, Mapping):
            raise ApiError("STATION_NOT_FOUND")
        return Station.from_dict(data)

    # ----- charging session ---------------------------------------------

    async def find_last_active_charge_socket(self) -> Socket | None:
        data = await self._request("POST", PATH_LAST_ACTIVE_SOCKET, data={})
        if isinstance(data, Mapping) and data.get("id") is not None:
            return Socket.from_dict(data)
        return None

    async def find_current_transaction_start_time(self, socket_id: int) -> datetime | None:
        data = await self._request(
            "POST", PATH_TRANSACTION_START_TIME, data={"stationSocketId": socket_id}
        )
        return parse_start_time(data)

    async def find_current_transaction_estimate(self, socket_id: int) -> SessionEstimate | None:
        data = await self._request("POST", PATH_TRANSACTION_ESTIMATE, data={"socketId": socket_id})
        if isinstance(data, Mapping):
            return SessionEstimate.from_dict(data)
        return None

    async def start_charge(self, socket_id: int) -> None:
        """Ask the portal to start charging; the charger then waits for the cable."""
        data = await self._request("POST", PATH_START_CHARGE, data={"stationSocketId": socket_id})
        status = data.get("operationStatus") if isinstance(data, Mapping) else None
        if status != OPERATION_PENDING:
            raise ApiError(str(status) if status else "START_CHARGE_REJECTED")

    async def stop_charge(self, socket_id: int) -> None:
        data = await self._request("POST", PATH_STOP_CHARGE, data={"stationSocketId": socket_id})
        status = data.get("operationStatus") if isinstance(data, Mapping) else None
        if status is not None and status != OPERATION_PENDING:
            raise ApiError(str(status))

    # ----- account -------------------------------------------------------

    async def find_wallet(self) -> Wallet:
        data = await self._request("POST", PATH_WALLET, data={})
        if not isinstance(data, Mapping):
            raise ApiError("WALLET_NOT_FOUND")
        return Wallet.from_dict(data)

    async def find_transactions(
        self, customer_id: int, start: datetime, end: datetime
    ) -> list[Transaction]:
        data = await self._request(
            "POST",
            PATH_TRANSACTIONS,
            json={
                "filterByStartedOnFrom": _to_ms(start),
                "filterByStartedOnTo": _to_ms(end),
                "filterByMemberId": customer_id,
            },
        )
        transactions = [Transaction.from_dict(item) for item in data or []]
        transactions.sort(
            key=lambda t: t.started_at.timestamp() if t.started_at else 0.0, reverse=True
        )
        return transactions
```

- [ ] **Step 4: Run all client tests**

Run: `scripts/test tests/driivz -q`
Expected: all pass (models 13, core 13, methods 15).

- [ ] **Step 5: Lint and commit**

```bash
scripts/format && scripts/lint
git add -A
git commit -m "feat(driivz): typed station, session, command, wallet and history methods

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Client factory, coordinator and integration setup

**Files:**
- Create: `custom_components/mer/api.py`, `custom_components/mer/coordinator.py`
- Modify: `custom_components/mer/__init__.py` (replace placeholder)
- Modify: `tests/conftest.py` (add `mock_client`, `mock_config_entry`)
- Modify: `tests/helpers.py` (add `setup_integration`, `station_from_fixture`)
- Test: `tests/test_init.py`, `tests/test_coordinator.py`

**Interfaces:**
- Consumes: `DriivzDriverClient` (Tasks 3–4), models, exceptions, `const.py` (Task 1).
- Produces:
  - `api.create_client(hass, username, password, base_url) -> DriivzDriverClient`
  - `coordinator.ActiveSession(socket_id, station_id, started_at, energy_kwh, cost, currency)`
  - `coordinator.MerData(stations, details, active, wallet, last_transaction, customer_id)`
  - `coordinator.MerCoordinator(hass, entry, client)` with attributes `client`, `station_ids: list[int]`, `site_id: int | None`, `site_name: str`, methods `get_station(station_id) -> Station | None`, `get_socket(station_id, socket_id) -> Socket | None`, `configured_stations() -> list[Station]`, `schedule_refresh() -> None`
  - `coordinator.MerConfigEntry = ConfigEntry[MerCoordinator]`
  - `tests.conftest.mock_client` fixture (a `MagicMock` with `AsyncMock` methods returning fixture-derived models), `tests.conftest.mock_config_entry` fixture, `tests.helpers.setup_integration(hass, entry) -> None`.

- [ ] **Step 1: Extend test helpers and conftest**

Append to `tests/helpers.py`:

```python
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mer.driivz.models import Station


def station_from_fixture(name: str) -> Station:
    """Build a Station from a `findStationById` fixture."""
    return Station.from_dict(load_json_fixture(name)["data"])


def stations_from_fixture(name: str) -> list[Station]:
    """Build Stations from a list-returning fixture."""
    return [Station.from_dict(item) for item in load_json_fixture(name)["data"]]


async def setup_integration(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add the entry to hass and set it up."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
```

Replace `tests/conftest.py` with:

```python
"""Pytest configuration for the Mer integration tests."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mer.const import (
    CONF_BASE_URL,
    CONF_SCAN_INTERVAL,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    CONF_STATION_IDS,
    DEFAULT_BASE_URL,
    DOMAIN,
)
from custom_components.mer.driivz.models import (
    SessionEstimate,
    Site,
    Socket,
    Transaction,
    Wallet,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from tests.helpers import load_json_fixture, station_from_fixture, stations_from_fixture

SITE_NAME = "Durham County Council - Business Durham NETPark"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom integrations in every test."""
    return


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A configured entry monitoring Explorer 1 and 2."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"Mer – {SITE_NAME}",
        unique_id="user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "secret",
            CONF_BASE_URL: DEFAULT_BASE_URL,
        },
        options={
            CONF_SITE_ID: 2877,
            CONF_SITE_NAME: SITE_NAME,
            CONF_STATION_IDS: [6042, 6041],
            CONF_SCAN_INTERVAL: 60,
        },
    )


def _details() -> dict[int, object]:
    return {
        6042: station_from_fixture("station_6042.json"),
        6041: station_from_fixture("station_6041.json"),
        17886: station_from_fixture("station_17886.json"),
    }


@pytest.fixture
def mock_client() -> Generator[MagicMock]:
    """Patch the Driivz client class so create_client returns this mock."""
    details = _details()
    with patch("custom_components.mer.api.DriivzDriverClient") as cls:
        client = cls.return_value
        client.rate_limit_remaining = 9
        client.logged_in = True
        client.login = AsyncMock()
        client.find_sites_in_bounds = AsyncMock(
            return_value=[Site.from_dict(s) for s in load_json_fixture("sites_in_bounds.json")["data"]]
        )
        client.find_stations_in_bounds = AsyncMock(
            return_value=stations_from_fixture("stations_in_bounds.json")
        )
        client.find_stations_by_ids = AsyncMock(
            return_value=stations_from_fixture("stations_by_ids.json")
        )
        client.find_station_by_id = AsyncMock(
            side_effect=lambda station_id, billing_plan_id=None: details[station_id]
        )
        client.find_last_active_charge_socket = AsyncMock(return_value=None)
        client.find_current_transaction_start_time = AsyncMock(
            return_value=datetime(2026, 9, 16, 10, 34, 26, tzinfo=UTC)
        )
        client.find_current_transaction_estimate = AsyncMock(
            return_value=SessionEstimate(energy_kwh=12.345, cost=0.0, currency="GBP")
        )
        client.start_charge = AsyncMock()
        client.stop_charge = AsyncMock()
        client.find_wallet = AsyncMock(
            return_value=Wallet.from_dict(load_json_fixture("wallet.json")["data"])
        )
        client.find_transactions = AsyncMock(
            return_value=[
                Transaction.from_dict(t) for t in load_json_fixture("transactions.json")["data"]
            ]
        )
        yield client


@pytest.fixture
def charging_socket() -> Socket:
    """The socket DTO returned while a charge is running."""
    return Socket.from_dict(load_json_fixture("last_active_charging.json")["data"])
```

- [ ] **Step 2: Write the failing tests**

`tests/test_init.py`:

```python
"""Tests for integration setup and unload."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mer import async_remove_config_entry_device
from custom_components.mer.const import DOMAIN
from custom_components.mer.driivz.exceptions import AuthError, DriivzConnectionError
from tests.helpers import setup_integration


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


async def test_devices_created_and_stale_station_removable(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    registry = dr.async_get(hass)
    site = registry.async_get_device(identifiers={(DOMAIN, "site_2877")})
    station = registry.async_get_device(identifiers={(DOMAIN, "station_6042")})
    account = registry.async_get_device(
        identifiers={(DOMAIN, f"account_{mock_config_entry.entry_id}")}
    )
    assert site is not None and site.name == "Durham County Council - Business Durham NETPark"
    assert station is not None
    assert station.name == "Business Durham - NETPark 3 - Explorer 1"
    assert station.model == "Eve Double Pro-line"
    assert station.via_device_id == site.id
    assert account is not None

    stale = registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id, identifiers={(DOMAIN, "station_999")}
    )
    assert await async_remove_config_entry_device(hass, mock_config_entry, stale) is True
    assert await async_remove_config_entry_device(hass, mock_config_entry, station) is False
    assert await async_remove_config_entry_device(hass, mock_config_entry, site) is False
```

`tests/test_coordinator.py`:

```python
"""Tests for MerCoordinator polling behaviour."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.mer.driivz.exceptions import (
    AuthError,
    DriivzConnectionError,
    RateLimitError,
)
from custom_components.mer.driivz.models import Socket
from tests.helpers import setup_integration


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_idle_cycle_request_budget(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, mock_config_entry)
    # first refresh: stations, active, wallet+history, 2 details
    assert mock_client.find_stations_by_ids.await_count == 1
    assert mock_client.find_last_active_charge_socket.await_count == 1
    assert mock_client.find_wallet.await_count == 1
    assert mock_client.find_transactions.await_count == 1
    assert mock_client.find_station_by_id.await_count == 2
    assert mock_client.find_current_transaction_estimate.await_count == 0

    await _tick(hass, freezer, 61)
    assert mock_client.find_stations_by_ids.await_count == 2
    assert mock_client.find_last_active_charge_socket.await_count == 2
    assert mock_client.find_wallet.await_count == 1  # not due yet
    assert mock_client.find_station_by_id.await_count == 2  # not due yet

    await _tick(hass, freezer, 15 * 60)
    assert mock_client.find_wallet.await_count == 2
    assert mock_client.find_transactions.await_count == 2
    assert mock_client.find_station_by_id.await_count == 2

    await _tick(hass, freezer, 60 * 60)
    assert mock_client.find_station_by_id.await_count == 4


async def test_charging_cycle_fetches_session(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket: Socket,
) -> None:
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data
    active = coordinator.data.active
    assert active is not None
    assert active.socket_id == 11241
    assert active.station_id == 6041
    assert active.energy_kwh == 12.345
    assert active.cost == 0.0
    assert active.started_at is not None
    mock_client.find_current_transaction_start_time.assert_awaited_once_with(11241)
    mock_client.find_current_transaction_estimate.assert_awaited_once_with(11241)


async def test_rate_limit_low_skips_next_cycle(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, mock_config_entry)
    mock_client.rate_limit_remaining = 1
    await _tick(hass, freezer, 61)  # this cycle runs and observes remaining=1
    assert mock_client.find_stations_by_ids.await_count == 2
    await _tick(hass, freezer, 61)  # skipped
    assert mock_client.find_stations_by_ids.await_count == 2
    mock_client.rate_limit_remaining = 9
    await _tick(hass, freezer, 61)  # resumes
    assert mock_client.find_stations_by_ids.await_count == 3
    assert mock_config_entry.runtime_data.last_update_success is True


async def test_rate_limit_error_marks_failed_then_skips(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, mock_config_entry)
    mock_client.find_stations_by_ids.side_effect = RateLimitError("429")
    await _tick(hass, freezer, 61)
    coordinator = mock_config_entry.runtime_data
    assert coordinator.last_update_success is False
    mock_client.find_stations_by_ids.side_effect = None
    await _tick(hass, freezer, 61)  # skipped cycle, still failed
    assert mock_client.find_stations_by_ids.await_count == 2
    await _tick(hass, freezer, 61)
    assert coordinator.last_update_success is True


async def test_connection_error_is_update_failed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, mock_config_entry)
    mock_client.find_stations_by_ids.side_effect = DriivzConnectionError("boom")
    await _tick(hass, freezer, 61)
    assert mock_config_entry.runtime_data.last_update_success is False


async def test_auth_error_during_poll_starts_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, mock_config_entry)
    mock_client.find_stations_by_ids.side_effect = AuthError("SESSION_EXPIRED")
    await _tick(hass, freezer, 61)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"].get("source") == "reauth" for f in flows)


async def test_get_station_merges_detail_and_live(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data
    station = coordinator.get_station(6041)
    assert station is not None
    assert station.model_name == "Eve Double Pro-line"
    assert station.status == "CHARGING"
    socket = coordinator.get_socket(6041, 11241)
    assert socket is not None and socket.name == "Left" and socket.status == "CHARGING"
    assert coordinator.get_socket(6041, 1) is None
    assert coordinator.get_station(999) is None
    assert [s.id for s in coordinator.configured_stations()] == [6042, 6041]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `scripts/test tests/test_init.py tests/test_coordinator.py -q`
Expected: FAIL with `ImportError` (no `api`/`coordinator` module, no `async_remove_config_entry_device`).

- [ ] **Step 4: Write `api.py`**

```python
"""Factory for the Driivz client (single patch point for tests)."""

from __future__ import annotations

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .driivz.client import DriivzDriverClient


def create_client(
    hass: HomeAssistant, username: str, password: str, base_url: str
) -> DriivzDriverClient:
    """Create a client with its own cookie jar so sessions never leak between entries."""
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
    return DriivzDriverClient(session, username, password, base_url=base_url)
```

- [ ] **Step 5: Write `coordinator.py`**

```python
"""Data update coordinator for the Mer integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_SCAN_INTERVAL,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    CONF_STATION_IDS,
    DEFAULT_SCAN_INTERVAL,
    DETAIL_REFRESH,
    DOMAIN,
    HISTORY_LOOKBACK,
    RATE_LIMIT_SKIP_THRESHOLD,
    RATE_LIMIT_WARN_INTERVAL,
    REFRESH_AFTER_COMMAND_SECONDS,
    WALLET_REFRESH,
)
from .driivz.client import DriivzDriverClient
from .driivz.exceptions import AuthError, DriivzError, RateLimitError
from .driivz.models import Socket, Station, Transaction, Wallet

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ActiveSession:
    """The customer's running charge, if any."""

    socket_id: int
    station_id: int | None
    started_at: datetime | None
    energy_kwh: float | None
    cost: float | None
    currency: str | None


@dataclass(slots=True)
class MerData:
    """Everything the entities read."""

    stations: dict[int, Station] = field(default_factory=dict)
    details: dict[int, Station] = field(default_factory=dict)
    active: ActiveSession | None = None
    wallet: Wallet | None = None
    last_transaction: Transaction | None = None
    customer_id: int | None = None


class MerCoordinator(DataUpdateCoordinator[MerData]):
    """Polls the Driivz portal for the configured chargers and the account."""

    config_entry: MerConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: MerConfigEntry, client: DriivzDriverClient
    ) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.station_ids: list[int] = [int(i) for i in entry.options.get(CONF_STATION_IDS, [])]
        self.site_id: int | None = entry.options.get(CONF_SITE_ID)
        self.site_name: str = entry.options.get(CONF_SITE_NAME) or "Mer site"
        self._details: dict[int, Station] = {}
        self._wallet: Wallet | None = None
        self._last_transaction: Transaction | None = None
        self._customer_id: int | None = None
        self._details_refreshed: datetime | None = None
        self._wallet_refreshed: datetime | None = None
        self._skip_next = False
        self._rate_warned_at: datetime | None = None

    # ----- lifecycle -----------------------------------------------------

    async def _async_setup(self) -> None:
        try:
            await self.client.login()
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except DriivzError as err:
            raise UpdateFailed(f"Login failed: {err}") from err

    async def _async_update_data(self) -> MerData:
        if self._skip_next:
            self._skip_next = False
            if self.data is not None:
                _LOGGER.debug("Skipping one poll to respect the portal rate limit")
                return self.data
        now = dt_util.utcnow()
        try:
            stations = {
                s.id: s for s in await self.client.find_stations_by_ids(self.station_ids)
            }
            active = await self._fetch_active()
            if self._due(self._wallet_refreshed, WALLET_REFRESH, now):
                await self._refresh_account(now)
            if self._due(self._details_refreshed, DETAIL_REFRESH, now):
                await self._refresh_details(now)
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except RateLimitError as err:
            self._skip_next = True
            raise UpdateFailed(f"Rate limited: {err}") from err
        except DriivzError as err:
            raise UpdateFailed(str(err)) from err
        self._check_rate_limit(now)
        return MerData(
            stations=stations,
            details=dict(self._details),
            active=active,
            wallet=self._wallet,
            last_transaction=self._last_transaction,
            customer_id=self._customer_id,
        )

    @staticmethod
    def _due(last: datetime | None, every: timedelta, now: datetime) -> bool:
        return last is None or now - last >= every

    async def _fetch_active(self) -> ActiveSession | None:
        socket = await self.client.find_last_active_charge_socket()
        if socket is None:
            return None
        started = await self.client.find_current_transaction_start_time(socket.id)
        estimate = await self.client.find_current_transaction_estimate(socket.id)
        return ActiveSession(
            socket_id=socket.id,
            station_id=socket.station_id,
            started_at=started,
            energy_kwh=estimate.energy_kwh if estimate else None,
            cost=estimate.cost if estimate else None,
            currency=(estimate.currency if estimate else None)
            or (self._wallet.currency if self._wallet else None),
        )

    async def _refresh_account(self, now: datetime) -> None:
        self._wallet = await self.client.find_wallet()
        self._customer_id = self._wallet.customer_id
        if self._customer_id is not None:
            transactions = await self.client.find_transactions(
                self._customer_id, now - HISTORY_LOOKBACK, now
            )
            self._last_transaction = transactions[0] if transactions else None
        self._wallet_refreshed = now

    async def _refresh_details(self, now: datetime) -> None:
        for station_id in self.station_ids:
            self._details[station_id] = await self.client.find_station_by_id(station_id)
        self._details_refreshed = now

    def _check_rate_limit(self, now: datetime) -> None:
        remaining = self.client.rate_limit_remaining
        if remaining is None or remaining > RATE_LIMIT_SKIP_THRESHOLD:
            return
        self._skip_next = True
        if self._rate_warned_at is None or now - self._rate_warned_at >= RATE_LIMIT_WARN_INTERVAL:
            _LOGGER.warning(
                "Mer portal rate limit nearly exhausted (%s remaining); skipping next poll",
                remaining,
            )
            self._rate_warned_at = now

    # ----- accessors -----------------------------------------------------

    def get_station(self, station_id: int) -> Station | None:
        """Return the station with detail fields and live statuses merged."""
        if self.data is None:
            return None
        live = self.data.stations.get(station_id)
        detail = self.data.details.get(station_id)
        if live is None:
            return None
        return detail.with_live(live) if detail else live

    def get_socket(self, station_id: int, socket_id: int) -> Socket | None:
        station = self.get_station(station_id)
        if station is None:
            return None
        return next((s for s in station.sockets if s.id == socket_id), None)

    def configured_stations(self) -> list[Station]:
        """Configured stations that are present in the latest data."""
        stations = (self.get_station(sid) for sid in self.station_ids)
        return [s for s in stations if s is not None]

    def schedule_refresh(self) -> None:
        """Refresh shortly after a start/stop command."""

        async def _refresh(_now: datetime) -> None:
            await self.async_request_refresh()

        async_call_later(self.hass, REFRESH_AFTER_COMMAND_SECONDS, _refresh)


type MerConfigEntry = ConfigEntry[MerCoordinator]
```

- [ ] **Step 6: Write `__init__.py`**

```python
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
```

- [ ] **Step 7: Create a minimal `sensor.py`, `binary_sensor.py`, `button.py` so platform setup succeeds**

Each file, for now:

```python
"""<Platform> platform for Mer (filled in later tasks)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MerConfigEntry


async def async_setup_entry(
    hass: HomeAssistant, entry: MerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up entities (none yet)."""
```

The device-registry test (`test_devices_created_and_stale_station_removable`) needs devices; devices are created by entities in Tasks 7–10. Mark that single test `@pytest.mark.xfail(reason="entities added in Task 7", strict=True)` now and remove the marker in Task 7.

- [ ] **Step 8: Run tests**

Run: `scripts/test tests/test_init.py tests/test_coordinator.py -q`
Expected: all pass except the xfail. If `freezer` is missing, install `pytest-freezer` (it ships with pytest-homeassistant-custom-component; check `pip show pytest-freezer`).

Pitfalls:
- `async_fire_time_changed(hass)` without a time argument fires at the frozen "now"; always `freezer.tick(...)` first.
- The coordinator only polls when the entry has listeners or `always_update`; `async_config_entry_first_refresh` plus platform entities keep it scheduled. If polling does not fire in `test_idle_cycle_request_budget`, add a listener in the test: `mock_config_entry.runtime_data.async_add_listener(lambda: None)` right after setup.

- [ ] **Step 9: Lint and commit**

```bash
scripts/format && scripts/lint
git add -A
git commit -m "feat: coordinator, client factory and config entry setup

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Config flow (credentials, site search, charger selection), reauth and options

**Files:**
- Create: `custom_components/mer/config_flow.py`, `custom_components/mer/strings.json`, `custom_components/mer/translations/en.json`
- Test: `tests/test_config_flow.py`

**Interfaces:**
- Consumes: `api.create_client`, `DriivzDriverClient.login/find_sites_in_bounds/find_stations_in_bounds/find_station_by_id`, `Bounds`, exceptions, `const.py`.
- Produces: `MerConfigFlow` (steps `user`, `site`, `site_select`, `stations`, `reauth_confirm`), `MerOptionsFlow` (steps `init` menu, `interval`, `site`, `site_select`, `stations`). Entry `data` keys: `username`, `password`, `base_url`. Entry `options` keys: `site_id` (int), `site_name` (str), `station_ids` (list[int]), `scan_interval` (int).

- [ ] **Step 1: Write the failing tests**

`tests/test_config_flow.py`:

```python
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
    assert result["title"] == "Mer – Durham County Council - Business Durham NETPark"
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
    assert result["data_schema"]({})[CONF_SEARCH] == "Durham County Council - Business Durham NETPark"
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `scripts/test tests/test_config_flow.py -q`
Expected: FAIL (`UnknownHandler` / no config flow registered).

- [ ] **Step 3: Write `config_flow.py`**

```python
"""Config flow for the Mer EV Charging integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import create_client
from .const import (
    CONF_BASE_URL,
    CONF_SCAN_INTERVAL,
    CONF_SEARCH,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    CONF_STATION_IDS,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .driivz.client import DriivzDriverClient
from .driivz.exceptions import AuthError, DriivzError
from .driivz.models import Bounds, Site, Station

_LOGGER = logging.getLogger(__name__)

MAX_SITE_MATCHES = 25

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)
REAUTH_SCHEMA = vol.Schema(
    {vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
)


async def _login(hass: HomeAssistant, username: str, password: str, base_url: str) -> DriivzDriverClient:
    """Create a client and log in; raises AuthError / DriivzError."""
    client = create_client(hass, username, password, base_url)
    await client.login()
    return client


async def _find_site_stations(client: DriivzDriverClient, site: Site) -> list[Station]:
    """Stations at a site: search around its coordinates, keep those whose detail matches."""
    if site.latitude is None or site.longitude is None:
        return []
    candidates = await client.find_stations_in_bounds(Bounds.around(site.latitude, site.longitude))
    stations: list[Station] = []
    for candidate in candidates:
        detail = await client.find_station_by_id(candidate.id)
        if detail.site_id == site.id:
            stations.append(detail)
    stations.sort(key=lambda s: s.display_name)
    return stations


class _SiteStationsMixin:
    """Shared site-search and charger-selection steps for config and options flows."""

    hass: HomeAssistant
    _client: DriivzDriverClient
    _sites: dict[int, Site]
    _site: Site | None
    _candidates: list[Station]

    def _init_site_state(self) -> None:
        self._sites = {}
        self._site = None
        self._candidates = []

    def _default_search(self) -> str:
        return ""

    def _preselected_station_ids(self) -> list[int]:
        return []

    async def _finish(self, site: Site, station_ids: list[int]) -> ConfigFlowResult:
        raise NotImplementedError

    async def async_step_site(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            query = user_input[CONF_SEARCH].strip().lower()
            try:
                sites = await self._client.find_sites_in_bounds(Bounds.UK)
            except DriivzError:
                errors["base"] = "cannot_connect"
            else:
                matches = [s for s in sites if query in s.name.lower()][:MAX_SITE_MATCHES]
                if not matches:
                    errors["base"] = "no_sites"
                else:
                    self._sites = {s.id: s for s in matches}
                    return await self.async_step_site_select()
        schema = vol.Schema(
            {vol.Required(CONF_SEARCH, default=self._default_search()): TextSelector()}
        )
        return self.async_show_form(step_id="site", data_schema=schema, errors=errors)  # type: ignore[attr-defined]

    async def async_step_site_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._site = self._sites[int(user_input[CONF_SITE_ID])]
            self._candidates = []
            return await self.async_step_stations()
        options = [
            SelectOptionDict(
                value=str(site.id),
                label=f"{site.name} ({site.socket_count} sockets, {site.status.lower()})",
            )
            for site in self._sites.values()
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_SITE_ID): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                )
            }
        )
        return self.async_show_form(step_id="site_select", data_schema=schema)  # type: ignore[attr-defined]

    async def async_step_stations(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._site is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            ids = [int(value) for value in user_input[CONF_STATION_IDS]]
            if ids:
                return await self._finish(self._site, ids)
            errors["base"] = "no_stations_selected"
        if not self._candidates:
            try:
                self._candidates = await _find_site_stations(self._client, self._site)
            except DriivzError:
                return self.async_abort(reason="cannot_connect")  # type: ignore[attr-defined]
            if not self._candidates:
                return self.async_abort(reason="no_stations")  # type: ignore[attr-defined]
        candidate_ids = {s.id for s in self._candidates}
        preselected = [
            str(sid) for sid in self._preselected_station_ids() if sid in candidate_ids
        ] or [str(s.id) for s in self._candidates]
        options = [
            SelectOptionDict(value=str(s.id), label=s.display_name) for s in self._candidates
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_STATION_IDS, default=preselected): SelectSelector(
                    SelectSelectorConfig(
                        options=options, multiple=True, mode=SelectSelectorMode.LIST
                    )
                )
            }
        )
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="stations",
            data_schema=schema,
            errors=errors,
            description_placeholders={"site": self._site.name},
        )


class MerConfigFlow(_SiteStationsMixin, ConfigFlow, domain=DOMAIN):
    """Handle the initial setup and reauthentication."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._init_site_state()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MerOptionsFlow:
        return MerOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
            self._abort_if_unique_id_configured()
            try:
                self._client = await _login(
                    self.hass, user_input[CONF_USERNAME], user_input[CONF_PASSWORD], DEFAULT_BASE_URL
                )
            except AuthError:
                errors["base"] = "invalid_auth"
            except DriivzError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during Mer login")
                errors["base"] = "unknown"
            else:
                self._data = {
                    CONF_USERNAME: user_input[CONF_USERNAME],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_BASE_URL: DEFAULT_BASE_URL,
                }
                return await self.async_step_site()
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def _finish(self, site: Site, station_ids: list[int]) -> ConfigFlowResult:
        return self.async_create_entry(
            title=f"Mer – {site.name}",
            data=self._data,
            options={
                CONF_SITE_ID: site.id,
                CONF_SITE_NAME: site.name,
                CONF_STATION_IDS: station_ids,
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
            },
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await _login(
                    self.hass,
                    entry.data[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
                )
            except AuthError:
                errors["base"] = "invalid_auth"
            except DriivzError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={CONF_USERNAME: entry.data[CONF_USERNAME]},
        )


class MerOptionsFlow(_SiteStationsMixin, OptionsFlow):
    """Change the poll interval or the monitored chargers."""

    def __init__(self) -> None:
        self._init_site_state()

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["interval", "site"])

    async def async_step_interval(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={**self.config_entry.options, CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )
        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                )
            }
        )
        return self.async_show_form(step_id="interval", data_schema=schema)

    def _default_search(self) -> str:
        return self.config_entry.options.get(CONF_SITE_NAME, "")

    def _preselected_station_ids(self) -> list[int]:
        return [int(i) for i in self.config_entry.options.get(CONF_STATION_IDS, [])]

    async def async_step_site(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if not hasattr(self, "_client"):
            data = self.config_entry.data
            try:
                self._client = await _login(
                    self.hass,
                    data[CONF_USERNAME],
                    data[CONF_PASSWORD],
                    data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
                )
            except AuthError:
                return self.async_abort(reason="invalid_auth")
            except DriivzError:
                return self.async_abort(reason="cannot_connect")
        return await super().async_step_site(user_input)

    async def _finish(self, site: Site, station_ids: list[int]) -> ConfigFlowResult:
        return self.async_create_entry(
            data={
                **self.config_entry.options,
                CONF_SITE_ID: site.id,
                CONF_SITE_NAME: site.name,
                CONF_STATION_IDS: station_ids,
            }
        )
```

- [ ] **Step 4: Write `strings.json` and copy to `translations/en.json`**

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Sign in to Mer",
        "description": "Use the e-mail and password of your Mer Connect / Driver Portal account.",
        "data": {
          "username": "E-mail",
          "password": "Password"
        }
      },
      "site": {
        "title": "Find your charging site",
        "description": "Type part of the site name as shown in the Mer app, e.g. NETPark.",
        "data": {
          "search": "Site name"
        }
      },
      "site_select": {
        "title": "Choose the site",
        "data": {
          "site_id": "Site"
        }
      },
      "stations": {
        "title": "Choose the chargers to monitor",
        "description": "Chargers at {site}. Each selected charger becomes a device.",
        "data": {
          "station_ids": "Chargers"
        }
      },
      "reauth_confirm": {
        "title": "Re-authenticate Mer",
        "description": "The password for {username} no longer works. Enter the new password.",
        "data": {
          "password": "Password"
        }
      }
    },
    "error": {
      "invalid_auth": "Invalid e-mail or password",
      "cannot_connect": "Could not reach the Mer driver portal",
      "no_sites": "No site matched that name",
      "no_stations_selected": "Select at least one charger",
      "unknown": "Unexpected error"
    },
    "abort": {
      "already_configured": "This Mer account is already configured",
      "reauth_successful": "Re-authentication was successful",
      "cannot_connect": "Could not reach the Mer driver portal",
      "no_stations": "No chargers were found at that site"
    }
  },
  "options": {
    "step": {
      "init": {
        "title": "Mer options",
        "menu_options": {
          "interval": "Change the poll interval",
          "site": "Change site or chargers"
        }
      },
      "interval": {
        "title": "Poll interval",
        "data": {
          "scan_interval": "Seconds between updates"
        }
      },
      "site": {
        "title": "Find your charging site",
        "data": {
          "search": "Site name"
        }
      },
      "site_select": {
        "title": "Choose the site",
        "data": {
          "site_id": "Site"
        }
      },
      "stations": {
        "title": "Choose the chargers to monitor",
        "description": "Chargers at {site}.",
        "data": {
          "station_ids": "Chargers"
        }
      }
    },
    "error": {
      "cannot_connect": "Could not reach the Mer driver portal",
      "no_sites": "No site matched that name",
      "no_stations_selected": "Select at least one charger"
    },
    "abort": {
      "invalid_auth": "Stored password no longer works; re-authenticate the integration first",
      "cannot_connect": "Could not reach the Mer driver portal",
      "no_stations": "No chargers were found at that site"
    }
  },
  "entity": {},
  "exceptions": {
    "command_failed": {
      "message": "Mer rejected the command: {error}"
    },
    "no_active_session": {
      "message": "There is no active charging session to stop"
    }
  }
}
```

Create `custom_components/mer/translations/en.json` as an exact copy (`cp custom_components/mer/strings.json custom_components/mer/translations/en.json`). The `entity` block is filled in Tasks 7–10; keep both files identical after every change.

- [ ] **Step 5: Run tests**

Run: `scripts/test tests/test_config_flow.py -q`
Expected: 7 passed.

Pitfalls:
- `result["data_schema"].schema[CONF_SITE_ID]` is the `SelectSelector`; its `.config["options"]` is the list of option dicts.
- `result["data_schema"]({})` applies defaults; it is how the tests read the preselected values.
- The options flow gets `self.config_entry` automatically; do not assign it in `__init__`.
- If `start_reauth_flow` is unavailable on `MockConfigEntry`, use `hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id}, data=entry.data)`.

- [ ] **Step 6: Lint and commit**

```bash
scripts/format && scripts/lint
git add -A
git commit -m "feat: config flow with site search, charger selection, reauth and options

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Entity base classes and station/socket sensors

**Files:**
- Create: `custom_components/mer/entity.py`
- Modify: `custom_components/mer/sensor.py` (replace stub), `strings.json` + `translations/en.json` (entity block), `tests/test_init.py` (remove xfail marker)
- Test: `tests/test_sensor.py`

**Interfaces:**
- Consumes: `MerCoordinator.get_station/get_socket/configured_stations/site_id/site_name/station_ids`, models, `STATUSES`.
- Produces:
  - `entity.STATUS_OPTIONS: list[str]` (lower-cased `STATUSES`), `entity.status_option(status: str) -> str`
  - `entity.MerEntity(coordinator)` (`_attr_has_entity_name = True`)
  - `entity.MerStationEntity(coordinator, station_id, description)` with `.station` property, unique id `f"{entry_id}_station_{station_id}_{key}"`, device `(DOMAIN, f"station_{station_id}")`
  - `entity.MerSocketEntity(coordinator, station_id, socket_id, description)` with `.socket` property, unique id `f"{entry_id}_socket_{socket_id}_{key}"`, translation placeholder `socket`
  - `entity.MerSiteEntity(coordinator, description)` unique id `f"{entry_id}_site_{key}"`, device `(DOMAIN, f"site_{site_id}")`
  - `entity.MerAccountEntity(coordinator, description)` unique id `f"{entry_id}_account_{key}"`, device `(DOMAIN, f"account_{entry_id}")`
  - `entity.socket_label(socket) -> str`

- [ ] **Step 1: Write the failing tests**

`tests/test_sensor.py`:

```python
"""Tests for Mer sensors."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mer.const import DOMAIN
from custom_components.mer.driivz.models import Socket
from tests.helpers import setup_integration


def state_by_unique_id(hass: HomeAssistant, platform: str, unique_id: str):
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id is not None, f"no {platform} entity with unique_id {unique_id}"
    state = hass.states.get(entity_id)
    assert state is not None
    return state


async def test_station_and_socket_sensors(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id

    status = state_by_unique_id(hass, "sensor", f"{eid}_station_6042_status")
    assert status.state == "available"
    assert status.attributes["device_class"] == "enum"
    assert "charging" in status.attributes["options"]
    assert status.name == "Business Durham - NETPark 3 - Explorer 1 Status"

    charging = state_by_unique_id(hass, "sensor", f"{eid}_station_6041_status")
    assert charging.state == "charging"

    identity = state_by_unique_id(hass, "sensor", f"{eid}_station_6042_identity_key")
    assert identity.state == "MER-FS-AD00137"
    registry_entry = er.async_get(hass).async_get(identity.entity_id)
    assert registry_entry is not None and registry_entry.entity_category == er.EntityCategory.DIAGNOSTIC

    left = state_by_unique_id(hass, "sensor", f"{eid}_socket_11241_status")
    assert left.state == "charging"
    assert left.name == "Business Durham - NETPark 4 - Explorer 2 Left status"
    right = state_by_unique_id(hass, "sensor", f"{eid}_socket_11242_status")
    assert right.state == "available"

    price = state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_price")
    assert price.state == "0.0"
    assert price.attributes["unit_of_measurement"] == "GBP/kWh"

    power = state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_max_power")
    assert power.state == "7.0"
    assert power.attributes["unit_of_measurement"] == "kW"
    assert power.attributes["device_class"] == "power"


async def test_unknown_status_maps_to_unknown_option(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    stations = mock_client.find_stations_by_ids.return_value
    weird = stations[0]
    stations[0] = type(weird).from_dict(
        {
            "id": weird.id,
            "caption": weird.caption,
            "stationStatusId": "SOMETHING_NEW",
            "stationSockets": [{"id": 11243, "socketStatusId": "WEIRD"}, {"id": 11244}],
        }
    )
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    assert state_by_unique_id(hass, "sensor", f"{eid}_station_6042_status").state == "unknown"
    assert state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_status").state == "unknown"


async def test_station_missing_from_poll_is_unavailable(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    mock_client.find_stations_by_ids.return_value = [
        s for s in mock_client.find_stations_by_ids.return_value if s.id != 6042
    ]
    coordinator = mock_config_entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert state_by_unique_id(hass, "sensor", f"{eid}_station_6042_status").state == STATE_UNAVAILABLE
    assert state_by_unique_id(hass, "sensor", f"{eid}_socket_11243_status").state == STATE_UNAVAILABLE
    assert state_by_unique_id(hass, "sensor", f"{eid}_station_6041_status").state == "charging"


def test_socket_label_fallbacks() -> None:
    from custom_components.mer.entity import socket_label

    assert socket_label(Socket(id=1, name="Left")) == "Left"
    assert socket_label(Socket(id=1, identity_key="2")) == "Socket 2"
    assert socket_label(Socket(id=77)) == "Socket 77"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `scripts/test tests/test_sensor.py -q`
Expected: FAIL (`no sensor entity with unique_id ...`, `ImportError` for `entity`).

- [ ] **Step 3: Write `entity.py`**

```python
"""Base entities and device helpers for Mer."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MerCoordinator
from .driivz.const import STATUSES, STATUS_UNKNOWN
from .driivz.models import Socket, Station

STATUS_OPTIONS: list[str] = [status.lower() for status in STATUSES]


def status_option(status: str | None) -> str:
    """Map a portal status to an enum option, falling back to unknown."""
    value = (status or STATUS_UNKNOWN).lower()
    return value if value in STATUS_OPTIONS else STATUS_UNKNOWN.lower()


def socket_label(socket: Socket) -> str:
    """Human label for a socket used in entity names."""
    if socket.name:
        return socket.name
    if socket.identity_key:
        return f"Socket {socket.identity_key}"
    return f"Socket {socket.id}"


class MerEntity(CoordinatorEntity[MerCoordinator]):
    """Common base: entity names are composed from device name + translated name."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MerCoordinator, description: EntityDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description

    @property
    def entry_id(self) -> str:
        return self.coordinator.config_entry.entry_id

    def site_device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"site_{self.coordinator.site_id}")},
            name=self.coordinator.site_name,
            manufacturer="Mer",
            model="Charging site",
            entry_type=None,
        )


class MerSiteEntity(MerEntity):
    """Entity attached to the site device."""

    def __init__(self, coordinator: MerCoordinator, description: EntityDescription) -> None:
        super().__init__(coordinator, description)
        self._attr_unique_id = f"{self.entry_id}_site_{description.key}"
        self._attr_device_info = self.site_device_info()


class MerAccountEntity(MerEntity):
    """Entity attached to the account device."""

    def __init__(self, coordinator: MerCoordinator, description: EntityDescription) -> None:
        super().__init__(coordinator, description)
        self._attr_unique_id = f"{self.entry_id}_account_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"account_{self.entry_id}")},
            name="Mer account",
            manufacturer="Mer",
            model="Driver account",
        )


class MerStationEntity(MerEntity):
    """Entity attached to a charger device."""

    def __init__(
        self, coordinator: MerCoordinator, station_id: int, description: EntityDescription
    ) -> None:
        super().__init__(coordinator, description)
        self.station_id = station_id
        self._attr_unique_id = f"{self.entry_id}_station_{station_id}_{description.key}"
        station = coordinator.get_station(station_id)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"station_{station_id}")},
            name=station.display_name if station else f"Charger {station_id}",
            manufacturer="Mer",
            model=station.model_name if station else None,
            serial_number=station.identity_key if station else None,
            via_device=(DOMAIN, f"site_{coordinator.site_id}"),
            configuration_url=(
                f"{coordinator.config_entry.data.get('base_url', '')}/findCharger"
                if coordinator.config_entry.data.get("base_url")
                else None
            ),
        )

    @property
    def station(self) -> Station | None:
        return self.coordinator.get_station(self.station_id)

    @property
    def available(self) -> bool:
        return super().available and self.station is not None


class MerSocketEntity(MerStationEntity):
    """Entity attached to a charger device but describing one socket."""

    def __init__(
        self,
        coordinator: MerCoordinator,
        station_id: int,
        socket_id: int,
        description: EntityDescription,
    ) -> None:
        super().__init__(coordinator, station_id, description)
        self.socket_id = socket_id
        self._attr_unique_id = f"{self.entry_id}_socket_{socket_id}_{description.key}"
        socket = coordinator.get_socket(station_id, socket_id)
        label = socket_label(socket) if socket else f"Socket {socket_id}"
        self._attr_translation_placeholders = {"socket": label}

    @property
    def socket(self) -> Socket | None:
        return self.coordinator.get_socket(self.station_id, self.socket_id)

    @property
    def available(self) -> bool:
        return super().available and self.socket is not None
```

- [ ] **Step 4: Write `sensor.py` (station and socket sensors; site/account sensors are added in Tasks 8 and 10)**

```python
"""Sensor platform for Mer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import MerConfigEntry, MerCoordinator
from .driivz.models import Socket, Station
from .entity import STATUS_OPTIONS, MerSocketEntity, MerStationEntity, status_option


@dataclass(frozen=True, kw_only=True)
class MerStationSensorDescription(SensorEntityDescription):
    """Sensor reading a Station."""

    value_fn: Callable[[Station], StateType]


@dataclass(frozen=True, kw_only=True)
class MerSocketSensorDescription(SensorEntityDescription):
    """Sensor reading a Socket."""

    value_fn: Callable[[Socket], StateType]


STATION_SENSORS: tuple[MerStationSensorDescription, ...] = (
    MerStationSensorDescription(
        key="status",
        translation_key="station_status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUS_OPTIONS,
        value_fn=lambda station: status_option(station.status),
    ),
    MerStationSensorDescription(
        key="identity_key",
        translation_key="identity_key",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda station: station.identity_key,
    ),
)

SOCKET_SENSORS: tuple[MerSocketSensorDescription, ...] = (
    MerSocketSensorDescription(
        key="status",
        translation_key="socket_status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUS_OPTIONS,
        value_fn=lambda socket: status_option(socket.status),
    ),
    MerSocketSensorDescription(
        key="price",
        translation_key="socket_price",
        native_unit_of_measurement="GBP/kWh",
        suggested_display_precision=2,
        value_fn=lambda socket: socket.price_per_kwh,
    ),
    MerSocketSensorDescription(
        key="max_power",
        translation_key="socket_max_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda socket: socket.max_power_kw,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: MerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Create sensors for every configured station and its sockets."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for station in coordinator.configured_stations():
        entities.extend(
            MerStationSensor(coordinator, station.id, description)
            for description in STATION_SENSORS
        )
        for socket in station.sockets:
            entities.extend(
                MerSocketSensor(coordinator, station.id, socket.id, description)
                for description in SOCKET_SENSORS
            )
    async_add_entities(entities)


class MerStationSensor(MerStationEntity, SensorEntity):
    """A sensor describing a charger."""

    entity_description: MerStationSensorDescription

    def __init__(
        self, coordinator: MerCoordinator, station_id: int, description: MerStationSensorDescription
    ) -> None:
        super().__init__(coordinator, station_id, description)

    @property
    def native_value(self) -> StateType:
        station = self.station
        return self.entity_description.value_fn(station) if station else None


class MerSocketSensor(MerSocketEntity, SensorEntity):
    """A sensor describing one socket."""

    entity_description: MerSocketSensorDescription

    def __init__(
        self,
        coordinator: MerCoordinator,
        station_id: int,
        socket_id: int,
        description: MerSocketSensorDescription,
    ) -> None:
        super().__init__(coordinator, station_id, socket_id, description)

    @property
    def native_value(self) -> StateType:
        socket = self.socket
        return self.entity_description.value_fn(socket) if socket else None
```

- [ ] **Step 5: Add entity translations**

Replace `"entity": {}` in `strings.json` (and `translations/en.json`) with:

```json
  "entity": {
    "sensor": {
      "station_status": {
        "name": "Status",
        "state": {
          "available": "Available",
          "occupied": "Occupied",
          "charging": "Charging",
          "discharging": "Discharging",
          "paused": "Paused",
          "preparing": "Preparing",
          "finishing": "Finishing",
          "reserved": "Reserved",
          "unavailable": "Unavailable",
          "faulted": "Faulted",
          "unknown": "Unknown"
        }
      },
      "identity_key": { "name": "Identity key" },
      "socket_status": {
        "name": "{socket} status",
        "state": {
          "available": "Available",
          "occupied": "Occupied",
          "charging": "Charging",
          "discharging": "Discharging",
          "paused": "Paused",
          "preparing": "Preparing",
          "finishing": "Finishing",
          "reserved": "Reserved",
          "unavailable": "Unavailable",
          "faulted": "Faulted",
          "unknown": "Unknown"
        }
      },
      "socket_price": { "name": "{socket} price" },
      "socket_max_power": { "name": "{socket} max power" }
    }
  },
```

Remove the `xfail` marker from `test_devices_created_and_stale_station_removable` in `tests/test_init.py`.

- [ ] **Step 6: Run tests**

Run: `scripts/test tests/test_sensor.py tests/test_init.py -q`
Expected: all pass.

Pitfall: entity names in state objects are `"<device name> <translated name>"`. If `state.name` lacks the translated part, the `translations/en.json` copy is missing or out of sync with `strings.json`.

- [ ] **Step 7: Lint and commit**

```bash
scripts/format && scripts/lint
git add -A
git commit -m "feat: station and socket sensors with device hierarchy

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Binary sensors and site aggregate sensors

**Files:**
- Modify: `custom_components/mer/binary_sensor.py` (replace stub), `custom_components/mer/sensor.py` (add site sensors), `strings.json` + `translations/en.json`
- Test: `tests/test_binary_sensor.py`, extend `tests/test_sensor.py`

**Interfaces:**
- Consumes: `MerSocketEntity`, `MerSiteEntity`, `MerAccountEntity`, `MerCoordinator.configured_stations()`, `MerData.active`.
- Produces: binary sensors `socket available` (unique id `..._socket_<id>_available`), site `any_available` (`..._site_any_available`), account `charging` (`..._account_charging`); sensors site `available_sockets`, `sockets_in_use` (`..._site_available_sockets`, `..._site_sockets_in_use`).

- [ ] **Step 1: Write the failing tests**

`tests/test_binary_sensor.py`:

```python
"""Tests for Mer binary sensors."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mer.driivz.models import Socket
from tests.helpers import setup_integration
from tests.test_sensor import state_by_unique_id


async def test_socket_available_and_site_any_available(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_socket_11243_available").state == STATE_ON
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_socket_11241_available").state == STATE_OFF
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_site_any_available").state == STATE_ON
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_account_charging").state == STATE_OFF

    # everything occupied -> site not available
    stations = mock_client.find_stations_by_ids.return_value
    busy = []
    for station in stations:
        raw = {
            "id": station.id,
            "caption": station.caption,
            "stationStatusId": "OCCUPIED",
            "stationSockets": [{"id": s.id, "socketStatusId": "OCCUPIED"} for s in station.sockets],
        }
        busy.append(type(station).from_dict(raw))
    mock_client.find_stations_by_ids.return_value = busy
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_site_any_available").state == STATE_OFF
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_socket_11243_available").state == STATE_OFF


async def test_account_charging_on_when_session_active(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket: Socket,
) -> None:
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    assert state_by_unique_id(hass, "binary_sensor", f"{eid}_account_charging").state == STATE_ON
```

Append to `tests/test_sensor.py`:

```python
async def test_site_count_sensors(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    # Explorer 1: 2 available; Explorer 2: 1 charging + 1 available
    assert state_by_unique_id(hass, "sensor", f"{eid}_site_available_sockets").state == "3"
    assert state_by_unique_id(hass, "sensor", f"{eid}_site_sockets_in_use").state == "1"
    site_state = state_by_unique_id(hass, "sensor", f"{eid}_site_available_sockets")
    assert site_state.name == "Durham County Council - Business Durham NETPark Available sockets"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `scripts/test tests/test_binary_sensor.py tests/test_sensor.py -q`
Expected: FAIL (`no binary_sensor entity with unique_id ...`, `no sensor entity ... _site_available_sockets`).

- [ ] **Step 3: Write `binary_sensor.py`**

```python
"""Binary sensor platform for Mer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MerConfigEntry, MerCoordinator, MerData
from .driivz.models import Socket, Station
from .entity import MerAccountEntity, MerSiteEntity, MerSocketEntity


@dataclass(frozen=True, kw_only=True)
class MerSocketBinaryDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[Socket], bool]


@dataclass(frozen=True, kw_only=True)
class MerSiteBinaryDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[list[Station]], bool]


@dataclass(frozen=True, kw_only=True)
class MerAccountBinaryDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[MerData], bool]


SOCKET_BINARY_SENSORS: tuple[MerSocketBinaryDescription, ...] = (
    MerSocketBinaryDescription(
        key="available",
        translation_key="socket_available",
        icon="mdi:ev-station",
        is_on_fn=lambda socket: socket.is_available,
    ),
)

SITE_BINARY_SENSORS: tuple[MerSiteBinaryDescription, ...] = (
    MerSiteBinaryDescription(
        key="any_available",
        translation_key="site_any_available",
        icon="mdi:ev-station",
        is_on_fn=lambda stations: any(
            socket.is_available for station in stations for socket in station.sockets
        ),
    ),
)

ACCOUNT_BINARY_SENSORS: tuple[MerAccountBinaryDescription, ...] = (
    MerAccountBinaryDescription(
        key="charging",
        translation_key="account_charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        is_on_fn=lambda data: data.active is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: MerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    for station in coordinator.configured_stations():
        for socket in station.sockets:
            entities.extend(
                MerSocketBinarySensor(coordinator, station.id, socket.id, description)
                for description in SOCKET_BINARY_SENSORS
            )
    entities.extend(MerSiteBinarySensor(coordinator, d) for d in SITE_BINARY_SENSORS)
    entities.extend(MerAccountBinarySensor(coordinator, d) for d in ACCOUNT_BINARY_SENSORS)
    async_add_entities(entities)


class MerSocketBinarySensor(MerSocketEntity, BinarySensorEntity):
    entity_description: MerSocketBinaryDescription

    def __init__(
        self,
        coordinator: MerCoordinator,
        station_id: int,
        socket_id: int,
        description: MerSocketBinaryDescription,
    ) -> None:
        super().__init__(coordinator, station_id, socket_id, description)

    @property
    def is_on(self) -> bool | None:
        socket = self.socket
        return self.entity_description.is_on_fn(socket) if socket else None


class MerSiteBinarySensor(MerSiteEntity, BinarySensorEntity):
    entity_description: MerSiteBinaryDescription

    @property
    def is_on(self) -> bool:
        return self.entity_description.is_on_fn(self.coordinator.configured_stations())


class MerAccountBinarySensor(MerAccountEntity, BinarySensorEntity):
    entity_description: MerAccountBinaryDescription

    @property
    def is_on(self) -> bool:
        return self.entity_description.is_on_fn(self.coordinator.data)
```

- [ ] **Step 4: Add site sensors to `sensor.py`**

Add after `SOCKET_SENSORS`:

```python
@dataclass(frozen=True, kw_only=True)
class MerSiteSensorDescription(SensorEntityDescription):
    """Sensor aggregating the configured stations."""

    value_fn: Callable[[list[Station]], StateType]


SITE_SENSORS: tuple[MerSiteSensorDescription, ...] = (
    MerSiteSensorDescription(
        key="available_sockets",
        translation_key="site_available_sockets",
        icon="mdi:ev-plug-type2",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda stations: sum(
            1 for station in stations for socket in station.sockets if socket.is_available
        ),
    ),
    MerSiteSensorDescription(
        key="sockets_in_use",
        translation_key="site_sockets_in_use",
        icon="mdi:ev-plug-type2",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda stations: sum(
            1 for station in stations for socket in station.sockets if socket.is_in_use
        ),
    ),
)
```

Import `SensorStateClass` from `homeassistant.components.sensor` and `MerSiteEntity` from `.entity`. In `async_setup_entry`, before `async_add_entities(entities)` add:

```python
    entities.extend(MerSiteSensor(coordinator, description) for description in SITE_SENSORS)
```

Add the class:

```python
class MerSiteSensor(MerSiteEntity, SensorEntity):
    """A sensor aggregating all configured chargers at the site."""

    entity_description: MerSiteSensorDescription

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.coordinator.configured_stations())
```

- [ ] **Step 5: Add translations**

In `strings.json` and `translations/en.json`, add to `entity.sensor`:

```json
      "site_available_sockets": { "name": "Available sockets" },
      "site_sockets_in_use": { "name": "Sockets in use" }
```

and a new `entity.binary_sensor` block:

```json
    "binary_sensor": {
      "socket_available": { "name": "{socket} available" },
      "site_any_available": { "name": "Any socket available" },
      "account_charging": { "name": "Charging" }
    }
```

- [ ] **Step 6: Run tests**

Run: `scripts/test tests/test_binary_sensor.py tests/test_sensor.py -q`
Expected: all pass.

- [ ] **Step 7: Lint and commit**

```bash
scripts/format && scripts/lint
git add -A
git commit -m "feat: socket availability, site aggregates and charging binary sensors

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Start and stop charge buttons

**Files:**
- Modify: `custom_components/mer/button.py` (replace stub), `strings.json` + `translations/en.json`
- Test: `tests/test_button.py`

**Interfaces:**
- Consumes: `MerSocketEntity`, `MerAccountEntity`, `DriivzDriverClient.start_charge/stop_charge`, `MerCoordinator.schedule_refresh()`, `MerData.active`.
- Produces: button `start_charge` per socket (unique id `..._socket_<id>_start_charge`), button `stop_charge` on the account device (`..._account_stop_charge`). Failures raise `HomeAssistantError` with translation keys `command_failed` / `no_active_session`.

- [ ] **Step 1: Write the failing tests**

`tests/test_button.py`:

```python
"""Tests for Mer buttons."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.mer.const import DOMAIN
from custom_components.mer.driivz.exceptions import ApiError
from custom_components.mer.driivz.models import Socket
from tests.helpers import setup_integration


def entity_id_for(hass: HomeAssistant, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(BUTTON_DOMAIN, DOMAIN, unique_id)
    assert entity_id is not None
    return entity_id


async def press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


async def test_start_charge_button(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    entity_id = entity_id_for(hass, f"{eid}_socket_11243_start_charge")
    polls_before = mock_client.find_stations_by_ids.await_count
    await press(hass, entity_id)
    mock_client.start_charge.assert_awaited_once_with(11243)
    # the refresh is scheduled ~5 s later, then debounced by the coordinator (~10 s)
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert mock_client.find_stations_by_ids.await_count == polls_before + 1


async def test_start_charge_rejected_raises(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    mock_client.start_charge.side_effect = ApiError("OPERATION_NOT_ALLOWED_IN_CURRENT_SOCKET_STATE")
    with pytest.raises(HomeAssistantError) as excinfo:
        await press(hass, entity_id_for(hass, f"{eid}_socket_11243_start_charge"))
    assert "OPERATION_NOT_ALLOWED_IN_CURRENT_SOCKET_STATE" in str(excinfo.value)


async def test_stop_charge_button(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket: Socket,
) -> None:
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    await press(hass, entity_id_for(hass, f"{eid}_account_stop_charge"))
    mock_client.stop_charge.assert_awaited_once_with(11241)


async def test_stop_charge_without_session_raises(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    with pytest.raises(HomeAssistantError):
        await press(hass, entity_id_for(hass, f"{eid}_account_stop_charge"))
    mock_client.stop_charge.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `scripts/test tests/test_button.py -q`
Expected: FAIL (`assert entity_id is not None`).

- [ ] **Step 3: Write `button.py`**

```python
"""Button platform for Mer."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import MerConfigEntry, MerCoordinator
from .driivz.exceptions import DriivzError
from .entity import MerAccountEntity, MerSocketEntity

START_CHARGE = ButtonEntityDescription(
    key="start_charge", translation_key="socket_start_charge", icon="mdi:play-circle"
)
STOP_CHARGE = ButtonEntityDescription(
    key="stop_charge", translation_key="account_stop_charge", icon="mdi:stop-circle"
)


async def async_setup_entry(
    hass: HomeAssistant, entry: MerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[ButtonEntity] = [
        MerStartChargeButton(coordinator, station.id, socket.id)
        for station in coordinator.configured_stations()
        for socket in station.sockets
    ]
    entities.append(MerStopChargeButton(coordinator))
    async_add_entities(entities)


def _raise_command_failed(err: DriivzError) -> None:
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="command_failed",
        translation_placeholders={"error": str(err)},
    ) from err


class MerStartChargeButton(MerSocketEntity, ButtonEntity):
    """Start a charge on this socket."""

    def __init__(self, coordinator: MerCoordinator, station_id: int, socket_id: int) -> None:
        super().__init__(coordinator, station_id, socket_id, START_CHARGE)

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.start_charge(self.socket_id)
        except DriivzError as err:
            _raise_command_failed(err)
        self.coordinator.schedule_refresh()


class MerStopChargeButton(MerAccountEntity, ButtonEntity):
    """Stop the account's active charge."""

    def __init__(self, coordinator: MerCoordinator) -> None:
        super().__init__(coordinator, STOP_CHARGE)

    async def async_press(self) -> None:
        active = self.coordinator.data.active
        if active is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_active_session")
        try:
            await self.coordinator.client.stop_charge(active.socket_id)
        except DriivzError as err:
            _raise_command_failed(err)
        self.coordinator.schedule_refresh()
```

- [ ] **Step 4: Add translations**

Add to `entity` in `strings.json` / `translations/en.json`:

```json
    "button": {
      "socket_start_charge": { "name": "{socket} start charge" },
      "account_stop_charge": { "name": "Stop charge" }
    }
```

(The `exceptions` block with `command_failed` and `no_active_session` already exists from Task 6.)

- [ ] **Step 5: Run tests**

Run: `scripts/test tests/test_button.py -q`
Expected: 4 passed.

- [ ] **Step 6: Lint and commit**

```bash
scripts/format && scripts/lint
git add -A
git commit -m "feat: start and stop charge buttons

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Account sensors (active session, last session, wallet)

**Files:**
- Modify: `custom_components/mer/sensor.py`, `strings.json` + `translations/en.json`
- Test: extend `tests/test_sensor.py`

**Interfaces:**
- Consumes: `MerAccountEntity`, `MerData.active/last_transaction/wallet`, `MerCoordinator.get_station`.
- Produces: account sensors with keys `active_station`, `active_started`, `active_energy`, `active_cost`, `last_energy`, `last_cost`, `last_started`, `wallet_balance` (unique ids `..._account_<key>`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sensor.py`:

```python
async def test_account_sensors_idle(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_active_station").state == "unknown"
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_active_energy").state == "unknown"
    wallet = state_by_unique_id(hass, "sensor", f"{eid}_account_wallet_balance")
    assert wallet.state == "12.5"
    assert wallet.attributes["unit_of_measurement"] == "GBP"
    assert wallet.attributes["device_class"] == "monetary"
    last_energy = state_by_unique_id(hass, "sensor", f"{eid}_account_last_energy")
    assert last_energy.state == "32.408"
    assert last_energy.attributes["unit_of_measurement"] == "kWh"
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_last_cost").state == "0.0"
    last_started = state_by_unique_id(hass, "sensor", f"{eid}_account_last_started")
    assert last_started.state == "2026-09-16T10:34:26+00:00"
    assert last_started.attributes["device_class"] == "timestamp"
    assert last_started.attributes["station"] == "Business Durham - NETPark 4 - Explorer 2"


async def test_account_sensors_charging(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    charging_socket: Socket,
) -> None:
    mock_client.find_last_active_charge_socket.return_value = charging_socket
    await setup_integration(hass, mock_config_entry)
    eid = mock_config_entry.entry_id
    station = state_by_unique_id(hass, "sensor", f"{eid}_account_active_station")
    assert station.state == "Business Durham - NETPark 4 - Explorer 2"
    assert station.attributes["socket"] == "Left"
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_active_energy").state == "12.345"
    assert state_by_unique_id(hass, "sensor", f"{eid}_account_active_cost").state == "0.0"
    assert (
        state_by_unique_id(hass, "sensor", f"{eid}_account_active_started").state
        == "2026-09-16T10:34:26+00:00"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `scripts/test tests/test_sensor.py -q -k account`
Expected: FAIL (`no sensor entity with unique_id ..._account_active_station`).

- [ ] **Step 3: Add account sensors to `sensor.py`**

Add imports: `from datetime import datetime`, `from homeassistant.const import UnitOfEnergy`, `from .coordinator import MerData`, `from .entity import MerAccountEntity`, and `from .driivz.models import Transaction`. Then add:

```python
@dataclass(frozen=True, kw_only=True)
class MerAccountSensorDescription(SensorEntityDescription):
    """Sensor reading coordinator-wide data."""

    value_fn: Callable[[MerCoordinator, MerData], StateType | datetime]
    unit_fn: Callable[[MerData], str | None] | None = None
    attributes_fn: Callable[[MerCoordinator, MerData], dict[str, str]] | None = None


def _active_station_name(coordinator: MerCoordinator, data: MerData) -> str | None:
    if data.active is None:
        return None
    if data.active.station_id is not None:
        station = coordinator.get_station(data.active.station_id)
        if station is not None:
            return station.display_name
    return f"Socket {data.active.socket_id}"


def _active_attributes(coordinator: MerCoordinator, data: MerData) -> dict[str, str]:
    if data.active is None or data.active.station_id is None:
        return {}
    socket = coordinator.get_socket(data.active.station_id, data.active.socket_id)
    return {"socket": socket_label(socket)} if socket else {}


def _last(data: MerData) -> Transaction | None:
    return data.last_transaction


def _currency(data: MerData) -> str | None:
    if data.wallet and data.wallet.currency:
        return data.wallet.currency
    if data.last_transaction and data.last_transaction.currency:
        return data.last_transaction.currency
    return "GBP"


ACCOUNT_SENSORS: tuple[MerAccountSensorDescription, ...] = (
    MerAccountSensorDescription(
        key="active_station",
        translation_key="active_station",
        icon="mdi:ev-station",
        value_fn=_active_station_name,
        attributes_fn=_active_attributes,
    ),
    MerAccountSensorDescription(
        key="active_started",
        translation_key="active_started",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda _c, data: data.active.started_at if data.active else None,
    ),
    MerAccountSensorDescription(
        key="active_energy",
        translation_key="active_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda _c, data: data.active.energy_kwh if data.active else None,
    ),
    MerAccountSensorDescription(
        key="active_cost",
        translation_key="active_cost",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        value_fn=lambda _c, data: data.active.cost if data.active else None,
        unit_fn=lambda data: (data.active.currency if data.active and data.active.currency else _currency(data)),
    ),
    MerAccountSensorDescription(
        key="last_energy",
        translation_key="last_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda _c, data: _last(data).energy_kwh if _last(data) else None,
    ),
    MerAccountSensorDescription(
        key="last_cost",
        translation_key="last_cost",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        value_fn=lambda _c, data: _last(data).cost if _last(data) else None,
        unit_fn=_currency,
    ),
    MerAccountSensorDescription(
        key="last_started",
        translation_key="last_started",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda _c, data: _last(data).started_at if _last(data) else None,
        attributes_fn=lambda _c, data: (
            {"station": _last(data).display_name} if _last(data) else {}
        ),
    ),
    MerAccountSensorDescription(
        key="wallet_balance",
        translation_key="wallet_balance",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        value_fn=lambda _c, data: data.wallet.balance if data.wallet else None,
        unit_fn=_currency,
    ),
)
```

Import `socket_label` from `.entity`. In `async_setup_entry` add before `async_add_entities`:

```python
    entities.extend(MerAccountSensor(coordinator, description) for description in ACCOUNT_SENSORS)
```

Add the class:

```python
class MerAccountSensor(MerAccountEntity, SensorEntity):
    """A sensor about the driver's account or session."""

    entity_description: MerAccountSensorDescription

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self.coordinator, self.coordinator.data)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.entity_description.unit_fn is not None:
            return self.entity_description.unit_fn(self.coordinator.data)
        return self.entity_description.native_unit_of_measurement

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator, self.coordinator.data)
```

- [ ] **Step 4: Add translations**

Add to `entity.sensor` in both JSON files:

```json
      "active_station": { "name": "Active session charger" },
      "active_started": { "name": "Active session started" },
      "active_energy": { "name": "Active session energy" },
      "active_cost": { "name": "Active session cost" },
      "last_energy": { "name": "Last session energy" },
      "last_cost": { "name": "Last session cost" },
      "last_started": { "name": "Last session started" },
      "wallet_balance": { "name": "Wallet balance" }
```

- [ ] **Step 5: Run the whole suite**

Run: `scripts/test -q`
Expected: all pass.

Pitfall: HA rejects a `MONETARY` sensor whose unit is `None`; `_currency` always returns a code, so a wallet-less first cycle still works.

- [ ] **Step 6: Lint and commit**

```bash
scripts/format && scripts/lint
git add -A
git commit -m "feat: active session, last session and wallet sensors

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Diagnostics

**Files:**
- Create: `custom_components/mer/diagnostics.py`
- Test: `tests/test_diagnostics.py`

**Interfaces:**
- Consumes: `MerConfigEntry`, `MerData`, `DriivzDriverClient.rate_limit_remaining`.
- Produces: `async_get_config_entry_diagnostics(hass, entry) -> dict`.

- [ ] **Step 1: Write the failing test**

`tests/test_diagnostics.py`:

```python
"""Tests for diagnostics output."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mer.diagnostics import async_get_config_entry_diagnostics
from tests.helpers import setup_integration


async def test_diagnostics_redacts_secrets(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    diag = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert diag["entry"]["data"]["username"] == "**REDACTED**"
    assert diag["entry"]["data"]["password"] == "**REDACTED**"
    assert diag["entry"]["options"]["station_ids"] == [6042, 6041]
    assert diag["rate_limit_remaining"] == 9
    assert diag["data"]["customer_id"] == "**REDACTED**"
    assert diag["data"]["wallet"]["balance"] == 12.5
    assert diag["data"]["wallet"]["account_number"] == "**REDACTED**"
    assert sorted(diag["data"]["stations"]) == ["6041", "6042"]
    assert diag["data"]["stations"]["6042"]["sockets"][0]["status"] == "AVAILABLE"
    assert diag["data"]["active"] is None
    assert diag["data"]["last_transaction"]["energy_kwh"] == 32.408
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/test tests/test_diagnostics.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `diagnostics.py`**

```python
"""Diagnostics support for Mer."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
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
}


def _plain(value: Any) -> Any:
    """Convert dataclasses/datetimes into JSON-friendly values."""
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _plain(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
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
                "active": _plain(data.active),
                "wallet": _plain(data.wallet),
                "last_transaction": _plain(data.last_transaction),
            },
            TO_REDACT,
        ),
    }
```

- [ ] **Step 4: Run test, lint, commit**

Run: `scripts/test tests/test_diagnostics.py -q` → 1 passed.

```bash
scripts/format && scripts/lint
git add -A
git commit -m "feat: redacted config entry diagnostics

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: Documentation, HACS metadata, release

**Files:**
- Create: `README.md`, `LICENSE`, `docs/api.md`, `info.md` (optional HACS description; skip if `render_readme` is true — it is, so skip)
- Modify: `custom_components/mer/manifest.json` (codeowners/URLs once the GitHub repo exists)

- [ ] **Step 1: Write `LICENSE`** — MIT, copyright 2026 Daniel Sørensen.

- [ ] **Step 2: Write `README.md`**

Sections, each 3–8 lines:
1. What it is: unofficial HACS integration for Mer UK chargers via the Driivz driver portal; monitors chosen chargers' sockets, starts/stops charges, shows sessions and wallet.
2. Disclaimer: unofficial, personal use, may break when Mer/Driivz change the portal; not affiliated.
3. Install via HACS: *HACS → Integrations → ⋮ → Custom repositories → `https://github.com/<owner>/ha-mer`, category Integration → Install → restart HA*.
4. Setup: *Settings → Devices & services → Add integration → Mer EV Charging*, then the three steps (credentials, site search e.g. "NETPark", tick chargers).
5. Entities table copied from spec §3.4 (devices → entities → meaning).
6. Options: poll interval 30–600 s, change chargers; reauth.
7. Example automation: notify when `binary_sensor.<site>_any_socket_available` turns on between 07:00 and 09:30 on weekdays.
8. Rate limits and polling notes.
9. Development: `python -m venv .venv`, `pip install -r requirements_test.txt`, `pytest`, `ruff`.

- [ ] **Step 3: Write `docs/api.md`**

Copy spec §2 (findings) verbatim as the starting point, then add the "mapped but unused" endpoints with the payloads captured on 2026-09-17:

```
POST billingFacade/findBillingCustomerContractsByCustomerId  form {}            → contracts (billing plans, tariffs)
POST billingFacade/findAvailableBillingPlans                 json {}            → plans
POST customerFacade/findCustomerCars                         form {}            → vehicles
POST customerFacade/findNotifications                        form {}            → notification preferences (stationEventType, isSms, isCellApp)
POST stationFacade/findStationsByStationLandmarkOfCustomer   form {billingPlanId?} → favourites
POST stationFacade/findCustomerReservations                  form {}            → reservations (empty; RESERVATION_IS_NOT_ALLOWED on NETPark)
POST customerFacade/notifyMeWhenStationIsAvailable           form {stationId}
POST customerFacade/isDriverSubscribedToNotifyMeWhenStationIsAvailable form {stationId}
POST stationFacade/getStationCapabilitiesAndValidate         form {stationId, stationSocketId, socketStatus} → allowed/denied operations
POST stationFacade/findPlacesByQuery / findPlaceDetails      (Google Places proxy)
GET  configurationFacade/getServerConfiguration              → feature flags (authenticated)
GET  configurationFacade/getAnonymousConfiguration           → feature flags (anonymous)
WS   /websocket  (send "0" on open) → JSON with "@c": StationStatusSummaryDtoImp {stationId, stationSocketId, stationSocketStatusDto{socketStatus}}, CustomerDetailChargeEventDtoImp, BillingChargingEstimationMessageImp, ...
```

- [ ] **Step 4: Create the GitHub repository and push**

Ask the user to create `ha-mer` (public) under their personal GitHub account, then:

```bash
git remote add origin https://github.com/<owner>/ha-mer.git
git push -u origin main
```

Replace `danielmsorensen` placeholders in `manifest.json` and `README.md` with the real owner, commit, push, and confirm the CI workflow is green (hassfest, HACS, tests).

- [ ] **Step 5: Live verification checklist (with the user)**

1. Install via HACS custom repository on the live HA, restart, add the integration with the Mer UK account, search "NETPark", select Explorer 1 and 2.
2. Confirm socket sensors match the portal map for both chargers; confirm `Any socket available`.
3. Confirm `Wallet balance` and `Last session` values match the portal history.
4. Ask the user for explicit go-ahead, then press **one** `start charge` on a free NETPark socket while they are at the charger, and verify: the button succeeds, the socket goes to `preparing`/`charging` within two polls, `Charging` turns on, `Active session energy` rises, `Stop charge` works.
5. Record the real shapes of `findLastActiveChargeSocket`, `findCurrentTransactionStartTime` and `findCurrentTransactionBillingChargingEstimation` (from HA debug logs or the browser) and replace the synthetic fixtures `last_active_charging.json`, `transaction_start_time.json`, `transaction_estimate.json`; adjust `SessionEstimate.from_dict`/`parse_start_time` key lists if needed.
6. Tag `v0.1.0`, create a GitHub release.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "docs: README, API reference and licence

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec coverage:** §2 findings → `docs/api.md` (Task 12) and client (Tasks 3–4). §3.1 client → Tasks 3–4. §3.2 coordinator (budget, 15 min wallet, 60 min details, rate-limit skip, auth → reauth, 5 s refresh) → Task 5. §3.3 config flow (user/site/site_select/stations, options, reauth) → Task 6. §3.4 entities → Tasks 7–10; the account device id uses `entry_id` instead of the customer id so it exists before the wallet is fetched (documented deviation). §3.5 error handling → Tasks 4, 5, 9. §3.6/§4 diagnostics and testing → Tasks 5–11. Websocket, capabilities method and syrupy snapshots are deliberately out of v1.
- **Type consistency:** `get_station`, `get_socket`, `configured_stations`, `schedule_refresh`, `MerData` fields, unique-id patterns and translation keys are identical across Tasks 5–11.
- **Known unknowns:** shapes of the three live-session endpoints are synthetic until Task 12 step 5; parsers are tolerant and tests assert on the tolerant behaviour.
