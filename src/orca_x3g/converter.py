"""High-level G-code -> X3G conversion orchestration.

Pure Python, UI-free, stdlib-only orchestration layer connecting:
``parser`` -> ``planner`` -> ``encoder`` -> optional atomic file write.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .commands import X3GCommand
from .encoder import encode_stream
from .planner import ConversionOptions, ExportSummary, GCodePlanner, sanitize_ascii_name


@dataclass(frozen=True)
class ConversionResult:
    """Output bundle returned by high-level conversion entry points."""

    x3g_bytes: bytes
    commands: tuple[X3GCommand, ...]
    summary: ExportSummary


def _check_sd_filename_warnings(output_path: Path, firmware: str, summary: ExportSummary) -> None:
    """Warn if output filename violates Sailfish (<=31 chars) or Stock FAT 8.3 limits."""
    name = output_path.name
    if len(name) > 31:
        summary.add_warning_once(
            f"Filename '{name}' is {len(name)} characters; Sailfish LCD SD menu truncates "
            "filenames over 31 characters."
        )
    if firmware.strip().lower() == "stock":
        stem = output_path.stem
        suffix = output_path.suffix.lstrip(".")
        is_83 = (
            1 <= len(stem) <= 8
            and len(suffix) <= 3
            and all(ch.isascii() and (ch.isalnum() or ch in "_-") for ch in stem)
            and all(ch.isascii() and ch.isalnum() for ch in suffix)
        )
        if not is_83:
            summary.add_warning_once(
                f"Filename '{name}' is not strict FAT 8.3; Stock MightyBoard firmware requires "
                "8.3 names (e.g. PART01.X3G) to display reliably on the SD card."
            )


def convert_lines(
    lines: Iterable[str],
    options: ConversionOptions | None = None,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> ConversionResult:
    """Convert an iterable of G-code lines into X3G bytes, command IR, and summary."""
    opts = options or ConversionOptions()
    planner = GCodePlanner(opts)
    commands, summary = planner.plan_lines(lines, cancel_check=cancel_check)
    x3g_bytes = encode_stream(commands, framed=opts.framed)
    summary.x3g_bytes = len(x3g_bytes)
    return ConversionResult(
        x3g_bytes=x3g_bytes,
        commands=tuple(commands),
        summary=summary,
    )


def convert_text(
    gcode_text: str,
    options: ConversionOptions | None = None,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> ConversionResult:
    """Convert a complete G-code string into X3G bytes, command IR, and summary."""
    return convert_lines(
        gcode_text.splitlines(),
        options=options,
        cancel_check=cancel_check,
    )


def _derive_options_with_build_name(
    options: ConversionOptions | None,
    source_path: Path,
    output_path: Path,
) -> ConversionOptions:
    base = options or ConversionOptions()
    if base.build_name != "OrcaX3G":
        return base
    derived = sanitize_ascii_name(
        output_path.stem or source_path.stem or "OrcaX3G",
        base.profile.build_name_max_len,
    )
    return ConversionOptions(
        profile=base.profile,
        firmware=base.firmware,
        build_name=derived,
        strict_bounds=base.strict_bounds,
        framed=base.framed,
        auto_build_notifications=base.auto_build_notifications,
    )


def convert_file(
    input_path: str | Path,
    output_path: str | Path,
    options: ConversionOptions | None = None,
    *,
    cancel_check: Callable[[], bool] | None = None,
    in_place: bool = False,
    warn_sd_filename: bool = True,
    display_output_name: str | None = None,
) -> ConversionResult:
    """Convert a G-code file on disk to an ``.x3g`` binary file.

    By default, writes atomically to a temporary file in ``output_path.parent``
    and replaces ``output_path`` so a failed conversion never leaves a corrupt
    ``.x3g`` on disk. When ``in_place=True`` (used by OrcaSlicer's post-process
    hook where the host tracks the exact temp file path), the input file is read
    completely first, converted in memory, and only overwritten after conversion
    succeeds without errors.
    """
    src = Path(input_path)
    dst = Path(output_path)
    name_for_build = Path(display_output_name) if display_output_name else dst
    opts = _derive_options_with_build_name(options, src, name_for_build)

    with src.open("r", encoding="utf-8", errors="replace") as fh:
        result = convert_lines(fh, options=opts, cancel_check=cancel_check)

    result.summary.source_path = str(src)
    result.summary.output_path = display_output_name or str(dst)

    if warn_sd_filename:
        _check_sd_filename_warnings(name_for_build, opts.firmware, result.summary)

    dst.parent.mkdir(parents=True, exist_ok=True)
    if in_place:
        with dst.open("wb") as out_fh:
            out_fh.write(result.x3g_bytes)
    else:
        fd, tmp_name = tempfile.mkstemp(prefix=".orca_x3g_", suffix=".tmp", dir=str(dst.parent))
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as out_fh:
                out_fh.write(result.x3g_bytes)
            os.replace(tmp_path, dst)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    return result
