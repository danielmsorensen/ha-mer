"""Config flow for the Mer EV Charging integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
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
    CONF_STATION_IDS,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
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


class _SiteStationsMixin:
    """Shared site-search and charger-selection steps for config and options flows."""

    hass: HomeAssistant
    _client: DriivzDriverClient
    _sites: dict[int, Site]
    _site: Site | None
    _candidates: list[Station]

    def _init_site_state(self) -> None:
        self._sites = {}
        self._site = None
        self._candidates = []

    def _default_search(self) -> str:
        return ""

    def _preselected_station_ids(self) -> list[int]:
        return []

    async def _finish(self, site: Site, station_ids: list[int]) -> ConfigFlowResult:
        raise NotImplementedError

    async def async_step_site(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
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
        schema = vol.Schema(
            {vol.Required(CONF_SEARCH, default=self._default_search()): TextSelector()}
        )
        return self.async_show_form(step_id="site", data_schema=schema, errors=errors)  # type: ignore[attr-defined]

    async def async_step_site_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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
        return self.async_show_form(step_id="site_select", data_schema=schema)  # type: ignore[attr-defined]

    async def async_step_stations(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._site is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            ids = [int(value) for value in user_input[CONF_STATION_IDS]]
            if ids:
                return await self._finish(self._site, ids)
            errors["base"] = "no_stations_selected"
        if not self._candidates:
            try:
                self._candidates = await _find_site_stations(self._client, self._site)
            except DriivzError:
                return self.async_abort(reason="cannot_connect")  # type: ignore[attr-defined]
            if not self._candidates:
                return self.async_abort(reason="no_stations")  # type: ignore[attr-defined]
        candidate_ids = {s.id for s in self._candidates}
        preselected = [
            str(sid) for sid in self._preselected_station_ids() if sid in candidate_ids
        ] or [str(s.id) for s in self._candidates]
        options = [
            SelectOptionDict(value=str(s.id), label=s.display_name) for s in self._candidates
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_STATION_IDS, default=preselected): SelectSelector(
                    SelectSelectorConfig(
                        options=options, multiple=True, mode=SelectSelectorMode.LIST
                    )
                )
            }
        )
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="stations",
            data_schema=schema,
            errors=errors,
            description_placeholders={"site": self._site.name},
        )


class MerConfigFlow(_SiteStationsMixin, ConfigFlow, domain=DOMAIN):
    """Handle the initial setup and reauthentication."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._init_site_state()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MerOptionsFlow:
        return MerOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
            self._abort_if_unique_id_configured()
            try:
                self._client = await _login(
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
                self._data = {
                    CONF_USERNAME: user_input[CONF_USERNAME],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_BASE_URL: DEFAULT_BASE_URL,
                }
                return await self.async_step_site()
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def _finish(self, site: Site, station_ids: list[int]) -> ConfigFlowResult:
        return self.async_create_entry(
            title=f"Mer - {site.name}",
            data=self._data,
            options={
                CONF_SITE_ID: site.id,
                CONF_SITE_NAME: site.name,
                CONF_STATION_IDS: station_ids,
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
            },
        )

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


class MerOptionsFlow(_SiteStationsMixin, OptionsFlow):
    """Change the poll interval or the monitored chargers."""

    def __init__(self) -> None:
        self._init_site_state()

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["interval", "site"])

    async def async_step_interval(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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
        return self.async_show_form(step_id="interval", data_schema=schema)

    def _default_search(self) -> str:
        return self.config_entry.options.get(CONF_SITE_NAME, "")

    def _preselected_station_ids(self) -> list[int]:
        return [int(i) for i in self.config_entry.options.get(CONF_STATION_IDS, [])]

    async def async_step_site(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if not hasattr(self, "_client"):
            data = self.config_entry.data
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
        return await super().async_step_site(user_input)

    async def _finish(self, site: Site, station_ids: list[int]) -> ConfigFlowResult:
        return self.async_create_entry(
            data={
                **self.config_entry.options,
                CONF_SITE_ID: site.id,
                CONF_SITE_NAME: site.name,
                CONF_STATION_IDS: station_ids,
            }
        )
