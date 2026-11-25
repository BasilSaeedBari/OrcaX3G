# Phase 0 Research: OrcaSlicer X3G Export Plugin for MakerBot Replicator 2

**Date:** 2026-11-25  
**Target Printer:** MakerBot Replicator 2 (single extruder, no heated bed, MightyBoard Rev G/H, Sailfish & Stock v7.x firmware)  
**Pinned OrcaSlicer Target:** `v2.5.2` (commit `f92a7c065c5718b8ef1baac1cd704df2911238f4`, released 2026-11-21)  
**Citations:** All source URLs, commit SHAs, file paths, and line numbers are cataloged in [`docs/SOURCES.md`](SOURCES.md).

---

## A. OrcaSlicer Plugin System (`v2.5.2`)

### A.1 Version Status & Stability Caveats
The Python plugin system is no longer confined to the 2.5.0-alpha branch: OrcaSlicer shipped official releases `v2.5.0` (2026-11-07), `v2.5.1` (2026-11-14), and `v2.5.2` (2026-11-21) with the embedded CPython plugin architecture intact. However:
- The wiki explicitly marks **`slicing_pipeline_plugin`** as *experimental* (`plugins/plugins_types.md:25–26`) and **`PrinterConnection` (Printer Agent)** as *WIP* and gated behind an off-by-default Developer preference (`api_reference/printer_agent.md:27–31`).
- Several `PluginType` enum values (`Analysis`, `Importer`, `Exporter`, `Visualization`) exist in `PythonPluginInterface.hpp:16` as placeholders, **with no C++ capability bases or call sites implemented in `v2.5.2`**.
- We pin our target to **OrcaSlicer `v2.5.2`** (`f92a7c065c5718b8ef1baac1cd704df2911238f4`) and keep all converter/planner/encoder logic in a pure, host-independent Python package so Orca API churn only touches a thin glue layer.

### A.2 Embedded Python Runtime, Stdlib, and Dependencies
Verified in `deps/python3/python3.cmake` and `src/slic3r/plugin/PluginLoader.cpp`:
- **Interpreter:** Embedded **CPython `3.12.13`** (`deps/python3/python3.cmake:4`), built as shared `libpython3.12` linked with OpenSSL and ZLIB.
- **Stdlib:** Standard CPython 3.12 stdlib is available (`struct`, `math`, `re`, `json`, `pathlib`, `dataclasses`, `typing`, `enum`, `io`, `hashlib`, `logging`, etc.). `_tkinter` (`py_cv_module__tkinter=n/a`) and CPython test modules (`--disable-test-modules`) are intentionally excluded. Plugins must **never** import their own GUI toolkits (`tkinter`, `PyQt`, `wxPython`).
- **Third-party pip dependencies & wheels:** Supported. At load time, `PluginLoader` invokes a **bundled `uv`** executable (`PythonInterpreter.cpp:456–472`, `PluginLoader.cpp:296`) to install `dependencies` (PEP 723) or `Requires-Dist` (`.whl`) into `data_dir()/python/packages/<abi_tag>/`. Compiled platform wheels are supported if their `WHEEL` `Tag:` matches `PythonInterpreter::python_abi_tag()` and OS; pure-Python `*-none-any` wheels work cross-platform (`plugin_development.md:564–569`). For our plugin, **zero third-party runtime dependencies** (stdlib-only) eliminates install latency, network failure modes, and audit surface.

### A.3 Packaging, Installation, and Registration
- **Install directories** (`plugin_system.md:99–104`):
  - Local / side-loaded: `data_dir()/orca_plugins/<plugin_dir>/`
  - Cloud-subscribed: `data_dir()/orca_plugins/_subscribed/<user_id>/<plugin_dir>/`
  - Installed via **File → Plugins → Browse plugins → Install local plugin** (or dropped into its own subdirectory).
- **Packaging forms** (`plugin_development.md:51–122, 539–572`):
  1. **Single `.py` file** with a PEP 723 `# /// script` TOML header containing `[tool.orcaslicer.plugin]` (`name`, `description`, `author`, `version`) and optional root `dependencies` / `requires-python` (note: `requires-python` is parsed but not enforced in `v2.5.2`).
  2. **Standard Python wheel (`.whl`)** containing a single `*.dist-info/` (`METADATA`, `WHEEL`, `RECORD`), recommended for multi-module packages. `METADATA` provides `Name`, `Version`, `Summary`, `Author`, and `Requires-Dist`.
  - Neither form declares a static `type`; OrcaSlicer derives the plugin's types from the capabilities registered at load time.
- **Registration contract** (`plugin_development.md:490–538`):
  - Exactly **one** package class per entry module decorated with `@orca.plugin` subclassing `orca.base`, implementing `register_capabilities(self)`.
  - Inside `register_capabilities(self)`, call `orca.register_capability(CapabilityClass)` for each capability class, and optionally `orca.request_permissions(...)`.
  - Every capability class subclasses a typed base (ultimately `orca.PythonPluginBase`) and implements `get_name(self) -> str` (must be unique per `(type, name)` and **must not contain `;`**, which is reserved in preset `plugins` manifest strings `<plugin_name>;<cloud_uuid>;<capability_name>`).

### A.4 Capability Types & Exact APIs in `v2.5.2`

| Capability Base (`orca.*`) | Type | Entry Method(s) | Execution Thread | Host / Config Access |
|---|---|---|---|---|
| `orca.slicing.SlicingPipelineCapabilityBase` | `SlicingPipeline` | `execute(self, ctx) -> ExecutionResult` | **Background slicing worker thread** | `ctx.step`, `ctx.orca_version`, `ctx.gcode_path`, `ctx.host`, `ctx.output_name`, `ctx.config_value(key)`, `ctx.cancelled()`, `self.get_config()`. **MUST NOT call `orca.host.ui.*`** (every `orca.host.ui` method—including `push_notification`—uses `run_on_ui_blocking` in `PluginHostUi.cpp:672`, which deadlocks if the UI thread is waiting on slicing). |
| `orca.script.ScriptPluginCapabilityBase` | `Script` | `execute(self) -> ExecutionResult` | **Main/UI thread** (invoked via Plugins dialog → Run) | Full `orca.host` (read-only `model()`, `plater()`, `preset_bundle()`, `plugin.storage()`), `orca.host.ui.*` dialogs, `self.get_config()`. |
| `orca.pages.PagesPluginCapabilityBase` | `Pages` | `get_ui(self) -> str`, `on_message(self, data)`, `post_message(data)` | **Main/UI thread** (adds a main-window tab) | Full `orca.host` + bidirectional HTML/JS `window.orca.postMessage` / `onMessage` bridge + `self.get_config()`. |
| `orca.printer_agent.PrinterAgentBase` | `PrinterConnection` | `get_agent_info()`, `connect_printer()`, `start_send_gcode_to_sdcard()`, `start_local_print()`, ~30 methods | UI thread (connect/discover) & job threads (`start_*`) | Receives `PrintParams` (`filename` = local 3MF/file). Only active when experimental `use_printer_agents` is enabled (off by default). |
| `orca.resource_texture.ResourceTexturePluginCapabilityBase` | `ResourceTexture` | `get_texture_dir(self) -> str` | Materialization | `.whl` only; height-map texture packs. Not relevant to X3G export. |

Additionally, **every** capability inherits:
- JSON-backed configuration (`get_default_config()`, `get_config()`, `save_config()`, optional custom HTML editor via `has_config_ui()` / `get_config_ui()`), with per-preset overrides (`print_plugin_config_overrides`).
- `on_load()`, `on_unload()`, and `on_lifecycle_event(event, ctx)` (`GCodeExportStarted`, `GCodeExportFinished`, `SlicingJobComplete`, `PresetSelected`, etc. — delivered synchronously on the raising thread).

### A.5 Security / CPython Audit Hook (`PluginAuditManager`) — Crucial Export Constraint
Verified in `src/slic3r/plugin/PluginAuditManager.cpp` and `PySlicingPipelinePluginCapabilityTrampoline.hpp`:
- Every C++→Python call opens a `thread_local` `ScopedPluginAuditContext`.
- Pre-approved **allowed roots** (no prompt):
  1. `data_dir()` (read+write, includes `orca.host.plugin.storage()`)
  2. `resources_dir()` (read-only)
  3. At `Step.psGCodePostProcess`: **`ctx.gcode_path.parent_path()` ONLY** (read+write, scoped to the call). Note that `ctx.gcode_path` is a `.pp` temporary file in Orca's temp directory—**NOT** `ctx.output_name`'s destination directory (e.g., an SD card or `~/Desktop`).
- **Denied path keywords** (blocked unconditionally, even inside allowed roots): any path component containing `"secret"`, `"cert"`, or `"conf"` (case-insensitive). *(Design implication: never name any plugin module or file `config.py` / `machine_config.py`!)*
- **Off-thread fail-closed rule** (`plugin_audit_hook.md:151–159`): `psGCodePostProcess` runs on the slicing worker thread. If plugin code tries to open/write a path **outside** allowed roots (e.g., attempting to directly write a sibling `.x3g` next to `ctx.output_name`), the audit hook **immediately fails closed with `PermissionError`** and posts the Yes/No prompt asynchronously. (Note: `v2.5.2` added `fs_grant_covers()` tree grants `"/dir/*"` in `PluginAuditManager.cpp:1409–1425`, but a plugin still cannot predict arbitrary user export directories at load time.)

### A.6 CRITICAL QUESTION: What Can a Plugin Do in the Export Flow Today?

| Question | Answer in OrcaSlicer `v2.5.2` | Exact Primary-Source Evidence |
|---|---|---|
| **(a) Receive the final G-code path?** | **YES.** | At `Step.psGCodePostProcess`, `ctx.gcode_path` is the writable working copy (`m_temp_output_path + ".pp"`) and `ctx.output_name` is the final destination path chosen by the user (`PostProcessor.cpp:290–293`, `BackgroundSlicingProcess.cpp:862–871`). |
| **(b) Write a differently-named / different-format output file?** | **Different format: YES (verbatim binary copy). Dynamic rename from Python: NO. Static `.x3g` filename via preset: YES!** | 1. **Binary format works cleanly:** For non-BBL printers, `BackgroundSlicingProcess::finalize_gcode()` (`L871–888`) runs `run_post_process_scripts(output_path, /*make_copy=*/true, "File", export_path, ...)` on a `.pp` copy (keeping the G-code viewer's memory-mapped `m_temp_output_path` untouched!), then calls raw binary `copy_file(output_path, export_path, ..., m_export_path_on_removable_media)` (with SD-card read-back verification) without re-parsing `output_path` as G-code.<br>2. **Dynamic rename blocked in C++:** `PostProcessor.cpp:465–506` reads `path + ".output_name"` **after** classic `post_process` scripts and **BEFORE** `run_post_process_plugins()`, and passes `output_name` as `const std::string&`. A Python plugin cannot change `export_path` at runtime.<br>3. **Direct `.x3g` extension works via `filename_format`:** `Plater::export_gcode()` (`Plater.cpp:19534–19565`) computes `default_output_file` from the process preset's `filename_format` and passes `ext = default_output_file.extension()` to `GUI::file_wildcards(FT_GCODE, ext)` (`GUI_App.cpp:695–725`), which puts `*.x3g;*.X3G` first in the Save dialog filter and pre-fills `<model>.x3g`! |
| **(c) Hook into Export G-code / Send to printer / Print / CLI?** | **Export G-code: YES.** **Send (classic print host): YES.** **Print/Send (PrinterAgent): experimental only.** **Headless CLI `--export-gcode`: NO.** | `finalize_gcode()` (`BackgroundSlicingProcess.cpp:871`) and `prepare_upload()` (`L1017`) both invoke `run_post_process_scripts()`. `run_post_process_scripts` resides in `src/slic3r/GUI/PostProcessor.cpp` and has **no CLI call site** in `v2.5.2`. |
| **(d) Add a UI action?** | **Tab (`Pages`) or Plugins→Run (`Script`): YES. Native File→Export menu item: NO.** | `orca.pages` adds a top-level main-window tab; `orca.script` adds a Run button in File → Plugins; `orca.host.ui` provides dialogs/docked panels (from UI thread only). No C++ API exists to add entries to native menus or toolbar export buttons. |

### A.7 Ranked Integration Approaches & Trade-offs

1. **Rank 1 (Recommended): Multi-Capability Wheel (`.whl`) combining `SlicingPipeline` (`psGCodePostProcess`) + `Pages` (X3G Export Status/Tool Tab) + `Script` (Summary/Manual Convert), paired with a Replicator 2 preset setting `filename_format = "{input_filename_base}.x3g"`**
   - *How it works:* User clicks OrcaSlicer's standard **Export G-code** (including direct-to-SD-card removable media!). Save dialog pre-fills `<model>.x3g` with `*.x3g` filter. At `Step.psGCodePostProcess`, the plugin converts the `.pp` temp G-code to binary `.x3g` in-place (zero audit prompts, previewer G-code stays untouched), saves the export report to `orca.host.plugin.storage()`, and OrcaSlicer copies + verifies the `.x3g` directly onto the SD card! The **X3G Export** `Pages` tab (and `Script` action) displays the full export summary (size, moves, layers, warnings, ignored commands, profile check) and also allows converting any `.gcode` file manually.
   - *Safeguard:* If a user forgets to set `filename_format` and exports to `*.gcode`, the plugin rejects loudly with a clear error explaining how to save as `.x3g` (or offers a configurable override), preventing binary `.x3g` bytes from silently being written to a `.gcode` filename.
   - *Trade-offs:* Zero audit popups; true one-click `.x3g` export to SD card; zero external tools. Requires the user to use the supplied Replicator 2 preset (or type `.x3g` in the Save dialog).
2. **Rank 2 (Fallback): `SlicingPipeline` writing a sibling `<stem>.x3g` next to `ctx.output_name` while leaving `.gcode` untouched**
   - *Trade-offs:* **Broken UX under `v2.5.2` audit hook** — writing outside `ctx.gcode_path.parent_path()` from the slicing worker thread fails closed with `PermissionError` on first export to any ungranted folder, forcing an error dialog + retry. Not recommended as primary mode.
3. **Rank 3 (Future / Upstream Change Request): Contribute two small upstream PRs to OrcaSlicer:**
   - (a) Move the `.output_name` check in `PostProcessor.cpp:465–500` **after** `run_post_process_plugins()` (or pass `std::string& output_name` mutably in `SlicingPipelineContext`), allowing plugins to dynamically swap `.gcode` → `.x3g` even without `filename_format`.
   - (b) Implement the placeholder `orca.PluginType.Exporter` capability type (`PythonPluginInterface.hpp:16`) wired into the Export dropdown.
4. **Rank 4 (Not recommended for Replicator 2 SD workflow): `PrinterConnection` (`PrinterAgentBase`)**
   - *Trade-offs:* Gated behind an experimental Developer setting (off by default), designed for LAN/cloud Bambu-style JSON protocols, and the Replicator 2 has no Wi-Fi/LAN (SD card + legacy USB serial only).

---

## B. X3G / S3G Protocol & MakerBot Replicator 2 Firmware Analysis

### B.1 Critical Distinction: Serial S3G Framing vs `.x3g` SD-Card File Format
Verified independently across **four** primary sources (`Sailfish/SDCard.cc:382–389`, `Sailfish/Command.cc:1213–1216`, `s3g/makerbot_driver/Writer/FileWriter.py:38–44`, `GPX/README.md:82`):
- **USB/RS-232 Serial S3G Wire Packet (`Packet.hh:22–23`, `Packet.cc:54–139`, `s3gProtocol.md:111–140`):**
  ```
  [0xD5] [Length: uint8, 0..32] [Payload: Length bytes] [CRC-8: uint8]
  ```
  - `START_BYTE = 0xD5`, `MAX_PACKET_PAYLOAD = 32` bytes.
  - **CRC Algorithm:** 8-bit Maxim / iButton CRC (`_crc_ibutton_update` in avr-libc `<util/crc16.h>`, `Packet.cc:25`):
    - Polynomial: `0x31` ($x^8 + x^5 + x^4 + 1$), reflected (LSB-first) = `0x8C`
    - Initial value: `0x00` (`Packet.cc:33`), RefIn = `True`, RefOut = `True`, XorOut = `0x00`
    - Computed over the **Payload bytes only** (excluding `0xD5` and `Length`).
    - Verified test vectors (`s3g/tests/pi_test_Crc.py:13–18`):
      - `b""` → `0x00`
      - `b"abcdefghijk"` → `0xB4`
      - `b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"` → `0x3C`
- **`.x3g` SD-Card File Format (`SDCard.cc:387`, `Command.cc:1215`, `FileWriter.py:44`):**
  - **RAW, UNFRAMED concatenation of Host Buffered Command payloads** (each beginning directly with its 1-byte command opcode `>= 128`).
  - There is **NO `0xD5` start byte, NO length byte, and NO CRC byte** in an `.x3g` file! When the MightyBoard reads an `.x3g` file from the SD card, `runCommandSlice()` pushes file bytes directly into `command_buffer` and dispatches `command_buffer[0]` as the command opcode. Wrapping `.x3g` file commands in `0xD5` framing will stall the printer immediately (`0xD5 = 213` hits the unhandled `else {}` branch in `Command.cc`).
  - *(Note: We will still implement and unit-test S3G packet framing + CRC-8-Maxim in our encoder/decoder library as an optional framed mode mirroring `gpx -F` and for serial-wire verification against the spec vectors.)*

### B.2 Data Types, Endianness, and Axes Bitfield
Verified in `s3gProtocol.md:291–370` and `Sailfish/Command.cc:329–368`:
- **Endianness:** All multi-byte integers (`int16`, `uint16`, `int32`, `uint32`) and IEEE-754 single-precision `float32` values are **little-endian** (matching 8-bit AVR).
- **Axes Bitfield (`uint8`):** Bit 0 = `X` (`0x01`), Bit 1 = `Y` (`0x02`), Bit 2 = `Z` (`0x04`), Bit 3 = `A` (`0x08`, Tool 0 / right extruder), Bit 4 = `B` (`0x10`, Tool 1 / left extruder). Bits 5–7 reserved (`0`; note Sailfish clears bit 7 on Command 155 `relative` with `& 0x7F` at `Command.cc:836`).
- **Extruder Step Polarity (`Sailfish/boards/mighty_two/Configuration.hh:374`, `Replicator2.json:22`, `GPX/gpx.c:2279`):**
  - On the Replicator 2, **`ACCELERATION_EXTRUDE_WHEN_NEGATIVE_A` is `true`**: **negative A steps extrude filament forward**, and **positive A steps retract filament**!

### B.3 Command Catalog (Verified Against `s3gProtocol.md`, `Sailfish/Command.cc`, and `MightyBoardFirmware/Command.cc`)

| Opcode | Name (`Commands.hh`) | Total Size (bytes) | Payload Layout (after opcode byte, little-endian) | Firmware Support |
|---|---|---|---|---|
| `131` (`0x83`) | `HOST_CMD_FIND_AXES_MINIMUM` | 8 | `uint8 axes`, `uint32 step_delay_us`, `uint16 timeout_s` | Sailfish + Stock |
| `132` (`0x84`) | `HOST_CMD_FIND_AXES_MAXIMUM` | 8 | `uint8 axes`, `uint32 step_delay_us`, `uint16 timeout_s` | Sailfish + Stock |
| `133` (`0x85`) | `HOST_CMD_DELAY` | 5 | `uint32 delay_ms` | Sailfish + Stock |
| `134` (`0x86`) | `HOST_CMD_CHANGE_TOOL` | 2 | `uint8 tool_id` (`0` on R2) | Sailfish + Stock |
| `135` (`0x87`) | `HOST_CMD_WAIT_FOR_TOOL` | 6 | `uint8 tool_id`, `uint16 ping_delay_ms`, `uint16 timeout_s` | Sailfish + Stock |
| `136` (`0x88`) | `HOST_CMD_TOOL_COMMAND` | `4 + N` | `uint8 tool_id`, `uint8 action_id`, `uint8 payload_len (N)`, `N bytes` | Sailfish + Stock |
| ↳ action `3` | `SLAVE_CMD_SET_TEMP` | 6 total | `N=2`, `int16 target_celsius` | Sailfish + Stock |
| ↳ action `12` | `SLAVE_CMD_TOGGLE_FAN` | 5 total | `N=1`, `uint8 state` (bit 0: heatsink fan `EXA_FAN`) | Sailfish + Stock |
| ↳ action `13` | `SLAVE_CMD_TOGGLE_VALVE` | 5 total | `N=1`, `uint8 state` (bit 0: **part cooling blower `EX_FAN`/`EXTRA_FET`**) | Sailfish + Stock |
| ↳ action `31` | `SLAVE_CMD_SET_PLATFORM_TEMP` | 6 total | `N=2`, `int16 target_celsius` (no-op if `!hasHBP()`) | Sailfish + Stock |
| `137` (`0x89`) | `HOST_CMD_ENABLE_AXES` | 2 | `uint8 bitfield` (bit 7 = `1` enable / `0` disable, bits 0–4 = XYZAB) | Sailfish + Stock |
| `139` (`0x8B`) | `HOST_CMD_QUEUE_POINT_EXT` | 25 | `int32 X, Y, Z, A, B` (steps), `uint32 dda_interval_us` (unaccelerated) | Sailfish + Stock |
| `140` (`0x8C`) | `HOST_CMD_SET_POSITION_EXT` | 21 | `int32 X, Y, Z, A, B` (steps) — sets ALL 5 axes simultaneously | Sailfish + Stock |
| `141` (`0x8D`) | `HOST_CMD_WAIT_FOR_PLATFORM` | 6 | `uint8 tool_id`, `uint16 ping_delay_ms`, `uint16 timeout_s` | Sailfish + Stock |
| `142` (`0x8E`) | `HOST_CMD_QUEUE_POINT_NEW` | 26 | `int32 X, Y, Z, A, B`, `uint32 duration_us`, `uint8 relative_axes` | Sailfish + Stock |
| `143` (`0x8F`) | `HOST_CMD_STORE_HOME_POSITION` | 2 | `uint8 axes` | Sailfish + Stock |
| `144` (`0x90`) | `HOST_CMD_RECALL_HOME_POSITION` | 2 | `uint8 axes` (recalls EEPROM home offsets into planner position) | Sailfish + Stock |
| `145` (`0x91`) | `HOST_CMD_SET_POT_VALUE` | 3 | `uint8 axis (0..4)`, `uint8 value (0..127)` | Sailfish + Stock |
| `146` (`0x92`) | `HOST_CMD_SET_RGB_LED` | 6 | `uint8 R, G, B, blink_rate, effect (0)` | Sailfish + Stock |
| `147` (`0x93`) | `HOST_CMD_SET_BEEP` | 6 | `uint16 freq_hz`, `uint16 duration_ms`, `uint8 effect (0)` | Sailfish + Stock |
| `148` (`0x94`) | `HOST_CMD_PAUSE_FOR_BUTTON` | 5 | `uint8 button_mask`, `uint16 timeout_s`, `uint8 options` | Sailfish + Stock |
| `149` (`0x95`) | `HOST_CMD_DISPLAY_MESSAGE` | `6 + L` | `uint8 options`, `uint8 x`, `uint8 y`, `uint8 timeout_s`, `L bytes ASCII + NUL` | Sailfish + Stock |
| `150` (`0x96`) | `HOST_CMD_SET_BUILD_PERCENT` | 3 | `uint8 percent (0..100)`, `uint8 reserved (0)` | Sailfish + Stock |
| `151` (`0x97`) | `HOST_CMD_QUEUE_SONG` | 2 | `uint8 song_id` (`0`=error4, `1`=done, `2`=error2) | Sailfish + Stock |
| `153` (`0x99`) | `HOST_CMD_BUILD_START_NOTIFICATION` | `6 + L` | `uint32 reserved (0)`, `L bytes ASCII build name + NUL` (`L <= 26`, GPX caps at `24`, LCD width `20`) | Sailfish + Stock |
| `154` (`0x9A`) | `HOST_CMD_BUILD_END_NOTIFICATION` | 2 | `uint8 flags (0)` | Sailfish + Stock |
| `155` (`0x9B`) | `HOST_CMD_QUEUE_POINT_NEW_EXT` | 32 | `int32 X, Y, Z, A, B`, `uint32 dda_rate` (steps/s), `uint8 relative_axes`, `float32 distance_mm`, `uint16/int16 feedrate_mult64` | Sailfish + Stock |
| `156` (`0x9C`) | `HOST_CMD_SET_ACCELERATION_TOGGLE` | 2 | `uint8 enable` (`1`=on, `0`=off) | Sailfish + Stock |
| `157` (`0x9D`) | `HOST_CMD_STREAM_VERSION` | 21 | `uint8 ver_hi, ver_lo, 0`, `uint32 0`, `uint16 bot_pid (0xB015)`, 11 reserved bytes | Sailfish ignores; Stock has length-check bug (`>= 11` vs 21 popped); **GPX does not emit** |
| `158` (`0x9E`) | `HOST_CMD_PAUSE_AT_ZPOS` | 5 | `float32 z_mm` | **Sailfish ONLY** (hangs Stock firmware!) |

### B.4 Motion Timing & Feedrate Quantization Formulas (Command 155)
Verified in `Sailfish/Steppers.cc:823–1005`, `MightyBoardFirmware/Steppers.cc:517–583`, `s3gProtocol.md:1258–1273`, and `GPX/gpx.c:753–798, 2285–2391`:

1. **Move Distance ($d$, mm):**
   - If any of X, Y, Z changes: $d = \sqrt{\Delta x^2 + \Delta y^2 + \Delta z^2}$ (Euclidean XYZ distance in mm).
   - If XYZ does not change (extruder-only move, e.g., retraction/prime): $d = |\Delta e|$ (in mm).
2. **Per-Axis Feedrate Clamping ($F_{\text{safe}}$, mm/min):**
   Starting from commanded feedrate $F$ (mm/min):
   - For each moving axis $i \in \{X, Y, Z\}$ with $|\Delta p_i| > 0$: if $F \cdot |\Delta p_i| / d > F_{\max,i}$, clamp $F \leftarrow F_{\max,i} \cdot d / |\Delta p_i|$.
   - For extruder $A$: if $d_{\text{xyz}} = 0$, clamp $F \leftarrow \min(F, F_{\max,A})$; else if $F \cdot |\Delta e| / d > F_{\max,A}$, clamp $F \leftarrow F_{\max,A} \cdot d / |\Delta e|$.
   - Convert to mm/s: $f = F_{\text{safe}} / 60$.
3. **Master Step Count ($N_{\max}$):**
   $$N_{\max} = \max(|\Delta s_X|, |\Delta s_Y|, |\Delta s_Z|, |\Delta s_A|, |\Delta s_B|)$$
   Both Sailfish (`Steppers.cc:929`) and Stock (`Steppers.cc:554`) immediately skip any move with $N_{\max} = 0$ or $d = 0.0$.
4. **DDA Rate & Fixed-Point Feedrate:**
   - Ideal move duration: $t = d / f$ (seconds) $= 60{,}000{,}000 \cdot d / F_{\text{safe}}$ (microseconds).
   - Master-axis DDA interval: $\tau_{\text{DDA}} = (t \cdot 10^6) / N_{\max}$ ($\mu\text{s}/\text{step}$).
   - **Command 155 `dda_rate`** (`uint32`, steps/s on longest axis):
     $$\text{dda\_rate} = \left\lfloor \frac{10^6}{\tau_{\text{DDA}}} \right\rfloor = \left\lfloor \frac{N_{\max} \cdot f}{d} \right\rfloor$$
     *(Clamped to $\ge 1$ for valid nonzero moves so firmware never divides by zero.)*
   - **Command 155 `feedrate_mult64`** (`int16` in firmware `Command.cc:839`, `uint16` in `s3gProtocol.md:1271`, representing `mm/s * 64`):
     $$\text{feedrate\_mult64} = \lfloor f \cdot 64 \rfloor \quad (\text{max safe signed int16} = 32767 \approx 511.98\text{ mm/s})$$
   - **Command 155 `relative_axes`:** X, Y, Z are encoded as **absolute step targets** (bits 0–2 = `0`, except if an axis position is unknown after homing before `M132`/`G92`, where a relative `0` is emitted); A and B are encoded as **relative step deltas** (bits 3–4 = `1`, `0x18`), with `A = -delta_e_steps` (negative = forward extrusion).
   - **Homing commands (`131`/`132`):** Use `step_delay_us = int(60_000_000 / (home_feedrate_mm_min * max_steps_per_mm_of_homed_axes))` (microseconds per step, NOT steps/sec!).

### B.5 Replicator 2 Machine Definition — Cross-Check Across 4 Primary Sources

| Parameter | Sailfish `mighty_two` (`platforms.py`, `EepromMap.hh`) | Stock `MightyBoardFirmware` (`EepromMap.hh`) | MakerBot `s3g` (`Replicator2.json`) | GPX `r2` (`std_machines.h`) / `or2` (`classic_machines.h`) | Status |
|---|---|---|---|---|---|
| **X / Y steps/mm** | `88.573186` | `88.573186` | `88.573186` | `r2`: **`88.888889`** (`3200/36`); `or2`: `88.573186` | **CONFLICT → Open Question #1** |
| **Z steps/mm** | `400` | `400` | `400` | `400` | **AGREED** |
| **A (extruder) steps/mm** | `96.275202` (neg=extrude) | `96.2752018` (neg=extrude) | `-96.275` | `96.27520187033366` (negated on write) | **AGREED** ($\le 0.0002\%$ rounding diff) |
| **X / Y max feedrate (mm/min)** | `18000` (300 mm/s) | `18000` (300 mm/s) | `12450` (207.5 mm/s) | `18000` (300 mm/s) | **CONFLICT → Open Question #2** |
| **Z max feedrate (mm/min)** | `1170` (19.5 mm/s) | `1140` (`19` mm/s int) | `1170` | `1170` | Minor (`1170` vs `1140` — Sailfish/s3g/GPX agree on `1170`) |
| **A max feedrate (mm/min)** | `1600` (26.67 mm/s) | `1800` (`30` mm/s) | `1600` | `1600` | Sailfish/s3g/GPX agree on `1600` |
| **Build volume X × Y × Z (mm)** | `285 × 152 × 155` | `285 × 150 × 160` | `285 × 148 × 150` | `285 × 152 × 155` | **MINOR CONFLICT → Open Question #2** (spec sheet says `285 × 153 × 155`) |
| **Coordinate Origin** | Center `(0, 0)` | Center `(0, 0)` | Center `(0, 0)` | Center `(0, 0)` | **AGREED** (`X ∈ [-142.5, +142.5]`, `Y ∈ [-76, +76]`, `Z ∈ [0, 155]`) |
| **Endstops / Homing Dir** | X: MAX, Y: MAX, Z: MIN | X: MAX, Y: MAX, Z: MIN | X: MAX (`G162`), Y: MAX (`G162`), Z: MIN (`G161`) | X: MAX, Y: MAX, Z: MIN | **AGREED** |
| **Home Offsets (X, Y)** | `13463`, `6643` steps (`152`, `75` mm) | `152` mm, `72` mm (single) | `152` mm, `72` mm (`G92 X152 Y72 Z0`) | Recalled from printer EEPROM via `M132` (`144`) | Printer EEPROM is authoritative via `M132` |
| **Tool / Heater / Fan Mapping** | Tool `0` → Axis `A`, no HBP (`PLATFORM_HBP_PRESENT=0`), blower=`EXTRA_FET` (`SLAVE_CMD_TOGGLE_VALVE=13`), heatsink=`EXA_FAN` (`12`) | Tool `0` → Axis `A`, single extruder, blower=`13`, heatsink=`12` | Tool `0` ("Mk8 Right") → Axis `A`, `heated_platforms: {}` | `extruder_count=1`, `has_heated_build_platform=0`, RepRap `M106/M107` → `13` | **AGREED** |
| **PID / Bot Type** | `0xB015` | `0xB015` | `45077` (`0xB015`), `45078` (`0xB016`) | `0xB015` | **AGREED** |

**Replicator 2 vs Replicator 2X Differences** (`platforms.py:209–300`, `std_machines.h:186–235`, `Replicator2X.json`):
- **Extruders:** R2 has **1** extruder (Tool 0 / Axis A, `SINGLE_EXTRUDER`); R2X has **2** extruders (Tool 0 Right / Axis A, Tool 1 Left / Axis B, toolhead X offset ~35 mm in GPX / 3100 steps in Sailfish).
- **Heated Bed:** R2 has **no heated build platform** (unheated acrylic, PLA); R2X has a **heated build platform** (`HBP`, `SLAVE_CMD_SET_PLATFORM_TEMP=31`, `HOST_CMD_WAIT_FOR_PLATFORM=141`, ABS).
- **X Build Volume:** R2 = **285 mm**; R2X = **246–250 mm** (reduced by dual carriage width).
- **Machine ID:** R2 = `0xB015`; R2X = `0xB017` (Sailfish).
- **Part Cooling Fan:** R2 ships with an active PLA blower on `EXTRA_FET` (`EX_FAN`, Pin PG5); R2X omits the stock blower (enclosure + ABS).

### B.6 Sailfish vs Stock MightyBoard Firmware — Primary Target & Byte-Stream Differences
- **Primary Target: Sailfish (v7.7 / v7.8)**, with **default byte-stream compatibility for Stock MightyBoard Firmware v7.6**.
  - *Justification:* Sailfish is the actively recommended community firmware for third-party slicers on MightyBoard Rev G/H (fixed-point planner, SDHC/FAT32 support, subdirectory browsing, fixes stock `HOST_CMD_STREAM_VERSION` bug). Crucially, every core command needed for printing (`131–137`, `139–140`, `142–151`, `153–156`) has **identical binary wire format** on both Sailfish and Stock v7.x (`Command.cc` comparison).
  - *Verified byte-stream differences:*
    1. **Command `158` (`HOST_CMD_PAUSE_AT_ZPOS`):** Sailfish-only (`Sailfish/Commands.hh:118`). Unhandled on Stock firmware (causes print hang). **Never emitted by default; gated behind an explicit `firmware = "sailfish"` option if `M322` is encountered.**
    2. **Command `157` (`HOST_CMD_STREAM_VERSION`):** Stock firmware checks `command_buffer.getLength() >= 11` while popping 21 bytes (`MightyBoardFirmware/Command.cc:796–826`) and alerts on version mismatch; Sailfish ignores it; GPX omits it. **We omit Command `157` by default**, matching GPX.
    3. **Header/Footer conventions:** Emit `HOST_CMD_BUILD_START_NOTIFICATION` (`153`, name truncated to $\le 24$ ASCII bytes + NUL) at start (if not already emitted by `M136`/`M73 P0`) and `HOST_CMD_BUILD_END_NOTIFICATION` (`154`, flags=`0`) at end (if not already emitted by `M137`/`M73 P100`). Both Sailfish and Stock require `153`/`154` for build timer, filament accounting, and LCD progress state.
    4. **SD-card constraints:** Stock firmware enforces FAT16 `< 2 GB` (`SDCard.cc:91`) and root directory; both Stock and Sailfish use `char long_name[32]` (`fat.h:75/90`, `MAX_FILE_LEN = 31` chars). Our plugin should warn if the exported filename exceeds 31 characters (or 8.3 DOS characters in strict stock mode).

### B.7 Known GPX Pitfalls (Verified in `GPX/src/gpx/gpx.c`)
1. **G-code Flavor Mismatch (`M109` & `M106`/`M107`):** Under MakerBot flavor, `M109` sets the *heated bed* (not extruder!) and `M106`/`M107` toggle the *extruder heatsink fan* (`12`) instead of the *part cooling blower* (`13`). Under RepRap/Marlin flavor, `M109` sets+waits for the extruder and `M106`/`M107` toggle the blower (`13`). **Always use RepRap/Marlin flavor for OrcaSlicer G-code.**
2. **Binary Fan Only:** X3G `SLAVE_CMD_TOGGLE_VALVE` (13) only carries 1 bit (`payload[0] & 0x01`, `Command.cc:938`). Fractional PWM (`M106 S128`) becomes ON (`1`), and `M106 S0` / `M107` becomes OFF (`0`). We must document this and report PWM-to-binary quantization in the export summary/warnings!
3. **Absolute vs Relative Extrusion (`M82`/`M83`) & `G92 E0`:** Long prints in `M82` without periodic `G92 E0` or naive floating-point delta summation in `M83` can accumulate fractional-step drift. Tracking exact target step integers (and keeping residual sub-step tracking on `G92` resets) eliminates drift completely.
4. **Heated Bed Commands (`M140`/`M190`) on Unheated Replicator 2:** GPX emits a semantic warning and skips; if `S > 0`, the user almost certainly selected the wrong filament/printer profile (e.g., ABS with heated bed). Rejecting `M140`/`M190` with `S > 0` (and safely ignoring `S0` with a note) prevents silent misprints.
5. **Partial `G92` Warning:** X3G Command `140` (`HOST_CMD_SET_POSITION_EXT`) sets all 5 axes (`X, Y, Z, A, B`) at once. When a slicer emits `G92 E0` (very common!), we only need to update our internal E-coordinate origin (because A is emitted as relative step deltas in Command `155`!) — emitting Command `140` for `G92 E0` when XYZ are known is unnecessary and risky if XYZ aren't yet known! *(Note: GPX itself warns about this at `gpx.c:4901–4906`.)*

---

## C. OrcaSlicer G-code Output for Replicator 2

### C.1 What OrcaSlicer `v2.5.2` Emits (`src/libslic3r/GCodeWriter.cpp`)
- **Why `"marlin"` (`gcfMarlinLegacy`) is the right G-code flavor for OrcaSlicer → X3G:**
  - Choosing Orca's `"sailfish"` or `"makerware"` flavor **breaks modern workflows**: `GCodeWriter.cpp:285–305, 318–319, 728–731` suppresses `M82`/`M83`, suppresses `G92 E0`, AND returns an **empty string** for `set_temperature(..., wait=true)` (so Orca omits waiting for nozzle heat!).
  - Choosing `"reprapfirmware"` emits `G10 P0 S<T>` + `M116` instead of `M104`/`M109` (`GCodeWriter.cpp:327–328, 353–354`).
  - Choosing **`"marlin"` (`gcfMarlinLegacy`)** emits clean, unambiguous RepRap/Marlin G-code: `G90`, `G21`, `M82`/`M83`, `G92 E0`, `G0`/`G1` moves (with `G1 E... F...` retraction when firmware retraction is off), `M104`/`M109`, `M106 S<0..255>`/`M106 S0`, and comments (`; ...`).
- **Hidden OrcaSlicer G-code traps verified in `GCodeWriter.cpp`:**
  - **Arc moves (`G2`/`G3` + `G17`):** Emitted if `enable_arc_fitting != "disabled"` **OR** if `z_hop_types` uses `Spiral Lift` (`GCodeWriter.cpp:951–961, 1270–1280`). Both must be disabled in the recommended profile, and any `G2`/`G3` must be rejected loudly (unless we later add arc linearization — see Open Question #4).
  - **Firmware retraction (`G10`/`G11`):** Emitted if `use_firmware_retraction = true` (`GCodeWriter.cpp:1497, 1526`). Must be off.
  - **Dynamic acceleration/jerk/PA/IS (`M204`, `M205`, `M900`, `M593`):** Emitted when acceleration/jerk control or pressure advance is enabled (`GCodeWriter.cpp:428–723`). X3G has no runtime acceleration/jerk/PA override commands (handled in MightyBoard EEPROM).
  - **Auxiliary / exhaust fans (`M106 P2` / `M106 P3`):** Emitted if `auxiliary_fan` or exhaust fan is enabled (`GCodeWriter.cpp:1606–1635`). Replicator 2 has only 1 part cooling fan (`P0`).
  - **Progress (`M73`):** `M73 P<pct>` (and optionally `R<min>`). Maps cleanly to `HOST_CMD_SET_BUILD_PERCENT` (`150`)!

### C.2 G-code Support Contract (MUST SUPPORT / REJECT / IGNORE)

| Category | Commands | Exact Behavior |
|---|---|---|
| **MUST SUPPORT** | `G0`, `G1` | Coordinated linear motion → Command `155` (`HOST_CMD_QUEUE_POINT_NEW_EXT`) with absolute XYZ steps, relative negative A steps, safe clamped feedrate, `dda_rate`, `distance`, `feedrate_mult64`. Pure feedrate-only `G1 F...` updates modal feedrate without emitting a move. |
| **MUST SUPPORT** | `G4 P<ms>` / `G4 S<s>` | Dwell → Command `133` (`HOST_CMD_DELAY`, ms). |
| **MUST SUPPORT** | `G21` | Millimeters (default; verified no-op). |
| **MUST SUPPORT** | `G28` (all or subset `X`/`Y`/`Z`), `G161` (home min), `G162` (home max) | Homing → Command `132` (`FIND_AXES_MAXIMUM` for X/Y) and Command `131` (`FIND_AXES_MINIMUM` for Z), XY before Z on `G28`. |
| **MUST SUPPORT** | `G90`, `G91`, `M82`, `M83` | Absolute/relative positioning for XYZ and independent absolute/relative mode for E. |
| **MUST SUPPORT** | `G92` | Coordinate reset. For `G92 E...` only: updates software E origin without clobbering XYZ on firmware. For XYZ resets: emits Command `140` (`HOST_CMD_SET_POSITION_EXT`) with all 5 axes. |
| **MUST SUPPORT** | `G130` | Set MightyBoard digital potentiometer Vref → Command `145` (`HOST_CMD_SET_POT_VALUE`) per specified axis (`0..127`). |
| **MUST SUPPORT** | `M0`, `M1`, `M25`, `M600` (optional pause/filament change) | Pause for button → Command `148` (`HOST_CMD_PAUSE_FOR_BUTTON`) / optional `M70` LCD message `149`. *(Confirm `M600` handling in Open Questions.)* |
| **MUST SUPPORT** | `M17`, `M18`, `M84` | Enable/disable steppers → Command `137` (`HOST_CMD_ENABLE_AXES`). |
| **MUST SUPPORT** | `M70`, `M72`, `M73` | LCD message (`149`), Play song (`151`), Set build percent (`150`, clamping `0..100`; ignoring `R` remaining minutes). |
| **MUST SUPPORT** | `M104`, `M109` (RepRap/Marlin flavor) | Extruder temp (`136` + tool action `3` `SLAVE_CMD_SET_TEMP`); `M109` additionally emits Command `135` (`HOST_CMD_WAIT_FOR_TOOL`, ping=100ms, timeout=600s) when `S > 0`. |
| **MUST SUPPORT** | `M106` (P0 or omitted), `M107`, `M126`, `M127` | Part cooling blower fan → Command `136` + tool action `13` (`SLAVE_CMD_TOGGLE_VALVE`), state=`1` if `S > 0` (or omitted on `M106`/`M126`), `0` if `S == 0` or `M107`/`M127`. Warns if `0 < S < 255` (PWM quantized to ON). |
| **MUST SUPPORT** | `M131`, `M132`, `M133`, `M135`, `M136`, `M137` | Native MakerBot start/end codes: Store home (`143`), Recall home (`144`), Wait for tool (`135`), Change tool (`134`, `T0` only), Build start (`153`), Build end (`154`). |
| **MUST SUPPORT** | `T0` | Select tool 0 (`134`, or no-op if already T0). |
| **MUST SUPPORT** | `M320` / `M321` | Enable/disable firmware segment acceleration → Command `156` (`HOST_CMD_SET_ACCELERATION_TOGGLE`). |
| **EXPLICITLY REJECT (Fatal Error w/ Line #)** | `G2`, `G3` | Arc moves not supported natively by X3G (error instructs user to set `enable_arc_fitting = "disabled"` and `z_hop_types != "Spiral Lift"`). |
| **EXPLICITLY REJECT** | `G20` | Inches mode not supported (require `G21` mm). |
| **EXPLICITLY REJECT** | `G10`, `G11` | Firmware retraction / RepRapFirmware tool-temp not supported (disable `use_firmware_retraction` and use `marlin` flavor). |
| **EXPLICITLY REJECT** | `M140 S>0`, `M190 S>0`, `M141 S>0`, `M191 S>0` | Heated bed / heated chamber commands on a printer with **no heated bed/chamber**. (`S0` is safely ignored with a warning.) |
| **EXPLICITLY REJECT** | `T1`, `T2`, ... (any tool `!= 0`) | Single-extruder Replicator 2 only supports `T0`. |
| **EXPLICITLY REJECT** | `M106 P>0` (e.g. `P2` aux, `P3` exhaust) | Replicator 2 only has one part cooling fan (`P0`). |
| **EXPLICITLY REJECT** | Out-of-bounds XYZ coordinates, invalid temperature (`< 0` or `> 280°C`), malformed syntax, unknown G/M commands | Never silently drop unknown motion/control commands; always report exact line number and source line. |
| **SAFELY IGNORE (Logged + Counted in Summary)** | Comments (`;...`, `(...)`), blank lines, `M140 S0`/`M190 S0`, `M201`–`M205` (machine accel/jerk hints), `M900` (linear advance hint, with warning), `M486` / `EXCLUDE_OBJECT_*` (object labels), `M117` (status text if not mapped to `M70`), `M400` (wait for moves, implicit on non-pipelined X3G cmds) | Counted and displayed in the post-export summary so the user sees every ignored command transparently. |

### C.3 Recommended OrcaSlicer Replicator 2 Profile
- **Printer Preset (`MakerBot Replicator 2 0.4 nozzle`):**
  - `gcode_flavor`: `"marlin"` (`gcfMarlinLegacy`)
  - `printable_area`: `["-142.5x-76", "142.5x-76", "142.5x76", "-142.5x76"]` (centered origin `(0, 0)` matching `285 × 152` mm, subject to Open Question #2)
  - `printable_height`: `155` mm
  - `nozzle_diameter`: `["0.4"]`, `filament_diameter`: `["1.75"]`
  - `use_relative_e_distances`: `false` (`M82` absolute extrusion; `M83` also supported!)
  - `use_firmware_retraction`: `false`
  - `emit_machine_limits_to_gcode`: `false`
  - `auxiliary_fan`: `false`
  - `machine_max_speed_x/y`: `150` mm/s (safe practical limit; hardware cap `300` mm/s), `z`: `19` mm/s (`1140` mm/min), `e`: `25` mm/s (`1500` mm/min)
  - `machine_start_gcode` (Sailfish + Stock compatible):
    ```gcode
    M136 ; build start notification
    M73 P0 ; build progress 0%
    G162 X Y F2500 ; home XY axes maximum
    G161 Z F1100 ; home Z axis minimum
    G92 Z-5 ; set Z to -5
    G1 Z0 F1100 ; move Z to 0
    G161 Z F100 ; slow-home Z axis minimum
    M132 X Y Z A B ; recall stored home offsets from EEPROM
    G92 E0 ; zero extruder
    G1 X-112 Y-73 Z30 F3300 ; move to front-left waiting position
    G130 X20 Y20 A20 B20 ; lower stepper Vref while heating
    M104 S[nozzle_temperature_initial_layer] T0
    M109 S[nozzle_temperature_initial_layer] T0
    G130 X127 Y127 A127 B127 ; restore stepper Vref
    G1 Z0.4 F900 ; lower to prime height
    G92 E0
    G1 X-100 Y-70 E12 F1200 ; prime purge line
    G92 E0
    ```
  - `machine_end_gcode`:
    ```gcode
    M104 S0 T0 ; turn off extruder heater
    M107 ; turn off part cooling fan
    G91 ; relative moves
    G1 E-2 F1500 ; small retract
    G90 ; absolute moves
    G1 Z150 F1100 ; lower build plate
    G162 X Y F2500 ; home XY axes
    M18 ; disable steppers
    M73 P100 ; build progress 100%
    M72 P1 ; play Ta-Da song
    M137 ; build end notification
    ```
- **Process Preset:** `filename_format`: `"{input_filename_base}.x3g"`, `enable_arc_fitting`: `"disabled"`, `z_hop_types`: `"Normal Lift"` (never `"Spiral Lift"`), `slicing_pipeline_plugin`: `["X3G Exporter (Replicator 2)"]`.
- **Filament Preset (PLA):** `hot_plate_temp`: `0`, `hot_plate_temp_initial_layer`: `0`, `enable_pressure_advance`: `false`.
- **Plugin Profile Validation:** At export time, the plugin inspects `ctx.config_value(...)` for `gcode_flavor`, `enable_arc_fitting`, `use_firmware_retraction`, `hot_plate_temp`, and `ctx.output_name` extension, emitting clear warnings or errors if misconfigured.

---

## D. Open Questions — Resolved (`2026-11-25`)

All Phase 0 conflicts and architectural choices were reviewed and approved by the project owner (recorded in [`docs/DECISIONS.md`](DECISIONS.md)):

1. **XY Steps/mm Conflict (`88.888889` vs `88.573186`) — RESOLVED (ADR-002):**
   - Default profile `r2` uses **`88.888889`** steps/mm (`3200 / (18 * 2)`, exact 18T GT2 tooth pitch, matching GPX `-m r2`).
   - Selectable profile variant `r2_factory` provides **`88.573186`** steps/mm (matching Sailfish `platforms.py`, Stock `MightyBoardFirmware` `EepromMap.hh`, MakerBot `Replicator2.json`, and GPX `-m or2`).
2. **Build Volume & XY Max Feedrate Discrepancy — RESOLVED (ADR-003):**
   - Machine limits set to **`285 × 152 × 155 mm`** and **`18000 mm/min`** (`300 mm/s`) XY, **`1170 mm/min`** Z, **`1600 mm/min`** A (matching Sailfish `mighty_two` and GPX `r2`). Recommended OrcaSlicer print speeds stay conservative (`<= 100 mm/s` print, `<= 150 mm/s` travel).
3. **OrcaSlicer Integration Hook — RESOLVED (ADR-004):**
   - **Rank 1 approved:** Pure-Python `.whl` combining `SlicingPipeline` (`psGCodePostProcess` in-place `.pp` conversion) + `Pages` (main-window **X3G Export** tab) + `Script` (summary / manual convert), paired with Replicator 2 preset setting `filename_format = "{input_filename_base}.x3g"`.
4. **License & Repo Layout — RESOLVED (ADR-001):**
   - **MIT License** (established in `https://github.com/BasilSaeedBari/OrcaX3G`), implemented 100% **clean-room** from primary protocol specifications with zero code copied from GPL/AGPL references.
   - Standard layout approved: `docs/`, `src/orca_x3g/`, `profiles/`, `tests/`, `pyproject.toml`, `.github/workflows/ci.yml`.
5. **Minor G-code & Filename Policy — RESOLVED (ADR-006):**
   - **`M600` (filament change):** Maps to `HOST_CMD_DISPLAY_MESSAGE` (`149`, `"Change Filament"`) + `HOST_CMD_PAUSE_FOR_BUTTON` (`148`, center button, no timeout).
   - **Export path ending in `.gcode`:** Rejected loudly by default with an actionable error message (preventing binary X3G data from silently overwriting a `.gcode` file), with an opt-in plugin config override (`allow_non_x3g_extension: false`).

**Open Questions Remaining:** None.

---

## E. Proposed Plan (Phases 1–4)

- **Phase 1 — Design (`docs/DESIGN.md`):** Formalize module boundaries, exact integer-step state machine and zero-drift quantization math (absolute step targets for XYZ and E, relative negative A emission in Command 155), error taxonomy with line numbers, determinism guarantees, and Orca `v2.5.2` multi-capability glue. *(Stop for approval.)*
- **Phase 2 — Implementation:** Implement pure-Python 3.12+ `orca_x3g` package (`profile`, `parser`, `state`, `planner`, `encoder`, `cli`, `plugin`), machine profile data with source annotations, recommended OrcaSlicer Replicator 2 JSON presets, and `gcode2x3g` CLI.
- **Phase 3 — Testing & Equivalence (`docs/EQUIVALENCE.md` + `tests/`):**
  - Formalize equivalence tolerances in `docs/EQUIVALENCE.md`.
  - Build independent spec-driven X3G decoder/VM simulator and G-code simulator + equivalence checker + JSON/Markdown report & matplotlib layer overlay / error-histogram plotter.
  - Compile `markwal/GPX` from source at pinned commit `28f346005ee37117b1f042dd66570d108726a02f` and run automated oracle comparisons classifying every byte/toolpath difference.
  - Run full pytest suite: unit, golden, Hypothesis property tests, 1,000,000-line drift test, 5 real-job OrcaSlicer `v2.5.2` sliced models (cube, thin vase, high-retraction, large footprint, supports), negative tests, fuzzing, performance/memory profiling, and OrcaSlicer plugin integration verification.
- **Phase 4 — Packaging & Documentation:** Build pure-Python `.whl` (`orca_x3g-*.whl`), `LICENSE`, `CHANGELOG.md`, `README.md`, optional 1-page hardware validation checklist (`docs/HARDWARE_CHECKLIST.md`), and final Verification Summary.
