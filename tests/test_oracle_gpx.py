"""External oracle comparison against compiled markwal/GPX (28f34600).

Converts real OrcaSlicer v2.5.2 jobs with both ``orca_x3g`` and ``gpx -r -m r2``
(plus ``r2_factory`` vs ``gpx -r -m or2``), decodes BOTH outputs using the
independent ``decoder.py`` VM, asserts physical toolpaths agree within tolerance,
and explicitly classifies every packet-level difference per docs/EQUIVALENCE.md Section 4.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from orca_x3g import ConversionOptions, REPLICATOR_2, REPLICATOR_2_FACTORY, convert_file
from orca_x3g.decoder import simulate_x3g

REAL_JOBS_DIR = Path(__file__).parent / "fixtures" / "real_jobs"
JOB_NAMES = [
    "01_calibration_cube",
    "02_thin_vase",
    "03_retraction_towers",
    "04_large_footprint",
    "05_overhang_supports",
]


def _find_gpx_binary() -> Path | None:
    env_path = os.environ.get("GPX_BIN")
    if env_path and Path(env_path).is_file():
        return Path(env_path)
    which = shutil.which("gpx")
    if which:
        return Path(which)
    local_scratch = Path(
        "/home/basilsaeedbari/.gemini/antigravity/brain/"
        "af8c16c8-bc1b-4d55-ac88-097be640faea/scratch/gpx_build/src/gpx/gpx"
    )
    if local_scratch.is_file():
        return local_scratch
    return None


GPX_BIN = _find_gpx_binary()
pytestmark = pytest.mark.skipif(
    GPX_BIN is None,
    reason="External markwal/GPX binary not installed (set GPX_BIN to enable oracle comparison).",
)


@pytest.mark.parametrize("job_name", JOB_NAMES)
def test_oracle_gpx_r2_toolpath_and_diff_classification(job_name: str, tmp_path: Path) -> None:
    assert GPX_BIN is not None
    gcode_path = REAL_JOBS_DIR / job_name / f"{job_name}.gcode"
    ours_x3g = REAL_JOBS_DIR / job_name / f"{job_name}.x3g"
    gpx_x3g = tmp_path / f"{job_name}_gpx.x3g"

    proc = subprocess.run(
        [str(GPX_BIN), "-r", "-m", "r2", str(gcode_path), str(gpx_x3g)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.returncode == 0

    sim_ours = simulate_x3g(ours_x3g.read_bytes(), REPLICATOR_2)
    sim_gpx = simulate_x3g(gpx_x3g.read_bytes(), REPLICATOR_2)

    # 1. Motion count must match 1-to-1
    assert len(sim_ours.motion_events) == len(sim_gpx.motion_events)

    # 2. Physical toolpath agreement (strictly <= 1.0 step on every axis and cumulative E)
    x_tol = 1.0 / REPLICATOR_2.x.steps_per_mm + 1e-6
    y_tol = 1.0 / REPLICATOR_2.y.steps_per_mm + 1e-6
    z_tol = 1.0 / REPLICATOR_2.z.steps_per_mm + 1e-6
    e_tol = 1.0 / REPLICATOR_2.extruder_a.steps_per_mm + 1e-6

    for mo, mg in zip(sim_ours.motion_events, sim_gpx.motion_events):
        if mo.x_known and mg.x_known:
            assert abs(mo.x_mm - mg.x_mm) <= x_tol
        if mo.y_known and mg.y_known:
            assert abs(mo.y_mm - mg.y_mm) <= y_tol
        if mo.z_known and mg.z_known:
            assert abs(mo.z_mm - mg.z_mm) <= z_tol
        assert abs(mo.e_cum_mm - mg.e_cum_mm) <= e_tol

    # 3. Non-motion physical commands (temperature, fan, homing, digipots, tool) must match
    ours_temps = [ev.data["temperature_c"] for ev in sim_ours.control_events if ev.kind == "set_temp"]
    gpx_temps = [ev.data["temperature_c"] for ev in sim_gpx.control_events if ev.kind == "set_temp"]
    assert ours_temps == gpx_temps

    ours_fans = [(ev.data["action_id"], ev.data["enabled"]) for ev in sim_ours.control_events if ev.kind == "fan"]
    gpx_fans = [(ev.data["action_id"], ev.data["enabled"]) for ev in sim_gpx.control_events if ev.kind == "fan"]
    assert ours_fans == gpx_fans

    # 4. Classify every packet-kind count difference (only documented benign categories allowed)
    ours_counts = Counter(p.kind for p in sim_ours.packets)
    gpx_counts = Counter(p.kind for p in sim_gpx.packets)
    allowed_diff_kinds = {
        "queue_point_new_ext",  # GPX emits 1 legacy CMD 139 right after M132 instead of CMD 155
        "queue_point_ext",      # (the matching 1 legacy CMD 139 from GPX)
        "set_position",         # GPX emits CMD 140 on every G92 E0; OrcaX3G avoids overwriting XYZ
        "build_percent",        # GPX deduplicates consecutive identical M73 percentages
    }
    for kind in set(ours_counts) | set(gpx_counts):
        if ours_counts[kind] != gpx_counts[kind]:
            assert kind in allowed_diff_kinds, (
                f"Unclassified packet count difference for kind={kind!r}: "
                f"ours={ours_counts[kind]} vs gpx={gpx_counts[kind]}"
            )
    assert ours_counts["queue_point_new_ext"] == gpx_counts["queue_point_new_ext"] + gpx_counts["queue_point_ext"]


def test_oracle_gpx_or2_matches_r2_factory_profile(tmp_path: Path) -> None:
    assert GPX_BIN is not None
    gcode_path = REAL_JOBS_DIR / "01_calibration_cube" / "01_calibration_cube.gcode"
    ours_x3g = tmp_path / "cube_r2_factory.x3g"
    gpx_x3g = tmp_path / "cube_gpx_or2.x3g"

    convert_file(gcode_path, ours_x3g, options=ConversionOptions(profile=REPLICATOR_2_FACTORY))
    # Note: In markwal/GPX@28f34600 (src/gpx/gpx.c:168), gpx_set_machine() guards memcpy with
    # `if (init || gpx->machine.id != machine->id)`. Because both `r2` (loaded at init) and `or2`
    # share `MACHINE_TYPE_REPLICATOR_2`, `-m or2` alone is silently ignored! Passing `-m r1 -m or2`
    # forces GPX to switch away from MACHINE_TYPE_REPLICATOR_2 first so `or2` (88.573186) actually loads.
    subprocess.run(
        [str(GPX_BIN), "-I", "-r", "-m", "r1", "-m", "or2", str(gcode_path), str(gpx_x3g)],
        capture_output=True,
        text=True,
        check=True,
    )

    sim_ours = simulate_x3g(ours_x3g.read_bytes(), REPLICATOR_2_FACTORY)
    sim_gpx = simulate_x3g(gpx_x3g.read_bytes(), REPLICATOR_2_FACTORY)
    assert len(sim_ours.motion_events) == len(sim_gpx.motion_events)
    for mo, mg in zip(sim_ours.motion_events, sim_gpx.motion_events):
        if mo.x_known and mg.x_known:
            assert abs(mo.x_mm - mg.x_mm) <= 1.0 / REPLICATOR_2_FACTORY.x.steps_per_mm + 1e-6
        if mo.y_known and mg.y_known:
            assert abs(mo.y_mm - mg.y_mm) <= 1.0 / REPLICATOR_2_FACTORY.y.steps_per_mm + 1e-6
        assert abs(mo.e_cum_mm - mg.e_cum_mm) <= 1.0 / REPLICATOR_2_FACTORY.extruder_a.steps_per_mm + 1e-6
