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

# The relay reports its own link to the spa in a "stsR" frame. Zero means it has
# no live link, which is the state that produces silent command loss -- every
# command accepted, acknowledged, and dropped. It volunteers this on connect,
# promptly and reliably; on a socket left open it goes quiet for hours, which is
# the whole reason this integration no longer keeps one open.
KEY_RELAY_STATUS = "stsR"

# How long to listen on a visit before hanging up.
#
# Best effort, never a verdict. Display frames arrive in bursts and go quiet in
# between -- ten to thirty minute gaps are normal on a healthy spa, the longest
# measured thirty-three -- so a visit can easily end having heard nothing. That
# is why no confirmation depends on hearing a frame: the setpoint is confirmed
# over HTTP, and a frame caught here is only a bonus reading.
VISIT_SECONDS = 60

# The same, for a reading the user asked for by pressing a button. Shorter,
# because a button that appears to hang for a minute is worse than one that
# comes back honestly empty.
REFRESH_SECONDS = 30

# How long to let the relay volunteer its link state after connecting. It states
# it on connect, promptly -- it is on an already-open socket that it goes quiet,
# which is the whole reason this integration no longer keeps one.
RELAY_ANSWER_SECONDS = 10

# Confirming a setpoint: how many times to re-read the spa's own page, and how
# long to wait between tries. A fresh page load rather than the POST's echo,
# because whether that echo carries the new value or the old one was never
# established against the hardware.
CONFIRM_ATTEMPTS = 3
CONFIRM_DELAY_SECONDS = 5

# The scheduled jobs, named so a failure can say which one.
JOB_SETPOINT = "setpoint"
JOB_CLOCK = "clock"

# WebSocket ping interval. The relay closes idle connections after ~60s, which
# matters even for short visits: a quiet listen would otherwise be hung up on
# from the far end partway through.
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
