"""The Spa WebSocket integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_CODE,
    ATTR_TIMEZONE,
    CONF_URL,
    DOMAIN,
    SERVICE_SEND_RAW,
    SERVICE_SET_TIME,
)
from .coordinator import SpaConnection

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
]

SEND_RAW_SCHEMA = vol.Schema({vol.Required(ATTR_CODE): cv.string})
SET_TIME_SCHEMA = vol.Schema({vol.Optional(ATTR_TIMEZONE): cv.string})


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

    async def handle_set_time(call: ServiceCall) -> None:
        """Set every configured spa's clock to the current wall time."""
        tz_name = call.data.get(ATTR_TIMEZONE)
        if tz_name is None:
            tz = dt_util.DEFAULT_TIME_ZONE
        elif (tz := await dt_util.async_get_time_zone(tz_name)) is None:
            raise ServiceValidationError(f"Unknown timezone: {tz_name}")

        now = dt_util.now().astimezone(tz)
        for conn in hass.data[DOMAIN].values():
            await conn.async_set_time(now)

    hass.services.async_register(
        DOMAIN, SERVICE_SEND_RAW, handle_send_raw, schema=SEND_RAW_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_TIME, handle_set_time, schema=SET_TIME_SCHEMA
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
            hass.services.async_remove(DOMAIN, SERVICE_SET_TIME)
    return unload_ok
