"""Equivalence report writers (JSON + Markdown) and matplotlib visual verification plots.

``matplotlib`` is imported lazily inside the plotting functions so the core
package remains 100% stdlib-only at runtime.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .decoder import X3GSimulationResult
from .equivalence import EquivalenceReport
from .gcode_sim import GCodeSimulationResult


def format_equivalence_markdown(
    job_name: str,
    report: EquivalenceReport,
    *,
    extra_metadata: dict[str, Any] | None = None,
) -> str:
    """Render a human-readable Markdown equivalence verification report."""
    m = report.metrics
    t = report.tolerances
    status = "PASS" if report.passed else "FAIL"

    lines = [
        f"# Equivalence Report: `{job_name}` — **{status}**",
        "",
        f"- **Machine profile:** `{report.profile_id}`",
        f"- **Build notices (153/154):** start=`{m.build_started}`, end=`{m.build_ended}`",
        f"- **Motions:** G-code total `{m.gcode_motion_total:,}` "
        f"(non-zero step `{m.gcode_nonzero_step_motions:,}`, sub-step skipped `{m.gcode_zero_step_motions:,}`), "
        f"X3G decoded `{m.x3g_motion_total:,}`",
        f"- **Control events verified in order:** `{m.control_events_compared:,}`",
    ]
    if extra_metadata:
        for k, v in extra_metadata.items():
            lines.append(f"- **{k}:** `{v}`")

    lines.extend(
        [
            "",
            "## Tolerance Verification Table",
            "",
            "| Criterion | Metric | Observed Max / Value | Allowed Bound | Status |",
            "|---|---|---|---|---|",
            f"| E1 | Max X position error | `{m.max_x_err_mm * 1000:.3f} µm` (`{m.max_x_err_mm:.6f} mm`) | `<= {t.x_half_step_mm * 1000:.3f} µm` | {'PASS' if m.max_x_err_mm <= t.x_half_step_mm else 'FAIL'} |",
            f"| E1 | Max Y position error | `{m.max_y_err_mm * 1000:.3f} µm` (`{m.max_y_err_mm:.6f} mm`) | `<= {t.y_half_step_mm * 1000:.3f} µm` | {'PASS' if m.max_y_err_mm <= t.y_half_step_mm else 'FAIL'} |",
            f"| E1 | Max Z position error | `{m.max_z_err_mm * 1000:.3f} µm` (`{m.max_z_err_mm:.6f} mm`) | `<= {t.z_half_step_mm * 1000:.3f} µm` | {'PASS' if m.max_z_err_mm <= t.z_half_step_mm else 'FAIL'} |",
            f"| E2 | Max cumulative extrusion error | `{m.max_e_cum_err_mm * 1000:.3f} µm` (`{m.max_e_cum_err_mm:.6f} mm`) | `<= {t.e_cum_half_step_mm * 1000:.3f} µm` | {'PASS' if m.max_e_cum_err_mm <= t.e_cum_half_step_mm else 'FAIL'} |",
            f"| E2 | Max per-segment extrusion error | `{m.max_e_seg_err_mm * 1000:.3f} µm` (`{m.max_e_seg_err_mm:.6f} mm`) | `<= {t.e_seg_one_step_mm * 1000:.3f} µm` | {'PASS' if m.max_e_seg_err_mm <= t.e_seg_one_step_mm else 'FAIL'} |",
            f"| E2 | Final cumulative filament (G-code vs X3G) | `{m.gcode_final_e_mm:.4f}` vs `{m.x3g_final_e_mm:.4f} mm` (Δ `{m.final_e_cum_err_mm * 1000:.3f} µm`) | `<= {t.e_cum_half_step_mm * 1000:.3f} µm` | {'PASS' if m.final_e_cum_err_mm <= t.e_cum_half_step_mm else 'FAIL'} |",
            f"| E3 | Non-zero-step motion count | `{m.x3g_motion_total:,}` vs `{m.gcode_nonzero_step_motions:,}` | Exact match | {'PASS' if m.x3g_motion_total == m.gcode_nonzero_step_motions else 'FAIL'} |",
            f"| E4 | Max `feedrate_mult64` error | `{m.max_feedrate_mult64_err_mm_s:.6f} mm/s` | `<= {t.feedrate_mult64_abs_mm_s:.6f} mm/s` | {'PASS' if m.max_feedrate_mult64_err_mm_s <= t.feedrate_mult64_abs_mm_s else 'FAIL'} |",
            f"| E4 | Max `dda_rate` error | `{m.max_dda_rate_err_steps_s:.4f} steps/s` (max rel speed `{m.max_dda_rel_speed_err * 100:.4f}%`) | `<= {t.dda_rate_abs_steps_s:.4f} steps/s` | {'PASS' if m.max_dda_rate_err_steps_s <= t.dda_rate_abs_steps_s else 'FAIL'} |",
            f"| E6 | Total est. time (G-code vs X3G) | `{m.gcode_total_time_s:.3f} s` vs `{m.x3g_total_time_s:.3f} s` (Δ `{m.time_abs_err_s:.4f} s`, `{m.time_rel_err * 100:.4f}%`) | `<= max(0.5 s, 0.5%)` | {'PASS' if m.time_abs_err_s <= max(t.time_abs_floor_s, t.time_rel_tol * m.gcode_total_time_s) else 'FAIL'} |",
            "",
        ]
    )

    if report.violations:
        lines.append(f"## Violations ({len(report.violations)})")
        lines.append("")
        for v in report.violations:
            lines.append(f"- {v}")
        lines.append("")
    else:
        lines.append("## Violations")
        lines.append("")
        lines.append("None — all formal equivalence criteria E1–E8 satisfied.")
        lines.append("")

    return "\n".join(lines)


def write_equivalence_reports(
    output_dir: str | Path,
    job_name: str,
    report: EquivalenceReport,
    *,
    extra_metadata: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    """Write ``equivalence_report.json`` and ``equivalence_report.md`` into ``output_dir``."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    json_payload = report.to_dict()
    json_payload["job_name"] = job_name
    if extra_metadata:
        json_payload["metadata"] = extra_metadata

    json_path = out / "equivalence_report.json"
    json_path.write_text(json.dumps(json_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    md_path = out / "equivalence_report.md"
    md_path.write_text(
        format_equivalence_markdown(job_name, report, extra_metadata=extra_metadata),
        encoding="utf-8",
    )
    return json_path, md_path


def _extract_extrusion_segments_by_layer(
    events: list[Any],
    *,
    is_x3g: bool,
) -> dict[float, list[tuple[float, float, float, float]]]:
    layers: dict[float, list[tuple[float, float, float, float]]] = {}
    prev_x: float | None = None
    prev_y: float | None = None
    for ev in events:
        if not (ev.x_known and ev.y_known and ev.z_known):
            continue
        x, y, z = ev.x_mm, ev.y_mm, round(ev.z_mm, 3)
        de = ev.de_mm
        if prev_x is not None and prev_y is not None and de > 1e-6:
            layers.setdefault(z, []).append((prev_x, prev_y, x, y))
        prev_x, prev_y = x, y
    return layers


def generate_equivalence_plots(
    output_dir: str | Path,
    job_name: str,
    report: EquivalenceReport,
    gcode_sim: GCodeSimulationResult,
    x3g_sim: X3GSimulationResult,
) -> tuple[Path, Path]:
    """Generate ``layer_overlay.png`` and ``error_histogram.png`` using matplotlib (Agg)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # --- Plot 1: Layer-by-layer toolpath overlay (G-code vs decoded X3G) ---
    g_layers = _extract_extrusion_segments_by_layer(gcode_sim.non_zero_step_motions, is_x3g=False)
    x_layers = _extract_extrusion_segments_by_layer(x3g_sim.motion_events, is_x3g=True)
    common_zs = sorted(set(g_layers.keys()) & set(x_layers.keys()))
    if not common_zs:
        common_zs = sorted(g_layers.keys())

    # Choose up to 3 representative layers (first, middle, upper)
    if len(common_zs) >= 3:
        sample_zs = [common_zs[0], common_zs[len(common_zs) // 2], common_zs[-1]]
    elif common_zs:
        sample_zs = common_zs
    else:
        sample_zs = [0.0]

    fig, axes = plt.subplots(1, len(sample_zs), figsize=(5.2 * len(sample_zs), 4.8), squeeze=False)
    fig.suptitle(f"{job_name}: G-code vs Decoded X3G Extrusion Overlay", fontsize=12, fontweight="bold")
    for col, z_val in enumerate(sample_zs):
        ax = axes[0][col]
        g_segs = [[(x0, y0), (x1, y1)] for x0, y0, x1, y1 in g_layers.get(z_val, [])]
        x_segs = [[(x0, y0), (x1, y1)] for x0, y0, x1, y1 in x_layers.get(z_val, [])]
        if g_segs:
            ax.add_collection(
                LineCollection(g_segs, colors="#1f77b4", linewidths=1.8, alpha=0.75, label="G-code ref")
            )
        if x_segs:
            ax.add_collection(
                LineCollection(
                    x_segs, colors="#ff7f0e", linewidths=0.8, linestyles="--", alpha=0.95, label="Decoded X3G"
                )
            )
        ax.autoscale()
        ax.set_aspect("equal", adjustable="datalim")
        ax.set_title(f"Z = {z_val:.2f} mm ({len(g_segs)} segs)")
        ax.set_xlabel("X (mm)")
        ax.set_ylabel("Y (mm)")
        ax.grid(True, alpha=0.25)
        ax.legend(loc="upper right", fontsize=8)

    fig.tight_layout()
    overlay_path = out / "layer_overlay.png"
    fig.savefig(overlay_path, dpi=140)
    plt.close(fig)

    # --- Plot 2: Error histograms (X, Y, cumulative E, feedrate_mult64) ---
    fig2, axes2 = plt.subplots(2, 2, figsize=(10.5, 7.2))
    fig2.suptitle(f"{job_name}: Quantization Error Distributions (Decoded X3G - G-code)", fontsize=12, fontweight="bold")

    panels = [
        (axes2[0, 0], report.x_errors_um, "X Position Error (µm)", report.tolerances.x_half_step_mm * 1000.0),
        (axes2[0, 1], report.y_errors_um, "Y Position Error (µm)", report.tolerances.y_half_step_mm * 1000.0),
        (axes2[1, 0], report.e_cum_errors_um, "Cumulative Extrusion Error (µm)", report.tolerances.e_cum_half_step_mm * 1000.0),
        (axes2[1, 1], report.feedrate_errors_mm_s, "Feedrate_mult64 Error (mm/s)", report.tolerances.feedrate_mult64_abs_mm_s),
    ]
    for ax, series, title, bound in panels:
        if series:
            ax.hist(series, bins=40, color="#2ca02c", alpha=0.8, edgecolor="#1b4f1b", linewidth=0.4)
        ax.axvline(-bound, color="#d62728", linestyle="--", linewidth=1.2, label=f"±bound ({bound:.3f})")
        ax.axvline(+bound, color="#d62728", linestyle="--", linewidth=1.2)
        ax.set_title(title)
        ax.set_ylabel("Moves")
        ax.grid(True, alpha=0.25)
        ax.legend(loc="upper right", fontsize=8)

    fig2.tight_layout()
    hist_path = out / "error_histogram.png"
    fig2.savefig(hist_path, dpi=140)
    plt.close(fig2)

    return overlay_path, hist_path
