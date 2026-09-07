"""Short, deliberate visits to the spa — no standing connection.

The integration used to hold a WebSocket open all day, ping it every twenty
seconds, and re-assert the setpoint every hour. That bought a live temperature
reading and a fast offline signal, and it made the interesting question harder
to answer rather than easier: *did the thing I needed actually happen?*

What matters is three events a day. The clock is set once, at 04:00 spa-local
when nothing else is going on. The setpoint goes up for the afternoon window and
back down after it. Each one is verified against the spa's own account of itself,
and a failure to verify is the only thing worth an alarm.

So this connects when it has something to do, confirms it, and hangs up. Between
visits the readings keep their last value and say when they were taken, which is
honest: a reading from this morning is still the truth about this morning.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import aiohttp

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
    async_get_clientsession,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONFIRM_ATTEMPTS,
    CONFIRM_DELAY_SECONDS,
    DSP_FLAG_TO_STATE,
    FLAG_EDIT,
    FLAG_FILTERING,
    FLAG_HEATING,
    HEARTBEAT,
    JOB_CLOCK,
    JOB_SETPOINT,
    KEY_RELAY_STATUS,
    PATH_APP,
    PATH_SETTEMP,
    REFRESH_SECONDS,
    RELAY_ANSWER_SECONDS,
    STATE_NAMES,
    STATE_OFF,
    VISIT_SECONDS,
)
from .decode import decode_temperature

_LOGGER = logging.getLogger(__name__)

# The app page carries the spa's live setpoint in the form it renders:
#   <input type="number" name="void" ... value="85">
# Matching the whole tag first keeps this indifferent to attribute order.
_SETPOINT_INPUT = re.compile(r"<input[^>]*name=\"void\"[^>]*>", re.IGNORECASE)
_SETPOINT_VALUE = re.compile(r"value=\"(\d{1,3})\"", re.IGNORECASE)


def parse_setpoint(html: str) -> int | None:
    """Return the setpoint the spa reports on its app page, or None.

    This is the only way to learn what the spa actually has, as opposed to what
    was last sent to it. The distinction is not academic: a relay that has lost
    the spa keeps answering 200 and keeps rendering a page, but that page comes
    back without a temperature. So None is not "parse failed" — it is the
    signature of a spa that is not there.
    """
    if (tag := _SETPOINT_INPUT.search(html)) is None:
        return None
    if (value := _SETPOINT_VALUE.search(tag.group(0))) is None:
        return None
    return int(value.group(1))


@dataclass(frozen=True)
class JobResult:
    """How a scheduled job went, and what it saw."""

    ok: bool
    at: datetime
    detail: str


class SpaConnection:
    """Talks to one spa in short visits, and remembers what it was told."""

    def __init__(self, hass: HomeAssistant, url: str) -> None:
        """Initialize the connection."""
        self.hass = hass
        self.url = url

        # Last known readings. None of this expires. An hours-old temperature is
        # not wrong, it is old, and `measured_at` is what says so -- whereas
        # blanking it out would throw away the only reading there is.
        self.temperature: int | None = None
        self.temperature_unit: str | None = None
        self.measured_at: datetime | None = None
        self.heating: bool = False
        self.filtering: bool = False
        self.jets_state: int = STATE_OFF

        # What the spa says its setpoint is, read back off its own page. Never
        # what we sent -- believing our own writes is what let two days of
        # discarded setpoints look like success.
        self.reported_setpoint: int | None = None
        self.setpoint_confirmed_at: datetime | None = None

        # Whether the relay claimed a live link during the visit in progress.
        # None until it says, because "has not said" is not "said no".
        self.relay_linked: bool | None = None

        # How each scheduled job last went. This is the alarm surface.
        self.jobs: dict[str, JobResult] = {}

        self._listeners: list[Callable[[], None]] = []
        # Its own cookie jar: the settemp POST is authenticated by a session
        # cookie that must not leak into Home Assistant's shared session.
        self._http = async_create_clientsession(hass)

    # ---- state other objects read -------------------------------------------

    @property
    def failing(self) -> list[str]:
        """Names of the jobs whose most recent run did not confirm."""
        return sorted(name for name, job in self.jobs.items() if not job.ok)

    @callback
    def add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        """Register an entity to be notified when anything changes."""
        self._listeners.append(update_callback)

        def _remove() -> None:
            self._listeners.remove(update_callback)

        return _remove

    @callback
    def _notify_listeners(self) -> None:
        for update_callback in self._listeners:
            update_callback()

    @callback
    def _record(self, job: str, ok: bool, detail: str) -> None:
        """Record how a job went and tell the entities."""
        self.jobs[job] = JobResult(ok=ok, at=dt_util.utcnow(), detail=detail)
        if ok:
            _LOGGER.info("Spa %s job confirmed: %s", job, detail)
        else:
            _LOGGER.error("Spa %s job did NOT confirm: %s", job, detail)
        self._notify_listeners()

    @property
    def _base_url(self) -> str:
        """Return the HTTP base for this spa, derived from the socket URL.

        ``wss://host/spa/<token>/wsb`` -> ``https://host/spa/<token>``
        """
        base = self.url.replace("wss://", "https://").replace("ws://", "http://")
        return base.rsplit("/", 1)[0]

    # ---- the socket, opened only when there is something to do ---------------

    async def _listen(
        self, ws: aiohttp.ClientWebSocketResponse, seconds: float
    ) -> bool:
        """Read frames for up to ``seconds``, returning once the spa speaks.

        Best effort by design. Display frames are bursty -- gaps of ten to
        thirty minutes are normal on a perfectly healthy spa -- so a visit can
        easily end without one arriving. That is not a fault and must never be
        reported as one: the confirmations that matter go over HTTP. A frame
        caught here is a bonus reading, nothing more.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        while (remaining := deadline - loop.time()) > 0:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
            except TimeoutError:
                return False
            if msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                if self._handle_message(msg.data):
                    return True
            elif msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.ERROR,
            ):
                return False
        return False

    async def _visit(self, send: str | None = None, listen: float | None = None) -> bool:
        """Open the socket, optionally send one frame, listen briefly, hang up.

        The relay states its link on connect, promptly and reliably, so opening
        a socket is also how we ask whether the spa is there at all.
        """
        listen = VISIT_SECONDS if listen is None else listen
        self.relay_linked = None
        session = async_get_clientsession(self.hass)
        async with session.ws_connect(self.url, heartbeat=HEARTBEAT) as ws:
            # Give the relay its moment to volunteer stsR before acting on it.
            await self._listen(ws, RELAY_ANSWER_SECONDS)
            if send is not None:
                if self.relay_linked is False:
                    raise HomeAssistantError(
                        "the relay reports no link to the spa, so the command "
                        "would have been accepted and discarded"
                    )
                await ws.send_str(send)
            spoke = await self._listen(ws, listen)
        self._notify_listeners()
        return spoke

    # ---- the three jobs ------------------------------------------------------

    async def async_apply_setpoint(self, temperature: int) -> None:
        """Set the target temperature and confirm the spa took it.

        Confirmation is a *fresh* page load, not the POST's own echo. Whether
        that echo carries the new value or the pre-write one was never
        established against the hardware, so trusting it would be guessing; a
        page fetched a few seconds later is the spa's settled answer.

        Raises when it cannot be confirmed, which is the point of the exercise.
        """
        base = self._base_url
        payload = {
            "flip-scale": "0",
            "void": str(temperature),
            "temp": str(temperature),
        }

        try:
            # The app page is what issues the session cookie the POST needs.
            async with self._http.get(f"{base}/{PATH_APP}") as page:
                if page.status != 200:
                    raise HomeAssistantError(f"app page returned {page.status}")
                await page.text()
            async with self._http.post(f"{base}/{PATH_SETTEMP}", data=payload) as resp:
                if resp.status != 200:
                    raise HomeAssistantError(f"settemp returned {resp.status}")
                await resp.text()
            reported = await self._read_back(temperature)
        except (aiohttp.ClientError, TimeoutError) as err:
            detail = f"could not reach the spa to set {temperature}F: {err}"
            self._record(JOB_SETPOINT, False, detail)
            raise HomeAssistantError(detail) from err
        except HomeAssistantError as err:
            detail = f"setting {temperature}F failed: {err}"
            self._record(JOB_SETPOINT, False, detail)
            raise HomeAssistantError(detail) from err

        if reported is None:
            detail = (
                f"sent {temperature}F, but the spa's page came back with no "
                "setpoint at all — that is what the relay renders when the "
                "WF-100 is off the cloud"
            )
            self._record(JOB_SETPOINT, False, detail)
            raise HomeAssistantError(detail)

        if reported != temperature:
            detail = f"sent {temperature}F, but the spa still reports {reported}F"
            self._record(JOB_SETPOINT, False, detail)
            raise HomeAssistantError(detail)

        self._record(JOB_SETPOINT, True, f"spa confirms {reported}F")

        # Opportunistic, and never allowed to fail the job: while we are here,
        # see whether the panel has anything to say.
        try:
            await self._visit()
        except (aiohttp.ClientError, OSError, TimeoutError) as err:
            _LOGGER.debug("No reading taken after the setpoint change: %s", err)

    async def _read_back(self, expected: int) -> int | None:
        """Return the spa's own setpoint, retrying while it settles."""
        reported = None
        for _ in range(CONFIRM_ATTEMPTS):
            await asyncio.sleep(CONFIRM_DELAY_SECONDS)
            async with self._http.get(f"{self._base_url}/{PATH_APP}") as page:
                if page.status != 200:
                    raise HomeAssistantError(f"app page returned {page.status}")
                reported = parse_setpoint(await page.text())
            self.reported_setpoint = reported
            if reported == expected:
                self.setpoint_confirmed_at = dt_util.utcnow()
                self._notify_listeners()
                return reported
        self._notify_listeners()
        return reported

    async def async_sync_clock(self, when: datetime) -> None:
        """Set the spa's clock, and record whether it could be delivered.

        The panel takes a 12-hour clock as four digits plus an A or P -- 3:21pm
        is ``0321P`` -- on the same socket the buttons use.

        **This cannot be confirmed.** The clock cannot be read back: the display
        multiplexes between water temperature and setpoint, never the time. So
        what is recorded here is delivery, not correctness — the socket opened,
        the relay did not say it had lost the spa, and the frame went out. That
        is weaker than the setpoint's confirmation and is labelled as such.

        The filter cycle is the way to close this gap later: FP1 runs noon to
        3pm spa-local, so a filtering bit that comes on at noon is a clock that
        is right. `binary_sensor` exposes that bit; nothing acts on it yet.
        """
        meridiem = "A" if when.hour < 12 else "P"
        frame = json.dumps({"time": f"{when:%I%M}{meridiem}"})
        try:
            await self._visit(send=frame)
        except (aiohttp.ClientError, OSError, TimeoutError) as err:
            detail = f"could not reach the spa to set the clock: {err}"
            self._record(JOB_CLOCK, False, detail)
            raise HomeAssistantError(detail) from err
        except HomeAssistantError as err:
            detail = f"clock not set: {err}"
            self._record(JOB_CLOCK, False, detail)
            raise HomeAssistantError(detail) from err

        self._record(
            JOB_CLOCK, True, f"clock frame delivered ({when:%-I:%M %p} spa-local)"
        )

    async def async_refresh(self) -> None:
        """Take a reading now, on demand.

        May come back with nothing: frames arrive in bursts and a quiet spa can
        easily say nothing for half an hour. The reading's timestamp is what
        tells you whether it worked, so this never raises on silence.
        """
        try:
            if not await self._visit(listen=REFRESH_SECONDS):
                _LOGGER.info("Spa said nothing during the refresh window")
        except (aiohttp.ClientError, OSError, TimeoutError) as err:
            raise HomeAssistantError(f"Could not reach the spa: {err}") from err

    async def async_press(self, code: str) -> None:
        """Send one command code, opening a socket for it."""
        try:
            await self._visit(send=code, listen=REFRESH_SECONDS)
        except (aiohttp.ClientError, OSError, TimeoutError) as err:
            raise HomeAssistantError(f"Could not reach the spa: {err}") from err

    # ---- decoding what arrives ----------------------------------------------

    @callback
    def _handle_message(self, raw: str | bytes) -> bool:
        """Decode one frame. Returns True if it was a real display frame."""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "ignore")
        try:
            parsed = json.loads(raw)
        except ValueError:
            _LOGGER.debug("Ignoring non-JSON frame from spa: %r", raw)
            return False

        if not isinstance(parsed, dict):
            return False

        if KEY_RELAY_STATUS in parsed:
            self.relay_linked = bool(parsed[KEY_RELAY_STATUS])
            if not self.relay_linked:
                _LOGGER.warning("Spa relay reports it has no link to the spa")

        dsp = parsed.get("dsp")
        # Ignore all-zero / too-short frames, matching the original plugin.
        if not isinstance(dsp, str) or len(dsp) < 12 or dsp.strip("0") == "":
            return False

        # A display frame is the panel's own output, so it is proof of a live
        # link whatever the relay last claimed.
        self.relay_linked = True
        flags = int(dsp[8:10], 16)

        self.jets_state = STATE_OFF
        for flag, state in DSP_FLAG_TO_STATE:
            if flags & flag:
                self.jets_state = state
                break
        _LOGGER.debug("Spa jets: %s", STATE_NAMES[self.jets_state])

        self.heating = bool(flags & FLAG_HEATING)
        self.filtering = bool(flags & FLAG_FILTERING)

        # While the edit LED is lit the panel is showing the set temperature
        # rather than the water temperature, so that reading is not what the
        # temperature sensor reports.
        editing = bool(flags & FLAG_EDIT)
        reading = decode_temperature(dsp)
        if reading is not None and not editing:
            self.temperature, self.temperature_unit = reading
            self.measured_at = dt_util.utcnow()
            _LOGGER.debug("Spa temperature: %s°%s", *reading)

        return True
