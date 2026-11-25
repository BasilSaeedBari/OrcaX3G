# Legacy Software, Firmware & OrcaSlicer Technical Findings

**Retrieval Date:** 2026-11-25  
**Purpose:** Permanent engineering reference documenting every protocol subtlety, firmware quirk, legacy software bug, and OrcaSlicer `v2.5.2` plugin-host detail discovered by reading primary source code. Full file/line citations are indexed in [`docs/SOURCES.md`](SOURCES.md).

---

## 1. The `.x3g` File Format vs S3G Wire Protocol (Crucial Distinction)

A frequent source of confusion in third-party documentation is conflating the **S3G serial transport protocol** with the **`.x3g` SD-card binary file format**.

- **Over USB / RS-232 Serial (`Sailfish/shared/Packet.cc:54–139`, `s3gProtocol.md:111–140`):**
  Every message is wrapped in a packet frame:
  ```
  Byte 0:         0xD5 (START_BYTE)
  Byte 1:         N    (payload length, 0..32; MAX_PACKET_PAYLOAD = 32)
  Bytes 2..1+N:   Payload (Host Query 0..127 or Host Buffered Action 128..255)
  Byte 2+N:       CRC-8-Maxim / iButton of Payload bytes only
  ```
  The CRC is computed with avr-libc `_crc_ibutton_update(crc, data)` (`Packet.cc:25`), corresponding to CRC-8-Maxim (polynomial `0x31`, reflected LSB-first `0x8C`, init `0x00`, no final XOR).

- **Inside an `.x3g` File on SD Card (`Sailfish/Motherboard/SDCard.cc:382–389`, `Command.cc:1213–1216`, `s3g/Writer/FileWriter.py:38–44`, `GPX/README.md:82`):**
  When the MightyBoard captures packets to SD (`capturePacket`), it writes **only `packet.getData()`** (`packet.getLength()` bytes), stripping `0xD5`, `Length`, and `CRC`. When playing back an `.x3g` file from SD, `runCommandSlice()` feeds raw bytes directly from `fat_read_file()` into `command_buffer` and immediately switches on `command_buffer[0]` as the Host Buffered Command opcode.
  - **Consequence:** An `.x3g` file is a **raw, unframed concatenation of buffered action command payloads** (no magic header, no `0xD5`, no length prefix, no per-packet CRC). If `0xD5` (213) framing bytes were written into an `.x3g` file, `Command.cc` would hit the unhandled `else {}` branch on byte `213`, never pop it from `command_buffer`, and **hang the printer indefinitely**.

---

## 2. Firmware Archaeology: Sailfish vs Stock MightyBoardFirmware (Rev G/H `mighty_two`)

Direct comparison of `jetty840/Sailfish-MightyBoardFirmware` (`4d8456e5`) and `makerbot/MightyBoardFirmware` (`207c6db1`) for the `mighty_two` platform (MakerBot Replicator 2, ATmega1280, MightyBoard Rev G/H) revealed the following ground-truth behaviors:

### 2.1 Hardware Pin & Electrical Findings (`boards/mighty_two/Configuration.hh`)
- **Microstepping:** `MICROSTEPPING = 4` → $1/16$ microstepping ($200 \times 16 = 3200$ microsteps/rev) on Allegro A4982 BotStep17 drivers.
- **Digital Potentiometers:** Software-I2C digipots (`DIGIPOT_SUPPORT = 1`) set stepper Vref per axis via Command `145` (`G130`, values `0..127`). Start G-code lowers XYAB digipots to `20` during heating to avoid driver overheating while stationary, then restores them to `127` before printing.
- **Temperature Sensing:** Dual-channel Type-K thermocouple via TI ADS1118 SPI ADC (`USE_THERMOCOUPLE_DUAL = 1`, sampled at `250 ms` intervals → `1.333 Hz` across cold junction + 2 channels).
- **Two Separate Fan Circuits (Critical Mapping!):**
  - `EXA_FAN` (`PortH,4`) / `EXB_FAN` (`PortE,4`): **Extruder heatsink fans**, controlled by Tool Action `12` (`SLAVE_CMD_TOGGLE_FAN`) and automatically managed by firmware above 50°C.
  - `EXTRA_FET` = `EX_FAN` (`PortG,5`, `#define ACTIVE_COOLING_FAN`): **Part cooling PLA blower fan**, controlled by Tool Action **`13` (`SLAVE_CMD_TOGGLE_VALVE`)**!
  - In `Command.cc:938`, `SLAVE_CMD_TOGGLE_VALVE` calls `board.setExtra((command_buffer[4] & 0x01) != 0)`. **X3G has NO PWM duty-cycle argument for the part cooling fan** — only bit 0 (`0` = OFF, `1` = ON). While Sailfish's `COOLING_FAN_PWM` feature (`Motherboard.cc:1009–1039`) can modulate `EXTRA_FET` using a static EEPROM duty cycle set from the LCD menu, the `.x3g` byte stream itself can only turn the blower ON or OFF.

### 2.2 Extruder Step Polarity (`ACCELERATION_EXTRUDE_WHEN_NEGATIVE_A`)
- In `boards/mighty_two/Configuration.hh:374–375`, both `ACCELERATION_EXTRUDE_WHEN_NEGATIVE_A` and `ACCELERATION_EXTRUDE_WHEN_NEGATIVE_B` are `true`: **negative A-axis step deltas extrude filament forward**, and **positive A-axis step deltas retract**.
- Cross-confirmed by `s3g/makerbot_driver/profiles/Replicator2.json:22` (`"steps_per_mm": -96.275`) and `GPX/src/gpx/gpx.c:2279` (`target.a = -deltaMM.a;`).
- Getting this sign backwards causes the printer to retract continuously instead of extruding (immediately unloading the filament at print start!).

### 2.3 Coordinate System Origin & Homing Sequence
- Replicator 2 homes **X and Y to MAX endstops** (`G162` → Command `132`) and **Z to MIN endstop** (`G161` → Command `131`).
- After hitting the endstops, `M132 X Y Z A B` (Command `144`, `HOST_CMD_RECALL_HOME_POSITION`) loads the machine-calibrated home offsets stored in EEPROM (`PLATFORM_X_OFFSET_STEPS = 13463` ≈ +152 mm, `PLATFORM_Y_OFFSET_STEPS = 6643` ≈ +75 mm in Sailfish; +152 mm / +72 mm in Stock), placing **`(0, 0, 0)` at the center of the build plate**.
- Unlike most modern RepRap/Marlin printers (whose `(0, 0)` is the front-left corner), valid Replicator 2 print coordinates are **signed**: $X \in [-142.5, +142.5]$ mm, $Y \in [-76.0, +76.0]$ mm, $Z \in [0.0, 155.0]$ mm.

### 2.4 Why `88.573186` vs `88.888889` Steps/mm Exists
A well-known historical discrepancy surfaced across our four primary sources:
- **True geometric tooth pitch (`88.888889` steps/mm):** The Replicator 2 uses 200-step/rev motors, 1/16 microstepping (`3200` steps/rev), and 18-tooth GT2 (2 mm pitch) timing pulleys → $3200 / (18 \times 2) = 88.\overline{8}\text{ steps/mm}$. Adopted by GPX `-m r2` (`std_machines.h:188–189`).
- **MakerBot original figure (`88.573186` steps/mm):** MakerBot originally derived steps/mm from the catalog *effective pitch diameter* of the GT2 belt/pulley rather than discrete tooth pitch, baking `88.573186` into Stock firmware (`EepromMap.hh:50–51,95`), Sailfish `mighty_two` (`platforms.py:223`), MakerBot Desktop/s3g (`Replicator2.json:8,13`), and GPX `-m or2` (`classic_machines.h:112–113`).
- **Magnitude of difference:** $0.356\%$ ($\approx 0.71\text{ mm}$ or $\approx 63\text{ steps}$ across 200 mm). We support `88.888889` as the default `r2` profile and `88.573186` as the selectable `r2_factory` profile (ADR-002).

### 2.5 Command `155` (`HOST_CMD_QUEUE_POINT_NEW_EXT`) Exact Firmware Execution
Verified in `Sailfish/Steppers.cc:823–1005` and `MightyBoardFirmware/Steppers.cc:517–583`:
- **Zero-move skip:** `if ((planner_master_steps == 0) || (distance == 0.0)) return;` — firmware drops moves with zero master steps or zero distance.
- **`relative` bitmask:** Bits 0–4 select relative vs absolute for X, Y, Z, A, B. Sailfish masks `pop8() & 0x7F` (`Command.cc:836`) because bit 7 is reused internally for live speed override (`alterSpeed`).
- **Signed `feedrateMult64`:** Although `s3gProtocol.md:1271` labels this field `uint16`, both Sailfish (`Command.cc:839`) and Stock firmware read it as signed `int16_t`, then shift `>> 6` (dividing by 64) to recover mm/s in fixed-point. Maximum safe value is `32767` (`511.98 mm/s`, well above the Replicator 2's `300 mm/s` = `19200`).
- **Unaccelerated vs Accelerated execution:** When firmware acceleration is OFF, the planner executes at `dda_rate` (master-axis steps/sec). When firmware acceleration is ON, it uses `feedrateMult64 / 64.0`, `distance` (mm), and `dda_rate` (`nominal_rate`) together.

### 2.6 Bugs & Incompatibilities Found Between Stock Firmware and Sailfish
1. **Stock Firmware `HOST_CMD_STREAM_VERSION` (157) Buffer Bug (`MightyBoardFirmware/Command.cc:796–826`):** Stock checks `if (command_buffer.getLength() >= 11)` and then unconditionally pops **21 bytes** (risking circular-buffer underflow if the command spans an SD read boundary!), and raises an error dialog if `stream_version` or `bot_type != 0xB015`. Sailfish pops and ignores all 21 bytes; GPX omits Command `157` entirely. **OrcaX3G omits Command `157`.**
2. **Sailfish-Only `HOST_CMD_PAUSE_AT_ZPOS` (158):** Defined in `Sailfish/Commands.hh:118` and handled in `Sailfish/Command.cc:2107–2117`. Completely absent from Stock `MightyBoardFirmware` — emitting opcode `158` on Stock firmware causes an unrecoverable print hang. **OrcaX3G gates Command `158` behind explicit Sailfish-only mode.**
3. **Partial `G92` Trap (`HOST_CMD_SET_POSITION_EXT` = 140):** Command `140` overwrites all 5 axes (`X, Y, Z, A, B`) simultaneously (`Command.cc:1532–1564`, explicitly noted as "botched" in Sailfish's own comment at `Command.cc:73–76`). When G-code issues `G92 E0` (common on every layer/retraction in `M82` mode), emitting Command `140` is dangerous if XYZ aren't yet established; instead, because `A` is emitted as relative deltas in Command `155`, `G92 E<val>` only needs to update the converter's software E origin!
4. **SD Card Filesystem & Name Limits:**
   - Stock firmware (`SDCard.cc:88–91`): `SD_RAW_SDHC` is disabled → **FAT16 only, volume size `< 2 GB` (`< 2,147,483,648` bytes)**, root directory only.
   - Sailfish (`boards/mighty_two/Configuration.hh:462–464`): Single-extruder Replicator 2 builds define `SD_RAW_SDHC 1` → **SDHC & FAT32 supported**, plus subdirectory browsing.
   - **Both** firmwares use `char long_name[32]` (`lib_sd/fat.h:75/90`) and `MAX_FILE_LEN = 31` (`Host.hh:32`), so SD filenames must stay $\le 31$ ASCII characters (8.3 DOS recommended for maximum Stock reliability). Build start notice (`153`) names are capped at $\le 24$ ASCII bytes (fitting inside the 32-byte `MAX_PACKET_PAYLOAD` limit and 20-char LCD).

---

## 3. Legacy Converter (`GPX`) & Slicer Flavor Findings

1. **The `M109` & `M106`/`M107` Flavor Collision (`GPX/src/gpx/gpx.c:5310–5528`):**
   - Under **MakerBot/ReplicatorG flavor**, `M109` falls through to `M140` (heated bed temp!), `M133` waits for extruder, `M106`/`M107` toggle the **extruder heatsink fan** (`12`), and `M126`/`M127` toggle the **part cooling blower** (`13`).
   - Under **RepRap/Marlin flavor**, `M109` sets+waits for the **extruder**, and `M106`/`M107` toggle the **part cooling blower** (`13`).
2. **Why OrcaSlicer's Built-In `sailfish` / `makerware` Flavors Break Modern Slicing (`OrcaSlicer/src/libslic3r/GCodeWriter.cpp:285–319, 728–731`):**
   - When `gcode_flavor` is `gcfSailfish` or `gcfMakerWare`, OrcaSlicer's `GCodeWriter::preamble()` omits `M82`/`M83`, `reset_e()` returns `""` (never emits `G92 E0`), and `set_temperature(..., wait=true)` returns `""` (never waits for the nozzle to heat!).
   - Therefore, OrcaSlicer **must** slice with **`gcode_flavor = "marlin"`** (`gcfMarlinLegacy`), paired with our RepRap/Marlin converter mode.
3. **Hidden Arc Sources in OrcaSlicer (`GCodeWriter.cpp:951–961, 1269–1280`):**
   - OrcaSlicer emits `G2`/`G3` arcs not only when `enable_arc_fitting != "disabled"`, but also when `z_hop_types` is set to `Spiral Lift` while arc fitting is enabled. Both must be disabled in the Replicator 2 profile, and any `G2`/`G3` encountered is rejected with an explicit error and line number.

---

## 4. OrcaSlicer `v2.5.2` Plugin Architecture Findings

Reading `src/slic3r/plugin/` and `src/slic3r/GUI/` directly surfaced four critical host constraints that documentation alone did not fully reveal:

1. **In-Place `.pp` Conversion + `filename_format = "{input_filename_base}.x3g"` Enables Native `.x3g` Export:**
   - `Plater::export_gcode()` (`Plater.cpp:19534–19565`) passes the extension from `filename_format` into `GUI::file_wildcards(FT_GCODE, ext)` (`GUI_App.cpp:695–725`), putting `*.x3g;*.X3G` first in the native Save dialog.
   - `BackgroundSlicingProcess::finalize_gcode()` (`BackgroundSlicingProcess.cpp:857–888`) copies the temp G-code to a `.pp` file, runs `run_post_process_plugins()`, and then runs raw binary `copy_file()` (including removable-media read-back verification) directly to `export_path` without re-parsing it as G-code.
2. **Why `.output_name` Dynamic Renaming Does Not Work for Python Plugins:**
   - `PostProcessor.cpp:465–506` checks `path + ".output_name"` **after** classic `post_process` scripts and **before** `run_post_process_plugins()`, and passes `output_name` by `const std::string&`.
3. **Slicing Worker Thread UI Deadlock & Off-Thread Audit Fail-Closed:**
   - Every `orca.host.ui.*` function — including `push_notification()` (`PluginHostUi.cpp:672`) — uses `run_on_ui_blocking()`. Calling any of them from `SlicingPipelineCapabilityBase.execute()` can deadlock OrcaSlicer.
   - `PySlicingPipelinePluginCapabilityTrampoline::execute()` grants a scoped allowed root **only** for `ctx.gcode_path.parent_path()` (the temp folder, not `ctx.output_name.parent_path()`). Any off-thread write outside allowed roots fails closed with `PermissionError` (`PluginAuditManager.cpp`).
4. **Denied Path Keyword Substring Trap:**
   - `PluginAuditManager` unconditionally blocks any path component containing `"secret"`, `"cert"`, or `"conf"` (case-insensitive substring match, even inside `data_dir()`). Naming any plugin module `config.py` or `machine_config.py` would trigger audit denials!
