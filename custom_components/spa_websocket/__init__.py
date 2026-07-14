"""The Spa WebSocket integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_URL, DOMAIN
from .coordinator import SpaConnection

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Spa WebSocket from a config entry."""
    connection = SpaConnection(hass, entry.data[CONF_URL])
    await connection.start()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = connection
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        connection: SpaConnection = hass.data[DOMAIN].pop(entry.entry_id)
        await connection.stop()
    return unload_ok
