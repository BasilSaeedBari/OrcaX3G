"""OrcaSlicer v2.5.2 multi-capability plugin glue for OrcaX3G.

Verified against OrcaSlicer ``v2.5.2`` (``f92a7c065c5718b8ef1baac1cd704df2911238f4``):
- ``PythonPluginBridge.cpp`` (``@orca.plugin``, ``orca.base``, ``orca.register_capability``,
  ``orca.ExecutionResult``, ``orca.PluginResult``)
- ``SlicingPipelinePluginCapability.cpp`` (``orca.slicing.SlicingPipelineCapabilityBase``,
  ``orca.slicing.Step.psGCodePostProcess``, ``ctx.gcode_path``, ``ctx.output_name``,
  ``ctx.config_value(key)``, ``ctx.cancelled()``)
- ``PagesPluginCapability.cpp`` (``orca.pages.PagesPluginCapabilityBase``,
  ``get_ui()``, ``get_icon()``, ``on_message(data)``, ``post_message(data)``)
- ``ScriptPluginCapability.cpp`` (``orca.script.ScriptPluginCapabilityBase``, ``execute()``)
- ``PluginHost.cpp`` (``orca.host.plugin.storage()``)
- ``PluginHostUi.cpp`` (``orca.host.ui.message(...)``; NEVER called from slicing worker thread!)

Audit-safety (ADR-007):
- No file, module, or stored filename contains ``conf``, ``secret``, or ``cert``.
- All post-process file access stays strictly inside ``ctx.gcode_path`` (scoped allowed root)
  and ``orca.host.plugin.storage()`` (inside ``data_dir()``), requiring zero extra permissions.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Callable

from .converter import convert_file
from .errors import OrcaX3GError
from .machine_profile import get_profile
from .planner import ConversionOptions

POST_PROCESS_CAPABILITY_NAME = "X3G Exporter (Replicator 2)"
PAGE_CAPABILITY_NAME = "X3G Export"
SCRIPT_CAPABILITY_NAME = "X3G Last Export Summary"
SUMMARY_FILENAME = "last_export_summary.json"

DEFAULT_PLUGIN_SETTINGS: dict[str, Any] = {
    "machine_profile": "r2",
    "firmware": "sailfish",
    "allow_non_x3g_extension": False,
    "strict_bounds": True,
    "warn_sd_filename_length": True,
}


def validate_orca_profile(
    lookup: Callable[[str], Any],
) -> tuple[list[str], list[str]]:
    """Validate OrcaSlicer preset values against Replicator 2 requirements.

    Returns ``(errors, warnings)``. ``lookup(key)`` mirrors ``ctx.config_value(key)``
    which returns serialized string values or ``None`` when absent.
    """
    errors: list[str] = []
    warnings: list[str] = []

    def _str(key: str) -> str:
        val = lookup(key)
        return "" if val is None else str(val).strip()

    flavor = _str("gcode_flavor").lower()
    if flavor in ("sailfish", "makerware"):
        warnings.append(
            f"OrcaSlicer gcode_flavor is '{flavor}'. Recommended flavor is 'marlin' so "
            "Orca emits explicit M82/M83, G92 E0, and M109 wait commands."
        )
    elif flavor and flavor not in ("marlin", "marlin2", "reprapfirmware", "reprap"):
        warnings.append(
            f"OrcaSlicer gcode_flavor is '{flavor}'. Recommended flavor for Replicator 2 is 'marlin'."
        )

    arc_fitting = _str("enable_arc_fitting").lower()
    if arc_fitting and arc_fitting not in ("0", "false", "disabled", "none"):
        warnings.append(
            f"enable_arc_fitting is '{arc_fitting}'; X3G does not support G2/G3 arcs. "
            "Set enable_arc_fitting to 'disabled' in the process preset."
        )

    fw_retract = _str("use_firmware_retraction").lower()
    if fw_retract in ("1", "true", "yes"):
        warnings.append(
            "use_firmware_retraction is enabled; X3G does not support G10/G11. "
            "Disable firmware retraction in the printer preset."
        )

    aux_fan = _str("auxiliary_fan").lower()
    if aux_fan in ("1", "true", "yes"):
        warnings.append(
            "auxiliary_fan is enabled; Replicator 2 has no auxiliary fan (M106 P2 will be rejected)."
        )

    for bed_key in ("hot_plate_temp", "hot_plate_temp_initial_layer", "bed_temperature"):
        bed_raw = _str(bed_key)
        if not bed_raw:
            continue
        first_token = bed_raw.replace(",", " ").replace(";", " ").split()[0]
        try:
            bed_val = float(first_token)
        except ValueError:
            continue
        if bed_val > 0.0:
            warnings.append(
                f"{bed_key} is {bed_val:g} C; Replicator 2 has no heated build plate "
                "(M140/M190 S>0 will be rejected)."
            )
            break

    z_hop_type = _str("z_hop_types").lower()
    if "spiral" in z_hop_type:
        warnings.append(
            f"z_hop_types is '{z_hop_type}'; Spiral Lift emits G2/G3 arcs which X3G rejects. "
            "Use 'Normal Lift'."
        )

    return errors, warnings


def _load_merged_settings(raw_json: str | None) -> dict[str, Any]:
    merged = dict(DEFAULT_PLUGIN_SETTINGS)
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict):
                merged.update(parsed)
        except json.JSONDecodeError:
            pass
    return merged


def _summary_file_path(orca_mod: Any) -> Path | None:
    try:
        storage_dir = orca_mod.host.plugin.storage()
    except Exception:
        return None
    if not storage_dir:
        return None
    return Path(storage_dir) / SUMMARY_FILENAME


def _write_last_summary(orca_mod: Any, payload: dict[str, Any]) -> None:
    path = _summary_file_path(orca_mod)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        # Non-fatal: never abort a valid X3G export because summary persistence failed.
        pass


def _read_last_summary(orca_mod: Any) -> dict[str, Any] | None:
    path = _summary_file_path(orca_mod)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def format_summary_dict_text(data: dict[str, Any]) -> str:
    """Format a persisted summary dictionary into readable text for dialogs."""
    total_s = int(round(float(data.get("estimated_print_time_s", 0.0))))
    mins, secs = divmod(total_s, 60)
    hrs, mins = divmod(mins, 60)
    lines = [
        f"OrcaX3G Last Export Summary ({data.get('machine_profile', 'r2')} / {data.get('firmware', 'sailfish')})",
        f"Output file:      {data.get('output_path', '(unknown)')}",
        f"Build name:       {data.get('build_name', '')}",
        f"X3G size:         {int(data.get('x3g_bytes', 0)):,} bytes ({int(data.get('x3g_commands', 0)):,} commands)",
        f"Motion commands:  {int(data.get('motion_commands', 0)):,} (zero-step skipped: {int(data.get('zero_step_moves_skipped', 0))})",
        f"Layers detected:  {int(data.get('layers_detected', 0))}",
        f"Filament used:    {float(data.get('total_filament_mm', 0.0)):.2f} mm",
        f"Est. print time:  {hrs}h {mins:02d}m {secs:02d}s (constant-speed model)",
        f"Feedrate clamps:  {int(data.get('feedrate_clamps', 0))}",
    ]
    ignored = data.get("ignored_commands") or {}
    if ignored:
        ignored_str = ", ".join(f"{k}x{v}" for k, v in sorted(ignored.items()))
        lines.append(f"Ignored commands: {ignored_str}")
    else:
        lines.append("Ignored commands: none")
    warnings = data.get("warnings") or []
    if warnings:
        lines.append(f"Warnings ({len(warnings)}):")
        for item in warnings:
            lines.append(f"  - {item}")
    else:
        lines.append("Warnings:         none")
    return "\n".join(lines)


def build_export_page_html() -> str:
    """Return themed HTML for the ``X3G Export`` OrcaSlicer tab."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>OrcaX3G Export</title>
<style>
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--orca-bg, #1e1e1e);
    color: var(--orca-fg, #e6e6e6);
    margin: 0;
    padding: 20px;
    line-height: 1.5;
  }
  .card {
    border: 1px solid var(--orca-border, #3a3a3a);
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 16px;
    background: rgba(128, 128, 128, 0.06);
  }
  h1 { margin-top: 0; font-size: 1.35rem; }
  h2 { margin-top: 0; font-size: 1.05rem; color: var(--orca-accent, #00ae42); }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 10px;
  }
  .metric {
    border: 1px solid var(--orca-border, #3a3a3a);
    border-radius: 6px;
    padding: 10px;
  }
  .metric-label { font-size: 0.8rem; opacity: 0.75; }
  .metric-val { font-size: 1.1rem; font-weight: 600; margin-top: 2px; }
  button {
    background: var(--orca-accent, #00ae42);
    color: #fff;
    border: none;
    border-radius: 6px;
    padding: 8px 14px;
    cursor: pointer;
    font-weight: 600;
  }
  pre {
    white-space: pre-wrap;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.85rem;
    background: rgba(0, 0, 0, 0.2);
    padding: 10px;
    border-radius: 6px;
  }
</style>
</head>
<body>
  <h1>OrcaX3G — MakerBot Replicator 2 Exporter</h1>
  <div class="card">
    <h2>Last Export Summary</h2>
    <div id="summary-container">Loading latest export report...</div>
    <p><button id="refresh-btn" type="button">Refresh Summary</button></p>
  </div>
  <div class="card">
    <h2>Required OrcaSlicer Profile Checklist</h2>
    <ul>
      <li><strong>Output filename format:</strong> <code>{input_filename_base}.x3g</code></li>
      <li><strong>G-code flavor:</strong> <code>marlin</code> (not Sailfish/MakerWare)</li>
      <li><strong>Arc fitting:</strong> Disabled (<code>enable_arc_fitting = disabled</code>)</li>
      <li><strong>Z-hop type:</strong> Normal Lift (no Spiral Lift)</li>
      <li><strong>Firmware retraction:</strong> Disabled</li>
      <li><strong>Bed temperature:</strong> 0 &deg;C (no heated bed on Replicator 2)</li>
    </ul>
  </div>
<script>
function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function renderSummary(msg) {
  const el = document.getElementById("summary-container");
  if (!msg || !msg.summary) {
    el.innerHTML = "<p>No X3G export has been recorded yet. Enable <strong>X3G Exporter (Replicator 2)</strong> in Process &rarr; Others &rarr; Slicing pipeline plugins, slice a plate, and click <strong>Export G-code</strong>.</p>";
    return;
  }
  const s = msg.summary;
  const warnings = (s.warnings || []).map(w => "<li>" + esc(w) + "</li>").join("");
  const ignored = Object.entries(s.ignored_commands || {}).map(([k, v]) => esc(k) + " x " + esc(v)).join(", ") || "none";
  el.innerHTML = `
    <div class="grid">
      <div class="metric"><div class="metric-label">Output</div><div class="metric-val">${esc(s.output_path || "")}</div></div>
      <div class="metric"><div class="metric-label">Profile / Firmware</div><div class="metric-val">${esc(s.machine_profile)} / ${esc(s.firmware)}</div></div>
      <div class="metric"><div class="metric-label">X3G Size</div><div class="metric-val">${Number(s.x3g_bytes || 0).toLocaleString()} B</div></div>
      <div class="metric"><div class="metric-label">Motion Moves</div><div class="metric-val">${Number(s.motion_commands || 0).toLocaleString()}</div></div>
      <div class="metric"><div class="metric-label">Layers</div><div class="metric-val">${Number(s.layers_detected || 0)}</div></div>
      <div class="metric"><div class="metric-label">Filament</div><div class="metric-val">${Number(s.total_filament_mm || 0).toFixed(2)} mm</div></div>
    </div>
    <p><strong>Ignored commands:</strong> ${ignored}</p>
    ${warnings ? "<p><strong>Warnings:</strong></p><ul>" + warnings + "</ul>" : "<p><strong>Warnings:</strong> none</p>"}
  `;
}
if (window.orca) {
  window.orca.onMessage(renderSummary);
  document.getElementById("refresh-btn").addEventListener("click", () => {
    window.orca.postMessage({ action: "refresh" });
  });
  window.orca.postMessage({ action: "refresh" });
}
</script>
</body>
</html>
"""


def build_orca_plugin_classes(orca_mod: Any) -> dict[str, type]:
    """Build and return the OrcaSlicer capability & package classes bound to ``orca_mod``.

    Separating class construction from module-level ``import orca`` makes the exact
    OrcaSlicer v2.5.2 glue 100% unit- and integration-testable outside OrcaSlicer using
    a faithful mock ``orca`` host harness.
    """

    class X3GPostProcessCapability(orca_mod.slicing.SlicingPipelineCapabilityBase):
        """Converts exported G-code into binary Replicator 2 X3G at Step.psGCodePostProcess."""

        def get_name(self) -> str:
            return POST_PROCESS_CAPABILITY_NAME

        def get_default_config(self) -> dict[str, Any]:
            # Verified against PyPluginTrampoline.hpp:180-196: override returns a dict.
            return dict(DEFAULT_PLUGIN_SETTINGS)

        def execute(self, ctx: Any) -> Any:
            if ctx.step != orca_mod.slicing.Step.psGCodePostProcess:
                return orca_mod.ExecutionResult.success()

            raw_settings = self.get_config() if hasattr(self, "get_config") else "{}"
            settings = _load_merged_settings(raw_settings)

            output_name = str(getattr(ctx, "output_name", "") or "")
            allow_non_x3g = bool(settings.get("allow_non_x3g_extension", False))
            if output_name and not allow_non_x3g and not output_name.lower().endswith(".x3g"):
                return orca_mod.ExecutionResult.failure(
                    orca_mod.PluginResult.RecoverableError,
                    (
                        f"OrcaX3G refused to write binary X3G to non-.x3g destination '{output_name}'. "
                        "Set filename_format = \"{input_filename_base}.x3g\" in your process preset, "
                        "save with a .x3g extension, or enable allow_non_x3g_extension in plugin settings."
                    ),
                )

            gcode_path = str(getattr(ctx, "gcode_path", "") or "")
            if not gcode_path:
                return orca_mod.ExecutionResult.failure(
                    orca_mod.PluginResult.RecoverableError,
                    "OrcaX3G received an empty ctx.gcode_path at Step.psGCodePostProcess.",
                )

            profile_warnings: list[str] = []
            if hasattr(ctx, "config_value"):
                prof_errors, profile_warnings = validate_orca_profile(ctx.config_value)
                if prof_errors:
                    return orca_mod.ExecutionResult.failure(
                        orca_mod.PluginResult.RecoverableError,
                        "OrcaX3G profile validation failed: " + "; ".join(prof_errors),
                    )

            try:
                profile = get_profile(str(settings.get("machine_profile", "r2")))
                opts = ConversionOptions(
                    profile=profile,
                    firmware=str(settings.get("firmware", "sailfish")),
                    strict_bounds=bool(settings.get("strict_bounds", True)),
                    framed=False,
                    auto_build_notifications=True,
                )
                result = convert_file(
                    gcode_path,
                    gcode_path,
                    options=opts,
                    cancel_check=ctx.cancelled if hasattr(ctx, "cancelled") else None,
                    in_place=True,
                    warn_sd_filename=bool(settings.get("warn_sd_filename_length", True)),
                    display_output_name=output_name or gcode_path,
                )
            except OrcaX3GError as exc:
                return orca_mod.ExecutionResult.failure(
                    orca_mod.PluginResult.RecoverableError,
                    str(exc),
                )
            except RuntimeError as exc:
                if "cancelled" in str(exc).lower():
                    return orca_mod.ExecutionResult.skipped("X3G conversion cancelled.")
                return orca_mod.ExecutionResult.failure(
                    orca_mod.PluginResult.RecoverableError,
                    str(exc),
                )
            except Exception as exc:
                return orca_mod.ExecutionResult.failure(
                    orca_mod.PluginResult.RecoverableError,
                    f"Unexpected OrcaX3G conversion error: {exc}",
                )

            for warning in profile_warnings:
                result.summary.add_warning_once(warning)

            summary_dict = result.summary.to_dict()
            _write_last_summary(orca_mod, summary_dict)

            one_liner = (
                f"Exported X3G ({result.summary.x3g_bytes:,} B, "
                f"{result.summary.motion_commands:,} moves, "
                f"{result.summary.layers_detected} layers, "
                f"{len(result.summary.warnings)} warning(s))."
            )
            return orca_mod.ExecutionResult.success(one_liner)

    class X3GExportPageCapability(orca_mod.pages.PagesPluginCapabilityBase):
        """Provides the 'X3G Export' main-window status tab."""

        def get_name(self) -> str:
            return PAGE_CAPABILITY_NAME

        def get_icon(self) -> str:
            return ""

        def get_ui(self) -> str:
            return build_export_page_html()

        def on_message(self, data: Any) -> None:
            action = data.get("action") if isinstance(data, dict) else None
            if action == "refresh":
                self.post_message({"summary": _read_last_summary(orca_mod)})

    class X3GSummaryScriptCapability(orca_mod.script.ScriptPluginCapabilityBase):
        """Shows the latest X3G export summary in a native dialog (File -> Plugins -> Run)."""

        def get_name(self) -> str:
            return SCRIPT_CAPABILITY_NAME

        def execute(self) -> Any:
            summary = _read_last_summary(orca_mod)
            if summary is None:
                msg = (
                    "No X3G export summary found yet.\n\n"
                    "Enable 'X3G Exporter (Replicator 2)' under Process -> Others -> "
                    "Slicing pipeline plugins, slice a plate, and export as .x3g."
                )
                orca_mod.host.ui.message(msg, title="OrcaX3G Export Summary", buttons="ok", icon="info")
                return orca_mod.ExecutionResult.success("No summary available yet.")

            report_text = format_summary_dict_text(summary)
            icon = "warning" if summary.get("warnings") else "info"
            orca_mod.host.ui.message(
                report_text,
                title="OrcaX3G Export Summary",
                buttons="ok",
                icon=icon,
            )
            return orca_mod.ExecutionResult.success("Displayed X3G export summary.")

    @orca_mod.plugin
    class OrcaX3GPlugin(orca_mod.base):
        """Top-level OrcaSlicer plugin package registering all three capabilities."""

        def register_capabilities(self) -> None:
            orca_mod.register_capability(X3GPostProcessCapability)
            orca_mod.register_capability(X3GExportPageCapability)
            orca_mod.register_capability(X3GSummaryScriptCapability)

    # Suppress unused-import warning on html (kept for future custom escaping)
    _ = html
    return {
        "OrcaX3GPlugin": OrcaX3GPlugin,
        "X3GPostProcessCapability": X3GPostProcessCapability,
        "X3GExportPageCapability": X3GExportPageCapability,
        "X3GSummaryScriptCapability": X3GSummaryScriptCapability,
    }


def register_with_orca() -> dict[str, type] | None:
    """Register OrcaX3G capabilities if running inside OrcaSlicer's embedded Python."""
    try:
        import orca  # type: ignore[import-not-found]
    except ImportError:
        return None
    return build_orca_plugin_classes(orca)
