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

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Point an existing entry at a fresh URL.

        The token embedded in the spa's URL is what authorises this integration,
        and it goes stale -- re-pairing the module or regenerating the web link
        rotates it. When it does, the relay keeps serving the page and answering
        with 200 while silently routing nothing, so the spa appears connected and
        simply stops obeying.

        Recovering has to be an edit rather than a delete-and-re-add, because
        every entity's unique_id is derived from the config entry's id. A new
        entry mints a new id, and with it a whole new set of entities under new
        entity_ids -- leaving every automation that referenced the old ones
        pointing at nothing, with no error to show for it. Updating in place
        keeps the id, so the entities and everything built on them survive.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_URL].strip()
            if not url.startswith(("ws://", "wss://")):
                errors[CONF_URL] = "invalid_url"
            else:
                # The entry's own unique_id is the URL, so a rotated token has
                # to move it too, or the entry keeps claiming the dead one.
                await self.async_set_unique_id(url)
                return self.async_update_reload_and_abort(
                    entry, unique_id=url, data_updates={CONF_URL: url}
                )

        schema = vol.Schema(
            {vol.Required(CONF_URL, default=entry.data[CONF_URL]): str}
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )
