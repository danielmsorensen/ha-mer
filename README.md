# Mer EV Charging for Home Assistant

An unofficial [HACS](https://hacs.xyz) integration for Mer UK electric vehicle
chargers. It watches the sockets on the chargers you choose to monitor and
reports whether each one is free, occupied or charging, and it lets you
start and stop a charge on them from Home Assistant. It also surfaces your
active charging session, your last completed session, and your Mer wallet
balance.

## Disclaimer

This integration is unofficial. It is not affiliated with, endorsed by, or
supported by Mer or Driivz. There is no public Mer API: every request this
integration makes was reverse-engineered from the traffic the Mer driver
portal (driver.uk.mer.eco) sends to its own backend. Mer and Driivz owe this
project nothing, can change the portal at any time without notice, and doing
so may break this integration without warning. Use it at your own risk, on
your own account, for your own personal use.

## Installation

1. In Home Assistant, go to **HACS → Integrations → ⋮ (top right) → Custom
   repositories**, and add this repository's URL with category **Integration**.
2. Find **Mer EV Charging** in HACS and install it.
3. Restart Home Assistant.

## Setup

Go to **Settings → Devices & services → Add integration**, and search for
**Mer EV Charging**. Setup is three steps:

1. **Sign in** with the e-mail and password you use for the Mer Connect /
   driver portal app.
2. **Search for your site** by typing part of its name — the search matches
   anywhere in the name, so "NETPark" is enough to find "Durham County
   Council - Business Durham NETPark" without typing it in full. Pick your
   site from the matches.
3. **Tick the chargers** you want Home Assistant to monitor. Every charger
   you select becomes its own device, with a device per socket entity
   attached to it.

## Entities

Names below are the entity's own name; the full entity name Home Assistant
shows is "*device name* *entity name*", e.g. "Explorer 1 status" or "Mer
account wallet balance".

### Site device

One device per configured site, aggregating all the chargers you selected.

| Entity | Platform | Meaning |
|---|---|---|
| Available sockets | sensor | Count of configured sockets currently `AVAILABLE` |
| Sockets in use | sensor | Count of configured sockets in any "in use" state |
| Any socket available | binary sensor | On if any configured socket is `AVAILABLE` |

### Charger (station) device

One device per charger you selected during setup.

| Entity | Platform | Meaning |
|---|---|---|
| Status | sensor | The charger's own status (`available`, `charging`, `faulted`, …) |
| Identity key | sensor (diagnostic) | The charger's portal identity key, e.g. `MER-FS-AD00137` |
| Session energy | sensor | Energy delivered by *your* active session, only while it is running on this charger |
| Session cost | sensor | Cost of that active session so far |
| Session started | sensor | When that active session started |
| Session duration | sensor | How long that active session has been running |
| My session here | binary sensor | On while your active session is running on this charger |
| Stop charge | button | Stops your active session, but only if it is running on this charger |
| Notify me when available | switch | See [The notify-me switch](#the-notify-me-switch) below |

Each socket on the charger adds its own entities to the same charger device,
named after the socket (a charger can have more than one — some Mer chargers
have a "Left" and a "Right" socket):

| Entity | Platform | Meaning |
|---|---|---|
| *Socket* status | sensor | The socket's own status |
| *Socket* available | binary sensor | On if this socket is `AVAILABLE` |
| *Socket* price | sensor | Your tariff's price per kWh on this socket |
| *Socket* max power | sensor (diagnostic) | The socket's maximum power in kW |
| *Socket* start charge | button | Starts a charge on this socket |

### Account device

One device per Mer account, covering your active session (wherever it is
running) and your charging history.

| Entity | Platform | Meaning |
|---|---|---|
| Charging | binary sensor | On while you have an active session, on any charger |
| Active session charger | sensor | Name of the charger your active session is running on |
| Active session socket | sensor | Name of the socket your active session is running on |
| Active session started | sensor | When the active session started |
| Active session energy | sensor | Energy delivered so far in the active session |
| Active session cost | sensor | Cost so far in the active session |
| Active session duration | sensor | How long the active session has been running |
| Stop charge | button | Stops the active session, wherever it is running |
| Last session energy | sensor | Energy delivered in your last completed session |
| Last session cost | sensor | Cost of your last completed session |
| Last session started | sensor | When your last completed session started |
| Wallet balance | sensor | Your Mer account's wallet balance |

## Options

**Settings → Devices & services → Mer EV Charging → Configure** gives you two
things:

- **Poll interval** — how often, in seconds, Home Assistant polls the portal.
  Configurable between 30 and 600 seconds; the default is 60.
- **Change site or chargers** — re-run the site search and charger selection
  from setup, seeded with your current choices.

Changing either setting reloads the integration so the new value takes
effect immediately. If you remove a charger from the selection, its device
and all of its entities are removed too.

If your Mer password changes and the integration can no longer log in, Home
Assistant raises a repair/reauthentication prompt on the integration; follow
it and enter the new password to resume without redoing the whole setup.

## Example automation

Get a phone notification on weekday mornings if a socket frees up at your
work site, so you know before you set off whether you'll get a spot:

```yaml
alias: Notify when a NETPark socket frees up
triggers:
  - trigger: state
    entity_id: binary_sensor.netpark_any_socket_available
    to: "on"
conditions:
  - condition: time
    after: "07:00:00"
    before: "09:30:00"
    weekday:
      - mon
      - tue
      - wed
      - thu
      - fri
actions:
  - action: notify.mobile_app_your_phone
    data:
      title: NETPark charger free
      message: A socket at NETPark is available now.
mode: single
```

The entity id above follows from the site device's own name (Developer Tools
→ States is the reliable way to find your actual id); to watch one specific
charger instead of the whole site, trigger on that charger's own `*_available`
binary sensor, e.g. `binary_sensor.explorer_1_available`.

## Polling and rate limits

The portal returns an `X-Rate-Limit-Remaining` header on every response, and
this integration tracks it. At the default 60-second interval it makes
roughly two portal calls a minute while idle, and about four a minute while a
session is active (the extra calls fetch the live session's duration and
energy estimate). Every 15 minutes it also refreshes your wallet balance and
last session, and once an hour it refreshes each charger's socket names and
prices.

If the portal reports that its rate-limit budget is nearly exhausted, the
integration deliberately skips the next poll cycle rather than pushing the
limit further — entities keep showing their last known values through that
skipped cycle rather than going unavailable. This is intentional and not a
bug: one stale cycle is a smaller cost than being rate-limited outright.

## The notify-me switch

The **Notify me when available** switch on each charger device mirrors the
Mer app's own "notify me when this charger frees up" subscription — flipping
it on subscribes you with Mer, flipping it off unsubscribes, and it reflects
whatever the portal currently has recorded for that charger.

One caveat: this only controls Mer's own subscription and Mer's own delivery
of that notification. On the account this integration was developed against,
Mer's delivery of that notification did not actually arrive. The dependable
way to get notified is the automation above, triggered on the socket
availability binary sensor — Home Assistant then owns the alert end to end,
independent of whether Mer's own notification ever shows up.

## Development

Home Assistant core cannot be imported on native Windows Python, so the local
test environment runs inside WSL. `scripts/bootstrap-dev` creates (or
updates) the WSL virtual environment and installs the test dependencies into
it; run it once, and again whenever `requirements_test.txt` changes.

From then on:

- `scripts/test` runs the test suite (`pytest`, with any arguments passed
  through).
- `scripts/lint` runs `ruff check` and `ruff format --check` — read-only, for
  CI parity.
- `scripts/format` runs `ruff format` to fix formatting in place.

All three scripts shell out to `wsl.exe` under the hood, so they work from a
normal Windows shell without you needing to open a WSL terminal yourself.
