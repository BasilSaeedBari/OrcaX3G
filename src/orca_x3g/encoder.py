"""Clean-room binary X3G / S3G packet encoder and CRC-8-Maxim implementation.

Wire & file format verified against:
- makerbot/s3g@d7991b8b (doc/s3gProtocol.md, tests/pi_test_Crc.py)
- jetty840/Sailfish-MightyBoardFirmware@4d8456e5 (shared/Packet.cc, Motherboard/Command.cc)

Note:
- ``.x3g`` SD-card files are raw, UNFRAMED concatenations of Host Buffered
  Command payloads (``framed=False``, the default).
- Serial S3G wire packets wrap each payload in
  ``[0xD5][length][payload][crc8_maxim(payload)]`` (``framed=True``).
"""

from __future__ import annotations

import struct
from typing import Iterable

from .commands import (
    BuildEndNotification,
    BuildStartNotification,
    ChangeTool,
    Delay,
    DisplayMessage,
    ENABLE_AXES_FLAG,
    EnableAxes,
    FindAxesMaximum,
    FindAxesMinimum,
    HOST_CMD_BUILD_END_NOTIFICATION,
    HOST_CMD_BUILD_START_NOTIFICATION,
    HOST_CMD_CHANGE_TOOL,
    HOST_CMD_DELAY,
    HOST_CMD_DISPLAY_MESSAGE,
    HOST_CMD_ENABLE_AXES,
    HOST_CMD_FIND_AXES_MAXIMUM,
    HOST_CMD_FIND_AXES_MINIMUM,
    HOST_CMD_PAUSE_AT_ZPOS,
    HOST_CMD_PAUSE_FOR_BUTTON,
    HOST_CMD_QUEUE_POINT_EXT,
    HOST_CMD_QUEUE_POINT_NEW,
    HOST_CMD_QUEUE_POINT_NEW_EXT,
    HOST_CMD_QUEUE_SONG,
    HOST_CMD_RECALL_HOME_POSITION,
    HOST_CMD_SET_ACCELERATION_TOGGLE,
    HOST_CMD_SET_BEEP,
    HOST_CMD_SET_BUILD_PERCENT,
    HOST_CMD_SET_POSITION_EXT,
    HOST_CMD_SET_POT_VALUE,
    HOST_CMD_SET_RGB_LED,
    HOST_CMD_STORE_HOME_POSITION,
    HOST_CMD_TOOL_COMMAND,
    HOST_CMD_WAIT_FOR_PLATFORM,
    HOST_CMD_WAIT_FOR_TOOL,
    PauseAtZPos,
    PauseForButton,
    QueuePointExt,
    QueuePointNew,
    QueuePointNewExt,
    QueueSong,
    RecallHomePosition,
    S3G_MAX_PACKET_PAYLOAD,
    S3G_START_BYTE,
    SLAVE_CMD_SET_PLATFORM_TEMP,
    SLAVE_CMD_SET_TEMP,
    SLAVE_CMD_TOGGLE_FAN,
    SLAVE_CMD_TOGGLE_VALVE,
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
    X3GCommand,
)
from .errors import ValueRangeError

# Precomputed 256-entry lookup table for CRC-8-Maxim / iButton
# Polynomial: x^8 + x^5 + x^4 + 1 (0x31), reflected LSB-first = 0x8C, init = 0x00.
def _build_crc8_maxim_table() -> tuple[int, ...]:
    table: list[int] = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0x8C
            else:
                crc >>= 1
        table.append(crc)
    return tuple(table)


_CRC8_MAXIM_TABLE: tuple[int, ...] = _build_crc8_maxim_table()


def crc8_maxim(payload: bytes | bytearray | memoryview) -> int:
    """Compute the 8-bit Maxim/iButton CRC (reflected poly 0x8C, init 0x00)."""
    table = _CRC8_MAXIM_TABLE
    crc = 0
    for b in payload:
        crc = table[crc ^ b]
    return crc


def frame_s3g_packet(payload: bytes) -> bytes:
    """Wrap a raw S3G/X3G command payload in serial wire framing [0xD5][len][payload][crc]."""
    length = len(payload)
    if length > S3G_MAX_PACKET_PAYLOAD:
        raise ValueRangeError(
            f"S3G packet payload length {length} exceeds MAX_PACKET_PAYLOAD ({S3G_MAX_PACKET_PAYLOAD})."
        )
    return bytes((S3G_START_BYTE, length)) + payload + bytes((crc8_maxim(payload),))


def _encode_ascii_nul(text: str, max_bytes: int, field_name: str) -> bytes:
    raw = text.encode("ascii")
    if b"\x00" in raw:
        raise ValueRangeError(f"{field_name} cannot contain NUL bytes.")
    if len(raw) > max_bytes:
        raise ValueRangeError(f"{field_name} length {len(raw)} exceeds maximum {max_bytes} bytes.")
    return raw + b"\x00"


# Pre-compiled struct packers for hot-path performance
_PACK_155 = struct.Struct("<BiiiiiIBfh")
_PACK_139 = struct.Struct("<BiiiiiI")
_PACK_140 = struct.Struct("<Biiiii")
_PACK_142 = struct.Struct("<BiiiiiIB")
_PACK_HOME = struct.Struct("<BBIH")
_PACK_DELAY = struct.Struct("<BI")
_PACK_WAIT = struct.Struct("<BBHH")
_PACK_TOOL_I16 = struct.Struct("<BBBBh")
_PACK_TOOL_U8 = struct.Struct("<BBBBB")


def encode_command(cmd: X3GCommand) -> bytes:
    """Serialize a single X3G command IR object into its raw little-endian payload bytes."""
    if isinstance(cmd, QueuePointNewExt):
        return _PACK_155.pack(
            HOST_CMD_QUEUE_POINT_NEW_EXT,
            cmd.x,
            cmd.y,
            cmd.z,
            cmd.a,
            cmd.b,
            cmd.dda_rate,
            cmd.relative_axes & 0x7F,
            cmd.distance_mm,
            cmd.feedrate_mult64,
        )
    if isinstance(cmd, ToolSetTemp):
        return _PACK_TOOL_I16.pack(
            HOST_CMD_TOOL_COMMAND, cmd.tool_id, SLAVE_CMD_SET_TEMP, 2, cmd.temperature_c
        )
    if isinstance(cmd, ToolToggleValve):
        return _PACK_TOOL_U8.pack(
            HOST_CMD_TOOL_COMMAND, cmd.tool_id, SLAVE_CMD_TOGGLE_VALVE, 1, 1 if cmd.enabled else 0
        )
    if isinstance(cmd, ToolToggleFan):
        return _PACK_TOOL_U8.pack(
            HOST_CMD_TOOL_COMMAND, cmd.tool_id, SLAVE_CMD_TOGGLE_FAN, 1, 1 if cmd.enabled else 0
        )
    if isinstance(cmd, ToolSetPlatformTemp):
        return _PACK_TOOL_I16.pack(
            HOST_CMD_TOOL_COMMAND, cmd.tool_id, SLAVE_CMD_SET_PLATFORM_TEMP, 2, cmd.temperature_c
        )
    if isinstance(cmd, WaitForTool):
        return _PACK_WAIT.pack(HOST_CMD_WAIT_FOR_TOOL, cmd.tool_id, cmd.ping_delay_ms, cmd.timeout_s)
    if isinstance(cmd, WaitForPlatform):
        return _PACK_WAIT.pack(HOST_CMD_WAIT_FOR_PLATFORM, cmd.tool_id, cmd.ping_delay_ms, cmd.timeout_s)
    if isinstance(cmd, FindAxesMinimum):
        return _PACK_HOME.pack(HOST_CMD_FIND_AXES_MINIMUM, cmd.axes_mask, cmd.step_delay_us, cmd.timeout_s)
    if isinstance(cmd, FindAxesMaximum):
        return _PACK_HOME.pack(HOST_CMD_FIND_AXES_MAXIMUM, cmd.axes_mask, cmd.step_delay_us, cmd.timeout_s)
    if isinstance(cmd, Delay):
        return _PACK_DELAY.pack(HOST_CMD_DELAY, cmd.delay_ms)
    if isinstance(cmd, ChangeTool):
        return struct.pack("<BB", HOST_CMD_CHANGE_TOOL, cmd.tool_id)
    if isinstance(cmd, EnableAxes):
        bits = (cmd.axes_mask & 0x1F) | (ENABLE_AXES_FLAG if cmd.enable else 0)
        return struct.pack("<BB", HOST_CMD_ENABLE_AXES, bits)
    if isinstance(cmd, QueuePointExt):
        return _PACK_139.pack(
            HOST_CMD_QUEUE_POINT_EXT, cmd.x, cmd.y, cmd.z, cmd.a, cmd.b, cmd.dda_interval_us
        )
    if isinstance(cmd, SetPositionExt):
        return _PACK_140.pack(
            HOST_CMD_SET_POSITION_EXT, cmd.x, cmd.y, cmd.z, cmd.a, cmd.b
        )
    if isinstance(cmd, QueuePointNew):
        return _PACK_142.pack(
            HOST_CMD_QUEUE_POINT_NEW,
            cmd.x,
            cmd.y,
            cmd.z,
            cmd.a,
            cmd.b,
            cmd.duration_us,
            cmd.relative_axes & 0x1F,
        )
    if isinstance(cmd, StoreHomePosition):
        return struct.pack("<BB", HOST_CMD_STORE_HOME_POSITION, cmd.axes_mask & 0x1F)
    if isinstance(cmd, RecallHomePosition):
        return struct.pack("<BB", HOST_CMD_RECALL_HOME_POSITION, cmd.axes_mask & 0x1F)
    if isinstance(cmd, SetPotValue):
        return struct.pack("<BBB", HOST_CMD_SET_POT_VALUE, cmd.axis, cmd.value)
    if isinstance(cmd, SetRgbLed):
        return struct.pack(
            "<BBBBBB", HOST_CMD_SET_RGB_LED, cmd.red, cmd.green, cmd.blue, cmd.blink_rate, cmd.effect
        )
    if isinstance(cmd, SetBeep):
        return struct.pack("<BHHB", HOST_CMD_SET_BEEP, cmd.frequency_hz, cmd.duration_ms, cmd.effect)
    if isinstance(cmd, PauseForButton):
        return struct.pack(
            "<BBHB", HOST_CMD_PAUSE_FOR_BUTTON, cmd.button_mask, cmd.timeout_s, cmd.options
        )
    if isinstance(cmd, DisplayMessage):
        # 5 header bytes + text + NUL <= 32 bytes => text <= 26 bytes
        text_bytes = _encode_ascii_nul(cmd.message, S3G_MAX_PACKET_PAYLOAD - 6, "DisplayMessage.message")
        return struct.pack(
            "<BBBBB", HOST_CMD_DISPLAY_MESSAGE, cmd.options, cmd.x, cmd.y, cmd.timeout_s
        ) + text_bytes
    if isinstance(cmd, SetBuildPercent):
        return struct.pack("<BBB", HOST_CMD_SET_BUILD_PERCENT, cmd.percent, cmd.reserved)
    if isinstance(cmd, QueueSong):
        return struct.pack("<BB", HOST_CMD_QUEUE_SONG, cmd.song_id)
    if isinstance(cmd, BuildStartNotification):
        # 1 opcode + 4 reserved + text + NUL <= 32 bytes => text <= 26 bytes
        name_bytes = _encode_ascii_nul(
            cmd.build_name, S3G_MAX_PACKET_PAYLOAD - 6, "BuildStartNotification.build_name"
        )
        return struct.pack("<BI", HOST_CMD_BUILD_START_NOTIFICATION, cmd.reserved) + name_bytes
    if isinstance(cmd, BuildEndNotification):
        return struct.pack("<BB", HOST_CMD_BUILD_END_NOTIFICATION, cmd.flags)
    if isinstance(cmd, SetAccelerationToggle):
        return struct.pack("<BB", HOST_CMD_SET_ACCELERATION_TOGGLE, 1 if cmd.enable else 0)
    if isinstance(cmd, PauseAtZPos):
        return struct.pack("<Bf", HOST_CMD_PAUSE_AT_ZPOS, cmd.z_mm)

    raise TypeError(f"Unsupported X3G command IR type: {type(cmd).__name__}")


def encode_stream(commands: Iterable[X3GCommand], *, framed: bool = False) -> bytes:
    """Encode a sequence of X3G commands into a raw ``.x3g`` byte stream (or S3G framed bytes)."""
    chunks = bytearray()
    if framed:
        for cmd in commands:
            chunks.extend(frame_s3g_packet(encode_command(cmd)))
    else:
        for cmd in commands:
            chunks.extend(encode_command(cmd))
    return bytes(chunks)
