# Architecture Decision Log (ADR)

Running log of all key technical, protocol, and packaging decisions for **OrcaX3G**.

---

## ADR-001: Clean-Room Pure-Python Implementation & MIT License
- **Date:** 2026-11-25
- **Status:** Accepted
- **Context:** Consulted reference repositories use copyleft licenses (`OrcaSlicer`: AGPL-3.0, `makerbot/s3g`: AGPL-3.0, `GPX`: GPL-2.0-or-later, `Sailfish` & `MightyBoardFirmware`: GPL-3.0-or-later). The target repository `BasilSaeedBari/OrcaX3G` is licensed under **MIT**.
- **Decision:** Write all converter, planner, encoder, decoder, simulator, CLI, and Orca plugin code **100% clean-room in pure Python 3.12+** (matching OrcaSlicer `v2.5.2`'s embedded CPython `3.12.13`) with **zero third-party runtime dependencies**. Never copy or port code from GPX, s3g, Sailfish, or OrcaSlicer. Compile and invoke `markwal/GPX` strictly as an external CLI oracle subprocess during Phase 3 verification.
- **Consequences:** Preserves clean MIT licensing, eliminates `uv` network install latency/failures inside OrcaSlicer, and avoids native-extension ABI complexity across Linux/macOS/Windows unless profiling proves pure Python too slow.

## ADR-002: Default XY Steps/mm (`88.888889`) + `r2_factory` Variant (`88.573186`)
- **Date:** 2026-11-25
- **Status:** Accepted
- **Context:** Primary sources conflict on Replicator 2 X/Y steps/mm: Sailfish `platforms.py:223`, Stock `EepromMap.hh:50–51`, MakerBot `Replicator2.json:8,13`, and GPX `-m or2` use `88.573186`, while GPX `-m r2` (`std_machines.h:188–189`) uses `88.888889` (`3200 / (18 * 2)`, exact 18-tooth GT2 pulley pitch).
- **Decision:** Default profile `r2` uses **`88.888889`** steps/mm (exact GT2 tooth geometry and direct parity with `gpx -m r2`). Expose a first-class selectable profile `r2_factory` with **`88.573186`** steps/mm (matching Sailfish/Stock factory EEPROM defaults and `gpx -m or2`).
- **Consequences:** Both hardware calibrations are supported transparently without hidden assumptions; Phase 3 oracle comparisons against `gpx -m r2` and `gpx -m or2` can verify both.

## ADR-003: Replicator 2 Machine Limits & Centered Coordinate Origin
- **Date:** 2026-11-25
- **Status:** Accepted
- **Context:** Build volume and XY max feedrate vary slightly across legacy sources (`285×152×155` vs `285×150×160` vs `285×148×150`; `18000` vs `12450` mm/min).
- **Decision:** Adopt Sailfish `mighty_two` + GPX `r2` limits: **`285 × 152 × 155 mm`** (`X ∈ [-142.5, +142.5]`, `Y ∈ [-76.0, +76.0]`, `Z ∈ [0.0, 155.0]` centered at `(0, 0)`), max feedrates **`18000 mm/min`** (X/Y), **`1170 mm/min`** (Z), **`1600 mm/min`** (A), Z = **`400` steps/mm**, A = **`96.27520187033366` steps/mm** (negative steps = forward extrusion), single extruder (Tool 0), no heated bed. Keep recommended OrcaSlicer print speeds conservative (`<= 100 mm/s` print, `<= 150 mm/s` travel).

## ADR-004: OrcaSlicer `v2.5.2` Multi-Capability Integration (Rank 1)
- **Date:** 2026-11-25
- **Status:** Accepted
- **Context:** In OrcaSlicer `v2.5.2`, `PostProcessor.cpp:465–506` checks `.output_name` *before* running Python plugins and passes `output_name` by `const std::string&`, while `PluginAuditManager` fails closed on off-thread writes outside `ctx.gcode_path.parent_path()`. However, `Plater::export_gcode()` derives the Save dialog's filename and filter from `filename_format`, and `BackgroundSlicingProcess::finalize_gcode()` performs a raw binary `copy_file()` (with SD-card verification) of the `.pp` working file to `export_path`.
- **Decision:** Package as a pure-Python `.whl` registering three capabilities:
  1. `SlicingPipelineCapabilityBase` (`Step.psGCodePostProcess`) — converts the `.pp` temp G-code to binary `.x3g` in-place (zero audit prompts, G-code previewer untouched), writes export summary JSON to `orca.host.plugin.storage()`.
  2. `PagesPluginCapabilityBase` — **X3G Export** main-window tab showing the latest export summary, warnings, ignored commands, profile diagnostics, and a manual `.gcode → .x3g` converter UI.
  3. `ScriptPluginCapabilityBase` — **X3G Export Summary & Convert** action under File → Plugins → Run.
  Pair with an OrcaSlicer Replicator 2 preset setting `filename_format = "{input_filename_base}.x3g"`.
- **Consequences:** True one-click `.x3g` export straight from OrcaSlicer to SD card with zero audit popups and zero external converter binaries.

## ADR-005: Firmware Target Policy (Sailfish Primary, Stock v7.x Byte-Stream Compatible)
- **Date:** 2026-11-25
- **Status:** Accepted
- **Context:** Sailfish and Stock MightyBoardFirmware v7.x share identical binary layouts for all standard print commands (`131–137`, `139–140`, `142–151`, `153–156`). Command `158` (`HOST_CMD_PAUSE_AT_ZPOS`) is Sailfish-only and hangs Stock firmware. Command `157` (`HOST_CMD_STREAM_VERSION`) has a buffer-length bug in Stock firmware (`>= 11` check before popping 21 bytes) and is ignored by Sailfish and omitted by GPX.
- **Decision:** Target **Sailfish** as primary firmware while keeping default `.x3g` output **100% compatible with Stock v7.x** by omitting Command `157` and gating Sailfish-only Command `158` (`M322`) behind an explicit `firmware="sailfish"` setting.

## ADR-006: G-code Flavor (`marlin`), `M600` Mapping, and `.gcode` Destination Guard
- **Date:** 2026-11-25
- **Status:** Accepted
- **Context:** OrcaSlicer's `sailfish`/`makerware` flavors suppress `M82`/`M83`, `G92 E0`, and `M109` wait-for-temperature (`GCodeWriter.cpp:285–319, 728–731`). RepRap/Marlin flavor (`"marlin"`) emits unambiguous `M82`/`M83`, `M104`/`M109`, and `M106`/`M107`.
- **Decision:**
  - Require RepRap/Marlin flavor (`marlin` / `marlin2`) in the recommended profile and default converter flavor (with explicit validation warnings if `sailfish`/`makerware`/`reprapfirmware` is detected).
  - Map `M600` (filament change) to `HOST_CMD_DISPLAY_MESSAGE` (`149`, `"Change Filament"`) + `HOST_CMD_PAUSE_FOR_BUTTON` (`148`, center button, no timeout).
  - Reject exports whose destination path does not end in `.x3g` by default (preventing binary X3G data from being saved with a `.gcode` extension), with an opt-in plugin config override `allow_non_x3g_extension`.

## ADR-007: Audit-Safe Module Naming
- **Date:** 2026-11-25
- **Status:** Accepted
- **Context:** OrcaSlicer `PluginAuditManager` unconditionally blocks filesystem access to any path component containing `"secret"`, `"cert"`, or `"conf"` (case-insensitive substring match, `plugin_audit_hook.md:298–304, 481–485`).
- **Decision:** Never name any Python module, data file, or directory under `src/orca_x3g/` or plugin storage with `"conf"`, `"secret"`, or `"cert"` in its path (e.g., use `settings.py` / `machine_profile.py` / `options.json`, NEVER `config.py` or `machine_config.py`).
