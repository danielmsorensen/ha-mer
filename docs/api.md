# Driivz driver-portal API reference

This documents the web API the Mer UK driver portal (`https://driver.uk.mer.eco`)
uses internally, as reverse-engineered from its own traffic on 2026-09-16/17. The
portal is a white-label **Driivz** driver portal; nothing here is published or
supported by Mer or Driivz, and any of it can change without notice. This
integration talks to the endpoints in [Endpoints used in v1](#endpoints-used-in-v1)
only; everything else is recorded here for completeness and for anyone extending
the integration later.

All endpoints live on the portal host. Responses are JSON envelopes:

```json
{"success": true, "errors": [], "data": ...}
```

or, on failure:

```json
{"success": false, "errors": [{"errorType": "...", "messageKey": "..."}], "data": null}
```

## Authentication

- `GET /login` returns an HTML page with `<meta name="_csrf" content="<uuid>">`,
  `<meta name="_csrf_header" content="X-CSRF-TOKEN">`, and
  `<meta name="_csrf_parameterName" content="_csrf">`.
- `POST /login`, `application/x-www-form-urlencoded`, fields `username`,
  `password`, `_spring_security_remember_me=true`, `_csrf=<token>`; headers
  `X-Ajax-call: true`, `X-APP-TYPE: WEB`, `X-CSRF-TOKEN: <token>`. Success: HTTP
  200, `{"success": true, "targetUrl": "/findCharger"}`. Failure: `success:
  false` with an `errorType` (observed in the portal's own JS:
  `USER_IS_NOT_ACTIVE`, `PASSWORD_EXPIRES` with a `token`, and generic types
  rendered from `enum.ErrorType.*`). HTTP 403 means the CSRF token or session is
  stale.
- Session state is cookie based (Spring Security).
  `SPRING_SECURITY_REMEMBER_ME_COOKIE` carries a JWT valid 24 hours; the
  servlet session cookie is `HttpOnly`. The CSRF token can change after login,
  so it is re-read from any HTML page (`/findCharger`) once logged in.
- reCAPTCHA Enterprise is disabled on this tenant
  (`reCaptchaEnterpriseEnabled: false`), which is why scripted login works at
  all. If it is ever turned on, login will start failing with a recaptcha
  error that needs to surface clearly rather than being mistaken for bad
  credentials.
- `POST /logout` with the CSRF token as the body ends the session.

## Request conventions

Every API call sends `X-CSRF-TOKEN`, `X-APP-TYPE: WEB`, `X-JSON-TYPES: None`
(the portal's own browser client also sends `Accept-Language`, which is not
required). Two request body styles exist, and the server is strict about
which one each endpoint expects:

- **JSON** (`Content-Type: application/json`): the map and station query
  endpoints, which take a `queryInfo`-shaped object.
- **Form** (`application/x-www-form-urlencoded`): most other facade calls.

Every response carries `X-Rate-Limit-Remaining` (observed at 9 at rest,
decrementing per burst of calls). **The refill window was never measured.**
Nothing in this repository establishes how long the budget takes to return to
9, or whether it refills steadily or resets on a fixed boundary — only that it
was seen at 9 while idle. Treat any sizing of the poll interval or of a
per-cycle call budget against that number as an unverified assumption, not as
a checked constraint. Anonymous access works for public station data; a
logged-in session is required for restricted chargers' tariffs, sessions,
history, wallet, and starting or stopping a charge.

## Endpoints used in v1

| Purpose | Method / path | Body | Notes |
|---|---|---|---|
| Sites in map bounds | `POST stationFacade/findSitesInBounds` | JSON `{"filterByBounds": {"northEastLat","northEastLng","southWestLat","southWestLng"}, "filterByIsManaged": true}` | Whole-UK bounds works in one call. Site DTO: `id`, `dn` (name), `ss` (status), `ns` (socket count), `sal` (access level), `latitude`, `longitude`, `scs` (speed), `im` (managed) |
| Stations in map bounds | `POST stationFacade/findStationsInBounds` | JSON, same `queryInfo` shape; optional `filterByStationStatuses`, `filterBySocketTypes`, `filterByChargingSpeeds`, `filterBySiteAccessLevels`, `filterByBillingPlanIds` (observed as non-restrictive) | Short station DTO: `id`, `caption`, `latitude`, `longitude`, `stationStatusId`, `comingSoon`, `isManaged`, `stationSockets[{id}]` |
| Stations by id | `POST stationFacade/findStationsByIds` | JSON `{"filterByIds": [ids]}` | Same DTO as above but includes `stationSockets[].socketStatusId` and `maximumPower`. This is the integration's primary polling call. |
| Station detail | `GET stationFacade/findStationById?stationId=&billingPlanId=` | query string | Full DTO: address, `identityKey`, `siteId`, `siteName`, `stationModelName`, `stationOwnerName`, `siteStationAccessLevel`, `stationSockets[]` with `id`, `name` (e.g. Left/Right), `identityKey`, `socketStatusId`, `maximumPower`, `stationModelSocketSocketTypeId`, `stationModelSocketVoltageType`, `socketPrices[{billingPlanId, billingPlanCode, kwhPrice, plugInMinuteRate, transactionFee, currency}]` |
| Start charge | `POST stationFacade/startChargeNow` | form `stationSocketId` | Success is `data.operationStatus == "PENDING"`; the charger then waits for the cable to be connected |
| Stop charge | `POST stationFacade/stopCharge` | form `stationSocketId` | |
| Active session | `POST stationFacade/findLastActiveChargeSocket` | form, empty | `data` is absent/null when idle; when charging, returns the active socket's DTO, including `stationCaption` |
| Session transaction | `POST stationFacade/findCurrentTransactionStartTime` | form `stationSocketId` | See [`findCurrentTransactionStartTime` returns no start time](#findcurrenttransactionstarttime-returns-no-start-time) below |
| Session estimate | `POST stationFacade/findCurrentTransactionBillingChargingEstimation` | form `socketId` | Energy/cost estimate for the running transaction. See [`totalKw` is kWh, not power](#totalkw-is-kwh-not-power) below |
| Charge history | `POST customerFacade/findDriverChargeTransactionLogByView` | JSON `{"filterByStartedOnFrom": ms, "filterByStartedOnTo": ms, "filterByMemberId": <customerDetailId>}` | An empty body returns `INSUFFICIENT_PERMISSIONS`; `filterByMemberId` is required. Row: `id`, `stationId`, `caption`, `siteName`, `startOn`, `stoppedOn`, `durationTime` (seconds), `totalEnergy` (**watt-hours**), `cost`, `currency`, `billingPlanName`, `chargeTransactionBillingStatus`, `socketType`, `startInitiator` |
| Wallet | `POST billingFacade/findCustomerDetailWalletByCustomerId` | form, empty | `accountBalance`, `currency`, `customerDetailId`, `customerDetailMemberId`, `accountNumber`, `timezoneZoneId`. Also the only source of the customer id used to filter charge history |
| Notify-me subscription status | `POST customerFacade/isDriverSubscribedToNotifyMeWhenStationIsAvailable` | form `stationId` | Boolean. Backs the "Notify me when available" switch |
| Subscribe to notify-me | `POST customerFacade/notifyMeWhenStationIsAvailable` | form `stationId` | |
| Unsubscribe from notify-me | `POST customerFacade/unSubscribeFromNotifyMeWhenStationIsAvailable` | form `stationId` | |

### `findCurrentTransactionStartTime` returns no start time

Despite its name, this endpoint's response carries no timestamp at all. A
real payload looks like:

```json
{"boostEnabled": false, "transactionId": 9088676, "txDuration": 953622}
```

`txDuration` is milliseconds *elapsed* since the transaction began, not a
duration setting and not a start time. The only way to get a start time out
of this endpoint is to subtract that elapsed duration from the current wall
clock at the moment the response arrives: `started_at = now - txDuration`.
That is what this integration does (`ActiveTransaction.started_at` in
`custom_components/mer/driivz/models.py`); it is an approximation good to
about one poll interval, not an exact timestamp from the portal.

### `totalKw` is kWh, not power

`findCurrentTransactionBillingChargingEstimation`'s response includes a field
named `totalKw`. The name reads as an instantaneous power reading (kilowatts),
but it is actually the cumulative energy delivered so far, in kilowatt-hours.

This was established from a live session: `totalKw` read `1.606` after the
transaction had been running for `953` seconds (from the `txDuration` above)
on a socket whose `maximumPower` is `7.4` kW. Treating `1.606` as an average
power over that time gives:

```
1.606 kWh / (953 s / 3600 s/h) ≈ 1.606 / 0.2647 h ≈ 6.07 kW
```

roughly 6 kW average draw on a 7.4 kW socket — a plausible average power for
an EV charge, and far too small a number to be a power reading taken directly
(an instantaneous 1.6 kW draw on a 7.4 kW socket would mean the car was barely
drawing current). The arithmetic only makes sense if `totalKw` is energy, so
this integration reads it as kWh (`SessionEstimate.energy_kwh` in
`custom_components/mer/driivz/models.py`, which also tolerates a handful of
other field-name spellings observed across the codebase in case another
tenant or portal version names it differently).

## Mapped but unused endpoints

These were found in the portal's own JavaScript while mapping the API, but
nothing in this integration calls them. They are recorded here in case a
later feature needs them.

```
POST billingFacade/findBillingCustomerContractsByCustomerId  form {}            → contracts (billing plans, tariffs)
POST billingFacade/findAvailableBillingPlans                 json {}            → plans
POST customerFacade/findCustomerCars                         form {}            → vehicles
POST customerFacade/findNotifications                        form {}            → notification preferences (stationEventType, isSms, isCellApp)
POST stationFacade/findStationsByStationLandmarkOfCustomer   form {billingPlanId?} → favourites
POST stationFacade/findCustomerReservations                  form {}            → reservations (empty; RESERVATION_IS_NOT_ALLOWED on NETPark)
POST stationFacade/getStationCapabilitiesAndValidate         form {stationId, stationSocketId, socketStatus} → allowed/denied operations
POST stationFacade/findPlacesByQuery / findPlaceDetails      (Google Places proxy)
GET  configurationFacade/getServerConfiguration              → feature flags (authenticated)
GET  configurationFacade/getAnonymousConfiguration           → feature flags (anonymous)
WS   /websocket  (send "0" on open) → JSON with "@c": StationStatusSummaryDtoImp {stationId, stationSocketId, stationSocketStatusDto{socketStatus}}, CustomerDetailChargeEventDtoImp, BillingChargingEstimationMessageImp, ...
```

## What the portal does not expose

`getStationCapabilitiesAndValidate` returns a capability list per socket
(`allowedSocketOperations`, `denySocketOperations`, `socketStatuses`,
`stationStatus`), and the logged-in account's own permissions describe a
wider set of operations than start/stop: **setting the charging current**
(`SET_CHARGE_CURRENT`), **charge-full-speed** (`CHARGE_FULL_SPEED`),
**unlocking a socket** (`UNLOCK_SOCKET`), **charging profiles**, and
**boost**.

None of these has a corresponding endpoint anywhere in the driver portal's
own JavaScript. They are operator-portal operations — actions available to
the site operator, surfaced in the driver-facing capability and permission
data without a driver-facing endpoint to invoke them. This integration does
not implement them, and that omission is deliberate: it was checked against
the actual set of endpoints the portal ships, not assumed.

No `getStationCapabilitiesAndValidate` fixture lives in this repository, so
this claim isn't reproducible from the test suite alone. The check behind it
was this: the driver portal's own JavaScript bundles were searched for
endpoints matching those operation names — `SET_CHARGE_CURRENT`,
`CHARGE_FULL_SPEED`, `UNLOCK_SOCKET`, the charging-profile operations and
boost — and none exists. The names appear only in the shared capabilities and
permissions payload, which is the same payload the operator portal consumes,
and there is no code in the driver portal that would call them. Anyone tempted
to add a "set current" or "boost" service to this integration on the strength
of the capability list should know up front that doing so means guessing at an
unpublished operator-portal endpoint, not calling something the driver portal
already exposes.

## Enumerations

- **Socket / station status**: `AVAILABLE`, `OCCUPIED`, `CHARGING`,
  `DISCHARGING`, `PAUSED`, `PREPARING`, `FINISHING`, `RESERVED`,
  `UNAVAILABLE`, `FAULTED`, `UNKNOWN`. "In use" covers `OCCUPIED`,
  `CHARGING`, `DISCHARGING`, `PAUSED`, `PREPARING`, `FINISHING`.
- **Site access level**: `PUBLIC`, `PRIVATE`, `TAXI_ONLY`, `HOME`. Restricted
  workplace chargers are still `PUBLIC` at the site level; the restriction
  shows only as the caption prefix `[RESTRICTED ACCESS]` and is enforced at
  charge-start time by billing plan.
- **Charging speed**: `SLOW`, `SEMI_FAST`, `FAST`, `ULTRA_FAST`.
- **Socket type**: `TYPE_2_MENNEKES`, `TYPE_COMBO_GERMANY`, `TYPE_4_CHADEMO`
  (others exist on the wider network but were not observed on the chargers
  this integration was developed against).

## Websocket (not used)

`wss://<host>/websocket`; the client sends the literal string `"0"` on open,
and the server pushes JSON messages whose `@c` (or `@class`) field names the
DTO, e.g. `StationStatusSummaryDtoImp` with `stationId`, `stationSocketId`,
`stationSocketStatusDto.socketStatus`, plus `CustomerDetailChargeEventDtoImp`
and `BillingChargingEstimationMessageImp` for session updates. This would let
a future version push updates instead of polling, but it is not used in this
integration.
