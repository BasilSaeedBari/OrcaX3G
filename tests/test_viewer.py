"""Tests for the interactive 3D X3G viewer, standalone HTML exporter, and x3gview CLI."""

from __future__ import annotations

import base64
from pathlib import Path

from orca_x3g.plugin import LAST_EXPORT_X3G_FILENAME
from orca_x3g.viewer import build_viewer_html, export_viewer_html, main as viewer_main

REAL_CUBE_X3G = (
    Path(__file__).parent
    / "fixtures"
    / "real_jobs"
    / "01_calibration_cube"
    / "01_calibration_cube.x3g"
)


def test_build_viewer_html_contains_webgl_decoder_and_orca_bridge() -> None:
    html = build_viewer_html()
    assert "--orca-bg" in html
    assert "window.orca" in html
    assert "decodeX3G" in html
    assert "glcanvas" in html
    assert "x3g_chunk" in html
    assert "88.888889" in html
    assert "88.573186" in html
    # Audit-safe cached binary filename
    for forbidden in ("conf", "secret", "cert"):
        assert forbidden not in LAST_EXPORT_X3G_FILENAME.lower()


def test_export_viewer_html_embeds_exact_x3g_payload(tmp_path: Path) -> None:
    out_html = tmp_path / "cube_3d_viewer.html"
    written = export_viewer_html(REAL_CUBE_X3G, out_html, profile_id="r2")
    assert written == out_html
    assert out_html.is_file()

    content = out_html.read_text(encoding="utf-8")
    expected_b64 = base64.b64encode(REAL_CUBE_X3G.read_bytes()).decode("ascii")
    assert expected_b64 in content
    assert '"01_calibration_cube.x3g"' in content


def test_x3gview_cli_no_open(tmp_path: Path) -> None:
    out_html = tmp_path / "cli_viewer.html"
    rc = viewer_main([str(REAL_CUBE_X3G), "--out", str(out_html), "--no-open"])
    assert rc == 0
    assert out_html.is_file() and out_html.stat().st_size > 100_000


def test_x3gview_cli_missing_file_returns_nonzero(tmp_path: Path) -> None:
    rc = viewer_main([str(tmp_path / "does_not_exist.x3g"), "--no-open"])
    assert rc == 2
