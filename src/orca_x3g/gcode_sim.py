"""Independent idealized G-code reference simulator for Phase 3 equivalence testing.

IMPORTANT INDEPENDENCE RULE (docs/EQUIVALENCE.md Section 2):
This module MUST NOT import ``parser.py``, ``planner.py``, ``state.py``, or
``encoder.py``. It parses G-code independently in floating-point millimeters and
records the exact idealized toolpath and non-motion event stream for comparison
against the decoded X3G virtual machine output.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .machine_profile import EndstopSide, MachineProfile, REPLICATOR_2

_WORD_RE = re.compile(r"([A-Za-z])([+-]?(?:\d+(?:\.\d*)?|\.\d+))?")
_PAREN_COMMENT_RE = re.compile(r"\([^)]*\)")
_CHECKSUM_RE = re.compile(r"\*\d+\s*$")


@dataclass(frozen=True)
class ReferenceMotionEvent:
    """Idealized continuous-space G-code motion event."""

    line_number: int
    x_mm: float
    y_mm: float
    z_mm: float
    x_known: bool
    y_known: bool
    z_known: bool
    dx_mm: float
    dy_mm: float
    dz_mm: float
    e_cum_mm: float
    de_mm: float
    distance_mm: float
    commanded_feedrate_mm_s: float
    clamped_feedrate_mm_s: float
    duration_s: float
    # Ideal quantized step deltas (used solely to classify zero-step sub-step moves per E3)
    expected_step_moved: bool


@dataclass(frozen=True)
class ReferenceControlEvent:
    """Idealized non-motion event from the input G-code stream."""

    line_number: int
    kind: str
    data: dict[str, Any]


@dataclass
class GCodeSimulationResult:
    """Complete output of the independent G-code reference simulator."""

    events: list[ReferenceMotionEvent | ReferenceControlEvent] = field(default_factory=list)
    motion_events: list[ReferenceMotionEvent] = field(default_factory=list)
    non_zero_step_motions: list[ReferenceMotionEvent] = field(default_factory=list)
    zero_step_motions: list[ReferenceMotionEvent] = field(default_factory=list)
    control_events: list[ReferenceControlEvent] = field(default_factory=list)
    final_x_mm: float = 0.0
    final_y_mm: float = 0.0
    final_z_mm: float = 0.0
    final_e_cum_mm: float = 0.0
    total_time_s: float = 0.0


def _rha(v: float) -> int:
    return math.floor(v + 0.5) if v >= 0.0 else -math.floor(-v + 0.5)


def _strip_comments_and_extract_msg(raw: str) -> tuple[str, str]:
    """Return ``(code_part, comment_text)`` independently of ``parser.py``."""
    line = raw.rstrip("\r\n")
    semi_msg = ""
    if ";" in line:
        idx = line.index(";")
        semi_msg = line[idx + 1 :].strip()
        line = line[:idx]
    paren_msgs = [m.group(0)[1:-1].strip() for m in _PAREN_COMMENT_RE.finditer(line)]
    line = _PAREN_COMMENT_RE.sub(" ", line)
    line = _CHECKSUM_RE.sub("", line).strip()
    comment = paren_msgs[0] if paren_msgs else semi_msg
    return line, comment


def _clamp_feedrate_mm_min(
    f_mm_min: float,
    dx: float,
    dy: float,
    dz: float,
    de: float,
    d_xyz: float,
    profile: MachineProfile,
) -> float:
    f = f_mm_min
    if d_xyz > 0.0:
        for delta, fmax in (
            (abs(dx), profile.x.max_feedrate_mm_min),
            (abs(dy), profile.y.max_feedrate_mm_min),
            (abs(dz), profile.z.max_feedrate_mm_min),
            (abs(de), profile.extruder_a.max_feedrate_mm_min),
        ):
            if delta > 0.0 and f * (delta / d_xyz) > fmax:
                f = fmax * (d_xyz / delta)
    elif abs(de) > 0.0:
        if f > profile.extruder_a.max_feedrate_mm_min:
            f = profile.extruder_a.max_feedrate_mm_min
    return f


def simulate_gcode(
    lines: Iterable[str],
    profile: MachineProfile = REPLICATOR_2,
) -> GCodeSimulationResult:
    """Simulate a G-code stream in continuous mm coordinates on an ideal machine model."""
    res = GCodeSimulationResult()

    x = 0.0
    y = 0.0
    z = 0.0
    x_known = False
    y_known = False
    z_known = False
    e_logical = 0.0
    e_cum = 0.0

    # Parallel integer step tracker ONLY to determine whether a sub-step move crosses a step boundary
    xs = 0
    ys = 0
    zs = 0
    es = 0

    rel_xyz = False
    rel_e = False
    feedrate_mm_min: float | None = None

    spm_x = profile.x.steps_per_mm
    spm_y = profile.y.steps_per_mm
    spm_z = profile.z.steps_per_mm
    spm_a = profile.extruder_a.steps_per_mm

    ignored_m = {"M117", "M201", "M202", "M203", "M204", "M205", "M400", "M486", "M900", "M108"}

    for lineno, raw in enumerate(lines, start=1):
        code, comment = _strip_comments_and_extract_msg(raw)
        if not code:
            continue
        upper = code.upper()
        if upper.startswith("EXCLUDE_OBJECT"):
            continue
        if upper.startswith("M117"):
            continue

        words = [(m.group(1).upper(), m.group(2)) for m in _WORD_RE.finditer(code)]
        if not words:
            continue

        # Drop optional N line number
        if words[0][0] == "N":
            words = words[1:]
            if not words:
                continue

        cmd_letter, cmd_num_str = words[0]
        if cmd_num_str is None:
            continue
        cmd = f"{cmd_letter}{int(float(cmd_num_str))}"
        params: dict[str, float | None] = {}
        for k, v in words[1:]:
            params[k] = None if v is None else float(v)

        if cmd in ("G0", "G1"):
            if "F" in params and params["F"] is not None:
                feedrate_mm_min = params["F"]

            has_motion_axis = any(a in params for a in ("X", "Y", "Z", "E"))
            if not has_motion_axis:
                continue

            if "X" in params and params["X"] is not None:
                nx = (x + params["X"]) if rel_xyz else params["X"]
                if not rel_xyz:
                    x_known = True
            else:
                nx = x

            if "Y" in params and params["Y"] is not None:
                ny = (y + params["Y"]) if rel_xyz else params["Y"]
                if not rel_xyz:
                    y_known = True
            else:
                ny = y

            if "Z" in params and params["Z"] is not None:
                nz = (z + params["Z"]) if rel_xyz else params["Z"]
                if not rel_xyz:
                    z_known = True
            else:
                nz = z

            if "E" in params and params["E"] is not None:
                ev = params["E"]
                if rel_e or rel_xyz:
                    de = ev
                    e_logical += ev
                else:
                    de = ev - e_logical
                    e_logical = ev
            else:
                de = 0.0

            dx = nx - x
            dy = ny - y
            dz = nz - z
            x, y, z = nx, ny, nz
            e_cum += de

            # Step-crossing classification
            if x_known:
                nxs = _rha(x * spm_x)
                dxs = nxs - xs
            elif "X" in params and params["X"] is not None:
                dxs = _rha(dx * spm_x)
                nxs = xs + dxs
            else:
                dxs = 0
                nxs = xs

            if y_known:
                nys = _rha(y * spm_y)
                dys = nys - ys
            elif "Y" in params and params["Y"] is not None:
                dys = _rha(dy * spm_y)
                nys = ys + dys
            else:
                dys = 0
                nys = ys

            if z_known:
                nzs = _rha(z * spm_z)
                dzs = nzs - zs
            elif "Z" in params and params["Z"] is not None:
                dzs = _rha(dz * spm_z)
                nzs = zs + dzs
            else:
                dzs = 0
                nzs = zs

            nes = _rha(e_cum * spm_a)
            des = nes - es

            xs, ys, zs, es = nxs, nys, nzs, nes
            step_moved = (dxs != 0) or (dys != 0) or (dzs != 0) or (des != 0)

            d_xyz = math.sqrt(dx * dx + dy * dy + dz * dz)
            dist = d_xyz if d_xyz > 0.0 else abs(de)
            f_cmd = feedrate_mm_min if feedrate_mm_min is not None else 0.0
            f_clamped = _clamp_feedrate_mm_min(f_cmd, dx, dy, dz, de, d_xyz, profile)
            f_clamped_s = f_clamped / 60.0
            dur_s = (dist / f_clamped_s) if (dist > 0.0 and f_clamped_s > 0.0 and step_moved) else 0.0
            res.total_time_s += dur_s

            mev = ReferenceMotionEvent(
                line_number=lineno,
                x_mm=x,
                y_mm=y,
                z_mm=z,
                x_known=x_known,
                y_known=y_known,
                z_known=z_known,
                dx_mm=dx,
                dy_mm=dy,
                dz_mm=dz,
                e_cum_mm=e_cum,
                de_mm=de,
                distance_mm=dist,
                commanded_feedrate_mm_s=f_cmd / 60.0,
                clamped_feedrate_mm_s=f_clamped_s,
                duration_s=dur_s,
                expected_step_moved=step_moved,
            )
            res.motion_events.append(mev)
            if step_moved:
                res.non_zero_step_motions.append(mev)
                res.events.append(mev)
            else:
                res.zero_step_motions.append(mev)

        elif cmd == "G4":
            if "P" in params and params["P"] is not None:
                ms = _rha(params["P"])
            elif "S" in params and params["S"] is not None:
                ms = _rha(params["S"] * 1000.0)
            else:
                continue
            res.total_time_s += ms / 1000.0
            cev = ReferenceControlEvent(lineno, "dwell", {"delay_ms": ms})
            res.control_events.append(cev)
            res.events.append(cev)

        elif cmd == "G21":
            continue
        elif cmd == "G90":
            rel_xyz = False
        elif cmd == "G91":
            rel_xyz = True
        elif cmd == "M82":
            rel_e = False
        elif cmd == "M83":
            rel_e = True

        elif cmd == "G92":
            if not params:
                x = y = z = e_logical = 0.0
                xs = ys = zs = 0
                x_known = y_known = z_known = True
                cev = ReferenceControlEvent(
                    lineno, "set_position", {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0}
                )
                res.control_events.append(cev)
                res.events.append(cev)
            else:
                if "E" in params and params["E"] is not None:
                    e_logical = params["E"]
                has_xyz = any(a in params for a in ("X", "Y", "Z"))
                if has_xyz:
                    if "X" in params and params["X"] is not None:
                        x = params["X"]
                        xs = _rha(x * spm_x)
                        x_known = True
                    if "Y" in params and params["Y"] is not None:
                        y = params["Y"]
                        ys = _rha(y * spm_y)
                        y_known = True
                    if "Z" in params and params["Z"] is not None:
                        z = params["Z"]
                        zs = _rha(z * spm_z)
                        z_known = True
                    cev = ReferenceControlEvent(
                        lineno,
                        "set_position",
                        {"x_mm": x, "y_mm": y, "z_mm": z},
                    )
                    res.control_events.append(cev)
                    res.events.append(cev)

        elif cmd == "G28":
            sel_x = "X" in params
            sel_y = "Y" in params
            sel_z = "Z" in params
            if not (sel_x or sel_y or sel_z):
                sel_x = sel_y = sel_z = True
            max_mask = 0
            min_mask = 0
            if sel_x:
                x_known = False
                if profile.x.home_endstop is EndstopSide.MAX:
                    max_mask |= 0x01
                else:
                    min_mask |= 0x01
            if sel_y:
                y_known = False
                if profile.y.home_endstop is EndstopSide.MAX:
                    max_mask |= 0x02
                else:
                    min_mask |= 0x02
            if sel_z:
                z_known = False
                if profile.z.home_endstop is EndstopSide.MAX:
                    max_mask |= 0x04
                else:
                    min_mask |= 0x04
            if max_mask:
                cev = ReferenceControlEvent(lineno, "home", {"direction": "max", "axes_mask": max_mask})
                res.control_events.append(cev)
                res.events.append(cev)
            if min_mask:
                cev = ReferenceControlEvent(lineno, "home", {"direction": "min", "axes_mask": min_mask})
                res.control_events.append(cev)
                res.events.append(cev)

        elif cmd in ("G161", "G162"):
            direction = "min" if cmd == "G161" else "max"
            mask = 0
            if "X" in params:
                mask |= 0x01
                x_known = False
            if "Y" in params:
                mask |= 0x02
                y_known = False
            if "Z" in params:
                mask |= 0x04
                z_known = False
            if mask:
                cev = ReferenceControlEvent(lineno, "home", {"direction": direction, "axes_mask": mask})
                res.control_events.append(cev)
                res.events.append(cev)

        elif cmd == "G130":
            for ax_idx, ax_letter in enumerate(("X", "Y", "Z", "A", "B")):
                if ax_letter in params and params[ax_letter] is not None:
                    cev = ReferenceControlEvent(
                        lineno, "set_pot", {"axis": ax_idx, "value": _rha(params[ax_letter])}
                    )
                    res.control_events.append(cev)
                    res.events.append(cev)

        elif cmd in ("M104", "M109"):
            temp_val = params.get("S")
            if temp_val is None and cmd == "M109":
                temp_val = params.get("R")
            if temp_val is None:
                continue
            temp_c = _rha(temp_val)
            cev = ReferenceControlEvent(lineno, "set_temp", {"tool_id": 0, "temperature_c": temp_c})
            res.control_events.append(cev)
            res.events.append(cev)
            if cmd == "M109" and temp_c > 0:
                wev = ReferenceControlEvent(lineno, "wait_temp", {"tool_id": 0})
                res.control_events.append(wev)
                res.events.append(wev)

        elif cmd == "M133":
            wev = ReferenceControlEvent(lineno, "wait_temp", {"tool_id": 0})
            res.control_events.append(wev)
            res.events.append(wev)

        elif cmd == "M106":
            s_val = 255.0 if ("S" not in params or params["S"] is None) else params["S"]
            cev = ReferenceControlEvent(lineno, "fan", {"tool_id": 0, "enabled": s_val > 0.0})
            res.control_events.append(cev)
            res.events.append(cev)

        elif cmd == "M107":
            cev = ReferenceControlEvent(lineno, "fan", {"tool_id": 0, "enabled": False})
            res.control_events.append(cev)
            res.events.append(cev)

        elif cmd == "M126":
            cev = ReferenceControlEvent(lineno, "fan", {"tool_id": 0, "enabled": True})
            res.control_events.append(cev)
            res.events.append(cev)

        elif cmd == "M127":
            cev = ReferenceControlEvent(lineno, "fan", {"tool_id": 0, "enabled": False})
            res.control_events.append(cev)
            res.events.append(cev)

        elif cmd in ("M131", "M132"):
            mask = 0
            for bit, ax in ((0x01, "X"), (0x02, "Y"), (0x04, "Z"), (0x08, "A"), (0x10, "B")):
                if ax in params:
                    mask |= bit
            kind = "store_home" if cmd == "M131" else "recall_home"
            if cmd == "M132":
                if mask & 0x01:
                    x = profile.x.home_offset_mm
                    xs = _rha(x * spm_x)
                    x_known = True
                if mask & 0x02:
                    y = profile.y.home_offset_mm
                    ys = _rha(y * spm_y)
                    y_known = True
                if mask & 0x04:
                    z = profile.z.home_offset_mm
                    zs = _rha(z * spm_z)
                    z_known = True
            cev = ReferenceControlEvent(lineno, kind, {"axes_mask": mask})
            res.control_events.append(cev)
            res.events.append(cev)

        elif cmd == "M135" or cmd_letter == "T":
            cev = ReferenceControlEvent(lineno, "change_tool", {"tool_id": 0})
            res.control_events.append(cev)
            res.events.append(cev)

        elif cmd in ("M17", "M18", "M84"):
            mask = 0
            for bit, ax in ((0x01, "X"), (0x02, "Y"), (0x04, "Z"), (0x08, "A"), (0x10, "B")):
                if ax in params:
                    mask |= bit
            if mask == 0:
                mask = 0x1F
            cev = ReferenceControlEvent(
                lineno, "enable_axes", {"enable": cmd == "M17", "axes_mask": mask}
            )
            res.control_events.append(cev)
            res.events.append(cev)

        elif cmd in ("M0", "M1", "M25"):
            if "P" in params or "S" in params:
                ms = 0
                if "P" in params and params["P"] is not None:
                    ms += _rha(params["P"])
                if "S" in params and params["S"] is not None:
                    ms += _rha(params["S"] * 1000.0)
                res.total_time_s += ms / 1000.0
                dev = ReferenceControlEvent(lineno, "dwell", {"delay_ms": ms})
                res.control_events.append(dev)
                res.events.append(dev)
            else:
                cev = ReferenceControlEvent(lineno, "pause", {})
                res.control_events.append(cev)
                res.events.append(cev)

        elif cmd == "M600":
            msg_ev = ReferenceControlEvent(lineno, "display_message", {"message": "Change Filament"})
            pause_ev = ReferenceControlEvent(lineno, "pause", {})
            res.control_events.extend([msg_ev, pause_ev])
            res.events.extend([msg_ev, pause_ev])

        elif cmd == "M70":
            cleaned = "".join(ch if 0x20 <= ord(ch) <= 0x7E else " " for ch in comment)[:20]
            cev = ReferenceControlEvent(lineno, "display_message", {"message": cleaned})
            res.control_events.append(cev)
            res.events.append(cev)

        elif cmd == "M72":
            song = _rha(params.get("P") or 0.0)
            cev = ReferenceControlEvent(lineno, "queue_song", {"song_id": song})
            res.control_events.append(cev)
            res.events.append(cev)

        elif cmd == "M73":
            if "P" in params and params["P"] is not None:
                pct = max(0, min(100, _rha(params["P"])))
                cev = ReferenceControlEvent(lineno, "build_percent", {"percent": pct})
                res.control_events.append(cev)
                res.events.append(cev)

        elif cmd in ("M320", "M321"):
            cev = ReferenceControlEvent(lineno, "accel_toggle", {"enable": cmd == "M320"})
            res.control_events.append(cev)
            res.events.append(cev)

        elif cmd == "M322":
            z_val = params.get("Z")
            if z_val is not None:
                cev = ReferenceControlEvent(lineno, "pause_at_z", {"z_mm": z_val})
                res.control_events.append(cev)
                res.events.append(cev)

        elif cmd in ("M136", "M137"):
            # Build start/end notices are checked via X3GSimulationResult.build_started/ended
            continue

        elif cmd in ("M140", "M190", "M141", "M191") or cmd in ignored_m:
            continue

    res.final_x_mm = x
    res.final_y_mm = y
    res.final_z_mm = z
    res.final_e_cum_mm = e_cum
    return res
