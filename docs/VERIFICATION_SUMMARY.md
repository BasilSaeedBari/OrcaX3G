# Verification Summary — OrcaX3G (`v0.1.0`)

**Date:** 2026-11-25  
**Test Suite Run:** `.venv/bin/pytest -v` → **92 passed, 0 failed, 0 skipped** (`77.87 s`, Python `3.14.7`, pytest `9.2.0`, Hypothesis `6.170.17`)  
**Slicer Tested Against:** Real **OrcaSlicer `v2.5.2`** (`com.orcaslicer.OrcaSlicer`)  
**External Oracle Tested Against:** Compiled **`markwal/GPX`** (`28f346005ee37117b1f042dd66570d108726a02f`)  
**Hardware Bench Status:** **NOT RUN** (100% software-verified; see [`docs/HARDWARE_CHECKLIST.md`](HARDWARE_CHECKLIST.md) for optional operator bench checks)

---

## 1. Criterion-to-Test Proof Matrix

| Criterion (`docs/EQUIVALENCE.md`) | Automated Test(s) | Observed Result | Verdict |
|---|---|---|---|
| **E1 — XYZ Position Accuracy (`<= 0.5 / spm`) & Zero Cumulative Drift** | `test_quantization_feedrate.py`, `test_golden.py`, `test_properties.py` (40 Hypothesis programs), `test_drift_million.py` (1,000,000 moves), `test_real_jobs.py` (5 OrcaSlicer models, 41,975 moves) | Worst-case across all 5 real jobs: **X `5.500 µm`** (`<= 5.625 µm`), **Y `5.500 µm`** (`<= 5.625 µm`), **Z `1.000 µm`** (`<= 1.250 µm`). After **1,000,000** irrational moves, final XYZ error remained `<= 0.5` step (zero drift). | **PROVEN** |
| **E2 — Extrusion Accuracy (`<= 0.5` step cum, `<= 1.0` step seg) & Sign** | `test_quantization_feedrate.py`, `test_golden.py`, `test_properties.py`, `test_drift_million.py`, `test_real_jobs.py` | Worst-case across all 5 real jobs: **cum E `5.193 µm`** (`<= 5.193 µm`), **per-seg E `10.366 µm`** (`<= 10.387 µm`). Final E error on `4,846.23 mm` job = **`2.273 µm`**. Forward extrusion verified as negative `A` steps; retractions positive `A` steps. Survives `G92 E0` and `M82`/`M83` switches with zero drift over 1,000,000 moves. | **PROVEN** |
| **E3 — Motion Count & Sub-Step Fidelity** | `test_quantization_feedrate.py::test_sub_step_moves_accumulate_without_losing_residual`, `test_real_jobs.py`, `test_properties.py` | 1-to-1 match on all 41,975 non-zero-step moves across 5 real jobs (and 97 sub-step moves in `02_thin_vase` cleanly accumulated without losing residual). Zero dropped/merged/reordered moves. | **PROVEN** |
| **E4 — Feedrate (`feedrate_mult64`) & `dda_rate` Accuracy** | `test_quantization_feedrate.py::test_feedrate_clamping_and_dda_table` (6 cases), `test_real_jobs.py`, `test_properties.py` | Worst-case across all 5 real jobs: **`feedrate_mult64` error `0.007292 mm/s`** (`<= 0.007813 mm/s`); **`dda_rate` error `0.5000 steps/s`** (`<= 0.5001 steps/s`), max relative DDA speed error **`0.0238%`**. Per-axis clamping verified for XY (`18000`), Z (`1170`), and A (`1600` mm/min). | **PROVEN** |
| **E5 — Non-Motion Ordering & Exact Values** | `test_golden.py` (4 fixtures), `test_real_jobs.py` (740 control events across 5 jobs), `test_properties.py` | Exact order & value match for nozzle temp (`136` action `3`), heater wait (`135`), blower fan (`136` action `13`), dwells (`133`), homing (`131`/`132`), home recall (`144`), digipots (`145`), tool change (`134`), axis enable (`137`), pauses & `M600` (`148`/`149`), progress (`150`), song (`151`), accel toggle (`156`), Sailfish pause-at-Z (`158`), and paired `153`/`154` build notices. | **PROVEN** |
| **E6 — Total Estimated Print Time (`<= max(0.5 s, 0.5%)`)** | `test_real_jobs.py`, `test_properties.py`, `test_golden.py` | Worst-case across all 5 real jobs: **`0.0024%`** relative error (max absolute `0.0401 s` on a `2,715.9 s` print — 200x tighter than the `0.5%` bound). | **PROVEN** |
| **E7 — Binary Framing, Payload Length (`<= 32 B`) & CRC-8-Maxim** | `test_encoder_crc.py` (10 tests: official `s3g/tests/pi_test_Crc.py` vectors, all 256 single bytes, hand-computed opcodes `131`–`158`), `test_properties.py` | Table CRC matches independent bitwise CRC on all 3 official vectors (`0x00`, `0xB4`, `0x3C`) and all 256 single bytes. Every emitted command `<= 32` bytes. Raw & S3G-framed streams decode identically; corrupted CRC / start byte / truncation rejected. | **PROVEN** |
| **E8 — Determinism** | `test_real_jobs.py` (5 real jobs), `test_properties.py` (40 Hypothesis programs) | Repeated runs produce byte-identical `.x3g` output matching checked-in fixtures (`sha256` identical). | **PROVEN** |
| **External Oracle (`markwal/GPX`)** | `test_oracle_gpx.py` (5 jobs vs `gpx -r -m r2`, plus `r2_factory` vs `gpx -I -r -m r1 -m or2`) | **41,975 / 41,975 moves match GPX with `0.00 µm` max deviation on X, Y, Z, and cumulative E!** All packet-level differences classified cleanly (build name, GPX's 1 legacy `139` after `M132`, GPX's 107 `140` packets on `G92 E0`, `M73` dedup, `wait_temp` timeout). Uncovered upstream GPX `-m or2` bug (`gpx.c:168`). | **PROVEN** |
| **Error Model & Loud Rejection** | `test_negative.py` (31 cases), `test_parser.py` (12 cases) | Every unsupported/invalid construct (`G2`/`G3`, `G17`, `G20`, `G10`/`G11`, `M140/M190 S>0`, `T1`, `M106 P2`, `T>280`, OOB XYZ, `M322` in `stock`, missing `F`, bad checksums, syntax errors) fails loudly with exact line number; CLI exits non-zero without writing partial `.x3g`. | **PROVEN** |
| **Robustness / Fuzzing** | `test_fuzz.py` (600+ Hypothesis fuzz inputs + 100 random byte mutations + all prefix truncations) | Zero unhandled exceptions or hangs in `parser`, `convert_text`, or `decoder` (raw & framed). | **PROVEN** |
| **OrcaSlicer `v2.5.2` Plugin Integration & Audit Safety** | `test_plugin_integration.py` (3 tests), wheel build verification | All 3 capabilities register via `@orca.plugin`; in-place `.pp → .x3g` output passes equivalence; zero `orca.host.ui.*` calls on worker thread; non-`.x3g` destinations rejected by default; 6 misconfigured profile warnings verified; zero audit-forbidden keywords (`conf`/`secret`/`cert`) in any file or wheel entry. | **PROVEN** |

---

## 2. Real-Job Verification Results (Sliced in Real OrcaSlicer `v2.5.2`)

All five jobs were sliced directly by **OrcaSlicer `v2.5.2`** using [`profiles/`](../profiles/). Full `.gcode`, `.x3g`, JSON/Markdown reports, `layer_overlay.png`, and `error_histogram.png` are checked into [`tests/fixtures/real_jobs/`](../tests/fixtures/real_jobs/).

| Job | Moves | X3G Size | Max X / Y Err | Max Z Err | Max E Cum / Seg Err | Time Δ (%) | GPX Oracle Max Δ (X/Y/Z/E) | Status |
|---|---|---|---|---|---|---|---|---|
| `01_calibration_cube` | 2,617 | 84,227 B | 5.50 / 5.50 µm | 0.00 µm | 5.19 / 10.14 µm | 0.0017% | **0.00 / 0.00 / 0.00 / 0.00 µm** | **PASS** |
| `02_thin_vase` (spiral) | 5,556 (+97 sub-step) | 178,277 B | 5.50 / 5.50 µm | 1.00 µm | 5.19 / 10.14 µm | 0.0001% | **0.00 / 0.00 / 0.00 / 0.00 µm** | **PASS** |
| `03_retraction_towers` | 7,312 | 234,504 B | 5.50 / 5.50 µm | 0.00 µm | 5.19 / 9.92 µm | 0.0013% | **0.00 / 0.00 / 0.00 / 0.00 µm** | **PASS** |
| `04_large_footprint` (250×124 mm) | 22,183 | 710,505 B | 5.50 / 5.50 µm | 0.00 µm | 5.19 / 10.37 µm | 0.0015% | **0.00 / 0.00 / 0.00 / 0.00 µm** | **PASS** |
| `05_overhang_supports` (supports) | 4,307 | 138,323 B | 5.50 / 5.50 µm | 1.00 µm | 5.19 / 10.23 µm | 0.0024% | **0.00 / 0.00 / 0.00 / 0.00 µm** | **PASS** |

---

## 3. Performance & Peak Memory Benchmark (Pure Python)

Measured on Linux (`Python 3.14.7`) converting real OrcaSlicer `v2.5.2` G-code files end-to-end (`convert_file`):

| Tier | Job | G-code Lines | X3G Moves | X3G Output | Wall Time | Throughput | Peak Heap (`tracemalloc`) |
|---|---|---:|---:|---:|---:|---:|---:|
| Small | `01_calibration_cube` | 5,123 | 2,617 | 84,227 B | 109.8 ms | 23,841 moves/s | 1.15 MB |
| Medium | `03_retraction_towers` | 13,764 | 7,312 | 234,504 B | 868.7 ms | 8,417 moves/s | 2.90 MB |
| Large | `04_large_footprint` | 27,860 | 22,183 | 710,505 B | 1,105.7 ms | 20,062 moves/s | 8.69 MB |

**Conclusion:** Pure Python achieves `~20,000 moves/s` and `< 9 MB` peak RAM on a 710 KB `.x3g` print job. No Rust or native C extension is required, keeping the plugin a single portable `py3-none-any` wheel.

---

## 4. Remaining Risks & Mitigations

1. **No physical MakerBot Replicator 2 hardware run yet:** All protocol checks were verified against firmware source code (`Sailfish`, `MightyBoardFirmware`), `makerbot/s3g`, and compiled `markwal/GPX`, but not yet executed on physical ATmega1280/2560 hardware. **Mitigation:** Follow [`docs/HARDWARE_CHECKLIST.md`](HARDWARE_CHECKLIST.md) for the first live print with a hand near the power switch.
2. **OrcaSlicer `v2.5.2` GUI plugin loading tested via source-verified mock harness (not interactive desktop GUI):** While the CLI slicer (`OrcaSlicer 2.5.2` Flatpak) sliced all 5 real jobs using our presets and the wheel matches `read_wheel_plugin_metadata` & `PluginAuditManager` source rules, live desktop GUI installation of `.whl` has not been clicked through interactively. **Mitigation:** Included in Step 5 of [`docs/HARDWARE_CHECKLIST.md`](HARDWARE_CHECKLIST.md).
3. **OrcaSlicer `Step.psGCodePostProcess` experimental API stability:** OrcaSlicer `v2.5.2` marks `orca.slicing` as research/experimental; future releases may rename hooks or introduce a native `Exporter` capability. **Mitigation:** All conversion logic is completely decoupled from `plugin.py`.
4. **Firmware-side JKN / acceleration timing:** Constant-speed idealized print time matches within `<= 0.0024%`, whereas real wall-clock print time on Sailfish includes ATmega acceleration/JKN deceleration ramps and nozzle warm-up delay (identical to GPX because Command `155` fields match).
5. **XY calibration difference (`88.888889` vs `88.573186`):** Printers whose EEPROM uses factory `88.573186` may measure `~0.35%` different on `r2` vs `r2_factory`. **Mitigation:** Both profiles are exposed (`--profile r2` / `--profile r2_factory`) and verified against GPX.
