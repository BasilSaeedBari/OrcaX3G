"""Verified MakerBot Replicator 2 machine profiles with primary-source citations.

Every physical constant in this module is traced to a primary source recorded in
docs/SOURCES.md (retrieved 2026-11-25). Per ADR-002 and ADR-003:
- ``r2`` (default) uses 88.888889 steps/mm on X/Y (exact 18T GT2 tooth pitch:
  200 * 16 / (18 * 2), matching GPX ``-m r2``).
- ``r2_factory`` uses 88.573186 steps/mm on X/Y (MakerBot factory pitch-diameter
  figure, matching Sailfish ``mighty_two``, Stock MightyBoardFirmware EEPROM
  defaults, MakerBot ``Replicator2.json``, and GPX ``-m or2``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EndstopSide(str, Enum):
    """Physical endstop placement along an axis."""

    MIN = "min"
    MAX = "max"


@dataclass(frozen=True)
class AxisSpec:
    """Kinematic specification for a linear axis (X, Y, or Z)."""

    name: str
    steps_per_mm: float
    max_feedrate_mm_min: float
    home_feedrate_mm_min: float
    min_mm: float
    max_mm: float
    home_endstop: EndstopSide
    home_offset_mm: float
    source: str

    @property
    def length_mm(self) -> float:
        return self.max_mm - self.min_mm


@dataclass(frozen=True)
class ExtruderSpec:
    """Kinematic specification for an extruder axis (A on single-extruder R2)."""

    name: str
    tool_id: int
    steps_per_mm: float
    max_feedrate_mm_min: float
    inverted_direction: bool  # True => negative A steps extrude forward
    nominal_filament_diameter_mm: float
    nozzle_diameter_mm: float
    source: str


@dataclass(frozen=True)
class MachineProfile:
    """Complete verified machine definition for X3G planning and validation."""

    profile_id: str
    display_name: str
    bot_type_pid: int
    x: AxisSpec
    y: AxisSpec
    z: AxisSpec
    extruder_a: ExtruderSpec
    extruder_count: int
    has_heated_bed: bool
    has_heated_chamber: bool
    max_nozzle_temp_c: int
    homing_timeout_s: int
    heater_wait_ping_ms: int
    heater_wait_timeout_s: int
    sd_max_filename_len: int
    build_name_max_len: int
    sources: tuple[str, ...]

    def axis(self, name: str) -> AxisSpec:
        key = name.upper()
        if key == "X":
            return self.x
        if key == "Y":
            return self.y
        if key == "Z":
            return self.z
        raise KeyError(f"Unknown linear axis: {name}")


REPLICATOR_2 = MachineProfile(
    profile_id="r2",
    display_name="MakerBot Replicator 2 (18T GT2: 88.888889 steps/mm)",
    bot_type_pid=0xB015,  # Sailfish platforms.py:218, s3gProtocol.md:1297
    x=AxisSpec(
        name="X",
        steps_per_mm=88.888889,
        max_feedrate_mm_min=18000.0,
        home_feedrate_mm_min=2500.0,
        min_mm=-142.5,
        max_mm=142.5,
        home_endstop=EndstopSide.MAX,
        home_offset_mm=152.0,
        source="GPX std_machines.h:188 (3200/(18*2)=88.888889, 285mm, 18000 mm/min); "
        "Sailfish platforms.py:219,222 (13463 steps ~152mm, 285mm)",
    ),
    y=AxisSpec(
        name="Y",
        steps_per_mm=88.888889,
        max_feedrate_mm_min=18000.0,
        home_feedrate_mm_min=2500.0,
        min_mm=-76.0,
        max_mm=76.0,
        home_endstop=EndstopSide.MAX,
        home_offset_mm=75.0,
        source="GPX std_machines.h:189 (88.888889, 152mm, 18000 mm/min); "
        "Sailfish platforms.py:220,222 (6643 steps ~75mm, 152mm)",
    ),
    z=AxisSpec(
        name="Z",
        steps_per_mm=400.0,
        max_feedrate_mm_min=1170.0,
        home_feedrate_mm_min=1100.0,
        min_mm=0.0,
        max_mm=155.0,
        home_endstop=EndstopSide.MIN,
        home_offset_mm=0.0,
        source="Sailfish platforms.py:222-223 & EepromMap.hh:166 (400 steps/mm, 155mm, 1170 mm/min); "
        "GPX std_machines.h:190",
    ),
    extruder_a=ExtruderSpec(
        name="A",
        tool_id=0,
        steps_per_mm=96.27520187033366,
        max_feedrate_mm_min=1600.0,
        inverted_direction=True,
        nominal_filament_diameter_mm=1.75,
        nozzle_diameter_mm=0.4,
        source="Sailfish boards/mighty_two/Configuration.hh:374 (ACCELERATION_EXTRUDE_WHEN_NEGATIVE_A=true), "
        "EepromMap.hh:166,186 (96.275202, 1600 mm/min); GPX std_machines.h:191",
    ),
    extruder_count=1,
    has_heated_bed=False,
    has_heated_chamber=False,
    max_nozzle_temp_c=280,
    homing_timeout_s=20,
    heater_wait_ping_ms=100,
    heater_wait_timeout_s=600,
    sd_max_filename_len=31,  # Sailfish Host.hh:32 (MAX_FILE_LEN = MAX_PACKET_PAYLOAD - 1 = 31)
    build_name_max_len=24,  # GPX gpx.c:2171 (fits inside 32-byte payload and 20-char LCD)
    sources=(
        "jetty840/Sailfish-MightyBoardFirmware@4d8456e5",
        "makerbot/MightyBoardFirmware@207c6db1",
        "makerbot/s3g@d7991b8b",
        "markwal/GPX@28f34600",
    ),
)

REPLICATOR_2_FACTORY = MachineProfile(
    profile_id="r2_factory",
    display_name="MakerBot Replicator 2 (Factory EEPROM: 88.573186 steps/mm)",
    bot_type_pid=0xB015,
    x=AxisSpec(
        name="X",
        steps_per_mm=88.573186,
        max_feedrate_mm_min=18000.0,
        home_feedrate_mm_min=2500.0,
        min_mm=-142.5,
        max_mm=142.5,
        home_endstop=EndstopSide.MAX,
        home_offset_mm=152.0,
        source="Sailfish platforms.py:223 (88573186/1e6); Stock MightyBoardFirmware EepromMap.hh:50; "
        "s3g Replicator2.json:8; GPX classic_machines.h:112 (or2)",
    ),
    y=AxisSpec(
        name="Y",
        steps_per_mm=88.573186,
        max_feedrate_mm_min=18000.0,
        home_feedrate_mm_min=2500.0,
        min_mm=-76.0,
        max_mm=76.0,
        home_endstop=EndstopSide.MAX,
        home_offset_mm=75.0,
        source="Sailfish platforms.py:223 (88573186/1e6); Stock MightyBoardFirmware EepromMap.hh:51; "
        "s3g Replicator2.json:13; GPX classic_machines.h:113 (or2)",
    ),
    z=REPLICATOR_2.z,
    extruder_a=REPLICATOR_2.extruder_a,
    extruder_count=1,
    has_heated_bed=False,
    has_heated_chamber=False,
    max_nozzle_temp_c=280,
    homing_timeout_s=20,
    heater_wait_ping_ms=100,
    heater_wait_timeout_s=600,
    sd_max_filename_len=31,
    build_name_max_len=24,
    sources=REPLICATOR_2.sources,
)

PROFILES: dict[str, MachineProfile] = {
    REPLICATOR_2.profile_id: REPLICATOR_2,
    REPLICATOR_2_FACTORY.profile_id: REPLICATOR_2_FACTORY,
    # Friendly aliases matching GPX flags
    "or2": REPLICATOR_2_FACTORY,
}


def get_profile(profile_id: str) -> MachineProfile:
    """Look up a verified Replicator 2 machine profile by ID."""
    key = profile_id.strip().lower()
    if key not in PROFILES:
        valid = ", ".join(sorted(PROFILES.keys()))
        raise ValueError(f"Unknown machine profile {profile_id!r}. Valid profiles: {valid}")
    return PROFILES[key]
