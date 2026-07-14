"""Button entities for triggering spa jets and filter."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CMD_FILTER, CMD_JETS, DOMAIN
from .coordinator import SpaConnection


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the spa buttons from a config entry."""
    connection: SpaConnection = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            SpaButton(connection, entry, "Jets", "jets", CMD_JETS, "mdi:hot-tub"),
            SpaButton(connection, entry, "Filter", "filter", CMD_FILTER, "mdi:air-filter"),
        ]
    )


class SpaButton(ButtonEntity):
    """A momentary button that sends a single command code to the spa."""

    _attr_has_entity_name = True

    def __init__(
        self,
        connection: SpaConnection,
        entry: ConfigEntry,
        name: str,
        key: str,
        code: str,
        icon: str,
    ) -> None:
        """Initialize the button."""
        self._connection = connection
        self._code = code
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Spa",
        )

    async def async_press(self) -> None:
        """Send the command code to the spa."""
        await self._connection.send(self._code)
