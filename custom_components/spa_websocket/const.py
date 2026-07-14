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

# The 5th byte of the "dsp" hex string (chars [8:10]) encodes the jets state.
DSP_BYTE_TO_STATE = {
    "04": STATE_LOW,
    "08": STATE_HIGH,
    "10": STATE_FILTERING,
}

# How long to wait before reconnecting after the socket drops.
RECONNECT_DELAY = 5
