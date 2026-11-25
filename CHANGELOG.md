# Changelog

All notable changes to **OrcaX3G** are documented in this file.

## [0.1.0] - 2026-11-25

### Added
- **Clean-room pure-Python G-code → X3G converter (`src/orca_x3g/`):**
  - Zero-drift absolute-target integer-step quantizer for X, Y, Z, and cumulative-physical E tracking (surviving arbitrary `G92 E0` resets and `M82`/`M83` switches).
  - Verified MakerBot Replicator 2 machine profiles (`r2` at `88.888889` XY steps/mm default, `r2_factory` / `or2` at `88.573186` XY steps/mm) with primary-source citations per field.
  - Strict deterministic G-code parser (`;` and `(...)` comments, XOR checksum verification, compact tokens, bare axis flags, loud syntax & unsupported-command rejection with line numbers).
  - Clean-room X3G binary serializer (`Command 131–158`, `SLAVE_CMD` actions `3/12/13/31`) and S3G serial wire framing with CRC-8-Maxim (`0x8C` reflected, init `0x00`).
  - Standalone CLI (`gcode2x3g`) with `--profile`, `--firmware`, `--framed`, `--build-name`, `--summary-json`, and `--quiet`.
- **OrcaSlicer `v2.5.2` Multi-Capability Plugin (`src/orca_x3g/plugin.py`):**
  - `X3GPostProcessCapability` (`Step.psGCodePostProcess`) converting `.pp` temp G-code to binary `.x3g` in-place with zero audit popups and strict worker-thread safety.
  - `X3GExportPageCapability` themed **X3G Export** status tab.
  - `X3GSummaryScriptCapability` modal export summary viewer (`orca.host.ui.message`).
  - Audit-safe naming (`ADR-007`) and profile validator warning on misconfigured OrcaSlicer presets.
- **Recommended OrcaSlicer `v2.5.2` Presets (`profiles/`):**
  - Machine, 0.20mm process (`filename_format = "{input_filename_base}.x3g"`, `enable_arc_fitting = "disabled"`), and Replicator 2 PLA filament presets (validated directly in OrcaSlicer `v2.5.2`).
- **Independent Verification & Equivalence Suite (`docs/EQUIVALENCE.md`, `tests/`):**
  - Spec-driven independent X3G decoder & virtual machine (`decoder.py`), independent G-code reference simulator (`gcode_sim.py`), equivalence checker (`equivalence.py`), and JSON/Markdown/matplotlib report generator (`report.py`).
  - Complete `pytest` suite: CRC/framing/encoder vectors, quantization/feedrate table, parser tests, golden fixtures, Hypothesis property tests, 1,000,000-line drift test, 5 real OrcaSlicer `v2.5.2`-sliced models with checked-in reports & plots, external `markwal/GPX` oracle comparison, negative rejection tests, fuzz tests, performance/memory benchmark, and OrcaSlicer plugin integration tests.
- **Comprehensive Research & Engineering Documentation (`docs/`):**
  - `SOURCES.md`, `RESEARCH.md`, `DECISIONS.md`, `FIRMWARE_AND_LEGACY_FINDINGS.md`, `DESIGN.md`, `EQUIVALENCE.md`, `VERIFICATION_SUMMARY.md`, and `HARDWARE_CHECKLIST.md`.
