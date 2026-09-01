"""Decoding for the spa's seven-segment display buffer.

The spa reports its topside panel as a ``dsp`` hex string. Bytes 0-3 are the
four display characters as seven-segment bitmaps, stored right-to-left: byte 0
is the rightmost character (the unit, ``F`` or ``C``), then ones, tens and
hundreds. So ``f1 6d 6f 00`` renders as ``_`` ``9`` ``5`` ``F`` — 95°F, and a
three-digit reading such as 101°F fills the fourth character.

The panel is four wide, not three: it also shows words, e.g. ``54 5c 39 79``
renders as ``ECon``.

Bit 7 is the decimal point / degree indicator and is masked off before lookup.
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

# Unit characters in the rightmost position.
SEGMENTS_TO_UNIT = {
    0x71: "F",
    0x39: "C",
}

# Bit 7 carries the decimal point / degree marker, not a segment.
SEGMENT_MASK = 0x7F

# No segments lit — the leading character of a two-digit reading.
BLANK = 0x00


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

    unit = SEGMENTS_TO_UNIT.get(raw[0] & SEGMENT_MASK)
    ones = SEGMENTS_TO_DIGIT.get(raw[1] & SEGMENT_MASK)
    tens = SEGMENTS_TO_DIGIT.get(raw[2] & SEGMENT_MASK)

    if unit is None or ones is None or tens is None:
        return None

    # The leading character is blank for a two-digit reading and carries the
    # hundreds digit for three-digit ones such as 101F.
    leading = raw[3] & SEGMENT_MASK
    if leading == BLANK:
        hundreds = 0
    else:
        hundreds = SEGMENTS_TO_DIGIT.get(leading)
        if hundreds is None:
            return None

    return hundreds * 100 + tens * 10 + ones, unit
