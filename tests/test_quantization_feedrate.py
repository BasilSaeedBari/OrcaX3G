"""Unit tests for deterministic step quantization, edge cases, and feedrate/DDA calculations."""

from __future__ import annotations

import math

import pytest

from orca_x3g import (
    ConversionOptions,
    REPLICATOR_2,
    REPLICATOR_2_FACTORY,
    ValueRangeError,
    convert_text,
    round_half_away,
)
from orca_x3g.decoder import simulate_x3g
from orca_x3g.equivalence import check_equivalence


class TestRoundHalfAway:
    def test_exact_half_boundaries_symmetric(self) -> None:
        assert round_half_away(0.0) == 0
        assert round_half_away(-0.0) == 0
        assert round_half_away(0.5) == 1
        assert round_half_away(-0.5) == -1
        assert round_half_away(1.5) == 2
        assert round_half_away(-1.5) == -2
        assert round_half_away(2.5) == 3
        assert round_half_away(-2.5) == -3
        assert round_half_away(0.4999999) == 0
        assert round_half_away(-0.4999999) == 0


class TestQuantizationEdgeCases:
    def test_negative_coordinates_and_sign_inversion_on_extruder(self) -> None:
        gcode = "\n".join(
            [
                "G90",
                "M82",
                "G92 X0 Y0 Z0 E0",
                "G1 X-50.123 Y-25.789 Z1.25 E2.5 F1800",
                "G1 E1.2 F1500",  # retraction (-1.3 mm)
            ]
        )
        res = convert_text(gcode)
        sim = simulate_x3g(res.x3g_bytes, REPLICATOR_2)
        assert len(sim.motion_events) == 2
        m0, m1 = sim.motion_events
        assert m0.x_steps == round_half_away(-50.123 * REPLICATOR_2.x.steps_per_mm)
        assert m0.y_steps == round_half_away(-25.789 * REPLICATOR_2.y.steps_per_mm)
        assert m0.z_steps == 500
        # Extrude forward (+2.5 mm) MUST produce negative A steps on Replicator 2!
        assert m0.da_steps == -round_half_away(2.5 * REPLICATOR_2.extruder_a.steps_per_mm)
        assert m0.da_steps < 0
        # Retract (-1.3 mm) MUST produce positive A steps!
        assert m1.da_steps > 0

        rep, _, _ = check_equivalence(gcode.splitlines(), res.x3g_bytes, REPLICATOR_2)
        assert rep.passed, rep.violations

    def test_sub_step_moves_accumulate_without_losing_residual(self) -> None:
        # Each move is +0.004 mm in X (~0.3556 steps at 88.888889 steps/mm).
        # Individually each move is < 0.5 step, so naive per-segment rounding would stay at 0!
        # Over 100 moves (+0.400 mm = 35.5556 -> 36 steps), zero-drift tracking MUST reach 36 steps.
        lines = ["G90", "M82", "G92 X0 Y0 Z0 E0", "G1 F1200"]
        for i in range(1, 101):
            lines.append(f"G1 X{i * 0.004:.6f} E{i * 0.003:.6f}")
        gcode = "\n".join(lines)
        res = convert_text(gcode)
        assert res.summary.zero_step_moves_skipped > 0
        sim = simulate_x3g(res.x3g_bytes, REPLICATOR_2)
        assert sim.final_x_steps == round_half_away(0.400 * REPLICATOR_2.x.steps_per_mm)
        assert abs(sim.final_e_cum_mm - 0.300) <= 0.5 / REPLICATOR_2.extruder_a.steps_per_mm + 1e-9

        rep, _, _ = check_equivalence(lines, res.x3g_bytes, REPLICATOR_2)
        assert rep.passed, rep.violations

    def test_zero_length_move_is_skipped_cleanly(self) -> None:
        gcode = "G90\nG92 X0 Y0 Z0 E0\nG1 X10 Y10 F3000\nG1 X10 Y10\nG1 X20 Y10"
        res = convert_text(gcode)
        assert res.summary.motion_commands == 2
        assert res.summary.zero_step_moves_skipped == 1

    def test_r2_vs_r2_factory_steps_per_mm(self) -> None:
        gcode = "G90\nG92 X0 Y0 Z0 E0\nG1 X100 Y50 F3000"
        res_r2 = convert_text(gcode, ConversionOptions(profile=REPLICATOR_2))
        res_fac = convert_text(gcode, ConversionOptions(profile=REPLICATOR_2_FACTORY))
        sim_r2 = simulate_x3g(res_r2.x3g_bytes, REPLICATOR_2)
        sim_fac = simulate_x3g(res_fac.x3g_bytes, REPLICATOR_2_FACTORY)
        assert sim_r2.final_x_steps == 8889
        assert sim_fac.final_x_steps == 8857
        assert check_equivalence(gcode.splitlines(), res_r2.x3g_bytes, REPLICATOR_2)[0].passed
        assert check_equivalence(gcode.splitlines(), res_fac.x3g_bytes, REPLICATOR_2_FACTORY)[0].passed

    def test_int32_step_overflow_rejected(self) -> None:
        # Disable strict bounds to test int32 step guard specifically (2^31 / 88.888889 ~ 24.16e6 mm)
        gcode = "G90\nG92 X0 Y0 Z0 E0\nG1 X30000000 F3000"
        with pytest.raises(ValueRangeError, match="32-bit"):
            convert_text(gcode, ConversionOptions(strict_bounds=False))


class TestFeedrateAndDDACalculation:
    @pytest.mark.parametrize(
        "move_line,expected_clamped_f_mm_min,expect_clamp",
        [
            # Normal XY move: 3000 mm/min = 50 mm/s (below 18000 mm/min)
            ("G1 X10 Y0 F3000", 3000.0, False),
            # Excessive XY move: 24000 mm/min clamped to 18000 mm/min (300 mm/s)
            ("G1 X10 Y0 F24000", 18000.0, True),
            # Excessive Z move: 3000 mm/min clamped to 1170 mm/min (19.5 mm/s)
            ("G1 Z5 F3000", 1170.0, True),
            # Excessive E-only retraction: 3600 mm/min clamped to 1600 mm/min
            ("G1 E-2 F3600", 1600.0, True),
            # 45-deg XY move at 20000 mm/min: vx = vy = 20000/sqrt(2) = 14142 <= 18000 (not clamped!)
            ("G1 X10 Y10 F20000", 20000.0, False),
            # Diagonal XZ move where Z limits total speed: dx=3, dz=4 -> d=5, vz = 0.8*F <= 1170 => F <= 1462.5
            ("G1 X3 Z4 F3000", 1462.5, True),
        ],
    )
    def test_feedrate_clamping_and_dda_table(
        self, move_line: str, expected_clamped_f_mm_min: float, expect_clamp: bool
    ) -> None:
        gcode = f"G90\nM82\nG92 X0 Y0 Z0 E0\n{move_line}"
        res = convert_text(gcode)
        assert (res.summary.feedrate_clamps > 0) is expect_clamp
        sim = simulate_x3g(res.x3g_bytes, REPLICATOR_2)
        ev = sim.motion_events[0]
        expected_f_s = expected_clamped_f_mm_min / 60.0
        assert ev.feedrate_mult64 == round_half_away(expected_f_s * 64.0)
        expected_dda = round_half_away(ev.master_steps * expected_f_s / ev.distance_mm)
        assert abs(ev.dda_rate - expected_dda) <= 1

        rep, _, _ = check_equivalence(gcode.splitlines(), res.x3g_bytes, REPLICATOR_2)
        assert rep.passed, rep.violations
