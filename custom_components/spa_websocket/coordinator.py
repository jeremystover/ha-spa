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

import aiohttp

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DSP_BYTE_TO_STATE, RECONNECT_DELAY, STATE_NAMES, STATE_OFF

_LOGGER = logging.getLogger(__name__)


class SpaConnection:
    """Maintains a single reconnecting WebSocket connection to the spa."""

    def __init__(self, hass: HomeAssistant, url: str) -> None:
        """Initialize the connection."""
        self.hass = hass
        self.url = url
        self.jets_state: int = STATE_OFF
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._task: asyncio.Task | None = None
        self._closing = False
        self._listeners: list[Callable[[], None]] = []

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

    async def stop(self) -> None:
        """Stop the connection loop and close the socket."""
        self._closing = True
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
                async with session.ws_connect(self.url) as ws:
                    self._ws = ws
                    _LOGGER.info("Spa WebSocket connected to %s", self.url)
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
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
    def _handle_message(self, raw: str) -> None:
        """Parse an incoming message and update the jets state."""
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return

        dsp = parsed.get("dsp") if isinstance(parsed, dict) else None
        if not isinstance(dsp, str):
            return

        # Ignore all-zero / too-short frames, matching the original plugin.
        if len(dsp) < 10 or dsp.strip("0") == "":
            return

        new_state = DSP_BYTE_TO_STATE.get(dsp.lower()[8:10], STATE_OFF)
        if new_state != self.jets_state:
            _LOGGER.info("Spa jets changed to: %s", STATE_NAMES[new_state])
            self.jets_state = new_state
            self._notify_listeners()
