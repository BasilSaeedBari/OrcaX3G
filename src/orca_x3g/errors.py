"""Structured error hierarchy for OrcaX3G.

Every conversion or decoding failure carries exact source location (line number
or byte offset) and an actionable remediation hint so no unsupported or
out-of-range input is ever dropped silently.
"""

from __future__ import annotations


class OrcaX3GError(Exception):
    """Base exception for all OrcaX3G errors."""

    def __init__(
        self,
        message: str,
        *,
        line_number: int | None = None,
        raw_line: str | None = None,
        hint: str | None = None,
    ) -> None:
        self.base_message = message
        self.line_number = line_number
        self.raw_line = raw_line
        self.hint = hint
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        parts: list[str] = []
        if self.line_number is not None:
            parts.append(f"Line {self.line_number}: {self.base_message}")
        else:
            parts.append(self.base_message)
        if self.raw_line is not None:
            parts.append(f"  Source: {self.raw_line.strip()}")
        if self.hint:
            parts.append(f"  Hint: {self.hint}")
        return "\n".join(parts)


class GCodeSyntaxError(OrcaX3GError):
    """Raised when a G-code line is syntactically malformed."""


class UnsupportedCommandError(OrcaX3GError):
    """Raised when a G-code command or parameter is unsupported on Replicator 2 / X3G."""

    def __init__(
        self,
        message: str,
        *,
        command: str,
        line_number: int | None = None,
        raw_line: str | None = None,
        hint: str | None = None,
    ) -> None:
        self.command = command
        super().__init__(message, line_number=line_number, raw_line=raw_line, hint=hint)


class OutOfBoundsError(OrcaX3GError):
    """Raised when a motion target exceeds the Replicator 2 build volume."""

    def __init__(
        self,
        axis: str,
        value_mm: float,
        min_mm: float,
        max_mm: float,
        *,
        line_number: int | None = None,
        raw_line: str | None = None,
        hint: str | None = None,
    ) -> None:
        self.axis = axis
        self.value_mm = value_mm
        self.min_mm = min_mm
        self.max_mm = max_mm
        msg = (
            f"Axis {axis} target {value_mm:.4f} mm is outside build volume "
            f"[{min_mm:.3f}, {max_mm:.3f}] mm"
        )
        default_hint = (
            hint
            or "Check that your OrcaSlicer printer profile uses a centered origin "
            "(X in [-142.5, +142.5], Y in [-76.0, +76.0], Z in [0.0, 155.0])."
        )
        super().__init__(msg, line_number=line_number, raw_line=raw_line, hint=default_hint)


class ValueRangeError(OrcaX3GError):
    """Raised when a numeric parameter falls outside hardware or wire-format limits."""


class ProfileValidationError(OrcaX3GError):
    """Raised when OrcaSlicer export settings conflict with safe X3G generation."""


class X3GDecodeError(OrcaX3GError):
    """Raised by the independent X3G decoder on invalid framing, CRC, opcode, or truncation."""

    def __init__(
        self,
        message: str,
        *,
        byte_offset: int | None = None,
        hint: str | None = None,
    ) -> None:
        self.byte_offset = byte_offset
        prefix = f"Byte offset {byte_offset}: " if byte_offset is not None else ""
        super().__init__(f"{prefix}{message}", hint=hint)
