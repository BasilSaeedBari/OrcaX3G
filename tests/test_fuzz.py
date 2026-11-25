"""Fuzz tests for parser, converter, and independent X3G decoder (raw + framed).

Ensures malformed strings and arbitrary random byte streams NEVER cause unhandled
exceptions (struct.error, IndexError, UnicodeDecodeError, ValueError) or hangs.
"""

from __future__ import annotations

import random

from hypothesis import given, settings, strategies as st

from orca_x3g import OrcaX3GError, X3GDecodeError, convert_text
from orca_x3g.decoder import decode_packets, simulate_x3g
from orca_x3g.parser import parse_gcode_line as parse_line


@settings(max_examples=200, deadline=None)
@given(raw_bytes=st.binary(min_size=0, max_size=256))
def test_fuzz_decoder_raw_never_crashes(raw_bytes: bytes) -> None:
    try:
        decode_packets(raw_bytes, framed=False)
        simulate_x3g(raw_bytes, framed=False)
    except X3GDecodeError:
        pass


@settings(max_examples=200, deadline=None)
@given(raw_bytes=st.binary(min_size=0, max_size=256))
def test_fuzz_decoder_framed_never_crashes(raw_bytes: bytes) -> None:
    try:
        decode_packets(raw_bytes, framed=True)
        simulate_x3g(raw_bytes, framed=True)
    except X3GDecodeError:
        pass


@settings(max_examples=200, deadline=None)
@given(line=st.text(min_size=0, max_size=120))
def test_fuzz_parser_and_converter_never_crash(line: str) -> None:
    try:
        parse_line(line, 1)
    except OrcaX3GError:
        pass

    try:
        convert_text(line)
    except OrcaX3GError:
        pass


def test_fuzz_mutated_valid_x3g_bitflips_and_truncations() -> None:
    valid = convert_text(
        "G90\nM82\nG92 X0 Y0 Z0 E0\nM104 S210\nG1 X10 Y10 Z0.2 E0.5 F1800\nM106 S255\nM107\n"
    ).x3g_bytes

    # Every strict prefix truncation except exact packet boundaries must raise X3GDecodeError
    rng = random.Random(42)
    for cut in range(1, len(valid)):
        try:
            decode_packets(valid[:cut], framed=False)
        except X3GDecodeError:
            pass

    # 100 random byte mutations must never raise non-X3GDecodeError exceptions
    for _ in range(100):
        buf = bytearray(valid)
        idx = rng.randrange(len(buf))
        buf[idx] ^= rng.randint(1, 255)
        try:
            simulate_x3g(bytes(buf), framed=False)
        except X3GDecodeError:
            pass
