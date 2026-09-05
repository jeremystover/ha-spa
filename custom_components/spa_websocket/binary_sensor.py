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
    """Whether the spa's heater is currently running.

    Worth having because nothing else reports whether a setpoint actually did
    anything: the setpoint POST returns 200 whether or not the water moves, and
    the water temperature takes hours to show it. This says so within a minute.

    On this spa the heater runs on DEMAND, whenever the water is below setpoint
    -- a frame captured outside both programmed filter cycles read flags 0x05,
    heating plus low pump, with the filtering bit clear. So heater runtime
    tracks the setpoint, not the filter schedule, and a raised setpoint that
    produces no runtime at all means the spa is not acting on it.

    One caveat, unresolved: the flag has been seen set with the water above
    setpoint. It may cover a heat cycle including pump overrun rather than the
    element alone. Treat it as "heating activity", not "element energized".
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
    def available(self) -> bool:
        """Return False while the spa is not reporting."""
        return self._connection.available

    @property
    def is_on(self) -> bool:
        """Return True while the heater is firing."""
        return self._connection.heating
