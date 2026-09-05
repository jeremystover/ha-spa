"""Drive the spa coordinator with the Home Assistant runtime stubbed out.

Covers availability and the relay link, a full day of scheduled traffic, the
setpoint readback, and the decoder's plausibility bound. No dependencies --
run it with plain python3.
"""

import pathlib
import sys
import types
from datetime import datetime, timedelta, timezone

_HERE = pathlib.Path(__file__).resolve().parent.parent

# --- minimal homeassistant stubs -------------------------------------------
def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m

NOW = [datetime(2026, 9, 5, 0, 0, tzinfo=timezone.utc)]

_mod("aiohttp", ClientError=type("ClientError", (Exception,), {}),
     ClientWebSocketResponse=object,
     WSMsgType=types.SimpleNamespace(TEXT=1, BINARY=2, CLOSED=3, ERROR=4))
_mod("homeassistant")
_mod("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
_mod("homeassistant.exceptions",
     HomeAssistantError=type("HomeAssistantError", (Exception,), {}))
_mod("homeassistant.helpers")
_mod("homeassistant.helpers.aiohttp_client",
     async_create_clientsession=lambda hass: None,
     async_get_clientsession=lambda hass: None)
_mod("homeassistant.helpers.event",
     async_track_time_interval=lambda hass, cb, iv: (lambda: None))
_mod("homeassistant.util")
_mod("homeassistant.util.dt", utcnow=lambda: NOW[0])

# Stand in for the package itself so relative imports resolve without running
# the real __init__.py, which pulls in voluptuous and the config-entry stack.
_pkg = types.ModuleType("spa_websocket")
_pkg.__path__ = [str(_HERE / "custom_components" / "spa_websocket")]
sys.modules["spa_websocket"] = _pkg

from spa_websocket.coordinator import SpaConnection  # noqa: E402
from spa_websocket.const import STALE_AFTER_SECONDS  # noqa: E402

FRAME = '{"dsp":"007d30ce0500"}'
fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")
    if not ok:
        fails.append(label)


c = SpaConnection(object(), "wss://h/spa/TOKEN/wsb")

print("before any frame (the state after a fresh reconnect):")
check("available", c.available, False)

print("\nafter a real display frame:")
c._handle_message(FRAME)
check("available", c.available, True)
check("temperature decoded", c.temperature, 91)

print(f"\nafter {STALE_AFTER_SECONDS}s of silence:")
NOW[0] += timedelta(seconds=STALE_AFTER_SECONDS + 1)
check("available", c.available, False)

print("\nrelay chatter must NOT count as the spa reporting:")
c._handle_message('{"stsR":0}')
check("available", c.available, False)
check("relay_linked", c.relay_linked, False)

print("\nfresh frame revives it:")
c._handle_message(FRAME)
check("available (relay still says 0)", c.available, False)
c._handle_message('{"stsR":1}')
check("available (relay recovered)", c.available, True)

print("\nthe real-world case — relay up, spa gone, only stsR:0 arriving:")
c2 = SpaConnection(object(), "wss://h/spa/TOKEN/wsb")
c2._handle_message('{"stsR":0}')
check("available", c2.available, False)



# --- traffic: a full simulated day of the hourly schedule --------------------
import asyncio  # noqa: E402


APP_PAGE = (
    '<form method="post" action="https://h/spa/TOKEN/settemp">'
    '<input type="number" name="void" id="slider-1" min="45" max="104" '
    'step="1" value="{sp}">'
    '<input type="hidden" name="temp" id="slider-F" value="{sp}">'
    "</form>"
)


class FakeResp:
    status = 200

    def __init__(self, body=""):
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self):
        return self._body


class FakeHTTP:
    """Counts what actually goes over the wire, and echoes a page back."""

    def __init__(self, echo=True):
        self.gets = 0
        self.posts = 0
        self.echo = echo
        self.last_sent = None

    def get(self, url):
        self.gets += 1
        sp = self.last_sent if self.last_sent is not None else 85
        return FakeResp(APP_PAGE.format(sp=sp) if self.echo else "")

    def post(self, url, data=None):
        self.posts += 1
        self.last_sent = int(data["temp"])
        return FakeResp(APP_PAGE.format(sp=self.last_sent) if self.echo else "")


def simulate_day():
    """Run 24 hourly enforcements exactly as the schedule automation does."""
    conn = SpaConnection(object(), "wss://h/spa/TOKEN/wsb")
    http = FakeHTTP()
    conn._http = http
    for hour in range(24):
        NOW[0] = datetime(2026, 9, 6, hour, 0, tzinfo=timezone.utc)
        conn._handle_message(FRAME)  # the spa keeps reporting all day
        want = 103 if hour in (15, 16, 17) else 85
        asyncio.run(conn.async_set_temperature(want))
    return http, conn


print("\n=== one day of the hourly schedule ===")
NOW[0] = datetime(2026, 9, 6, 0, 0, tzinfo=timezone.utc)
http, conn = simulate_day()
before = 24 * 2  # every hour did a GET /app plus a POST
after = http.gets + http.posts
print(f"  GET /app (session mints): {http.gets}   was 24")
print(f"  POST settemp:             {http.posts}   was 24")
print(f"  total requests:           {after}   was {before}"
      f"   ({100 - round(100 * after / before)}% fewer)")
check("setpoint still ends correct", conn._setpoint, 85)
check("session mints under 24", http.gets < 24, True)
check("both schedule transitions sent", http.posts >= 2, True)

print("\nan outage must clear the dedupe so recovery re-asserts:")
NOW[0] += timedelta(seconds=STALE_AFTER_SECONDS + 1)
conn._async_check_staleness(NOW[0])
check("available", conn.available, False)
check("remembered setpoint cleared", conn._setpoint, None)
conn._handle_message(FRAME)
asyncio.run(conn.async_set_temperature(85))
check("re-asserted after recovery", conn._setpoint, 85)



# --- setpoint readback -------------------------------------------------------
from spa_websocket.coordinator import parse_setpoint  # noqa: E402
from spa_websocket.decode import decode_temperature, plausible  # noqa: E402

print("\n=== setpoint readback off the app page ===")
check("parses the rendered form", parse_setpoint(APP_PAGE.format(sp=103)), 103)
check("attribute order independent",
      parse_setpoint('<input value="99" name="void" type="number">'), 99)
check("no form -> None", parse_setpoint("<html>nothing here</html>"), None)

NOW[0] = datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc)
c3 = SpaConnection(object(), "wss://h/spa/TOKEN/wsb")
c3._http = FakeHTTP()
c3._handle_message(FRAME)
asyncio.run(c3.async_set_temperature(103))
check("reports the spa's value, not ours", c3.reported_setpoint, 103)

print("\na page that does not echo must not invent a reading:")
c4 = SpaConnection(object(), "wss://h/spa/TOKEN/wsb")
c4._http = FakeHTTP(echo=False)
c4._handle_message(FRAME)
asyncio.run(c4.async_set_temperature(103))
check("reported_setpoint stays None", c4.reported_setpoint, None)

print("\nan outage clears the readback too (it is stale, not truth):")
NOW[0] += timedelta(seconds=STALE_AFTER_SECONDS + 1)
c3._async_check_staleness(NOW[0])
check("cleared", c3.reported_setpoint, None)


# --- decoder plausibility ----------------------------------------------------
print("\n=== decoder rejects what cannot be spa water ===")
for value, unit, want in [
    (92, "F", True), (40, "F", True), (115, "F", True),
    (19, "F", False), (194, "F", False), (195, "F", False),
    (592, "F", False), (599, "F", False), (992, "F", False),
    (38, "C", True), (46, "C", True), (99, "C", False),
]:
    check(f"{value}{unit} plausible", plausible(value, unit), want)

print("\nreal frames still decode:")
check("upright 92F", decode_temperature("f15b6f000500"), (92, "F"))
check("flipped 96F", decode_temperature("007d6fce1400"), (96, "F"))
check("flipped 91F", decode_temperature("007d30ce0500"), (91, "F"))
check("ECon is not a temperature", decode_temperature("4f0f63620000"), None)

print("\n" + ("ALL PASS" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
