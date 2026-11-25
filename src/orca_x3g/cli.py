"""Standalone command-line interface (``gcode2x3g``) for OrcaX3G.

Usable completely outside OrcaSlicer with zero external dependencies.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .converter import convert_file
from .errors import OrcaX3GError
from .machine_profile import get_profile
from .planner import ConversionOptions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gcode2x3g",
        description="Convert OrcaSlicer / Marlin G-code to MakerBot Replicator 2 X3G binary format.",
    )
    parser.add_argument("input_gcode", type=Path, help="Input .gcode file path")
    parser.add_argument("output_x3g", type=Path, help="Output .x3g file path")
    parser.add_argument(
        "--profile",
        default="r2",
        choices=("r2", "r2_factory", "or2"),
        help=(
            "Machine profile: 'r2' (88.888889 XY steps/mm, exact GT2 belt pitch, default) "
            "or 'r2_factory'/'or2' (88.573186 XY steps/mm, stock/Sailfish EEPROM default)."
        ),
    )
    parser.add_argument(
        "--firmware",
        default="sailfish",
        choices=("sailfish", "stock"),
        help="Target firmware compatibility ('sailfish' default; 'stock' rejects Sailfish-only M322).",
    )
    parser.add_argument(
        "--build-name",
        default=None,
        help="ASCII build name shown on the Replicator 2 LCD (max 24 chars; defaults to output stem).",
    )
    parser.add_argument(
        "--framed",
        action="store_true",
        help="Wrap commands in serial S3G wire framing ([0xD5][len][payload][CRC-8]). Default is raw SD .x3g.",
    )
    parser.add_argument(
        "--no-strict-bounds",
        action="store_true",
        help="Disable build-volume bounds rejection (not recommended for printing).",
    )
    parser.add_argument(
        "--no-auto-build-notices",
        action="store_true",
        help="Do not automatically wrap output with Command 153/154 if M136/M137 are absent.",
    )
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=None,
        help="Optional path to write the structured JSON export summary.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress human-readable export summary output on stdout.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        profile = get_profile(args.profile)
        opts = ConversionOptions(
            profile=profile,
            firmware=args.firmware,
            build_name=args.build_name if args.build_name is not None else "OrcaX3G",
            strict_bounds=not args.no_strict_bounds,
            framed=args.framed,
            auto_build_notifications=not args.no_auto_build_notices,
        )
        result = convert_file(
            args.input_gcode,
            args.output_x3g,
            options=opts,
        )
    except OrcaX3GError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: I/O failure: {exc}", file=sys.stderr)
        return 3

    if args.summary_json is not None:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(
            json.dumps(result.summary.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    if not args.quiet:
        print(result.summary.format_text())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
