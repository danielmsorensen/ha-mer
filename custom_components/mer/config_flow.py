"""Config flow scaffold for Mer (the user and options steps land in Task 6).

The manifest declares `config_flow: true`, so Home Assistant requires this
module to exist and register a handler before any config entry -- including
one created directly by tests via `MockConfigEntry` -- can be set up. This
stub only wires up the reauth entry point that `MerCoordinator` relies on;
Task 6 replaces it with the real user/options flow.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class MerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Placeholder handler; Task 6 implements the user and options steps."""

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start a reauth flow when the stored credentials stop working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauthentication (the real form is added in Task 6)."""
        return self.async_show_form(step_id="reauth_confirm")
