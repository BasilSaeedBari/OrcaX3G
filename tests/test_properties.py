"""Hypothesis property-based tests proving equivalence, zero drift, and determinism."""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings, strategies as st

from orca_x3g import ConversionOptions, REPLICATOR_2, REPLICATOR_2_FACTORY, convert_lines
from orca_x3g.decoder import decode_packets
from orca_x3g.equivalence import check_equivalence


@st.composite
def valid_gcode_program(draw: st.DrawFn) -> list[str]:
    """Generate a random valid Replicator 2 G-code program mixing modes and control events."""
    lines = ["G21", "G90", "M82", "G92 X0 Y0 Z0 E0", "G1 F2400"]
    x = 0.0
    y = 0.0
    z = 0.2
    e_logical = 0.0
    rel_xyz = False
    rel_e = False

    n_ops = draw(st.integers(min_value=5, max_value=80))
    for _ in range(n_ops):
        op = draw(
            st.sampled_from(
                [
                    "move",
                    "move",
                    "move",
                    "move",
                    "move",
                    "substep_move",
                    "retract",
                    "g92_e0",
                    "toggle_rel_e",
                    "toggle_rel_xyz",
                    "temp",
                    "fan",
                    "dwell",
                ]
            )
        )
        if op == "move":
            nx = draw(st.floats(min_value=-130.0, max_value=130.0, allow_nan=False, allow_infinity=False))
            ny = draw(st.floats(min_value=-70.0, max_value=70.0, allow_nan=False, allow_infinity=False))
            nz = draw(st.floats(min_value=0.0, max_value=140.0, allow_nan=False, allow_infinity=False))
            de = draw(st.floats(min_value=0.0, max_value=2.0, allow_nan=False, allow_infinity=False))
            feed = draw(st.floats(min_value=120.0, max_value=12000.0, allow_nan=False, allow_infinity=False))
            if rel_xyz:
                cmd = f"G1 X{nx - x:.4f} Y{ny - y:.4f} Z{nz - z:.4f} E{de:.4f} F{feed:.1f}"
                e_logical += de
            else:
                if rel_e:
                    cmd = f"G1 X{nx:.4f} Y{ny:.4f} Z{nz:.4f} E{de:.4f} F{feed:.1f}"
                    e_logical += de
                else:
                    e_logical += de
                    cmd = f"G1 X{nx:.4f} Y{ny:.4f} Z{nz:.4f} E{e_logical:.4f} F{feed:.1f}"
            x, y, z = nx, ny, nz
            lines.append(cmd)

        elif op == "substep_move":
            dx = draw(st.floats(min_value=-0.004, max_value=0.004, allow_nan=False, allow_infinity=False))
            dy = draw(st.floats(min_value=-0.004, max_value=0.004, allow_nan=False, allow_infinity=False))
            de = draw(st.floats(min_value=0.0, max_value=0.003, allow_nan=False, allow_infinity=False))
            nx = max(-140.0, min(140.0, x + dx))
            ny = max(-74.0, min(74.0, y + dy))
            if rel_xyz:
                lines.append(f"G1 X{nx - x:.5f} Y{ny - y:.5f} E{de:.5f}")
                e_logical += de
            else:
                if rel_e:
                    lines.append(f"G1 X{nx:.5f} Y{ny:.5f} E{de:.5f}")
                    e_logical += de
                else:
                    e_logical += de
                    lines.append(f"G1 X{nx:.5f} Y{ny:.5f} E{e_logical:.5f}")
            x, y = nx, ny

        elif op == "retract":
            de = draw(st.floats(min_value=-2.0, max_value=2.0, allow_nan=False, allow_infinity=False))
            feed = draw(st.floats(min_value=600.0, max_value=2400.0, allow_nan=False, allow_infinity=False))
            if rel_e or rel_xyz:
                e_logical += de
                lines.append(f"G1 E{de:.4f} F{feed:.1f}")
            else:
                e_logical += de
                lines.append(f"G1 E{e_logical:.4f} F{feed:.1f}")

        elif op == "g92_e0":
            e_logical = 0.0
            lines.append("G92 E0")

        elif op == "toggle_rel_e":
            rel_e = not rel_e
            lines.append("M83" if rel_e else "M82")

        elif op == "toggle_rel_xyz":
            rel_xyz = not rel_xyz
            lines.append("G91" if rel_xyz else "G90")

        elif op == "temp":
            t = draw(st.integers(min_value=0, max_value=250))
            lines.append(f"M104 S{t} T0")

        elif op == "fan":
            s = draw(st.sampled_from([0, 128, 255]))
            lines.append(f"M106 S{s}")

        elif op == "dwell":
            ms = draw(st.integers(min_value=0, max_value=2000))
            lines.append(f"G4 P{ms}")

    return lines


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(lines=valid_gcode_program(), use_factory=st.booleans())
def test_property_equivalence_determinism_and_framing(lines: list[str], use_factory: bool) -> None:
    profile = REPLICATOR_2_FACTORY if use_factory else REPLICATOR_2

    res1 = convert_lines(lines, ConversionOptions(profile=profile, framed=False))
    res2 = convert_lines(lines, ConversionOptions(profile=profile, framed=False))
    # Criterion E8: Determinism
    assert res1.x3g_bytes == res2.x3g_bytes

    # Criteria E1-E7: Full independent equivalence check
    rep, _, _ = check_equivalence(lines, res1.x3g_bytes, profile=profile)
    assert rep.passed, f"Equivalence violations: {rep.violations[:5]}"

    # Framed S3G round-trip produces identical decoded packets
    res_framed = convert_lines(lines, ConversionOptions(profile=profile, framed=True))
    pkts_raw = decode_packets(res1.x3g_bytes, framed=False)
    pkts_framed = decode_packets(res_framed.x3g_bytes, framed=True)
    assert len(pkts_raw) == len(pkts_framed)
    for pr, pf in zip(pkts_raw, pkts_framed):
        assert pr.opcode == pf.opcode
        assert pr.fields == pf.fields
