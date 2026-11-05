# Mer

Home Assistant integration for Mer UK EV chargers, via the reverse-engineered Driivz driver portal.

## Language

### Places

**Site**:
A charging location with one or more chargers.
_Avoid_: location, park

**Charger**:
One charging post; the portal calls it a station.
_Avoid_: station (in user-facing text), post

**Socket**:
One connector on a charger, each with its own status.
_Avoid_: plug, connector, outlet

### Data sources

**Poll**:
A request-driven read of chargers, your session, wallet and history.
_Avoid_: refresh (except "Refresh now"), fetch

**Push**:
A message from the portal's websocket: a socket's status change or your session's estimate.
_Avoid_: event, notification

### Your session

**Session**:
Your charge running on a socket. The portal reports at most one ("last active").
_Avoid_: transaction (that's the history record), charge

**Session end**:
The moment the integration saw your session stop, by poll or push.
_Avoid_: stop (that's the command)

**Pushed end**:
A session end seen by push. Trusted over polls still reporting the session for a short while.

**Left socket**:
The socket your last session ran on, after it ended.
_Avoid_: last socket, previous socket

**Freed**:
The left socket has been available since your session ended.

**Reused**:
Someone else started using the left socket after it freed.
_Avoid_: taken (blueprint wording for any socket)

**Held back**:
A free socket not to be offered since your session ended: free since before it ended, or the left socket until reused. Never while a session of yours runs.
_Avoid_: excluded, skipped

**Own start**:
A start command of yours on a socket, running, or recent and not rejected; its charging is expected to be your session.

**Session watch**:
Repeatedly asking the portal for your session after an own start begins charging, until it appears.

**Session tracker**:
The integration's one view of your session: running, just ended, and what to look for next.
