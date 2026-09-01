"""The Spa WebSocket integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import ATTR_CODE, CONF_URL, DOMAIN, SERVICE_SEND_RAW
from .coordinator import SpaConnection

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR]

SEND_RAW_SCHEMA = vol.Schema({vol.Required(ATTR_CODE): cv.string})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Spa WebSocket from a config entry."""
    connection = SpaConnection(hass, entry.data[CONF_URL])
    await connection.start()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = connection
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def handle_send_raw(call: ServiceCall) -> None:
        """Send one raw command code to every configured spa."""
        code = call.data[ATTR_CODE]
        for conn in hass.data[DOMAIN].values():
            await conn.send(code)

    hass.services.async_register(
        DOMAIN, SERVICE_SEND_RAW, handle_send_raw, schema=SEND_RAW_SCHEMA
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        connection: SpaConnection = hass.data[DOMAIN].pop(entry.entry_id)
        await connection.stop()
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_SEND_RAW)
    return unload_ok
