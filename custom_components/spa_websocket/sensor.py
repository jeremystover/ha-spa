"""Sensor entity reporting the spa jets state."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_info import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, STATE_NAMES, STATE_OFF
from .coordinator import SpaConnection


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the spa status sensor from a config entry."""
    connection: SpaConnection = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SpaJetsSensor(connection, entry)])


class SpaJetsSensor(SensorEntity):
    """Read-only sensor reporting Off / Low / High / Filtering."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:hot-tub"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(STATE_NAMES.values())

    def __init__(self, connection: SpaConnection, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        self._connection = connection
        self._attr_name = "Jets status"
        self._attr_unique_id = f"{entry.entry_id}_jets_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Spa",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to connection updates."""
        self.async_on_remove(self._connection.add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> str:
        """Return the current jets state name."""
        return STATE_NAMES.get(self._connection.jets_state, STATE_NAMES[STATE_OFF])
