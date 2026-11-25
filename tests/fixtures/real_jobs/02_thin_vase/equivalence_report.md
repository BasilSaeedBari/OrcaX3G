# Equivalence Report: `02_thin_vase` — **PASS**

- **Machine profile:** `r2`
- **Build notices (153/154):** start=`True`, end=`True`
- **Motions:** G-code total `5,653` (non-zero step `5,556`, sub-step skipped `97`), X3G decoded `5,556`
- **Control events verified in order:** `136`
- **slicer:** `OrcaSlicer 2.5.2`
- **gcode_bytes:** `253169`
- **x3g_bytes:** `178277`
- **warnings:** `1`
- **ignored_commands:** `{'M204': 198}`

## Tolerance Verification Table

| Criterion | Metric | Observed Max / Value | Allowed Bound | Status |
|---|---|---|---|---|
| E1 | Max X position error | `5.500 µm` (`0.005500 mm`) | `<= 5.625 µm` | PASS |
| E1 | Max Y position error | `5.500 µm` (`0.005500 mm`) | `<= 5.625 µm` | PASS |
| E1 | Max Z position error | `1.000 µm` (`0.001000 mm`) | `<= 1.250 µm` | PASS |
| E2 | Max cumulative extrusion error | `5.192 µm` (`0.005192 mm`) | `<= 5.193 µm` | PASS |
| E2 | Max per-segment extrusion error | `10.135 µm` (`0.010135 mm`) | `<= 10.387 µm` | PASS |
| E2 | Final cumulative filament (G-code vs X3G) | `603.8742` vs `603.8731 mm` (Δ `1.128 µm`) | `<= 5.193 µm` | PASS |
| E3 | Non-zero-step motion count | `5,556` vs `5,556` | Exact match | PASS |
| E4 | Max `feedrate_mult64` error | `0.005208 mm/s` | `<= 0.007813 mm/s` | PASS |
| E4 | Max `dda_rate` error | `0.4971 steps/s` (max rel speed `0.0238%`) | `<= 0.5001 steps/s` | PASS |
| E6 | Total est. time (G-code vs X3G) | `672.912 s` vs `672.912 s` (Δ `0.0007 s`, `0.0001%`) | `<= max(0.5 s, 0.5%)` | PASS |

## Violations

None — all formal equivalence criteria E1–E8 satisfied.
