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

    return hundreds * 100 + tens * 10 + ones, unit
