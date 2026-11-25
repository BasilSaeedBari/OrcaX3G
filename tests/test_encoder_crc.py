"""Unit tests for CRC-8-Maxim, S3G wire framing, and every X3G binary command encoder.

CRC-8-Maxim test vectors sourced directly from makerbot/s3g@d7991b8b
(tests/pi_test_Crc.py) — recorded in docs/SOURCES.md.
"""

from __future__ import annotations

import struct

import pytest

from orca_x3g.commands import (
    BuildEndNotification,
    BuildStartNotification,
    ChangeTool,
    Delay,
    DisplayMessage,
    EnableAxes,
    FindAxesMaximum,
    FindAxesMinimum,
    PauseAtZPos,
    PauseForButton,
    QueuePointExt,
    QueuePointNew,
    QueuePointNewExt,
    QueueSong,
    RecallHomePosition,
    SetAccelerationToggle,
    SetBeep,
    SetBuildPercent,
    SetPositionExt,
    SetPotValue,
    SetRgbLed,
    StoreHomePosition,
    ToolSetPlatformTemp,
    ToolSetTemp,
    ToolToggleFan,
    ToolToggleValve,
    WaitForPlatform,
    WaitForTool,
)
from orca_x3g.decoder import crc8_maxim_bitwise, decode_packets, unframe_s3g_stream
from orca_x3g.encoder import crc8_maxim, encode_command, encode_stream, frame_s3g_packet
from orca_x3g.errors import ValueRangeError, X3GDecodeError


class TestCRC8Maxim:
    def test_official_s3g_vectors(self) -> None:
        # Primary source: makerbot/s3g/tests/pi_test_Crc.py
        vectors = [
            (b"", 0x00),
            (b"abcdefghijk", 0xB4),
            (bytes(range(16)), 0x3C),
        ]
        for payload, expected in vectors:
            assert crc8_maxim(payload) == expected
            assert crc8_maxim_bitwise(payload) == expected

    def test_table_matches_bitwise_for_all_single_bytes_and_sequences(self) -> None:
        for b in range(256):
            data = bytes([b])
            assert crc8_maxim(data) == crc8_maxim_bitwise(data)
        sample = bytes((i * 37 + 13) & 0xFF for i in range(64))
        assert crc8_maxim(sample) == crc8_maxim_bitwise(sample)


class TestS3GFraming:
    def test_frame_and_unframe_roundtrip(self) -> None:
        payload = encode_command(ToolSetTemp(tool_id=0, temperature_c=210))
        framed = frame_s3g_packet(payload)
        assert framed[0] == 0xD5
        assert framed[1] == len(payload) == 6
        assert framed[2:-1] == payload
        assert framed[-1] == crc8_maxim_bitwise(payload)

        unframed = unframe_s3g_stream(framed)
        assert len(unframed) == 1
        assert unframed[0] == (0, payload)

    def test_frame_rejects_payload_over_32_bytes(self) -> None:
        with pytest.raises(ValueRangeError):
            frame_s3g_packet(b"\x00" * 33)

    def test_unframe_rejects_bad_start_byte(self) -> None:
        framed = bytearray(frame_s3g_packet(b"\x9a\x00"))
        framed[0] = 0xD4
        with pytest.raises(X3GDecodeError, match="start byte"):
            unframe_s3g_stream(framed)

    def test_unframe_rejects_bad_crc(self) -> None:
        framed = bytearray(frame_s3g_packet(b"\x9a\x00"))
        framed[-1] ^= 0x01
        with pytest.raises(X3GDecodeError, match="CRC-8 mismatch"):
            unframe_s3g_stream(framed)

    def test_unframe_rejects_truncated_packet(self) -> None:
        framed = frame_s3g_packet(b"\x9a\x00")
        with pytest.raises(X3GDecodeError, match="Truncated"):
            unframe_s3g_stream(framed[:-1])

    def test_unframe_rejects_zero_and_oversize_length(self) -> None:
        with pytest.raises(X3GDecodeError, match="length"):
            unframe_s3g_stream(b"\xd5\x00\x00")
        with pytest.raises(X3GDecodeError, match="length"):
            unframe_s3g_stream(b"\xd5\x21" + b"\x00" * 34)


class TestCommandEncodersHandComputed:
    def test_cmd_155_queue_point_new_ext(self) -> None:
        cmd = QueuePointNewExt(
            x=889,
            y=-444,
            z=80,
            a=-96,
            b=0,
            dda_rate=2667,
            relative_axes=0x18,
            distance_mm=10.0,
            feedrate_mult64=1920,
        )
        raw = encode_command(cmd)
        expected = struct.pack("<BiiiiiIBfh", 155, 889, -444, 80, -96, 0, 2667, 0x18, 10.0, 1920)
        assert raw == expected
        assert len(raw) == 32

        pkts = decode_packets(raw)
        assert len(pkts) == 1
        assert pkts[0].opcode == 155
        assert pkts[0].fields["x"] == 889
        assert pkts[0].fields["y"] == -444
        assert pkts[0].fields["z"] == 80
        assert pkts[0].fields["a"] == -96
        assert pkts[0].fields["dda_rate"] == 2667
        assert pkts[0].fields["relative_mask"] == 0x18
        assert pkts[0].fields["feedrate_mult64"] == 1920

    def test_all_commands_byte_exact_and_decodable(self) -> None:
        cases = [
            (FindAxesMinimum(axes_mask=0x04, step_delay_us=136, timeout_s=20), struct.pack("<BBIH", 131, 4, 136, 20)),
            (FindAxesMaximum(axes_mask=0x03, step_delay_us=270, timeout_s=20), struct.pack("<BBIH", 132, 3, 270, 20)),
            (Delay(delay_ms=1500), struct.pack("<BI", 133, 1500)),
            (ChangeTool(tool_id=0), struct.pack("<BB", 134, 0)),
            (WaitForTool(tool_id=0, ping_delay_ms=100, timeout_s=600), struct.pack("<BBHH", 135, 0, 100, 600)),
            (ToolSetTemp(tool_id=0, temperature_c=215), struct.pack("<BBBBh", 136, 0, 3, 2, 215)),
            (ToolToggleFan(tool_id=0, enabled=True), struct.pack("<BBBBB", 136, 0, 12, 1, 1)),
            (ToolToggleValve(tool_id=0, enabled=True), struct.pack("<BBBBB", 136, 0, 13, 1, 1)),
            (ToolToggleValve(tool_id=0, enabled=False), struct.pack("<BBBBB", 136, 0, 13, 1, 0)),
            (ToolSetPlatformTemp(tool_id=0, temperature_c=0), struct.pack("<BBBBh", 136, 0, 31, 2, 0)),
            (EnableAxes(axes_mask=0x1F, enable=True), struct.pack("<BB", 137, 0x9F)),
            (EnableAxes(axes_mask=0x1F, enable=False), struct.pack("<BB", 137, 0x1F)),
            (QueuePointExt(10, 20, 30, -40, 0, 500), struct.pack("<BiiiiiI", 139, 10, 20, 30, -40, 0, 500)),
            (SetPositionExt(0, 0, -2000, 0, 0), struct.pack("<Biiiii", 140, 0, 0, -2000, 0, 0)),
            (WaitForPlatform(tool_id=0, ping_delay_ms=100, timeout_s=600), struct.pack("<BBHH", 141, 0, 100, 600)),
            (QueuePointNew(1, 2, 3, -4, 0, 10000, 0x18), struct.pack("<BiiiiiIB", 142, 1, 2, 3, -4, 0, 10000, 0x18)),
            (StoreHomePosition(axes_mask=0x07), struct.pack("<BB", 143, 0x07)),
            (RecallHomePosition(axes_mask=0x1F), struct.pack("<BB", 144, 0x1F)),
            (SetPotValue(axis=0, value=118), struct.pack("<BBB", 145, 0, 118)),
            (SetRgbLed(255, 128, 0, 0, 0), struct.pack("<BBBBBB", 146, 255, 128, 0, 0, 0)),
            (SetBeep(440, 250, 0), struct.pack("<BHHB", 147, 440, 250, 0)),
            (PauseForButton(button_mask=0x01, timeout_s=0, options=0x04), struct.pack("<BBHB", 148, 1, 0, 4)),
            (DisplayMessage(options=0, x=0, y=0, timeout_s=5, message="Hello R2"), struct.pack("<BBBBB", 149, 0, 0, 0, 5) + b"Hello R2\x00"),
            (SetBuildPercent(percent=42, reserved=0), struct.pack("<BBB", 150, 42, 0)),
            (QueueSong(song_id=1), struct.pack("<BB", 151, 1)),
            (BuildStartNotification(build_name="CubeTest", reserved=0), struct.pack("<BI", 153, 0) + b"CubeTest\x00"),
            (BuildEndNotification(flags=0), struct.pack("<BB", 154, 0)),
            (SetAccelerationToggle(enable=True), struct.pack("<BB", 156, 1)),
            (PauseAtZPos(z_mm=12.5), struct.pack("<Bf", 158, 12.5)),
        ]

        all_cmds = [c for c, _ in cases]
        for cmd, expected in cases:
            encoded = encode_command(cmd)
            assert encoded == expected
            assert len(encoded) <= 32

        raw_stream = encode_stream(all_cmds, framed=False)
        framed_stream = encode_stream(all_cmds, framed=True)
        raw_pkts = decode_packets(raw_stream, framed=False)
        framed_pkts = decode_packets(framed_stream, framed=True)
        assert len(raw_pkts) == len(cases)
        assert len(framed_pkts) == len(cases)
        for rp, fp in zip(raw_pkts, framed_pkts):
            assert rp.opcode == fp.opcode
            assert rp.fields == fp.fields
