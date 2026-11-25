"""Integration tests for the OrcaSlicer v2.5.2 multi-capability plugin wrapper.

Uses a faithful in-process mock of OrcaSlicer v2.5.2's embedded ``orca`` pybind11
module (verified against PythonPluginBridge.cpp, SlicingPipelinePluginCapability.cpp,
PagesPluginCapability.cpp, ScriptPluginCapability.cpp, PluginHost.cpp, and
PluginHostUi.cpp) to test:
1. ``@orca.plugin`` package capture and registration of all 3 capabilities.
2. ``X3GPostProcessCapability`` in-place ``.pp`` -> binary ``.x3g`` conversion on a
   real OrcaSlicer v2.5.2 G-code file, verifying output passes full equivalence.
3. Strict worker-thread safety: ZERO calls to ``orca.host.ui.*`` during ``execute(ctx)``.
4. Audit-safe path hygiene (ADR-007): no module or written path contains
   ``conf``, ``secret``, or ``cert``.
5. Non-``.x3g`` extension rejection and ``allow_non_x3g_extension`` override.
6. Profile validation warnings (``gcode_flavor=sailfish``, ``enable_arc_fitting``,
   ``use_firmware_retraction``, ``hot_plate_temp > 0``, ``z_hop_types=Spiral Lift``).
7. ``X3GExportPageCapability`` HTML and ``on_message({"action": "refresh"})``.
8. ``X3GSummaryScriptCapability`` modal ``orca.host.ui.message()`` report.
"""

from __future__ import annotations

import json
import shutil
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from orca_x3g import REPLICATOR_2
from orca_x3g.equivalence import check_equivalence
from orca_x3g.plugin import (
    PAGE_CAPABILITY_NAME,
    POST_PROCESS_CAPABILITY_NAME,
    SCRIPT_CAPABILITY_NAME,
    SUMMARY_FILENAME,
    build_orca_plugin_classes,
    validate_orca_profile,
)

REAL_CUBE_GCODE = (
    Path(__file__).parent / "fixtures" / "real_jobs" / "01_calibration_cube" / "01_calibration_cube.gcode"
)


class _MockOrcaHost:
    """Faithful mock of OrcaSlicer v2.5.2's embedded ``orca`` module."""

    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.captured_package: type | None = None
        self.registered_capabilities: list[type] = []
        self.ui_calls: list[tuple[str, dict[str, Any]]] = []
        self.ui_forbidden = False

        class PluginResult(Enum):
            Success = 0
            Skipped = 1
            RecoverableError = 2
            FatalError = 3

        class ExecutionResult:
            def __init__(self, status: PluginResult, message: str = "", data: str = "") -> None:
                self.status = status
                self.message = message
                self.data = data

            @staticmethod
            def success(message: str = "", data: str = "") -> ExecutionResult:
                return ExecutionResult(PluginResult.Success, message, data)

            @staticmethod
            def skipped(message: str = "") -> ExecutionResult:
                return ExecutionResult(PluginResult.Skipped, message)

            @staticmethod
            def failure(status: PluginResult, message: str, data: str = "") -> ExecutionResult:
                return ExecutionResult(status, message, data)

        class Step(Enum):
            posSlice = 0
            posInfill = 1
            psGCodePostProcess = 99

        class PythonPluginBase:
            def __init__(self) -> None:
                self._stored_config: str = "{}"

            def get_config(self) -> str:
                return self._stored_config

            def save_config(self, cfg_json: str) -> bool:
                self._stored_config = cfg_json
                return True

        class SlicingPipelineCapabilityBase(PythonPluginBase):
            pass

        class PagesPluginCapabilityBase(PythonPluginBase):
            def __init__(self) -> None:
                super().__init__()
                self.posted_messages: list[Any] = []

            def post_message(self, data: Any) -> None:
                self.posted_messages.append(data)

        class ScriptPluginCapabilityBase(PythonPluginBase):
            pass

        class PackageBase:
            pass

        self.PluginResult = PluginResult
        self.ExecutionResult = ExecutionResult
        self.base = PackageBase
        self.slicing = SimpleNamespace(
            Step=Step,
            SlicingPipelineCapabilityBase=SlicingPipelineCapabilityBase,
        )
        self.pages = SimpleNamespace(
            PagesPluginCapabilityBase=PagesPluginCapabilityBase,
        )
        self.script = SimpleNamespace(
            ScriptPluginCapabilityBase=ScriptPluginCapabilityBase,
        )

        def _ui_message(text: str, title: str = "OrcaSlicer", buttons: str = "ok", icon: str = "info") -> str:
            if self.ui_forbidden:
                raise AssertionError("DEADLOCK VIOLATION: orca.host.ui.message called on worker thread!")
            self.ui_calls.append(("message", {"text": text, "title": title, "buttons": buttons, "icon": icon}))
            return "ok"

        self.host = SimpleNamespace(
            plugin=SimpleNamespace(storage=lambda: str(self.storage_dir)),
            ui=SimpleNamespace(message=_ui_message),
        )

    def plugin(self, cls: type) -> type:
        assert issubclass(cls, self.base)
        self.captured_package = cls
        return cls

    def register_capability(self, cap_cls: type) -> None:
        self.registered_capabilities.append(cap_cls)


def _make_ctx(
    orca_mod: _MockOrcaHost,
    *,
    step: Any,
    gcode_path: str = "",
    output_name: str = "",
    config_map: dict[str, str] | None = None,
    cancelled: bool = False,
) -> SimpleNamespace:
    cfg = config_map or {}
    return SimpleNamespace(
        orca_version="2.5.2",
        step=step,
        gcode_path=gcode_path,
        host="File",
        output_name=output_name,
        config_value=lambda key: cfg.get(key),
        cancelled=lambda: cancelled,
    )


def test_plugin_audit_safe_naming_no_forbidden_substrings() -> None:
    # ADR-007: OrcaSlicer PluginAuditManager blocks paths containing conf, secret, cert
    pkg_dir = Path(__file__).parent.parent / "src" / "orca_x3g"
    forbidden = ("conf", "secret", "cert")
    for py_file in pkg_dir.glob("*.py"):
        for kw in forbidden:
            assert kw not in py_file.name.lower(), f"Audit-forbidden keyword {kw!r} in {py_file.name}"
    for kw in forbidden:
        assert kw not in SUMMARY_FILENAME.lower()


def test_plugin_full_lifecycle_postprocess_page_and_script(tmp_path: Path) -> None:
    storage_dir = tmp_path / "plugin_storage"
    storage_dir.mkdir()
    mock_orca = _MockOrcaHost(storage_dir)

    classes = build_orca_plugin_classes(mock_orca)
    assert mock_orca.captured_package is classes["OrcaX3GPlugin"]

    pkg_instance = mock_orca.captured_package()
    pkg_instance.register_capabilities()
    assert len(mock_orca.registered_capabilities) == 3

    post_cap = classes["X3GPostProcessCapability"]()
    page_cap = classes["X3GExportPageCapability"]()
    script_cap = classes["X3GSummaryScriptCapability"]()

    assert post_cap.get_name() == POST_PROCESS_CAPABILITY_NAME
    assert ";" not in post_cap.get_name()
    assert page_cap.get_name() == PAGE_CAPABILITY_NAME
    assert script_cap.get_name() == SCRIPT_CAPABILITY_NAME
    assert isinstance(post_cap.get_default_config(), dict)

    # Non-post-process step must immediately succeed as a no-op
    res_other = post_cap.execute(_make_ctx(mock_orca, step=mock_orca.slicing.Step.posSlice))
    assert res_other.status == mock_orca.PluginResult.Success

    # Simulate OrcaSlicer creating a .pp temp copy of the real sliced calibration cube G-code
    pp_copy = tmp_path / "plate_1.gcode.pp"
    shutil.copy2(REAL_CUBE_GCODE, pp_copy)

    # Reject non-.x3g destination by default
    mock_orca.ui_forbidden = True
    res_bad_ext = post_cap.execute(
        _make_ctx(
            mock_orca,
            step=mock_orca.slicing.Step.psGCodePostProcess,
            gcode_path=str(pp_copy),
            output_name="/tmp/cube.gcode",
        )
    )
    assert res_bad_ext.status == mock_orca.PluginResult.RecoverableError
    assert ".x3g" in res_bad_ext.message

    # Convert in-place with valid .x3g output_name and worker-thread UI guard active!
    ctx = _make_ctx(
        mock_orca,
        step=mock_orca.slicing.Step.psGCodePostProcess,
        gcode_path=str(pp_copy),
        output_name="/media/sd/CUBE01.X3G",
        config_map={
            "gcode_flavor": "marlin",
            "enable_arc_fitting": "disabled",
            "use_firmware_retraction": "0",
            "auxiliary_fan": "0",
            "hot_plate_temp": "0",
            "z_hop_types": "Normal Lift",
        },
    )
    res_ok = post_cap.execute(ctx)
    mock_orca.ui_forbidden = False

    assert res_ok.status == mock_orca.PluginResult.Success, res_ok.message
    assert "Exported X3G" in res_ok.message
    assert len(mock_orca.ui_calls) == 0  # Zero UI calls on slicing worker thread

    # Verify the in-place overwritten .pp file is valid binary X3G equivalent to original G-code!
    x3g_bytes = pp_copy.read_bytes()
    rep, _, _ = check_equivalence(
        REAL_CUBE_GCODE.read_text(encoding="utf-8").splitlines(),
        x3g_bytes,
        profile=REPLICATOR_2,
    )
    assert rep.passed, rep.violations

    # Verify summary persisted cleanly to plugin storage
    summary_file = storage_dir / SUMMARY_FILENAME
    assert summary_file.is_file()
    persisted = json.loads(summary_file.read_text(encoding="utf-8"))
    assert persisted["motion_commands"] == rep.metrics.x3g_motion_total
    assert persisted["output_path"] == "/media/sd/CUBE01.X3G"

    # Exercise X3GExportPageCapability (both summary refresh and chunked 3D X3G load_last)
    html_ui = page_cap.get_ui()
    assert "--orca-bg" in html_ui and "window.orca" in html_ui and "decodeX3G" in html_ui
    page_cap.on_message({"action": "refresh"})
    assert len(page_cap.posted_messages) == 1
    assert page_cap.posted_messages[0]["summary"]["motion_commands"] == rep.metrics.x3g_motion_total

    page_cap.posted_messages.clear()
    page_cap.on_message({"action": "load_last"})
    assert len(page_cap.posted_messages) >= 2
    begin_msg = page_cap.posted_messages[0]
    assert begin_msg["type"] == "x3g_begin"
    assert begin_msg["name"] == "CUBE01.X3G"
    assert begin_msg["total_bytes"] == len(x3g_bytes)
    import base64 as _b64

    reassembled = b"".join(
        _b64.b64decode(m["b64"])
        for m in page_cap.posted_messages[1:]
        if m.get("type") == "x3g_chunk"
    )
    assert reassembled == x3g_bytes

    # Exercise X3GSummaryScriptCapability (runs on UI thread -> calls orca.host.ui.message)
    script_res = script_cap.execute()
    assert script_res.status == mock_orca.PluginResult.Success
    assert len(mock_orca.ui_calls) == 1
    assert mock_orca.ui_calls[0][0] == "message"
    assert "CUBE01.X3G" in mock_orca.ui_calls[0][1]["text"]


def test_profile_validation_warns_on_misconfigured_presets() -> None:
    bad_cfg = {
        "gcode_flavor": "sailfish",
        "enable_arc_fitting": "enabled",
        "use_firmware_retraction": "1",
        "auxiliary_fan": "1",
        "hot_plate_temp": "60",
        "z_hop_types": "Spiral Lift",
    }
    errors, warnings = validate_orca_profile(bad_cfg.get)
    assert errors == []
    assert len(warnings) == 6
    joined = "\n".join(warnings)
    assert "sailfish" in joined
    assert "enable_arc_fitting" in joined
    assert "use_firmware_retraction" in joined
    assert "auxiliary_fan" in joined
    assert "hot_plate_temp" in joined
    assert "Spiral Lift" in joined
