"""Config flow for the Spa WebSocket integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import CONF_URL, DOMAIN


class SpaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Spa WebSocket."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip()
            if not url.startswith(("ws://", "wss://")):
                errors[CONF_URL] = "invalid_url"
            else:
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input.get("name", "Spa").strip() or "Spa",
                    data={CONF_URL: url},
                )

        schema = vol.Schema(
            {
                vol.Required("name", default="Spa"): str,
                vol.Required(CONF_URL): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )
