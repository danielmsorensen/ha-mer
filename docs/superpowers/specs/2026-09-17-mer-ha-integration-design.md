# Mer (UK) Home Assistant integration — design

Date: 2026-09-17
Status: approved design, awaiting implementation plan

## 1. Goal

A HACS custom integration, domain `mer`, that lets Home Assistant monitor and control
EV chargers on the Mer UK network through the same web API the Mer Driver Portal
(https://driver.uk.mer.eco) uses. The portal is a white-label **Driivz** driver portal,
so the client is written against Driivz conventions with a configurable base URL.

Primary user goals (v1):

1. See whether specific chargers (e.g. "Business Durham - NETPark 3 - Explorer 1" and
   "NETPark 4 - Explorer 2" at site "Durham County Council - Business Durham NETPark")
   are free right now, per socket, and whether any socket at the site is free.
2. Start a charge on a chosen socket and stop the active charge.
3. See the live session (energy, cost, start time) and the last completed session.

Non-goals for v1 (candidates for later phases): reservations (portal reports
`RESERVATION_IS_NOT_ALLOWED` for these chargers), notify-me-when-available, RFID cards,
vehicles, contracts and tariff tables, history statistics beyond the last session,
websocket push updates, other Mer countries or other Driivz tenants (the base URL is
already a parameter so this is cheap later).

There is no official API and no terms-of-use grant for this usage; the integration is
unofficial, for personal use, and may break when Mer or Driivz change the portal.

## 2. Findings from reverse engineering (2026-09-16/17)

All endpoints live on the portal host. Responses are JSON envelopes
`{"success": bool, "errors": [{"errorType", "messageKey"}], "data": ...}`.

### 2.1 Authentication

- `GET /login` returns HTML with `<meta name="_csrf" content="<uuid>">`,
  `<meta name="_csrf_header" content="X-CSRF-TOKEN">`,
  `<meta name="_csrf_parameterName" content="_csrf">`.
- `POST /login`, `application/x-www-form-urlencoded`, fields `username`, `password`,
  `_spring_security_remember_me=true`, `_csrf=<token>`; headers `X-Ajax-call: true`,
  `X-APP-TYPE: WEB`, `X-CSRF-TOKEN: <token>`. Success: HTTP 200,
  `{"success": true, "targetUrl": "/findCharger"}`. Failure: `success: false` with
  `errorType` (observed in JS: `USER_IS_NOT_ACTIVE`, `PASSWORD_EXPIRES` with `token`,
  generic types rendered from `enum.ErrorType.*`). HTTP 403 means the CSRF/session is stale.
- Session state is cookie based (Spring Security). `SPRING_SECURITY_REMEMBER_ME_COOKIE`
  carries a JWT valid 24 h; the servlet session cookie is HttpOnly. The CSRF token can
  change after login, so it is re-read from any HTML page (`/findCharger`) after login.
- reCAPTCHA Enterprise is disabled on this tenant (`reCaptchaEnterpriseEnabled: false`),
  so scripted login works. If it is ever enabled, login will fail with a recaptcha error
  and the integration must surface that clearly.
- `POST /logout` with the CSRF token as body ends the session.

### 2.2 Common request conventions

Headers on every API call: `X-CSRF-TOKEN`, `X-APP-TYPE: WEB`, `X-JSON-TYPES: None`
(the portal also sends `Accept-Language`). Two body styles exist and the server is strict
about which one each endpoint expects:

- **JSON** (`Content-Type: application/json`): the map/station query endpoints that take
  a `queryInfo` object.
- **Form** (`application/x-www-form-urlencoded`): most other facade calls.

Every response carries `X-Rate-Limit-Remaining` (observed 9 at rest, decrementing per
burst). Anonymous access works for public station data; a logged-in session is needed for
restricted chargers' tariffs, sessions, history, wallet, and start/stop.

### 2.3 Endpoints used in v1

| Purpose | Method / path | Body | Notes |
|---|---|---|---|
| Sites in map bounds | `POST /stationFacade/findSitesInBounds` | JSON `{"filterByBounds": {"northEastLat","northEastLng","southWestLat","southWestLng"}, "filterByIsManaged": true}` | Whole-UK bounds works in one call. Site DTO: `id`, `dn` (name), `ss` (status), `ns` (socket count), `sal` (access level), `latitude`, `longitude`, `scs` (speed), `im` (managed) |
| Stations in bounds | `POST /stationFacade/findStationsInBounds` | JSON, same `queryInfo` shape; optional `filterByStationStatuses`, `filterBySocketTypes`, `filterByChargingSpeeds`, `filterBySiteAccessLevels`, `filterByBillingPlanIds` (observed as non-restrictive) | Short station DTO: `id`, `caption`, `latitude`, `longitude`, `stationStatusId`, `comingSoon`, `isManaged`, `stationSockets[{id}]` |
| Stations by ids | `POST /stationFacade/findStationsByIds` | JSON `{"filterByIds": [ids]}` | Same DTO as above but includes `stationSockets[].socketStatusId` and `maximumPower`. **Primary polling call.** |
| Station detail | `GET /stationFacade/findStationById?stationId=&billingPlanId=` | query | Full DTO: address, `identityKey`, `siteId`, `siteName`, `stationModelName`, `stationOwnerName`, `siteStationAccessLevel`, `stationSockets[]` with `id`, `name` (e.g. Left/Right), `identityKey`, `socketStatusId`, `maximumPower`, `stationModelSocketSocketTypeId`, `stationModelSocketVoltageType`, `socketPrices[{billingPlanId, billingPlanCode, kwhPrice, plugInMinuteRate, transactionFee, currency}]` |
| Capabilities | `POST /stationFacade/getStationCapabilitiesAndValidate` | form `stationId`, `stationSocketId`, `socketStatus` | Returns `allowedSocketOperations`, `denySocketOperations`, `socketStatuses`, `stationStatus`. Used only in diagnostics / tests |
| Start charge | `POST /stationFacade/startChargeNow` | form `stationSocketId` | Success when `data.operationStatus == "PENDING"`; the charger then waits for the cable ("Swipe to start, then connect") |
| Stop charge | `POST /stationFacade/stopCharge` | form `stationSocketId` | |
| Active session | `POST /stationFacade/findLastActiveChargeSocket` | form, empty | `data` absent when idle; when charging returns the active socket DTO (fields to be confirmed on first live session) |
| Session start time | `POST /stationFacade/findCurrentTransactionStartTime` | form `stationSocketId` | |
| Session estimate | `POST /stationFacade/findCurrentTransactionBillingChargingEstimation` | form `socketId` | energy / cost estimate for the running transaction (fields confirmed on first live session) |
| History | `POST /customerFacade/findDriverChargeTransactionLogByView` | JSON `{"filterByStartedOnFrom": ms, "filterByStartedOnTo": ms, "filterByMemberId": <customerDetailId>}` | Empty body → `INSUFFICIENT_PERMISSIONS`. Row: `id`, `stationId`, `caption`, `siteName`, `startOn`, `stoppedOn`, `durationTime` (s), `totalEnergy` (Wh), `cost`, `currency`, `billingPlanName`, `chargeTransactionBillingStatus`, `socketType`, `startInitiator` |
| Wallet | `POST /billingFacade/findCustomerDetailWalletByCustomerId` | form, empty | `accountBalance`, `currency`, `customerDetailId`, `customerDetailMemberId`, `accountNumber`, `timezoneZoneId`. Also the source of the customer id for the history filter |

Other mapped but unused endpoints are recorded in `docs/api.md` (contracts, plans,
cards, vehicles, notifications, landmarks/favourites, notify-me, reservations, places
search, websocket message classes).

### 2.4 Enumerations

- Socket / station status: `AVAILABLE`, `OCCUPIED`, `CHARGING`, `DISCHARGING`, `PAUSED`,
  `PREPARING`, `FINISHING`, `RESERVED`, `UNAVAILABLE`, `FAULTED`, `UNKNOWN`.
  "In use" = OCCUPIED, CHARGING, DISCHARGING, PAUSED, PREPARING, FINISHING.
- Site access level: `PUBLIC`, `PRIVATE`, `TAXI_ONLY`, `HOME`. Restricted workplace
  chargers are still `PUBLIC` at site level; restriction is visible only as the caption
  prefix `[RESTRICTED ACCESS]` and enforced at start time by billing plan.
- Charging speed: `SLOW`, `SEMI_FAST`, `FAST`, `ULTRA_FAST`.
- Socket type: `TYPE_2_MENNEKES`, `TYPE_COMBO_GERMANY`, `TYPE_4_CHADEMO` (others exist).

### 2.5 Websocket (deferred in v1; adopted 2026-09-24, see docs/api.md and docs/decisions.md)

`wss://<host>/websocket`; client sends `"0"` on open; server pushes JSON messages whose
`@c` (or `@class`) field names the DTO, e.g. `StationStatusSummaryDtoImp` with
`stationId`, `stationSocketId`, `stationSocketStatusDto.socketStatus`. Not used in v1.

## 3. Architecture

Single repository, single HACS integration. Layers:

```
custom_components/mer/
  driivz/                 HA-agnostic client package (no homeassistant imports)
    __init__.py
    client.py             DriivzDriverClient
    models.py             dataclasses + from_dict parsers
    const.py              enums, header names, paths
    exceptions.py         DriivzError, AuthError, RateLimitError, ApiError
  __init__.py             setup/unload, platforms, entry migration hook
  manifest.json           domain mer, iot_class cloud_polling, no external requirements
  const.py                DOMAIN, CONF_*, defaults
  config_flow.py          user → site → stations; options; reauth
  coordinator.py          MerCoordinator(DataUpdateCoordinator[MerData])
  entity.py               MerEntity base, device_info helpers
  sensor.py, binary_sensor.py, button.py
  diagnostics.py          redacted config-entry diagnostics
  strings.json, translations/en.json
tests/                    pytest, fixtures/*.json (sanitised captures)
docs/api.md               endpoint reference
hacs.json, README.md, LICENSE (MIT), pyproject.toml, requirements_test.txt
.github/workflows/ci.yml  hassfest, HACS validation, ruff, pytest
```

### 3.1 `DriivzDriverClient`

```python
class DriivzDriverClient:
    def __init__(self, session: aiohttp.ClientSession, username: str, password: str,
                 base_url: str = "https://driver.uk.mer.eco") -> None
    async def login(self) -> None                       # GET /login → csrf; POST /login
    async def find_sites_in_bounds(self, bounds: Bounds) -> list[Site]
    async def find_stations_in_bounds(self, bounds: Bounds) -> list[Station]
    async def find_stations_by_ids(self, ids: Iterable[int]) -> list[Station]
    async def find_station_by_id(self, station_id: int, billing_plan_id: int | None = None) -> Station
    async def find_last_active_charge_socket(self) -> ActiveSocket | None
    async def find_current_transaction_start_time(self, socket_id: int) -> datetime | None
    async def find_current_transaction_estimate(self, socket_id: int) -> SessionEstimate | None
    async def start_charge(self, socket_id: int) -> None    # raises ApiError unless PENDING
    async def stop_charge(self, socket_id: int) -> None
    async def find_wallet(self) -> Wallet
    async def find_transactions(self, customer_id: int, start: datetime, end: datetime) -> list[Transaction]
    @property
    def rate_limit_remaining(self) -> int | None
```

- The client owns a dedicated `aiohttp.CookieJar` (`unsafe=False`) and stores the CSRF
  token. The caller passes the HA-managed session created with that jar; in HA this is
  `async_create_clientsession(hass, cookie_jar=jar)` so the integration does not share
  cookies with other integrations.
- `_request(method, path, *, json=None, form=None, params=None)` adds headers, sends,
  parses the envelope, records `X-Rate-Limit-Remaining`, and maps failures:
  - HTTP 401/403, or `errorType` in a small set of auth types, or an HTML login page in
    place of JSON → `AuthError`. `_request` performs one `login()` and retries once; a
    second failure propagates `AuthError`.
  - HTTP 429 → `RateLimitError`.
  - `success: false` → `ApiError(error_type, message_key)`.
  - Transport errors → `DriivzConnectionError` (subclass of `DriivzError`).
- A lock serialises re-login so concurrent calls do not each log in.
- Models are `@dataclass(frozen=True, slots=True)` with a `from_dict` classmethod that
  uses `.get()` everywhere and leaves unknown enum strings as-is. Energy from history is
  converted Wh → kWh in the model.

### 3.2 Coordinator and data

```python
@dataclass
class MerData:
    stations: dict[int, Station]          # id → latest short DTO with socket statuses
    details: dict[int, Station]           # id → full DTO (names, prices), refreshed hourly
    active: ActiveSession | None          # socket id, station id, start, energy, cost
    wallet: Wallet | None
    last_transaction: Transaction | None
    customer_id: int | None
```

Per cycle (default 60 s, options 30–600):

1. `find_stations_by_ids(configured ids)` — 1 call.
2. `find_last_active_charge_socket()` — 1 call.
3. If active: start time + estimate — 2 calls, only while charging.
4. Every 15 min: wallet, and history for the last 30 days (first row = last session) — 2 calls.
5. Every 60 min or on first run: `find_station_by_id` per configured station for socket
   names, prices and model — N calls, spread across the run.

Budget: about 2 calls/min idle, 4 while charging. If `rate_limit_remaining` is `<= 1`
after a cycle, the next cycle is skipped and a warning logged once per hour.
Any `AuthError` after the client's own retry → `ConfigEntryAuthFailed` (reauth flow).
`RateLimitError`, `DriivzConnectionError`, `ApiError` → `UpdateFailed`.

Button presses call the client directly, then `coordinator.async_request_refresh()` after
a 5 s delay so the new socket status shows promptly.

### 3.3 Config flow (revised 2026-09-23)

> **Superseded in part (2026-09-23, later):** chargers are grouped into one subentry per
> *site* (type `site`, `station_ids` list, entry `VERSION` 3) rather than one per charger;
> see `docs/decisions.md`, "one subentry per site, not per charger".

The config entry is the **account**. **Data**: `username`, `password`, `base_url`.
**Options**: `scan_interval` only. Title: the username. Entry `VERSION = 2`; version 1
entries (site + station list in options) are not migrated, since none existed outside
the dev instance when the shape changed.

`user` step: email, password. Validate with `login()`. Errors: `invalid_auth`,
`cannot_connect`, `unknown`. `unique_id = username.lower()`; abort if configured. Creates
the entry immediately; with no chargers the coordinator polls the account only.

**Chargers are config subentries** of type `charger`, one per station, added from the
integration page's "Add charger" button (`MerChargerSubentryFlow`):

1. `user`: text field `search`. Log in with the entry's credentials first (abort
   `invalid_auth` / `cannot_connect`). Fetch all sites with a whole-UK bounds call,
   filter case-insensitively on `dn`, present a `SelectSelector` of up to 25 matches
   labelled `"<name> (<n> sockets, <status>)"`. No match → error `no_sites`.
2. `site_select`: pick one site.
3. `stations`: `find_stations_in_bounds` around the chosen site's coordinates (±0.003°)
   then keep those whose `find_station_by_id` says `siteId == site_id`. Stations that
   already have a subentry are hidden (all hidden → abort `all_stations_configured`).
   Multi-select, nothing pre-selected, must select ≥ 1.
4. One subentry per ticked station: `data = {station_id, station_name, site_id,
   site_name}`, `title = "<station> (<site>)"`, `unique_id = "station_<id>"`. A flow
   returns one subentry, so the others are added via
   `hass.config_entries.async_add_subentry` first.

Adding or removing a subentry notifies the entry's update listener, which reloads the
entry. Removing one also has HA delete the devices and entities registered under that
subentry, so no `async_remove_config_entry_device` hook is needed.

Options flow: a single `init` form with the interval (number 30–600).

Reauth flow: `reauth_confirm` asks for the password only, validates, updates data,
reloads.

### 3.4 Devices and entities

Device identifiers: `(mer, "account_<entry_id>")` and `(mer, "station_<id>")` with
`via_device` the account device; there is no site device (revised 2026-09-23, since one
entry can hold chargers from several sites). Each charger's entities are added under
its subentry id. Station device: name = caption with
`[RESTRICTED ACCESS]` and `(MER-FS-…)` removed and trimmed, manufacturer `Mer`,
model `stationModelName`, serial `identityKey`, `configuration_url` = portal map link.

Unique ids: `<entry_id>_<device>_<key>`; socket entities use the socket id.

| Device | Entity | Platform | Value |
|---|---|---|---|
| Station | `status` | sensor (enum) | `stationStatusId` lower-cased |
| Station | `identity_key` | sensor (diagnostic) | e.g. `MER-FS-AD00137` |
| Socket | `<socket name> status` | sensor (enum) | `socketStatusId` |
| Socket | `<socket name> available` | binary_sensor (no device class, icon `mdi:ev-station`) | `True` iff status == `AVAILABLE` |
| Socket | `<socket name> price` | sensor | `kwhPrice` for the user's plan (first `socketPrices` entry), unit `GBP/kWh` |
| Socket | `<socket name> max power` | sensor (diagnostic) | `maximumPower` kW |
| Socket | `<socket name> start charge` | button | `start_charge(socket_id)` |
| Account | `any socket available` | binary_sensor | any socket on any added charger AVAILABLE |
| Account | `available sockets` | sensor | count over added chargers |
| Account | `sockets in use` | sensor | count with in-use statuses |
| Account | `charging` | binary_sensor | `active is not None` |
| Account | `active session station` | sensor | cleaned caption or `none` |
| Account | `active session started` | sensor (timestamp) | |
| Account | `active session energy` | sensor (energy, kWh) | from estimate |
| Account | `active session cost` | sensor (monetary GBP) | from estimate |
| Account | `stop charge` | button | `stop_charge(active.socket_id)`; raises if idle |
| Account | `last session energy` / `cost` / `started` | sensors | from last history row |
| Account | `wallet balance` | sensor (monetary) | `accountBalance` |

Entity availability follows the coordinator; socket entities are additionally
unavailable when their station is missing from the latest response.

### 3.5 Error handling summary

- Auth failure → reauth flow, entities unavailable, one log line.
- Connection / 5xx / rate limit → `UpdateFailed`; entities unavailable until recovery.
- Button failures → `HomeAssistantError(f"Mer: {error_type}")` shown in the UI; translated
  message keys for the common types (`OPERATION_NOT_ALLOWED_IN_CURRENT_SOCKET_STATE`,
  `INSUFFICIENT_PERMISSIONS`).
- Unknown enum values pass through lower-cased; enum sensors declare the known options and
  fall back to `unknown` for anything else so recorder statistics stay valid.
- Diagnostics redact username, password, cookies, CSRF token, customer ids, addresses.

## 4. Testing

- Targets Home Assistant 2026.9.2 (current release) and the Python version it requires
  (3.13 or newer; local development runs 3.14).
- `pytest`, `pytest-homeassistant-custom-component` 0.13.365 (pinned to the HA release in
  `requirements_test.txt`), `aioresponses` 0.7.9 for HTTP, `syrupy` snapshots for entity
  registries.
- Fixtures under `tests/fixtures/` are the sanitised captures from 2026-09-17:
  login page HTML (csrf meta), login success/failure JSON, `findSitesInBounds`,
  `findStationsByIds`, `findStationById` (restricted NETPark station with prices),
  `findLastActiveChargeSocket` idle and (synthetic until confirmed) active,
  `findDriverChargeTransactionLogByView`, wallet, and `getStationCapabilitiesAndValidate`.
- Client tests: csrf extraction, login form fields/headers, re-login-once on 403, envelope
  errors → `ApiError`, 429 → `RateLimitError`, rate-limit header tracking, JSON vs form
  body selection, model parsing including Wh→kWh and missing fields.
- Config flow tests: happy path through all three steps, invalid auth, no site match,
  duplicate entry abort, options (interval and stations), reauth.
- Coordinator tests: request budget per cycle (idle vs charging), skip-on-rate-limit,
  `ConfigEntryAuthFailed` propagation, periodic wallet/history/detail refresh timing.
- Entity tests: state mapping for every status, site aggregates, button success and
  failure, unavailability when a station disappears.
- Manual verification with the real account: monitor Explorer 1 & 2, then one supervised
  start/stop on a free NETPark socket (explicit user confirmation required before any
  real start or stop).

## 5. Implementation phases

1. Repo scaffold, CI, client package with tests (no HA yet).
2. Config flow + coordinator + station/socket/site entities (monitoring goal).
3. Start/stop buttons, active session and last session entities, wallet.
4. README, `docs/api.md`, HACS metadata, first tagged release, live verification.

Later phases (separate specs): websocket push, notify-me, history statistics, cards and
vehicles, multi-tenant (other Driivz portals).
