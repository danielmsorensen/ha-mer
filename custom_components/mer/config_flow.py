"""Config, options and charging-site subentry flows for the Mer integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import create_client
from .const import (
    CONF_BASE_URL,
    CONF_SCAN_INTERVAL,
    CONF_SEARCH,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    CONF_STATION_IDS,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    SUBENTRY_TYPE_SITE,
)
from .driivz.client import DriivzDriverClient
from .driivz.exceptions import AuthError, DriivzError
from .driivz.models import Bounds, Site, Station

_LOGGER = logging.getLogger(__name__)

MAX_SITE_MATCHES = 25
# Pseudo-option in the site list that returns to the search form.
SEARCH_AGAIN = "__search_again__"
# Checkbox on the chargers step that returns to the site list.
CONF_BACK = "back"

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)
REAUTH_SCHEMA = vol.Schema(
    {vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
)


def site_subentries(entry: ConfigEntry) -> list[ConfigSubentry]:
    """The entry's charging-site subentries, in the order they were added."""
    return [s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_TYPE_SITE]


def configured_station_ids(entry: ConfigEntry) -> list[int]:
    """Every monitored station id across all sites, in the order they were added."""
    return [int(i) for s in site_subentries(entry) for i in s.data[CONF_STATION_IDS]]


def site_unique_id(site_id: int) -> str:
    return f"site_{site_id}"


async def _login(
    hass: HomeAssistant, username: str, password: str, base_url: str
) -> DriivzDriverClient:
    """Create a client and log in; raises AuthError / DriivzError."""
    client = create_client(hass, username, password, base_url)
    await client.login()
    return client


async def _find_site_stations(client: DriivzDriverClient, site: Site) -> list[Station]:
    """Stations at a site: search around its coordinates, keep those whose detail matches."""
    if site.latitude is None or site.longitude is None:
        return []
    candidates = await client.find_stations_in_bounds(Bounds.around(site.latitude, site.longitude))
    stations: list[Station] = []
    for candidate in candidates:
        detail = await client.find_station_by_id(candidate.id)
        if detail.site_id == site.id:
            stations.append(detail)
    stations.sort(key=lambda s: s.display_name)
    return stations


class MerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Sign in once; chargers are added afterwards as subentries."""

    VERSION = 3

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MerOptionsFlow:
        return MerOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_TYPE_SITE: MerSiteSubentryFlow}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
            self._abort_if_unique_id_configured()
            try:
                await _login(
                    self.hass,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    DEFAULT_BASE_URL,
                )
            except AuthError:
                errors["base"] = "invalid_auth"
            except DriivzError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during Mer login")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_BASE_URL: DEFAULT_BASE_URL,
                    },
                    options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
                )
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await _login(
                    self.hass,
                    entry.data[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
                )
            except AuthError:
                errors["base"] = "invalid_auth"
            except DriivzError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={CONF_USERNAME: entry.data[CONF_USERNAME]},
        )


class MerOptionsFlow(OptionsFlow):
    """Change the poll interval."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    **self.config_entry.options,
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                }
            )
        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


class MerSiteSubentryFlow(ConfigSubentryFlow):
    """Add chargers ("Add charger") and change a site's chargers ("Change chargers").

    One subentry per charging site, holding the ids of the chargers monitored there,
    so the integration page shows each site as a group with its chargers' devices
    inside. Adding chargers at a site that already has a subentry adds them to it.
    Every change notifies the entry's update listener, which reloads the entry; the
    reload removes the devices of chargers that are no longer monitored.
    """

    _client: DriivzDriverClient
    _search: str = ""
    _sites: dict[int, Site]
    _site: Site | None = None
    _candidates: list[Station]

    async def _ensure_client(self) -> SubentryFlowResult | None:
        """Log in with the account's credentials; returns an abort result on failure."""
        if hasattr(self, "_client"):
            return None
        data = self._get_entry().data
        try:
            self._client = await _login(
                self.hass,
                data[CONF_USERNAME],
                data[CONF_PASSWORD],
                data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
            )
        except AuthError:
            return self.async_abort(reason="invalid_auth")
        except DriivzError:
            return self.async_abort(reason="cannot_connect")
        return None

    def _existing_site_subentry(self, site_id: int) -> ConfigSubentry | None:
        return next(
            (
                s
                for s in site_subentries(self._get_entry())
                if s.unique_id == site_unique_id(site_id)
            ),
            None,
        )

    # ----- Add charger ---------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if (abort := await self._ensure_client()) is not None:
            return abort
        errors: dict[str, str] = {}
        if user_input is not None:
            self._search = user_input[CONF_SEARCH].strip()
            query = self._search.lower()
            try:
                sites = await self._client.find_sites_in_bounds(Bounds.UK)
            except DriivzError:
                errors["base"] = "cannot_connect"
            else:
                matches = [s for s in sites if query in s.name.lower()][:MAX_SITE_MATCHES]
                if not matches:
                    errors["base"] = "no_sites"
                else:
                    self._sites = {s.id: s for s in matches}
                    return await self.async_step_site_select()
        schema = vol.Schema({vol.Required(CONF_SEARCH, default=self._search): TextSelector()})
        # last_step=False makes the frontend label the button "Next" instead of "Submit".
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors, last_step=False
        )

    async def async_step_site_select(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            if user_input[CONF_SITE_ID] == SEARCH_AGAIN:
                return await self.async_step_user()
            self._site = self._sites[int(user_input[CONF_SITE_ID])]
            self._candidates = []
            return await self.async_step_stations()
        options = [
            SelectOptionDict(
                value=str(site.id),
                label=f"{site.name} ({site.socket_count} sockets, {site.status.lower()})",
            )
            for site in self._sites.values()
        ]
        # Flows have no back button, so going back is an option in the list itself.
        options.append(SelectOptionDict(value=SEARCH_AGAIN, label="Search again"))
        schema = vol.Schema(
            {
                vol.Required(CONF_SITE_ID): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                )
            }
        )
        return self.async_show_form(
            step_id="site_select",
            data_schema=schema,
            description_placeholders={"search": self._search},
            last_step=False,
        )

    async def async_step_stations(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        assert self._site is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_BACK):
                return await self.async_step_site_select()
            ids = [int(value) for value in user_input[CONF_STATION_IDS]]
            if ids:
                return self._add_stations(ids)
            errors["base"] = "no_stations_selected"
        if not self._candidates:
            try:
                found = await _find_site_stations(self._client, self._site)
            except DriivzError:
                return self.async_abort(reason="cannot_connect")
            already = set(configured_station_ids(self._get_entry()))
            self._candidates = [s for s in found if s.id not in already]
            if not self._candidates:
                return self.async_abort(
                    reason="all_stations_configured" if found else "no_stations"
                )
        options = [
            SelectOptionDict(value=str(s.id), label=s.display_name) for s in self._candidates
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_STATION_IDS, default=[]): SelectSelector(
                    SelectSelectorConfig(
                        options=options, multiple=True, mode=SelectSelectorMode.LIST
                    )
                ),
                vol.Optional(CONF_BACK, default=False): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="stations",
            data_schema=schema,
            errors=errors,
            description_placeholders={"site": self._site.name},
            last_step=True,
        )

    def _add_stations(self, station_ids: list[int]) -> SubentryFlowResult:
        assert self._site is not None
        entry = self._get_entry()
        existing = self._existing_site_subentry(self._site.id)
        if existing is not None:
            current = [int(i) for i in existing.data[CONF_STATION_IDS]]
            merged = current + [sid for sid in station_ids if sid not in current]
            self.hass.config_entries.async_update_subentry(
                entry, existing, data={**existing.data, CONF_STATION_IDS: merged}
            )
            return self.async_abort(
                reason="stations_added", description_placeholders={"site": self._site.name}
            )
        return self.async_create_entry(
            title=self._site.name,
            data={
                CONF_SITE_ID: self._site.id,
                CONF_SITE_NAME: self._site.name,
                CONF_STATION_IDS: station_ids,
            },
            unique_id=site_unique_id(self._site.id),
        )

    # ----- Change chargers -------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Tick or untick the monitored chargers at this site."""
        subentry = self._get_reconfigure_subentry()
        if (abort := await self._ensure_client()) is not None:
            return abort
        current = [int(i) for i in subentry.data[CONF_STATION_IDS]]
        errors: dict[str, str] = {}
        if user_input is not None:
            ids = [int(value) for value in user_input[CONF_STATION_IDS]]
            if ids:
                # Plain update, not update-and-reload: the entry's update listener
                # already reloads it, and HA refuses to combine the two.
                return self.async_update_and_abort(
                    self._get_entry(), subentry, data={**subentry.data, CONF_STATION_IDS: ids}
                )
            errors["base"] = "no_stations_selected"
        if not getattr(self, "_candidates", None):
            site_id = int(subentry.data[CONF_SITE_ID])
            try:
                site = next(
                    (
                        s
                        for s in await self._client.find_sites_in_bounds(Bounds.UK)
                        if s.id == site_id
                    ),
                    None,
                )
                found = await _find_site_stations(self._client, site) if site else []
            except DriivzError:
                return self.async_abort(reason="cannot_connect")
            # Keep monitored chargers listed even if the portal stopped returning them,
            # so they can still be unticked.
            known = {s.id for s in found}
            self._candidates = found + [
                Station(id=sid, caption=f"Charger {sid}") for sid in current if sid not in known
            ]
        options = [
            SelectOptionDict(value=str(s.id), label=s.display_name) for s in self._candidates
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_STATION_IDS, default=[str(i) for i in current]): SelectSelector(
                    SelectSelectorConfig(
                        options=options, multiple=True, mode=SelectSelectorMode.LIST
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
            description_placeholders={"site": subentry.title},
        )
