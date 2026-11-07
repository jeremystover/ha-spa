"""Which module we are talking to, and what that changes.

The spa carries two modules on the same panel bus:

* the **WF-100** — WiFi only, the original, and the one the legacy web relay at
  ``wss://host/spa/<token>/wsb`` serves;
* the **BT-200** — Bluetooth *and* WiFi, the one the iOS app talks to, at
  ``wss://host/wsa/<mac>/<role>/<id>/<token>``.

They speak the same inbound protocol — ``{"dsp": "<12 hex>"}``, byte for byte,
decoded by the same code — and differ in what they accept:

===========  ==========================  ==============================
             WF-100 (``/wsb``)           BT-200 (``/wsa/``)
===========  ==========================  ==============================
setpoint     form POST to ``/settemp``   ``{"temp":"101F"}`` on the socket
clock        ``{"time":"1012A"}``        the same
keys         bare code, ``"3"``          ``{"keysec":"j0"}``
===========  ==========================  ==============================

The setpoint is the reason to prefer the BT-200. On the WF-100 path, setting a
temperature meant fetching ``/app`` for a session cookie, posting a form, and
reading the value back off the rendered page -- and that readback turned out to
be echoing the value just written rather than reporting the spa's. It spent a
week confirming 85F at a spa that held something else. Here there is no HTTP
layer left to lie: the write is a socket frame and the confirmation is the
panel's own display.

Which transport to use is read off the URL rather than configured, because the
URL already says. Getting it wrong is not subtle -- a ``keysec`` frame means
nothing to the web relay and a bare ``"3"`` means nothing to the BT-200 -- so
there is no value in letting the two disagree.

Every frame here is byte-for-byte what the iOS app was captured sending on
7 November 2026, compact separators included. The app's form is the only one
with evidence behind it; guessing at whitespace tolerance on hardware that
cannot be re-flashed is not worth the two bytes saved.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime

from .const import PANEL, PANEL_KEY_CODES, RELAY, RELAY_KEY_CODES

# wss://host/wsa/<12 hex mac>/<M or N>/<id>/<token>
#
# Matched on the ``/wsa/`` path and the role letter rather than on the host,
# because the host is the same for both modules -- they go through one relay.
_PANEL_PATH = re.compile(r"/wsa/[0-9a-f]{12}/[MN]/", re.IGNORECASE)

_LABELS = {PANEL: "BT-200", RELAY: "WF-100"}


def _frame(payload: dict[str, str]) -> str:
    """Serialize a command the way the app does: no spaces."""
    return json.dumps(payload, separators=(",", ":"))


@dataclass(frozen=True)
class Transport:
    """One module's endpoint, and the wire format it expects."""

    url: str
    kind: str

    @property
    def is_panel(self) -> bool:
        """Whether this is the BT-200, which takes the setpoint on the socket."""
        return self.kind == PANEL

    @property
    def label(self) -> str:
        """The module's name, for saying which one answered."""
        return _LABELS[self.kind]

    def clock_frame(self, when: datetime) -> str:
        """The frame that sets the panel clock.

        ``HHMM`` in 12-hour form plus ``A`` or ``P``: 3:21 pm is ``0321P``,
        midnight ``1200A`` and noon ``1200P``. Both modules take it.
        """
        meridiem = "A" if when.hour < 12 else "P"
        return _frame({"time": f"{when:%I%M}{meridiem}"})

    def setpoint_frame(self, temperature: int) -> str | None:
        """The frame that sets the target temperature, or None on the WF-100.

        None is not a failure: the web relay has no socket command for this at
        all, which is why that path goes through HTTP. Three digits, zero
        padded -- the app sent ``099F`` for 99, not ``99F``.
        """
        if not self.is_panel:
            return None
        return _frame({"temp": f"{temperature:03d}F"})

    def key_frame(self, key: str) -> str:
        """The frame for one panel key press.

        Takes a logical key -- ``jets``, ``filter`` -- because the two modules
        do not share a code for either, and a button that means "jets" should
        not have to know which module is on the other end.
        """
        codes = PANEL_KEY_CODES if self.is_panel else RELAY_KEY_CODES
        if (code := codes.get(key)) is None:
            raise KeyError(f"the {self.label} has no known code for {key!r}")
        return _frame({"keysec": code}) if self.is_panel else code


def transport_for(url: str) -> Transport:
    """Pick the transport the URL's own shape calls for."""
    return Transport(url, PANEL if _PANEL_PATH.search(url) else RELAY)
