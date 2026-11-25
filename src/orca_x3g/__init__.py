"""OrcaX3G — Pure-Python G-code to MakerBot Replicator 2 X3G exporter & OrcaSlicer v2.5.2 plugin.

When imported outside OrcaSlicer (CLI, tests, library usage), exposes the pure-Python
conversion API with zero external dependencies. When imported inside OrcaSlicer's embedded
Python runtime (where the ``orca`` module is present), automatically registers the
``@orca.plugin`` package and its three capabilities.
"""

from __future__ import annotations

from .converter import ConversionResult, convert_file, convert_lines, convert_text
from .encoder import crc8_maxim, encode_command, encode_stream, frame_s3g_packet
from .errors import (
    GCodeSyntaxError,
    OrcaX3GError,
    OutOfBoundsError,
    ProfileValidationError,
    UnsupportedCommandError,
    ValueRangeError,
    X3GDecodeError,
)
from .machine_profile import (
    AxisSpec,
    EndstopSide,
    ExtruderSpec,
    MachineProfile,
    PROFILES,
    REPLICATOR_2,
    REPLICATOR_2_FACTORY,
    get_profile,
)
from .planner import ConversionOptions, ExportSummary, GCodePlanner, round_half_away
from .plugin import register_with_orca

__version__ = "0.1.0"

__all__ = [
    "AxisSpec",
    "ConversionOptions",
    "ConversionResult",
    "EndstopSide",
    "ExportSummary",
    "ExtruderSpec",
    "GCodePlanner",
    "GCodeSyntaxError",
    "MachineProfile",
    "OrcaX3GError",
    "OutOfBoundsError",
    "PROFILES",
    "ProfileValidationError",
    "REPLICATOR_2",
    "REPLICATOR_2_FACTORY",
    "UnsupportedCommandError",
    "ValueRangeError",
    "X3GDecodeError",
    "__version__",
    "convert_file",
    "convert_lines",
    "convert_text",
    "crc8_maxim",
    "encode_command",
    "encode_stream",
    "frame_s3g_packet",
    "get_profile",
    "round_half_away",
]

# Register OrcaSlicer plugin capabilities only when loaded by OrcaSlicer's embedded interpreter.
_ORCA_PLUGIN_CLASSES = register_with_orca()
