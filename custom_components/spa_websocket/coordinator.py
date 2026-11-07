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

*Which* endpoint it visits is read off the URL — see ``transport.py``. On the
BT-200 the setpoint is a socket frame confirmed against the panel's own display,
which is the whole reason to prefer it; on the WF-100 it is a form POST
confirmed against a page that has been caught echoing the write back. A second
URL may be configured, and a write that fails on the first is tried on it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
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
    JOB_READING,
    JOB_SETPOINT,
    KEY_RELAY_STATUS,
    PATH_APP,
    PATH_SETTEMP,
    REFRESH_SECONDS,
    RELAY_ANSWER_SECONDS,
    SETPOINT_ECHO_SECONDS,
    STATE_NAMES,
    STATE_OFF,
    VISIT_SECONDS,
)
from .decode import decode_temperature
from .transport import Transport, transport_for

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

    def __init__(
        self, hass: HomeAssistant, url: str, fallback_url: str | None = None
    ) -> None:
        """Initialize the connection."""
        self.hass = hass
        self.url = url
        self.transport = transport_for(url)
        self.fallback = transport_for(fallback_url) if fallback_url else None

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

        # The setpoint the panel showed during the visit in progress, from a
        # display frame with the edit LED lit. Reset at the start of every
        # setpoint write and never carried between visits: a number left over
        # from an earlier visit would confirm a write that never landed, which
        # is the September failure wearing a different hat.
        self._edit_reading: int | None = None

        # How each scheduled job last went. This is the alarm surface.
        self.jobs: dict[str, JobResult] = {}

        # Display frames seen, ever. Only ever compared against itself, to ask
        # "did the panel speak during that visit" -- which cannot be answered by
        # the trailing listen alone, because the frame often arrives during the
        # opening one and would then be counted as silence.
        self._frames = 0

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

    @staticmethod
    def _base_url(transport: Transport) -> str:
        """Return the HTTP base for a web-relay endpoint.

        ``wss://host/spa/<token>/wsb`` -> ``https://host/spa/<token>``

        Only meaningful for the WF-100. The BT-200's ``/wsa/`` path has no HTTP
        sibling at all, which is the point: nothing to fetch, nothing to echo.
        """
        base = transport.url.replace("wss://", "https://").replace("ws://", "http://")
        return base.rsplit("/", 1)[0]

    # ---- the socket, opened only when there is something to do ---------------

    async def _listen(
        self,
        ws: aiohttp.ClientWebSocketResponse,
        seconds: float,
        done: Callable[[], bool] | None = None,
    ) -> bool:
        """Read frames for up to ``seconds``, returning once we have an answer.

        By default the answer is "the panel spoke at all", and that is best
        effort by design: display frames are bursty -- gaps of ten to thirty
        minutes are normal on a perfectly healthy spa -- so a visit can easily
        end without one arriving. That is not a fault and must never be reported
        as one. A frame caught that way is a bonus reading, nothing more.

        ``done`` is for the one case that is not best effort: a setpoint write,
        which waits for the specific frame that confirms it rather than for any
        frame. While the panel is in edit mode it repeats that frame steadily,
        so there is a real answer to wait for.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        while (remaining := deadline - loop.time()) > 0:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
            except TimeoutError:
                return False
            if msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                spoke = self._handle_message(msg.data)
                if done() if done is not None else spoke:
                    return True
            elif msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.ERROR,
            ):
                return False
        return False

    async def _visit(
        self,
        transport: Transport,
        send: str | None = None,
        listen: float | None = None,
    ) -> bool:
        """Open the socket, optionally send one frame, listen briefly, hang up.

        The relay states its link on connect, promptly and reliably, so opening
        a socket is also how we ask whether the spa is there at all.
        """
        listen = VISIT_SECONDS if listen is None else listen
        self.relay_linked = None
        before = self._frames
        session = async_get_clientsession(self.hass)
        async with session.ws_connect(transport.url, heartbeat=HEARTBEAT) as ws:
            # Give the relay its moment to volunteer stsR before acting on it.
            await self._listen(ws, RELAY_ANSWER_SECONDS)
            if send is not None:
                if self.relay_linked is False:
                    raise HomeAssistantError(
                        "the relay reports no link to the spa, so the command "
                        "would have been accepted and discarded"
                    )
                await ws.send_str(send)
            if self._frames == before:
                await self._listen(ws, listen)
        self._notify_listeners()
        return self._frames > before

    # ---- the three jobs ------------------------------------------------------

    async def _write(
        self, attempt: Callable[[Transport], Awaitable[None]]
    ) -> Transport:
        """Run a write against the primary module, then the fallback.

        Returns whichever transport got it done. Raises with *both* failures
        named when neither did, because "the spa did not take it" is a different
        problem from "the spa took it, on the other module" and the alarm text
        is the only place that distinction survives.

        Worth being clear about what this does not buy. Both modules reach the
        spa through the same relay, so a relay outage takes both down together --
        this is not path redundancy and must never be described as such. What it
        covers is one module dropping off the cloud while the other stays up,
        which is precisely what the WF-100 did in September 2026 and the only
        reason a second URL is worth keeping.
        """
        errors = (HomeAssistantError, aiohttp.ClientError, OSError, TimeoutError)
        try:
            await attempt(self.transport)
        except errors as first:
            if self.fallback is None:
                raise HomeAssistantError(str(first) or repr(first)) from first
            # Loudly, even though the fallback may yet succeed: a primary that
            # has quietly failed every day for a week is the thing that cost
            # two days of cold water, and a job recorded as confirmed will not
            # say so.
            _LOGGER.warning(
                "Spa write failed on the %s, trying the %s: %s",
                self.transport.label,
                self.fallback.label,
                first,
            )
            try:
                await attempt(self.fallback)
            except errors as second:
                raise HomeAssistantError(
                    f"failed on the {self.transport.label} ({first}) and on the "
                    f"{self.fallback.label} ({second})"
                ) from second
            return self.fallback
        return self.transport

    async def async_apply_setpoint(self, temperature: int) -> None:
        """Set the target temperature and confirm the spa took it.

        How depends on the module. The BT-200 takes it as a socket frame and
        shows it back on the panel; the WF-100 takes a form POST and renders it
        on a page. Only the first of those is a confirmation in any strong
        sense, and the difference is the reason this work was done at all.

        Raises when it cannot be confirmed, which is the point of the exercise.
        """

        async def attempt(transport: Transport) -> None:
            if transport.is_panel:
                await self._setpoint_on_panel(transport, temperature)
            else:
                await self._setpoint_on_relay(transport, temperature)

        try:
            used = await self._write(attempt)
        except HomeAssistantError as err:
            detail = f"setting {temperature}F failed: {err}"
            self._record(JOB_SETPOINT, False, detail)
            raise HomeAssistantError(detail) from err

        self._record(JOB_SETPOINT, True, f"{used.label} confirms {temperature}F")

    async def _setpoint_on_panel(
        self, transport: Transport, temperature: int
    ) -> None:
        """Set the setpoint on the BT-200 and read it back off the panel.

        One socket frame, then wait for the panel to show the number with its
        edit LED lit. That is a real confirmation and the first one this
        integration has ever had: the display is the panel's own output, so it
        cannot show a value the panel does not hold. Contrast the WF-100's page,
        which spent a week rendering a setpoint the spa had never accepted.

        Note that the edit bit alone does not make a frame a setpoint -- the
        capture includes a *clock* frame with it lit. What makes one is the edit
        bit plus a reading that decodes as a temperature, which is what
        ``_handle_message`` requires before recording anything here.
        """
        frame = transport.setpoint_frame(temperature)
        if frame is None:  # pragma: no cover - transport.is_panel guards this
            raise HomeAssistantError(f"the {transport.label} has no setpoint frame")

        self._edit_reading = None
        session = async_get_clientsession(self.hass)
        async with session.ws_connect(transport.url, heartbeat=HEARTBEAT) as ws:
            # Send first and ask questions afterwards. The relay's own link
            # claim is not reliable enough to withhold a write on: on 8
            # September it reported no link at 07:00 and the spa heated
            # perfectly that afternoon. Sending into a dead module costs
            # nothing; not sending into a live one costs the whole day.
            await ws.send_str(frame)
            await self._listen(
                ws,
                SETPOINT_ECHO_SECONDS,
                done=lambda: self._edit_reading == temperature,
            )
        shown = self._edit_reading
        self._notify_listeners()

        if shown is None:
            raise HomeAssistantError(
                f"sent {temperature}F, but the panel never showed a setpoint in "
                f"{SETPOINT_ECHO_SECONDS}s — nothing confirms it landed"
            )
        if shown != temperature:
            raise HomeAssistantError(
                f"sent {temperature}F, but the panel shows {shown}F"
            )
        self.reported_setpoint = shown
        self.setpoint_confirmed_at = dt_util.utcnow()

    async def _setpoint_on_relay(
        self, transport: Transport, temperature: int
    ) -> None:
        """Set the setpoint on the WF-100, and distrust the readback.

        Confirmation is a *fresh* page load, not the POST's own echo. Whether
        that echo carries the new value or the pre-write one was never
        established against the hardware, so trusting it would be guessing; a
        page fetched a few seconds later is the spa's settled answer.

        A fresh page load is still not enough on its own, and this was learned
        the hard way. On 8 September 2026 the WF-100 went off the cloud and this
        job reported "spa confirms 85F" every hour for a day, while the clock job
        said the relay had no link and not one display frame arrived. The page
        was echoing our own POST back at us. A readback that repeats what you
        just wrote confirms nothing.

        So ask the relay whether it still has the spa, and refuse to call the
        echo a confirmation when it says no.

        But ask AFTER writing, never before, and never let the answer withhold
        the write. On 8 September the relay reported no link at 07:00 and the
        spa heated perfectly that afternoon regardless -- so that signal is not
        reliable enough to act on, only to doubt on. Gating the write behind it
        would have turned a working day into a cold one. Sending into a dead
        relay costs nothing; not sending into a live one costs the whole day.

        Raises when it cannot be confirmed, which is the point of the exercise.
        """
        base = self._base_url(transport)
        payload = {
            "flip-scale": "0",
            "void": str(temperature),
            "temp": str(temperature),
        }

        # The app page is what issues the session cookie the POST needs.
        async with self._http.get(f"{base}/{PATH_APP}") as page:
            if page.status != 200:
                raise HomeAssistantError(f"app page returned {page.status}")
            await page.text()
        async with self._http.post(f"{base}/{PATH_SETTEMP}", data=payload) as resp:
            if resp.status != 200:
                raise HomeAssistantError(f"settemp returned {resp.status}")
            await resp.text()
        reported = await self._read_back(transport, temperature)

        if reported is None:
            raise HomeAssistantError(
                f"sent {temperature}F, but the spa's page came back with no "
                "setpoint at all — that is what the relay renders when the "
                "WF-100 is off the cloud"
            )

        if reported != temperature:
            raise HomeAssistantError(
                f"sent {temperature}F, but the spa still reports {reported}F"
            )

        if not await self._link_still_good(transport):
            raise HomeAssistantError(
                f"the page echoed {reported}F back, but the relay says it has no "
                "link to the spa — it would echo either way, so this is not a "
                "confirmation. The write was sent regardless"
            )

    async def _link_still_good(self, transport: Transport) -> bool:
        """Whether the relay will vouch for its link to the spa.

        Only an explicit denial counts against it. The relay does not always
        volunteer stsR, and "has not said" is not "said no" -- and a socket that
        will not open is not evidence about the spa either, since the write it
        is being asked about went over HTTP and may well have landed.
        """
        try:
            await self._visit(transport, listen=0)
        except (aiohttp.ClientError, OSError, TimeoutError) as err:
            _LOGGER.debug("Could not ask the relay about its link: %s", err)
            return True
        return self.relay_linked is not False

    async def _read_back(self, transport: Transport, expected: int) -> int | None:
        """Return the spa's own setpoint, retrying while it settles."""
        reported = None
        for _ in range(CONFIRM_ATTEMPTS):
            await asyncio.sleep(CONFIRM_DELAY_SECONDS)
            async with self._http.get(f"{self._base_url(transport)}/{PATH_APP}") as page:
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

        Both modules take the same frame: a 12-hour clock as four digits plus an
        A or P, so 3:21pm is ``0321P``.

        **This records delivery, not correctness.** The socket opened, the relay
        did not say it had lost the spa, and the frame went out — that is all.
        The panel *can* now be read back (``decode_clock``), which is new as of
        November 2026, but a clock frame only appears when the display happens
        to be cycling the time, so verifying it is a separate job from setting
        it and is not wired up yet.
        """

        async def attempt(transport: Transport) -> None:
            await self._visit(transport, send=transport.clock_frame(when))

        try:
            used = await self._write(attempt)
        except HomeAssistantError as err:
            detail = f"clock not set: {err}"
            self._record(JOB_CLOCK, False, detail)
            raise HomeAssistantError(detail) from err

        self._record(
            JOB_CLOCK,
            True,
            f"{used.label} took the clock frame ({when:%-I:%M %p} spa-local)",
        )

    async def async_take_reading(
        self, seconds: float | None = None, record: bool = True
    ) -> bool:
        """Connect, wait for the panel to say something, and hang up.

        This is what answers the question a confirmed setpoint cannot: the spa
        accepting 103F says nothing about whether the water actually moved. Only
        a reading taken at the end of the window does.

        May come back with nothing -- frames arrive in bursts and a quiet spa can
        say nothing for half an hour -- so silence never raises. It is recorded
        as a failed reading instead, because "I could not look" is a different
        thing from "I looked and it was fine", and the checks downstream need to
        tell them apart rather than compare against a stale number.

        ``record`` is off for the Refresh button: a human pressing it and getting
        nothing should not set off an alarm.
        """
        window = REFRESH_SECONDS if seconds is None else seconds
        try:
            spoke = await self._visit(self.transport, listen=window)
        except (aiohttp.ClientError, OSError, TimeoutError) as err:
            if record:
                self._record(JOB_READING, False, f"could not reach the spa: {err}")
            raise HomeAssistantError(f"Could not reach the spa: {err}") from err

        if record:
            if spoke:
                self._record(
                    JOB_READING,
                    True,
                    f"{self.temperature}°{self.temperature_unit}",
                )
            else:
                self._record(
                    JOB_READING,
                    False,
                    f"connected, but the panel sent nothing in {window:.0f}s — "
                    "no reading to check the water against",
                )
        return spoke

    async def async_refresh(self) -> None:
        """Take a reading for a human who asked for one. Never sets the alarm."""
        await self.async_take_reading(record=False)

    async def async_press(self, key: str) -> None:
        """Press one panel key, opening a socket for it.

        Takes a logical key -- ``jets``, ``filter`` -- not a wire code, because
        the two modules share neither the codes nor the framing: the WF-100
        wants a bare ``"3"`` and the BT-200 a ``{"keysec":"j0"}``. Sending one
        module's vocabulary to the other is silently ignored, which is the worst
        possible outcome, so the transport does the translating.

        No fallback here. These are toggles with no readback, so a retry on the
        other module cannot tell a dropped press from a doubled one, and a spa
        whose jets come on and straight back off is worse than one whose button
        reported an error.
        """
        try:
            frame = self.transport.key_frame(key)
        except KeyError as err:
            raise HomeAssistantError(str(err)) from err
        await self.async_send_raw(frame)

    async def async_send_raw(self, code: str) -> None:
        """Send one frame exactly as given, for probing undocumented codes.

        Verbatim on purpose: the point of the diagnostic service is to try
        things this code does not know about, so wrapping or validating the
        payload would defeat it. On the BT-200 that means passing whole frames
        such as ``{"keysec":"l0"}``.
        """
        try:
            await self._visit(self.transport, send=code, listen=REFRESH_SECONDS)
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
        self._frames += 1
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
        # rather than the water temperature. That reading is not what the
        # temperature sensor reports -- and it is exactly what confirms a
        # setpoint write, so it is kept rather than thrown away.
        #
        # The edit bit on its own does not make a frame a setpoint: the November
        # capture includes a *clock* frame carrying it. Requiring a reading that
        # also decodes as a temperature is what separates them.
        editing = bool(flags & FLAG_EDIT)
        reading = decode_temperature(dsp)
        if reading is None:
            return True
        if editing:
            self._edit_reading = reading[0]
            self.reported_setpoint = reading[0]
            _LOGGER.debug("Spa panel shows setpoint %s°%s", *reading)
        else:
            self.temperature, self.temperature_unit = reading
            self.measured_at = dt_util.utcnow()
            _LOGGER.debug("Spa temperature: %s°%s", *reading)

        return True
