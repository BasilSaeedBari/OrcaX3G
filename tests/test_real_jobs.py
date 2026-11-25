"""End-to-end equivalence, determinism, and artifact tests for 5 real OrcaSlicer v2.5.2 jobs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orca_x3g import (
    ConversionOptions,
    REPLICATOR_2,
    REPLICATOR_2_FACTORY,
    convert_lines,
)
from orca_x3g.equivalence import check_equivalence

REAL_JOBS_DIR = Path(__file__).parent / "fixtures" / "real_jobs"
JOB_NAMES = [
    "01_calibration_cube",
    "02_thin_vase",
    "03_retraction_towers",
    "04_large_footprint",
    "05_overhang_supports",
]


@pytest.mark.parametrize("job_name", JOB_NAMES)
def test_real_job_byte_exact_determinism_and_equivalence(job_name: str) -> None:
    job_dir = REAL_JOBS_DIR / job_name
    gcode_path = job_dir / f"{job_name}.gcode"
    x3g_path = job_dir / f"{job_name}.x3g"
    json_report_path = job_dir / "equivalence_report.json"
    md_report_path = job_dir / "equivalence_report.md"
    overlay_plot_path = job_dir / "layer_overlay.png"
    hist_plot_path = job_dir / "error_histogram.png"

    assert gcode_path.is_file(), f"Missing fixture {gcode_path}"
    assert x3g_path.is_file(), f"Missing fixture {x3g_path}"
    assert json_report_path.is_file(), f"Missing report {json_report_path}"
    assert md_report_path.is_file(), f"Missing report {md_report_path}"
    assert overlay_plot_path.is_file() and overlay_plot_path.stat().st_size > 1000
    assert hist_plot_path.is_file() and hist_plot_path.stat().st_size > 1000

    gcode_lines = gcode_path.read_text(encoding="utf-8").splitlines()
    # Confirm fixture was genuinely sliced by OrcaSlicer 2.5.2
    assert any("OrcaSlicer 2.5.2" in ln for ln in gcode_lines[:30])

    # Re-convert with default r2 profile and verify byte-identical determinism (Criterion E8)
    opts = ConversionOptions(profile=REPLICATOR_2, build_name=job_name[:24])
    res1 = convert_lines(gcode_lines, opts)
    res2 = convert_lines(gcode_lines, opts)
    assert res1.x3g_bytes == res2.x3g_bytes
    assert res1.x3g_bytes == x3g_path.read_bytes()

    # Independent equivalence proof (Criteria E1-E7)
    rep, _, _ = check_equivalence(gcode_lines, res1.x3g_bytes, profile=REPLICATOR_2)
    assert rep.passed, f"{job_name} equivalence failed: {rep.violations[:5]}"
    assert rep.metrics.x3g_motion_total > 1000

    # Verify checked-in JSON report matches passing state
    saved = json.loads(json_report_path.read_text(encoding="utf-8"))
    assert saved["passed"] is True
    assert saved["metrics"]["x3g_motion_total"] == rep.metrics.x3g_motion_total

    # Also verify r2_factory profile (88.573186 steps/mm) passes full equivalence
    res_fac = convert_lines(
        gcode_lines,
        ConversionOptions(profile=REPLICATOR_2_FACTORY, build_name=job_name[:24]),
    )
    rep_fac, _, _ = check_equivalence(gcode_lines, res_fac.x3g_bytes, profile=REPLICATOR_2_FACTORY)
    assert rep_fac.passed, f"{job_name} r2_factory failed: {rep_fac.violations[:5]}"
