# Mer EV Charging for Home Assistant

An unofficial [HACS](https://hacs.xyz) integration for Mer UK EV chargers. It shows
whether each socket on the chargers you pick is free, occupied or charging, starts and
stops charges, and reports your active session, last session and wallet balance.

> **Unofficial.** Not affiliated with or supported by Mer or Driivz. There is no public
> Mer API: everything here was reverse-engineered from the Mer driver portal
> (driver.uk.mer.eco), which can change without notice and break this integration. Use
> it at your own risk, on your own account.

## Installation

1. In HACS, open **⋮ → Custom repositories** and add this repository with category
   **Integration**.
2. Install **Mer EV Charging (UK)** from HACS and restart Home Assistant.

## Setup

**Settings → Devices & services → Add integration → Mer** asks only for the e-mail and
password of your Mer app account. That creates the account device; chargers come next.

On the integration's page, **Add charger**:

1. **Search for the site** by any part of its name as the Mer app shows it ("riverside"
   finds "Riverside Retail Park - Anytown"), and pick it. The list ends with **Search
   again**.
2. **Tick the chargers** to monitor. Each becomes a device in that site's group; chargers
   already added are not offered. A **go back** checkbox returns to the site list.

A site's menu has **Change chargers** (untick to stop monitoring one) and **Delete**.
The account device sits under Home Assistant's "Devices that don't belong to a
sub-entry", since it belongs to the whole integration.

**Configure** has one option, the poll interval: 30–600 seconds, default 60. If your
password changes, Home Assistant prompts you to re-authenticate.

## Entities

Entity names are prefixed with the device name, e.g. "Charger A Left status" or "Mer
account wallet balance". A charger's model and serial number are on its device page.

### Charger device

| Entity | Meaning |
| --- | --- |
| Status | The charger's own status (`available`, `charging`, `faulted`, …) |
| Available sockets | Free sockets on this charger; `total_sockets` attribute |
| My session here | On while your session runs on this charger; `socket` attribute |
| Session energy, cost, charging rate, started, duration | Your session on this charger; unavailable otherwise |
| Stop charge | Stops your session if it is on this charger; greyed out otherwise |
| Notify me when available | Mer's own notify-me subscription; see [below](#the-notify-me-switch) |

Each socket (often "Left" and "Right") adds:

| Entity | Meaning |
| --- | --- |
| *Socket* status | The socket's status, whoever is using it. Attributes: `charger`, `socket`, `start_button`, `my_session`, `max_power_kw`, `connector` |
| *Socket* price | Your tariff's price per kWh; billing plan, fixed price, per-minute rate and transaction fee as attributes |
| *Socket* start charge | Starts a charge; greyed out unless the socket is free (or plugged in and waiting) and the portal allows you a start on it |

### Account device

| Entity | Meaning |
| --- | --- |
| Available sockets | Free sockets on your chargers. Attributes: `available_sockets` (each with `charger`, `socket`, `station_id`, `socket_id`, `start_button`, in charger order), `total_sockets`, `chargers` |
| Sockets in use | Sockets on your chargers in any in-use state |
| Charging | On while you have an active session anywhere |
| Active session | Where it is running, as *charger socket*, with ids as attributes |
| Active session started, energy, cost, duration | As named; unavailable while not charging |
| Active session charging rate | The portal's estimated rate in kW at the charger, refreshed with each meter reading (the car sees roughly 10% less) |
| Active session price | Price per kWh on the socket in use, with the tariff summary and plan as attributes |
| Stop charge | Stops the active session wherever it is; greyed out while not charging |
| Last session energy, cost, started | Your last completed session |
| Wallet balance | Your Mer wallet |

Diagnostic entities on the account device:

| Entity | Meaning |
| --- | --- |
| Live status | On while the portal's push channel is connected; `connected_since` attribute |
| Last poll | Last successful poll; the attributes give the poll interval, whether the last poll succeeded, and its error |
| Portal requests remaining | The portal's rate-limit headroom after the last request |
| Last command | Outcome of the last start or stop, with charger, socket, method, times and any error |
| Refresh now | Polls the portal immediately |

Session durations are in hours. Nothing pushes them, so they change on each poll; for a
live readout use the *started* timestamp, which dashboards show as "2 hours ago" and keep
ticking.

## Starting and stopping

When you press a start button, the integration asks the portal what your account may do
on that socket, as the Mer web app does. It sends the normal start (approved against your
charging card) or, if only that is allowed, "Charge now". If neither is allowed it fails
at once with the portal's reason. The button is greyed out when the socket's status
rules out a start, or when the portal refuses you a start there for another reason, such
as your card. That check is re-read a few seconds after any socket on the charger changes,
and on **Refresh now**. The "Charge now" path is untested live: the Mer app's virtual card
is refused for it.

A press stays open until the charger shows the outcome: the button spins, then shows a
tick or a red cross with the reason. The outcomes are:

| Outcome | Means |
| --- | --- |
| **Charging** | The charge has started |
| **Ready, plug in** | The socket was free and the charger is waiting for the cable |
| **Stopped** | The charge has ended |
| **Rejected** | The portal refused the command |
| **Not confirmed** | Nothing visible happened within 60 s, though the portal accepted the command |

The portal does not say how long a free socket waits for the cable, so press start at the
charger, or plug in first. Each outcome goes to **Last command** and is fired as a
`mer_command_result` event (`command`, `result`, `station_name`, `socket_name`,
`message`):

```yaml
triggers:
  - trigger: event
    event_type: mer_command_result
actions:
  - action: notify.mobile_app_your_phone
    data:
      message: >-
        {{ trigger.event.data.command | capitalize }} on
        {{ trigger.event.data.station_name }} {{ trigger.event.data.socket_name }}:
        {{ trigger.event.data.result }}
```

## Blueprint

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fdanielmsorensen%2Fha-mer%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fmer%2Foffer_free_charger.yaml)

**Mer: Free charger notification.** You choose:

- a person or device tracker, such as a car's;
- a zone;
- the sockets to watch, in order of preference, by picking their *socket* status sensors;
- your phone.

When you arrive, and whenever a socket frees up while you are there and not charging, it
sends **Mer charger free** with the charger's name and a button such as **Start Right**.

- Arriving with everything taken sends **No Mer charger free**.
- Tapping Start presses that socket's start button, or tells you the socket has been
  taken.
- The notification clears once you are charging.
- **Preview:** choose **Run actions** from the automation's ⋮ menu. It sends what
  arriving would send right now, skipping the zone and charging checks. If nothing is
  free it sends **No Mer charger free** even when that option is off. Its Start button
  is real.

### In the car

**Android Auto** shows the notifications, Start button included, on the car's screen.
On the phone:

1. Use the Home Assistant Companion app.
2. In Android Auto's settings, keep Home Assistant enabled in the launcher. If it is
   hidden there, the notifications reach the phone but not the car.
3. If the notification doesn't pop up over the map, open the Home Assistant app's
   notification settings on the phone and set its **Mer charger** channel to pop up on
   screen. The channel appears after the first notification.

**CarPlay** can't show the notification usefully. The iPhone app only puts *critical*
notifications on the CarPlay screen, and those can't have buttons. Instead, add your
sockets' start buttons to CarPlay under **Companion App Settings → CarPlay → Quick
Access**. A button only acts while the portal allows a start. Android Auto can do the same
from **Settings → Companion app → Android Auto favorites**, when parked.

### Your own automations

The socket status attributes and the account's `available_sockets` list are enough to
work without naming chargers. For example, to press the first free socket's button:

```yaml
actions:
  - action: button.press
    target:
      entity_id: >-
        {{ (state_attr('sensor.mer_account_available_sockets',
        'available_sockets') | first).start_button }}
```

To trigger on "anything free", use a numeric state trigger on that sensor, `above: 0`.

## Sessions

The portal reports one active session per account, wherever it is. On a charger you have
not added, such as a public one, the account's session sensors, price and **Stop charge**
all still work. If you run two charges at once, the integration follows the most recently
started one; the other socket still shows **Charging**, but not as yours.

## Live status and polling

The integration keeps open the websocket the Mer web app uses. While it is connected,
**Live status** is on and polling drops to every 5 minutes.

- **If it drops:** one poll runs straight away, polling returns to the configured
  interval, and it reconnects with growing delays of up to 5 minutes.
- **If it goes quiet:** a channel silent for 3 minutes is treated as dropped and reopened
  after a fresh login.

Pushes do not count against the portal's rate limit.

| Data | Pushed | Polled |
| --- | --- | --- |
| Charger and socket status | As it changes | Every poll |
| Your session's energy, cost, charging rate | About every 45 s | Every poll |
| Session start and end | End: as the socket stops charging. Start: on its first estimate, which triggers a poll | Every poll |
| Session started time and duration | No | Every poll |
| Wallet, last session | No | Every 15 minutes |
| Socket names, tariffs, model, notify-me state | No | Hourly, one charger per poll |
| Which start a socket allows | No | A few seconds after a socket on the charger changes, and on **Refresh now** |

Extra polls run after a start or stop resolves, when the channel drops, on **Refresh
now** and on reload. While a command waits for its outcome it also polls every 5 seconds
if nothing is pushed.

**Rate limits.** Every portal response carries `X-Rate-Limit-Remaining`, which is about 9
at rest; how quickly it refills is unknown. A poll makes two calls while idle and four
during a session. Setup and reload fetch every charger's details at once. When the
headroom is nearly used up, the next poll is skipped and the entities keep their last
values.

## The notify-me switch

The switch mirrors the Mer app's "notify me when this charger frees up" subscription.
Mer's own notification did not reliably arrive during development, so use the blueprint
for alerts you can depend on.

## Development

Home Assistant cannot be imported on native Windows Python, so the tooling runs in WSL.
Run `scripts/bootstrap-dev` once, and again whenever `requirements_test.txt` changes. It
creates `~/.venvs/ha-mer`. The scripts call `wsl.exe` themselves, so you can run them
from Windows:

| Script | Does |
| --- | --- |
| `scripts/test` | `pytest`, passing your arguments through |
| `scripts/lint` | `ruff check` and `ruff format --check` |
| `scripts/format` | `ruff format` |
| `scripts/dev-hass` | A throwaway Home Assistant instance at <http://localhost:8123>, config in `~/ha-mer-dev`; `--reset` wipes it |
| `scripts/release <version> "<notes>"` | Sets the manifest version, commits, tags `v<version>`, pushes, and publishes the GitHub release. CI fails a tag that does not match the manifest |

On Linux or macOS, `pip install -r requirements_test.txt` into a virtualenv and run
`pytest` and `ruff` directly.

**WSL needs a compiler.** Home Assistant's voice packages must be built from source.
Without a compiler, the dev UI fails with `No module named 'pymicro_vad'`.
`scripts/bootstrap-dev` warns when the compiler is missing. Install it once; it asks for
your WSL password:

```bash
wsl -d Ubuntu -- sudo apt update
```

```bash
wsl -d Ubuntu -- sudo apt install -y build-essential python3-dev
```

**VS Code.** The configurations in `.vscode/`:

- **F5** on "Dev Home Assistant (start, open, debug)" starts the dev instance, attaches
  the debugger on port 5678 and opens the UI. **Disconnect** (Shift+F5) stops the
  instance.
- **Ctrl+Shift+B** starts the instance without the debugger.
- "Attach only" attaches to an instance that is already running. With nothing running it
  fails with `ECONNREFUSED`.
- If your WSL user or config directory differs from the defaults, adjust `remoteRoot` in
  `launch.json`.

Only one instance runs at a time. A second one reports "Another Home Assistant instance
is already running".

**Brand images** live in `custom_components/mer/brand/`, where Home Assistant serves them
directly. `scripts/generate_brand_icon.py` rebuilds them from the logo on Mer's website.

**HACS updates.** HACS checks custom repositories for new releases every few hours.
**Update information** on the repository's HACS page checks now.
