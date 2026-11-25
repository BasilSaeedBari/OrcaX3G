"""Physical toolpath equivalence checker comparing G-code reference VM vs decoded X3G VM.

Implements every formal criterion E1-E8 specified in ``docs/EQUIVALENCE.md``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

from .decoder import (
    DecodedControlEvent,
    DecodedMotionEvent,
    X3GSimulationResult,
    simulate_x3g,
)
from .gcode_sim import (
    GCodeSimulationResult,
    ReferenceControlEvent,
    ReferenceMotionEvent,
    simulate_gcode,
)
from .machine_profile import MachineProfile, REPLICATOR_2

FP_GUARD_MM = 1e-9


@dataclass(frozen=True)
class EquivalenceTolerances:
    """Explicit tolerance bounds derived from docs/EQUIVALENCE.md Section 3."""

    x_half_step_mm: float
    y_half_step_mm: float
    z_half_step_mm: float
    e_cum_half_step_mm: float
    e_seg_one_step_mm: float
    feedrate_mult64_abs_mm_s: float = (0.5 / 64.0) + 1e-6
    dda_rate_abs_steps_s: float = 0.5 + 1e-4
    pause_z_abs_mm: float = 1e-5
    time_rel_tol: float = 0.005
    time_abs_floor_s: float = 0.5

    @classmethod
    def for_profile(cls, profile: MachineProfile) -> EquivalenceTolerances:
        return cls(
            x_half_step_mm=(0.5 / profile.x.steps_per_mm) + FP_GUARD_MM,
            y_half_step_mm=(0.5 / profile.y.steps_per_mm) + FP_GUARD_MM,
            z_half_step_mm=(0.5 / profile.z.steps_per_mm) + FP_GUARD_MM,
            e_cum_half_step_mm=(0.5 / profile.extruder_a.steps_per_mm) + FP_GUARD_MM,
            e_seg_one_step_mm=(1.0 / profile.extruder_a.steps_per_mm) + (2.0 * FP_GUARD_MM),
        )


@dataclass
class EquivalenceMetrics:
    """Observed worst-case deviations across an entire print job."""

    gcode_motion_total: int = 0
    gcode_nonzero_step_motions: int = 0
    gcode_zero_step_motions: int = 0
    x3g_motion_total: int = 0
    control_events_compared: int = 0

    max_x_err_mm: float = 0.0
    max_y_err_mm: float = 0.0
    max_z_err_mm: float = 0.0
    final_x_err_mm: float = 0.0
    final_y_err_mm: float = 0.0
    final_z_err_mm: float = 0.0

    max_e_cum_err_mm: float = 0.0
    max_e_seg_err_mm: float = 0.0
    final_e_cum_err_mm: float = 0.0

    max_feedrate_mult64_err_mm_s: float = 0.0
    max_dda_rate_err_steps_s: float = 0.0
    max_dda_rel_speed_err: float = 0.0

    gcode_total_time_s: float = 0.0
    x3g_total_time_s: float = 0.0
    time_abs_err_s: float = 0.0
    time_rel_err: float = 0.0

    gcode_final_e_mm: float = 0.0
    x3g_final_e_mm: float = 0.0

    build_started: bool = False
    build_ended: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "gcode_motion_total": self.gcode_motion_total,
            "gcode_nonzero_step_motions": self.gcode_nonzero_step_motions,
            "gcode_zero_step_motions": self.gcode_zero_step_motions,
            "x3g_motion_total": self.x3g_motion_total,
            "control_events_compared": self.control_events_compared,
            "max_x_err_mm": self.max_x_err_mm,
            "max_y_err_mm": self.max_y_err_mm,
            "max_z_err_mm": self.max_z_err_mm,
            "final_x_err_mm": self.final_x_err_mm,
            "final_y_err_mm": self.final_y_err_mm,
            "final_z_err_mm": self.final_z_err_mm,
            "max_e_cum_err_mm": self.max_e_cum_err_mm,
            "max_e_seg_err_mm": self.max_e_seg_err_mm,
            "final_e_cum_err_mm": self.final_e_cum_err_mm,
            "max_feedrate_mult64_err_mm_s": self.max_feedrate_mult64_err_mm_s,
            "max_dda_rate_err_steps_s": self.max_dda_rate_err_steps_s,
            "max_dda_rel_speed_err": self.max_dda_rel_speed_err,
            "gcode_total_time_s": self.gcode_total_time_s,
            "x3g_total_time_s": self.x3g_total_time_s,
            "time_abs_err_s": self.time_abs_err_s,
            "time_rel_err": self.time_rel_err,
            "gcode_final_e_mm": self.gcode_final_e_mm,
            "x3g_final_e_mm": self.x3g_final_e_mm,
            "build_started": self.build_started,
            "build_ended": self.build_ended,
        }


@dataclass
class EquivalenceReport:
    """Pass/fail report produced by ``check_equivalence``."""

    passed: bool
    profile_id: str
    tolerances: EquivalenceTolerances
    metrics: EquivalenceMetrics
    violations: list[str] = field(default_factory=list)
    # Optional per-motion error series for plotting (kept empty when collect_series=False)
    x_errors_um: list[float] = field(default_factory=list)
    y_errors_um: list[float] = field(default_factory=list)
    z_errors_um: list[float] = field(default_factory=list)
    e_cum_errors_um: list[float] = field(default_factory=list)
    e_seg_errors_um: list[float] = field(default_factory=list)
    feedrate_errors_mm_s: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "profile_id": self.profile_id,
            "tolerances": {
                "x_half_step_mm": self.tolerances.x_half_step_mm,
                "y_half_step_mm": self.tolerances.y_half_step_mm,
                "z_half_step_mm": self.tolerances.z_half_step_mm,
                "e_cum_half_step_mm": self.tolerances.e_cum_half_step_mm,
                "e_seg_one_step_mm": self.tolerances.e_seg_one_step_mm,
                "feedrate_mult64_abs_mm_s": self.tolerances.feedrate_mult64_abs_mm_s,
                "dda_rate_abs_steps_s": self.tolerances.dda_rate_abs_steps_s,
                "time_rel_tol": self.tolerances.time_rel_tol,
                "time_abs_floor_s": self.tolerances.time_abs_floor_s,
            },
            "metrics": self.metrics.to_dict(),
            "violation_count": len(self.violations),
            "violations": list(self.violations[:50]),
        }


def _compare_control_event(
    idx: int,
    ref: ReferenceControlEvent,
    dec: DecodedControlEvent,
    tol: EquivalenceTolerances,
) -> str | None:
    if ref.kind != dec.kind:
        return (
            f"Event #{idx} (line {ref.line_number}): control kind mismatch "
            f"(G-code={ref.kind!r}, X3G={dec.kind!r}, opcode={dec.opcode})."
        )
    k = ref.kind
    rd, dd = ref.data, dec.data
    if k == "set_temp":
        if rd["tool_id"] != dd.get("tool_id") or rd["temperature_c"] != dd.get("temperature_c"):
            return f"Event #{idx} (line {ref.line_number}): set_temp mismatch {rd} vs {dd}."
    elif k == "wait_temp":
        if rd["tool_id"] != dd.get("tool_id"):
            return f"Event #{idx} (line {ref.line_number}): wait_temp tool mismatch {rd} vs {dd}."
    elif k == "fan":
        if rd["tool_id"] != dd.get("tool_id") or bool(rd["enabled"]) != bool(dd.get("enabled")):
            return f"Event #{idx} (line {ref.line_number}): fan state mismatch {rd} vs {dd}."
    elif k == "dwell":
        if int(rd["delay_ms"]) != int(dd.get("delay_ms", -1)):
            return f"Event #{idx} (line {ref.line_number}): dwell ms mismatch {rd} vs {dd}."
    elif k == "home":
        if rd["direction"] != dd.get("direction") or rd["axes_mask"] != dd.get("axes_mask"):
            return f"Event #{idx} (line {ref.line_number}): home mismatch {rd} vs {dd}."
    elif k in ("store_home", "recall_home"):
        if rd["axes_mask"] != dd.get("axes_mask"):
            return f"Event #{idx} (line {ref.line_number}): {k} mask mismatch {rd} vs {dd}."
    elif k == "set_position":
        for ax, half_tol in (
            ("x_mm", tol.x_half_step_mm),
            ("y_mm", tol.y_half_step_mm),
            ("z_mm", tol.z_half_step_mm),
        ):
            if abs(float(rd[ax]) - float(dd[ax])) > half_tol:
                return f"Event #{idx} (line {ref.line_number}): set_position {ax} mismatch {rd} vs {dd}."
    elif k == "change_tool":
        if rd["tool_id"] != dd.get("tool_id"):
            return f"Event #{idx} (line {ref.line_number}): change_tool mismatch {rd} vs {dd}."
    elif k == "enable_axes":
        if bool(rd["enable"]) != bool(dd.get("enable")) or rd["axes_mask"] != dd.get("axes_mask"):
            return f"Event #{idx} (line {ref.line_number}): enable_axes mismatch {rd} vs {dd}."
    elif k == "display_message":
        if rd["message"] != dd.get("message"):
            return f"Event #{idx} (line {ref.line_number}): display_message mismatch {rd} vs {dd}."
    elif k == "set_pot":
        if rd["axis"] != dd.get("axis") or rd["value"] != dd.get("value"):
            return f"Event #{idx} (line {ref.line_number}): set_pot mismatch {rd} vs {dd}."
    elif k == "build_percent":
        if rd["percent"] != dd.get("percent"):
            return f"Event #{idx} (line {ref.line_number}): build_percent mismatch {rd} vs {dd}."
    elif k == "queue_song":
        if rd["song_id"] != dd.get("song_id"):
            return f"Event #{idx} (line {ref.line_number}): queue_song mismatch {rd} vs {dd}."
    elif k == "accel_toggle":
        if bool(rd["enable"]) != bool(dd.get("enable")):
            return f"Event #{idx} (line {ref.line_number}): accel_toggle mismatch {rd} vs {dd}."
    elif k == "pause_at_z":
        if abs(float(rd["z_mm"]) - float(dd.get("z_mm", -999.0))) > tol.pause_z_abs_mm:
            return f"Event #{idx} (line {ref.line_number}): pause_at_z mismatch {rd} vs {dd}."
    elif k == "pause":
        pass
    return None


def compare_simulations(
    gcode_sim: GCodeSimulationResult,
    x3g_sim: X3GSimulationResult,
    profile: MachineProfile = REPLICATOR_2,
    *,
    require_build_notifications: bool = True,
    collect_series: bool = False,
    max_violations: int = 100,
) -> EquivalenceReport:
    """Compare G-code reference VM and decoded X3G VM streams under E1-E8."""
    tol = EquivalenceTolerances.for_profile(profile)
    metrics = EquivalenceMetrics(
        gcode_motion_total=len(gcode_sim.motion_events),
        gcode_nonzero_step_motions=len(gcode_sim.non_zero_step_motions),
        gcode_zero_step_motions=len(gcode_sim.zero_step_motions),
        x3g_motion_total=len(x3g_sim.motion_events),
        gcode_total_time_s=gcode_sim.total_time_s,
        x3g_total_time_s=x3g_sim.total_time_s,
        gcode_final_e_mm=gcode_sim.final_e_cum_mm,
        x3g_final_e_mm=x3g_sim.final_e_cum_mm,
        build_started=x3g_sim.build_started,
        build_ended=x3g_sim.build_ended,
    )
    violations: list[str] = []

    def add_violation(msg: str) -> None:
        if len(violations) < max_violations:
            violations.append(msg)

    if require_build_notifications:
        if not x3g_sim.build_started:
            add_violation("Missing Command 153 (BUILD_START_NOTIFICATION) in X3G stream.")
        if not x3g_sim.build_ended:
            add_violation("Missing Command 154 (BUILD_END_NOTIFICATION) in X3G stream.")

    # Filter framing-only notices (build_start / build_end / stream_version) out of the 1-to-1 event comparison
    x3g_comparable = [
        ev
        for ev in x3g_sim.events
        if not (isinstance(ev, DecodedControlEvent) and ev.kind in ("build_start", "build_end", "stream_version"))
    ]
    ref_events = gcode_sim.events

    if len(ref_events) != len(x3g_comparable):
        add_violation(
            f"Event stream length mismatch: G-code emitted {len(ref_events)} comparable events "
            f"({len(gcode_sim.non_zero_step_motions)} motions + {len(gcode_sim.control_events)} controls), "
            f"X3G decoded {len(x3g_comparable)} comparable events "
            f"({len(x3g_sim.motion_events)} motions)."
        )

    if len(gcode_sim.non_zero_step_motions) != len(x3g_sim.motion_events):
        add_violation(
            f"Motion count mismatch (Criterion E3): G-code has {len(gcode_sim.non_zero_step_motions)} "
            f"non-zero-step motions, X3G has {len(x3g_sim.motion_events)}."
        )

    x_errs_um: list[float] = []
    y_errs_um: list[float] = []
    z_errs_um: list[float] = []
    e_cum_errs_um: list[float] = []
    e_seg_errs_um: list[float] = []
    f_errs: list[float] = []

    pair_count = min(len(ref_events), len(x3g_comparable))
    for idx in range(pair_count):
        ref_ev = ref_events[idx]
        dec_ev = x3g_comparable[idx]

        if isinstance(ref_ev, ReferenceMotionEvent) and isinstance(dec_ev, DecodedMotionEvent):
            # Criterion E1: XYZ position accuracy
            if ref_ev.x_known:
                ex = abs(dec_ev.x_mm - ref_ev.x_mm)
                if ex > metrics.max_x_err_mm:
                    metrics.max_x_err_mm = ex
                if collect_series:
                    x_errs_um.append((dec_ev.x_mm - ref_ev.x_mm) * 1000.0)
                if ex > tol.x_half_step_mm:
                    add_violation(
                        f"E1 X position error {ex:.6f} mm > {tol.x_half_step_mm:.6f} mm "
                        f"at line {ref_ev.line_number} (ref={ref_ev.x_mm:.6f}, x3g={dec_ev.x_mm:.6f})."
                    )
            if ref_ev.y_known:
                ey = abs(dec_ev.y_mm - ref_ev.y_mm)
                if ey > metrics.max_y_err_mm:
                    metrics.max_y_err_mm = ey
                if collect_series:
                    y_errs_um.append((dec_ev.y_mm - ref_ev.y_mm) * 1000.0)
                if ey > tol.y_half_step_mm:
                    add_violation(
                        f"E1 Y position error {ey:.6f} mm > {tol.y_half_step_mm:.6f} mm "
                        f"at line {ref_ev.line_number} (ref={ref_ev.y_mm:.6f}, x3g={dec_ev.y_mm:.6f})."
                    )
            if ref_ev.z_known:
                ez = abs(dec_ev.z_mm - ref_ev.z_mm)
                if ez > metrics.max_z_err_mm:
                    metrics.max_z_err_mm = ez
                if collect_series:
                    z_errs_um.append((dec_ev.z_mm - ref_ev.z_mm) * 1000.0)
                if ez > tol.z_half_step_mm:
                    add_violation(
                        f"E1 Z position error {ez:.6f} mm > {tol.z_half_step_mm:.6f} mm "
                        f"at line {ref_ev.line_number} (ref={ref_ev.z_mm:.6f}, x3g={dec_ev.z_mm:.6f})."
                    )

            # Criterion E2: Extrusion accuracy (cumulative <= 0.5 step, per-segment <= 1.0 step)
            e_cum_err = abs(dec_ev.e_cum_mm - ref_ev.e_cum_mm)
            e_seg_err = abs(dec_ev.de_mm - ref_ev.de_mm)
            if e_cum_err > metrics.max_e_cum_err_mm:
                metrics.max_e_cum_err_mm = e_cum_err
            if e_seg_err > metrics.max_e_seg_err_mm:
                metrics.max_e_seg_err_mm = e_seg_err
            if collect_series:
                e_cum_errs_um.append((dec_ev.e_cum_mm - ref_ev.e_cum_mm) * 1000.0)
                e_seg_errs_um.append((dec_ev.de_mm - ref_ev.de_mm) * 1000.0)
            if e_cum_err > tol.e_cum_half_step_mm:
                add_violation(
                    f"E2 cumulative E error {e_cum_err:.6f} mm > {tol.e_cum_half_step_mm:.6f} mm "
                    f"at line {ref_ev.line_number} (ref={ref_ev.e_cum_mm:.6f}, x3g={dec_ev.e_cum_mm:.6f})."
                )
            if e_seg_err > tol.e_seg_one_step_mm:
                add_violation(
                    f"E2 segment E error {e_seg_err:.6f} mm > {tol.e_seg_one_step_mm:.6f} mm "
                    f"at line {ref_ev.line_number} (ref_de={ref_ev.de_mm:.6f}, x3g_de={dec_ev.de_mm:.6f})."
                )

            # Criterion E4: Feedrate & DDA accuracy
            f_ref = ref_ev.clamped_feedrate_mm_s
            f_err = abs(dec_ev.feedrate_mm_s - f_ref)
            if f_err > metrics.max_feedrate_mult64_err_mm_s:
                metrics.max_feedrate_mult64_err_mm_s = f_err
            if collect_series:
                f_errs.append(dec_ev.feedrate_mm_s - f_ref)
            if f_ref >= (1.0 / 64.0) and f_err > tol.feedrate_mult64_abs_mm_s:
                add_violation(
                    f"E4 feedrate_mult64 error {f_err:.6f} mm/s > {tol.feedrate_mult64_abs_mm_s:.6f} mm/s "
                    f"at line {ref_ev.line_number} (ref={f_ref:.4f}, x3g={dec_ev.feedrate_mm_s:.4f})."
                )

            if ref_ev.distance_mm > 1e-12 and dec_ev.master_steps > 0 and f_ref > 0.0:
                dda_ideal = (dec_ev.master_steps * f_ref) / ref_ev.distance_mm
                if dda_ideal >= 1.0:
                    dda_err = abs(dec_ev.dda_rate - dda_ideal)
                    if dda_err > metrics.max_dda_rate_err_steps_s:
                        metrics.max_dda_rate_err_steps_s = dda_err
                    f_dda = (ref_ev.distance_mm * dec_ev.dda_rate) / dec_ev.master_steps
                    rel_v_err = abs(f_dda - f_ref) / f_ref
                    if rel_v_err > metrics.max_dda_rel_speed_err:
                        metrics.max_dda_rel_speed_err = rel_v_err
                    if dda_err > tol.dda_rate_abs_steps_s:
                        add_violation(
                            f"E4 dda_rate error {dda_err:.4f} steps/s > {tol.dda_rate_abs_steps_s:.4f} "
                            f"at line {ref_ev.line_number} (ideal={dda_ideal:.2f}, x3g={dec_ev.dda_rate})."
                        )

        elif isinstance(ref_ev, ReferenceControlEvent) and isinstance(dec_ev, DecodedControlEvent):
            metrics.control_events_compared += 1
            err_msg = _compare_control_event(idx, ref_ev, dec_ev, tol)
            if err_msg is not None:
                add_violation(err_msg)
        else:
            add_violation(
                f"Event #{idx} type ordering mismatch: G-code={type(ref_ev).__name__}, "
                f"X3G={type(dec_ev).__name__}."
            )

    # Final position & cumulative extrusion drift check (even after trailing zero-step moves)
    if gcode_sim.motion_events and x3g_sim.motion_events:
        last_ref = gcode_sim.motion_events[-1]
        last_dec = x3g_sim.motion_events[-1]
        if last_ref.x_known:
            metrics.final_x_err_mm = abs(last_dec.x_mm - gcode_sim.final_x_mm)
            if metrics.final_x_err_mm > tol.x_half_step_mm + 1e-6:
                add_violation(
                    f"E1 final X drift {metrics.final_x_err_mm:.6f} mm > {tol.x_half_step_mm:.6f} mm."
                )
        if last_ref.y_known:
            metrics.final_y_err_mm = abs(last_dec.y_mm - gcode_sim.final_y_mm)
            if metrics.final_y_err_mm > tol.y_half_step_mm + 1e-6:
                add_violation(
                    f"E1 final Y drift {metrics.final_y_err_mm:.6f} mm > {tol.y_half_step_mm:.6f} mm."
                )
        if last_ref.z_known:
            metrics.final_z_err_mm = abs(last_dec.z_mm - gcode_sim.final_z_mm)
            if metrics.final_z_err_mm > tol.z_half_step_mm + 1e-6:
                add_violation(
                    f"E1 final Z drift {metrics.final_z_err_mm:.6f} mm > {tol.z_half_step_mm:.6f} mm."
                )
    metrics.final_e_cum_err_mm = abs(x3g_sim.final_e_cum_mm - gcode_sim.final_e_cum_mm)
    if metrics.final_e_cum_err_mm > tol.e_cum_half_step_mm + 1e-6:
        add_violation(
            f"E2 final cumulative E drift {metrics.final_e_cum_err_mm:.6f} mm > "
            f"{tol.e_cum_half_step_mm:.6f} mm."
        )

    # Criterion E6: Total estimated print time
    metrics.time_abs_err_s = abs(x3g_sim.total_time_s - gcode_sim.total_time_s)
    if gcode_sim.total_time_s > 0.0:
        metrics.time_rel_err = metrics.time_abs_err_s / gcode_sim.total_time_s
    allowed_time_err = max(tol.time_abs_floor_s, tol.time_rel_tol * gcode_sim.total_time_s)
    if metrics.time_abs_err_s > allowed_time_err:
        add_violation(
            f"E6 total print time error {metrics.time_abs_err_s:.4f} s ({metrics.time_rel_err*100:.3f}%) "
            f"exceeds allowed {allowed_time_err:.4f} s "
            f"(G-code={gcode_sim.total_time_s:.3f} s, X3G={x3g_sim.total_time_s:.3f} s)."
        )

    return EquivalenceReport(
        passed=len(violations) == 0,
        profile_id=profile.profile_id,
        tolerances=tol,
        metrics=metrics,
        violations=violations,
        x_errors_um=x_errs_um,
        y_errors_um=y_errs_um,
        z_errors_um=z_errs_um,
        e_cum_errors_um=e_cum_errs_um,
        e_seg_errors_um=e_seg_errs_um,
        feedrate_errors_mm_s=f_errs,
    )


def check_equivalence(
    gcode_lines: Iterable[str],
    x3g_bytes: bytes | bytearray | memoryview,
    profile: MachineProfile = REPLICATOR_2,
    *,
    framed: bool = False,
    require_build_notifications: bool = True,
    collect_series: bool = False,
) -> tuple[EquivalenceReport, GCodeSimulationResult, X3GSimulationResult]:
    """Run both independent simulators and verify equivalence under E1-E8."""
    g_sim = simulate_gcode(gcode_lines, profile=profile)
    x_sim = simulate_x3g(x3g_bytes, profile=profile, framed=framed)
    report = compare_simulations(
        g_sim,
        x_sim,
        profile=profile,
        require_build_notifications=require_build_notifications,
        collect_series=collect_series,
    )
    return report, g_sim, x_sim
