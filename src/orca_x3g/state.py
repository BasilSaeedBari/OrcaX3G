"""Modal printer state machine for deterministic G-code to X3G conversion."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PrinterState:
    """Tracks modal positioning, integer step coordinates, extruder state, and build lifecycle."""

    # Commanded XYZ positions in mm (IEEE-754 float64)
    x_mm: float = 0.0
    y_mm: float = 0.0
    z_mm: float = 0.0

    # Current integer step coordinates held by the printer on X, Y, Z
    x_steps: int = 0
    y_steps: int = 0
    z_steps: int = 0

    # Whether each XYZ axis absolute step coordinate is known by the printer
    x_known: bool = False
    y_known: bool = False
    z_known: bool = False

    # Extruder tracking (separated into logical G-code coordinate vs physical cumulative filament)
    e_logical_mm: float = 0.0
    e_physical_mm: float = 0.0
    e_emitted_steps: int = 0

    # Positioning modes
    relative_xyz: bool = False  # G90 = False, G91 = True
    relative_e: bool = False    # M82 = False, M83 = True

    # Modal feedrate in mm/min (None until first F word)
    feedrate_mm_min: float | None = None

    # Active tool, thermal, and fan state
    active_tool: int = 0
    nozzle_temp_c: int = 0
    fan_on: bool = False
    acceleration_enabled: bool = True

    # Build lifecycle flags
    build_started: bool = False
    build_ended: bool = False

    # Layer tracking (via ;LAYER_CHANGE comments or distinct Z extrusion heights)
    layers_from_comments: int = 0
    distinct_extrusion_z: set[float] = field(default_factory=set)

    @property
    def effective_relative_e(self) -> bool:
        """Per Marlin/RepRap/GPX semantics, G91 makes E relative too."""
        return self.relative_xyz or self.relative_e

    @property
    def layers_detected(self) -> int:
        if self.layers_from_comments > 0:
            return self.layers_from_comments
        return len(self.distinct_extrusion_z)
