"""Drive the spa coordinator with the Home Assistant runtime stubbed out.

The contract under test is narrow and deliberate: three jobs a day, each one
confirmed against the spa's own account of itself, and a failure to confirm
surfaced rather than swallowed. No dependencies -- run it with plain python3.
"""

import asyncio
import pathlib
import sys
import types
from datetime import datetime, timezone

_HERE = pathlib.Path(__file__).resolve().parent.parent


# --- minimal homeassistant stubs -------------------------------------------
def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


NOW = [datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)]

_mod("aiohttp", ClientError=type("ClientError", (Exception,), {}),
     ClientWebSocketResponse=object,
     WSMsgType=types.SimpleNamespace(
         TEXT=1, BINARY=2, CLOSE=3, CLOSED=4, CLOSING=5, ERROR=6))
_mod("homeassistant")
_mod("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
_mod("homeassistant.exceptions",
     HomeAssistantError=type("HomeAssistantError", (Exception,), {}))
_mod("homeassistant.helpers")
_mod("homeassistant.helpers.aiohttp_client",
     async_create_clientsession=lambda hass: None,
     async_get_clientsession=lambda hass: None)
_mod("homeassistant.util")
_mod("homeassistant.util.dt", utcnow=lambda: NOW[0])

# Stand in for the package itself so relative imports resolve without running
# the real __init__.py, which pulls in voluptuous and the config-entry stack.
_pkg = types.ModuleType("spa_websocket")
_pkg.__path__ = [str(_HERE / "custom_components" / "spa_websocket")]
sys.modules["spa_websocket"] = _pkg

import spa_websocket.coordinator as coord  # noqa: E402
from spa_websocket.coordinator import SpaConnection, parse_setpoint  # noqa: E402
from spa_websocket.const import (  # noqa: E402
    JOB_CLOCK,
    JOB_READING,
    JOB_SETPOINT,
)
from spa_websocket.decode import decode_temperature, plausible  # noqa: E402

HomeAssistantError = sys.modules["homeassistant.exceptions"].HomeAssistantError

# Real waits would make this suite take a minute for no benefit.
coord.CONFIRM_DELAY_SECONDS = 0
coord.RELAY_ANSWER_SECONDS = 0.01
coord.VISIT_SECONDS = 0.01
coord.REFRESH_SECONDS = 0.01

FRAME = '{"dsp":"007d30ce0500"}'          # 91F, heating + low jets
FILTER_FRAME = '{"dsp":"007d30ce1000"}'   # filtering bit set
fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")
    if not ok:
        fails.append(label)


def run(coro):
    return asyncio.run(coro)


# --- fake HTTP ---------------------------------------------------------------
APP_PAGE = (
    '<form method="post" action="https://h/spa/TOKEN/settemp">'
    '<input type="number" name="void" id="slider-1" min="45" max="104" '
    'step="1" value="{sp}">'
    "</form>"
)
OFFLINE_PAGE = "<html><body>Spa control</body></html>"  # renders, no temperature


class FakeResp:
    def __init__(self, body="", status=200):
        self._body = body
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self):
        return self._body


class FakeHTTP:
    """Counts what goes over the wire and controls what comes back.

    ``reports`` is what the spa's page will claim its setpoint is: an int, the
    string "offline" for a page with no temperature on it at all, or a list to
    play out one answer per read.
    """

    def __init__(self, reports=None):
        self.gets = 0
        self.posts = 0
        self.sent = None
        self.reports = reports

    def _page(self):
        value = self.reports
        if isinstance(value, list):
            value = value.pop(0) if len(value) > 1 else value[0]
        if value == "offline":
            return OFFLINE_PAGE
        if value is None:
            value = self.sent if self.sent is not None else 85
        return APP_PAGE.format(sp=value)

    def get(self, url):
        self.gets += 1
        return FakeResp(self._page())

    def post(self, url, data=None):
        self.posts += 1
        self.sent = int(data["temp"])
        return FakeResp(self._page())


# --- fake WebSocket ----------------------------------------------------------
class FakeWS:
    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []

    async def receive(self):
        if self.frames:
            return types.SimpleNamespace(type=1, data=self.frames.pop(0))
        await asyncio.sleep(3600)  # silence; the listen window expires

    async def send_str(self, text):
        self.sent.append(text)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, ws):
        self.ws = ws
        self.connects = 0

    def ws_connect(self, url, heartbeat=None):
        self.connects += 1
        return self.ws


def with_socket(frames):
    """Point the coordinator at a fake socket, and hand back the fake."""
    ws = FakeWS(frames)
    session = FakeSession(ws)
    coord.async_get_clientsession = lambda hass: session
    return ws, session


def new_conn(http=None, frames=()):
    conn = SpaConnection(object(), "wss://h/spa/TOKEN/wsb")
    conn._http = http if http is not None else FakeHTTP()
    with_socket(frames)
    return conn


# =============================================================================
print("=== the setpoint job: confirmed against the spa's own page ===")
http = FakeHTTP()
c = new_conn(http)
run(c.async_apply_setpoint(103))
check("job recorded ok", c.jobs[JOB_SETPOINT].ok, True)
check("spa's value reported back", c.reported_setpoint, 103)
check("nothing is failing", c.failing, [])
check("confirmed_at stamped", c.setpoint_confirmed_at is not None, True)

print("\nthe relay says no link — doubt the echo, but SEND ANYWAY:")
print("  (on 8 Sep the relay reported no link at 07:00 and the spa heated")
print("   perfectly that afternoon; withholding the write would have cost")
print("   the day on the strength of a signal that was simply wrong)")
c = new_conn(FakeHTTP(), frames=['{"stsR":0}'])
try:
    run(c.async_apply_setpoint(103))
    check("raised", False, True)
except HomeAssistantError as err:
    check("raised", True, True)
    check("names the echo", "not a confirmation" in str(err), True)
    check("says it sent anyway", "sent regardless" in str(err), True)
check("job recorded failed", c.jobs[JOB_SETPOINT].ok, False)
check("the write WAS sent", c._http.posts, 1)

print("\nsilence from the relay is NOT a denial — do not invent a failure:")
c = new_conn(FakeHTTP(), frames=[])
run(c.async_apply_setpoint(103))
check("job ok", c.jobs[JOB_SETPOINT].ok, True)

print("\nthe spa keeps a different value — the write did not land:")
c = new_conn(FakeHTTP(reports=85))
try:
    run(c.async_apply_setpoint(103))
    check("raised", False, True)
except HomeAssistantError as err:
    check("raised", True, True)
    check("says both numbers", "103" in str(err) and "85" in str(err), True)
check("job recorded failed", c.jobs[JOB_SETPOINT].ok, False)
check("failing names the job", c.failing, [JOB_SETPOINT])

print("\nthe page renders with no temperature — the WF-100 signature:")
print("  (this is the two-day outage: 200 OK, a page, and nothing behind it)")
c = new_conn(FakeHTTP(reports="offline"))
try:
    run(c.async_apply_setpoint(103))
    check("raised", False, True)
except HomeAssistantError as err:
    check("raised", True, True)
    check("names the real cause", "off the cloud" in str(err), True)
check("job recorded failed", c.jobs[JOB_SETPOINT].ok, False)

print("\na spa that takes a moment to settle still confirms:")
c = new_conn(FakeHTTP(reports=[85, 85, 103]))
run(c.async_apply_setpoint(103))
check("confirmed on a later read", c.jobs[JOB_SETPOINT].ok, True)
check("reported", c.reported_setpoint, 103)

print("\nrecovery clears the alarm — failing is the LAST run, not ever:")
c = new_conn(FakeHTTP(reports=85))
try:
    run(c.async_apply_setpoint(103))
except HomeAssistantError:
    pass
check("failing", c.failing, [JOB_SETPOINT])
c._http = FakeHTTP()
run(c.async_apply_setpoint(103))
check("failing after a good run", c.failing, [])


# =============================================================================
print("\n=== the clock job: delivery, honestly labelled ===")
c = new_conn(frames=['{"stsR":1}'])
run(c.async_sync_clock(datetime(2026, 9, 8, 15, 21)))
check("job recorded ok", c.jobs[JOB_CLOCK].ok, True)
ws, _ = with_socket(['{"stsR":1}'])
c2 = new_conn(frames=[])
coord.async_get_clientsession = lambda hass: FakeSession(ws)
run(c2.async_sync_clock(datetime(2026, 9, 8, 15, 21)))
check("sent the right frame", ws.sent, ['{"time": "0321P"}'])

print("\nmidnight and noon are the edge cases that break 12-hour clocks:")
for hour, minute, want in [(0, 0, "1200A"), (12, 0, "1200P"), (13, 5, "0105P")]:
    ws, _ = with_socket([])
    c3 = new_conn()
    coord.async_get_clientsession = lambda hass, _ws=ws: FakeSession(_ws)
    run(c3.async_sync_clock(datetime(2026, 9, 8, hour, minute)))
    check(f"{hour:02d}:{minute:02d}", ws.sent[0], '{"time": "%s"}' % want)

print("\nthe relay says it has lost the spa — do not pretend the clock was set:")
c = new_conn(frames=['{"stsR":0}'])
try:
    run(c.async_sync_clock(datetime(2026, 9, 8, 4, 0)))
    check("raised", False, True)
except HomeAssistantError as err:
    check("raised", True, True)
    check("says why", "accepted and discarded" in str(err), True)
check("job recorded failed", c.jobs[JOB_CLOCK].ok, False)
check("failing names the job", c.failing, [JOB_CLOCK])


# =============================================================================
print("\n=== readings keep their value and carry their age ===")
c = new_conn(frames=[FRAME])
run(c.async_refresh())
check("temperature", c.temperature, 91)
check("heating", c.heating, True)
check("measured_at stamped", c.measured_at, NOW[0])

print("\nsilence is not a fault — frames are bursty, gaps reach 33 minutes:")
c = new_conn(frames=[])
c.temperature, c.measured_at = 91, NOW[0]
run(c.async_refresh())          # must not raise
check("keeps the old reading", c.temperature, 91)
check("keeps the old timestamp", c.measured_at, NOW[0])

print("\na scheduled reading is how we check the water actually moved:")
c = new_conn(frames=[FRAME])
run(c.async_take_reading(0.01))
check("reading job ok", c.jobs[JOB_READING].ok, True)
check("nothing failing", c.failing, [])
check("detail carries the number", c.jobs[JOB_READING].detail, "91°F")

print("\na frame that lands while we are waiting on the relay still counts:")
print("  (it arrives in the opening listen; counting only the trailing one")
print("   would file a failed reading with a fresh number in hand)")
c = new_conn(frames=['{"stsR":1}', FRAME])
run(c.async_take_reading(0.01))
check("reading job ok", c.jobs[JOB_READING].ok, True)
check("temperature captured", c.temperature, 91)

print("\nno frame in the window is a FAILED reading, not a silent pass:")
print("  (comparing the water against a stale number is how you miss a cold tub)")
c = new_conn(frames=[])
c.temperature, c.measured_at = 91, NOW[0]
run(c.async_take_reading(0.01))
check("reading job failed", c.jobs[JOB_READING].ok, False)
check("failing names it", c.failing, [JOB_READING])
check("says it could not look", "no reading" in c.jobs[JOB_READING].detail, True)

print("\nbut the Refresh button a human pressed never sets the alarm:")
c = new_conn(frames=[])
run(c.async_refresh())
check("no job recorded", JOB_READING in c.jobs, False)
check("nothing failing", c.failing, [])

print("\nthe filtering bit, for verifying the clock later:")
c = new_conn(frames=[FILTER_FRAME])
run(c.async_refresh())
check("filtering", c.filtering, True)
c = new_conn(frames=[FRAME])
run(c.async_refresh())
check("not filtering", c.filtering, False)

print("\na display frame outranks a stale offline verdict:")
c = new_conn()
c._handle_message('{"stsR":0}')
check("relay says offline", c.relay_linked, False)
c._handle_message(FRAME)
check("frame proves otherwise", c.relay_linked, True)


# =============================================================================
print("\n=== what a day now costs the relay ===")
http = FakeHTTP()
c = new_conn(http)
_, session = with_socket([])
coord.async_get_clientsession = lambda hass: session
run(c.async_apply_setpoint(103))    # 15:00 here / noon spa-local
run(c.async_apply_setpoint(85))     # 18:00 here / 3pm spa-local
check("each setpoint job checks the link first", session.connects, 2)
run(c.async_sync_clock(datetime(2026, 9, 8, 4, 0)))
check("three short visits in the day", session.connects, 3)
print(f"  HTTP requests: {http.gets + http.posts}   (was 48 hourly, then 10)")
print("  socket:        3 short visits   (was open 24h with a 20s heartbeat)")
check("two setpoint writes", http.posts, 2)
check("both confirmed", [j.ok for j in c.jobs.values()], [True, True])


# =============================================================================
print("\n=== setpoint parsing ===")
check("parses the rendered form", parse_setpoint(APP_PAGE.format(sp=103)), 103)
check("attribute order independent",
      parse_setpoint('<input value="99" name="void" type="number">'), 99)
check("a page with no setpoint -> None", parse_setpoint(OFFLINE_PAGE), None)

print("\n=== decoder rejects what cannot be spa water ===")
for value, unit, want in [
    (92, "F", True), (40, "F", True), (115, "F", True),
    (19, "F", False), (194, "F", False), (592, "F", False), (992, "F", False),
    (38, "C", True), (99, "C", False),
]:
    check(f"{value}{unit} plausible", plausible(value, unit), want)

print("\nreal frames still decode:")
check("upright 92F", decode_temperature("f15b6f000500"), (92, "F"))
check("flipped 96F", decode_temperature("007d6fce1400"), (96, "F"))
check("ECon is not a temperature", decode_temperature("4f0f63620000"), None)

print("\n" + ("ALL PASS" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
