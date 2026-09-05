"""Shared WebSocket connection to the spa.

One connection is opened per config entry and shared by every entity, mirroring
the original Homebridge plugin's protocol: single-character commands are sent to
toggle jets/filter, and incoming ``{"dsp": "<hex>"}`` messages report the jets
state.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

import aiohttp

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
    async_get_clientsession,
)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    DSP_FLAG_TO_STATE,
    FLAG_EDIT,
    FLAG_HEATING,
    HEARTBEAT,
    KEY_RELAY_STATUS,
    PATH_APP,
    PATH_SETTEMP,
    RECONNECT_DELAY,
    STALE_AFTER_SECONDS,
    STALENESS_TICK_SECONDS,
    STATE_NAMES,
    STATE_OFF,
)
from .decode import decode_temperature

_LOGGER = logging.getLogger(__name__)


class SpaConnection:
    """Maintains a single reconnecting WebSocket connection to the spa."""

    def __init__(self, hass: HomeAssistant, url: str) -> None:
        """Initialize the connection."""
        self.hass = hass
        self.url = url
        self.jets_state: int = STATE_OFF
        # Most recent raw frame, surfaced as a diagnostic attribute so the
        # protocol can be inspected without turning on debug logging.
        self.last_frame: str | None = None
        # Last successfully decoded display reading. The panel cycles through
        # states the decoder does not read as a temperature, so the last good
        # value is kept rather than flapping to unknown between frames.
        self.temperature: int | None = None
        self.temperature_unit: str | None = None
        # Whether the heater is currently running. This spa heats on demand
        # whenever the water is below setpoint, independent of the filter
        # cycles, so it is the fastest available signal that a setpoint we sent
        # actually took effect.
        self.heating: bool = False
        # When the last display frame arrived, and whether the relay says it has
        # a live link to the spa. Both feed `available` -- see that property for
        # why a dead link is otherwise completely silent.
        self.last_frame_at: datetime | None = None
        self.relay_linked: bool = True
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._task: asyncio.Task | None = None
        self._unsub_tick: Callable[[], None] | None = None
        self._closing = False
        self._listeners: list[Callable[[], None]] = []
        # Its own cookie jar: the settemp POST is authenticated by a session
        # cookie that must not leak into Home Assistant's shared session.
        self._http = async_create_clientsession(hass)

    @property
    def available(self) -> bool:
        """Whether the spa is actually reporting right now.

        Worth the machinery because the failure this catches is invisible
        otherwise. When the spa's WiFi module drops off the cloud relay, the
        relay stays up: the WebSocket connects, HTTP returns 200, and no
        exception is raised anywhere. It just has nothing from the spa. Readings
        freeze at their last value and setpoints are accepted and discarded.

        Observed in the field: the setpoint was written on schedule for two days
        while the water cooled from 99F to 83F, with every automation reporting
        success and not one error in the log.
        """
        if not self.relay_linked:
            return False
        if self.last_frame_at is None:
            return False
        age = (dt_util.utcnow() - self.last_frame_at).total_seconds()
        return age < STALE_AFTER_SECONDS

    @property
    def _base_url(self) -> str:
        """Return the HTTP base for this spa, derived from the socket URL.

        ``wss://host/spa/<token>/wsb`` -> ``https://host/spa/<token>``
        """
        base = self.url.replace("wss://", "https://").replace("ws://", "http://")
        return base.rsplit("/", 1)[0]

    async def async_set_temperature(self, temperature: int) -> None:
        """Set the spa's target temperature.

        Temperature is not a WebSocket command. It is a form POST, authenticated
        by a session cookie that the app page issues and that expires after about
        an hour, so the cookie is minted fresh rather than stored.
        """
        if not self.available:
            raise HomeAssistantError(
                f"Spa is not reporting, so setpoint {temperature} was not sent. "
                "The relay accepts and acknowledges commands even when the spa "
                "is offline, so sending anyway would look like success and "
                "change nothing."
            )

        base = self._base_url
        payload = {
            "flip-scale": "0",
            "void": str(temperature),
            "temp": str(temperature),
        }

        try:
            await self._http.get(f"{base}/{PATH_APP}")
            async with self._http.post(
                f"{base}/{PATH_SETTEMP}", data=payload
            ) as resp:
                if resp.status != 200:
                    raise HomeAssistantError(
                        f"Spa rejected setpoint {temperature}: HTTP {resp.status}"
                    )
        except aiohttp.ClientError as err:
            raise HomeAssistantError(f"Could not reach the spa: {err}") from err

        _LOGGER.info("Spa target temperature set to %s", temperature)

    async def async_set_time(self, when: datetime) -> None:
        """Set the spa's clock to ``when``.

        The panel keeps a 12-hour clock and takes it as four digits plus an A or
        P suffix -- 3:21pm is ``0321P`` -- wrapped in a JSON frame on the same
        socket the buttons use. Captured from the iOS app, which reaches a
        sibling endpoint on the same relay.

        This matters because the filter cycles are programmed against that clock
        and it does not survive a power cut, so after an outage the spa filters
        at the wrong times. Heating is not affected -- this spa heats on demand
        rather than only during filter cycles -- but filtration still drifts.

        The clock cannot be read back -- the display multiplexes to the water
        temperature at idle -- so it is re-asserted on a schedule rather than
        checked and corrected. Writing the correct time to an already-correct
        clock changes nothing, which is what makes that safe.
        """
        meridiem = "A" if when.hour < 12 else "P"
        await self.send(json.dumps({"time": f"{when:%I%M}{meridiem}"}))

    @callback
    def add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        """Register an entity to be notified when the jets state changes."""
        self._listeners.append(update_callback)

        def _remove() -> None:
            self._listeners.remove(update_callback)

        return _remove

    @callback
    def _notify_listeners(self) -> None:
        for update_callback in self._listeners:
            update_callback()

    async def start(self) -> None:
        """Start the background connection loop."""
        self._closing = False
        self._task = self.hass.async_create_background_task(
            self._run(), name=f"spa_websocket {self.url}"
        )
        # Availability is a function of elapsed time, so once frames stop there
        # is no incoming event left to recompute it. Without this tick the
        # entities would sit on stale values forever, which is the exact bug.
        self._unsub_tick = async_track_time_interval(
            self.hass,
            self._async_check_staleness,
            timedelta(seconds=STALENESS_TICK_SECONDS),
        )

    async def stop(self) -> None:
        """Stop the connection loop and close the socket."""
        self._closing = True
        if self._unsub_tick is not None:
            self._unsub_tick()
            self._unsub_tick = None
        if self._ws is not None:
            await self._ws.close()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def send(self, code: str) -> None:
        """Send a command code to the spa."""
        if self._ws is None or self._ws.closed:
            _LOGGER.warning("WebSocket not open, cannot send %r", code)
            return
        _LOGGER.debug("Sending %r to spa", code)
        await self._ws.send_str(code)

    async def _run(self) -> None:
        """Connect, read messages, and reconnect forever until stopped."""
        session = async_get_clientsession(self.hass)
        while not self._closing:
            try:
                async with session.ws_connect(self.url, heartbeat=HEARTBEAT) as ws:
                    self._ws = ws
                    _LOGGER.info("Spa WebSocket connected to %s", self.url)
                    async for msg in ws:
                        if msg.type in (
                            aiohttp.WSMsgType.TEXT,
                            aiohttp.WSMsgType.BINARY,
                        ):
                            self._handle_message(msg.data)
                        elif msg.type in (
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            break
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, OSError) as err:
                _LOGGER.warning("Spa WebSocket error: %s", err)
            finally:
                self._ws = None

            if self._closing:
                break
            _LOGGER.info("Spa WebSocket closed, reconnecting in %ss", RECONNECT_DELAY)
            await asyncio.sleep(RECONNECT_DELAY)

    @callback
    def _async_check_staleness(self, _now: datetime) -> None:
        """Re-publish entity state so availability reflects elapsed time."""
        self._notify_listeners()

    @callback
    def _handle_message(self, raw: str | bytes) -> None:
        """Parse an incoming message and update the jets state."""
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="ignore")

        _LOGGER.debug("Spa frame received: %s", raw[:200])

        # Record every frame, including shapes this integration does not parse
        # yet — the spa's protocol is undocumented and the unparsed frames are
        # where the temperature readout is expected to live.
        changed = raw != self.last_frame
        self.last_frame = raw

        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            parsed = None

        dsp = parsed.get("dsp") if isinstance(parsed, dict) else None

        # The relay volunteers its own link state. A zero here is the relay
        # saying it has nothing from the spa, which is exactly when it would
        # otherwise go on silently accepting commands.
        if isinstance(parsed, dict) and KEY_RELAY_STATUS in parsed:
            linked = bool(parsed[KEY_RELAY_STATUS])
            if linked != self.relay_linked:
                _LOGGER.warning(
                    "Spa relay reports the spa is %s",
                    "reachable" if linked else "OFFLINE — commands will be lost",
                )
                self.relay_linked = linked
                changed = True

        # Ignore all-zero / too-short frames, matching the original plugin.
        if isinstance(dsp, str) and len(dsp) >= 12 and dsp.strip("0") != "":
            # Only a real display frame counts as the spa reporting. The relay
            # keeps talking to us after the spa is gone, so its own chatter must
            # not be mistaken for liveness.
            self.last_frame_at = dt_util.utcnow()
            flags = int(dsp[8:10], 16)

            new_state = STATE_OFF
            for flag, state in DSP_FLAG_TO_STATE:
                if flags & flag:
                    new_state = state
                    break
            if new_state != self.jets_state:
                _LOGGER.info("Spa jets changed to: %s", STATE_NAMES[new_state])
                self.jets_state = new_state
                changed = True

            heating = bool(flags & FLAG_HEATING)
            if heating != self.heating:
                _LOGGER.info("Spa heater %s", "on" if heating else "off")
                self.heating = heating
                changed = True

            # While the edit LED is lit the panel is showing the set temperature
            # rather than the water temperature, so that reading is not what the
            # temperature sensor reports.
            editing = bool(flags & FLAG_EDIT)
            reading = decode_temperature(dsp)
            if reading is not None and not editing and reading != (
                self.temperature,
                self.temperature_unit,
            ):
                self.temperature, self.temperature_unit = reading
                _LOGGER.debug("Spa temperature: %s°%s", *reading)
                changed = True

        if changed:
            self._notify_listeners()
