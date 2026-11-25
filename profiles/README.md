# Recommended OrcaSlicer v2.5.2 Profiles for MakerBot Replicator 2

Import these three JSON presets in OrcaSlicer via **File → Import → Import Configs...**:

1. [`MakerBot_Replicator_2_0.4_nozzle.json`](MakerBot_Replicator_2_0.4_nozzle.json) — Machine profile:
   - Centered build plate `285 × 152 × 155 mm` (`X ∈ [-142.5, +142.5]`, `Y ∈ [-76, +76]`, `Z ∈ [0, 155]`).
   - `gcode_flavor = "marlin"` (required per ADR-006; OrcaSlicer's built-in `sailfish` flavor suppresses `M82`/`M83`, `G92 E0`, and `M109` waits).
   - Verified Replicator 2 axis speed limits (`300 / 300 / 19 / 26 mm/s`), `use_firmware_retraction = 0`, `auxiliary_fan = 0`, `z_hop_types = ["Normal Lift"]`.
   - Native Replicator 2 start/end G-code (`M136`, `G162`/`G161`, `M132`, `G130`, `M104`/`M109`, purge line, `M107`, `M104 S0`, `M18`, `M72 P1`, `M137`).
2. [`0.20mm_Standard_Replicator_2.json`](0.20mm_Standard_Replicator_2.json) — Process profile:
   - `enable_arc_fitting = "disabled"` (prevents unsupported `G2`/`G3` arcs).
   - `filename_format = "{input_filename_base}.x3g"` (makes OrcaSlicer's Save dialog default to `.x3g`).
   - `slicing_pipeline_plugin = ["X3G Exporter (Replicator 2)"]` (auto-runs OrcaX3G during **Export G-code**).
3. [`Replicator_2_Generic_PLA.json`](Replicator_2_Generic_PLA.json) — PLA filament profile:
   - All bed temperatures set to `0 °C` (Replicator 2 has no heated build plate).
   - `enable_pressure_advance = 0`, binary `100%` part cooling fan after layer 1.
