"""Sensor entity reporting the spa jets state."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
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
    async_add_entities(
        [SpaJetsSensor(connection, entry), SpaTemperatureSensor(connection, entry)]
    )


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
    def available(self) -> bool:
        """Return False while the spa is not reporting."""
        return self._connection.available

    @property
    def native_value(self) -> str:
        """Return the current jets state name."""
        return STATE_NAMES.get(self._connection.jets_state, STATE_NAMES[STATE_OFF])

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Expose the last raw frame for protocol inspection."""
        return {"last_frame": self._connection.last_frame}


class SpaTemperatureSensor(SensorEntity):
    """Water temperature, read from the topside panel's display buffer."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, connection: SpaConnection, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        self._connection = connection
        self._attr_name = "Temperature"
        self._attr_unique_id = f"{entry.entry_id}_temperature"
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
        """Return False while the spa is not reporting.

        Without this the sensor holds its last decoded reading indefinitely, so
        a dead link reads as a plausible water temperature rather than as no
        data. That reading also gates the heat-window verification, which then
        silently passes on stale numbers.
        """
        return self._connection.available

    @property
    def native_value(self) -> int | None:
        """Return the last decoded temperature."""
        return self._connection.temperature

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit the panel is currently displaying."""
        if self._connection.temperature_unit == "C":
            return UnitOfTemperature.CELSIUS
        if self._connection.temperature_unit == "F":
            return UnitOfTemperature.FAHRENHEIT
        return None
