# Optional Hardware Verification Checklist — MakerBot Replicator 2

> **STATUS: NOT RUN**  
> All automated verification in OrcaX3G runs 100% in software (independent X3G decoder/VM, G-code simulator, Hypothesis properties, 1,000,000-line drift test, 5 real OrcaSlicer `v2.5.2` models, and external `markwal/GPX` oracle). This 1-page checklist is provided for a human operator with physical access to a MakerBot Replicator 2 to perform final bench confirmation before unattended printing.

---

## Pre-Flight Preparation

- [ ] **Firmware check:** Record Replicator 2 LCD splash firmware version (Sailfish `v7.7` / `v7.8` recommended; or MakerBot Stock `v7.5`/`v7.6`).
- [ ] **SD card:** Use an SD/SDHC card formatted **FAT16** (`<= 2 GB` recommended on Stock; Sailfish supports FAT16/FAT32).
- [ ] **Filename:** Copy `01_calibration_cube.x3g` to the SD card root as `CUBE01.X3G` (strict FAT 8.3 for Stock, `<= 31` chars for Sailfish).
- [ ] **Safety:** Keep one hand near the Replicator 2 rear power switch during Tests 1–3.

---

## Step-by-Step Bench Checks

### 1. SD Menu Recognition & Dry-Run Header Check (Cold)
- [ ] Insert SD card and open **Print from SD**. Confirm `CUBE01.X3G` appears cleanly without garbled characters.
- [ ] Start a cold test file (or cancel after homing) and confirm LCD displays the build name from Command `153` (`BUILD_START_NOTIFICATION`) and progress `0%` (`M73`).

### 2. Homing, Endstops & Build-Plate Centering (Hand on Power Switch)
- [ ] Confirm X and Y move toward **back-right** (MAX endstops, `G162` / Command `132`) and stop cleanly without grinding.
- [ ] Confirm Z moves **upward** toward the nozzle (MIN endstop, `G161` / Command `131`), backs off 5 mm, and re-homes slowly.
- [ ] Confirm after `M132 X Y Z A B` the carriage moves to the front-left waiting corner (`X=-112, Y=-73, Z=30`) without crashing into frame rails.
- [ ] Confirm the print itself executes centered on the build plate (`(0, 0)` at plate center, NOT front-left corner).

### 3. Heater, Wait, Extrusion Direction & Fan Channel
- [ ] Confirm LCD target nozzle temperature matches G-code (`215 °C` first layer, `210 °C` subsequent layers) and the printer **waits** (`Command 135`) before moving to purge.
- [ ] Confirm no heated-bed target appears (Replicator 2 has no heated bed).
- [ ] During purge line (`E+8.0 mm`), confirm filament feeds **downward into the hotend** (confirming negative `A` step sign `ACCELERATION_EXTRUDE_WHEN_NEGATIVE_A`).
- [ ] Confirm on layer 2 (`M106 S255`), the **part cooling blower fan** (Tool Action `13`, `SLAVE_CMD_TOGGLE_VALVE`) turns **ON**, while the heatsink fan remains under thermal control; confirm it turns **OFF** (`M107`) at end of print.

### 4. Dimensional Accuracy & Retraction Check
- [ ] Print `01_calibration_cube.x3g` (`20.0 × 20.0 × 10.0 mm`) with PLA.
- [ ] Measure with digital calipers:
  - `X`: expected `20.0 ± 0.2 mm` (if systematically `~0.35%` undersized/oversized, compare `r2` `88.888889` vs `r2_factory` `88.573186` profile!).
  - `Y`: expected `20.0 ± 0.2 mm`.
  - `Z`: expected `10.0 ± 0.15 mm`.
- [ ] Confirm end-of-print sequence: fan off, heater `0 °C`, bed drops to `Z=150`, carriage parks at MAX endstops, steppers disable (`M18`), **Ta-Da** melody plays (`M72 P1`), and LCD exits build cleanly (`Command 154`).

### 5. Optional Retraction & Large-Footprint Checks
- [ ] Print `03_retraction_towers.x3g` — confirm clean retractions with no backward-extrusion blobs or Z-skipping.
- [ ] Dry-run `04_large_footprint.x3g` (no filament) — confirm carriage reaches `±125 mm X`, `±62 mm Y` smoothly without hitting endstops.
- [ ] Test OrcaSlicer `v2.5.2` GUI install: install `orca_x3g-0.1.0-py3-none-any.whl` via **Plugins → Install**, import `profiles/*.json`, slice a model, click **Export G-code**, confirm Save dialog defaults to `.x3g` and outputs a valid binary `.x3g` with zero audit prompts, and inspect the **X3G Export** tab.
