"""Target temperature control for the spa."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MAX_TEMP_F, MIN_TEMP_F
from .coordinator import SpaConnection


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the spa's target temperature from a config entry."""
    connection: SpaConnection = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SpaTargetTemperature(connection, entry)])


class SpaTargetTemperature(NumberEntity):
    """The spa's target temperature.

    The spa does not report its setpoint on the socket except while the panel is
    being edited, so the value shown is the last one set through Home Assistant.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_native_min_value = MIN_TEMP_F
    _attr_native_max_value = MAX_TEMP_F
    _attr_native_step = 1
    _attr_icon = "mdi:thermometer"

    def __init__(self, connection: SpaConnection, entry: ConfigEntry) -> None:
        """Initialize the control."""
        self._connection = connection
        self._attr_name = "Target temperature"
        self._attr_unique_id = f"{entry.entry_id}_target_temperature"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Spa",
        )
        self._value: float | None = None

    @property
    def available(self) -> bool:
        """Return False while the spa is not reporting."""
        return self._connection.available

    @property
    def native_value(self) -> float | None:
        """Return the last setpoint written from Home Assistant."""
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        """Send a new setpoint to the spa."""
        await self._connection.async_set_temperature(int(value))
        self._value = value
        self.async_write_ha_state()
