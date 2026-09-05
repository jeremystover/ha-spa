# Wire protocol

Everything here was reverse-engineered against a live ACC SmarTouch + WF-100
SmartLink. None of it is vendor-documented. It is recorded so the next change
doesn't start from zero.

> **Credentials.** Every URL below contains a per-spa token, written `<token>`.
> That token *is* the password — anyone holding it can control the spa. The
> iOS app's URL additionally carries the module's MAC and an account id. Never
> commit, paste, or screenshot a real one.

## Endpoints

| Path | Used by | Notes |
| --- | --- | --- |
| `wss://accsmartlink.com/spa/<token>/wsb` | this integration | `b` for browser |
| `wss://accsmartlink.com/wsa/<mac>/M/<id>/<token>` | the iOS app | `a` for app |
| `https://accsmartlink.com/spa/<token>/app` | both | Page; issues the session cookie |
| `https://accsmartlink.com/spa/<token>/settemp` | web app | Form POST |
| `https://accsmartlink.com/spa/<token>/settz` | web app | Form POST |

Both sockets reach the same relay and accept the same frames — the clock frame
was captured on `/wsa/` and verified working on `/wsb/`.

The relay closes idle sockets after about 60 seconds, which silently dropped
commands sent in the gap. A 20-second WebSocket heartbeat fixes it.

## Status frames

The spa pushes `{"dsp": "<12 hex chars>"}`, six bytes:

```
  00      7d      6f      ce      14      00
  └───────┴───────┴───────┘       │       │
   4 seven-segment characters     │       └─ LED byte 2
                                  └───────── LED byte 1
```

### Seven-segment characters (bytes 0–3)

Bit 0 = segment a … bit 6 = segment g. Bit 7 is the decimal point / degree mark
and is masked before lookup.

The panel can be **mounted either way up**, and a flipped panel is not simply
the bytes reversed — each glyph is also rotated 180°, swapping a↔d, b↔e, c↔f
and leaving g alone. Getting this wrong is not harmless: under rotation `6` and
`9` map to each other, so a flipped panel read as upright reports 96 where it
means 69. `1`, `3`, `4` and `7` rotate into shapes that aren't digits at all.

Orientation is detected from the scale marker, which sits at whichever end the
orientation puts it — `0x71` (`F`) or `0x39` (`C`) upright, or their rotations
`0xce` / `0x9c` when flipped. Digits then run from the marker: ones, tens,
hundreds.

```
f1 6d 6f 00  ->  95°F   (upright)
00 7d 6f ce  ->  96°F   (flipped)
```

A digit carrying bit 7 is a decimal point, which means Celsius tenths (the spa
adjusts Celsius in 0.1° steps). Whole degrees only, so those frames decode to
nothing rather than reporting 385 for 38.5.

See `decode.py`. The display is **multiplexed**: at idle it shows water
temperature, but while the setpoint is being edited it shows the setpoint
instead — check the edit LED before trusting a reading as water temperature.

### LED bitfields (bytes 4–5)

Taken from the web app's own bit-to-LED map. Byte 4:

| Bit | Meaning |
| --- | --- |
| `0x01` | Heating |
| `0x02` | Air / blower high |
| `0x04` | Jets low |
| `0x08` | Jets high |
| `0x10` | Filtering |
| `0x20` | Edit (setpoint being adjusted) |
| `0x40` | Overheat |

Byte 5: `0x10` = light.

These are **bit tests, not values**. Heating alongside low jets is `0x05`, so an
equality check against `0x04` misses it — an actual bug that was fixed here.

### The heater runs on demand

Worth stating plainly because the opposite was assumed for a while: **heating
does not require a filter cycle.** A frame captured at 18:57 spa-local, outside
both FP1 (12:00–15:00) and FP2 (23:00–23:45), read:

```
007d3fce0500
         └─ 0x05 = HEATING | JETS_LO, filtering bit clear
```

taken moments after the setpoint was raised above the water temperature. So the
setpoint is live around the clock, and the low setpoint — not the filter
schedule — is what keeps the heater off during expensive hours.

**Unresolved:** the flag has also been observed set with the water *above*
setpoint (water 91 °F, setpoint 85 °F). It may cover a heat cycle including pump
overrun rather than the element alone, or the setpoint may not have landed.
Read it as "heating activity", not "element energized", until someone pins it
down.

### A transient worth recognising

While a setpoint write is in flight the edit bit (`0x20`) is set and the digits
can briefly read `0x7F` — every segment lit — which decodes as `8` and yields a
plausible but wrong number. Frames captured mid-write are not readings. Wait for
the edit bit to clear.

## Setting the temperature

Not a socket command. An HTML form POST, and the setter is **absolute**, not
incremental:

```
POST https://accsmartlink.com/spa/<token>/settemp
Content-Type: application/x-www-form-urlencoded

flip-scale=0&void=101&temp=101
```

`flip-scale` is `0` for °F and `1` for °C. `void` is the visible slider and
`temp` the hidden carrier; sending both identical works. Authentication is a
Mojolicious signed session cookie that `GET /app` issues and that expires after
about an hour, so mint it fresh per call rather than storing it.

Limits, from the page's own `templims`:

```js
templims = {f: {min: 45, max: 104}, c: {min: 7.6, max: 40} };
```

`MIN_TEMP_F` is deliberately **65**, not the hardware's 45. A 45 °F setpoint
parks the water on the freeze-protection trigger — the controller shows `CoLd`
below 40 °F and runs the pump until it recovers past 45 °F — fine to touch
briefly, not to hold overnight through a freezing winter.

`GET /app` also exposes the current setpoint as the form's `value` attribute,
which is a cleaner readback than the multiplexed display. Not yet wired up.

## Setting the clock

A JSON frame on the WebSocket:

```json
{"time": "0321P"}
```

`HHMM` in 12-hour form plus `A` or `P`. `0321P` is 3:21 pm; midnight is `1200A`
and noon `1200P`.

Captured from the iOS app, which is a WKWebView (`Origin: null`, an
`AppleWebKit` UA with no `Safari/` token) rather than a native client — so there
is no separate native API, just the same web UI in a wrapper.

There is **no set-time HTTP endpoint** — the web app has only `settemp` and
`settz` — which is why this took a proxy capture to find.

The clock **cannot be read back**, so it is re-asserted on a schedule rather
than checked and corrected. Writing the correct time to an already-correct
clock changes nothing.

## Setting the timezone

```
POST https://accsmartlink.com/spa/<token>/settz
tz=America/Los_Angeles
```

Distinct from the clock: this sets the zone the relay labels the spa with, not
the panel's time-of-day.

## Re-deriving any of this

The web app is the best documentation there is. Open
`https://accsmartlink.com/spa/<token>/app` in a desktop browser, signed in, and
view source — the forms, the LED map, the button codes and `templims` are all
inline. Because that URL is token-gated it cannot be linked from here; you have
to open your own.

For anything the page doesn't expose — the clock frame, for one — proxy the iOS
app with mitmproxy or Charles and read the WebSocket **messages**, not the
handshake. Exporting a WebSocket flow "as cURL" only ever yields the HTTP
upgrade request; the payload lives in frames that have no cURL representation.
A mitmproxy addon that prints only client-to-server frames cuts through the
once-a-second `dsp` telemetry:

```python
from mitmproxy import http

def websocket_message(flow: http.HTTPFlow):
    m = flow.websocket.messages[-1]
    if m.from_client:
        print(f"OUT {m.content!r}", flush=True)
```

Remove the proxy setting and delete the CA certificate afterwards — a trusted
root left installed is a standing risk.

## The WF-100 can drop off the cloud, and nothing says so

The single most useful thing in this document. It happened in September 2026 and
cost two days of cold water.

The WiFi module stops talking to `accsmartlink.com`. The spa is completely fine —
the panel works, the heater works, the water is right there in front of you. But
the relay is still up, so nothing on the Home Assistant side errors:

| | |
| --- | --- |
| WebSocket `/wsb` | connects, then sends only `{"stsR": 0}` |
| `GET /app` | 200, and the control page still renders |
| The rendered page | no temperature, buttons inert |
| `POST /settemp` | 200, setpoint silently discarded |
| Home Assistant log | nothing |

`stsR` is the relay's own link to the spa: `1` healthy, `0` gone. A captured page
from a working day shows `stsR: 1` inline, which is what makes the reading solid.

### The trap: the phone app is not a connectivity test

**The SmartLink app also speaks Bluetooth.** With the panel in range it keeps
working perfectly while the WiFi module is off the cloud — same spa, same app,
totally different transport.

That cost hours of misdiagnosis: "the app works, so the module must be online"
is wrong, and it argued away the correct answer. The app only proves the cloud
path if you are nowhere near the spa.

**The valid test is the web UI** at `/spa/<token>/app`. It rides the same cloud
path as this integration, so it fails exactly when the integration does. A page
that loads with no temperature and dead buttons means the module is off the
cloud — not that the token is bad.

### Recovery

Reset the WF-100: **hold S1, press S2** on the board inside the module. It
reconnects on the same token — no re-pairing, no new URL, nothing to reconfigure
in Home Assistant. A power cycle at the breaker does not necessarily do this.

## Open questions

- Button code `4` is labelled **Filter** here; the web app calls it `system`.
- Whether `/wsb` accepts every frame `/wsa` does. Only the clock frame has been
  checked.
- The setpoint readback from `GET /app` is available but unused; `number.*`
  reports only the last value Home Assistant wrote and shows `unknown` until it
  writes one.
