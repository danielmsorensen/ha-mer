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
**Mer EV Charging**. Setup asks only for the e-mail and password you use for
the Mer Connect / driver portal app. That creates the account, with its own
device and entities, but no chargers yet.

Chargers are added afterwards, from the integration's page (**Settings →
Devices & services → Mer EV Charging**), where each charger you add gets its
own card and there is an **Add charger** button:

1. **Search for the site** by typing part of its name — the search matches
   anywhere in the name, so "NETPark" is enough to find "Durham County
   Council - Business Durham NETPark" without typing it in full. Pick the
   site from the matches.
2. **Tick the chargers** you want to monitor at that site. Chargers you have
   already added are not offered again. Every ticked charger becomes its own
   card on the integration page and its own device, with its socket entities
   attached to it.

You can repeat Add charger for chargers at other sites. To stop monitoring a
charger, open the menu on its card and choose **Delete**; Home Assistant
removes its device and entities and the integration reloads.

## Entities

Names below are the entity's own name; the full entity name Home Assistant
shows is "*device name* *entity name*", e.g. "Explorer 1 status" or "Mer
account wallet balance".

### Charger (station) device

One device per charger you added, linked to the account device.

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

One device per Mer account. It aggregates the chargers you added, and covers
your active session (wherever it is running) and your charging history.

| Entity | Platform | Meaning |
|---|---|---|
| Any socket available | binary sensor | On if any socket on any charger you added is `AVAILABLE` |
| Available sockets | sensor | Count of sockets on your added chargers currently `AVAILABLE` |
| Sockets in use | sensor | Count of sockets on your added chargers in any "in use" state |
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

**Settings → Devices & services → Mer EV Charging → Configure** has one
setting, the **poll interval**: how often, in seconds, Home Assistant polls
the portal. Configurable between 30 and 600 seconds; the default is 60.
Changing it reloads the integration so the new value takes effect
immediately.

Chargers are not managed here but on the integration's page, with **Add
charger** and each charger card's **Delete**, as described under
[Setup](#setup). Adding or deleting a charger also reloads the integration.

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
    entity_id: binary_sensor.mer_account_any_socket_available
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

The entity above is the account device's **Any socket available**, which
covers every charger you have added. Its id follows from the device name
"Mer account", so it is the same for everyone unless you rename the device;
if in doubt, open **Developer Tools → States** and copy the real id.

There is no charger-level availability entity — availability is a property
of a socket, and a charger with two sockets (e.g. Left and Right) has two
independent availability states. To watch one specific socket instead of
all your chargers, trigger on that socket's own availability sensor, e.g.
`binary_sensor.explorer_1_left_available` for the "Left" socket on a charger
device named "Explorer 1" — again, check Developer Tools for the id your
own charger and socket names actually produce.

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
  http://localhost:8123 that loads this checkout's integration directly, for
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
- The recommended extensions (Python, Python Debugger, Ruff, YAML) are listed
  in `.vscode/extensions.json`; VS Code offers to install them when you open
  the folder.

All four scripts shell out to `wsl.exe` under the hood, so they work from a
normal Windows shell without you needing to open a WSL terminal yourself.
That WSL hop is the only reason the wrappers exist, so they are a Windows
convenience rather than a requirement: on Linux or macOS, create a virtualenv
and `pip install -r requirements_test.txt` into it. Then run `pytest` and
`ruff check .` / `ruff format .` directly — the wrappers do nothing else.
