"""Unit tests for the strict deterministic G-code lexer/parser."""

from __future__ import annotations

import pytest

from orca_x3g.errors import GCodeSyntaxError
from orca_x3g.parser import parse_gcode_line as parse_line


class TestGCodeParserValid:
    def test_blank_and_comment_only_lines(self) -> None:
        p1 = parse_line("   \t  \n", 1)
        assert p1.command is None and p1.directive is None

        p2 = parse_line(";LAYER_CHANGE", 2)
        assert p2.command is None
        assert p2.comment == "LAYER_CHANGE"

        p3 = parse_line("(parenthesized comment)", 3)
        assert p3.command is None
        assert p3.comment == "parenthesized comment"

    def test_case_normalization_whitespace_and_compact_words(self) -> None:
        p = parse_line("  g01x10.5y-20.25z0.2e0.123f1800 ; move  ", 10)
        assert p.command == "G1"
        assert p.params == {"X": 10.5, "Y": -20.25, "Z": 0.2, "E": 0.123, "F": 1800.0}
        assert p.comment == "move"

    def test_line_number_and_valid_xor_checksum(self) -> None:
        body = "N12 G1 X10 Y20 F1500"
        csum = 0
        for ch in body.encode("ascii"):
            csum ^= ch
        p = parse_line(f"{body}*{csum} ; ok", 12)
        assert p.command == "G1"
        assert p.params["X"] == 10.0
        assert p.params["Y"] == 20.0

    def test_bare_axis_flags_on_homing_and_stepper_commands(self) -> None:
        p1 = parse_line("G28 X Y", 1)
        assert p1.command == "G28"
        assert p1.params == {"X": None, "Y": None}

        p2 = parse_line("M132 X Y Z A B", 2)
        assert p2.command == "M132"
        assert set(p2.params.keys()) == {"X", "Y", "Z", "A", "B"}

        p3 = parse_line("M18", 3)
        assert p3.command == "M18"
        assert p3.params == {}

    def test_m117_and_exclude_object_directives(self) -> None:
        p1 = parse_line("M117 Printing layer 5/50 (PLA)", 1)
        assert p1.command == "M117"
        assert "Printing layer 5/50" in p1.freeform_text

        p2 = parse_line("EXCLUDE_OBJECT_START NAME=cube_1", 2)
        assert p2.directive == "EXCLUDE_OBJECT_START"
        assert p2.command is None

    def test_leading_decimal_point_and_m70_paren_message(self) -> None:
        p1 = parse_line("G1 X.5 Y-.25 E.01 F1200", 1)
        assert p1.params == {"X": 0.5, "Y": -0.25, "E": 0.01, "F": 1200.0}

        p2 = parse_line("M70 P5 (Heating Nozzle)", 2)
        assert p2.command == "M70"
        assert p2.params == {"P": 5.0}
        assert p2.comment == "Heating Nozzle"


class TestGCodeParserErrors:
    def test_invalid_checksum_rejected(self) -> None:
        with pytest.raises(GCodeSyntaxError, match="checksum"):
            parse_line("N1 G1 X10*99", 5)

    def test_unclosed_parenthesis_rejected(self) -> None:
        with pytest.raises(GCodeSyntaxError, match="Unclosed"):
            parse_line("G1 X10 (unclosed", 6)

    def test_duplicate_parameter_rejected(self) -> None:
        with pytest.raises(GCodeSyntaxError, match="Duplicate"):
            parse_line("G1 X10 X20 F1200", 7)

    def test_malformed_token_rejected(self) -> None:
        with pytest.raises(GCodeSyntaxError):
            parse_line("G1 X10 @bad", 8)

    def test_missing_command_number_rejected(self) -> None:
        with pytest.raises(GCodeSyntaxError):
            parse_line("G X10", 9)

    def test_non_command_leading_letter_rejected(self) -> None:
        with pytest.raises(GCodeSyntaxError):
            parse_line("X10 Y20", 10)
