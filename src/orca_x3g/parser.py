"""Deterministic, strict G-code line parser for OrcaX3G.

Parses standard RepRap/Marlin and MakerBot G-code lines without silently
ignoring malformed tokens. Preserves comment text for M70 LCD messages and
layer-change detection.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Iterator

from .errors import GCodeSyntaxError

# Standard G-code word: single ASCII letter followed by optional signed float
_WORD_RE = re.compile(
    r"^([A-Za-z])([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)?$"
)

# Klipper / OrcaSlicer macro directives that are not standard G/M words
_IGNORABLE_DIRECTIVES = frozenset(
    {
        "EXCLUDE_OBJECT_DEFINE",
        "EXCLUDE_OBJECT_START",
        "EXCLUDE_OBJECT_END",
    }
)


@dataclass(frozen=True)
class ParsedGCodeLine:
    """Structured representation of one G-code source line."""

    line_number: int
    raw_line: str
    command: str | None  # Normalized e.g. "G1", "M104", "T0", or None if comment/blank
    params: dict[str, float | None]  # Parameter letter (uppercase) -> float or None (bare flag)
    comment: str  # Combined comment text from (...) and ;...
    directive: str | None = None  # Non-G/M ignorable directive (e.g. EXCLUDE_OBJECT_START)
    freeform_text: str = ""  # Freeform payload for M117

    @property
    def is_empty(self) -> bool:
        return self.command is None and self.directive is None


def _strip_comments(raw: str, line_number: int) -> tuple[str, str]:
    """Strip ';' line comments and '(...)' inline comments, returning (code, comment_text)."""
    code_chars: list[str] = []
    comment_parts: list[str] = []
    in_paren = False
    paren_buf: list[str] = []

    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if in_paren:
            if ch == ")":
                in_paren = False
                comment_parts.append("".join(paren_buf).strip())
                paren_buf.clear()
            elif ch == "(":
                raise GCodeSyntaxError(
                    "Nested '(' inside parenthetical comment",
                    line_number=line_number,
                    raw_line=raw,
                )
            else:
                paren_buf.append(ch)
        else:
            if ch == ";":
                comment_parts.append(raw[i + 1 :].strip())
                break
            if ch == "(":
                in_paren = True
            elif ch == ")":
                raise GCodeSyntaxError(
                    "Unmatched ')' in G-code line",
                    line_number=line_number,
                    raw_line=raw,
                )
            else:
                code_chars.append(ch)
        i += 1

    if in_paren:
        raise GCodeSyntaxError(
            "Unclosed '(' comment in G-code line",
            line_number=line_number,
            raw_line=raw,
        )

    code = "".join(code_chars).strip()
    comment = " ".join(p for p in comment_parts if p).strip()
    return code, comment


def _verify_and_strip_checksum(code: str, line_number: int, raw_line: str) -> str:
    """Validate optional RepRap '*<checksum>' suffix if present."""
    if "*" not in code:
        return code
    body, _, chk_str = code.partition("*")
    chk_str = chk_str.strip()
    if not chk_str.isdigit():
        raise GCodeSyntaxError(
            f"Invalid G-code checksum '*{chk_str}'",
            line_number=line_number,
            raw_line=raw_line,
        )
    expected = int(chk_str)
    actual = 0
    for b in body.encode("ascii", errors="replace"):
        actual ^= b
    if actual != expected:
        raise GCodeSyntaxError(
            f"G-code XOR checksum mismatch: expected *{expected}, computed *{actual}",
            line_number=line_number,
            raw_line=raw_line,
        )
    return body.strip()


def _tokenize_compact_words(code: str, line_number: int, raw_line: str) -> list[str]:
    """Split a G-code line into word tokens, handling both spaced and compact forms like G1X10Y20."""
    tokens: list[str] = []
    for chunk in code.split():
        # Fast path: single word token (vast majority of slicer output)
        if _WORD_RE.match(chunk):
            tokens.append(chunk)
            continue
        # Handle compact concatenated words like "G1X10Y20.5F1200"
        pos = 0
        length = len(chunk)
        while pos < length:
            if not chunk[pos].isalpha():
                raise GCodeSyntaxError(
                    f"Malformed G-code token {chunk!r}",
                    line_number=line_number,
                    raw_line=raw_line,
                )
            start = pos
            pos += 1
            # Consume signed float / scientific notation if present
            if pos < length and chunk[pos] in "+-":
                pos += 1
            has_digits = False
            while pos < length and chunk[pos].isdigit():
                has_digits = True
                pos += 1
            if pos < length and chunk[pos] == ".":
                pos += 1
                while pos < length and chunk[pos].isdigit():
                    has_digits = True
                    pos += 1
            # In compact concatenated words (e.g. "Z0.2E0.123" or "X10E5"), 'E' is the
            # extruder axis letter unless explicitly formatted as signed scientific notation
            # like "1.5e-05" or "1.5E+03" with no trailing decimal point.
            if has_digits and pos + 2 < length and chunk[pos] in "eE" and chunk[pos + 1] in "+-":
                exp_start = pos
                pos += 2
                exp_digits = False
                while pos < length and chunk[pos].isdigit():
                    exp_digits = True
                    pos += 1
                if not exp_digits or (pos < length and chunk[pos] == "."):
                    pos = exp_start
            sub = chunk[start:pos]
            if not _WORD_RE.match(sub):
                raise GCodeSyntaxError(
                    f"Malformed G-code word {sub!r} in token {chunk!r}",
                    line_number=line_number,
                    raw_line=raw_line,
                )
            tokens.append(sub)
    return tokens


def parse_gcode_line(raw_line: str, line_number: int) -> ParsedGCodeLine:
    """Parse a single line of G-code into a :class:`ParsedGCodeLine`."""
    stripped = raw_line.rstrip("\r\n")
    code, comment = _strip_comments(stripped, line_number)

    if not code:
        return ParsedGCodeLine(
            line_number=line_number,
            raw_line=stripped,
            command=None,
            params={},
            comment=comment,
        )

    code = _verify_and_strip_checksum(code, line_number, stripped)
    if not code:
        return ParsedGCodeLine(
            line_number=line_number,
            raw_line=stripped,
            command=None,
            params={},
            comment=comment,
        )

    # Check for OrcaSlicer / Klipper non-G/M macro directives (e.g., EXCLUDE_OBJECT_START)
    first_word = code.split(None, 1)[0].upper()
    if first_word in _IGNORABLE_DIRECTIVES:
        return ParsedGCodeLine(
            line_number=line_number,
            raw_line=stripped,
            command=None,
            params={},
            comment=comment,
            directive=first_word,
        )

    # M117 carries freeform status text (do not tokenize message words as G-code params)
    if first_word == "M117":
        rest = code[4:].strip()
        return ParsedGCodeLine(
            line_number=line_number,
            raw_line=stripped,
            command="M117",
            params={},
            comment=comment,
            freeform_text=rest,
        )

    tokens = _tokenize_compact_words(code, line_number, stripped)
    if not tokens:
        return ParsedGCodeLine(
            line_number=line_number,
            raw_line=stripped,
            command=None,
            params={},
            comment=comment,
        )

    # Strip optional leading line-number word N<int>
    idx = 0
    if tokens[0][0].upper() == "N":
        n_match = _WORD_RE.match(tokens[0])
        if n_match is None or n_match.group(2) is None:
            raise GCodeSyntaxError(
                f"Malformed line number word {tokens[0]!r}",
                line_number=line_number,
                raw_line=stripped,
            )
        idx = 1
        if idx >= len(tokens):
            return ParsedGCodeLine(
                line_number=line_number,
                raw_line=stripped,
                command=None,
                params={},
                comment=comment,
            )

    command: str | None = None
    params: dict[str, float | None] = {}

    for tok in tokens[idx:]:
        m = _WORD_RE.match(tok)
        if m is None:
            raise GCodeSyntaxError(
                f"Malformed G-code word {tok!r}",
                line_number=line_number,
                raw_line=stripped,
            )
        letter = m.group(1).upper()
        val_str = m.group(2)
        val: float | None = None
        if val_str is not None:
            try:
                val = float(val_str)
            except ValueError as exc:
                raise GCodeSyntaxError(
                    f"Invalid numeric value in word {tok!r}",
                    line_number=line_number,
                    raw_line=stripped,
                ) from exc
            if not math.isfinite(val):
                raise GCodeSyntaxError(
                    f"Non-finite numeric value in word {tok!r}",
                    line_number=line_number,
                    raw_line=stripped,
                )

        if command is None and letter in ("G", "M", "T"):
            if val is None or not val.is_integer() or val < 0:
                raise GCodeSyntaxError(
                    f"Command word {tok!r} must have a non-negative integer code",
                    line_number=line_number,
                    raw_line=stripped,
                )
            command = f"{letter}{int(val)}"
        else:
            if letter in params:
                raise GCodeSyntaxError(
                    f"Duplicate parameter '{letter}' on the same G-code line",
                    line_number=line_number,
                    raw_line=stripped,
                )
            # Treat a second G/M command on the same line as a syntax error rather than dropping it
            if command is not None and letter in ("G", "M"):
                raise GCodeSyntaxError(
                    f"Multiple commands on one line ('{command}' and '{tok}') are not supported",
                    line_number=line_number,
                    raw_line=stripped,
                    hint="Place each G/M command on its own line.",
                )
            params[letter] = val

    if command is None:
        raise GCodeSyntaxError(
            f"G-code line has parameters without a leading G/M/T command: {code!r}",
            line_number=line_number,
            raw_line=stripped,
            hint="OrcaX3G requires explicit G/M/T commands on every non-comment line.",
        )

    return ParsedGCodeLine(
        line_number=line_number,
        raw_line=stripped,
        command=command,
        params=params,
        comment=comment,
    )


def iter_parsed_lines(lines: Iterable[str]) -> Iterator[ParsedGCodeLine]:
    """Lazily parse an iterable of G-code lines with 1-based line numbers."""
    for idx, line in enumerate(lines, start=1):
        yield parse_gcode_line(line, idx)
