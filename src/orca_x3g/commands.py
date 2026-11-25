"""X3G / S3G protocol constants and immutable command IR dataclasses.

All opcode IDs, bitfield definitions, and payload limits are verified against:
- makerbot/s3g@d7991b8b (doc/s3gProtocol.md)
- jetty840/Sailfish-MightyBoardFirmware@4d8456e5 (shared/Commands.hh, shared/Packet.hh, Command.cc)
- makerbot/MightyBoardFirmware@207c6db1 (shared/Commands.hh, Command.cc)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

# Serial wire framing constants (Packet.hh:22-23, s3gProtocol.md:123)
S3G_START_BYTE: int = 0xD5
S3G_MAX_PACKET_PAYLOAD: int = 32

# Axes bitmask bits (s3gProtocol.md:332-369)
AXIS_BIT_X: int = 1 << 0  # 0x01
AXIS_BIT_Y: int = 1 << 1  # 0x02
AXIS_BIT_Z: int = 1 << 2  # 0x04
AXIS_BIT_A: int = 1 << 3  # 0x08
AXIS_BIT_B: int = 1 << 4  # 0x10
AXIS_BITS_XYZ: int = AXIS_BIT_X | AXIS_BIT_Y | AXIS_BIT_Z
AXIS_BITS_AB: int = AXIS_BIT_A | AXIS_BIT_B
AXIS_BITS_ALL: int = AXIS_BITS_XYZ | AXIS_BITS_AB

# Enable-axes bit 7 (s3gProtocol.md:903)
ENABLE_AXES_FLAG: int = 0x80

# Host Buffered Action Command IDs (Commands.hh:86-118)
HOST_CMD_FIND_AXES_MINIMUM: int = 131
HOST_CMD_FIND_AXES_MAXIMUM: int = 132
HOST_CMD_DELAY: int = 133
HOST_CMD_CHANGE_TOOL: int = 134
HOST_CMD_WAIT_FOR_TOOL: int = 135
HOST_CMD_TOOL_COMMAND: int = 136
HOST_CMD_ENABLE_AXES: int = 137
HOST_CMD_QUEUE_POINT_EXT: int = 139
HOST_CMD_SET_POSITION_EXT: int = 140
HOST_CMD_WAIT_FOR_PLATFORM: int = 141
HOST_CMD_QUEUE_POINT_NEW: int = 142
HOST_CMD_STORE_HOME_POSITION: int = 143
HOST_CMD_RECALL_HOME_POSITION: int = 144
HOST_CMD_SET_POT_VALUE: int = 145
HOST_CMD_SET_RGB_LED: int = 146
HOST_CMD_SET_BEEP: int = 147
HOST_CMD_PAUSE_FOR_BUTTON: int = 148
HOST_CMD_DISPLAY_MESSAGE: int = 149
HOST_CMD_SET_BUILD_PERCENT: int = 150
HOST_CMD_QUEUE_SONG: int = 151
HOST_CMD_RESET_TO_FACTORY: int = 152
HOST_CMD_BUILD_START_NOTIFICATION: int = 153
HOST_CMD_BUILD_END_NOTIFICATION: int = 154
HOST_CMD_QUEUE_POINT_NEW_EXT: int = 155
HOST_CMD_SET_ACCELERATION_TOGGLE: int = 156
HOST_CMD_STREAM_VERSION: int = 157
HOST_CMD_PAUSE_AT_ZPOS: int = 158  # Sailfish ONLY

# Slave / Tool Action Command IDs nested inside HOST_CMD_TOOL_COMMAND (Commands.hh:125-163)
SLAVE_CMD_SET_TEMP: int = 3
SLAVE_CMD_TOGGLE_FAN: int = 12       # Extruder heatsink fan (EXA_FAN)
SLAVE_CMD_TOGGLE_VALVE: int = 13     # Part cooling blower (EX_FAN / EXTRA_FET on R2)
SLAVE_CMD_SET_PLATFORM_TEMP: int = 31


@dataclass(frozen=True)
class FindAxesMinimum:
    axes_mask: int
    step_delay_us: int
    timeout_s: int
    opcode: int = HOST_CMD_FIND_AXES_MINIMUM


@dataclass(frozen=True)
class FindAxesMaximum:
    axes_mask: int
    step_delay_us: int
    timeout_s: int
    opcode: int = HOST_CMD_FIND_AXES_MAXIMUM


@dataclass(frozen=True)
class Delay:
    delay_ms: int
    opcode: int = HOST_CMD_DELAY


@dataclass(frozen=True)
class ChangeTool:
    tool_id: int
    opcode: int = HOST_CMD_CHANGE_TOOL


@dataclass(frozen=True)
class WaitForTool:
    tool_id: int
    ping_delay_ms: int
    timeout_s: int
    opcode: int = HOST_CMD_WAIT_FOR_TOOL


@dataclass(frozen=True)
class ToolSetTemp:
    tool_id: int
    temperature_c: int
    opcode: int = HOST_CMD_TOOL_COMMAND
    action_id: int = SLAVE_CMD_SET_TEMP


@dataclass(frozen=True)
class ToolToggleFan:
    tool_id: int
    enabled: bool
    opcode: int = HOST_CMD_TOOL_COMMAND
    action_id: int = SLAVE_CMD_TOGGLE_FAN


@dataclass(frozen=True)
class ToolToggleValve:
    """Controls EXTRA_FET / EX_FAN (active part cooling blower on Replicator 2)."""

    tool_id: int
    enabled: bool
    opcode: int = HOST_CMD_TOOL_COMMAND
    action_id: int = SLAVE_CMD_TOGGLE_VALVE


@dataclass(frozen=True)
class ToolSetPlatformTemp:
    tool_id: int
    temperature_c: int
    opcode: int = HOST_CMD_TOOL_COMMAND
    action_id: int = SLAVE_CMD_SET_PLATFORM_TEMP


@dataclass(frozen=True)
class EnableAxes:
    axes_mask: int
    enable: bool
    opcode: int = HOST_CMD_ENABLE_AXES


@dataclass(frozen=True)
class QueuePointExt:
    x: int
    y: int
    z: int
    a: int
    b: int
    dda_interval_us: int
    opcode: int = HOST_CMD_QUEUE_POINT_EXT


@dataclass(frozen=True)
class SetPositionExt:
    x: int
    y: int
    z: int
    a: int
    b: int
    opcode: int = HOST_CMD_SET_POSITION_EXT


@dataclass(frozen=True)
class WaitForPlatform:
    tool_id: int
    ping_delay_ms: int
    timeout_s: int
    opcode: int = HOST_CMD_WAIT_FOR_PLATFORM


@dataclass(frozen=True)
class QueuePointNew:
    x: int
    y: int
    z: int
    a: int
    b: int
    duration_us: int
    relative_axes: int
    opcode: int = HOST_CMD_QUEUE_POINT_NEW


@dataclass(frozen=True)
class StoreHomePosition:
    axes_mask: int
    opcode: int = HOST_CMD_STORE_HOME_POSITION


@dataclass(frozen=True)
class RecallHomePosition:
    axes_mask: int
    opcode: int = HOST_CMD_RECALL_HOME_POSITION


@dataclass(frozen=True)
class SetPotValue:
    axis: int
    value: int
    opcode: int = HOST_CMD_SET_POT_VALUE


@dataclass(frozen=True)
class SetRgbLed:
    red: int
    green: int
    blue: int
    blink_rate: int = 0
    effect: int = 0
    opcode: int = HOST_CMD_SET_RGB_LED


@dataclass(frozen=True)
class SetBeep:
    frequency_hz: int
    duration_ms: int
    effect: int = 0
    opcode: int = HOST_CMD_SET_BEEP


@dataclass(frozen=True)
class PauseForButton:
    button_mask: int = 0x01  # CENTER button
    timeout_s: int = 0
    options: int = 0x04      # clear screen on button press
    opcode: int = HOST_CMD_PAUSE_FOR_BUTTON


@dataclass(frozen=True)
class DisplayMessage:
    options: int
    x: int
    y: int
    timeout_s: int
    message: str
    opcode: int = HOST_CMD_DISPLAY_MESSAGE


@dataclass(frozen=True)
class SetBuildPercent:
    percent: int
    reserved: int = 0
    opcode: int = HOST_CMD_SET_BUILD_PERCENT


@dataclass(frozen=True)
class QueueSong:
    song_id: int
    opcode: int = HOST_CMD_QUEUE_SONG


@dataclass(frozen=True)
class BuildStartNotification:
    build_name: str
    reserved: int = 0
    opcode: int = HOST_CMD_BUILD_START_NOTIFICATION


@dataclass(frozen=True)
class BuildEndNotification:
    flags: int = 0
    opcode: int = HOST_CMD_BUILD_END_NOTIFICATION


@dataclass(frozen=True)
class QueuePointNewExt:
    """Command 155 (32 bytes): primary accelerated/unaccelerated motion command."""

    x: int
    y: int
    z: int
    a: int
    b: int
    dda_rate: int
    relative_axes: int
    distance_mm: float
    feedrate_mult64: int
    opcode: int = HOST_CMD_QUEUE_POINT_NEW_EXT


@dataclass(frozen=True)
class SetAccelerationToggle:
    enable: bool
    opcode: int = HOST_CMD_SET_ACCELERATION_TOGGLE


@dataclass(frozen=True)
class PauseAtZPos:
    """Command 158 (5 bytes): Sailfish-only pause at Z height in mm."""

    z_mm: float
    opcode: int = HOST_CMD_PAUSE_AT_ZPOS


X3GCommand = Union[
    FindAxesMinimum,
    FindAxesMaximum,
    Delay,
    ChangeTool,
    WaitForTool,
    ToolSetTemp,
    ToolToggleFan,
    ToolToggleValve,
    ToolSetPlatformTemp,
    EnableAxes,
    QueuePointExt,
    SetPositionExt,
    WaitForPlatform,
    QueuePointNew,
    StoreHomePosition,
    RecallHomePosition,
    SetPotValue,
    SetRgbLed,
    SetBeep,
    PauseForButton,
    DisplayMessage,
    SetBuildPercent,
    QueueSong,
    BuildStartNotification,
    BuildEndNotification,
    QueuePointNewExt,
    SetAccelerationToggle,
    PauseAtZPos,
]
