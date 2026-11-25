# OrcaX3G — MakerBot Replicator 2 `.x3g` Exporter & Plugin for OrcaSlicer `v2.5.2`

A **100% clean-room, pure-Python, MIT-licensed** OrcaSlicer `v2.5.2` plugin and standalone CLI (`gcode2x3g`) that exports binary **`.x3g`** files directly from OrcaSlicer for the **MakerBot Replicator 2** (single extruder, no heated build plate, MightyBoard Rev G/H, Sailfish primary & Stock `v7.x` byte-compatible) — backed by an independent spec-driven X3G decoder/VM, mathematical equivalence proofs, 1,000,000-line zero-drift testing, 5 real OrcaSlicer `v2.5.2`-sliced models, and external oracle cross-checks against `markwal/GPX`.

> **This repository is both production toolchain code and a complete primary-source engineering dossier.** Every constant, opcode, byte layout, CRC polynomial, firmware quirk, and OrcaSlicer `v2.5.2` plugin hook is traced to pinned commits and exact file/line references under [`docs/`](docs/).

---

## Documentation Map (Research, Firmware Findings, Design & Proofs)

| Document | Contents |
|---|---|
| [`docs/SOURCES.md`](docs/SOURCES.md) | Complete primary-source ledger (pinned commits, URLs, file/line citations, license audit for `makerbot/s3g`, `markwal/GPX`, `Sailfish`, `MightyBoardFirmware`, and `OrcaSlicer v2.5.2`) |
| [`docs/RESEARCH.md`](docs/RESEARCH.md) | Phase 0 research: OrcaSlicer `v2.5.2` plugin architecture & hooks, S3G/X3G binary protocol, Replicator 2 kinematics, Sailfish vs Stock differences, and OrcaSlicer G-code emission |
| [`docs/FIRMWARE_AND_LEGACY_FINDINGS.md`](docs/FIRMWARE_AND_LEGACY_FINDINGS.md) | Deep-dive engineering findings from analyzing Sailfish, Stock MightyBoardFirmware, `makerbot/s3g`, GPX, and OrcaSlicer `v2.5.2` (the `88.888889` vs `88.573186` XY split, negative `A` extrusion sign, blower fan Action `13` vs heatsink Action `12`, Command `140` 5-axis overwrite hazard, Command `157` Stock bug, Orca `sailfish` flavor suppression trap, and `PluginAuditManager` keyword traps) |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Architecture Decision Records (ADR-001 through ADR-007) resolving every source conflict and design trade-off |
| [`docs/DESIGN.md`](docs/DESIGN.md) | Phase 1 software architecture, zero-drift integer-step numeric proof, complete G-code → X3G mapping table, error model, determinism, and multi-capability Orca plugin design |
| [`docs/EQUIVALENCE.md`](docs/EQUIVALENCE.md) | Formal mathematical definition of physical toolpath equivalence (Criteria E1–E8) and justified per-axis, extrusion, feedrate, ordering, and timing tolerances |
| [`docs/VERIFICATION_SUMMARY.md`](docs/VERIFICATION_SUMMARY.md) | Complete test matrix mapping every equivalence criterion to executed tests, observed quantitative metrics, GPX oracle diffs, benchmarks, and remaining risks |
| [`docs/HARDWARE_CHECKLIST.md`](docs/HARDWARE_CHECKLIST.md) | Optional 1-page physical MakerBot Replicator 2 bench verification checklist (explicitly marked **NOT RUN** — all automated verification runs in software) |

---

## Quick Start — OrcaSlicer `v2.5.2` Plugin

### 1. Build (or download from CI) the pure-Python wheel
```bash
python3 -m pip install build
python3 -m build --wheel
# Produces: dist/orca_x3g-0.1.0-py3-none-any.whl
```
The wheel has **zero runtime dependencies** (`dependencies = []`, `py3-none-any`), matching OrcaSlicer `v2.5.2`'s embedded CPython `3.12.13`.

### 2. Install into OrcaSlicer `v2.5.2`
1. Open OrcaSlicer `v2.5.2` → **Plugins** (or **File → Plugins → Manage Plugins**).
2. Click **Install Plugin** and select `dist/orca_x3g-0.1.0-py3-none-any.whl`.
3. Enable the plugin. OrcaX3G registers three capabilities with **zero extra permission prompts**:
   - **Slicing Pipeline:** `X3G Exporter (Replicator 2)` (`Step.psGCodePostProcess`)
   - **Page Tab:** `X3G Export` (shows latest export metrics, warnings, and profile checklist)
   - **Script Action:** `X3G Last Export Summary` (**File → Plugins → Run**)

### 3. Import the Recommended Replicator 2 Profiles
Import all three JSON presets from [`profiles/`](profiles/) via **File → Import → Import Configs...**:
- [`profiles/MakerBot_Replicator_2_0.4_nozzle.json`](profiles/MakerBot_Replicator_2_0.4_nozzle.json) — Centered `285 × 152 × 155 mm` bed, `gcode_flavor = "marlin"`, verified speed limits, native R2 start/end G-code.
- [`profiles/0.20mm_Standard_Replicator_2.json`](profiles/0.20mm_Standard_Replicator_2.json) — `filename_format = "{input_filename_base}.x3g"`, `enable_arc_fitting = "disabled"`, `slicing_pipeline_plugin = ["X3G Exporter (Replicator 2)"]`.
- [`profiles/Replicator_2_Generic_PLA.json`](profiles/Replicator_2_Generic_PLA.json) — Bed temperature `0 °C`, pressure advance off, binary part-cooling fan.

### 4. Slice & Export Directly to `.x3g`
1. Select the Replicator 2 printer, process, and PLA presets.
2. Click **Slice plate** — the G-code previewer works normally because OrcaX3G runs on the export working copy (`.pp`), leaving preview intact.
3. Click **Export G-code** — the Save dialog defaults to `*.x3g`. OrcaSlicer invokes `X3G Exporter (Replicator 2)`, converts to binary X3G in-place, and writes the final `.x3g` file directly to your SD card or disk.
4. Open the **X3G Export** tab (or **File → Plugins → Run → X3G Last Export Summary**) to review file size, move count, layers, filament length, ignored commands, and warnings.

> **Why `gcode_flavor = "marlin"` instead of Orca's built-in `"sailfish"`?**  
> Reading OrcaSlicer `v2.5.2`'s `GCodeWriter.cpp` revealed that selecting `sailfish` or `makerware` suppresses `M82`/`M83` extrusion mode headers, suppresses `G92 E0` resets, and turns `M109` nozzle heating waits into no-ops! Using `marlin` gives OrcaX3G complete, explicit G-code while our start/end G-code handles native Replicator 2 homing (`G162`/`G161`/`M132`) and digipots (`G130`). See [`docs/FIRMWARE_AND_LEGACY_FINDINGS.md`](docs/FIRMWARE_AND_LEGACY_FINDINGS.md).

---

## Standalone CLI (`gcode2x3g`)

No OrcaSlicer installation is required to convert or verify G-code:

```bash
python3 -m pip install -e .
gcode2x3g input.gcode output.x3g
```

Common options:
```bash
# Use MakerBot factory/EEPROM XY steps/mm (88.573186 instead of exact GT2 88.888889)
gcode2x3g --profile r2_factory input.gcode output.x3g

# Target Stock v7.x firmware (rejects Sailfish-only M322 and warns on non-8.3 SD filenames)
gcode2x3g --firmware stock input.gcode PART01.X3G

# Emit serial S3G wire-framed packets ([0xD5][len][payload][CRC-8-Maxim])
gcode2x3g --framed input.gcode serial.s3g

# Write structured JSON export summary alongside .x3g
gcode2x3g --summary-json summary.json input.gcode output.x3g
```

---

## How Conversion Works

```
G-code stream
   │
   ▼  1. parser.py          Strict deterministic lexer (';' and '(...)', XOR checksums, compact tokens)
   ▼  2. state.py +         Modal state machine (G90/G91, M82/M83, known-axes mask) +
         machine_profile.py Verified Replicator 2 constants (r2 / r2_factory)
   ▼  3. planner.py         Zero-drift absolute-target XYZ & cumulative-physical E step quantizer,
                            per-axis speed clamping, Command 155 DDA rate & feedrate_mult64
   ▼  4. encoder.py         Clean-room little-endian binary serializer + optional S3G framing/CRC-8
   ▼
Binary .x3g file (unframed Host Buffered Commands, <= 32 B/command)
```

### Supported, Ignored, and Rejected G-code

| Category | Commands | Behavior |
|---|---|---|
| **Supported** | `G0`/`G1`, `G4` (`P`/`S`), `G21`, `G28`, `G90`/`G91`, `G92`, `G130`, `G161`/`G162`, `M0`/`M1`/`M25`, `M17`/`M18`/`M84`, `M70`, `M72`, `M73`, `M82`/`M83`, `M104`/`M109`, `M106`/`M107`, `M126`/`M127`, `M131`/`M132`, `M133`, `M135`/`T0`, `M136`/`M137`, `M320`/`M321`, `M322` (Sailfish), `M600` | Translated deterministically to verified X3G opcodes `131`–`158` |
| **Safely Ignored (counted in summary)** | `;` / `(...)` comments, `M140 S0`/`M190 S0`/`M141 S0`/`M191 S0`, `M201`–`M205`, `M400`, `M486`, `M900` (with warning), `M117`, `M108`, `EXCLUDE_OBJECT_*` | Counted per opcode in `ExportSummary.ignored_commands` |
| **Rejected Loudly (exact line # + hint)** | `G2`/`G3` arcs, `G17`, `G20` inches, `G10`/`G11` firmware retraction, `M140`/`M190` `S>0` (no heated bed), `M141`/`M191` `S>0`, `T1+` / `M104 T1+`, `M106 P>0`, `T > 280 °C`, out-of-bounds XYZ, `M322` in `stock` mode, missing feedrate, unknown G/M | Raises typed `OrcaX3GError` immediately; never writes partial/corrupt `.x3g` |

---

## Running the Verification Suite

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -v
```

See [`docs/VERIFICATION_SUMMARY.md`](docs/VERIFICATION_SUMMARY.md) for the complete criterion-by-criterion proof table, observed worst-case errors across 5 real OrcaSlicer `v2.5.2`-sliced models (`41,975` moves), `0.00 µm` step-for-step agreement with `markwal/GPX`, 1,000,000-line zero-drift proof, and performance/memory benchmarks.

---

## Troubleshooting & Known Limitations

- **Plugin refuses to save as `.gcode`:** By design, `X3GPostProcessCapability` rejects non-`.x3g` output filenames so you cannot accidentally overwrite a `.gcode` file with binary X3G. Set `filename_format = "{input_filename_base}.x3g"` in your process preset (already set in [`profiles/0.20mm_Standard_Replicator_2.json`](profiles/0.20mm_Standard_Replicator_2.json)), or enable `allow_non_x3g_extension` in plugin settings.
- **Parts measure ~0.35% off in X/Y:** Switch `machine_profile` from `r2` (`88.888889` steps/mm, exact 18T GT2 pitch) to `r2_factory` (`88.573186` steps/mm, MakerBot factory EEPROM figure) in plugin config or via `--profile r2_factory`.
- **PWM fan warning (`M106 S128`):** The Replicator 2 blower fan (`SLAVE_CMD_TOGGLE_VALVE`, Action `13`) is hardware binary ON/OFF only; any `S > 0` maps to ON with a user-visible warning.
- **OrcaSlicer API stability:** Targets OrcaSlicer `v2.5.2` (`Step.psGCodePostProcess` is marked research/experimental upstream). If a future OrcaSlicer release adds a dedicated `Exporter` capability hook, `src/orca_x3g/plugin.py` is isolated from the pure-Python core so only the thin glue layer needs updating.
- **Single-extruder Replicator 2 only:** Replicator 2X (dual extruder + heated bed) is intentionally out of scope for v0.1.0; `T1` and heated-bed commands are rejected loudly.

---

## License

MIT License — see [`LICENSE`](LICENSE). All code is a 100% clean-room implementation from primary protocol specifications and firmware analysis (see [`docs/SOURCES.md`](docs/SOURCES.md) Section 7).
