# Recommended OrcaSlicer v2.5.2 Profiles for MakerBot Replicator 2

Import these JSON presets in OrcaSlicer via **File → Import → Import Configs...**:

1. [`MakerBot_Replicator_2_0.4_nozzle.json`](MakerBot_Replicator_2_0.4_nozzle.json) — Machine profile:
   - Centered build plate `285 × 152 × 155 mm` (`X ∈ [-142.5, +142.5]`, `Y ∈ [-76, +76]`, `Z ∈ [0, 155]`).
   - `gcode_flavor = "marlin"` (required per ADR-006; OrcaSlicer's built-in `sailfish` flavor suppresses `M82`/`M83`, `G92 E0`, and `M109` waits).
   - Conservative machine acceleration ceiling `800 mm/s²` on X/Y/extruding/retracting/travel (`150 mm/s²` Z), verified Replicator 2 max axis feedrates (`300 / 300 / 19 / 26 mm/s`), `use_firmware_retraction = 0`, `auxiliary_fan = 0`, `z_hop_types = ["Normal Lift"]`.
   - Native Replicator 2 start/end G-code (`M136`, `G162`/`G161`, `M132`, `G130`, `M104`/`M109`, purge line, `M107`, `M104 S0`, `M18`, `M72 P1`, `M137`).
2. [`0.20mm_Slow_Safe_50mms_Replicator_2.json`](0.20mm_Slow_Safe_50mms_Replicator_2.json) — **Recommended First-Test Slow/Safe Process Profile (50 mm/s, 500–800 mm/s²):**
   - Gentle speeds for older hardware: `20 mm/s` first layer, `40 mm/s` outer walls / top surfaces, `50 mm/s` max for inner walls & infill, `30 mm/s` bridges, `80 mm/s` travel.
   - Conservative accelerations: `500 mm/s²` first layer / outer wall / top surface, `650 mm/s²` default / inner wall / solid infill, `750 mm/s²` sparse infill, `800 mm/s²` travel.
   - `enable_arc_fitting = "disabled"`, `filename_format = "{input_filename_base}.x3g"`, `slicing_pipeline_plugin = ["X3G Exporter (Replicator 2)"]`.
3. [`0.20mm_Standard_Replicator_2.json`](0.20mm_Standard_Replicator_2.json) — Standard process profile (`50 mm/s` outer wall, `70–80 mm/s` inner/infill, `120 mm/s` travel) for once the printer is verified healthy.
4. [`Replicator_2_Generic_PLA.json`](Replicator_2_Generic_PLA.json) — PLA filament profile:
   - All bed temperatures set to `0 °C` (Replicator 2 has no heated build plate).
   - `enable_pressure_advance = 0`, binary `100%` part cooling fan after layer 1.

> **Important MightyBoard / X3G Note on Acceleration (`500–800 mm/s²`):**  
> Print **speeds** (`<= 50 mm/s`) are encoded directly into every X3G Command `155` packet (`dda_rate` & `feedrate_mult64`), so the `50 mm/s` cap takes immediate physical effect on the printer.  
> However, the X3G binary format has **no per-move acceleration command** (`M204` is ignored by X3G converters including OrcaX3G and GPX). MightyBoard firmware uses its **onboard EEPROM** acceleration settings (Sailfish defaults: `Max Accel X/Y = 1000 mm/s²`, `Normal Move Accel = 2000 mm/s²`, per `Sailfish/EepromMap.hh:569–575`). Setting `500–800 mm/s²` in OrcaSlicer keeps slicer time estimates accurate, and to physically cap hardware acceleration to `500–800 mm/s²` on the Replicator 2, also set **Max Accel X = 650–800**, **Max Accel Y = 650–800**, and **Max Accel Normal = 650–800** in the printer's onboard EEPROM (via ReplicatorG / MakerBot Print → Machine → Onboard Preferences, or Sailfish LCD menu).
