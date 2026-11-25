"""Motion planner, zero-drift integer-step quantizer, and G-code -> X3G IR translator.

Design invariants (see docs/DESIGN.md):
1. XYZ positions are quantized from absolute mm coordinates via deterministic
   half-away-from-zero rounding so cumulative position error never exceeds
   0.5 / steps_per_mm across arbitrarily long prints.
2. Extruder positions track ``e_logical_mm`` (reset by G92 E) separately from
   cumulative ``e_physical_mm`` and ``e_emitted_steps``, emitting relative
   negative A-axis step deltas (bits 3-4 set in Command 155) so cumulative
   extrusion drift never exceeds 0.5 / a_steps_per_mm even across thousands of
   G92 E0 resets and M82/M83 switches.
3. Unsupported commands, heated-bed requests (S > 0), secondary tools (T1+),
   auxiliary fans (M106 P>0), arcs (G2/G3), and out-of-bounds moves are
   rejected loudly with exact source line numbers.
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .commands import (
    AXIS_BIT_A,
    AXIS_BIT_B,
    AXIS_BIT_X,
    AXIS_BIT_Y,
    AXIS_BIT_Z,
    AXIS_BITS_AB,
    AXIS_BITS_ALL,
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
    QueuePointNewExt,
    QueueSong,
    RecallHomePosition,
    SetAccelerationToggle,
    SetBuildPercent,
    SetPositionExt,
    SetPotValue,
    StoreHomePosition,
    ToolSetTemp,
    ToolToggleValve,
    WaitForTool,
    X3GCommand,
)
from .errors import (
    GCodeSyntaxError,
    OutOfBoundsError,
    UnsupportedCommandError,
    ValueRangeError,
)
from .machine_profile import EndstopSide, MachineProfile, REPLICATOR_2
from .parser import ParsedGCodeLine, iter_parsed_lines
from .state import PrinterState

INT32_MIN = -(1 << 31)
INT32_MAX = (1 << 31) - 1
UINT32_MAX = (1 << 32) - 1
BOUNDS_EPSILON_MM = 1e-3

_LAYER_COMMENT_RE = re.compile(r"^(?:LAYER_CHANGE|LAYER\s*:\s*\d+)", re.IGNORECASE)


def round_half_away(value: float) -> int:
    """Deterministic round-half-away-from-zero matching C99 ``round()``."""
    if value >= 0.0:
        return math.floor(value + 0.5)
    return -math.floor(-value + 0.5)


def sanitize_ascii_name(name: str, max_len: int) -> str:
    """Sanitize a build name to printable ASCII truncated to ``max_len`` bytes."""
    cleaned = "".join(ch if 0x20 <= ord(ch) <= 0x7E else "_" for ch in name).strip()
    if not cleaned:
        cleaned = "OrcaX3G"
    return cleaned[:max_len]


def _check_int32(
    value: int, axis: str, line_number: int, raw_line: str
) -> int:
    if value < INT32_MIN or value > INT32_MAX:
        raise ValueRangeError(
            f"Axis {axis} step coordinate {value} overflows 32-bit signed integer range",
            line_number=line_number,
            raw_line=raw_line,
        )
    return value


@dataclass(frozen=True)
class ConversionOptions:
    """Options controlling G-code -> X3G translation."""

    profile: MachineProfile = REPLICATOR_2
    firmware: str = "sailfish"  # "sailfish" (default, stock-compatible unless M322 used) or "stock"
    build_name: str = "OrcaX3G"
    strict_bounds: bool = True
    framed: bool = False  # False for .x3g SD files; True for serial wire framing (gpx -F)
    auto_build_notifications: bool = True

    def __post_init__(self) -> None:
        fw = self.firmware.strip().lower()
        if fw not in ("sailfish", "stock"):
            raise ValueError(f"Unknown firmware target {self.firmware!r}; expected 'sailfish' or 'stock'")


@dataclass
class ExportSummary:
    """Structured human- and machine-readable summary emitted after every conversion."""

    machine_profile: str
    firmware: str
    build_name: str
    source_path: str = ""
    output_path: str = ""
    gcode_lines: int = 0
    x3g_bytes: int = 0
    x3g_commands: int = 0
    motion_commands: int = 0
    zero_step_moves_skipped: int = 0
    layers_detected: int = 0
    total_filament_mm: float = 0.0
    estimated_print_time_s: float = 0.0
    feedrate_clamps: int = 0
    warnings: list[str] = field(default_factory=list)
    ignored_commands: dict[str, int] = field(default_factory=dict)

    def add_ignored(self, code: str) -> None:
        self.ignored_commands[code] = self.ignored_commands.get(code, 0) + 1

    def add_warning_once(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def to_dict(self) -> dict[str, Any]:
        return {
            "machine_profile": self.machine_profile,
            "firmware": self.firmware,
            "build_name": self.build_name,
            "source_path": self.source_path,
            "output_path": self.output_path,
            "gcode_lines": self.gcode_lines,
            "x3g_bytes": self.x3g_bytes,
            "x3g_commands": self.x3g_commands,
            "motion_commands": self.motion_commands,
            "zero_step_moves_skipped": self.zero_step_moves_skipped,
            "layers_detected": self.layers_detected,
            "total_filament_mm": round(self.total_filament_mm, 4),
            "estimated_print_time_s": round(self.estimated_print_time_s, 3),
            "feedrate_clamps": self.feedrate_clamps,
            "warnings": list(self.warnings),
            "ignored_commands": dict(sorted(self.ignored_commands.items())),
        }

    def format_text(self) -> str:
        mins, secs = divmod(int(round(self.estimated_print_time_s)), 60)
        hrs, mins = divmod(mins, 60)
        lines = [
            f"OrcaX3G Export Summary ({self.machine_profile} / {self.firmware})",
            f"  Build name:       {self.build_name}",
            f"  X3G size:         {self.x3g_bytes:,} bytes ({self.x3g_commands:,} commands)",
            f"  Motion commands:  {self.motion_commands:,} (zero-step skipped: {self.zero_step_moves_skipped})",
            f"  Layers detected:  {self.layers_detected}",
            f"  Filament used:    {self.total_filament_mm:.2f} mm",
            f"  Est. print time:  {hrs}h {mins:02d}m {secs:02d}s (constant-speed model)",
            f"  Feedrate clamps:  {self.feedrate_clamps}",
        ]
        if self.ignored_commands:
            ignored_str = ", ".join(f"{k}x{v}" for k, v in sorted(self.ignored_commands.items()))
            lines.append(f"  Ignored commands: {ignored_str}")
        else:
            lines.append("  Ignored commands: none")
        if self.warnings:
            lines.append(f"  Warnings ({len(self.warnings)}):")
            for w in self.warnings:
                lines.append(f"    - {w}")
        else:
            lines.append("  Warnings:         none")
        return "\n".join(lines)


class GCodePlanner:
    """Translates a parsed G-code stream into deterministic X3G command IR."""

    def __init__(self, options: ConversionOptions | None = None) -> None:
        self.options = options or ConversionOptions()
        self.profile = self.options.profile
        self.firmware = self.options.firmware.strip().lower()
        self.build_name = sanitize_ascii_name(
            self.options.build_name, self.profile.build_name_max_len
        )
        self.state = PrinterState()
        self.commands: list[X3GCommand] = []
        self.summary = ExportSummary(
            machine_profile=self.profile.profile_id,
            firmware=self.firmware,
            build_name=self.build_name,
        )
        self._pwm_fan_warned = False
        self._m900_warned = False
        self._bed_zero_warned = False

    def plan_lines(
        self,
        lines: Iterable[str],
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> tuple[list[X3GCommand], ExportSummary]:
        """Parse and plan an iterable of raw G-code lines."""
        for parsed in iter_parsed_lines(lines):
            self.summary.gcode_lines += 1
            if cancel_check is not None and (self.summary.gcode_lines & 0x3FF) == 0:
                if cancel_check():
                    raise RuntimeError("X3G conversion cancelled by user")
            self.process_line(parsed)

        self.finalize()
        return self.commands, self.summary

    def _ensure_build_started(self) -> None:
        if self.options.auto_build_notifications and not self.state.build_started:
            self.commands.append(BuildStartNotification(build_name=self.build_name))
            self.state.build_started = True

    def finalize(self) -> None:
        """Emit automatic build-start/build-end notifications if absent and finalize summary."""
        if self.options.auto_build_notifications:
            if not self.state.build_started:
                self.commands.insert(0, BuildStartNotification(build_name=self.build_name))
                self.state.build_started = True
            if not self.state.build_ended:
                self.commands.append(BuildEndNotification(flags=0))
                self.state.build_ended = True

        self.summary.x3g_commands = len(self.commands)
        self.summary.layers_detected = self.state.layers_detected
        self.summary.total_filament_mm = self.state.e_physical_mm

    def process_line(self, line: ParsedGCodeLine) -> None:
        """Process a single parsed G-code line into zero or more X3G commands."""
        if line.comment and _LAYER_COMMENT_RE.match(line.comment):
            self.state.layers_from_comments += 1

        if line.directive is not None:
            self.summary.add_ignored(line.directive)
            return

        if line.command is None:
            return

        cmd = line.command
        if cmd in ("G0", "G1"):
            self._handle_linear_move(line)
        elif cmd == "G4":
            self._handle_dwell(line)
        elif cmd == "G21":
            return  # Millimeters (native mode)
        elif cmd == "G28":
            self._handle_g28(line)
        elif cmd == "G90":
            self.state.relative_xyz = False
        elif cmd == "G91":
            self.state.relative_xyz = True
        elif cmd == "G92":
            self._handle_g92(line)
        elif cmd == "G130":
            self._handle_g130(line)
        elif cmd == "G161":
            self._handle_home_dir(line, EndstopSide.MIN)
        elif cmd == "G162":
            self._handle_home_dir(line, EndstopSide.MAX)
        elif cmd in ("G2", "G3"):
            raise UnsupportedCommandError(
                f"Arc command '{cmd}' is not natively supported in X3G.",
                command=cmd,
                line_number=line.line_number,
                raw_line=line.raw_line,
                hint='Disable arc fitting (enable_arc_fitting = "disabled") and avoid "Spiral Lift" Z-hop in OrcaSlicer.',
            )
        elif cmd in ("G10", "G11"):
            raise UnsupportedCommandError(
                f"Command '{cmd}' (firmware retraction / RepRapFirmware tool temp) is not supported in X3G.",
                command=cmd,
                line_number=line.line_number,
                raw_line=line.raw_line,
                hint='Disable firmware retraction (use_firmware_retraction = false) and set gcode_flavor = "marlin".',
            )
        elif cmd == "G17":
            raise UnsupportedCommandError(
                "Arc plane selection 'G17' indicates arc moves are enabled, which X3G does not support.",
                command=cmd,
                line_number=line.line_number,
                raw_line=line.raw_line,
                hint='Set enable_arc_fitting = "disabled" and z_hop_types = "Normal Lift" in OrcaSlicer.',
            )
        elif cmd == "G20":
            raise UnsupportedCommandError(
                "Inches mode ('G20') is not supported; use millimeters ('G21').",
                command=cmd,
                line_number=line.line_number,
                raw_line=line.raw_line,
            )
        elif cmd in ("M0", "M1", "M25"):
            self._handle_pause(line)
        elif cmd == "M600":
            self._handle_m600(line)
        elif cmd == "M17":
            self._handle_enable_axes(line, enable=True)
        elif cmd in ("M18", "M84"):
            self._handle_enable_axes(line, enable=False)
        elif cmd == "M70":
            self._handle_m70(line)
        elif cmd == "M72":
            self._handle_m72(line)
        elif cmd == "M73":
            self._handle_m73(line)
        elif cmd == "M82":
            self.state.relative_e = False
        elif cmd == "M83":
            self.state.relative_e = True
        elif cmd == "M104":
            self._handle_extruder_temp(line, wait=False)
        elif cmd == "M109":
            self._handle_extruder_temp(line, wait=True)
        elif cmd == "M106":
            self._handle_fan_m106(line)
        elif cmd == "M107":
            self._handle_fan_m107(line)
        elif cmd == "M126":
            self._handle_fan_m126(line)
        elif cmd == "M127":
            self._handle_fan_m127(line)
        elif cmd == "M131":
            self._handle_home_eeprom(line, store=True)
        elif cmd == "M132":
            self._handle_home_eeprom(line, store=False)
        elif cmd == "M133":
            self._handle_m133(line)
        elif cmd == "M135":
            self._handle_m135(line)
        elif cmd == "M136":
            if not self.state.build_started:
                self.commands.append(BuildStartNotification(build_name=self.build_name))
                self.state.build_started = True
        elif cmd == "M137":
            self._ensure_build_started()
            if not self.state.build_ended:
                self.commands.append(BuildEndNotification(flags=0))
                self.state.build_ended = True
        elif cmd in ("M140", "M190"):
            self._handle_heated_bed(line)
        elif cmd in ("M141", "M191"):
            self._handle_heated_chamber(line)
        elif cmd == "M320":
            self._ensure_build_started()
            self.state.acceleration_enabled = True
            self.commands.append(SetAccelerationToggle(enable=True))
        elif cmd == "M321":
            self._ensure_build_started()
            self.state.acceleration_enabled = False
            self.commands.append(SetAccelerationToggle(enable=False))
        elif cmd == "M322":
            self._handle_m322(line)
        elif cmd.startswith("T"):
            self._handle_tool_select(line)
        elif cmd in ("M201", "M202", "M203", "M204", "M205", "M400", "M486", "M117"):
            self.summary.add_ignored(cmd)
        elif cmd == "M900":
            self.summary.add_ignored(cmd)
            if not self._m900_warned:
                self._m900_warned = True
                self.summary.add_warning_once(
                    "Ignored M900 (Linear/Pressure Advance): X3G has no runtime PA command "
                    "(Sailfish JKN advance is configured in printer EEPROM)."
                )
        else:
            raise UnsupportedCommandError(
                f"Unsupported G-code command '{cmd}'.",
                command=cmd,
                line_number=line.line_number,
                raw_line=line.raw_line,
                hint="Remove or replace this command in your OrcaSlicer profile/custom G-code.",
            )

    def _require_float(self, line: ParsedGCodeLine, key: str) -> float:
        val = line.params.get(key)
        if val is None:
            raise GCodeSyntaxError(
                f"Command '{line.command}' requires a numeric value for parameter '{key}'.",
                line_number=line.line_number,
                raw_line=line.raw_line,
            )
        return val

    def _validate_tool_param(self, line: ParsedGCodeLine, key: str = "T") -> int:
        if key not in line.params:
            return 0
        raw = self._require_float(line, key)
        if not raw.is_integer() or int(raw) != 0:
            raise UnsupportedCommandError(
                f"Tool index {key}{raw:g} is unsupported on single-extruder Replicator 2 (only T0 exists).",
                command=f"{line.command} {key}{raw:g}",
                line_number=line.line_number,
                raw_line=line.raw_line,
            )
        return 0

    def _axes_mask_from_params(self, line: ParsedGCodeLine, default_mask: int) -> int:
        mask = 0
        if "X" in line.params:
            mask |= AXIS_BIT_X
        if "Y" in line.params:
            mask |= AXIS_BIT_Y
        if "Z" in line.params:
            mask |= AXIS_BIT_Z
        if "A" in line.params or "E" in line.params:
            mask |= AXIS_BIT_A
        if "B" in line.params:
            mask |= AXIS_BIT_B
        return mask if mask != 0 else default_mask

    def _clamp_safe_feedrate(
        self,
        commanded_mm_min: float,
        dx_mm: float,
        dy_mm: float,
        dz_mm: float,
        de_mm: float,
        d_xyz: float,
    ) -> float:
        """Clamp feedrate so no moving axis exceeds its Replicator 2 max feedrate."""
        f = commanded_mm_min
        clamped = False
        if d_xyz > 0.0:
            adx = abs(dx_mm)
            ady = abs(dy_mm)
            adz = abs(dz_mm)
            ade = abs(de_mm)
            if adx > 0.0 and (f * adx / d_xyz) > self.profile.x.max_feedrate_mm_min:
                f = self.profile.x.max_feedrate_mm_min * d_xyz / adx
                clamped = True
            if ady > 0.0 and (f * ady / d_xyz) > self.profile.y.max_feedrate_mm_min:
                f = self.profile.y.max_feedrate_mm_min * d_xyz / ady
                clamped = True
            if adz > 0.0 and (f * adz / d_xyz) > self.profile.z.max_feedrate_mm_min:
                f = self.profile.z.max_feedrate_mm_min * d_xyz / adz
                clamped = True
            if ade > 0.0 and (f * ade / d_xyz) > self.profile.extruder_a.max_feedrate_mm_min:
                f = self.profile.extruder_a.max_feedrate_mm_min * d_xyz / ade
                clamped = True
        else:
            if abs(de_mm) > 0.0 and f > self.profile.extruder_a.max_feedrate_mm_min:
                f = self.profile.extruder_a.max_feedrate_mm_min
                clamped = True

        if clamped:
            self.summary.feedrate_clamps += 1
        return f

    def _handle_linear_move(self, line: ParsedGCodeLine) -> None:
        # Reject unsupported parameters on G0/G1 (e.g., B axis on single-extruder R2, I/J/K)
        allowed = {"X", "Y", "Z", "E", "A", "F"}
        for key in line.params:
            if key == "B":
                val = self._require_float(line, "B")
                if val != 0.0:
                    raise UnsupportedCommandError(
                        "B-axis extrusion is unsupported on single-extruder Replicator 2.",
                        command=f"{line.command} B",
                        line_number=line.line_number,
                        raw_line=line.raw_line,
                    )
                continue
            if key not in allowed:
                raise UnsupportedCommandError(
                    f"Unsupported parameter '{key}' on {line.command}.",
                    command=f"{line.command} {key}",
                    line_number=line.line_number,
                    raw_line=line.raw_line,
                )

        if "E" in line.params and "A" in line.params:
            raise GCodeSyntaxError(
                f"Cannot specify both 'E' and 'A' on the same {line.command} line.",
                line_number=line.line_number,
                raw_line=line.raw_line,
            )

        # Update modal feedrate if F is specified
        if "F" in line.params:
            f_val = self._require_float(line, "F")
            if f_val <= 0.0:
                raise ValueRangeError(
                    f"Feedrate F{f_val:g} must be strictly positive.",
                    line_number=line.line_number,
                    raw_line=line.raw_line,
                )
            self.state.feedrate_mm_min = f_val

        has_x = "X" in line.params
        has_y = "Y" in line.params
        has_z = "Z" in line.params
        e_key = "E" if "E" in line.params else ("A" if "A" in line.params else None)

        if not (has_x or has_y or has_z or e_key is not None):
            # Pure feedrate update (e.g. "G1 F3000")
            return

        if self.state.feedrate_mm_min is None:
            raise ValueRangeError(
                f"Motion command '{line.command}' encountered before any feedrate (F) was set.",
                line_number=line.line_number,
                raw_line=line.raw_line,
                hint="Include an F<mm/min> parameter on or before the first G0/G1 move.",
            )

        self._ensure_build_started()

        # Compute target mm for XYZ
        if has_x:
            vx = self._require_float(line, "X")
            tx_mm = (self.state.x_mm + vx) if self.state.relative_xyz else vx
        else:
            tx_mm = self.state.x_mm

        if has_y:
            vy = self._require_float(line, "Y")
            ty_mm = (self.state.y_mm + vy) if self.state.relative_xyz else vy
        else:
            ty_mm = self.state.y_mm

        if has_z:
            vz = self._require_float(line, "Z")
            tz_mm = (self.state.z_mm + vz) if self.state.relative_xyz else vz
        else:
            tz_mm = self.state.z_mm

        # Bounds check on specified or known XYZ axes
        if self.options.strict_bounds:
            if has_x or self.state.x_known:
                if tx_mm < self.profile.x.min_mm - BOUNDS_EPSILON_MM or tx_mm > self.profile.x.max_mm + BOUNDS_EPSILON_MM:
                    raise OutOfBoundsError(
                        "X", tx_mm, self.profile.x.min_mm, self.profile.x.max_mm,
                        line_number=line.line_number, raw_line=line.raw_line,
                    )
            if has_y or self.state.y_known:
                if ty_mm < self.profile.y.min_mm - BOUNDS_EPSILON_MM or ty_mm > self.profile.y.max_mm + BOUNDS_EPSILON_MM:
                    raise OutOfBoundsError(
                        "Y", ty_mm, self.profile.y.min_mm, self.profile.y.max_mm,
                        line_number=line.line_number, raw_line=line.raw_line,
                    )
            if has_z or self.state.z_known:
                if tz_mm < self.profile.z.min_mm - BOUNDS_EPSILON_MM or tz_mm > self.profile.z.max_mm + BOUNDS_EPSILON_MM:
                    raise OutOfBoundsError(
                        "Z", tz_mm, self.profile.z.min_mm, self.profile.z.max_mm,
                        line_number=line.line_number, raw_line=line.raw_line,
                    )

        dx_mm = (tx_mm - self.state.x_mm) if has_x else 0.0
        dy_mm = (ty_mm - self.state.y_mm) if has_y else 0.0
        dz_mm = (tz_mm - self.state.z_mm) if has_z else 0.0

        # Compute extruder delta & cumulative physical filament
        if e_key is not None:
            ve = self._require_float(line, e_key)
            if self.state.effective_relative_e:
                de_mm = ve
                new_e_logical = self.state.e_logical_mm + ve
            else:
                de_mm = ve - self.state.e_logical_mm
                new_e_logical = ve
            new_e_physical = self.state.e_physical_mm + de_mm
        else:
            de_mm = 0.0
            new_e_logical = self.state.e_logical_mm
            new_e_physical = self.state.e_physical_mm

        # Quantize absolute target steps (zero cumulative drift)
        rel_mask = AXIS_BITS_AB  # A and B are always relative deltas

        if has_x:
            if not self.state.x_known and self.state.relative_xyz:
                ds_x = _check_int32(
                    round_half_away(dx_mm * self.profile.x.steps_per_mm),
                    "X", line.line_number, line.raw_line,
                )
                cmd_x = ds_x
                new_x_steps = self.state.x_steps + ds_x
                rel_mask |= AXIS_BIT_X
            else:
                new_x_steps = _check_int32(
                    round_half_away(tx_mm * self.profile.x.steps_per_mm),
                    "X", line.line_number, line.raw_line,
                )
                ds_x = new_x_steps - self.state.x_steps
                cmd_x = new_x_steps
        else:
            new_x_steps = self.state.x_steps
            ds_x = 0
            if not self.state.x_known:
                rel_mask |= AXIS_BIT_X
                cmd_x = 0
            else:
                cmd_x = self.state.x_steps

        if has_y:
            if not self.state.y_known and self.state.relative_xyz:
                ds_y = _check_int32(
                    round_half_away(dy_mm * self.profile.y.steps_per_mm),
                    "Y", line.line_number, line.raw_line,
                )
                cmd_y = ds_y
                new_y_steps = self.state.y_steps + ds_y
                rel_mask |= AXIS_BIT_Y
            else:
                new_y_steps = _check_int32(
                    round_half_away(ty_mm * self.profile.y.steps_per_mm),
                    "Y", line.line_number, line.raw_line,
                )
                ds_y = new_y_steps - self.state.y_steps
                cmd_y = new_y_steps
        else:
            new_y_steps = self.state.y_steps
            ds_y = 0
            if not self.state.y_known:
                rel_mask |= AXIS_BIT_Y
                cmd_y = 0
            else:
                cmd_y = self.state.y_steps

        if has_z:
            if not self.state.z_known and self.state.relative_xyz:
                ds_z = _check_int32(
                    round_half_away(dz_mm * self.profile.z.steps_per_mm),
                    "Z", line.line_number, line.raw_line,
                )
                cmd_z = ds_z
                new_z_steps = self.state.z_steps + ds_z
                rel_mask |= AXIS_BIT_Z
            else:
                new_z_steps = _check_int32(
                    round_half_away(tz_mm * self.profile.z.steps_per_mm),
                    "Z", line.line_number, line.raw_line,
                )
                ds_z = new_z_steps - self.state.z_steps
                cmd_z = new_z_steps
        else:
            new_z_steps = self.state.z_steps
            ds_z = 0
            if not self.state.z_known:
                rel_mask |= AXIS_BIT_Z
                cmd_z = 0
            else:
                cmd_z = self.state.z_steps

        # Extruder step quantization from cumulative physical filament
        new_e_steps = round_half_away(new_e_physical * self.profile.extruder_a.steps_per_mm)
        ds_e = _check_int32(
            new_e_steps - self.state.e_emitted_steps,
            "A", line.line_number, line.raw_line,
        )
        cmd_a = -ds_e if self.profile.extruder_a.inverted_direction else ds_e
        _check_int32(cmd_a, "A", line.line_number, line.raw_line)

        # Always commit exact mm coordinates (preserving sub-step residuals even on 0-step moves)
        self.state.x_mm = tx_mm
        self.state.y_mm = ty_mm
        self.state.z_mm = tz_mm
        if has_x and not self.state.relative_xyz:
            self.state.x_known = True
        if has_y and not self.state.relative_xyz:
            self.state.y_known = True
        if has_z and not self.state.relative_xyz:
            self.state.z_known = True
        self.state.e_logical_mm = new_e_logical
        self.state.e_physical_mm = new_e_physical

        master_steps = max(abs(ds_x), abs(ds_y), abs(ds_z), abs(ds_e))
        if master_steps == 0:
            self.summary.zero_step_moves_skipped += 1
            return

        # Commit integer step state
        self.state.x_steps = new_x_steps
        self.state.y_steps = new_y_steps
        self.state.z_steps = new_z_steps
        self.state.e_emitted_steps = new_e_steps

        if ds_e > 0 and (ds_x != 0 or ds_y != 0):
            self.state.distinct_extrusion_z.add(round(self.state.z_mm, 4))

        # Compute move distance and safe clamped feedrate
        d_xyz = math.sqrt(dx_mm * dx_mm + dy_mm * dy_mm + dz_mm * dz_mm)
        safe_f_mm_min = self._clamp_safe_feedrate(
            self.state.feedrate_mm_min, dx_mm, dy_mm, dz_mm, de_mm, d_xyz
        )
        distance = d_xyz if d_xyz > 0.0 else abs(de_mm)

        # Guard against float32 underflow to 0.0 (Sailfish/Stock drop distance == 0.0)
        if distance <= 0.0 or struct.unpack("<f", struct.pack("<f", distance))[0] == 0.0:
            step_d_xyz = math.sqrt(
                (ds_x / self.profile.x.steps_per_mm) ** 2
                + (ds_y / self.profile.y.steps_per_mm) ** 2
                + (ds_z / self.profile.z.steps_per_mm) ** 2
            )
            distance = (
                step_d_xyz
                if step_d_xyz > 0.0
                else abs(ds_e) / self.profile.extruder_a.steps_per_mm
            )

        f_mm_s = safe_f_mm_min / 60.0
        duration_s = distance / f_mm_s
        dda_rate = max(1, round_half_away(master_steps / duration_s))
        if dda_rate > UINT32_MAX:
            raise ValueRangeError(
                f"Computed dda_rate {dda_rate} exceeds uint32 maximum.",
                line_number=line.line_number,
                raw_line=line.raw_line,
            )

        feedrate_mult64 = max(1, min(32767, round_half_away(f_mm_s * 64.0)))
        distance_f32 = struct.unpack("<f", struct.pack("<f", distance))[0]

        self.commands.append(
            QueuePointNewExt(
                x=cmd_x,
                y=cmd_y,
                z=cmd_z,
                a=cmd_a,
                b=0,
                dda_rate=dda_rate,
                relative_axes=rel_mask,
                distance_mm=distance_f32,
                feedrate_mult64=feedrate_mult64,
            )
        )
        self.summary.motion_commands += 1
        self.summary.estimated_print_time_s += duration_s

    def _handle_dwell(self, line: ParsedGCodeLine) -> None:
        has_p = "P" in line.params
        has_s = "S" in line.params
        if not (has_p or has_s):
            raise GCodeSyntaxError(
                "G4 dwell requires P<milliseconds> and/or S<seconds>.",
                line_number=line.line_number,
                raw_line=line.raw_line,
            )
        total_ms = 0.0
        if has_p:
            p = self._require_float(line, "P")
            if p < 0:
                raise ValueRangeError(
                    f"G4 dwell P{p:g} cannot be negative.",
                    line_number=line.line_number,
                    raw_line=line.raw_line,
                )
            total_ms += p
        if has_s:
            s = self._require_float(line, "S")
            if s < 0:
                raise ValueRangeError(
                    f"G4 dwell S{s:g} cannot be negative.",
                    line_number=line.line_number,
                    raw_line=line.raw_line,
                )
            total_ms += s * 1000.0

        delay_ms = round_half_away(total_ms)
        if delay_ms > UINT32_MAX:
            raise ValueRangeError(
                f"G4 delay {delay_ms} ms exceeds uint32 range.",
                line_number=line.line_number,
                raw_line=line.raw_line,
            )
        self._ensure_build_started()
        self.commands.append(Delay(delay_ms=delay_ms))
        self.summary.estimated_print_time_s += delay_ms / 1000.0

    def _emit_home(self, axes_mask: int, endstop: EndstopSide, feedrate_override: float | None) -> None:
        if axes_mask == 0:
            return
        max_spm = 0.0
        default_f = float("inf")
        if axes_mask & AXIS_BIT_X:
            max_spm = max(max_spm, self.profile.x.steps_per_mm)
            default_f = min(default_f, self.profile.x.home_feedrate_mm_min)
            self.state.x_known = False
        if axes_mask & AXIS_BIT_Y:
            max_spm = max(max_spm, self.profile.y.steps_per_mm)
            default_f = min(default_f, self.profile.y.home_feedrate_mm_min)
            self.state.y_known = False
        if axes_mask & AXIS_BIT_Z:
            max_spm = max(max_spm, self.profile.z.steps_per_mm)
            default_f = min(default_f, self.profile.z.home_feedrate_mm_min)
            self.state.z_known = False

        # Clamp homing feedrate to axis max_feedrate
        f = feedrate_override if feedrate_override is not None else default_f
        if axes_mask & AXIS_BIT_X:
            f = min(f, self.profile.x.max_feedrate_mm_min)
        if axes_mask & AXIS_BIT_Y:
            f = min(f, self.profile.y.max_feedrate_mm_min)
        if axes_mask & AXIS_BIT_Z:
            f = min(f, self.profile.z.max_feedrate_mm_min)

        step_delay_us = max(1, round_half_away(60_000_000.0 / (f * max_spm)))
        self._ensure_build_started()
        if endstop == EndstopSide.MAX:
            self.commands.append(
                FindAxesMaximum(
                    axes_mask=axes_mask,
                    step_delay_us=step_delay_us,
                    timeout_s=self.profile.homing_timeout_s,
                )
            )
        else:
            self.commands.append(
                FindAxesMinimum(
                    axes_mask=axes_mask,
                    step_delay_us=step_delay_us,
                    timeout_s=self.profile.homing_timeout_s,
                )
            )

    def _handle_g28(self, line: ParsedGCodeLine) -> None:
        f_override: float | None = None
        if "F" in line.params:
            f_override = self._require_float(line, "F")
            if f_override <= 0:
                raise ValueRangeError(
                    f"Homing feedrate F{f_override:g} must be positive.",
                    line_number=line.line_number,
                    raw_line=line.raw_line,
                )
            self.state.feedrate_mm_min = f_override

        mask = 0
        if "X" in line.params:
            mask |= AXIS_BIT_X
        if "Y" in line.params:
            mask |= AXIS_BIT_Y
        if "Z" in line.params:
            mask |= AXIS_BIT_Z
        if mask == 0:
            mask = AXIS_BIT_X | AXIS_BIT_Y | AXIS_BIT_Z

        max_mask = 0
        min_mask = 0
        if mask & AXIS_BIT_X:
            if self.profile.x.home_endstop == EndstopSide.MAX:
                max_mask |= AXIS_BIT_X
            else:
                min_mask |= AXIS_BIT_X
        if mask & AXIS_BIT_Y:
            if self.profile.y.home_endstop == EndstopSide.MAX:
                max_mask |= AXIS_BIT_Y
            else:
                min_mask |= AXIS_BIT_Y
        if mask & AXIS_BIT_Z:
            if self.profile.z.home_endstop == EndstopSide.MAX:
                max_mask |= AXIS_BIT_Z
            else:
                min_mask |= AXIS_BIT_Z

        # Home XY (MAX on R2) before Z (MIN on R2)
        if max_mask:
            self._emit_home(max_mask, EndstopSide.MAX, f_override)
        if min_mask:
            self._emit_home(min_mask, EndstopSide.MIN, f_override)

    def _handle_home_dir(self, line: ParsedGCodeLine, endstop: EndstopSide) -> None:
        f_override: float | None = None
        if "F" in line.params:
            f_override = self._require_float(line, "F")
            if f_override <= 0:
                raise ValueRangeError(
                    f"Homing feedrate F{f_override:g} must be positive.",
                    line_number=line.line_number,
                    raw_line=line.raw_line,
                )
            self.state.feedrate_mm_min = f_override

        mask = 0
        if "X" in line.params:
            mask |= AXIS_BIT_X
        if "Y" in line.params:
            mask |= AXIS_BIT_Y
        if "Z" in line.params:
            mask |= AXIS_BIT_Z
        if mask == 0:
            raise GCodeSyntaxError(
                f"{line.command} requires at least one axis (X, Y, or Z).",
                line_number=line.line_number,
                raw_line=line.raw_line,
            )
        self._emit_home(mask, endstop, f_override)

    def _handle_g92(self, line: ParsedGCodeLine) -> None:
        has_x = "X" in line.params
        has_y = "Y" in line.params
        has_z = "Z" in line.params
        e_key = "E" if "E" in line.params else ("A" if "A" in line.params else None)
        has_b = "B" in line.params

        # Bare G92 resets X=0, Y=0, Z=0, E=0
        if not (has_x or has_y or has_z or e_key is not None or has_b):
            has_x = has_y = has_z = True
            self.state.x_mm = 0.0
            self.state.y_mm = 0.0
            self.state.z_mm = 0.0
            self.state.x_steps = 0
            self.state.y_steps = 0
            self.state.z_steps = 0
            self.state.x_known = True
            self.state.y_known = True
            self.state.z_known = True
            self.state.e_logical_mm = 0.0
            self._ensure_build_started()
            self.commands.append(SetPositionExt(0, 0, 0, 0, 0))
            return

        if e_key is not None:
            ve = self._require_float(line, e_key)
            self.state.e_logical_mm = ve

        if not (has_x or has_y or has_z):
            # G92 E0 only: never emit Command 140 (which overwrites all 5 axes!)
            return

        if has_x:
            vx = self._require_float(line, "X")
            self.state.x_mm = vx
            self.state.x_steps = _check_int32(
                round_half_away(vx * self.profile.x.steps_per_mm),
                "X", line.line_number, line.raw_line,
            )
            self.state.x_known = True
        if has_y:
            vy = self._require_float(line, "Y")
            self.state.y_mm = vy
            self.state.y_steps = _check_int32(
                round_half_away(vy * self.profile.y.steps_per_mm),
                "Y", line.line_number, line.raw_line,
            )
            self.state.y_known = True
        if has_z:
            vz = self._require_float(line, "Z")
            self.state.z_mm = vz
            self.state.z_steps = _check_int32(
                round_half_away(vz * self.profile.z.steps_per_mm),
                "Z", line.line_number, line.raw_line,
            )
            self.state.z_known = True

        if not (self.state.x_known and self.state.y_known and self.state.z_known):
            self.summary.add_warning_once(
                f"Line {line.line_number}: partial XYZ G92 emitted Command 140 while some axes were still unhomed."
            )

        self._ensure_build_started()
        a_step = (
            -self.state.e_emitted_steps
            if self.profile.extruder_a.inverted_direction
            else self.state.e_emitted_steps
        )
        self.commands.append(
            SetPositionExt(
                x=self.state.x_steps,
                y=self.state.y_steps,
                z=self.state.z_steps,
                a=max(INT32_MIN, min(INT32_MAX, a_step)),
                b=0,
            )
        )

    def _handle_g130(self, line: ParsedGCodeLine) -> None:
        axis_map = (("X", 0), ("Y", 1), ("Z", 2), ("A", 3), ("B", 4))
        emitted = False
        self._ensure_build_started()
        for letter, axis_idx in axis_map:
            if letter in line.params:
                raw = self._require_float(line, letter)
                if raw < 0 or raw > 127:
                    raise ValueRangeError(
                        f"G130 digipot value {letter}{raw:g} must be in range 0..127.",
                        line_number=line.line_number,
                        raw_line=line.raw_line,
                    )
                self.commands.append(SetPotValue(axis=axis_idx, value=round_half_away(raw)))
                emitted = True
        if not emitted:
            raise GCodeSyntaxError(
                "G130 requires at least one axis digipot value (X, Y, Z, A, or B).",
                line_number=line.line_number,
                raw_line=line.raw_line,
            )

    def _handle_pause(self, line: ParsedGCodeLine) -> None:
        self._ensure_build_started()
        if "P" in line.params or "S" in line.params:
            self._handle_dwell(line)
            return
        self.commands.append(PauseForButton(button_mask=0x01, timeout_s=0, options=0x04))

    def _handle_m600(self, line: ParsedGCodeLine) -> None:
        self._ensure_build_started()
        self.commands.append(
            DisplayMessage(options=0x02, x=0, y=0, timeout_s=0, message="Change Filament")
        )
        self.commands.append(PauseForButton(button_mask=0x01, timeout_s=0, options=0x04))

    def _handle_enable_axes(self, line: ParsedGCodeLine, *, enable: bool) -> None:
        mask = self._axes_mask_from_params(line, default_mask=AXIS_BITS_ALL)
        self._ensure_build_started()
        self.commands.append(EnableAxes(axes_mask=mask, enable=enable))

    def _handle_m70(self, line: ParsedGCodeLine) -> None:
        timeout = 0
        if "P" in line.params:
            raw_p = self._require_float(line, "P")
            if raw_p < 0 or raw_p > 255:
                raise ValueRangeError(
                    f"M70 timeout P{raw_p:g} must be in range 0..255 seconds.",
                    line_number=line.line_number,
                    raw_line=line.raw_line,
                )
            timeout = round_half_away(raw_p)
        msg = sanitize_ascii_name(line.comment or "Message", 20)
        self._ensure_build_started()
        # options=0x02: clear existing message (bit 0 == 0), last message in group (bit 1 == 1)
        self.commands.append(DisplayMessage(options=0x02, x=0, y=0, timeout_s=timeout, message=msg))

    def _handle_m72(self, line: ParsedGCodeLine) -> None:
        raw_p = self._require_float(line, "P")
        if raw_p < 0 or raw_p > 255 or not raw_p.is_integer():
            raise ValueRangeError(
                f"M72 song ID P{raw_p:g} must be an integer in 0..255.",
                line_number=line.line_number,
                raw_line=line.raw_line,
            )
        self._ensure_build_started()
        self.commands.append(QueueSong(song_id=int(raw_p)))

    def _handle_m73(self, line: ParsedGCodeLine) -> None:
        if "P" not in line.params:
            return
        raw_p = self._require_float(line, "P")
        pct = max(0, min(100, round_half_away(raw_p)))
        self._ensure_build_started()
        self.commands.append(SetBuildPercent(percent=pct, reserved=0))

    def _handle_extruder_temp(self, line: ParsedGCodeLine, *, wait: bool) -> None:
        tool_id = self._validate_tool_param(line, "T")
        if "S" in line.params:
            temp_raw = self._require_float(line, "S")
        elif wait and "R" in line.params:
            temp_raw = self._require_float(line, "R")
        else:
            raise GCodeSyntaxError(
                f"{line.command} requires target temperature parameter 'S' (0..{self.profile.max_nozzle_temp_c} C).",
                line_number=line.line_number,
                raw_line=line.raw_line,
            )
        temp_c = round_half_away(temp_raw)
        if temp_c < 0 or temp_c > self.profile.max_nozzle_temp_c:
            raise ValueRangeError(
                f"Extruder temperature {temp_c} C outside safe Replicator 2 range [0, {self.profile.max_nozzle_temp_c}] C.",
                line_number=line.line_number,
                raw_line=line.raw_line,
            )
        self._ensure_build_started()
        self.state.nozzle_temp_c = temp_c
        self.commands.append(ToolSetTemp(tool_id=tool_id, temperature_c=temp_c))
        if wait and temp_c > 0:
            self.commands.append(
                WaitForTool(
                    tool_id=tool_id,
                    ping_delay_ms=self.profile.heater_wait_ping_ms,
                    timeout_s=self.profile.heater_wait_timeout_s,
                )
            )

    def _handle_fan_m106(self, line: ParsedGCodeLine) -> None:
        if "P" in line.params:
            p_val = self._require_float(line, "P")
            if not p_val.is_integer() or int(p_val) != 0:
                raise UnsupportedCommandError(
                    f"Fan index M106 P{p_val:g} is unsupported on Replicator 2 (only part cooling fan P0 exists).",
                    command=f"M106 P{p_val:g}",
                    line_number=line.line_number,
                    raw_line=line.raw_line,
                    hint="Disable auxiliary_fan and exhaust_fan in OrcaSlicer.",
                )
        if "S" in line.params:
            s_val = self._require_float(line, "S")
            if s_val < 0 or s_val > 255:
                raise ValueRangeError(
                    f"M106 fan speed S{s_val:g} must be in range 0..255.",
                    line_number=line.line_number,
                    raw_line=line.raw_line,
                )
            enabled = s_val > 0.0
            if 0.0 < s_val < 255.0 and not self._pwm_fan_warned:
                self._pwm_fan_warned = True
                self.summary.add_warning_once(
                    f"Fractional fan PWM (e.g. M106 S{s_val:g}) quantized to binary ON: "
                    "X3G SLAVE_CMD_TOGGLE_VALVE (13) only supports ON/OFF."
                )
        else:
            enabled = True
        self._ensure_build_started()
        self.state.fan_on = enabled
        self.commands.append(ToolToggleValve(tool_id=0, enabled=enabled))

    def _handle_fan_m107(self, line: ParsedGCodeLine) -> None:
        if "P" in line.params:
            p_val = self._require_float(line, "P")
            if not p_val.is_integer() or int(p_val) != 0:
                raise UnsupportedCommandError(
                    f"Fan index M107 P{p_val:g} is unsupported on Replicator 2.",
                    command=f"M107 P{p_val:g}",
                    line_number=line.line_number,
                    raw_line=line.raw_line,
                )
        self._ensure_build_started()
        self.state.fan_on = False
        self.commands.append(ToolToggleValve(tool_id=0, enabled=False))

    def _handle_fan_m126(self, line: ParsedGCodeLine) -> None:
        enabled = True
        if "S" in line.params:
            s_val = self._require_float(line, "S")
            enabled = s_val > 0.0
        self._ensure_build_started()
        self.state.fan_on = enabled
        self.commands.append(ToolToggleValve(tool_id=0, enabled=enabled))

    def _handle_fan_m127(self, line: ParsedGCodeLine) -> None:
        self._ensure_build_started()
        self.state.fan_on = False
        self.commands.append(ToolToggleValve(tool_id=0, enabled=False))

    def _handle_home_eeprom(self, line: ParsedGCodeLine, *, store: bool) -> None:
        mask = self._axes_mask_from_params(line, default_mask=0)
        if mask == 0:
            raise GCodeSyntaxError(
                f"{line.command} requires at least one axis (X, Y, Z, A, B).",
                line_number=line.line_number,
                raw_line=line.raw_line,
            )
        self._ensure_build_started()
        if store:
            self.commands.append(StoreHomePosition(axes_mask=mask))
        else:
            self.commands.append(RecallHomePosition(axes_mask=mask))
            # Update software position to calibrated EEPROM home offsets (Sailfish/Stock)
            if mask & AXIS_BIT_X:
                self.state.x_mm = self.profile.x.home_offset_mm
                self.state.x_steps = round_half_away(
                    self.profile.x.home_offset_mm * self.profile.x.steps_per_mm
                )
                self.state.x_known = True
            if mask & AXIS_BIT_Y:
                self.state.y_mm = self.profile.y.home_offset_mm
                self.state.y_steps = round_half_away(
                    self.profile.y.home_offset_mm * self.profile.y.steps_per_mm
                )
                self.state.y_known = True
            if mask & AXIS_BIT_Z:
                self.state.z_mm = self.profile.z.home_offset_mm
                self.state.z_steps = round_half_away(
                    self.profile.z.home_offset_mm * self.profile.z.steps_per_mm
                )
                self.state.z_known = True

    def _handle_m133(self, line: ParsedGCodeLine) -> None:
        tool_id = self._validate_tool_param(line, "T")
        timeout = self.profile.heater_wait_timeout_s
        if "P" in line.params:
            raw_p = self._require_float(line, "P")
            if raw_p < 0 or raw_p > 65535:
                raise ValueRangeError(
                    f"M133 timeout P{raw_p:g} must be in 0..65535 seconds.",
                    line_number=line.line_number,
                    raw_line=line.raw_line,
                )
            timeout = round_half_away(raw_p)
        self._ensure_build_started()
        self.commands.append(
            WaitForTool(
                tool_id=tool_id,
                ping_delay_ms=self.profile.heater_wait_ping_ms,
                timeout_s=timeout,
            )
        )

    def _handle_m135(self, line: ParsedGCodeLine) -> None:
        tool_id = self._validate_tool_param(line, "T")
        self._ensure_build_started()
        self.state.active_tool = tool_id
        self.commands.append(ChangeTool(tool_id=tool_id))

    def _handle_tool_select(self, line: ParsedGCodeLine) -> None:
        assert line.command is not None
        tool_id = int(line.command[1:])
        if tool_id != 0:
            raise UnsupportedCommandError(
                f"Tool '{line.command}' is unsupported on single-extruder Replicator 2 (only T0 exists).",
                command=line.command,
                line_number=line.line_number,
                raw_line=line.raw_line,
            )
        self._ensure_build_started()
        self.state.active_tool = 0
        self.commands.append(ChangeTool(tool_id=0))

    def _handle_heated_bed(self, line: ParsedGCodeLine) -> None:
        temp = 0.0
        if "S" in line.params:
            temp = self._require_float(line, "S")
        elif "R" in line.params:
            temp = self._require_float(line, "R")
        if temp > 0.0:
            raise UnsupportedCommandError(
                f"Heated bed command '{line.command} S{temp:g}' is unsupported: "
                "MakerBot Replicator 2 has no heated build plate.",
                command=line.command or "M140",
                line_number=line.line_number,
                raw_line=line.raw_line,
                hint="Set bed temperature to 0 C in your OrcaSlicer PLA filament preset.",
            )
        self.summary.add_ignored(f"{line.command} S0")
        if not self._bed_zero_warned:
            self._bed_zero_warned = True
            self.summary.add_warning_once(
                f"Ignored {line.command} S0 (Replicator 2 has no heated build plate)."
            )

    def _handle_heated_chamber(self, line: ParsedGCodeLine) -> None:
        temp = 0.0
        if "S" in line.params:
            temp = self._require_float(line, "S")
        if temp > 0.0:
            raise UnsupportedCommandError(
                f"Heated chamber command '{line.command} S{temp:g}' is unsupported on Replicator 2.",
                command=line.command or "M141",
                line_number=line.line_number,
                raw_line=line.raw_line,
            )
        self.summary.add_ignored(f"{line.command} S0")

    def _handle_m322(self, line: ParsedGCodeLine) -> None:
        if self.firmware != "sailfish":
            raise UnsupportedCommandError(
                "M322 (Pause at Z, X3G Command 158) is Sailfish-only and hangs Stock firmware.",
                command="M322",
                line_number=line.line_number,
                raw_line=line.raw_line,
                hint="Switch firmware mode to 'sailfish' or remove M322.",
            )
        z_mm = self._require_float(line, "Z")
        if z_mm < 0.0 or z_mm > self.profile.z.max_mm:
            raise OutOfBoundsError(
                "Z", z_mm, self.profile.z.min_mm, self.profile.z.max_mm,
                line_number=line.line_number, raw_line=line.raw_line,
            )
        self._ensure_build_started()
        z_f32 = struct.unpack("<f", struct.pack("<f", z_mm))[0]
        self.commands.append(PauseAtZPos(z_mm=z_f32))
