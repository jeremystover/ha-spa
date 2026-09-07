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

    Shows the spa's own answer, read back off its page after every write, not
    the value Home Assistant last sent. Setting it raises if the spa does not
    confirm, so a scheduled change that goes nowhere fails loudly instead of
    looking like success.
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

    async def async_added_to_hass(self) -> None:
        """Subscribe to updates."""
        self.async_on_remove(self._connection.add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> float | None:
        """Return the setpoint the spa reports, or nothing if it never has."""
        return self._connection.reported_setpoint

    async def async_set_native_value(self, value: float) -> None:
        """Set the setpoint and confirm the spa took it.

        Raises when it cannot be confirmed. The caller finding out is the point:
        believing our own writes is what let the water cool for two days.
        """
        await self._connection.async_apply_setpoint(int(value))
