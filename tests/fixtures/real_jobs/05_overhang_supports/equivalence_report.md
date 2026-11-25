# Equivalence Report: `05_overhang_supports` — **PASS**

- **Machine profile:** `r2`
- **Build notices (153/154):** start=`True`, end=`True`
- **Motions:** G-code total `4,307` (non-zero step `4,307`, sub-step skipped `0`), X3G decoded `4,307`
- **Control events verified in order:** `138`
- **slicer:** `OrcaSlicer 2.5.2`
- **gcode_bytes:** `180548`
- **x3g_bytes:** `138323`
- **warnings:** `1`
- **ignored_commands:** `{'M204': 721}`

## Tolerance Verification Table

| Criterion | Metric | Observed Max / Value | Allowed Bound | Status |
|---|---|---|---|---|
| E1 | Max X position error | `5.500 µm` (`0.005500 mm`) | `<= 5.625 µm` | PASS |
| E1 | Max Y position error | `5.500 µm` (`0.005500 mm`) | `<= 5.625 µm` | PASS |
| E1 | Max Z position error | `1.000 µm` (`0.001000 mm`) | `<= 1.250 µm` | PASS |
| E2 | Max cumulative extrusion error | `5.190 µm` (`0.005190 mm`) | `<= 5.193 µm` | PASS |
| E2 | Max per-segment extrusion error | `10.234 µm` (`0.010234 mm`) | `<= 10.387 µm` | PASS |
| E2 | Final cumulative filament (G-code vs X3G) | `1086.1923` vs `1086.1883 mm` (Δ `4.028 µm`) | `<= 5.193 µm` | PASS |
| E3 | Non-zero-step motion count | `4,307` vs `4,307` | Exact match | PASS |
| E4 | Max `feedrate_mult64` error | `0.007292 mm/s` | `<= 0.007813 mm/s` | PASS |
| E4 | Max `dda_rate` error | `0.4985 steps/s` (max rel speed `0.0218%`) | `<= 0.5001 steps/s` | PASS |
| E6 | Total est. time (G-code vs X3G) | `598.503 s` vs `598.489 s` (Δ `0.0144 s`, `0.0024%`) | `<= max(0.5 s, 0.5%)` | PASS |

## Violations

None — all formal equivalence criteria E1–E8 satisfied.
