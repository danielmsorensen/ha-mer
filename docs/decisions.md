# Decisions

Why `ha-mer` is built the way it is. This is not a changelog or a review
transcript — it is the reasoning behind choices that a future maintainer,
including the author months from now, could not reconstruct from the code
alone. Each item also records what it costs if it turns out to have been the
wrong call, so re-opening a decision starts from a known price rather than a
guess.

## Development environment

Tests run inside WSL2, not native Windows Python. Home Assistant
`2026.9.2` imports the POSIX-only `fcntl` module from `homeassistant.runner`,
and the test plugin loads that unconditionally, so the whole HA-dependent
test suite (everything exercising config flow, coordinator, entities,
diagnostics) simply cannot import on Windows. The WSL2 Ubuntu image on this
machine had no `pip`, no `ensurecpip`, and no passwordless `sudo`, so `pip`
was bootstrapped into a dedicated venv (`~/.venvs/ha-mer`, outside the repo)
from a wheel fetched by Windows `pip`, rather than asking for a sudo password
or adding a fake Windows `fcntl` shim that would hide real platform breakage.
Local runs go through the committed wrapper scripts — `scripts/test`,
`scripts/lint`, `scripts/format`, `scripts/bootstrap-dev` — which is why
those exist instead of bare `pytest`/`ruff` invocations in the docs. CI on
`ubuntu-latest` is unaffected and remains the authority.

Cost if wrong: local runs depend on WSL staying installed, and the venv
lives outside the repo, so a new machine must run `scripts/bootstrap-dev`
first. If a maintainer would rather install `python3.14-venv` via `sudo`,
`scripts/bootstrap-dev` becomes a two-line `apt` call and nothing else about
the setup needs to change.

## Testing choices

**Mocking library: `AiohttpClientMocker`, not `aioresponses`.** The spec and
original plan called for `aioresponses`, but `aioresponses==0.7.9` — the
newest release — is broken against the `aiohttp` version Home Assistant
`2026.9.2` pins. Rather than pin test dependencies to an unmerged fork commit
on someone's personal branch (which can be rebased or deleted, taking CI down
with it), the suite mocks with
`pytest_homeassistant_custom_component.test_util.aiohttp.AiohttpClientMocker`
throughout. It was verified to cover every assertion the tests make: form and
JSON bodies, query params, response headers/status/exceptions, and
sequential different responses to the same URL via `side_effect`.

Cost if wrong: the HA-free client tests now import a Home Assistant test
utility, so they cannot run without `pytest-homeassistant-custom-component`
installed — but the full suite already requires that package, and the
production client itself still imports nothing from `homeassistant`.
Sequential-response tests are somewhat more verbose via `side_effect` than
they would have been with `aioresponses`. One consequence: a test that used
to assert "no form body was sent alongside the JSON body" can no longer make
that assertion, because the mocker records `data or json` into a single
slot — confirmed to be a lost assertion, not a live failure mode in
`client.py`.

**Rate-limit skip semantics are intentional and pinned by a test.** When a
poll cycle is skipped to conserve the observed portal rate-limit budget, the
coordinator returns cached data and `last_update_success` goes back to
`True` before the portal has actually been re-contacted. This looks like a
bug (data appears fresh when it merely wasn't re-fetched) but was decided to
be the better trade: blanking every entity to "unavailable" for a full cycle
just because the integration chose to be polite is worse for the user than
serving one-cycle-stale data. The semantics are documented in the coordinator
and locked in with an assertion so the decision is tested rather than
accidental.

Cost if wrong: after a rate-limit skip, entities show one cycle of
stale-but-recent data instead of going unavailable. If that proves
misleading in practice, the fix is a two-line change — re-raise
`UpdateFailed` on the skipped cycle instead of returning cached data.

**Coordinator refresh atomicity.** `_refresh_details` and `_refresh_account`
build their result locally and assign it to `self._details`/`self._wallet`
once, at the end, rather than writing fields as each call succeeds. This
closes an edge case rather than documenting it: a failure partway through no
longer leaves cached state ahead of the last successfully published poll.
(Note: a later task on the per-charger notify switch reintroduced a
comparable partial-write hazard on a different field — see the notify-switch
race below — so this invariant is not universal across the file.)

## What the portal does and does not expose

The two load-bearing reverse-engineering findings — that
`findCurrentTransactionStartTime` returns elapsed milliseconds rather than a
timestamp, and that the energy field named `totalKw` actually holds kWh, not
power — are documented with the supporting arithmetic in
[`docs/api.md`](api.md#findcurrenttransactionstarttime-returns-no-start-time)
and are not repeated here. Both were discovered from a live charging session
captured on 2026-09-17 (transaction 9088676, socket 11242 on station 6041),
which invalidated two of the three synthetic session fixtures the plan had
assumed and forced a client method signature change
(`find_current_transaction_start_time` → `find_current_transaction`, which
now returns a parsed transaction rather than a datetime, since deriving a
start time needs a wall clock the client should not own).

**Capabilities that exist in the payload but have no driver-portal
endpoint.** `getStationCapabilitiesAndValidate` and the logged-in account's
own permissions describe operations well beyond start/stop: **setting the
charging current** (`SET_CHARGE_CURRENT`), **charge-full-speed**
(`CHARGE_FULL_SPEED`), **unlocking a socket** (`UNLOCK_SOCKET`), **charging
profiles**, and **boost**. None of these has a corresponding endpoint
anywhere in the driver portal's own JavaScript bundles — they are
operator-portal operations, surfaced in the shared capability/permission
data without a driver-facing endpoint to invoke them. This integration
deliberately does not implement them. Do not add a "set current" or "boost"
service on the strength of the capability list alone: doing so means
guessing at an unpublished operator-portal endpoint, not calling something
the driver portal actually exposes. (Full detail and the search method are
in [`docs/api.md`](api.md#what-the-portal-does-not-expose).)

By contrast, **notify-me-when-available** is reachable through three
`customerFacade` endpoints and is implemented as a per-charger switch.

Cost if wrong (capabilities exclusion): if those operator operations do
exist under names the driver bundles never reference, working features have
been left on the table; confirming that would need traffic captured from an
operator account, which this project does not have.

## Integration design and entity layout

**Session visibility follows the charger, not just the account.** Beyond
the originally planned account-level "current session" sensors, a live
charge is also mirrored onto the specific charger device in use: a
per-charger "my session here" binary sensor, a per-charger stop button that
refuses to act when the session belongs to a different charger, and
per-charger sensors for session energy, cost, start time and duration. This
was a deliberate widening of scope on request, traded against creating more
entities per charger than the original design — a user can hide the extra
entities but not un-create them.

**Socket price unit is derived, not hardcoded.** The price-per-energy sensor
computes its unit from the socket's own tariff currency
(`SocketPrice.currency`) rather than a fixed `"GBP/kWh"` string, because this
Mer tenant's login payload lists two active currencies (GBP for the UK, EUR
for Ireland) — a hardcoded unit would mislabel an Irish account's prices.
Home Assistant tolerates a sensor whose unit is computed as long as it does
not change for a given account, and a currency does not.

**Diagnostics redaction is scoped, not by field name globally.** Several
identifiers are generically named `id` (stations, sockets, the wallet, and
transactions all use it), so redacting every field literally called `id`
would hide the very identifiers diagnostics exist to show. Instead, specific
nested objects get a second, targeted `async_redact_data` pass: the last
transaction's `id` (identifies one person's specific charge) and the
wallet's `id` (a per-account handle structurally equivalent to the
already-redacted customer id, not a shared-resource key like a station or
socket id). A test pins the other half of the contract — that station and
socket ids stay visible — so a later, broader redaction change can't
silently blank them. Diagnostics also serialize `timedelta` fields
explicitly, since active-session and estimate data include durations that
would otherwise fail to serialize during the one situation — a live
charge — when someone is most likely to pull diagnostics.

**Notify-switch write race, resolved with a lock.** `self._notify` (the
per-charger notify-when-available switch state) has two writers with
different consistency needs: a user's toggle, and the hourly details
refresh that can overwrite it with a stale snapshot if the two interleave.
Considered and rejected: a per-key merge (would require writing to
`self._notify` before every call in a refresh has succeeded, undoing the
atomicity invariant described above) and re-applying the toggle after a
refresh completes (needs a generation counter — more machinery than the
problem warrants). Chosen instead: a shared `asyncio.Lock` held across the
fetch-and-commit side of the refresh and the call-and-write side of the
toggle.

Cost if wrong: a toggle issued exactly during an hourly refresh is delayed by
about the length of two network calls (roughly a second), rather than being
silently lost or momentarily disagreeing with the portal for up to an hour.

**Coordinator polling burden.** The steady-state design polls one charger's
full details per cycle and rotates, to keep the per-minute call budget low.
On first setup, reload, or an options change, every selected charger is
primed in a single cycle instead — an un-detailed charger has no socket
names, model, or serial yet, so its entity unique-ids would freeze wrong
without this (verified empirically: omitting it broke entity-id assertions
in six tests). Steady-state worst case is a constant 6+2N calls regardless
of charger count; the one-time priming burst can exceed that, but only on
user-initiated, infrequent events (setup/reload/options change), not on the
recurring poll.

## Deferred and parked trade-offs

Two decisions were explicitly parked rather than fixed, because both are
refinements rather than defects:

- **Optional-endpoint failures degrade silently.** `_refresh_optional`
  swallows `ApiError` (a subclass of the client's general `DriivzError`)
  along with other portal errors, so a genuine permissions change on an
  optional endpoint (e.g. the wallet) shows up only as a debug log and a
  stale sensor, not a visible error. This is the direct consequence of an
  earlier fix that isolated optional refreshes so one broken endpoint can't
  make every availability sensor unavailable — the alternative is the
  failure that fix removed. A reasonable refinement would be to raise the
  log level after repeated consecutive failures. Cost if wrong: a real
  permissions or plan change at Mer looks like stale data rather than an
  error.

- **README doesn't mention the priming burst.** The polling section
  describes steady-state one-charger-per-cycle behaviour but not that
  setup/reload/options-change re-primes every charger in one cycle (the
  code's own docstring does say this). Left as a known gap because it is a
  one-sentence documentation clarification, not a load-bearing defect. Cost
  if wrong: mild confusion for a user watching logs immediately after a
  restart.

**Deferred minor findings.** The final whole-branch review triaged
twenty-one deferred minor findings accumulated across development and
judged none of them blocking for merge. They cluster into a few themes,
none of which affect correctness of shipped behaviour:

- **Duplicated helper code** that could be consolidated later without
  behaviour change — a shared `side_effect` test helper repeated across two
  test files, a repeated reject-bool-then-coerce coercion pattern in
  `driivz/models.py`, a repeated re-login guard condition in
  `driivz/client.py` (this one *was* promoted to a real fix during the final
  review, not left deferred).
- **Missing test coverage for already-safe failure paths** — e.g. a charger
  dropping out of the polled set mid-cycle (traced and confirmed to
  under-count rather than over-claim availability), coordinator failure
  propagating to entity unavailability (traced through
  `CoordinatorEntity.available` and confirmed working), a `DriivzError` from
  `stop_charge` itself (shares its wrapper with the already-tested start
  path).
- **Cosmetic/organizational** — `sensor.py` having grown to five sensor
  families in one file with an obvious future split point, an inconsistent
  callable name (`session_fn` vs. `value_fn`) on one sensor description, a
  device-registry lookup repeated per entity that could be hoisted into
  `async_setup_entry`.
- **Narrow forward risks** with no live failure today — a fixture
  (`station_17886.json`) that inherited AC-template fields on what is
  otherwise a DC station, unread by any current test; `_plain`'s
  diagnostics fallback passing an untested type (Enum/set/bytes) through to
  `json.dumps` if one is ever introduced; a bootstrap script glob
  (`scripts/bootstrap-dev`) that doesn't check whether it actually matched a
  downloaded wheel before using the result, so a failed download surfaces as
  a confusing error rather than a clear one.

If a maintainer wants the exact list rather than the theme summary, it no
longer exists anywhere durable — it lived only in the now-deleted scratch
planning ledger. Treat the absence of detail here as a signal that the
review judged each item genuinely minor, not as a gap to go looking for.

## 2026-09-23: chargers are config subentries; setup asks only for credentials

**Decision.** Initial setup takes the e-mail and password and nothing else. Each
monitored charger is a Home Assistant config *subentry* of type `charger` on the
account entry, added from the integration page's "Add charger" button (site search →
site → tick chargers, already-added ones hidden) and removed from its own card. The
site device and its three aggregate entities are gone; the aggregates now live on the
account device and cover every added charger, since one entry can hold chargers from
several sites. Configure keeps only the poll interval.

**Why.** Daniel asked for setup to stop requiring a charger up front and for chargers
to be searchable and addable afterwards. Subentries are HA's native shape for exactly
that: per-charger cards, an Add button, per-charger delete, and automatic removal of
the devices and entities registered under a deleted subentry. An options-flow
"manage chargers" menu would have reimplemented all of that by hand and left the
orphaned-device problem the README used to warn about.

**Consequences.** Entry `VERSION` is 2 with no migration; no version-1 entry existed
outside the dev instance. A flow can return only one subentry, so when several
chargers are ticked the extras are added through `async_add_subentry` before the
flow finishes; each addition triggers a reload, which is fine for the handful of
chargers anyone monitors. Aggregate unique ids moved from `<entry>_site_<key>` to
`<entry>_account_<key>`.

## 2026-09-23: one subentry per site, not per charger

**Decision.** A charging site is the subentry (type `site`, data `site_id`, `site_name`,
`station_ids`); its chargers' devices are registered under it, so the integration page
shows one group per site with its chargers inside. Adding chargers at a site that
already has a subentry extends it. A site's "Change chargers" (subentry reconfigure)
unticks chargers; setup then deletes devices of chargers no longer monitored. Entry
`VERSION` 3, with a migration from version 2's one-subentry-per-charger.

**Why.** Per-charger subentries put every charger in its own group titled with its own
name, a duplicate of the device inside it. Daniel asked for chargers to sit together
under their site. The account device's "Devices that don't belong to a sub-entry"
group is Home Assistant's fixed label for entry-level devices and cannot be renamed.
