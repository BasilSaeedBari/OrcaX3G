# Physical Toolpath Equivalence Specification

**Date:** 2026-11-25  
**Target Machine:** MakerBot Replicator 2 (`r2`: 88.888889 XY, 400.0 Z, 96.27520187033366 A steps/mm; `r2_factory`: 88.573186 XY)  
**Primary Sources:** [`docs/SOURCES.md`](SOURCES.md), [`docs/DESIGN.md`](DESIGN.md)

---

## 1. Purpose

Working encoder code without an independent mathematical proof of equivalence is insufficient: an encoder and a naive inverse test can share the exact same bug (wrong axis sign, wrong endianness, cumulative rounding drift, dropped moves, or swapped fan/heater opcodes).

Two print jobs — an input **G-code stream** $G$ and an output **`.x3g` binary stream** $X$ — are defined to be **physically equivalent** if and only if, when $G$ is executed on an independent **idealized G-code machine simulator** (`gcode_sim.py`) and $X$ is parsed and executed on an independent **spec-driven X3G decoder / virtual machine** (`decoder.py`, sharing **zero** code with `encoder.py` or `planner.py`), the two resulting event streams agree within every formal tolerance defined in Section 3.

---

## 2. Independent Verification Architecture

```
                     ┌──────────────────────────┐
                     │    Input G-code (.gcode) │
                     └──────┬────────────┬──────┘
                            │            │
         (Production path)  │            │  (Independent reference path)
                            ▼            ▼
               ┌────────────────┐    ┌─────────────────────┐
               │ orca_x3g       │    │ gcode_sim.py        │
               │ parser+planner │    │ Independent G-code  │
               │ +encoder       │    │ ideal machine model │
               └───────┬────────┘    └──────────┬──────────┘
                       │ .x3g bytes             │ ReferenceEvent[]
                       ▼                        │
               ┌────────────────┐               │
               │ decoder.py     │               │
               │ Spec-driven    │               │
               │ X3G decoder+VM │               │
               └───────┬────────┘               │
                       │ DecodedEvent[]         │
                       ▼                        ▼
               ┌───────────────────────────────────────────┐
               │ equivalence.py (Section 3 tolerances)     │
               │  → Pass/Fail + Metrics + JSON/MD + Plots  │
               └───────────────────────────────────────────┘
```

**No Shared Encoding Code Invariant:**
- `decoder.py` MUST NOT import `encoder.py`, `planner.py`, or `state.py`. It defines its own opcode byte tables, `struct` unpackers, CRC-8-Maxim routine (using bit-by-bit polynomial division from the spec rather than the encoder's table), and virtual machine step accumulator directly from [`docs/SOURCES.md`](SOURCES.md).
- `gcode_sim.py` MUST NOT import `planner.py` or `state.py`. It parses G-code lines independently in floating-point millimeters and records the idealized continuous toolpath and non-motion commands.

---

## 3. Formal Equivalence Criteria & Justified Tolerances

Let the Replicator 2 steps-per-mm vector be $(\text{spm}_X, \text{spm}_Y, \text{spm}_Z, \text{spm}_A)$, where for the default `r2` profile:
- $\text{spm}_X = \text{spm}_Y = 88.888889\text{ steps/mm}$ ($\frac{1}{2\,\text{spm}_{X,Y}} = 0.005625\text{ mm} = 5.625\,\mu\text{m}$)
- $\text{spm}_Z = 400.0\text{ steps/mm}$ ($\frac{1}{2\,\text{spm}_Z} = 0.001250\text{ mm} = 1.250\,\mu\text{m}$)
- $\text{spm}_A = 96.27520187033366\text{ steps/mm}$ ($\frac{1}{2\,\text{spm}_A} \approx 0.005193\text{ mm} = 5.193\,\mu\text{m}$)

*(For `r2_factory`, $\text{spm}_X = \text{spm}_Y = 88.573186\text{ steps/mm}$, giving half-step $\approx 5.645\,\mu\text{m}$.)*

A small floating-point guard $\varepsilon_{\text{fp}} = 10^{-9}\text{ mm}$ is added only to absorb IEEE-754 binary rounding on exact half-step boundaries (e.g., `0.5 / 88.888889`).

### Criterion E1 — XYZ Position Accuracy & Zero Cumulative Drift
- **Per-point bound:** At every motion event $k \in \{1, \dots, M\}$, for every known axis $i \in \{X, Y, Z\}$:
  $$\left|p_{i,\text{x3g}}^{(k)} - p_{i,\text{gcode}}^{(k)}\right| \le \frac{0.5}{\text{spm}_i} + \varepsilon_{\text{fp}}$$
  where $p_{i,\text{x3g}}^{(k)} = s_{i,\text{x3g}}^{(k)} / \text{spm}_i$ is the virtual machine's physical position in mm decoded from the X3G step stream.
- **Justification:** X3G represents axis positions as 32-bit signed integers in motor steps (`int32`). The minimum achievable worst-case quantization error for any real-valued target coordinate is $\pm 0.5$ step. Because the planner quantizes absolute targets rather than summing rounded deltas, this bound holds identically at $k = 1$ and at $k = 1{,}000{,}000$ — **zero cumulative drift**.
- **Long-print drift assertion:** Explicitly verified over a $1{,}000{,}000$-line synthetic print with irrational/fractional coordinates (both `G90` and `G91` modes): final deviation at move $1{,}000{,}000$ must remain $\le 0.5 / \text{spm}_i + 10^{-6}\text{ mm}$ (never growing with print length).

### Criterion E2 — Extrusion Accuracy & Zero Cumulative Drift
- **Cumulative filament bound:** At every motion event $k \in \{1, \dots, M\}$ (including across arbitrary `G92 E0` resets and `M82`/`M83` mode switches), total physical filament extruded (decoded from `-a_steps_cumulative / spm_A` because MightyBoard extrudes on negative `A` steps) must satisfy:
  $$\left|E_{\text{x3g}}^{(k)} - E_{\text{gcode}}^{(k)}\right| \le \frac{0.5}{\text{spm}_A} + \varepsilon_{\text{fp}} \approx 0.005193\text{ mm}$$
- **Per-segment delta bound (Triangle-Inequality Theorem):** Let $E_{\text{gcode}}^{(k)}$ be cumulative G-code filament after segment $k$, and $E_{\text{x3g}}^{(k)}$ be cumulative decoded X3G filament after segment $k$. If cumulative error satisfies $|E_{\text{x3g}}^{(k)} - E_{\text{gcode}}^{(k)}| \le \frac{0.5}{\text{spm}_A}$ for all $k$, then by the triangle inequality the single-segment extrusion delta $\Delta E^{(k)} = E^{(k)} - E^{(k-1)}$ satisfies:
  $$\left|\Delta E_{\text{x3g}}^{(k)} - \Delta E_{\text{gcode}}^{(k)}\right| = \left|\left(E_{\text{x3g}}^{(k)} - E_{\text{gcode}}^{(k)}\right) - \left(E_{\text{x3g}}^{(k-1)} - E_{\text{gcode}}^{(k-1)}\right)\right| \le \frac{1.0}{\text{spm}_A} + 2\varepsilon_{\text{fp}} \approx 0.010387\text{ mm}$$
  *Mathematical necessity proof:* Consider 10 consecutive segments each commanding $+0.4$ steps of extrusion ($E_{\text{gcode}} = 0.4, 0.8, 1.2, 1.6, \dots$). Any zero-drift quantizer MUST emit step deltas $(0, 1, 0, 1, 0, \dots)$. On segment 2 ($\Delta E_{\text{gcode}} = 0.4$ steps, $\Delta E_{\text{x3g}} = 1.0$ step), the single-segment error is $0.6 > 0.5$ steps; conversely, rounding each segment independently to $\le 0.5$ step would emit $(0, 0, 0, \dots)$ and accumulate unbounded $-4.0$ steps of under-extrusion! Therefore, the tightest mathematically consistent pair of bounds is:
  - **Cumulative extrusion error:** $\le 0.5\text{ step}$ ($\le 0.005193\text{ mm}$) at every segment and at job end (zero drift).
  - **Per-segment extrusion error:** $\le 1.0\text{ step}$ ($\le 0.010387\text{ mm}$) — and $\le 0.5\text{ step}$ whenever the segment starts on an integer step boundary.
- **Extruder direction sign:** Positive G-code $\Delta E > 0$ MUST decode to negative X3G `A` step delta ($\Delta s_A < 0$), and retraction $\Delta E < 0$ MUST decode to positive X3G `A` step delta ($\Delta s_A > 0$).

### Criterion E3 — Motion Count & Sub-Step Fidelity
- Every G-code `G0`/`G1` move whose quantized step delta $(\Delta s_X, \Delta s_Y, \Delta s_Z, \Delta s_A) \neq (0, 0, 0, 0)$ MUST correspond 1-to-1, in exact sequence, to one X3G Command `155` (`HOST_CMD_QUEUE_POINT_NEW_EXT`) packet.
- No non-zero-step move may be dropped, merged, or reordered.
- A G-code move whose commanded displacement produces zero integer step change on all axes ($\Delta s_X = \Delta s_Y = \Delta s_Z = \Delta s_A = 0$) MUST NOT emit a degenerate zero-step Command `155` (which MightyBoard firmware discards at `Steppers.cc:929`), **and** its fractional position/extrusion residual MUST be preserved in state so that consecutive sub-step moves still cross step boundaries at the exact cumulative position (verified by Criterion E1 & E2).
- `G0`/`G1` lines containing only `F<rate>` (feedrate-only modal change) emit zero motion commands and update modal speed.

### Criterion E4 — Feedrate & Timing Field Accuracy
In X3G Command `155`, move timing is encoded by three fields:
1. `feedrate_mult64` (`int16`): fixed-point speed in $\frac{1}{64}\text{ mm/s}$, clamped to $[1, 32767]$.
2. `distance_mm` (`float32`): Euclidean XYZ distance (or $|\Delta e|$ for extruder-only moves).
3. `dda_rate` (`uint32`): master-axis step frequency in integer steps/sec, $\max(1, \text{round}(N_{\max} \cdot f_{\text{clamped}} / d))$.

Let $f_{\text{ref}} = F_{\text{clamped}} / 60.0\text{ mm/s}$ be the G-code commanded feedrate after applying the verified Replicator 2 per-axis speed ceiling ($F_{\max,XY}=18000$, $F_{\max,Z}=1170$, $F_{\max,A}=1600\text{ mm/min}$).

- **Feedrate field bound (`feedrate_mult64`):**
  $$\left|\frac{\text{feedrate\_mult64}}{64.0} - f_{\text{ref}}\right| \le \frac{0.5}{64.0} + 10^{-6} = 0.0078135\text{ mm/s}$$
  (For $f_{\text{ref}} \ge 1/64\text{ mm/s}$; below $1/64$, `feedrate_mult64` floors at `1`.)
  At typical printing speeds ($f_{\text{ref}} = 50\text{ mm/s}$), this is $\le 0.0156\%$ relative error; even at slow $5\text{ mm/s}$ Z-hops, it is $\le 0.156\%$.
- **DDA rate bound (`dda_rate`):**
  Let $\text{dda}_{\text{ideal}} = N_{\max} \cdot f_{\text{ref}} / d_{\text{ref}}$. When $\text{dda}_{\text{ideal}} \ge 1.0$:
  $$\left|\text{dda\_rate} - \text{dda}_{\text{ideal}}\right| \le 0.5 + 10^{-4}\text{ steps/s}$$
  Consequently, the implied constant-speed move velocity $f_{\text{dda}} = d_{\text{ref}} \cdot \text{dda\_rate} / N_{\max}$ satisfies:
  $$\frac{|f_{\text{dda}} - f_{\text{ref}}|}{f_{\text{ref}}} \le \frac{0.5}{\text{dda}_{\text{ideal}}} + 10^{-4}$$
  At $10\text{ mm/s}$ XY ($\text{dda}_{\text{ideal}} = 889\text{ steps/s}$), relative speed error is $\le 0.056\%$; at $60\text{ mm/s}$ XY ($\text{dda}_{\text{ideal}} = 5333\text{ steps/s}$), it is $\le 0.0094\%$.
- **Distance field bound (`distance_mm`):**
  $$\left|\text{distance\_mm} - d_{\text{ref}}\right| \le \max\left(10^{-5}\text{ mm},\; 10^{-6} \cdot d_{\text{ref}}\right)$$
  (IEEE-754 single-precision `float32` representation accuracy, except when $d_{\text{ref}}$ underflows `float32` and the step-implied fallback is used).

### Criterion E5 — Non-Motion Command Ordering & Exact Value Fidelity
Non-motion physical events MUST appear in the **exact same order** relative to all motion events and relative to each other, with matching values:

| Event Kind | G-code Source | X3G Decoded Event | Equality Condition |
|---|---|---|---|
| `set_temp` | `M104 S<T>`, `M109 S<T>/R<T>` | `136` Tool `0` Action `3` | `tool_id == 0`, `temp_c == round_half_away(T)` (exact integer °C) |
| `wait_temp` | `M109 S<T> (T>0)`, `M133` | `135` `WAIT_FOR_TOOL` | `tool_id == 0` |
| `fan` | `M106`, `M107`, `M126`, `M127` | `136` Tool `0` Action `13` | `tool_id == 0`, boolean `enabled == (S > 0)` (exact boolean match) |
| `dwell` | `G4 P<ms>/S<s>`, `M0 P/S` | `133` `DELAY` | `delay_ms` exact integer ms match |
| `home` | `G28`, `G161`, `G162` | `131` (MIN) / `132` (MAX) | Exact `axes_mask` and `direction` (`MIN` vs `MAX`) |
| `recall_home` | `M132` | `144` `RECALL_HOME_POSITION` | Exact `axes_mask` |
| `store_home` | `M131` | `143` `STORE_HOME_POSITION` | Exact `axes_mask` |
| `set_position` | `G92` (with XYZ) | `140` `SET_POSITION_EXT` | Each specified XYZ axis within $\le 0.5 / \text{spm}_i$ |
| `change_tool` | `T0`, `M135 T0` | `134` `CHANGE_TOOL` | `tool_id == 0` |
| `enable_axes` | `M17`, `M18`, `M84` | `137` `ENABLE_AXES` | Exact `enable` boolean and `axes_mask` |
| `pause` | `M0`, `M1`, `M25`, `M600` | `148` `PAUSE_FOR_BUTTON` | Present at exact sequence point (`M600` preceded by `149` DisplayMessage) |
| `display_message` | `M70`, `M600` | `149` `DISPLAY_MESSAGE` | Exact ASCII message string |
| `set_pot` | `G130` | `145` `SET_POT_VALUE` | Exact `(axis, value)` |
| `build_percent` | `M73 P<pct>` | `150` `SET_BUILD_PERCENT` | Exact `percent` (`0..100`) |
| `queue_song` | `M72 P<id>` | `151` `QUEUE_SONG` | Exact `song_id` |
| `accel_toggle` | `M320`, `M321` | `156` `SET_ACCELERATION_TOGGLE` | Exact boolean `enable` |
| `pause_at_z` | `M322 Z<mm>` (Sailfish) | `158` `PAUSE_AT_ZPOS` | `|z_x3g - z_gcode| <= 1e-5 mm` (`float32`) |

**Header/Footer Wrapper:** `153` (`BUILD_START_NOTIFICATION`) at stream start and `154` (`BUILD_END_NOTIFICATION`) at stream end are validated as properly paired framing events (auto-inserted when `M136`/`M137` are absent).

### Criterion E6 — Total Estimated Print Time
Under the idealized constant-speed machine model (ignoring MightyBoard Sailfish JKN/acceleration and heater warm-up latency, which are executed identically on firmware from the same `155` packets):
- **G-code reference time:**
  $$T_{\text{gcode}} = \sum_{k=1}^{M} \frac{d_{\text{ref}}^{(k)}}{f_{\text{ref}}^{(k)}} + \sum \text{dwell\_s}$$
- **X3G decoded time:**
  $$T_{\text{x3g}} = \sum_{k=1}^{M} \frac{N_{\max}^{(k)}}{\text{dda\_rate}^{(k)}} + \sum \text{dwell\_s}$$
- **Tolerance:**
  $$\left|T_{\text{x3g}} - T_{\text{gcode}}\right| \le \max\left(0.5\text{ s},\; 0.005 \cdot T_{\text{gcode}}\right) \quad (\le 0.5\%)$$
- **Justification:** Because `dda_rate` is quantized to $\pm 0.5$ steps/s (typically $< 0.05\%$ per segment) and `N_max` is quantized to $\pm 0.5$ steps (unbiased zero-mean across segments), the summed constant-speed duration agrees to well under $0.5\%$ on real prints (typically $< 0.05\%$). The $0.5\text{ s}$ floor prevents false failures on 1-move synthetic unit tests whose total duration is $< 100\text{ ms}$.

### Criterion E7 — Binary Framing, Length & CRC Integrity
- For raw `.x3g` streams (`framed=False`): every command payload parses cleanly from byte `0` to `len(data)` with zero trailing garbage bytes, every command payload length is $\le 32$ bytes (`S3G_MAX_PACKET_PAYLOAD`, verified against Sailfish `Packet.hh:20`), and every string field is pure ASCII NUL-terminated.
- For serial framed S3G streams (`framed=True`): every packet starts with `0xD5`, has `1 <= length <= 32`, and its trailing byte matches the independent bit-by-bit CRC-8-Maxim (`x^8 + x^5 + x^4 + 1`, init `0x00`) over the payload bytes.

### Criterion E8 — Determinism
Converting the same G-code input twice with identical options MUST produce **byte-identical** `.x3g` output (`sha256(run1) == sha256(run2)`).

---

## 4. External Oracle Comparison Policy (`markwal/GPX`)

To guard against shared misunderstandings of the S3G/X3G protocol, we also compile `markwal/GPX` (`28f346005ee37117b1f042dd66570d108726a02f`) and compare decoded physical toolpaths of `orca_x3g` vs `gpx -r -m r2` (and `r2_factory` vs `gpx -r -m or2`).

Because GPX and OrcaX3G are independent implementations, exact byte-for-byte identity is **not** expected or required; instead, **every** observed difference must be explicitly classified into one of two categories:

1. **Benign / Documented Difference (Acceptable with firmware citation):**
   - Example 1: **Auto build name** (`153` `BUILD_START_NOTIFICATION`) differs (`"OrcaX3G"` / output stem vs GPX default filename).
   - Example 2: **Command `157` (`STREAM_VERSION`)** — GPX omits it by default or emits it conditionally; OrcaX3G omits it to avoid MakerBot Stock firmware bug (ADR-005).
   - Example 3: **Extruder reset strategy (`G92 E0`)** — GPX emits Command `140` (`SET_POSITION_EXT`) on `G92 E0` (which overwrites all 5 axes in firmware!), whereas OrcaX3G updates `e_logical_mm` in software and keeps relative `A` deltas zero-drift without risking an XYZ overwrite (ADR-006 / [`docs/FIRMWARE_AND_LEGACY_FINDINGS.md`](FIRMWARE_AND_LEGACY_FINDINGS.md)).
   - Example 4: **1-step boundary tie-breaking or `dda_rate` rounding** — IEEE-754 float order of operations can differ by $\pm 1$ LSB on exact `.5` midpoints, while still keeping decoded positions within $\le 1$ step (`11.25 µm` XY) and speed within `< 0.5%`.
   - Example 5: **`M106`/`M107` vs `M126`/`M127`** — GPX maps `M106` to Tool Action `12` (`TOGGLE_FAN`, which controls the heatsink cooling fan on Replicator 2!) whereas OrcaX3G maps `M106` to Tool Action `13` (`TOGGLE_VALVE`, the actual Replicator 2 part-cooling blower fan on MightyBoard Rev G/H, verified in `Sailfish/EepromMap.hh` & `ExtruderBoard.cc` — see [`docs/FIRMWARE_AND_LEGACY_FINDINGS.md`](FIRMWARE_AND_LEGACY_FINDINGS.md)).
2. **Unexplained / Physical Toolpath Discrepancy (BUG — Must Fail Test & Be Fixed):**
   - Any XYZ or E toolpath mismatch exceeding $1.0\text{ step}$, wrong axis sign, missing move, wrong nozzle temperature, or wrong homing direction.
