"""Config flow for the Spa WebSocket integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import CONF_FALLBACK_URL, CONF_URL, DOMAIN


def _check(
    user_input: dict[str, Any], taken: set[str] | None = None
) -> tuple[str, str | None, dict[str, str]]:
    """Validate the two URLs, returning them and any field errors.

    The spa has two modules on the panel bus and either can serve as the one the
    integration drives. Which transport a URL gets is read off its own shape
    rather than asked for, so there is nothing here to pick -- only the second
    URL is optional, and it is used solely as somewhere to retry a failed write.

    ``taken`` is the set of primary URLs other config entries already hold. The
    entry's own unique_id *is* its URL, so moving one entry onto another's URL
    leaves two entries claiming the same id -- which Home Assistant notices
    after the fact and reports as a bug in this integration:

        Unique id of config entry 'HotTupb' changed to 'wss://...' which is
        already in use, please create a bug report

    Found by doing exactly that on 7 November 2026 while testing the BT-200
    against real hardware. Caught here it is a correctable field error instead.
    """
    errors: dict[str, str] = {}
    url = user_input[CONF_URL].strip()
    if not url.startswith(("ws://", "wss://")):
        errors[CONF_URL] = "invalid_url"
    elif url in (taken or set()):
        errors[CONF_URL] = "url_in_use"

    fallback = (user_input.get(CONF_FALLBACK_URL) or "").strip() or None
    if fallback is not None and not fallback.startswith(("ws://", "wss://")):
        errors[CONF_FALLBACK_URL] = "invalid_url"
    elif fallback is not None and fallback == url:
        errors[CONF_FALLBACK_URL] = "same_url"

    return url, fallback, errors


class SpaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Spa WebSocket."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url, fallback, errors = _check(user_input)
            if not errors:
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                data: dict[str, Any] = {CONF_URL: url}
                if fallback is not None:
                    data[CONF_FALLBACK_URL] = fallback
                return self.async_create_entry(
                    title=user_input.get("name", "Spa").strip() or "Spa",
                    data=data,
                )

        schema = vol.Schema(
            {
                vol.Required("name", default="Spa"): str,
                vol.Required(CONF_URL): str,
                vol.Optional(CONF_FALLBACK_URL, default=""): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Point an existing entry at a fresh URL.

        Needed whenever the token embedded in the spa's URL changes -- re-pairing
        the module or regenerating the web link will do it.

        Note that a spa which has stopped responding does *not* usually need
        this. The WF-100 dropping off the cloud looks identical from here and is
        far more common; it keeps the same token and is fixed at the module, not
        in Home Assistant. See docs/PROTOCOL.md before reaching for a new URL.

        It is also how you move between the spa's two modules: paste the
        BT-200's ``/wsa/`` URL here and the integration drives that one instead,
        with the WF-100's URL kept below as somewhere to retry a failed write.

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
            # Every other entry's URL, so this one cannot be moved on top of a
            # URL another entry already claims.
            taken = {
                other.data[CONF_URL]
                for other in self._async_current_entries()
                if other.entry_id != entry.entry_id
            }
            url, fallback, errors = _check(user_input, taken)
            if not errors:
                # The entry's own unique_id is the URL, so a rotated token has
                # to move it too, or the entry keeps claiming the dead one.
                await self.async_set_unique_id(url)
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=url,
                    data_updates={CONF_URL: url, CONF_FALLBACK_URL: fallback},
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_URL, default=entry.data[CONF_URL]): str,
                vol.Optional(
                    CONF_FALLBACK_URL,
                    default=entry.data.get(CONF_FALLBACK_URL) or "",
                ): str,
            }
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )
