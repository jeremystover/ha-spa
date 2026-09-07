"""Binary readings, and the one alarm that matters."""

from __future__ import annotations

from typing import Any

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
    """Set up the spa binary sensors from a config entry."""
    connection: SpaConnection = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            SpaHeating(connection, entry),
            SpaFiltering(connection, entry),
            SpaActionFailed(connection, entry),
        ]
    )


class SpaBinaryEntity(BinarySensorEntity):
    """Shared wiring. Always available — see sensor.py for why."""

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


class SpaHeating(SpaBinaryEntity):
    """Whether the heater was running at the last reading."""

    _attr_name = "Heating"
    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, connection: SpaConnection, entry: ConfigEntry) -> None:
        super().__init__(connection, entry, "heating")

    @property
    def is_on(self) -> bool:
        """Return whether the heat LED was lit."""
        return self._connection.heating


class SpaFiltering(SpaBinaryEntity):
    """Whether a filter cycle was running at the last reading.

    Here to answer a question it cannot answer yet. The spa's clock cannot be
    read back, so there is no direct way to know it has drifted after a power
    cut — but the filter cycles are programmed against that clock. FP1 runs noon
    to 3pm spa-local, so a filtering bit that comes on at noon is a clock that is
    right, and one that comes on at some other hour is a clock that is not.

    Exposed and recorded; nothing acts on it. Watch it for a few days first and
    build the check on evidence rather than on this paragraph.
    """

    _attr_name = "Filtering"
    _attr_icon = "mdi:air-filter"

    def __init__(self, connection: SpaConnection, entry: ConfigEntry) -> None:
        super().__init__(connection, entry, "filtering")

    @property
    def is_on(self) -> bool:
        """Return whether the filter LED was lit."""
        return self._connection.filtering


class SpaActionFailed(SpaBinaryEntity):
    """On when a scheduled job did not confirm. This is the alarm.

    The old alert asked "is the spa online right now", which turned out to be
    both noisy and beside the point. This asks the question worth asking: of the
    three things that have to happen each day -- the clock at 04:00 spa-local
    and the two setpoint changes -- did any of them fail to be confirmed?
    """

    _attr_name = "Action failed"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, connection: SpaConnection, entry: ConfigEntry) -> None:
        super().__init__(connection, entry, "action_failed")

    @property
    def is_on(self) -> bool:
        """Return whether any job's most recent run failed to confirm."""
        return bool(self._connection.failing)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Name the failing jobs, and record how every job last went."""
        return {
            "failing": self._connection.failing,
            "jobs": {
                name: {
                    "ok": job.ok,
                    "at": job.at.isoformat(),
                    "detail": job.detail,
                }
                for name, job in sorted(self._connection.jobs.items())
            },
        }
