# Equivalence Report: `04_large_footprint` — **PASS**

- **Machine profile:** `r2`
- **Build notices (153/154):** start=`True`, end=`True`
- **Motions:** G-code total `22,183` (non-zero step `22,183`, sub-step skipped `0`), X3G decoded `22,183`
- **Control events verified in order:** `188`
- **slicer:** `OrcaSlicer 2.5.2`
- **gcode_bytes:** `743860`
- **x3g_bytes:** `710505`
- **warnings:** `1`
- **ignored_commands:** `{'M204': 2872}`

## Tolerance Verification Table

| Criterion | Metric | Observed Max / Value | Allowed Bound | Status |
|---|---|---|---|---|
| E1 | Max X position error | `5.500 µm` (`0.005500 mm`) | `<= 5.625 µm` | PASS |
| E1 | Max Y position error | `5.500 µm` (`0.005500 mm`) | `<= 5.625 µm` | PASS |
| E1 | Max Z position error | `0.000 µm` (`0.000000 mm`) | `<= 1.250 µm` | PASS |
| E2 | Max cumulative extrusion error | `5.193 µm` (`0.005193 mm`) | `<= 5.193 µm` | PASS |
| E2 | Max per-segment extrusion error | `10.366 µm` (`0.010366 mm`) | `<= 10.387 µm` | PASS |
| E2 | Final cumulative filament (G-code vs X3G) | `4846.2301` vs `4846.2324 mm` (Δ `2.273 µm`) | `<= 5.193 µm` | PASS |
| E3 | Non-zero-step motion count | `22,183` vs `22,183` | Exact match | PASS |
| E4 | Max `feedrate_mult64` error | `0.005208 mm/s` | `<= 0.007813 mm/s` | PASS |
| E4 | Max `dda_rate` error | `0.4991 steps/s` (max rel speed `0.0221%`) | `<= 0.5001 steps/s` | PASS |
| E6 | Total est. time (G-code vs X3G) | `2715.867 s` vs `2715.907 s` (Δ `0.0401 s`, `0.0015%`) | `<= max(0.5 s, 0.5%)` | PASS |

## Violations

None — all formal equivalence criteria E1–E8 satisfied.
