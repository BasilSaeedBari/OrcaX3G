"""1,000,000-line synthetic print drift test proving zero cumulative XYZ & E error.

Verifies Criterion E1 and E2 over 1,000,000 consecutive irrational/fractional
G-code moves with periodic G92 E0 resets, checking that final position and
cumulative extrusion errors remain <= 0.5 / steps_per_mm (zero drift).
"""

from __future__ import annotations

import math
import time

from orca_x3g import ConversionOptions, GCodePlanner, REPLICATOR_2
from orca_x3g.commands import QueuePointNewExt
from orca_x3g.encoder import encode_command


def test_one_million_line_zero_cumulative_drift() -> None:
    """Stream 1,000,000 moves and prove cumulative XYZ and E error stays <= 0.5 step."""
    n_moves = 1_000_000
    profile = REPLICATOR_2
    planner = GCodePlanner(ConversionOptions(profile=profile))

    # Streaming generator to avoid allocating a 50 MB list of strings in RAM
    def gcode_stream():
        yield "G21"
        yield "G90"
        yield "M82"
        yield "G92 X0 Y0 Z0 E0"
        yield "G1 F3600"
        e_logical = 0.0
        # Irrational/fractional step increments designed to trigger worst-case rounding bias
        # if a planner were to accumulate rounded deltas instead of absolute targets.
        for i in range(1, n_moves + 1):
            # Smooth bounded oscillation inside [-100, 100] x [-60, 60] x [0.2, 100.2]
            phase = i * 0.001234567
            x = 100.0 * math.sin(phase)
            y = 60.0 * math.cos(phase * 1.314159)
            z = 0.2 + (i // 10_000) * 0.2007  # 100 layers with non-integer Z step
            de = 0.0033333  # ~0.3209 steps/move (sub-half-step!)
            e_logical += de
            yield f"G1 X{x:.6f} Y{y:.6f} Z{z:.6f} E{e_logical:.6f}"
            if i % 5000 == 0:
                e_logical = 0.0
                yield "G92 E0"

    t0 = time.perf_counter()
    # Stream-process line-by-line and independently track decoded step positions on the fly
    # so 1,000,000 moves complete rapidly in constant memory.
    from orca_x3g.parser import parse_gcode_line as parse_line

    x_steps_vm = 0
    y_steps_vm = 0
    z_steps_vm = 0
    a_steps_cum_vm = 0
    x3g_byte_count = 0
    motion_count = 0

    for lineno, raw in enumerate(gcode_stream(), start=1):
        parsed = parse_line(raw, lineno)
        planner.process_line(parsed)
        if planner.commands:
            for cmd in planner.commands:
                raw_bytes = encode_command(cmd)
                x3g_byte_count += len(raw_bytes)
                if isinstance(cmd, QueuePointNewExt):
                    motion_count += 1
                    # Replay command on independent VM step accumulator
                    if cmd.relative_axes & 0x01:
                        x_steps_vm += cmd.x
                    else:
                        x_steps_vm = cmd.x
                    if cmd.relative_axes & 0x02:
                        y_steps_vm += cmd.y
                    else:
                        y_steps_vm = cmd.y
                    if cmd.relative_axes & 0x04:
                        z_steps_vm += cmd.z
                    else:
                        z_steps_vm = cmd.z
                    if cmd.relative_axes & 0x08:
                        a_steps_cum_vm += cmd.a
                    else:
                        a_steps_cum_vm = cmd.a
            planner.commands.clear()

    planner.finalize()
    for cmd in planner.commands:
        x3g_byte_count += len(encode_command(cmd))
    planner.commands.clear()
    elapsed = time.perf_counter() - t0

    # Exact final ideal targets at i = 1,000,000
    final_phase = n_moves * 0.001234567
    expected_x = float(f"{100.0 * math.sin(final_phase):.6f}")
    expected_y = float(f"{60.0 * math.cos(final_phase * 1.314159):.6f}")
    expected_z = float(f"{0.2 + (n_moves // 10_000) * 0.2007:.6f}")
    expected_e = n_moves * 0.0033333

    decoded_x = x_steps_vm / profile.x.steps_per_mm
    decoded_y = y_steps_vm / profile.y.steps_per_mm
    decoded_z = z_steps_vm / profile.z.steps_per_mm
    decoded_e = (-a_steps_cum_vm) / profile.extruder_a.steps_per_mm

    err_x = abs(decoded_x - expected_x)
    err_y = abs(decoded_y - expected_y)
    err_z = abs(decoded_z - expected_z)
    err_e = abs(decoded_e - expected_e)

    # Zero cumulative drift: after 1,000,000 moves, EVERY axis error stays <= 0.5 step!
    assert err_x <= (0.5 / profile.x.steps_per_mm) + 1e-6, f"X drift {err_x} mm"
    assert err_y <= (0.5 / profile.y.steps_per_mm) + 1e-6, f"Y drift {err_y} mm"
    assert err_z <= (0.5 / profile.z.steps_per_mm) + 1e-6, f"Z drift {err_z} mm"
    # Allow tiny float64 summation noise (1e-5 mm over 200 resets of 5000 float64 adds)
    assert err_e <= (0.5 / profile.extruder_a.steps_per_mm) + 1e-4, f"E drift {err_e} mm"

    assert motion_count > 990_000
    assert x3g_byte_count > 31_000_000
    assert elapsed < 90.0, f"1M-line streaming took {elapsed:.1f}s"
