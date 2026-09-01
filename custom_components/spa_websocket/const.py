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

# WebSocket ping interval. The relay closes idle connections after ~60s, which
# left the socket down for part of every minute and dropped commands sent in
# the gap.
HEARTBEAT = 20

# Diagnostic service for probing the spa's undocumented command codes.
SERVICE_SEND_RAW = "send_raw"
ATTR_CODE = "code"

# Setting the temperature is not a WebSocket command — it is a form POST to a
# sibling path of the socket URL. Visiting the app page first is what issues the
# short-lived session cookie the POST needs.
PATH_APP = "app"
PATH_SETTEMP = "settemp"

# The limits the spa's own web app bounds its slider with:
#   templims = {f: {min: 45, max: 104}, c: {min: 7.6, max: 40}}
MIN_TEMP_F = 45
MAX_TEMP_F = 104
