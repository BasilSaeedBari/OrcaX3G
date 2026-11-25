"""Independent spec-driven X3G / S3G binary decoder and virtual machine simulator.

IMPORTANT INDEPENDENCE RULE (docs/EQUIVALENCE.md Section 2):
This module MUST NOT import ``encoder.py``, ``planner.py``, ``commands.py``, or
``state.py``. Every opcode constant, byte layout, CRC-8-Maxim polynomial division,
and virtual machine axis rule is implemented directly from primary specifications:
- makerbot/s3g@d7991b8b (doc/s3gProtocol.md, tests/pi_test_Crc.py)
- jetty840/Sailfish-MightyBoardFirmware@4d8456e5 (Motherboard/Command.cc, Steppers.cc)
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Any

from .errors import X3GDecodeError
from .machine_profile import MachineProfile, REPLICATOR_2

# Independent wire constants (s3gProtocol.md, Sailfish Packet.hh:20)
_WIRE_START_BYTE = 0xD5
_WIRE_MAX_PAYLOAD = 32


def crc8_maxim_bitwise(data: bytes | bytearray | memoryview) -> int:
    """Independent bit-by-bit CRC-8-Maxim (x^8 + x^5 + x^4 + 1, reflected 0x8C, init 0x00).

    Intentionally implemented without a lookup table so it cross-checks the
    encoder's table-driven implementation.
    """
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x01:
                crc = (crc >> 1) ^ 0x8C
            else:
                crc >>= 1
    return crc & 0xFF


@dataclass(frozen=True)
class DecodedPacket:
    """Raw decoded X3G command record from the binary stream."""

    index: int
    byte_offset: int
    byte_length: int
    opcode: int
    kind: str
    fields: dict[str, Any]


@dataclass(frozen=True)
class DecodedMotionEvent:
    """Physical motion event produced by replaying a motion packet on the X3G VM."""

    packet_index: int
    opcode: int
    x_steps: int
    y_steps: int
    z_steps: int
    a_steps: int
    b_steps: int
    dx_steps: int
    dy_steps: int
    dz_steps: int
    da_steps: int
    x_mm: float
    y_mm: float
    z_mm: float
    x_known: bool
    y_known: bool
    z_known: bool
    e_cum_mm: float
    de_mm: float
    master_steps: int
    dda_rate: int
    distance_mm: float
    feedrate_mult64: int
    feedrate_mm_s: float
    duration_s: float
    relative_mask: int


@dataclass(frozen=True)
class DecodedControlEvent:
    """Non-motion physical event decoded from the X3G stream."""

    packet_index: int
    opcode: int
    kind: str
    data: dict[str, Any]


@dataclass
class X3GSimulationResult:
    """Complete result of decoding and replaying an X3G stream on the virtual machine."""

    packets: list[DecodedPacket] = field(default_factory=list)
    events: list[DecodedMotionEvent | DecodedControlEvent] = field(default_factory=list)
    motion_events: list[DecodedMotionEvent] = field(default_factory=list)
    control_events: list[DecodedControlEvent] = field(default_factory=list)
    final_x_steps: int = 0
    final_y_steps: int = 0
    final_z_steps: int = 0
    final_a_steps: int = 0
    final_e_cum_mm: float = 0.0
    total_time_s: float = 0.0
    build_started: bool = False
    build_ended: bool = False
    build_name: str = ""


def _read_exact(buf: memoryview, offset: int, count: int, context: str) -> memoryview:
    if offset + count > len(buf):
        raise X3GDecodeError(
            f"Truncated X3G stream while reading {context}: need {count} byte(s) at offset {offset}, "
            f"only {len(buf) - offset} remaining.",
            byte_offset=offset,
        )
    return buf[offset : offset + count]


def _read_nul_ascii(buf: memoryview, offset: int, max_len: int, context: str) -> tuple[str, int]:
    """Read a NUL-terminated ASCII string starting at ``offset``."""
    limit = min(len(buf), offset + max_len + 1)
    nul_pos = -1
    for i in range(offset, limit):
        if buf[i] == 0:
            nul_pos = i
            break
    if nul_pos < 0:
        raise X3GDecodeError(
            f"Missing NUL terminator in {context} starting at offset {offset} (max {max_len} chars).",
            byte_offset=offset,
        )
    raw = bytes(buf[offset:nul_pos])
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise X3GDecodeError(
            f"Non-ASCII byte in {context} at offset {offset}: {exc}",
            byte_offset=offset,
        ) from exc
    return text, (nul_pos - offset) + 1


def unframe_s3g_stream(data: bytes | bytearray | memoryview) -> list[tuple[int, bytes]]:
    """Parse a framed serial S3G stream into ``(wire_offset, payload)`` tuples.

    Independently validates start byte ``0xD5``, ``1 <= length <= 32``,
    and bit-by-bit CRC-8-Maxim.
    """
    view = memoryview(data)
    offset = 0
    total = len(view)
    payloads: list[tuple[int, bytes]] = []

    while offset < total:
        pkt_start = offset
        start_byte = view[offset]
        if start_byte != _WIRE_START_BYTE:
            raise X3GDecodeError(
                f"Invalid S3G start byte 0x{start_byte:02X} (expected 0xD5).",
                byte_offset=pkt_start,
            )
        if offset + 2 > total:
            raise X3GDecodeError(
                "Truncated S3G packet header (missing length byte).",
                byte_offset=pkt_start,
            )
        length = view[offset + 1]
        if length == 0 or length > _WIRE_MAX_PAYLOAD:
            raise X3GDecodeError(
                f"Invalid S3G packet payload length {length} (must be 1..{_WIRE_MAX_PAYLOAD}).",
                byte_offset=pkt_start + 1,
            )
        if offset + 2 + length + 1 > total:
            raise X3GDecodeError(
                f"Truncated S3G packet: declared payload length {length}, "
                f"only {total - (offset + 2)} byte(s) remaining.",
                byte_offset=pkt_start,
            )
        payload = bytes(view[offset + 2 : offset + 2 + length])
        crc_actual = view[offset + 2 + length]
        crc_expected = crc8_maxim_bitwise(payload)
        if crc_actual != crc_expected:
            raise X3GDecodeError(
                f"S3G CRC-8 mismatch at packet offset {pkt_start}: "
                f"got 0x{crc_actual:02X}, expected 0x{crc_expected:02X}.",
                byte_offset=offset + 2 + length,
            )
        payloads.append((pkt_start, payload))
        offset += 2 + length + 1

    return payloads


def _decode_one_command(
    view: memoryview, offset: int, index: int, *, report_offset: int | None = None
) -> tuple[DecodedPacket, int]:
    """Decode one command payload starting at ``offset`` in ``view``."""
    base_off = offset if report_offset is None else report_offset
    if offset >= len(view):
        raise X3GDecodeError("Unexpected end of X3G command buffer.", byte_offset=base_off)

    opcode = view[offset]
    pos = offset + 1

    if opcode == 155:  # HOST_CMD_QUEUE_POINT_NEW_EXT (32 bytes total)
        chunk = _read_exact(view, pos, 31, "CMD 155 QUEUE_POINT_NEW_EXT")
        x, y, z, a, b, dda_rate, rel_mask, dist, f_mult64 = struct.unpack("<iiiiiIBfh", chunk)
        if not math.isfinite(dist):
            raise X3GDecodeError(
                f"CMD 155 has non-finite float32 distance ({dist}).",
                byte_offset=base_off,
            )
        pkt = DecodedPacket(
            index=index,
            byte_offset=base_off,
            byte_length=32,
            opcode=opcode,
            kind="queue_point_new_ext",
            fields={
                "x": x,
                "y": y,
                "z": z,
                "a": a,
                "b": b,
                "dda_rate": dda_rate,
                "relative_mask": rel_mask,
                "distance_mm": float(dist),
                "feedrate_mult64": f_mult64,
            },
        )
        return pkt, 32

    if opcode == 129:  # HOST_CMD_QUEUE_POINT_ABS (legacy 17 bytes)
        chunk = _read_exact(view, pos, 16, "CMD 129 QUEUE_POINT_ABS")
        x, y, z, dda = struct.unpack("<iiiI", chunk)
        return (
            DecodedPacket(
                index, base_off, 17, opcode, "queue_point_abs",
                {"x": x, "y": y, "z": z, "dda": dda},
            ),
            17,
        )

    if opcode in (131, 132):  # FIND_AXES_MINIMUM / MAXIMUM (8 bytes)
        chunk = _read_exact(view, pos, 7, f"CMD {opcode} FIND_AXES")
        axes_mask, step_delay_us, timeout_s = struct.unpack("<BIH", chunk)
        direction = "min" if opcode == 131 else "max"
        return (
            DecodedPacket(
                index,
                base_off,
                8,
                opcode,
                "home",
                {
                    "direction": direction,
                    "axes_mask": axes_mask,
                    "step_delay_us": step_delay_us,
                    "timeout_s": timeout_s,
                },
            ),
            8,
        )

    if opcode == 133:  # DELAY (5 bytes)
        chunk = _read_exact(view, pos, 4, "CMD 133 DELAY")
        (delay_ms,) = struct.unpack("<I", chunk)
        return (
            DecodedPacket(index, base_off, 5, opcode, "dwell", {"delay_ms": delay_ms}),
            5,
        )

    if opcode == 134:  # CHANGE_TOOL (2 bytes)
        chunk = _read_exact(view, pos, 1, "CMD 134 CHANGE_TOOL")
        return (
            DecodedPacket(index, base_off, 2, opcode, "change_tool", {"tool_id": chunk[0]}),
            2,
        )

    if opcode in (135, 141):  # WAIT_FOR_TOOL / WAIT_FOR_PLATFORM (6 bytes)
        chunk = _read_exact(view, pos, 5, f"CMD {opcode} WAIT")
        tool_id, ping_ms, timeout_s = struct.unpack("<BHH", chunk)
        kind = "wait_temp" if opcode == 135 else "wait_platform"
        return (
            DecodedPacket(
                index,
                base_off,
                6,
                opcode,
                kind,
                {"tool_id": tool_id, "ping_delay_ms": ping_ms, "timeout_s": timeout_s},
            ),
            6,
        )

    if opcode == 136:  # TOOL_COMMAND (4 + payload_len bytes)
        hdr = _read_exact(view, pos, 3, "CMD 136 TOOL_COMMAND header")
        tool_id, action_id, sub_len = struct.unpack("<BBB", hdr)
        total_len = 4 + sub_len
        if total_len > _WIRE_MAX_PAYLOAD:
            raise X3GDecodeError(
                f"CMD 136 total length {total_len} exceeds MAX_PACKET_PAYLOAD ({_WIRE_MAX_PAYLOAD}).",
                byte_offset=base_off,
            )
        sub_payload = _read_exact(view, pos + 3, sub_len, "CMD 136 TOOL_COMMAND payload")
        fields: dict[str, Any] = {
            "tool_id": tool_id,
            "action_id": action_id,
            "payload_len": sub_len,
            "raw_payload": bytes(sub_payload),
        }
        kind = "tool_command"
        if action_id == 3:  # SLAVE_CMD_SET_TEMP
            if sub_len != 2:
                raise X3GDecodeError(
                    f"SLAVE_CMD_SET_TEMP expected payload_len=2, got {sub_len}.",
                    byte_offset=base_off,
                )
            (temp_c,) = struct.unpack("<h", sub_payload)
            kind = "set_temp"
            fields["temperature_c"] = temp_c
        elif action_id == 13:  # SLAVE_CMD_TOGGLE_VALVE (Replicator 2 part cooling fan)
            if sub_len != 1:
                raise X3GDecodeError(
                    f"SLAVE_CMD_TOGGLE_VALVE expected payload_len=1, got {sub_len}.",
                    byte_offset=base_off,
                )
            kind = "fan"
            fields["enabled"] = bool(sub_payload[0] & 0x01)
            fields["channel"] = "valve_extra"
        elif action_id == 12:  # SLAVE_CMD_TOGGLE_FAN (extruder heatsink fan / GPX M106)
            if sub_len != 1:
                raise X3GDecodeError(
                    f"SLAVE_CMD_TOGGLE_FAN expected payload_len=1, got {sub_len}.",
                    byte_offset=base_off,
                )
            kind = "fan"
            fields["enabled"] = bool(sub_payload[0] & 0x01)
            fields["channel"] = "extruder_fan"
        elif action_id == 31:  # SLAVE_CMD_SET_PLATFORM_TEMP
            if sub_len != 2:
                raise X3GDecodeError(
                    f"SLAVE_CMD_SET_PLATFORM_TEMP expected payload_len=2, got {sub_len}.",
                    byte_offset=base_off,
                )
            (temp_c,) = struct.unpack("<h", sub_payload)
            kind = "set_platform_temp"
            fields["temperature_c"] = temp_c
        return DecodedPacket(index, base_off, total_len, opcode, kind, fields), total_len

    if opcode == 137:  # ENABLE_AXES (2 bytes)
        chunk = _read_exact(view, pos, 1, "CMD 137 ENABLE_AXES")
        bitfield = chunk[0]
        return (
            DecodedPacket(
                index,
                base_off,
                2,
                opcode,
                "enable_axes",
                {"enable": bool(bitfield & 0x80), "axes_mask": bitfield & 0x1F},
            ),
            2,
        )

    if opcode == 139:  # QUEUE_POINT_EXT (25 bytes)
        chunk = _read_exact(view, pos, 24, "CMD 139 QUEUE_POINT_EXT")
        x, y, z, a, b, dda_us = struct.unpack("<iiiiiI", chunk)
        return (
            DecodedPacket(
                index,
                base_off,
                25,
                opcode,
                "queue_point_ext",
                {"x": x, "y": y, "z": z, "a": a, "b": b, "dda_interval_us": dda_us},
            ),
            25,
        )

    if opcode == 140:  # SET_POSITION_EXT (21 bytes)
        chunk = _read_exact(view, pos, 20, "CMD 140 SET_POSITION_EXT")
        x, y, z, a, b = struct.unpack("<iiiii", chunk)
        return (
            DecodedPacket(
                index,
                base_off,
                21,
                opcode,
                "set_position",
                {"x": x, "y": y, "z": z, "a": a, "b": b},
            ),
            21,
        )

    if opcode == 142:  # QUEUE_POINT_NEW (26 bytes)
        chunk = _read_exact(view, pos, 25, "CMD 142 QUEUE_POINT_NEW")
        x, y, z, a, b, duration_us, rel_mask = struct.unpack("<iiiiiIB", chunk)
        return (
            DecodedPacket(
                index,
                base_off,
                26,
                opcode,
                "queue_point_new",
                {
                    "x": x,
                    "y": y,
                    "z": z,
                    "a": a,
                    "b": b,
                    "duration_us": duration_us,
                    "relative_mask": rel_mask,
                },
            ),
            26,
        )

    if opcode == 143:  # STORE_HOME_POSITION (2 bytes)
        chunk = _read_exact(view, pos, 1, "CMD 143 STORE_HOME_POSITION")
        return (
            DecodedPacket(index, base_off, 2, opcode, "store_home", {"axes_mask": chunk[0]}),
            2,
        )

    if opcode == 144:  # RECALL_HOME_POSITION (2 bytes)
        chunk = _read_exact(view, pos, 1, "CMD 144 RECALL_HOME_POSITION")
        return (
            DecodedPacket(index, base_off, 2, opcode, "recall_home", {"axes_mask": chunk[0]}),
            2,
        )

    if opcode == 145:  # SET_POT_VALUE (3 bytes)
        chunk = _read_exact(view, pos, 2, "CMD 145 SET_POT_VALUE")
        return (
            DecodedPacket(
                index, base_off, 3, opcode, "set_pot", {"axis": chunk[0], "value": chunk[1]}
            ),
            3,
        )

    if opcode == 146:  # SET_RGB_LED (6 bytes)
        chunk = _read_exact(view, pos, 5, "CMD 146 SET_RGB_LED")
        r, g, b, blink, eff = struct.unpack("<BBBBB", chunk)
        return (
            DecodedPacket(
                index,
                base_off,
                6,
                opcode,
                "set_rgb_led",
                {"red": r, "green": g, "blue": b, "blink_rate": blink, "effect": eff},
            ),
            6,
        )

    if opcode == 147:  # SET_BEEP (6 bytes)
        chunk = _read_exact(view, pos, 5, "CMD 147 SET_BEEP")
        freq, dur, eff = struct.unpack("<HHB", chunk)
        return (
            DecodedPacket(
                index,
                base_off,
                6,
                opcode,
                "set_beep",
                {"frequency_hz": freq, "duration_ms": dur, "effect": eff},
            ),
            6,
        )

    if opcode == 148:  # PAUSE_FOR_BUTTON (5 bytes)
        chunk = _read_exact(view, pos, 4, "CMD 148 PAUSE_FOR_BUTTON")
        btn_mask, timeout_s, options = struct.unpack("<BHB", chunk)
        return (
            DecodedPacket(
                index,
                base_off,
                5,
                opcode,
                "pause",
                {"button_mask": btn_mask, "timeout_s": timeout_s, "options": options},
            ),
            5,
        )

    if opcode == 149:  # DISPLAY_MESSAGE (5 + strlen + 1 bytes <= 32)
        hdr = _read_exact(view, pos, 4, "CMD 149 DISPLAY_MESSAGE header")
        options, col, row, timeout_s = struct.unpack("<BBBB", hdr)
        msg, consumed = _read_nul_ascii(view, pos + 4, _WIRE_MAX_PAYLOAD - 6, "CMD 149 message")
        total_len = 5 + consumed
        return (
            DecodedPacket(
                index,
                base_off,
                total_len,
                opcode,
                "display_message",
                {
                    "options": options,
                    "x": col,
                    "y": row,
                    "timeout_s": timeout_s,
                    "message": msg,
                },
            ),
            total_len,
        )

    if opcode == 150:  # SET_BUILD_PERCENT (3 bytes)
        chunk = _read_exact(view, pos, 2, "CMD 150 SET_BUILD_PERCENT")
        return (
            DecodedPacket(
                index,
                base_off,
                3,
                opcode,
                "build_percent",
                {"percent": chunk[0], "reserved": chunk[1]},
            ),
            3,
        )

    if opcode == 151:  # QUEUE_SONG (2 bytes)
        chunk = _read_exact(view, pos, 1, "CMD 151 QUEUE_SONG")
        return (
            DecodedPacket(index, base_off, 2, opcode, "queue_song", {"song_id": chunk[0]}),
            2,
        )

    if opcode == 152:  # RESET_TO_FACTORY (1 byte)
        return DecodedPacket(index, base_off, 1, opcode, "reset_to_factory", {}), 1

    if opcode == 153:  # BUILD_START_NOTIFICATION (5 + strlen + 1 bytes <= 32)
        hdr = _read_exact(view, pos, 4, "CMD 153 BUILD_START_NOTIFICATION reserved")
        (reserved,) = struct.unpack("<I", hdr)
        name, consumed = _read_nul_ascii(view, pos + 4, _WIRE_MAX_PAYLOAD - 6, "CMD 153 build_name")
        total_len = 5 + consumed
        return (
            DecodedPacket(
                index,
                base_off,
                total_len,
                opcode,
                "build_start",
                {"reserved": reserved, "build_name": name},
            ),
            total_len,
        )

    if opcode == 154:  # BUILD_END_NOTIFICATION (2 bytes)
        chunk = _read_exact(view, pos, 1, "CMD 154 BUILD_END_NOTIFICATION")
        return (
            DecodedPacket(index, base_off, 2, opcode, "build_end", {"flags": chunk[0]}),
            2,
        )

    if opcode == 156:  # SET_ACCELERATION_TOGGLE (2 bytes)
        chunk = _read_exact(view, pos, 1, "CMD 156 SET_ACCELERATION_TOGGLE")
        return (
            DecodedPacket(
                index, base_off, 2, opcode, "accel_toggle", {"enable": bool(chunk[0])}
            ),
            2,
        )

    if opcode == 157:  # STREAM_VERSION (21 bytes, Sailfish Command.cc:2097-2106)
        chunk = _read_exact(view, pos, 20, "CMD 157 STREAM_VERSION")
        return (
            DecodedPacket(
                index, base_off, 21, opcode, "stream_version", {"raw": bytes(chunk)}
            ),
            21,
        )

    if opcode == 158:  # PAUSE_AT_ZPOS (5 bytes)
        chunk = _read_exact(view, pos, 4, "CMD 158 PAUSE_AT_ZPOS")
        (z_mm,) = struct.unpack("<f", chunk)
        if not math.isfinite(z_mm):
            raise X3GDecodeError(
                f"CMD 158 PAUSE_AT_ZPOS has non-finite float32 z ({z_mm}).",
                byte_offset=base_off,
            )
        return (
            DecodedPacket(index, base_off, 5, opcode, "pause_at_z", {"z_mm": float(z_mm)}),
            5,
        )

    raise X3GDecodeError(
        f"Unknown or unsupported X3G command opcode {opcode} (0x{opcode:02X}).",
        byte_offset=base_off,
    )


def decode_packets(
    data: bytes | bytearray | memoryview, *, framed: bool = False
) -> list[DecodedPacket]:
    """Decode an entire raw ``.x3g`` or framed S3G byte stream into ``DecodedPacket`` records."""
    packets: list[DecodedPacket] = []

    if framed:
        wire_packets = unframe_s3g_stream(data)
        for idx, (wire_off, payload) in enumerate(wire_packets):
            pview = memoryview(payload)
            pkt, consumed = _decode_one_command(pview, 0, idx, report_offset=wire_off)
            if consumed != len(payload):
                raise X3GDecodeError(
                    f"S3G framed packet at offset {wire_off} has trailing bytes: "
                    f"command consumed {consumed} of {len(payload)} payload bytes.",
                    byte_offset=wire_off + 2 + consumed,
                )
            packets.append(pkt)
        return packets

    view = memoryview(data)
    offset = 0
    total = len(view)
    idx = 0
    while offset < total:
        pkt, consumed = _decode_one_command(view, offset, idx)
        if consumed > _WIRE_MAX_PAYLOAD:
            raise X3GDecodeError(
                f"X3G command opcode {pkt.opcode} payload length {consumed} exceeds {_WIRE_MAX_PAYLOAD}.",
                byte_offset=offset,
            )
        packets.append(pkt)
        offset += consumed
        idx += 1

    return packets


def _round_half_away_local(val: float) -> int:
    if val >= 0.0:
        return math.floor(val + 0.5)
    return -math.floor(-val + 0.5)


def simulate_x3g(
    data: bytes | bytearray | memoryview,
    profile: MachineProfile = REPLICATOR_2,
    *,
    framed: bool = False,
) -> X3GSimulationResult:
    """Decode an X3G stream and replay every packet on an independent virtual MightyBoard VM."""
    packets = decode_packets(data, framed=framed)
    result = X3GSimulationResult(packets=packets)

    x_s = 0
    y_s = 0
    z_s = 0
    a_s = 0
    b_s = 0
    x_known = False
    y_known = False
    z_known = False

    # Separate cumulative physical A-axis steps (not corrupted by Command 140 E resets)
    cum_a_steps = 0

    spm_x = profile.x.steps_per_mm
    spm_y = profile.y.steps_per_mm
    spm_z = profile.z.steps_per_mm
    spm_a = profile.extruder_a.steps_per_mm
    ext_sign = -1.0 if profile.extruder_a.inverted_direction else 1.0

    for pkt in packets:
        f = pkt.fields
        if pkt.kind == "queue_point_new_ext":
            rel = int(f["relative_mask"])
            if rel & 0x01:
                dx = int(f["x"])
                new_x = x_s + dx
            else:
                new_x = int(f["x"])
                dx = new_x - x_s
                x_known = True

            if rel & 0x02:
                dy = int(f["y"])
                new_y = y_s + dy
            else:
                new_y = int(f["y"])
                dy = new_y - y_s
                y_known = True

            if rel & 0x04:
                dz = int(f["z"])
                new_z = z_s + dz
            else:
                new_z = int(f["z"])
                dz = new_z - z_s
                z_known = True

            if rel & 0x08:
                da = int(f["a"])
                new_a = a_s + da
            else:
                new_a = int(f["a"])
                da = new_a - a_s

            if rel & 0x10:
                db = int(f["b"])
                new_b = b_s + db
            else:
                new_b = int(f["b"])
                db = new_b - b_s

            x_s, y_s, z_s, a_s, b_s = new_x, new_y, new_z, new_a, new_b
            cum_a_steps += da

            master_steps = max(abs(dx), abs(dy), abs(dz), abs(da), abs(db))
            dda_rate = int(f["dda_rate"])
            duration_s = (master_steps / dda_rate) if (master_steps > 0 and dda_rate > 0) else 0.0
            result.total_time_s += duration_s

            de_mm = (da * ext_sign) / spm_a
            e_cum_mm = (cum_a_steps * ext_sign) / spm_a
            f_mult64 = int(f["feedrate_mult64"])

            ev = DecodedMotionEvent(
                packet_index=pkt.index,
                opcode=pkt.opcode,
                x_steps=x_s,
                y_steps=y_s,
                z_steps=z_s,
                a_steps=a_s,
                b_steps=b_s,
                dx_steps=dx,
                dy_steps=dy,
                dz_steps=dz,
                da_steps=da,
                x_mm=x_s / spm_x,
                y_mm=y_s / spm_y,
                z_mm=z_s / spm_z,
                x_known=x_known,
                y_known=y_known,
                z_known=z_known,
                e_cum_mm=e_cum_mm,
                de_mm=de_mm,
                master_steps=master_steps,
                dda_rate=dda_rate,
                distance_mm=float(f["distance_mm"]),
                feedrate_mult64=f_mult64,
                feedrate_mm_s=f_mult64 / 64.0,
                duration_s=duration_s,
                relative_mask=rel,
            )
            result.motion_events.append(ev)
            result.events.append(ev)

        elif pkt.kind == "queue_point_new":
            rel = int(f["relative_mask"])
            dx = int(f["x"]) if (rel & 0x01) else (int(f["x"]) - x_s)
            dy = int(f["y"]) if (rel & 0x02) else (int(f["y"]) - y_s)
            dz = int(f["z"]) if (rel & 0x04) else (int(f["z"]) - z_s)
            da = int(f["a"]) if (rel & 0x08) else (int(f["a"]) - a_s)
            db = int(f["b"]) if (rel & 0x10) else (int(f["b"]) - b_s)
            x_s += dx
            y_s += dy
            z_s += dz
            a_s += da
            b_s += db
            if not (rel & 0x01):
                x_known = True
            if not (rel & 0x02):
                y_known = True
            if not (rel & 0x04):
                z_known = True
            cum_a_steps += da
            master_steps = max(abs(dx), abs(dy), abs(dz), abs(da), abs(db))
            duration_s = int(f["duration_us"]) / 1_000_000.0
            result.total_time_s += duration_s
            dist_mm = math.sqrt((dx / spm_x) ** 2 + (dy / spm_y) ** 2 + (dz / spm_z) ** 2)
            if dist_mm == 0.0:
                dist_mm = abs(da) / spm_a
            speed = (dist_mm / duration_s) if duration_s > 0 else 0.0
            dda_equiv = _round_half_away_local(master_steps / duration_s) if duration_s > 0 else 0
            ev = DecodedMotionEvent(
                packet_index=pkt.index,
                opcode=pkt.opcode,
                x_steps=x_s,
                y_steps=y_s,
                z_steps=z_s,
                a_steps=a_s,
                b_steps=b_s,
                dx_steps=dx,
                dy_steps=dy,
                dz_steps=dz,
                da_steps=da,
                x_mm=x_s / spm_x,
                y_mm=y_s / spm_y,
                z_mm=z_s / spm_z,
                x_known=x_known,
                y_known=y_known,
                z_known=z_known,
                e_cum_mm=(cum_a_steps * ext_sign) / spm_a,
                de_mm=(da * ext_sign) / spm_a,
                master_steps=master_steps,
                dda_rate=dda_equiv,
                distance_mm=dist_mm,
                feedrate_mult64=_round_half_away_local(speed * 64.0),
                feedrate_mm_s=speed,
                duration_s=duration_s,
                relative_mask=rel,
            )
            result.motion_events.append(ev)
            result.events.append(ev)

        elif pkt.kind == "queue_point_ext":
            # Legacy Command 139: all 5 axes are absolute step coordinates, dda_interval_us per master step
            new_x = int(f["x"])
            new_y = int(f["y"])
            new_z = int(f["z"])
            new_a = int(f["a"])
            new_b = int(f["b"])
            dx = new_x - x_s
            dy = new_y - y_s
            dz = new_z - z_s
            da = new_a - a_s
            db = new_b - b_s
            x_s, y_s, z_s, a_s, b_s = new_x, new_y, new_z, new_a, new_b
            x_known = True
            y_known = True
            z_known = True
            cum_a_steps += da
            master_steps = max(abs(dx), abs(dy), abs(dz), abs(da), abs(db))
            dda_us = int(f["dda_interval_us"])
            duration_s = (master_steps * dda_us) / 1_000_000.0 if (master_steps > 0 and dda_us > 0) else 0.0
            result.total_time_s += duration_s
            dist_mm = math.sqrt((dx / spm_x) ** 2 + (dy / spm_y) ** 2 + (dz / spm_z) ** 2)
            if dist_mm == 0.0:
                dist_mm = abs(da) / spm_a
            speed = (dist_mm / duration_s) if duration_s > 0 else 0.0
            dda_equiv = _round_half_away_local(1_000_000.0 / dda_us) if dda_us > 0 else 0
            ev = DecodedMotionEvent(
                packet_index=pkt.index,
                opcode=pkt.opcode,
                x_steps=x_s,
                y_steps=y_s,
                z_steps=z_s,
                a_steps=a_s,
                b_steps=b_s,
                dx_steps=dx,
                dy_steps=dy,
                dz_steps=dz,
                da_steps=da,
                x_mm=x_s / spm_x,
                y_mm=y_s / spm_y,
                z_mm=z_s / spm_z,
                x_known=x_known,
                y_known=y_known,
                z_known=z_known,
                e_cum_mm=(cum_a_steps * ext_sign) / spm_a,
                de_mm=(da * ext_sign) / spm_a,
                master_steps=master_steps,
                dda_rate=dda_equiv,
                distance_mm=dist_mm,
                feedrate_mult64=_round_half_away_local(speed * 64.0),
                feedrate_mm_s=speed,
                duration_s=duration_s,
                relative_mask=0,
            )
            result.motion_events.append(ev)
            result.events.append(ev)

        elif pkt.kind == "set_position":
            x_s = int(f["x"])
            y_s = int(f["y"])
            z_s = int(f["z"])
            a_s = int(f["a"])
            b_s = int(f["b"])
            x_known = True
            y_known = True
            z_known = True
            ctrl = DecodedControlEvent(
                packet_index=pkt.index,
                opcode=pkt.opcode,
                kind="set_position",
                data={
                    "x_mm": x_s / spm_x,
                    "y_mm": y_s / spm_y,
                    "z_mm": z_s / spm_z,
                    "x_steps": x_s,
                    "y_steps": y_s,
                    "z_steps": z_s,
                    "a_steps": a_s,
                    "b_steps": b_s,
                },
            )
            result.control_events.append(ctrl)
            result.events.append(ctrl)

        elif pkt.kind == "home":
            mask = int(f["axes_mask"])
            if mask & 0x01:
                x_known = False
            if mask & 0x02:
                y_known = False
            if mask & 0x04:
                z_known = False
            ctrl = DecodedControlEvent(pkt.index, pkt.opcode, "home", dict(f))
            result.control_events.append(ctrl)
            result.events.append(ctrl)

        elif pkt.kind == "recall_home":
            mask = int(f["axes_mask"])
            if mask & 0x01:
                x_s = _round_half_away_local(profile.x.home_offset_mm * spm_x)
                x_known = True
            if mask & 0x02:
                y_s = _round_half_away_local(profile.y.home_offset_mm * spm_y)
                y_known = True
            if mask & 0x04:
                z_s = _round_half_away_local(profile.z.home_offset_mm * spm_z)
                z_known = True
            ctrl = DecodedControlEvent(pkt.index, pkt.opcode, "recall_home", dict(f))
            result.control_events.append(ctrl)
            result.events.append(ctrl)

        elif pkt.kind == "dwell":
            result.total_time_s += int(f["delay_ms"]) / 1000.0
            ctrl = DecodedControlEvent(pkt.index, pkt.opcode, "dwell", dict(f))
            result.control_events.append(ctrl)
            result.events.append(ctrl)

        elif pkt.kind == "build_start":
            result.build_started = True
            result.build_name = str(f["build_name"])
            ctrl = DecodedControlEvent(pkt.index, pkt.opcode, "build_start", dict(f))
            result.control_events.append(ctrl)
            result.events.append(ctrl)

        elif pkt.kind == "build_end":
            result.build_ended = True
            ctrl = DecodedControlEvent(pkt.index, pkt.opcode, "build_end", dict(f))
            result.control_events.append(ctrl)
            result.events.append(ctrl)

        else:
            ctrl = DecodedControlEvent(pkt.index, pkt.opcode, pkt.kind, dict(f))
            result.control_events.append(ctrl)
            result.events.append(ctrl)

    result.final_x_steps = x_s
    result.final_y_steps = y_s
    result.final_z_steps = z_s
    result.final_a_steps = cum_a_steps
    result.final_e_cum_mm = (cum_a_steps * ext_sign) / spm_a
    return result
