"""Config, options and charger subentry flows for the Mer EV Charging integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from types import MappingProxyType
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
    CONF_STATION_ID,
    CONF_STATION_IDS,
    CONF_STATION_NAME,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    SUBENTRY_TYPE_CHARGER,
)
from .driivz.client import DriivzDriverClient
from .driivz.exceptions import AuthError, DriivzError
from .driivz.models import Bounds, Site, Station

_LOGGER = logging.getLogger(__name__)

MAX_SITE_MATCHES = 25

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


def configured_station_ids(entry: ConfigEntry) -> list[int]:
    """Station ids of the charger subentries, in the order they were added."""
    return [
        int(subentry.data[CONF_STATION_ID])
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_CHARGER
    ]


def charger_subentry_title(station: Station, site: Site) -> str:
    return f"{station.display_name} ({site.name})"


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

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MerOptionsFlow:
        return MerOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_TYPE_CHARGER: MerChargerSubentryFlow}

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


class MerChargerSubentryFlow(ConfigSubentryFlow):
    """Add chargers to the account: find a site by name, then pick chargers at it.

    Every ticked charger becomes its own subentry, so each one has a card on the
    integration page and can be removed on its own. A flow can only return one
    subentry, so all but the last are added directly through the config entries
    manager first; each addition notifies the entry's update listener, which
    reloads the integration, so the new devices appear without further action.
    """

    _client: DriivzDriverClient
    _sites: dict[int, Site]
    _site: Site | None = None
    _candidates: list[Station]

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if not hasattr(self, "_client"):
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
        if user_input is not None:
            query = user_input[CONF_SEARCH].strip().lower()
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
        schema = vol.Schema({vol.Required(CONF_SEARCH, default=""): TextSelector()})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_site_select(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
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
        schema = vol.Schema(
            {
                vol.Required(CONF_SITE_ID): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                )
            }
        )
        return self.async_show_form(step_id="site_select", data_schema=schema)

    async def async_step_stations(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        assert self._site is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            ids = [int(value) for value in user_input[CONF_STATION_IDS]]
            if ids:
                return self._create_subentries(ids)
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
                )
            }
        )
        return self.async_show_form(
            step_id="stations",
            data_schema=schema,
            errors=errors,
            description_placeholders={"site": self._site.name},
        )

    def _create_subentries(self, station_ids: list[int]) -> SubentryFlowResult:
        assert self._site is not None
        entry = self._get_entry()
        by_id = {s.id: s for s in self._candidates}
        chosen = [by_id[sid] for sid in station_ids if sid in by_id]
        *extra, last = chosen
        for station in extra:
            self.hass.config_entries.async_add_subentry(
                entry,
                ConfigSubentry(
                    data=MappingProxyType(self._subentry_data(station)),
                    subentry_type=SUBENTRY_TYPE_CHARGER,
                    title=charger_subentry_title(station, self._site),
                    unique_id=f"station_{station.id}",
                ),
            )
        return self.async_create_entry(
            title=charger_subentry_title(last, self._site),
            data=self._subentry_data(last),
            unique_id=f"station_{last.id}",
        )

    def _subentry_data(self, station: Station) -> dict[str, Any]:
        assert self._site is not None
        return {
            CONF_STATION_ID: station.id,
            CONF_STATION_NAME: station.display_name,
            CONF_SITE_ID: self._site.id,
            CONF_SITE_NAME: self._site.name,
        }
