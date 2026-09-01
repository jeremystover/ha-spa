"""Binary sensor reporting whether the spa's heater is firing."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SpaConnection


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the spa's heater sensor from a config entry."""
    connection: SpaConnection = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SpaHeatingBinarySensor(connection, entry)])


class SpaHeatingBinarySensor(BinarySensorEntity):
    """Whether the spa's heater element is currently firing.

    Worth having beyond simple curiosity: the spa only heats while its filter
    pump is running, and the filter cycle is programmed on the topside panel
    against the panel's own clock. That clock is not readable over this
    connection and drifts whenever the spa loses power. This entity is how Home
    Assistant can tell — by consequence rather than by reading a clock — that a
    filter cycle really did overlap the setpoint it asked for.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, connection: SpaConnection, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        self._connection = connection
        self._attr_name = "Heating"
        self._attr_unique_id = f"{entry.entry_id}_heating"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Spa",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to connection updates."""
        self.async_on_remove(self._connection.add_listener(self.async_write_ha_state))

    @property
    def is_on(self) -> bool:
        """Return True while the heater is firing."""
        return self._connection.heating
