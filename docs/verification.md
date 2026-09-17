# Live verification checklist

The automated test suite runs entirely against recorded fixtures — it never
talks to the real Mer portal. Before trusting this integration day to day,
walk through this checklist once against your own account and your own
chargers. It is written to be followed by you, in your own Home Assistant,
not run by an agent: several steps need a real charger in front of you.

## 1. Install and set up

1. Install the integration through HACS as a custom repository (see the
   [README](../README.md#installation)) and restart Home Assistant.
2. Add the integration, sign in with your Mer account, search for your site
   by name (e.g. "NETPark"), and select the chargers you want — for this
   project, Explorer 1 and Explorer 2.

## 2. Check monitoring against the portal

3. Open the Mer app or driver portal side by side with Home Assistant.
   Confirm each socket's status sensor and `*_available` binary sensor on
   both of your chargers match what the portal's own map shows.
4. Confirm the site's **Any socket available** binary sensor is on exactly
   when at least one of your configured sockets shows available, and off
   when none do.
5. Confirm **Wallet balance** matches the balance shown in the portal, and
   that **Last session energy** / **cost** / **started** match your most
   recent completed session in the portal's history.

## 3. Start and stop a real charge — do this in person

This step moves your car's charger. Do not run it remotely, do not run it
speculatively, and do not run it without being physically at the charger and
having decided to charge right now.

6. Get explicit go-ahead from yourself (or whoever's account this is) before
   pressing anything here — this is a real command to a real charger, not a
   dry run.
7. **While standing at a free socket on one of your chargers**, press that
   socket's **start charge** button in Home Assistant. Then check:
   - The button call succeeds (no error shown in Home Assistant).
   - The socket's status moves to `preparing` or `charging` within two poll
     cycles.
   - The charger's own **Charging** indicator (the account's `Charging`
     binary sensor) turns on.
   - **Active session energy** starts rising.
8. When you're done, press **Stop charge** (either the account-level button
   or the button on the charger device) and confirm the session ends
   cleanly: the socket status returns to `available` (or whatever the
   charger reports once the cable is removed), and `Charging` turns off.

## 4. Check the derived values while a session is running

The three live-session response shapes (active socket, transaction start
time, charging estimate) were captured from a real charge on 2026-09-17 and
are already the fixtures the test suite uses — you don't need to record
anything new here. What's worth checking is that the *values this
integration derives from those responses* behave sensibly in your running
instance:

9. **Session duration** should advance in step with the wall clock — roughly
   one second of increase per second of real time, not jumping or freezing.
10. **Session energy** should climb over the course of the session. Note
    that the portal updates its energy figure less often than it updates
    duration, so it is normal — not a bug — for energy to read the same
    value across two or three consecutive polls before ticking up.
11. **Active session charger** should name the charger you are actually
    plugged into, not the other one.

If any of the above disagrees with what you see at the charger, that's a
real finding worth filing as an issue before relying on this integration
unattended.
