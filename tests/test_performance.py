"""Performance & peak memory benchmark on small, medium, and large real OrcaSlicer jobs."""

from __future__ import annotations

import time
import tracemalloc
from pathlib import Path

from orca_x3g import ConversionOptions, REPLICATOR_2, convert_file
from orca_x3g.cli import main as cli_main

REAL_JOBS_DIR = Path(__file__).parent / "fixtures" / "real_jobs"


def test_performance_and_memory_small_medium_large(tmp_path: Path) -> None:
    benchmarks = [
        ("small", "01_calibration_cube", 2.0, 25.0),
        ("medium", "03_retraction_towers", 3.0, 40.0),
        ("large", "04_large_footprint", 5.0, 80.0),
    ]
    for tier, job, max_seconds, max_peak_mb in benchmarks:
        gcode_path = REAL_JOBS_DIR / job / f"{job}.gcode"
        out_path = tmp_path / f"{job}.x3g"

        # Pass 1: Measure pure wall-clock throughput without tracemalloc overhead
        t0 = time.perf_counter()
        res = convert_file(
            gcode_path,
            out_path,
            options=ConversionOptions(profile=REPLICATOR_2, build_name=job[:24]),
        )
        elapsed = time.perf_counter() - t0
        moves_per_sec = res.summary.motion_commands / max(elapsed, 1e-6)

        # Pass 2: Measure peak Python heap memory via tracemalloc
        tracemalloc.start()
        convert_file(
            gcode_path,
            out_path,
            options=ConversionOptions(profile=REPLICATOR_2, build_name=job[:24]),
        )
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_mb = peak_bytes / (1024 * 1024)

        assert elapsed < max_seconds, f"{tier} ({job}) took {elapsed:.3f}s > {max_seconds}s"
        assert peak_mb < max_peak_mb, f"{tier} ({job}) peak RAM {peak_mb:.2f} MB > {max_peak_mb} MB"
        assert moves_per_sec > 5_000, f"{tier} ({job}) throughput too slow: {moves_per_sec:.0f} moves/s"


def test_cli_end_to_end_with_summary_json(tmp_path: Path) -> None:
    gcode_path = REAL_JOBS_DIR / "01_calibration_cube" / "01_calibration_cube.gcode"
    out_x3g = tmp_path / "cli_out.x3g"
    summary_json = tmp_path / "summary.json"

    rc = cli_main(
        [
            str(gcode_path),
            str(out_x3g),
            "--profile",
            "r2",
            "--firmware",
            "sailfish",
            "--summary-json",
            str(summary_json),
            "--quiet",
        ]
    )
    assert rc == 0
    assert out_x3g.is_file() and out_x3g.stat().st_size > 80_000
    assert summary_json.is_file()
