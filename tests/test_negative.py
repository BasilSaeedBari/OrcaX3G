"""Negative tests asserting loud, line-numbered rejection of unsupported/invalid G-code."""

from __future__ import annotations

from pathlib import Path

import pytest

from orca_x3g import (
    ConversionOptions,
    GCodeSyntaxError,
    OutOfBoundsError,
    UnsupportedCommandError,
    ValueRangeError,
    convert_text,
)
from orca_x3g.cli import main as cli_main


@pytest.mark.parametrize(
    "gcode,expected_exc,expected_line,match",
    [
        ("G21\nG90\nG2 X10 Y10 I5 J0 F1200", UnsupportedCommandError, 3, "Arc"),
        ("G21\nG3 X10 Y10 I5 J0 F1200", UnsupportedCommandError, 2, "Arc"),
        ("G17", UnsupportedCommandError, 1, "G17"),
        ("G20", UnsupportedCommandError, 1, "Inches"),
        ("G10", UnsupportedCommandError, 1, "firmware retraction"),
        ("G11", UnsupportedCommandError, 1, "firmware retraction"),
        ("M140 S60", UnsupportedCommandError, 1, "heated build plate"),
        ("M190 S55", UnsupportedCommandError, 1, "heated build plate"),
        ("M141 S45", UnsupportedCommandError, 1, "chamber"),
        ("M191 S45", UnsupportedCommandError, 1, "chamber"),
        ("T1", UnsupportedCommandError, 1, "single-extruder"),
        ("M104 S200 T1", UnsupportedCommandError, 1, "Tool index"),
        ("M135 T1", UnsupportedCommandError, 1, "Tool index"),
        ("M106 P2 S255", UnsupportedCommandError, 1, "Fan index"),
        ("M107 P1", UnsupportedCommandError, 1, "Fan index"),
        ("G29", UnsupportedCommandError, 1, "Unsupported"),
        ("M420 S1", UnsupportedCommandError, 1, "Unsupported"),
        ("G90\nG92 X0 Y0 Z0 E0\nG1 X150 Y0 F1800", OutOfBoundsError, 3, "X"),
        ("G90\nG92 X0 Y0 Z0 E0\nG1 X-150 Y0 F1800", OutOfBoundsError, 3, "X"),
        ("G90\nG92 X0 Y0 Z0 E0\nG1 X0 Y80 F1800", OutOfBoundsError, 3, "Y"),
        ("G90\nG92 X0 Y0 Z0 E0\nG1 X0 Y0 Z160 F1100", OutOfBoundsError, 3, "Z"),
        ("G90\nG92 X0 Y0 Z0 E0\nG1 X0 Y0 Z-1 F1100", OutOfBoundsError, 3, "Z"),
        ("M104 S300 T0", ValueRangeError, 1, "280"),
        ("M104 S-5 T0", ValueRangeError, 1, "temperature"),
        ("G90\nG92 X0 Y0 Z0 E0\nG1 X10 Y0", ValueRangeError, 3, "feedrate"),
        ("G90\nG92 X0 Y0 Z0 E0\nG1 X10 Y0 F0", ValueRangeError, 3, "Feedrate"),
        ("G90\nG92 X0 Y0 Z0 E0\nG1 X10 Y0 F-100", ValueRangeError, 3, "Feedrate"),
        ("G4 P-10", ValueRangeError, 1, "negative"),
        ("G4", GCodeSyntaxError, 1, "P<milliseconds>"),
    ],
)
def test_rejected_commands_raise_with_exact_line_number(
    gcode: str,
    expected_exc: type[Exception],
    expected_line: int,
    match: str,
) -> None:
    with pytest.raises(expected_exc, match=match) as exc_info:
        convert_text(gcode)
    assert exc_info.value.line_number == expected_line  # type: ignore[attr-defined]


def test_m322_rejected_in_stock_firmware_mode() -> None:
    with pytest.raises(UnsupportedCommandError, match="Sailfish-only") as exc_info:
        convert_text("G90\nM322 Z5.0", ConversionOptions(firmware="stock"))
    assert exc_info.value.line_number == 2


def test_heated_bed_zero_is_safely_ignored_with_warning() -> None:
    res = convert_text("M140 S0\nM190 S0\nG90\nG92 X0 Y0 Z0 E0\nG1 X5 F1200")
    assert res.summary.ignored_commands["M140 S0"] == 1
    assert res.summary.ignored_commands["M190 S0"] == 1
    assert any("no heated build plate" in w for w in res.summary.warnings)


def test_cli_returns_nonzero_and_leaves_no_output_on_error(tmp_path: Path) -> None:
    bad_gcode = tmp_path / "bad.gcode"
    out_x3g = tmp_path / "bad.x3g"
    bad_gcode.write_text("G90\nG2 X10 Y10 I5 J0 F1200\n", encoding="utf-8")

    rc = cli_main([str(bad_gcode), str(out_x3g), "--quiet"])
    assert rc == 2
    assert not out_x3g.exists()
