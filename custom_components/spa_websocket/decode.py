"""Decoding for the spa's seven-segment display buffer.

The spa reports its topside panel as a ``dsp`` hex string of six bytes. Bytes
0-3 are the four display characters as seven-segment bitmaps; bytes 4 and 5 are
LED bitfields (see ``const``).

The digits sit in bytes 1 (ones) and 2 (tens). The scale marker and the hundreds
digit occupy bytes 0 and 3, and which is which depends on the layout the panel
is using — the spa's own web app detects the scale by testing byte 0 for ``f1``
/ ``b9`` or byte 3 for ``ce`` / ``8f``. So ``f1 6d 6f 00`` is 95°F and
``00 6f 7d ce`` is 69°F.

Bit 7 is the decimal point / degree indicator and is masked off before lookup.

CAVEAT: this reads the display, and the display is multiplexed. At idle it shows
the current water temperature, but while the set temperature is being adjusted
it shows that instead. Callers should check the edit LED (``FLAG_EDIT`` in byte
4) to tell the two apart rather than trusting every reading as water
temperature.
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

# Scale marker in byte 0, masked of its degree bit.
SEGMENTS_TO_UNIT = {
    0x71: "F",
    0x39: "C",
}

# Scale marker in byte 3, matched unmasked as the spa's web app does.
ALT_SEGMENTS_TO_UNIT = {
    0xCE: "F",
    0x8F: "C",
}

# Bit 7 carries the decimal point / degree marker, not a segment.
SEGMENT_MASK = 0x7F

# No segments lit — a leading blank.
BLANK = 0x00


def _digit(raw: int) -> int | None:
    """Return the digit a segment bitmap draws, or None."""
    return SEGMENTS_TO_DIGIT.get(raw & SEGMENT_MASK)


def decode_temperature(dsp: str) -> tuple[int, str] | None:
    """Return ``(temperature, unit)`` from a ``dsp`` hex string, or None.

    Returns None whenever the buffer does not hold a readable temperature — a
    blank display, a word such as ``ECon``, or a frame shorter than the four
    characters this reads.
    """
    if len(dsp) < 8:
        return None

    try:
        raw = bytes.fromhex(dsp[:8])
    except ValueError:
        return None

    # The scale marker sits at one end or the other; the opposite end is then
    # the hundreds digit.
    if (unit := SEGMENTS_TO_UNIT.get(raw[0] & SEGMENT_MASK)) is not None:
        leading = raw[3]
    elif (unit := ALT_SEGMENTS_TO_UNIT.get(raw[3])) is not None:
        leading = raw[0]
    else:
        return None

    ones = _digit(raw[1])
    tens = _digit(raw[2])
    if ones is None or tens is None:
        return None

    if leading & SEGMENT_MASK == BLANK:
        hundreds = 0
    elif (hundreds := _digit(leading)) is None:
        return None

    return hundreds * 100 + tens * 10 + ones, unit
