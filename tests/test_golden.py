"""Golden tests covering hand-written G-code programs and exact expected decoded event streams."""

from __future__ import annotations

import math

from orca_x3g import ConversionOptions, REPLICATOR_2, convert_text
from orca_x3g.decoder import DecodedControlEvent, DecodedMotionEvent, simulate_x3g
from orca_x3g.equivalence import check_equivalence


def test_golden_single_line() -> None:
    gcode = "G21\nG90\nM82\nG92 X0 Y0 Z0 E0\nG1 X10 Y0 Z0.2 E0.5 F1800"
    res = convert_text(gcode)
    sim = simulate_x3g(res.x3g_bytes, REPLICATOR_2)

    assert sim.build_started and sim.build_ended
    assert len(sim.motion_events) == 1
    ev = sim.motion_events[0]
    assert ev.x_steps == 889
    assert ev.y_steps == 0
    assert ev.z_steps == 80
    assert ev.da_steps == -48
    assert check_equivalence(gcode.splitlines(), res.x3g_bytes, REPLICATOR_2)[0].passed


def test_golden_square_with_temp_fan_retraction_and_g92_e0() -> None:
    gcode = "\n".join(
        [
            "M136",
            "G21",
            "G90",
            "M82",
            "G162 X Y F2500",
            "G161 Z F1100",
            "M132 X Y Z A B",
            "G130 X118 Y118 Z40 A118 B118",
            "T0",
            "M104 S210 T0",
            "M109 S210 T0",
            "G92 E0",
            "G1 X0 Y0 Z0.2 F3000",
            "G1 X20 Y0 E1.0 F1800",
            "G1 X20 Y20 E2.0 F1800",
            "M106 S180",  # PWM fan -> ON + warning
            "G1 X0 Y20 E3.0 F1800",
            "G1 X0 Y0 E4.0 F1800",
            "G92 E0",
            "G1 E-1.3 F1500",  # retract
            "G1 Z1.0 F1100",
            "G1 E0.0 F1500",  # unretract
            "M107",
            "M104 S0 T0",
            "M18",
            "M72 P1",
            "M137",
        ]
    )
    res = convert_text(gcode)
    assert any("quantized to binary ON" in w for w in res.summary.warnings)

    sim = simulate_x3g(res.x3g_bytes, REPLICATOR_2)
    kinds = [
        ev.kind if isinstance(ev, DecodedControlEvent) else "move"
        for ev in sim.events
    ]
    assert kinds == [
        "build_start",
        "home",  # G162 X Y
        "home",  # G161 Z
        "recall_home",  # M132
        "set_pot", "set_pot", "set_pot", "set_pot", "set_pot",
        "change_tool",
        "set_temp",  # M104
        "set_temp",  # M109 set
        "wait_temp",  # M109 wait
        "move",  # X0 Y0 Z0.2
        "move",  # X20 Y0
        "move",  # X20 Y20
        "fan",   # M106 -> ON
        "move",  # X0 Y20
        "move",  # X0 Y0
        "move",  # retract -1.3
        "move",  # Z1.0
        "move",  # unretract +1.3
        "fan",   # M107 -> OFF
        "set_temp",  # M104 S0
        "enable_axes",  # M18
        "queue_song",  # M72 P1
        "build_end",
    ]
    # Note: G92 E0 emitted ZERO Command 140 packets!
    assert not any(p.opcode == 140 for p in sim.packets)
    # Net extrusion = 4.0 - 1.3 + 1.3 = 4.0 mm
    assert abs(sim.final_e_cum_mm - 4.0) <= 0.5 / REPLICATOR_2.extruder_a.steps_per_mm + 1e-9

    rep, _, _ = check_equivalence(gcode.splitlines(), res.x3g_bytes, REPLICATOR_2)
    assert rep.passed, rep.violations


def test_golden_spiral_and_relative_modes() -> None:
    lines = ["G21", "G90", "M83", "G92 X0 Y0 Z0 E0", "G1 F1800"]
    # 72-segment rising spiral (2 full turns, Z from 0.2 to 2.2 mm, M83 relative extrusion)
    for i in range(1, 73):
        theta = (2.0 * math.pi * i) / 36.0
        x = 15.0 * math.cos(theta)
        y = 15.0 * math.sin(theta)
        z = 0.2 + (2.0 * i) / 72.0
        lines.append(f"G1 X{x:.4f} Y{y:.4f} Z{z:.4f} E0.085")

    # Switch to G91 relative XYZ and back to G90 + M82
    lines.extend(
        [
            "G91",
            "G1 X5 Y-5 Z0.5 E0.25 F1500",
            "G1 X-5 Y5 Z0.5 E0.25",
            "G90",
            "M82",
            "G92 E0",
            "G1 X0 Y0 Z3.5 E0.5 F1800",
        ]
    )
    gcode = "\n".join(lines)
    res = convert_text(gcode)
    rep, _, _ = check_equivalence(lines, res.x3g_bytes, REPLICATOR_2)
    assert rep.passed, rep.violations
    assert rep.metrics.x3g_motion_total == 75


def test_golden_dwell_homing_m600_and_sailfish_commands() -> None:
    gcode = "\n".join(
        [
            "G28",
            "M132 X Y Z",
            "G90",
            "M82",
            "G92 E0",
            "M320",
            "M322 Z1.5",
            "G1 X10 Y10 Z0.2 E0.4 F2400",
            "G4 P500",
            "G4 S1.25",
            "M70 P3 (Mid-print)",
            "M73 P50 R10",
            "M600",
            "M126",
            "G1 X20 Y10 Z0.4 E0.8 F2400",
            "M127",
            "M321",
            "M0 P200",
            "M0",
            "M17 X Y",
        ]
    )
    res = convert_text(gcode, ConversionOptions(firmware="sailfish"))
    sim = simulate_x3g(res.x3g_bytes, REPLICATOR_2)

    ctrl_kinds = [ev.kind for ev in sim.control_events if ev.kind not in ("build_start", "build_end")]
    assert ctrl_kinds == [
        "home",  # G28 XY max
        "home",  # G28 Z min
        "recall_home",
        "accel_toggle",  # M320
        "pause_at_z",    # M322
        "dwell",         # G4 P500
        "dwell",         # G4 S1.25
        "display_message",  # M70
        "build_percent",    # M73
        "display_message",  # M600 banner
        "pause",            # M600 wait button
        "fan",              # M126
        "fan",              # M127
        "accel_toggle",     # M321
        "dwell",            # M0 P200
        "pause",            # M0 wait button
        "enable_axes",      # M17 X Y
    ]
    rep, _, _ = check_equivalence(gcode.splitlines(), res.x3g_bytes, REPLICATOR_2)
    assert rep.passed, rep.violations
