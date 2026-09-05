"""Drive SpaConnection's availability logic with the HA runtime stubbed out."""

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

print("\n" + ("ALL PASS" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
