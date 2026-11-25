# Phase 1 Design: OrcaX3G Architecture, Numerics & Plugin Specification

**Date:** 2026-11-25  
**Target:** OrcaSlicer `v2.5.2` (embedded CPython `3.12.13`) → MakerBot Replicator 2 (MightyBoard Rev G/H, Sailfish primary / Stock v7.x byte-compatible)  
**License:** MIT (100% clean-room pure Python; see [`docs/SOURCES.md`](SOURCES.md) and [`docs/DECISIONS.md`](DECISIONS.md))

---

## 1. System Architecture & Module Separation

All core conversion logic is strictly **UI-free, pure Python 3.12+ (stdlib only)**, and completely decoupled from OrcaSlicer's embedded `orca` module. Per **ADR-007**, **no module, file, or storage path contains `"conf"`, `"secret"`, or `"cert"`** (avoiding OrcaSlicer `PluginAuditManager`'s unconditional substring deny-list).

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  Entry Points                                                                │
│  • cli.py       (gcode2x3g standalone CLI, zero Orca dependency)             │
│  • plugin.py    (OrcaSlicer v2.5.2 glue: SlicingPipeline + Pages + Script)   │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │ ConversionOptions + G-code stream
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  converter.py   (Pure orchestration: read → parse → state/plan → encode)     │
│                                                                              │
│  ┌──────────────┐   ┌───────────────────────┐   ┌─────────────────────────┐  │
│  │ 1. parser.py │──▶│ 2. state.py           │──▶│ 3. planner.py           │  │
│  │ G-code lexer │   │ Modal state machine + │   │ Step quantizer +        │  │
│  │ & validator  │   │ machine_profile.py    │   │ feedrate/DDA calculator │  │
│  └──────────────┘   └───────────────────────┘   └────────────┬────────────┘  │
│                                                              │ X3GCommand IR │
│                                                              ▼               │
│                                                 ┌─────────────────────────┐  │
│                                                 │ 4. encoder.py           │  │
│                                                 │ Binary X3G serializer + │  │
│                                                 │ optional S3G framing/CRC│  │
│                                                 └─────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│  Independent Verification Subsystem (Phase 3 — shares ZERO encoder code)     │
│  • decoder.py      (Spec-driven X3G binary decoder & virtual machine)        │
│  • gcode_sim.py    (Reference G-code idealized toolpath simulator)           │
│  • equivalence.py  (Tolerance checker comparing G-code VM vs X3G VM)         │
│  • report.py       (JSON + Markdown equivalence reports & matplotlib plots)  │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Package Layout (`src/orca_x3g/`)

| Module | Responsibility | External Imports |
|---|---|---|
| `errors.py` | Exception hierarchy carrying `line_number`, `raw_line`, and actionable remediation hints | stdlib only |
| `machine_profile.py` | Verified Replicator 2 machine profiles (`r2`, `r2_factory`) with per-field primary-source metadata | stdlib only |
| `parser.py` | Line-by-line deterministic G-code lexer/parser (strips `;` and `(...)` comments, normalizes case, parses words & checksums, rejects malformed syntax) | stdlib only |
| `commands.py` | Immutable typed IR dataclasses for every emitted X3G command (opcode constants + fields) | stdlib only |
| `state.py` | Printer modal state machine (`G90`/`G91`, `M82`/`M83`, active tool, feedrate, temperature, fan, known-axes mask, layer counter, build start/end tracking) | stdlib only |
| `planner.py` | Zero-drift integer-step quantizer, bounds checker, per-axis feedrate clamper, DDA rate & `feedrate_mult64` calculator, G-code → `X3GCommand` translator | stdlib only |
| `encoder.py` | Little-endian binary serializer (`struct.pack`) for raw `.x3g` streams + optional S3G wire framing (`0xD5`, length, CRC-8-Maxim) | stdlib only |
| `converter.py` | High-level `convert_lines()` / `convert_file()` API returning `ConversionResult` (`x3g_bytes`, `ExportSummary`) | stdlib only |
| `cli.py` | `gcode2x3g in.gcode out.x3g` CLI with `--profile`, `--firmware`, `--framed`, `--build-name`, `--summary-json` flags | stdlib only |
| `plugin.py` | OrcaSlicer `v2.5.2` multi-capability wrapper (`X3GPostProcessCapability`, `X3GExportPageCapability`, `X3GSummaryScriptCapability`, `@orca.plugin OrcaX3GPlugin`) | `orca` (only when imported inside OrcaSlicer) |
| `__init__.py` | Exports public pure-Python API; lazily registers `OrcaX3GPlugin` **only** if `orca` is importable | conditional `orca` |
| `decoder.py`, `gcode_sim.py`, `equivalence.py`, `report.py` | Phase 3 independent spec-driven verification & reporting tools (`matplotlib` optional dev/test extra only) | stdlib (+ optional `matplotlib` in `report.py`) |

---

## 2. Machine Profile Data Design (`machine_profile.py`)

Profiles are immutable `@dataclass(frozen=True)` records with explicit provenance strings per parameter (sourced from [`docs/SOURCES.md`](SOURCES.md)):

| Field | `r2` (Default — ADR-002/003) | `r2_factory` (EEPROM default variant) | Source |
|---|---|---|---|
| `profile_id` | `"r2"` | `"r2_factory"` | ADR-002 |
| `x_steps_per_mm` | `88.888889` (`3200 / 36`) | `88.573186` | `GPX/std_machines.h:188` vs `Sailfish/platforms.py:223` & `MightyBoardFirmware/EepromMap.hh:50` |
| `y_steps_per_mm` | `88.888889` (`3200 / 36`) | `88.573186` | Same |
| `z_steps_per_mm` | `400.0` | `400.0` | All 4 primary sources |
| `a_steps_per_mm` | `96.27520187033366` | `96.27520187033366` | `GPX/std_machines.h:191` & `Sailfish/EepromMap.hh:186` |
| `extruder_inverted` | `True` (negative A steps = extrude) | `True` | `Sailfish/mighty_two/Configuration.hh:374`, `Replicator2.json:22` |
| `x_max_feedrate_mm_min` | `18000.0` (300 mm/s) | `18000.0` | `Sailfish/EepromMap.hh:166`, `GPX/std_machines.h:188` |
| `y_max_feedrate_mm_min` | `18000.0` (300 mm/s) | `18000.0` | Same |
| `z_max_feedrate_mm_min` | `1170.0` (19.5 mm/s) | `1170.0` | `Sailfish/EepromMap.hh:166`, `GPX/std_machines.h:190` |
| `a_max_feedrate_mm_min` | `1600.0` (26.67 mm/s) | `1600.0` | `Sailfish/EepromMap.hh:166`, `GPX/std_machines.h:191` |
| `home_feedrate_xy_mm_min` | `2500.0` | `2500.0` | `GPX/std_machines.h:188–189` |
| `home_feedrate_z_mm_min` | `1100.0` | `1100.0` | `GPX/std_machines.h:190` |
| `x_bounds_mm` | `(-142.5, +142.5)` (285 mm) | `(-142.5, +142.5)` | `Sailfish/platforms.py:222`, `GPX/std_machines.h:188` |
| `y_bounds_mm` | `(-76.0, +76.0)` (152 mm) | `(-76.0, +76.0)` | `Sailfish/platforms.py:222`, `GPX/std_machines.h:189` |
| `z_bounds_mm` | `(0.0, 155.0)` (155 mm) | `(0.0, 155.0)` | `Sailfish/platforms.py:222`, `GPX/std_machines.h:190` |
| `x_home_endstop` / `y_home_endstop` / `z_home_endstop` | `MAX` / `MAX` / `MIN` | `MAX` / `MAX` / `MIN` | All 4 primary sources |
| `home_offset_mm` | `(152.0, 75.0, 0.0)` | `(152.0, 75.0, 0.0)` | `Sailfish/platforms.py:219–220` (`13463/88.573186`, `6643/88.573186`) |
| `extruder_count` | `1` (Tool `0` / Axis `A`) | `1` | `Sailfish/platforms.py:213`, `Replicator2.json:25–32` |
| `has_heated_bed` | `False` | `False` | `Sailfish/platforms.py:221`, `Replicator2.json:33` |
| `max_nozzle_temp_c` | `280` | `280` | `GPX/gpx.c:5273` (`NOZZLE_MAX`) |
| `homing_timeout_s` | `20` | `20` | `Replicator2.json:104–105`, `GPX/std_machines.h:199` |
| `heater_wait_ping_ms` / `heater_wait_timeout_s` | `100` / `600` | `100` / `600` | `s3gProtocol.md:871–872`, `GPX/gpx.c` |

---

## 3. Numeric Design & Zero-Drift Proof

### 3.1 Deterministic Rounding Function
All mm → step conversions use deterministic **half-away-from-zero** rounding (matching C99 `round()`):
```python
def round_half_away(value: float) -> int:
    if value >= 0.0:
        return math.floor(value + 0.5)
    return -math.floor(-value + 0.5)
```
Unlike Python's built-in `round()` (banker's round-half-to-even), `round_half_away` is strictly symmetric (`round_half_away(-x) == -round_half_away(x)`), matches C99 `round()` used by GPX, and satisfies $|r - x| \le 0.5$ for every finite $x$.

### 3.2 XYZ Position Tracking (Zero Cumulative Drift)
The state machine tracks, for each axis $i \in \{X, Y, Z\}$:
- `pos_mm[i]: float` — exact commanded coordinate in mm (IEEE-754 float64).
- `pos_steps[i]: int` — exact integer step coordinate currently held by the printer.
- `axis_known[i]: bool` — whether the printer's absolute step coordinate on axis $i$ is known (`False` initially and after `G28`/`G161`/`G162`; becomes `True` after `M132`, `G92`, or an absolute `G0`/`G1` move specifying axis $i$).

On a `G0`/`G1` move:
1. For each axis $i \in \{X, Y, Z\}$:
   - If axis $i$ is present with value $v$:
     - In `G90` (absolute): `target_mm[i] = v`.
     - In `G91` (relative): `target_mm[i] = pos_mm[i] + v`.
   - Else: `target_mm[i] = pos_mm[i]`.
2. **Bounds check:** For every axis $i$ whose position is specified or known, verify `min_mm - 1e-3 <= target_mm[i] <= max_mm + 1e-3` (raising `OutOfBoundsError` with exact line number otherwise).
3. **Quantize absolute target to integer steps:**
   $$s_{\text{target},i} = \text{round\_half\_away}(\text{target\_mm}_i \cdot \text{spm}_i)$$
   Verify $-2^{31} \le s_{\text{target},i} \le 2^{31}-1$ (raising `ValueRangeError` on `int32` overflow).
4. **Step delta:** $\Delta s_i = s_{\text{target},i} - s_{\text{cur},i}$.
5. **Unknown-axis handling (e.g., move after `G28` before `M132`/`G92`):**
   - If axis $i$ is NOT known AND NOT specified on this line: set bit $i$ in Command `155`'s `relative_axes` bitmask and emit `0` for axis $i$ (so the printer does not jump axis $i$ to step `0`!).
   - If axis $i$ is NOT known AND IS specified in `G91` relative mode: set bit $i$ in `relative_axes` and emit `round_half_away(v * spm_i)` as a relative step delta.
   - Once axis $i$ is specified in `G90` absolute mode (or set via `G92`/`M132`), `axis_known[i]` becomes `True` and bit $i$ is `0` (absolute).
6. **Zero-drift proof:** Because $s_{\text{target},i}$ is computed directly from absolute `target_mm[i] * spm_i` rather than summing rounded per-segment deltas, at **every** point $k$ (including $k = 1{,}000{,}000$):
   $$\left|\frac{s_{\text{target},i}^{(k)}}{\text{spm}_i} - \text{target\_mm}_i^{(k)}\right| \le \frac{0.5}{\text{spm}_i}$$
   ($\le 5.625\,\mu\text{m}$ on X/Y at `88.888889` steps/mm, $\le 1.25\,\mu\text{m}$ on Z at `400` steps/mm). In `G91` relative mode, IEEE-754 float64 accumulation over $10^6$ segments introduces $< 10^{-8}\text{ mm}$ floating-point error, 5 orders of magnitude below half a step.

### 3.3 Extruder (E → A Axis) Tracking (`M82`, `M83`, `G92 E0`, Zero Cumulative Drift)
The state machine tracks **three** extruder quantities:
- `e_logical_mm: float` — G-code E coordinate (reset by `G92 E<val>` to `<val>`).
- `e_physical_mm: float` — true cumulative physical filament position in mm (monotonically updated by `+= delta_e_mm`; **never** reset by `G92 E0`!).
- `e_emitted_steps: int` — cumulative integer extruder steps already emitted to the X3G stream.

On a `G0`/`G1` move with E word $v$:
1. Compute exact move extrusion:
   - If relative extrusion (`M83` or `G91`): $\Delta e_{\text{mm}} = v$, `e_logical_mm += v`.
   - Else (`M82` and `G90`): $\Delta e_{\text{mm}} = v - e_{\text{logical\_mm}}$, `e_logical_mm = v`.
2. Update cumulative physical filament: `e_physical_mm += delta_e_mm`.
3. Compute cumulative integer step target:
   $$s_{\text{target},E} = \text{round\_half\_away}(e_{\text{physical\_mm}} \cdot \text{spm}_A)$$
4. Compute relative step delta to emit:
   $$\Delta s_E = s_{\text{target},E} - e_{\text{emitted\_steps}}$$
5. Update `e_emitted_steps = s_target_E`.
6. Encode in Command `155`: **`A = -delta_s_E`** (negative = forward extrusion per `ACCELERATION_EXTRUDE_WHEN_NEGATIVE_A`) with **`relative` bits 3 and 4 (`0x18`) ALWAYS set** (and `B = 0`).

On `G92 E<val>` (without X/Y/Z):
- Set `e_logical_mm = <val>`.
- **Do NOT reset `e_physical_mm` or `e_emitted_steps`**, and **do NOT emit Command `140` (`HOST_CMD_SET_POSITION_EXT`)** (avoiding Command 140's all-5-axis overwrite hazard!).

**Cumulative vs Per-Segment Bound (Triangle-Inequality Note):**
- At every point $k$ (including after 1,000,000 segments and arbitrary `G92 E0` resets), cumulative extrusion error is strictly bounded:
  $$\left|\frac{e_{\text{emitted\_steps}}^{(k)}}{\text{spm}_A} - e_{\text{physical\_mm}}^{(k)}\right| \le \frac{0.5}{\text{spm}_A} \approx 0.00519\text{ mm}$$
- By the triangle inequality, any single segment's step delta satisfies $|\Delta s_E^{(k)} - \Delta e_{\text{mm}}^{(k)} \cdot \text{spm}_A| \le 1.0\text{ step}$ ($\le 0.5$ step error at segment start + $\le 0.5$ step error at segment end). As proven in Phase 1 analysis, a bound tighter than $1.0$ step per segment is mathematically incompatible with zero cumulative drift for sub-step sequences (e.g., repeated $+0.4$ step extrusions). Both cumulative ($\le 0.5$ step) and per-segment ($\le 1.0$ step) invariants will be asserted explicitly in Phase 3.

### 3.4 Zero-Step Moves
If a `G0`/`G1` move changes coordinates by a non-zero sub-step amount such that $\Delta s_X = \Delta s_Y = \Delta s_Z = \Delta s_E = 0$:
- `pos_mm` and `e_physical_mm` ARE updated (preserving sub-step residuals so consecutive micro-segments accumulate cleanly and step as soon as they cross 0.5 step!).
- No Command `155` is emitted (because MightyBoard firmware drops `planner_master_steps == 0` anyway, `Steppers.cc:929`), and `summary.zero_step_moves_skipped` is incremented.
- If a `G0`/`G1` has only `F<rate>` (no X/Y/Z/E words), modal `feedrate_mm_min` is updated with no motion emitted.

### 3.5 Feedrate Clamping, DDA Rate & `feedrate_mult64` Quantization
For every move with $N_{\max} = \max(|\Delta s_X|, |\Delta s_Y|, |\Delta s_Z|, |\Delta s_E|) > 0$:
1. **Distance ($d$, mm):**
   - $d_{\text{xyz}} = \sqrt{\Delta x_{\text{mm}}^2 + \Delta y_{\text{mm}}^2 + \Delta z_{\text{mm}}^2}$.
   - If $d_{\text{xyz}} > 0$: $d = d_{\text{xyz}}$.
   - Else (extruder-only move): $d = |\Delta e_{\text{mm}}|$.
   - Safeguard against `float32` underflow: if `struct.unpack('<f', struct.pack('<f', d))[0] == 0.0`, fall back to step-implied distance $\sqrt{(\Delta s_X/\text{spm}_X)^2 + (\Delta s_Y/\text{spm}_Y)^2 + (\Delta s_Z/\text{spm}_Z)^2}$ (or $|\Delta s_E|/\text{spm}_A$) so firmware never sees `distance == 0.0`.
2. **Per-Axis Feedrate Clamping ($F_{\text{safe}}$, mm/min):**
   - Start with $F = \text{modal\_feedrate\_mm\_min}$ (if unset, raise `ValueRangeError` or clamp to safe default? — raise `ValueRangeError` if a motion move occurs before any `F` feedrate has been set, or require `F` on first move; check G-code spec: OrcaSlicer always emits `F` on first move; if a hand-written file omits `F` on line 1, fail loudly with clear error rather than guessing!).
   - Require $F > 0$ (reject $F \le 0$ with `ValueRangeError`).
   - If $d_{\text{xyz}} > 0$:
     - For $i \in \{X, Y, Z\}$ with $|\Delta p_i| > 0$: if $F \cdot |\Delta p_i| / d_{\text{xyz}} > F_{\max,i}$, set $F = F_{\max,i} \cdot d_{\text{xyz}} / |\Delta p_i|$.
     - For $E$ with $|\Delta e_{\text{mm}}| > 0$: if $F \cdot |\Delta e_{\text{mm}}| / d_{\text{xyz}} > F_{\max,A}$, set $F = F_{\max,A} \cdot d_{\text{xyz}} / |\Delta e_{\text{mm}}|$.
   - Else (extruder-only move):
     - If $F > F_{\max,A}$, set $F = F_{\max,A}$.
   - Record a warning/counter if feedrate clamping occurred (`summary.feedrate_clamps`).
3. **Timing Field Quantization:**
   - $f = F_{\text{safe}} / 60.0$ (mm/s).
   - Ideal duration $t = d / f$ (seconds).
   - **Master-axis DDA rate (steps/sec):**
     $$\text{dda\_rate} = \max\left(1,\; \text{round\_half\_away}\left(\frac{N_{\max} \cdot f}{d}\right)\right)$$
     Note: on Replicator 2, max possible `dda_rate` is $300 \times 88.888889 = 26{,}667\text{ steps/s} \le 32{,}767$ (`0x7FFF`), well inside both Sailfish and `uint32` limits.
   - **Fixed-point feedrate (`mm/s * 64`, signed `int16` in firmware):**
     $$\text{feedrate\_mult64} = \max\left(1,\; \min\left(32767,\; \text{round\_half\_away}(f \cdot 64.0)\right)\right)$$
   - **Quantization error bound:**
     - Effective speed error from `feedrate_mult64`: $|\hat{f} - f| \le \frac{0.5}{64} = 0.0078125\text{ mm/s}$ (at $f = 50\text{ mm/s}$, relative error $\le 0.016\%$; even at $f = 1\text{ mm/s}$, relative error $\le 0.78\%$).
     - Effective step-rate error from `dda_rate`: $|\widehat{\text{dda}} - \text{dda}| \le 0.5\text{ steps/s}$ (relative error $\le \frac{0.5}{\text{dda\_rate}}$, e.g., at $10\text{ mm/s}$ XY = $889\text{ steps/s}$, relative error $\le 0.056\%$).

---

## 4. Complete G-code → X3G Mapping Rules

| G-code | Action / Emitted X3G Command(s) | Notes |
|---|---|---|
| *(stream start)* | `153` `BUILD_START_NOTIFICATION` (0, ASCII name $\le 24$ B + NUL) | Auto-emitted before first command if no `M136` seen yet |
| `G0`, `G1` | `155` `QUEUE_POINT_NEW_EXT` (32 B) | Absolute XYZ, relative negative A (`0x18`), `dda_rate`, `float32 distance`, `int16 feedrate_mult64` |
| `G4 P<ms>` / `G4 S<s>` | `133` `DELAY` (`uint32 ms`) | Reject negative or missing `P`/`S`; `S` converted via `round_half_away(S * 1000)` |
| `G21` | State update (mm) | No X3G command emitted |
| `G28 [X] [Y] [Z]` | `132` `FIND_AXES_MAXIMUM` (XY) then `131` `FIND_AXES_MINIMUM` (Z) | Bare `G28` homes XY then Z; marks homed axes unknown; uses `F` if given else profile `home_feedrate` |
| `G161 [X] [Y] [Z] [F]` | `131` `FIND_AXES_MINIMUM` | `step_delay_us = round_half_away(60e6 / (F * max_spm_of_axes))`; marks homed axes unknown |
| `G162 [X] [Y] [Z] [F]` | `132` `FIND_AXES_MAXIMUM` | Same `step_delay_us` formula; marks homed axes unknown |
| `G90` / `G91` | State: `relative_xyz = False / True` | Note: `G91` also makes E relative until `G90` (standard Marlin/RepRap semantics; `M82`/`M83` overrides E explicitly) |
| `M82` / `M83` | State: `relative_e = False / True` | Independent of `G90`/`G91` |
| `G92 [X] [Y] [Z] [E]` | E-only: update `e_logical_mm` only (no X3G). If X/Y/Z present: update `pos_mm`/`pos_steps`/`axis_known` and emit `140` `SET_POSITION_EXT` | Bare `G92` resets X=Y=Z=E=0; warns if partial XYZ `G92` occurs while another XYZ axis is unknown |
| `G130 [X] [Y] [Z] [A] [B]` | `145` `SET_POT_VALUE` per axis (`0..127`) | Clamped/validated to `0..127` |
| `M0`, `M1`, `M25` | `148` `PAUSE_FOR_BUTTON` (center button `0x01`, timeout `0`, options `0x04`) | Optional `P<ms>` / `S<s>` on `M0` emits `133` `DELAY` if non-zero |
| `M600` | `149` `DISPLAY_MESSAGE` (`"Change Filament"`) + `148` `PAUSE_FOR_BUTTON` | Approved per ADR-006 |
| `M17` / `M18` / `M84` | `137` `ENABLE_AXES` (bit 7 = `1` for `M17`, `0` for `M18`/`M84`; bits 0–4 = specified axes or `0x1F` all) | |
| `M70 [P<s>] (msg)` | `149` `DISPLAY_MESSAGE` | Comment text inside `(...)` or after `;` |
| `M72 P<id>` | `151` `QUEUE_SONG` (`uint8 song_id`) | |
| `M73 P<pct>` | `150` `SET_BUILD_PERCENT` (`uint8 pct` clamped `0..100`, reserved `0`) | Ignores `R<min>` parameter |
| `M104 S<T> [T0]` | `136` `TOOL_COMMAND` (tool `0`, action `3` `SET_TEMP`, `int16 T`) | Validates `0 <= T <= 280` and tool `0` |
| `M109 S<T> [T0]` | `136` (`SET_TEMP`, `int16 T`) + if `T > 0`: `135` `WAIT_FOR_TOOL` (`0`, `100`, `600`) | Marlin/RepRap flavor (`M109 R<T>` also supported as target temp + wait) |
| `M106 [S<0..255>] [P0]` | `136` (`TOGGLE_VALVE` action `13`, `state = 1 if S > 0 else 0`) | Warns if `0 < S < 255` (binary fan); rejects `P > 0` |
| `M107 [P0]` | `136` (`TOGGLE_VALVE` action `13`, `state = 0`) | Rejects `P > 0` |
| `M126 [S<n>]` / `M127` | `136` (`TOGGLE_VALVE` action `13`, `state = 1/0`) | Native MakerBot blower fan codes |
| `M131` / `M132` | `143` `STORE_HOME_POSITION` / `144` `RECALL_HOME_POSITION` | `M132` sets `pos_mm`/`pos_steps` to profile `home_offset_mm` and marks recalled XYZ axes known |
| `M133 [T0] [P<s>]` | `135` `WAIT_FOR_TOOL` (`0`, `100`, `timeout`) | Native MakerBot wait-for-extruder |
| `M135 T0` / `T0` | `134` `CHANGE_TOOL` (`0`) | Rejects any tool `!= 0` |
| `M136` / `M137` | `153` `BUILD_START_NOTIFICATION` / `154` `BUILD_END_NOTIFICATION` | Suppresses duplicate auto-header/footer |
| `M320` / `M321` | `156` `SET_ACCELERATION_TOGGLE` (`1` / `0`) | Supported on both Sailfish and Stock |
| `M322 Z<mm>` | Sailfish mode: `158` `PAUSE_AT_ZPOS` (`float32 z`). Stock mode: **REJECT** | ADR-005 |
| `M140 S0`, `M190 S0`, `M141 S0`, `M191 S0` | Safely ignore (recorded in summary + warning) | `S > 0` **REJECTED** loudly (no heated bed/chamber) |
| `M201`–`M205`, `M900`, `M400`, `M486`, `M117`, `EXCLUDE_OBJECT_*`, `;` comments | Safely ignore (counted per opcode in `summary.ignored_commands`) | Warn on `M900` (linear advance not in X3G) |
| `G2`, `G3`, `G10`, `G11`, `G20`, `T1+`, `M106 P>0`, `M140/M190/M141/M191 S>0`, unknown G/M | **REJECT loudly** (`UnsupportedCommandError` with line #, raw line, remediation hint) | Zero silent drops |
| *(stream end)* | `154` `BUILD_END_NOTIFICATION` (`0`) | Auto-emitted at EOF if no `M137` was seen |

---

## 5. Error Model & Determinism

### 5.1 Error Hierarchy (`errors.py`)
Every failure raises a subclass of `OrcaX3GError` containing structured fields (`line_number: int | None`, `raw_line: str | None`, `hint: str | None`) and formats as:
```
Line 142: Unsupported command 'G2 X10 Y10 I5 J0': X3G does not support arc moves.
Hint: Set enable_arc_fitting = "disabled" and z_hop_types = "Normal Lift" in your OrcaSlicer profile.
```
- No bare `except Exception: pass` anywhere in the pipeline.
- Every rejected construct halts conversion immediately (preventing partial/corrupt `.x3g` output from ever reaching an SD card).

### 5.2 Determinism Guarantee
- Output `.x3g` bytes depend **strictly** on `(input_gcode_text, machine_profile, conversion_options, build_name)`.
- No wall-clock timestamps, no random numbers, no unordered `set` iteration in emitted bytes, no locale-sensitive formatting (`str`/`struct.pack` only).
- `convert_file()` writes atomically via temp file + replace (or in-place truncation inside Orca's `.pp` temp copy) and produces **byte-identical** outputs on repeated invocations.

---

## 6. OrcaSlicer `v2.5.2` Plugin Glue Design (`plugin.py`)

### 6.1 Multi-Capability Registration
Packaged as a pure-Python wheel (`orca_x3g-0.1.0-py3-none-any.whl`) with zero external dependencies (`dependencies = []`):

1. **`X3GPostProcessCapability(orca.slicing.SlicingPipelineCapabilityBase)`**
   - `get_name()` → `"X3G Exporter (Replicator 2)"` (no `;` character!).
   - `get_default_config()` → JSON options:
     ```json
     {
       "machine_profile": "r2",
       "firmware": "sailfish",
       "allow_non_x3g_extension": false,
       "strict_bounds": true,
       "warn_sd_filename_length": true
     }
     ```
   - `execute(self, ctx)`:
     - Immediately returns `orca.ExecutionResult.success()` for any `ctx.step != orca.slicing.Step.psGCodePostProcess`.
     - **Never calls `orca.host.ui.*`** (runs on slicing worker thread).
     - Checks `ctx.output_name`: unless `allow_non_x3g_extension` is `True`, fails loudly with `PluginResult.RecoverableError` if `not ctx.output_name.lower().endswith(".x3g")`, instructing the user to set `filename_format = "{input_filename_base}.x3g"` or save with `.x3g`.
     - Validates active slicer settings via `ctx.config_value(...)` (`gcode_flavor`, `enable_arc_fitting`, `use_firmware_retraction`, `hot_plate_temp`, `auxiliary_fan`) and records profile warnings.
     - Checks SD filename length (`len(Path(ctx.output_name).name) > 31` → warning; non-8.3 in stock mode → warning).
     - Reads G-code from `ctx.gcode_path`, converts to X3G bytes using `converter.convert_lines(...)`, checks `ctx.cancelled()` periodically, and overwrites `ctx.gcode_path` in-place with the binary `.x3g` bytes (inside the pre-approved scoped allowed root → **zero audit prompts**!).
     - Writes `last_export_summary.json` into `orca.host.plugin.storage()` (inside global allowed root `data_dir()`, no forbidden keywords → **zero audit prompts**!).
     - Returns `orca.ExecutionResult.success(summary_one_liner)` (logged by `PostProcessor.cpp:315`).
     - On any `OrcaX3GError`, returns `orca.ExecutionResult.failure(orca.PluginResult.RecoverableError, str(err))`, which OrcaSlicer surfaces as an `ExportError` dialog without invalidating the sliced plate (`PostProcessor.cpp:307–312, 517–535`).

2. **`X3GExportPageCapability(orca.pages.PagesPluginCapabilityBase)`**
   - `get_name()` → `"X3G Export"`
   - Adds a themed HTML tab (using `--orca-bg`, `--orca-fg`, `--orca-accent`, `--orca-border` CSS variables) displaying:
     - Latest export summary (output file, `.x3g` size in bytes, move count, layer count, estimated time, filament mm, warnings, ignored command breakdown, profile check results).
     - Refresh button & manual `.gcode → .x3g` conversion trigger (running via `orca.host` / background thread if large).

3. **`X3GSummaryScriptCapability(orca.script.ScriptPluginCapabilityBase)`**
   - `get_name()` → `"X3G Last Export Summary"`
   - Runs on the UI thread from **File → Plugins → Run**; displays the latest export summary from `orca.host.plugin.storage()/last_export_summary.json` in a native `orca.host.ui.show_dialog(...)` modal report.

4. **`@orca.plugin class OrcaX3GPlugin(orca.base)`**
   - Calls `orca.register_capability(...)` for all three capabilities.
   - Needs **zero** `orca.request_permissions(...)` declarations for normal Export G-code usage because all reads/writes occur strictly inside `ctx.gcode_path.parent_path()` and `orca.host.plugin.storage()`!

### 6.2 User-Facing Summary Schema (`ExportSummary`)
Every conversion (CLI or Orca plugin) produces a structured `ExportSummary`:
- `source_path`, `output_path`, `build_name`, `machine_profile`, `firmware`
- `gcode_lines`, `x3g_bytes`, `x3g_commands`, `motion_commands`, `zero_step_moves_skipped`, `layers_detected`
- `total_filament_mm`, `estimated_print_time_s`, `feedrate_clamps`
- `warnings: list[str]` (e.g., PWM fan quantized to ON, SD filename > 31 chars, profile flavor warnings)
- `ignored_commands: dict[str, int]` (e.g., `{"M204": 12, "M486": 4}`)
