"""Decoding for the spa's seven-segment display buffer.

The spa reports its topside panel as a ``dsp`` hex string of six bytes. Bytes
0-3 are the four display characters as seven-segment bitmaps; bytes 4 and 5 are
LED bitfields (see ``const``).

The panel can report its display in either orientation, and a flipped panel is
not simply the bytes in reverse: each glyph is also rotated 180 degrees, which
swaps segment a with d, b with e and c with f while leaving g alone. That is why
the spa's own web app accepts the Fahrenheit marker as either ``f1`` in byte 0
or ``ce`` in byte 3 — those are the same character seen from two orientations,
since rotating ``0xce`` yields ``0x71``, the letter F. Likewise ``b9`` and
``8f`` for Celsius.

Getting this wrong is not harmless: under rotation ``6`` and ``9`` map to each
other, so a flipped panel read as though it were upright reports 96 where it
means 69. ``1``, ``3``, ``4`` and ``7`` rotate into shapes that are not digits
at all.

The scale marker sits at whichever end the orientation puts it, and the digits
run from it: ones, tens, then hundreds. So ``f1 6d 6f 00`` is 95°F upright, and
``00 38 5b ce`` is 72°F flipped.

Bit 7 is the decimal point / degree indicator and is masked off before lookup.

A reading needs both a ones and a tens digit. The Fahrenheit range starts at 45
so that is never a limit there; in Celsius it means single-digit readings below
10 are not decoded, which is under the spa's own 7.6 floor anyway.

CAVEAT: this reads the display, and the display is multiplexed THREE ways. At
idle it shows the current water temperature; while the set temperature is being
adjusted it shows that instead (``FLAG_EDIT`` in byte 4 says which); and it also
cycles to the time of day. ``decode_temperature`` returns None for a clock frame
because there is no scale marker on it, and ``decode_clock`` reads those.
"""

from __future__ import annotations

# Seven-segment bitmaps, bit 0 = segment a through bit 6 = segment g.
SEGMENTS_TO_DIGIT = {
    0x3F: 0,
    0x06: 1,
    0x5B: 2,
    0x4F: 3,
    0x66: 4,
    0x6D: 5,
    0x7D: 6,
    0x07: 7,
    0x7F: 8,
    0x6F: 9,
}

SEGMENTS_TO_UNIT = {
    0x71: "F",
    0x39: "C",
}

# Bit 7 carries the decimal point / degree marker, not a segment.
SEGMENT_MASK = 0x7F

# No segments lit — a leading blank.
BLANK = 0x00

# Segment pairs that swap when the panel is upside down.
_ROTATION_PAIRS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20))  # a/d, b/e, c/f
_SEGMENT_G = 0x40


def rotate(segments: int) -> int:
    """Return a seven-segment bitmap as it reads rotated 180 degrees."""
    rotated = segments & _SEGMENT_G
    for low, high in _ROTATION_PAIRS:
        if segments & low:
            rotated |= high
        if segments & high:
            rotated |= low
    return rotated


def plausible(temperature: int, unit: str) -> bool:
    """Return whether a decoded reading could be spa water at all.

    The decoder happily turns any three well-formed glyphs into a number, and
    the panel shows plenty that are not temperatures -- menu items, codes, and
    mid-refresh artifacts where every segment lights. Real readings observed in
    the field include 19, 194, 195, 592, 599 and 992, all published as water
    temperature and all recorded by the history the verification check reads.

    The bounds are deliberately wider than the spa's own 45-104F setpoint range:
    water really can sit below the minimum setpoint, and the controller has its
    own ideas below 40F. This only rejects what cannot be water in a spa.
    """
    if unit == "F":
        return 40 <= temperature <= 115
    return 4 <= temperature <= 46


def decode_temperature(dsp: str) -> tuple[int, str] | None:
    """Return ``(temperature, unit)`` from a ``dsp`` hex string, or None.

    Handles the panel in either orientation. Returns None whenever the buffer
    does not hold a readable temperature — a blank display, a word such as
    ``ECon``, or a frame shorter than the four characters this reads.
    """
    if len(dsp) < 8:
        return None

    try:
        raw = bytes.fromhex(dsp[:8])
    except ValueError:
        return None

    chars = [b & SEGMENT_MASK for b in raw]

    # Upright: the marker is in byte 0 and the digits run up from byte 1.
    if (unit := SEGMENTS_TO_UNIT.get(chars[0])) is not None:
        digits = (raw[1], raw[2], raw[3])
        ones, tens, hundreds = chars[1], chars[2], chars[3]
    # Flipped: the marker is in byte 3, and every glyph reads rotated.
    elif (unit := SEGMENTS_TO_UNIT.get(rotate(chars[3]))) is not None:
        digits = (raw[2], raw[1], raw[0])
        ones, tens, hundreds = (rotate(chars[2]), rotate(chars[1]), rotate(chars[0]))
    else:
        return None

    # Bit 7 on the scale marker is the degree symbol, but on a digit it is a
    # decimal point: Celsius is adjusted in tenths, so the panel shows readings
    # like 38.5. This returns whole degrees, so rather than report that as 385
    # it reports nothing.
    if any(b & ~SEGMENT_MASK for b in digits):
        return None

    if (ones := SEGMENTS_TO_DIGIT.get(ones)) is None:
        return None
    if (tens := SEGMENTS_TO_DIGIT.get(tens)) is None:
        return None

    if hundreds == BLANK:
        hundreds = 0
    elif (hundreds := SEGMENTS_TO_DIGIT.get(hundreds)) is None:
        return None

    temperature = hundreds * 100 + tens * 10 + ones
    if not plausible(temperature, unit):
        return None
    return temperature, unit


def panel_flipped(dsp: str) -> bool | None:
    """Return True if the panel reads upside down, False if upright, else None.

    Determined from the scale marker, so only a temperature frame answers it.
    A clock frame carries no marker and returns None -- which is exactly why
    ``decode_clock`` has to be told the orientation rather than work it out.
    """
    if len(dsp) < 8:
        return None
    try:
        raw = bytes.fromhex(dsp[:8])
    except ValueError:
        return None
    chars = [b & SEGMENT_MASK for b in raw]
    if chars[0] in SEGMENTS_TO_UNIT:
        return False
    if rotate(chars[3]) in SEGMENTS_TO_UNIT:
        return True
    return None


def decode_clock(dsp: str, flipped: bool = False) -> tuple[int, int] | None:
    """Return ``(hour, minute)`` from a clock frame, or None.

    The panel cycles the time of day through the same four characters it uses
    for temperature, using the decimal points between the middle two digits as
    a colon -- which blinks, so half the frames have it dark and it cannot be
    required as a signature. What marks a clock frame instead is the *absence*
    of a scale marker: every temperature frame carries an F or a C.

    Captured from a BT-200 at 10:11 and 10:12 spa-local:

        0686bf06  ->  1 0. 1. 1  = 10:11   (colon lit)
        06063f06  ->  1 0  1  1  = 10:11   (colon dark)
        5b86bf06  ->  1 0. 1. 2  = 10:12

    This is what makes the clock verifiable. Until it was decoded, setting the
    spa's clock was write-only: the integration could record that a frame had
    been delivered but never that the panel agreed, and a drifting clock moves
    the filter cycles out from under the schedule.

    **The display carries no AM/PM indicator**, so this returns a 12-hour time.
    Comparing it against the expected spa-local time modulo twelve hours is
    enough to catch drift, which is the thing worth catching.

    ``flipped`` must come from a temperature frame via ``panel_flipped``; there
    is nothing in a clock frame to derive it from, and guessing would turn a
    correct clock into a false alarm.
    """
    if len(dsp) < 8:
        return None
    try:
        raw = bytes.fromhex(dsp[:8])
    except ValueError:
        return None

    chars = [b & SEGMENT_MASK for b in raw]

    # A scale marker means this is a temperature, not a clock.
    if chars[0] in SEGMENTS_TO_UNIT or rotate(chars[3]) in SEGMENTS_TO_UNIT:
        return None

    # Left to right across the panel. Upright, byte 0 is the rightmost
    # character; flipped, the order reverses and every glyph rotates.
    if flipped:
        shown = [rotate(chars[0]), rotate(chars[1]), rotate(chars[2]), rotate(chars[3])]
    else:
        shown = [chars[3], chars[2], chars[1], chars[0]]

    tens_hour, ones_hour, tens_min, ones_min = shown

    # A single-digit hour blanks the leading character rather than padding it.
    hour = 0 if tens_hour == BLANK else SEGMENTS_TO_DIGIT.get(tens_hour, -1) * 10
    if hour < 0:
        return None
    for glyph in (ones_hour, tens_min, ones_min):
        if glyph not in SEGMENTS_TO_DIGIT:
            return None
    hour += SEGMENTS_TO_DIGIT[ones_hour]
    minute = SEGMENTS_TO_DIGIT[tens_min] * 10 + SEGMENTS_TO_DIGIT[ones_min]

    # The panel runs a 12-hour clock, which rejects most non-clock frames that
    # happen to hold four readable digits.
    if not 1 <= hour <= 12 or minute > 59:
        return None
    return hour, minute
