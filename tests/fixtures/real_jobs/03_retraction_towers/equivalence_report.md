# Equivalence Report: `03_retraction_towers` — **PASS**

- **Machine profile:** `r2`
- **Build notices (153/154):** start=`True`, end=`True`
- **Motions:** G-code total `7,312` (non-zero step `7,312`, sub-step skipped `0`), X3G decoded `7,312`
- **Control events verified in order:** `145`
- **slicer:** `OrcaSlicer 2.5.2`
- **gcode_bytes:** `290837`
- **x3g_bytes:** `234504`
- **warnings:** `1`
- **ignored_commands:** `{'M204': 1889}`

## Tolerance Verification Table

| Criterion | Metric | Observed Max / Value | Allowed Bound | Status |
|---|---|---|---|---|
| E1 | Max X position error | `5.500 µm` (`0.005500 mm`) | `<= 5.625 µm` | PASS |
| E1 | Max Y position error | `5.500 µm` (`0.005500 mm`) | `<= 5.625 µm` | PASS |
| E1 | Max Z position error | `0.000 µm` (`0.000000 mm`) | `<= 1.250 µm` | PASS |
| E2 | Max cumulative extrusion error | `5.193 µm` (`0.005193 mm`) | `<= 5.193 µm` | PASS |
| E2 | Max per-segment extrusion error | `9.917 µm` (`0.009917 mm`) | `<= 10.387 µm` | PASS |
| E2 | Final cumulative filament (G-code vs X3G) | `1199.9813` vs `1199.9767 mm` (Δ `4.550 µm`) | `<= 5.193 µm` | PASS |
| E3 | Non-zero-step motion count | `7,312` vs `7,312` | Exact match | PASS |
| E4 | Max `feedrate_mult64` error | `0.005208 mm/s` | `<= 0.007813 mm/s` | PASS |
| E4 | Max `dda_rate` error | `0.5000 steps/s` (max rel speed `0.0225%`) | `<= 0.5001 steps/s` | PASS |
| E6 | Total est. time (G-code vs X3G) | `774.015 s` vs `774.025 s` (Δ `0.0097 s`, `0.0013%`) | `<= max(0.5 s, 0.5%)` | PASS |

## Violations

None — all formal equivalence criteria E1–E8 satisfied.
