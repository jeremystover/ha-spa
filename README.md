# Spa WebSocket — Home Assistant integration

Controls an **ACC (Applied Computer Controls) SmarTouch** spa through a
**WF-100 SmartLink** WiFi module, relayed via `accsmartlink.com`.

Originally a port of the `homebridge-spa-jets` Homebridge plugin (two buttons
and a jets sensor). It has since grown a decoded temperature readout, an
absolute setpoint control, a heater sensor, and a clock setter — see
[`docs/PROTOCOL.md`](docs/PROTOCOL.md) for how each was worked out.

## What it creates

One shared, auto-reconnecting WebSocket, plus a short-lived HTTPS session for
the one thing that isn't a socket command.

| Entity | Type | Behavior |
| --- | --- | --- |
| **Jets** | `button` | Sends `"3"` |
| **Filter** | `button` | Sends `"4"` — the web app calls this code `system` |
| **Jets status** | `sensor` (enum) | `Off` / `Low` / `High` / `Filtering` |
| **Temperature** | `sensor` | Water temperature, decoded from the panel display |
| **Target temperature** | `number` | Setpoint, 65–104 °F |
| **Heating** | `binary_sensor` | Whether the heater element is firing |

### Services

| Service | Fields | Purpose |
| --- | --- | --- |
| `spa_websocket.set_time` | `timezone` (optional IANA name) | Sets the panel clock |
| `spa_websocket.send_raw` | `code` | Diagnostic — sends one raw frame |

`set_time` defaults to Home Assistant's own timezone, which is correct only if
the spa sits in the same zone as the HA host. Pass the zone explicitly if not.

## Install

**Via HACS:** ⋮ → Custom repositories → add
`https://github.com/jeremystover/ha-spa` as an **Integration** → download →
restart → Settings → Devices & services → Add integration → Spa WebSocket.

Configuration takes one value: the spa's WebSocket URL, of the form
`wss://accsmartlink.com/spa/<token>/wsb`.

> **The token in that URL is a credential.** Anyone holding it can control the
> spa. Don't paste it into issues, PRs, logs, or screenshots.

HACS tracks this repo by commit, not release. If an update doesn't appear,
use ⋮ → **Update information** on the repository first.

## Home Assistant configuration

The integration exposes capability; the schedule lives in Home Assistant. This
is the setup it was built for — a spa on **America/Los_Angeles** whose Home
Assistant runs on **America/Toronto**, three hours ahead. All times below are
Home Assistant local.

### How this spa actually heats

**The heater runs on demand, whenever the water is below setpoint** — it does
*not* require a filter cycle. Observed directly: at 18:57 spa-local, outside
both FP1 and FP2, a frame read flags `0x05` (heating + low pump) with the
filtering bit clear, moments after the setpoint was raised above the water
temperature.

This corrects an earlier assumption, and it matters for cost rather than for
correctness. Because the setpoint is live around the clock, **the floor value is
a continuous running cost**: an 85 °F floor means the spa holds 85 °F all day,
heating on demand, including through the whole 3pm–3am peak window. A low floor
is the only thing keeping the heater off during peak.

The clock still matters — the filter cycles run against it, and it has no
battery, so it comes back wrong after a power cut — but heating no longer
depends on it. Hence the clock sync below is about filtration, not heat.

Panel-side programming assumed here: **FP1 12:00–15:00** and **FP2 23:00–23:45**,
both spa-local.

### Helpers

| Helper | Type | Purpose |
| --- | --- | --- |
| `input_boolean.hot_tub_schedule_hold` | Toggle | Manual override — suspends the schedule |
| `sensor.<spa>_heat_window` | History stats | Hours the heater ran during the heat window |

The history-stats helper watches the `Heating` binary sensor, state `on`, type
`Time`, from `{{ today_at('15:00') }}` to `{{ today_at('18:00') }}`.

### Automations

| Automation | Trigger | Action |
| --- | --- | --- |
| Hot tub temperature schedule | Hourly at :00 | 103 °F within 15:00–18:00, else an 85 °F floor |
| Hot tub clock sync | Hourly at :30 | `set_time` with `America/Los_Angeles` |
| Hot tub schedule hold — daily clear | 06:00 | Turns the hold off |
| Hot tub heat window verification | 18:00 | Notifies if the heater never ran |

### What happens each day

| HA time | Spa time | What |
| --- | --- | --- |
| every :30 | — | Clock re-asserted, so drift costs at most an hour |
| 06:00 | 03:00 | Peak ends; any manual hold is released |
| 15:00 | 12:00 | Setpoint goes to 103 °F; FP1 also starts |
| 15:00–18:00 | 12:00–15:00 | The push to temperature, entirely off-peak |
| 18:00 | 15:00 | Peak begins; setpoint drops to the 85 °F floor |
| 18:00 | 15:00 | Verification: alert if the heater never fired |
| 18:00–06:00 | 15:00–03:00 | Peak. The spa still holds 85 °F on demand — this is the floor's cost |
| 02:00 | 23:00 | FP2 filters; heat here depends on the floor, not on FP2 |

### Design notes

**Hourly, not once.** Every enforcement re-asserts rather than firing on a
single edge, so a missed run — spa offline, expired relay session, HA restart —
costs one hour instead of a whole day.

**The clock is written, never checked.** It can't be read back: the display
multiplexes to water temperature at idle. Writing the correct time to an
already-correct clock changes nothing, which makes blind re-assertion safe and
removes the need for a verify-then-fix path that the hardware can't support.

**Verification is by consequence.** Nothing raises an error when heating fails:
the schedule reports success as long as the POST returns 200, which says nothing
about whether the water moved. The heat window sensor measures heater runtime
instead — zero runtime across a whole window, with the water below target, means
the spa isn't acting on the setpoint at all. It uses `history_stats` rather than
a `for:` duration because `for:` clocks reset on every restart and every
`unavailable` blip.

**The hold expires.** An open-ended override is a rental hazard — a guest flips
it, leaves, and the tub heats through peak indefinitely. It clears at the end of
peak, so it covers one night at most.

## The floor is the cost knob

A spa heater moves water roughly 5 °F/hour, so a three-hour window lifts it
about 15 °F. That sets how far apart the floor and the target can usefully be:
from an 85 °F floor, three hours reaches roughly 100 °F against a 103 °F target,
which is close enough that the last stretch is deadband. From a 65 °F floor the
same window lands in the low 80s.

The catch is that the floor is held **continuously**, on demand, peak rates
included. So the two settings trade directly against each other:

- **Higher floor** — the tub is usable at any hour and the afternoon window
  reaches temperature, paid for with twelve hours a day of peak-rate maintenance.
- **Lower floor** — no peak heating at all, but the tub is cold outside the
  afternoon window and the window can't close the gap.

A third shape is available if the bill matters more than round-the-clock
readiness: hold a warm floor only during off-peak (06:00–18:00 HA) and drop to a
cold one for peak (18:00–06:00 HA). That heats on cheap hours only, and the tub
coasts down overnight from the afternoon peak instead of being held warm.

**Starting FP1 earlier is no longer a lever.** It would have been when heating
was thought to require a filter cycle; since the heater runs on demand, the
filter schedule doesn't gate heat.

The verification automation will tell you whether heat is happening at all; it
deliberately does not alarm on merely falling short of target.

## Reference

- **ACC SmarTouch manuals** — [manufacturer PDF](http://acc-spas.com/site/wp-content/uploads/2016/04/smartouch-manual-acc.pdf),
  [mirror](https://www.spaspecialist.com/ACCmanual-acc.pdf),
  [ManualsLib](https://www.manualslib.com/manual/1578288/Acc-Smartouch-Digital-Series.html).
  Covers panel programming, filter cycles, and error codes. These links are the
  manufacturer's published locations; they were not fetchable from the
  environment this was written in, so check them against your own hardware.
- **[`docs/PROTOCOL.md`](docs/PROTOCOL.md)** — the wire protocol: display buffer
  decoding, LED bitfields, the setpoint form POST, and the clock frame, with
  notes on how each was derived and how to re-derive it.

## License

MIT
