# Equivalence Report: `01_calibration_cube` — **PASS**

- **Machine profile:** `r2`
- **Build notices (153/154):** start=`True`, end=`True`
- **Motions:** G-code total `2,617` (non-zero step `2,617`, sub-step skipped `0`), X3G decoded `2,617`
- **Control events verified in order:** `133`
- **slicer:** `OrcaSlicer 2.5.2`
- **gcode_bytes:** `117346`
- **x3g_bytes:** `84227`
- **warnings:** `1`
- **ignored_commands:** `{'M204': 413}`

## Tolerance Verification Table

| Criterion | Metric | Observed Max / Value | Allowed Bound | Status |
|---|---|---|---|---|
| E1 | Max X position error | `5.500 µm` (`0.005500 mm`) | `<= 5.625 µm` | PASS |
| E1 | Max Y position error | `5.500 µm` (`0.005500 mm`) | `<= 5.625 µm` | PASS |
| E1 | Max Z position error | `0.000 µm` (`0.000000 mm`) | `<= 1.250 µm` | PASS |
| E2 | Max cumulative extrusion error | `5.193 µm` (`0.005193 mm`) | `<= 5.193 µm` | PASS |
| E2 | Max per-segment extrusion error | `10.144 µm` (`0.010144 mm`) | `<= 10.387 µm` | PASS |
| E2 | Final cumulative filament (G-code vs X3G) | `698.6077` vs `698.6119 mm` (Δ `4.222 µm`) | `<= 5.193 µm` | PASS |
| E3 | Non-zero-step motion count | `2,617` vs `2,617` | Exact match | PASS |
| E4 | Max `feedrate_mult64` error | `0.005208 mm/s` | `<= 0.007813 mm/s` | PASS |
| E4 | Max `dda_rate` error | `0.4936 steps/s` (max rel speed `0.0222%`) | `<= 0.5001 steps/s` | PASS |
| E6 | Total est. time (G-code vs X3G) | `383.798 s` vs `383.804 s` (Δ `0.0064 s`, `0.0017%`) | `<= max(0.5 s, 0.5%)` | PASS |

## Violations

None — all formal equivalence criteria E1–E8 satisfied.
