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
**Mer**. Setup asks only for the e-mail and password you use for
the Mer Connect / driver portal app. That creates the account, with its own
device and entities, but no chargers yet.

Chargers are added afterwards, from the integration's page (**Settings →
Devices & services → Mer**), with the **Add charger** button. The page groups
chargers by charging site:

1. **Search for the site** by typing any part of its name as it appears in
   the Mer app. The search is not case-sensitive and matches anywhere in the
   name, so one distinctive word is enough: the town, the street or the
   business, for example "riverside" for "Riverside Retail Park - Anytown".
   Pick the site from the matches.
2. **Tick the chargers** you want to monitor at that site. Chargers you have
   already added are not offered again. Every ticked charger becomes its own
   device, with its socket entities attached to it, inside that site's group
   on the integration page. Adding more chargers at a site you already have
   puts them in the same group.

Picked the wrong site? The site list ends with **Search again**, and the
chargers step has a **go back and choose a different site** checkbox, since
Home Assistant's setup dialogs have no back button of their own.

You can repeat Add charger for chargers at other sites. To stop monitoring
one charger, open its site's menu, choose **Change chargers** and untick it.
To stop monitoring a whole site, choose **Delete** from the same menu. Either
way Home Assistant removes the devices and entities and the integration
reloads.

The account device sits in a separate group labelled "Devices that don't
belong to a sub-entry". That label is Home Assistant's own: the account
belongs to the whole integration rather than to any one site.

## Entities

The charger's model and identity key are on its device page (model and serial number). Names below are the entity's own name; the full entity name Home Assistant
shows is "*device name* *entity name*", e.g. "Explorer 1 status" or "Mer
account wallet balance".

### Charger (station) device

One device per charger you added, linked to the account device.

| Entity | Platform | Meaning |
| --- | --- | --- |
| Status | sensor | The charger's own status (`available`, `charging`, `faulted`, …) |
| Available sockets | sensor | How many of this charger's sockets are free; `total_sockets` attribute |
| Session energy | sensor | Energy delivered by *your* active session, only while it is running on this charger; unavailable otherwise |
| Session charging rate | sensor | The portal's estimated charging rate for your session here, in kW at the charger; refreshed with each meter reading, every few minutes |
| Session cost | sensor | Cost of that active session so far |
| Session started | sensor | When that active session started |
| Session duration | sensor | How long that active session has been running |
| My session here | binary sensor | On while your active session is running on this charger; the `socket` attribute says which socket |
| Stop charge | button | Stops your active session, but only if it is running on this charger; greyed out otherwise |
| Notify me when available | switch | See [The notify-me switch](#the-notify-me-switch) below |

Each socket on the charger adds its own entities to the same charger device,
named after the socket (a charger can have more than one — some Mer chargers
have a "Left" and a "Right" socket):

| Entity | Platform | Meaning |
| --- | --- | --- |
| *Socket* status | sensor | The socket's own status, whoever is using it (`available`, `charging`, `faulted`, ...). Attributes: `charger`, `socket`, `start_button` (this socket's start button), `my_session` (true when it is your session), `max_power_kw`, `connector` |
| *Socket* price | sensor | Your tariff's price per kWh on this socket; the billing plan, fixed price, per-minute rate and transaction fee are attributes |
| *Socket* start charge | button | Starts a charge on this socket; greyed out unless the socket is free, or plugged in and waiting. See [Start and stop feedback](#start-and-stop-feedback) |

### Account device

One device per Mer account. It aggregates the chargers you added, and covers
your active session (wherever it is running) and your charging history.

| Entity | Platform | Meaning |
| --- | --- | --- |
| Available sockets | sensor | How many sockets on your added chargers are free. Attributes: `available_sockets` (each free socket's `charger`, `socket`, `station_id`, `socket_id` and `start_button`, in charger order), `total_sockets` and `chargers` |
| Sockets in use | sensor | Count of sockets on your added chargers in any "in use" state |
| Charging | binary sensor | On while you have an active session, on any charger |
| Live status | binary sensor (diagnostic) | On while the portal's push channel is connected, so charger and socket status and session estimates arrive the moment they change; `connected_since` attribute |
| Last poll | sensor (diagnostic) | When the portal was last polled successfully; attributes give the current poll interval, whether the last poll succeeded, and its error if not. Stays visible while polls fail |
| Portal requests remaining | sensor (diagnostic) | The portal's rate-limit headroom after the last request; see [Polling and rate limits](#polling-and-rate-limits) |
| Refresh now | button (diagnostic) | Polls the portal immediately instead of waiting for the next scheduled poll |
| Active session | sensor | Where your active session is running, as *charger socket*, with `charger`, `socket`, `station_id` and `socket_id` attributes. This and the other active session sensors are unavailable while you are not charging |
| Active session started | sensor | When the active session started |
| Active session energy | sensor | Energy delivered so far in the active session |
| Active session charging rate | sensor | The portal's estimated charging rate, kW at the charger (the car reports what reaches the battery, typically about 10% less); refreshed with each meter reading, every few minutes |
| Active session cost | sensor | Cost so far in the active session |
| Active session price | sensor | Your tariff's price per kWh on the socket in use; attributes give the portal's tariff summary (e.g. "flat £0.00"), billing plan, fixed price, per-minute rate and transaction fee |
| Active session duration | sensor | How long the active session has been running |
| Stop charge | button | Stops the active session, wherever it is running; greyed out while you are not charging |
| Last session energy | sensor | Energy delivered in your last completed session |
| Last session cost | sensor | Cost of your last completed session |
| Last session started | sensor | When your last completed session started |
| Last command | sensor (diagnostic) | Outcome of your last start or stop: Charging, Ready (plug in), Stopped, Rejected or Not confirmed, with the charger, socket, times and any error as attributes |
| Wallet balance | sensor | Your Mer account's wallet balance |

## Options

**Settings → Devices & services → Mer EV Charging → Configure** has one
setting, the **poll interval**: how often, in seconds, Home Assistant polls
the portal. Configurable between 30 and 600 seconds; the default is 60.
Changing it reloads the integration so the new value takes effect
immediately.

Chargers are not managed here but on the integration's page, with **Add
charger** and each site's **Change chargers** and **Delete**, as described under
[Setup](#setup). Adding or deleting a charger also reloads the integration.

If your Mer password changes and the integration can no longer log in, Home
Assistant raises a repair/reauthentication prompt on the integration; follow
it and enter the new password to resume without redoing the whole setup.

## Start and stop feedback

Pressing a start or stop button holds the press open until the charger shows
the outcome, so the button in the UI spins while it waits and then shows a
tick, or a red cross with the reason. While it waits, the integration polls
the charger every 5 seconds and updates its entities live, so the socket
status, the availability sensors and the session sensors change as it
happens. It gives up after 60 seconds with "not confirmed", which means the
portal accepted the command but the charger has not shown the result yet;
check the charger, and the entities catch up on the next refresh. Polling
also stops early if the portal's rate limit is nearly used up.

Outcomes for a start are **Charging**, or **Ready, plug in** when you pressed
start on a free socket and the charger is now waiting for the cable. A stop
ends with **Stopped**. Every outcome, including **Rejected**, is recorded on
the account device's **Last command** sensor and fired as a `mer_command_result`
event with the same details (`command`, `result`, `station_name`,
`socket_name`, `message`), so a phone notification is one automation away:

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

## Blueprints

Two automation blueprints ship with the repository. Import each with the
button, or paste its URL into **Settings → Automations → Blueprints → Import
blueprint**, then create an automation from it and fill in the fields.

**Offer a free charger when you arrive.** Pick who, which zone (a Work zone
around the car park, say), the sockets to offer **in order of preference**
(their *socket* status sensors),
and your phone. On arrival while not charging, it names the first free socket
in your order and the notification carries a **Start** action; while you stay
there without charging it does the same whenever a socket frees up, after it
has been free for 30 seconds so a car swapping over does not trigger it. Tap
Start and it presses that socket's start button, or tells you the socket has
been taken. Once you are charging the offer is cleared.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fdanielmsorensen%2Fha-mer%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fmer%2Foffer_free_charger.yaml)

**Report the outcome of a start or stop.** Notifies the result of every start
or stop made through the integration, from a notification tap, a dashboard or
an automation: charging, ready to plug the cable in, stopped, rejected, or
accepted but not yet confirmed.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fdanielmsorensen%2Fha-mer%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fmer%2Fcommand_outcome.yaml)

Both work from entity attributes rather than names, so they do not care what
your chargers or sockets are called. Each socket's **status** sensor
carries `charger`, `socket` and `start_button` attributes, and the account's
**Available sockets** sensor lists every free socket the same way under
`available_sockets`, which is enough for your own automations too, for
example to press the first free socket's button once you have plugged in:

```yaml
actions:
  - action: button.press
    target:
      entity_id: >-
        {{ (state_attr('sensor.mer_account_available_sockets',
        'available_sockets') | first).start_button }}
```

To trigger on "anything free", use a numeric state trigger on that sensor
with `above: 0`. To work across chargers and sockets without naming them, a
template can iterate over the integration's entities, for example the free
sockets' names from the socket sensors themselves:

```yaml
{{ integration_entities('mer')
   | select('match', 'sensor\..*_status$')
   | select('is_state', 'available')
   | map('state_attr', 'socket') | reject('none') | list }}
```

The `available_sockets` attribute above is usually simpler, since it is
already filtered and in charger order.

A start on a free socket makes the charger wait for the cable, and the portal
does not say for how long, so tap Start when you are at the charger, or plug
in first: the button stays available while the socket is plugged in and
waiting, and the outcome is then "Charging" rather than "Ready, plug in".

## Charging on a charger you have not added

The account's session sensors follow whatever your account is charging on,
anywhere on Mer's network, because the portal reports the active session per
account rather than per charger. On a public charger you have not added there
is no charger device, but **Active session**, its energy, cost, price,
charging rate, started and duration all work, and **Stop charge** on the
account device stops it. The price comes from that charger's tariff, fetched
once when the session is first seen. A session started there is picked up
from its first pushed estimate, and its end from the socket's pushed status,
the same as on your own chargers.

## One session at a time

The portal reports one active session per account, the one started most
recently. If you run two charges on the same account at once, on two sockets
or two chargers, the integration shows that most recent one: the account's
session sensors, **My session here** and the socket's `my_session` attribute
all follow it. The other socket still shows **Charging** in its status, since
that comes from the charger, but it is not marked as yours. Every per-charger
and per-socket lookup goes through `MerData.sessions`, so supporting several
sessions later means filling that from wherever the portal exposes them,
without touching the entities. Pushed estimates
for the second session are ignored rather than triggering polls, so running
two at once does not use up the portal's rate limit.

## Live status and polling

Besides polling, the integration keeps a websocket open to the portal, the
same channel the web app uses. Pushes for chargers you have not added are
ignored. While the channel is connected the **Live status** sensor on the
account device is on and polling drops to every 5 minutes; if it drops, the
sensor goes off, one poll runs straight away to catch up, polling returns to
the configured interval, and the integration reconnects with increasing
delays up to 5 minutes. A channel that stays open but goes quiet for
3 minutes is treated as dead too, since the portal normally pushes something
every second or so; it is closed and reopened after a fresh login, with a
warning in the log. Pushes cost nothing against the rate limit described
below.

### What comes from where

| Data | Pushed the moment it changes | Polled |
|---|---|---|
| Charger and socket status, so availability too | Yes | Every poll as well |
| Your running session's energy and cost | Yes, about every 45 seconds | Every poll as well |
| Whether a session is running, and where | A socket leaving the charging states ends it at once; a new one is detected from its first estimate, which triggers a poll | Every poll |
| Session start time | No | Every poll |
| Session duration | No | Every poll; see below |
| Wallet balance, last completed session | No | Every 15 minutes |
| Socket names, tariffs, charger model | No | Once an hour per charger |
| Notify-me subscription state | No | Once an hour per charger |

A poll also runs on demand: straight after a start or stop command resolves,
when the push channel drops, when a pushed estimate arrives for a session the
integration does not know about yet, when you press **Refresh now** on the
account device, and when you press **Reload** on the integration.

### Session duration

Nothing pushes the duration, so the duration sensors update on each poll:
every 5 minutes while live status is connected, otherwise at the configured
interval, plus the on-demand polls listed above. That is deliberate. Ticking
it locally would write a state change to the recorder every few seconds for
a value the portal already provides, and it is how other integrations treat
a running total. For a live readout, use the **Active session started**
timestamp sensor instead: dashboards render a timestamp as "2 hours
5 minutes ago" and keep it ticking in the browser, with no state changes at
all.

## Polling and rate limits

The portal returns an `X-Rate-Limit-Remaining` header on every response, and
this integration tracks it. At the default 60-second interval it makes
roughly two portal calls a minute while idle, and about four a minute while a
session is active (the extra calls fetch the live session's duration and
energy estimate). Every 15 minutes it also refreshes your wallet balance and
last session, and once an hour it refreshes each charger's socket names,
prices and notify-me subscription — one charger per poll cycle rather than
all of them in the same one, so that the size of a cycle's burst doesn't grow
with the number of chargers you selected.

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
- `scripts/dev-hass` starts a throwaway Home Assistant instance at
  <http://localhost:8123> that loads this checkout's integration directly, for
  manual testing without touching a real installation. Its config lives in the
  WSL home directory (`~/ha-mer-dev`) and can be deleted at any time;
  `scripts/dev-hass --reset` wipes it and starts fresh. The first launch
  installs the frontend, which takes a minute or two.

  **A C compiler and the Python headers are required in WSL.** Home
  Assistant always loads a handful of default integrations, and two of those
  pull in voice-assistant packages (`pymicro-vad`, `pyspeex-noise`) that have
  no prebuilt wheel for this Python and must be compiled. Without them every
  boot logs `Setup failed for 'assist_pipeline'` and `Setup failed for
  'assist_satellite'`,
  and, worse, the frontend's request for the list of actions fails with
  `ModuleNotFoundError: No module named 'pymicro_vad'`, so parts of the UI
  never finish loading. `scripts/bootstrap-dev` warns when they are missing.
  Install them once (it needs your WSL password, so run it yourself):

  ```bash
  wsl -d Ubuntu -- sudo apt update
  wsl -d Ubuntu -- sudo apt install -y build-essential python3-dev
  ```

  Then restart the dev instance; Home Assistant builds the two packages on
  that boot, which takes a minute or two. Re-running `scripts/bootstrap-dev`
  after installing them builds the packages ahead of time instead.

  **One instance at a time.** Home Assistant holds a file lock on
  `.ha_run.lock` while it runs. Starting a second copy against the same
  config directory fails with "Another Home Assistant instance is already
  running": stop the first one (Ctrl+C in its terminal) and try again. A
  leftover lock file from an interrupted run is harmless, since the lock
  itself is released when the process dies.

**From VS Code**, the repository ships tasks and a debug configuration in
`.vscode/`, so none of this needs a terminal:

- **F5** on "Dev Home Assistant (start, open, debug)" is the one-button flow,
  like an IDE run configuration: it starts the dev instance, waits until Home
  Assistant's debugger port is listening (a second task prints dots while it
  waits, usually for a few seconds), attaches the Python debugger, and opens
  the UI in your browser. Because this is an attach session the toolbar
  shows **Disconnect** (Shift+F5) instead of Stop; use it as the stop
  button, since the post-debug task shuts the instance down whenever the
  session ends. Holding **Alt** turns it into Stop (Alt+Shift+F5), which
  also terminates Home Assistant through the debugger. Only one instance
  can run at a time; if one is already up, F5 attaches to it instead of
  starting another.
- **Ctrl+Shift+B** runs the default build task, "HA: start dev instance",
  without the debugger, and opens the UI when it is ready. "HA: stop dev
  instance" and the other tasks (reset, test, test current file, lint,
  format) are under *Terminal → Run Task*.
- "Attach only (dev instance must already be running)" attaches to an
  instance started any other way and leaves it running when you disconnect.
  With nothing running it fails at once with "connect ECONNREFUSED
  127.0.0.1:5678"; that message means the Run and Debug dropdown is on this
  entry rather than the one-button flow above.
- The dev configuration enables Home Assistant's built-in `debugpy`
  integration on port 5678, and `launch.json` maps this checkout's files to
  the paths the WSL process sees, so breakpoints in `custom_components/mer/`
  bind. If your WSL username or config directory differ from the defaults,
  adjust the first `remoteRoot` in `launch.json`.
- **Releasing.** `scripts/release <version> "<notes>"` sets the version in the
  manifest (what Home Assistant shows), commits, tags `v<version>` (what HACS
  shows), pushes and publishes the GitHub release. CI fails a tag whose
  manifest version differs. HACS on a user's instance notices a new release on
  its own schedule, roughly every few hours for custom repositories; "Update
  information" on the repository's HACS page forces it.
- **Brand images.** Home Assistant serves a custom integration's icon and
  logo from `custom_components/mer/brand/` (`icon.png` at 256 px, `icon@2x.png`
  at 512 px, `logo.png`, `logo@2x.png`, transparent PNGs), so no upload to the
  brands repository is needed. `scripts/generate_brand_icon.py` rebuilds them
  from the official logo on Mer UK's website: the icon is the three-bar
  gradient mark, the logo adds the wordmark with the white background made
  transparent. Hard-refresh the browser after changing them, as it caches
  icons.
- The recommended extensions (Python, Python Debugger, Ruff, YAML) are listed
  in `.vscode/extensions.json`; VS Code offers to install them when you open
  the folder.

All four scripts shell out to `wsl.exe` under the hood, so they work from a
normal Windows shell without you needing to open a WSL terminal yourself.
That WSL hop is the only reason the wrappers exist, so they are a Windows
convenience rather than a requirement: on Linux or macOS, create a virtualenv
and `pip install -r requirements_test.txt` into it. Then run `pytest` and
`ruff check .` / `ruff format .` directly — the wrappers do nothing else.
