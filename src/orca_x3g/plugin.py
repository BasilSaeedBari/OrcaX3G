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

import base64
import json
from pathlib import Path
from typing import Any, Callable

from .converter import convert_file
from .errors import OrcaX3GError
from .machine_profile import get_profile
from .planner import ConversionOptions
from .viewer import build_viewer_html

POST_PROCESS_CAPABILITY_NAME = "X3G Exporter (Replicator 2)"
PAGE_CAPABILITY_NAME = "X3G Export"
SCRIPT_CAPABILITY_NAME = "X3G Last Export Summary"
SUMMARY_FILENAME = "last_export_summary.json"
LAST_EXPORT_X3G_FILENAME = "last_export.x3g"
X3G_STREAM_CHUNK_BYTES = 512 * 1024  # 512 KiB per post_message chunk

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


def _last_x3g_file_path(orca_mod: Any) -> Path | None:
    try:
        storage_dir = orca_mod.host.plugin.storage()
    except Exception:
        return None
    if not storage_dir:
        return None
    return Path(storage_dir) / LAST_EXPORT_X3G_FILENAME


def _write_last_x3g(orca_mod: Any, x3g_bytes: bytes) -> None:
    path = _last_x3g_file_path(orca_mod)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(x3g_bytes)
    except OSError:
        # Non-fatal: never abort a valid X3G export because preview caching failed.
        pass


def _read_last_x3g(orca_mod: Any) -> bytes | None:
    path = _last_x3g_file_path(orca_mod)
    if path is None or not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
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
    """Return themed interactive 3D X3G viewer HTML for the ``X3G Export`` OrcaSlicer tab."""
    return build_viewer_html()


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
            _write_last_x3g(orca_mod, result.x3g_bytes)

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
                return
            if action == "load_last":
                summary = _read_last_summary(orca_mod)
                raw_x3g = _read_last_x3g(orca_mod)
                if raw_x3g is None:
                    self.post_message({"type": "x3g_none", "summary": summary})
                    return
                name = (
                    Path(str(summary.get("output_path", LAST_EXPORT_X3G_FILENAME))).name
                    if summary and summary.get("output_path")
                    else LAST_EXPORT_X3G_FILENAME
                )
                profile_id = str(summary.get("machine_profile", "r2")) if summary else "r2"
                total_chunks = max(
                    1, (len(raw_x3g) + X3G_STREAM_CHUNK_BYTES - 1) // X3G_STREAM_CHUNK_BYTES
                )
                self.post_message(
                    {
                        "type": "x3g_begin",
                        "name": name,
                        "profile": profile_id,
                        "total_bytes": len(raw_x3g),
                        "total_chunks": total_chunks,
                        "summary": summary,
                    }
                )
                for idx in range(total_chunks):
                    start = idx * X3G_STREAM_CHUNK_BYTES
                    chunk = raw_x3g[start : start + X3G_STREAM_CHUNK_BYTES]
                    self.post_message(
                        {
                            "type": "x3g_chunk",
                            "index": idx,
                            "b64": base64.b64encode(chunk).decode("ascii"),
                        }
                    )

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
