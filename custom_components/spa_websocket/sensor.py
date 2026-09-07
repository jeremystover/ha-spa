"""Readings the spa gave us, and when it gave them."""

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

from .const import DOMAIN, STATE_NAMES
from .coordinator import SpaConnection


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the spa sensors from a config entry."""
    connection: SpaConnection = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            SpaTemperature(connection, entry),
            SpaJetsStatus(connection, entry),
            SpaConfirmedSetpoint(connection, entry),
        ]
    )


class SpaEntity(SensorEntity):
    """Shared wiring.

    Deliberately always available. There is no standing connection to be
    disconnected from, and blanking a reading out between visits would discard
    the only reading there is. Every reading carries the time it was taken
    instead, which says what staleness actually needs to say.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, connection: SpaConnection, entry: ConfigEntry, key: str) -> None:
        self._connection = connection
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Spa",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to updates."""
        self.async_on_remove(self._connection.add_listener(self.async_write_ha_state))


class SpaTemperature(SpaEntity):
    """Water temperature, as of whenever the spa last said."""

    _attr_name = "Temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, connection: SpaConnection, entry: ConfigEntry) -> None:
        super().__init__(connection, entry, "temperature")

    @property
    def native_value(self) -> int | None:
        """Return the last water temperature the spa reported."""
        return self._connection.temperature

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit the panel was displaying in."""
        if self._connection.temperature_unit == "C":
            return UnitOfTemperature.CELSIUS
        if self._connection.temperature_unit == "F":
            return UnitOfTemperature.FAHRENHEIT
        return None

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Say when this was measured — the reading is meaningless without it."""
        measured = self._connection.measured_at
        return {"measured_at": measured.isoformat() if measured else None}


class SpaJetsStatus(SpaEntity):
    """What the pump was doing at the last reading."""

    _attr_name = "Jets status"
    _attr_icon = "mdi:hot-tub"

    def __init__(self, connection: SpaConnection, entry: ConfigEntry) -> None:
        super().__init__(connection, entry, "jets_status")

    @property
    def native_value(self) -> str:
        """Return the jets state."""
        return STATE_NAMES[self._connection.jets_state]


class SpaConfirmedSetpoint(SpaEntity):
    """The setpoint the spa itself reports — not the one we sent it.

    The difference is the entire lesson of September 2026: for two days every
    write was accepted and discarded while the water cooled from 99F to 83F,
    and nothing anywhere disagreed, because nothing was reading the spa's own
    answer back.
    """

    _attr_name = "Confirmed setpoint"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_icon = "mdi:thermometer-check"

    def __init__(self, connection: SpaConnection, entry: ConfigEntry) -> None:
        super().__init__(connection, entry, "confirmed_setpoint")

    @property
    def native_value(self) -> int | None:
        """Return the spa's own setpoint."""
        return self._connection.reported_setpoint

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Say when the spa last agreed with what we asked for."""
        at = self._connection.setpoint_confirmed_at
        return {"confirmed_at": at.isoformat() if at else None}
