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
    KEY_FILTER,
    KEY_JETS,
    PANEL,
    RELAY,
)
from spa_websocket.transport import transport_for  # noqa: E402
from spa_websocket.decode import (  # noqa: E402
    decode_clock,
    decode_temperature,
    panel_flipped,
    plausible,
)

HomeAssistantError = sys.modules["homeassistant.exceptions"].HomeAssistantError

# Real waits would make this suite take a minute for no benefit.
coord.CONFIRM_DELAY_SECONDS = 0
coord.RELAY_ANSWER_SECONDS = 0.01
coord.VISIT_SECONDS = 0.01
coord.REFRESH_SECONDS = 0.01
coord.SETPOINT_ECHO_SECONDS = 0.05
coord.READING_AFTER_SETPOINT_SECONDS = 0.05

FRAME = '{"dsp":"007d30ce0500"}'          # 91F, heating + low jets
FILTER_FRAME = '{"dsp":"007d30ce1000"}'   # filtering bit set

# The BT-200's URL *shape*, from the 7 November 2026 capture. The MAC, the
# account id and the token are all made up: the token is the credential -- it is
# the only thing guarding the spa -- and the other two identify the hardware, so
# none of the three belongs in a repository.
PANEL_URL = "wss://accsmartlink.com/wsa/0123456789ab/M/5000000000/" + "0" * 64
RELAY_URL = "wss://h/spa/TOKEN/wsb"

# Setpoint frames as the panel itself sent them, edit LED lit.
SETPOINT_FRAMES = {
    99: '{"dsp":"f16f6f002000"}',
    100: '{"dsp":"f13f3f062000"}',
    101: '{"dsp":"f1063f062000"}',
}
# A clock frame that ALSO carries the edit bit. Captured, not invented: the edit
# bit on its own cannot mean "this is a setpoint".
CLOCK_EDIT_FRAME = '{"dsp":"5b86bf06a000"}'

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


def new_conn(http=None, frames=(), url=RELAY_URL, fallback=None):
    conn = SpaConnection(object(), url, fallback)
    conn._http = http if http is not None else FakeHTTP()
    with_socket(frames)
    return conn


def new_panel(frames=(), fallback=None):
    """A connection driving the BT-200, with the socket faked."""
    return new_conn(frames=frames, url=PANEL_URL, fallback=fallback)


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
print("\n=== the transport is read off the URL, not configured ===")
check("a /wsa/ URL is the BT-200", transport_for(PANEL_URL).kind, PANEL)
check("a /wsb URL is the WF-100", transport_for(RELAY_URL).kind, RELAY)
check("guest tokens too", transport_for(PANEL_URL.replace("/M/", "/N/")).kind, PANEL)
check("case does not matter",
      transport_for(PANEL_URL.replace("89ab", "89AB")).kind, PANEL)
print("  (a keysec frame means nothing to the web relay and a bare \"3\" means")
print("   nothing to the BT-200, so the two must never be able to disagree)")

t_panel, t_relay = transport_for(PANEL_URL), transport_for(RELAY_URL)
print("\nthe frames, byte for byte as the iOS app was captured sending them:")
check("setpoint", t_panel.setpoint_frame(101), '{"temp":"101F"}')
check("zero padded -- the app sent 099F, not 99F",
      t_panel.setpoint_frame(99), '{"temp":"099F"}')
check("the relay has no socket setpoint", t_relay.setpoint_frame(101), None)
check("panel jets", t_panel.key_frame(KEY_JETS), '{"keysec":"j0"}')
check("panel filter is the system key", t_panel.key_frame(KEY_FILTER),
      '{"keysec":"s0"}')
check("relay jets stays a bare code", t_relay.key_frame(KEY_JETS), "3")
check("relay filter", t_relay.key_frame(KEY_FILTER), "4")
check("the clock frame is shared", t_panel.clock_frame(datetime(2026, 11, 7, 10, 12)),
      '{"time":"1012A"}')


# =============================================================================
print("\n=== the BT-200 setpoint: confirmed by the panel's own display ===")
print("  (the first real confirmation this integration has had -- the WF-100's")
print("   page spent a week rendering a setpoint the spa never accepted)")
c = new_panel(frames=[SETPOINT_FRAMES[101]])
run(c.async_apply_setpoint(101))
check("job recorded ok", c.jobs[JOB_SETPOINT].ok, True)
check("detail names the module", c.jobs[JOB_SETPOINT].detail, "BT-200 confirms 101F")
check("setpoint read off the panel", c.reported_setpoint, 101)
check("confirmed_at stamped", c.setpoint_confirmed_at, NOW[0])
check("nothing over HTTP at all", (c._http.gets, c._http.posts), (0, 0))

ws, _ = with_socket([SETPOINT_FRAMES[99]])
c = new_panel()
coord.async_get_clientsession = lambda hass, _ws=ws: FakeSession(_ws)
run(c.async_apply_setpoint(99))
check("sent the frame the app sends", ws.sent, ['{"temp":"099F"}'])

print("\nthe panel shows a different number -- the write did not land:")
c = new_panel(frames=[SETPOINT_FRAMES[100]])
try:
    run(c.async_apply_setpoint(101))
    check("raised", False, True)
except HomeAssistantError as err:
    check("raised", True, True)
    check("says both numbers", "101" in str(err) and "100" in str(err), True)
check("job recorded failed", c.jobs[JOB_SETPOINT].ok, False)
check("failing names the job", c.failing, [JOB_SETPOINT])

print("\nthe panel never shows a setpoint -- nothing confirms it:")
c = new_panel(frames=[FRAME])
try:
    run(c.async_apply_setpoint(101))
    check("raised", False, True)
except HomeAssistantError as err:
    check("raised", True, True)
    check("says nothing confirms it", "never showed a setpoint" in str(err), True)
check("job recorded failed", c.jobs[JOB_SETPOINT].ok, False)
check("the water reading still landed", c.temperature, 91)

print("\nthe edit bit alone is NOT a setpoint -- a captured CLOCK frame has it:")
print("  (5b86bf06a000 is 10:12 with the edit LED lit; reading that as a")
print("   setpoint would confirm any write with whatever the clock said)")
c = new_panel(frames=[CLOCK_EDIT_FRAME])
try:
    run(c.async_apply_setpoint(101))
    check("raised", False, True)
except HomeAssistantError as err:
    check("raised", True, True)
    check("not mistaken for a setpoint", "never showed a setpoint" in str(err), True)
check("nothing claimed as the setpoint", c.reported_setpoint, None)

print("\na stale echo from an earlier visit cannot confirm the next write:")
print("  (believing a number we already had is the September failure exactly)")
c = new_panel(frames=[SETPOINT_FRAMES[101]])
run(c.async_apply_setpoint(101))
check("first write confirmed", c.jobs[JOB_SETPOINT].ok, True)
with_socket([])                       # the panel says nothing this time
try:
    run(c.async_apply_setpoint(85))
    check("raised", False, True)
except HomeAssistantError as err:
    check("raised", True, True)
    check("did not reuse the 101", "never showed a setpoint" in str(err), True)
check("job recorded failed", c.jobs[JOB_SETPOINT].ok, False)

print("\nthe setpoint job also comes back with a WATER reading:")
print("  (7 Nov 2026: four confirmed setpoint jobs across the afternoon window")
print("   and a water temperature five hours stale. The write puts the panel in")
print("   edit mode, so every confirmation frame is the setpoint -- a visit that")
print("   stops there proves the spa took a number and nothing about the water)")
c = new_panel(frames=[SETPOINT_FRAMES[101], FRAME])
run(c.async_apply_setpoint(101))
check("setpoint confirmed", c.jobs[JOB_SETPOINT].detail, "BT-200 confirms 101F")
check("and the water was read too", c.temperature, 91)
check("measured_at stamped", c.measured_at, NOW[0])

print("\nthe clock frames that actually turn up first do not end the wait:")
print("  (three Refresh presses that night returned clock frames only; stopping")
print("   on the first frame of any kind would look like success and deliver")
print("   nothing)")
c = new_panel(frames=[SETPOINT_FRAMES[101], CLOCK_EDIT_FRAME, FRAME])
run(c.async_apply_setpoint(101))
check("still got the water", c.temperature, 91)

print("\nbut a quiet panel afterwards NEVER undoes the confirmation:")
print("  (the write already landed; a stale reading is not a failed setpoint)")
c = new_panel(frames=[SETPOINT_FRAMES[101]])
run(c.async_apply_setpoint(101))
check("setpoint still ok", c.jobs[JOB_SETPOINT].ok, True)
check("nothing failing", c.failing, [])
check("no water reading invented", c.temperature, None)

print("\nand a setpoint that never confirms does not wait around for water:")
c = new_panel(frames=[FRAME])
try:
    run(c.async_apply_setpoint(101))
    check("raised", False, True)
except HomeAssistantError:
    check("raised", True, True)

print("\nan edit frame caught on any visit updates the reported setpoint:")
c = new_panel(frames=[SETPOINT_FRAMES[100]])
run(c.async_refresh())
check("free truth, taken", c.reported_setpoint, 100)
check("and not mistaken for the water", c.temperature, None)


# =============================================================================
print("\n=== the fallback: one module dropping, not a second path ===")
print("  (both modules reach the spa through the same relay, so a relay outage")
print("   takes both down together -- this covers the September case, where the")
print("   WF-100 went off the cloud while the panel itself was fine)")
c = new_panel(frames=[], fallback=RELAY_URL)
c._http = FakeHTTP()
run(c.async_apply_setpoint(103))
check("confirmed on the fallback", c.jobs[JOB_SETPOINT].ok, True)
check("detail names which module answered",
      c.jobs[JOB_SETPOINT].detail, "WF-100 confirms 103F")
check("the fallback's write really went out", c._http.posts, 1)

print("\nthe primary works, so the fallback is never touched:")
c = new_panel(frames=[SETPOINT_FRAMES[101]], fallback=RELAY_URL)
c._http = FakeHTTP()
run(c.async_apply_setpoint(101))
check("confirmed on the primary", c.jobs[JOB_SETPOINT].detail,
      "BT-200 confirms 101F")
check("no HTTP at all", (c._http.gets, c._http.posts), (0, 0))

print("\nboth fail -- the alarm has to name both, not just the last one:")
c = new_panel(frames=[], fallback=RELAY_URL)
c._http = FakeHTTP(reports="offline")
try:
    run(c.async_apply_setpoint(103))
    check("raised", False, True)
except HomeAssistantError as err:
    check("raised", True, True)
    check("names the BT-200", "BT-200" in str(err), True)
    check("names the WF-100", "WF-100" in str(err), True)
check("job recorded failed", c.jobs[JOB_SETPOINT].ok, False)

print("\nthe clock falls back the same way:")
ws, _ = with_socket([])
c = new_panel(fallback=RELAY_URL)
coord.async_get_clientsession = lambda hass, _ws=ws: FakeSession(_ws)
run(c.async_sync_clock(datetime(2026, 11, 7, 4, 0)))
check("delivered", c.jobs[JOB_CLOCK].ok, True)
print("  (both modules take the same clock frame, so a single socket that")
print("   opens is enough -- there is nothing here for the fallback to fix)")

print("\nbut a key press never falls back -- a doubled toggle is worse:")
c = new_panel(fallback=RELAY_URL)
ws, _ = with_socket([])
coord.async_get_clientsession = lambda hass, _ws=ws: FakeSession(_ws)
run(c.async_press(KEY_JETS))
check("one frame, the panel's", ws.sent, ['{"keysec":"j0"}'])

print("\nand send_raw passes the payload through verbatim, for probing:")
c = new_panel()
ws, _ = with_socket([])
coord.async_get_clientsession = lambda hass, _ws=ws: FakeSession(_ws)
run(c.async_send_raw('{"keysec":"l0"}'))
check("untouched", ws.sent, ['{"keysec":"l0"}'])


# =============================================================================
print("\n=== the clock job: delivery, honestly labelled ===")
c = new_conn(frames=['{"stsR":1}'])
run(c.async_sync_clock(datetime(2026, 9, 8, 15, 21)))
check("job recorded ok", c.jobs[JOB_CLOCK].ok, True)
ws, _ = with_socket(['{"stsR":1}'])
c2 = new_conn(frames=[])
coord.async_get_clientsession = lambda hass: FakeSession(ws)
run(c2.async_sync_clock(datetime(2026, 9, 8, 15, 21)))
check("sent the right frame", ws.sent, ['{"time":"0321P"}'])

print("\nmidnight and noon are the edge cases that break 12-hour clocks:")
for hour, minute, want in [(0, 0, "1200A"), (12, 0, "1200P"), (13, 5, "0105P")]:
    ws, _ = with_socket([])
    c3 = new_conn()
    coord.async_get_clientsession = lambda hass, _ws=ws: FakeSession(_ws)
    run(c3.async_sync_clock(datetime(2026, 9, 8, hour, minute)))
    check(f"{hour:02d}:{minute:02d}", ws.sent[0], '{"time":"%s"}' % want)

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

# --- the clock, in the display ---------------------------------------------
# Every frame below was captured from the BT-200 on 7 November 2026, while the
# panel clock read 10:11 then 10:12.
print("\n=== the clock is in the display, so it can finally be verified ===")
print("  (until this decoded, setting the spa's clock was write-only)")
for frame, want in [
    ("0686bf068000", (10, 11)),   # colon lit
    ("06063f068000", (10, 11)),   # colon dark -- it blinks, so it is no signature
    ("5b86bf06a000", (10, 12)),
    ("5b063f068500", (10, 12)),   # heating and low jets on the same frame
]:
    check(f"{frame}", decode_clock(frame), want)

print("\nthe two readings never collide:")
for frame in ("f16d6d000000", "f13f3f062000", "f1063f062000"):
    check(f"{frame} is not a clock", decode_clock(frame), None)
for frame in ("0686bf068000", "5b86bf06a000"):
    check(f"{frame} is not a temperature", decode_temperature(frame), None)

print("\nthe setpoint you pressed, read back off the panel's own display:")
print("  (this is a real confirmation -- the old HTTP readback was a mirror)")
check("100F before the change", decode_temperature("f13f3f062000"), (100, "F"))
check("99F after temp-down", decode_temperature("f16f6f002000"), (99, "F"))
check("101F after temp-up", decode_temperature("f1063f062000"), (101, "F"))

print("\norientation comes from the scale marker, which a clock frame lacks:")
check("upright", panel_flipped("f16d6d000000"), False)
check("flipped", panel_flipped("007d6fce1400"), True)
check("a clock frame cannot say", panel_flipped("0686bf068000"), None)

print("\npartial frames mid-refresh are neither:")
for frame in ("f10000000000", "5b0600008000", "00003f068000"):
    check(f"{frame} temp", decode_temperature(frame), None)
    check(f"{frame} clock", decode_clock(frame), None)


# =============================================================================
# Found by moving a live entry onto a URL another entry already held, on 7
# November 2026, while testing the BT-200 against the real spa. Home Assistant
# only notices afterwards, and reports it as a bug in this integration.
print("\n=== two entries cannot claim one URL ===")
print("  (an entry's unique_id IS its URL, so moving one onto another's leaves")
print("   both claiming the same id -- HA logs 'already in use ... bug report')")
_cfg = types.ModuleType("voluptuous")
_cfg.Schema = _cfg.Required = _cfg.Optional = lambda *a, **k: None
sys.modules["voluptuous"] = _cfg


class _ConfigFlow:
    """Stand in for HA's ConfigFlow, which takes a domain= class keyword."""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()


_mod("homeassistant.config_entries", ConfigFlow=_ConfigFlow,
     ConfigFlowResult=dict, ConfigEntry=object)
from spa_websocket.config_flow import _check  # noqa: E402

MINE, THEIRS = RELAY_URL, PANEL_URL
check("a free URL is fine", _check({"url": MINE}, {THEIRS})[2], {})
check("a URL another entry holds is refused",
      _check({"url": THEIRS}, {THEIRS})[2], {"url": "url_in_use"})
check("keeping your own URL is not a conflict",
      _check({"url": MINE}, set())[2], {})
check("not a ws URL at all", _check({"url": "https://h/x"}, set())[2],
      {"url": "invalid_url"})
check("the fallback cannot be the primary",
      _check({"url": MINE, "fallback_url": MINE}, set())[2],
      {"fallback_url": "same_url"})
check("a blank fallback is simply absent",
      _check({"url": MINE, "fallback_url": "  "}, set()), (MINE, None, {}))
check("both URLs kept", _check({"url": MINE, "fallback_url": THEIRS}, set()),
      (MINE, THEIRS, {}))


print("\n" + ("ALL PASS" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
