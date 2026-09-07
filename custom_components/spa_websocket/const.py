"""Constants for the Spa WebSocket integration."""

DOMAIN = "spa_websocket"

CONF_URL = "url"

# Command codes sent to the spa over the WebSocket.
CMD_JETS = "3"
CMD_FILTER = "4"

# Jets state numeric codes -> human-readable names.
STATE_OFF = 0
STATE_LOW = 1
STATE_HIGH = 2
STATE_FILTERING = 3

STATE_NAMES = {
    STATE_OFF: "Off",
    STATE_LOW: "Low",
    STATE_HIGH: "High",
    STATE_FILTERING: "Filtering",
}

# Bytes 4 and 5 of the "dsp" string are LED bitfields, not enumerated values.
# Taken from the spa's own web app, which maps each bit to a panel LED.
FLAG_HEATING = 0x01
FLAG_AIR_HI = 0x02
FLAG_JETS_LO = 0x04
FLAG_JETS_HI = 0x08
FLAG_FILTERING = 0x10
FLAG_EDIT = 0x20
FLAG_OVERHEAT = 0x40

# Byte 5.
FLAG_LIGHT = 0x10

# Jets state, most specific first. These are bit tests: the byte carries other
# LEDs at the same time, so heating alongside low jets is 0x05 and an equality
# check against 0x04 would miss it.
DSP_FLAG_TO_STATE = (
    (FLAG_JETS_HI, STATE_HIGH),
    (FLAG_JETS_LO, STATE_LOW),
    (FLAG_FILTERING, STATE_FILTERING),
)

# How long to wait before reconnecting after the socket drops.
RECONNECT_DELAY = 5

# How long without a display frame means the spa is gone. When it is, nothing
# errors: the relay stays reachable, keeps the socket open, and keeps answering
# HTTP with 200 -- it simply has nothing from the spa to forward. Every reading
# goes quietly stale and every command is accepted and dropped.
#
# An hour, which looks absurdly long until you watch the real cadence. Display
# frames do NOT stream steadily. They arrive in bursts when the panel changes
# and stop entirely in between: over six healthy hours on 5 September 2026, with
# the water sitting at 100-104F the whole time, gaps between frames routinely ran
# ten to thirty minutes and the longest was thirty-three. A five-minute threshold
# produced ten false offline alarms in those six hours.
#
# So this is not the fast signal, and it should not pretend to be. KEY_RELAY_STATUS
# is: the relay says outright when it has lost the spa. This is the backstop for a
# link that dies without ever saying so, where an hour of total silence is real.
STALE_AFTER_SECONDS = 3600

# How often to re-evaluate staleness. Availability is time-based, so without a
# tick nothing recomputes it once frames stop -- the very situation it exists to
# detect.
STALENESS_TICK_SECONDS = 60

# The relay reports its own link to the spa in a "stsR" frame. Zero means it has
# no live link, which is the state that produces silent command loss.
KEY_RELAY_STATUS = "stsR"

# How often to reopen the socket while the spa looks offline.
#
# The relay states its link ON CONNECT, promptly and reliably. On a socket that
# is already open it volunteers stsR only sporadically, and that gap is not
# academic: on 6 September 2026 the WF-100 was reset and came back within
# moments, and the relay did not say so on our open socket for another two hours
# and seven minutes. Home Assistant reported a healthy spa as offline that whole
# time, and would have gone on doing it.
#
# So while nothing is reporting, hang up and dial again on this interval. A
# reconnect is the one thing that forces a current answer out of the relay, it
# costs a single handshake, and it only happens when something is already wrong.
RELINK_PROBE_SECONDS = 300

# The session cookie the app page issues lasts about an hour. Fetching that page
# before every single write minted a brand new session roughly twenty-four times
# a day -- a lot of sessions to put through a small third-party relay to
# accomplish nothing. Reuse the cookie until it is close to expiring instead.
SESSION_MAX_AGE_SECONDS = 2700

# How stale an unchanged setpoint may get before it is re-sent. The hourly
# schedule exists so a lost write costs an hour rather than a day, but the value
# is genuinely different only twice a day -- the other twenty-two writes restate
# what the spa already has. Re-asserting on this cadence keeps the recovery
# property while dropping most of the traffic. It also still corrects a setpoint
# changed at the panel, just not within the hour.
SETPOINT_REASSERT_SECONDS = 21600

# WebSocket ping interval. The relay closes idle connections after ~60s, which
# left the socket down for part of every minute and dropped commands sent in
# the gap.
HEARTBEAT = 20

# Diagnostic service for probing the spa's undocumented command codes.
SERVICE_SEND_RAW = "send_raw"
ATTR_CODE = "code"

# Setting the spa's clock. The spa is not necessarily in the same timezone as
# the Home Assistant host -- this one runs three hours behind it -- so the zone
# is a parameter rather than assumed.
SERVICE_SET_TIME = "set_time"
ATTR_TIMEZONE = "timezone"

# Setting the temperature is not a WebSocket command — it is a form POST to a
# sibling path of the socket URL. Visiting the app page first is what issues the
# short-lived session cookie the POST needs.
PATH_APP = "app"
PATH_SETTEMP = "settemp"

# The spa's own web app bounds its slider with
#   templims = {f: {min: 45, max: 104}, c: {min: 7.6, max: 40}}
# The floor here is deliberately higher than the hardware's 45F. A 45F setpoint
# parks the water on the freeze-protection trigger — the controller shows CoLd
# below 40F and runs the pump until it recovers past 45F — which is fine to
# touch briefly but not to hold overnight through a freezing winter. 65F keeps
# the heater off during peak hours with real margin.
MIN_TEMP_F = 65
MAX_TEMP_F = 104
