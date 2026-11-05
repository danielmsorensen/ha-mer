# Mer

Home Assistant integration for a Mer (Driivz) driver account: the chargers you monitor, your charging sessions, and starting and stopping them.

## Language

**Charger**:
A charging station on the portal, with one or more sockets. "Station" in the portal's API.
_Avoid_: Station (outside API code), charge point

**Socket**:
One connector on a charger; the thing you plug into and start a charge on.
_Avoid_: Connector, outlet

**Session**:
One charge of yours, from start to stop, on one socket.
_Avoid_: Transaction (that is the portal's billing record of a session), charge

**Snapshot**:
Everything the entities show, as one immutable whole at a moment in time.
_Avoid_: State, data (too vague)

**Publish**:
Handing a new snapshot to the entities.
_Avoid_: Update, push (push is the portal's live channel)

**Socket view**:
A socket as automations see it: charger and socket names, their ids, and its start button.

**Session view**:
Where a session runs: charger and socket names and their ids.
_Avoid_: Session place

**Start button**:
The one button per socket that starts a charge there, picking the start method itself.
