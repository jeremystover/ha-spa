"""Buttons that open a socket, do one thing, and hang up."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, KEY_FILTER, KEY_JETS
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
            SpaCommandButton(
                connection, entry, "Jets", KEY_JETS, "mdi:hot-tub"
            ),
            SpaCommandButton(
                connection, entry, "Filter", KEY_FILTER, "mdi:air-filter"
            ),
            SpaRefreshButton(connection, entry),
        ]
    )


class SpaButtonBase(ButtonEntity):
    """Shared wiring."""

    _attr_has_entity_name = True

    def __init__(self, connection: SpaConnection, entry: ConfigEntry, key: str) -> None:
        self._connection = connection
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Spa",
        )


class SpaCommandButton(SpaButtonBase):
    """A momentary button that presses one key on the spa's panel.

    Carries the key it means -- ``jets``, ``filter`` -- rather than a wire code,
    because the WF-100 and the BT-200 share neither. The ``unique_id`` is
    derived from the same string, which is also what it always was, so the
    entity_ids and the automations built on them are unaffected.
    """

    def __init__(
        self,
        connection: SpaConnection,
        entry: ConfigEntry,
        name: str,
        key: str,
        icon: str,
    ) -> None:
        """Initialize the button."""
        super().__init__(connection, entry, key)
        self._key = key
        self._attr_name = name
        self._attr_icon = icon

    async def async_press(self) -> None:
        """Open a socket, press the key, and hang up."""
        await self._connection.async_press(self._key)


class SpaRefreshButton(SpaButtonBase):
    """Take a reading now.

    Between the three scheduled visits nothing is connected, so the temperature
    on the dashboard is as old as its timestamp says. This is how you get a
    current one without waiting for the next job -- press it before walking out
    to the tub.

    It can come back with nothing. Frames are bursty, and a quiet spa may not
    speak inside the listening window. The reading's `measured_at` is what tells
    you whether it worked.
    """

    _attr_name = "Refresh"
    _attr_icon = "mdi:refresh"

    def __init__(self, connection: SpaConnection, entry: ConfigEntry) -> None:
        """Initialize the button."""
        super().__init__(connection, entry, "refresh")

    async def async_press(self) -> None:
        """Connect briefly and take whatever reading arrives."""
        await self._connection.async_refresh()
