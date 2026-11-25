"""Self-contained interactive 3D X3G viewer & time-playback simulator.

Provides:
- ``build_viewer_html(...)`` — Zero-dependency HTML5 + WebGL 1.0 (with Canvas 2D
  projection fallback) interactive 3D ``.x3g`` player that integrates directly
  into OrcaSlicer v2.5.2's ``X3G Export`` tab (``PagesPluginCapabilityBase``)
  and also runs standalone in any modern browser.
- ``export_viewer_html(x3g_path, out_html, ...)`` — Writes a self-contained
  ``.html`` viewer with the ``.x3g`` binary embedded.
- ``main()`` — Standalone CLI entry point ``x3gview path/to/file.x3g``.

Audit-safety (ADR-007): filename ``viewer.py`` contains no forbidden keywords.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import tempfile
import webbrowser
from pathlib import Path
from typing import Sequence

from .machine_profile import REPLICATOR_2, REPLICATOR_2_FACTORY

_VIEWER_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OrcaX3G — Interactive 3D X3G Viewer</title>
<style>
  /* Complete, self-consistent high-contrast themes (never mixes light text on dark surfaces).
     Note: references --orca-bg / --orca-accent only safely via JS luminance detection & accent. */
  :root, :root[data-theme="dark"] {
    color-scheme: dark;
    --bg: #15181e;
    --panel: #1e222b;
    --card-bg: #252b36;
    --input-bg: #2c3340;
    --input-hover: #363f4f;
    --fg: #f1f5f9;
    --muted: #b0bac9;
    --accent: var(--orca-accent, #00ae42);
    --accent-fg: #ffffff;
    --accent-dim: rgba(0, 174, 66, 0.24);
    --border: #3d4657;
    --warn-bg: rgba(245, 158, 11, 0.16);
    --warn-border: rgba(245, 158, 11, 0.55);
  }
  :root[data-theme="light"] {
    color-scheme: light;
    --bg: #eef1f5;
    --panel: #f8fafc;
    --card-bg: #ffffff;
    --input-bg: #ffffff;
    --input-hover: #f1f5f9;
    --fg: #111827;
    --muted: #4b5563;
    --accent: var(--orca-accent, #00993a);
    --accent-fg: #ffffff;
    --accent-dim: rgba(0, 153, 58, 0.14);
    --border: #cbd5e1;
    --warn-bg: rgba(245, 158, 11, 0.14);
    --warn-border: rgba(217, 119, 6, 0.55);
  }
  /* 3D canvas overlays always sit on the dark 3D viewport -> always explicit light-on-dark */
  .hud-tl, .legend, .layer-rail {
    --hud-bg: rgba(16, 19, 25, 0.90);
    --hud-fg: #f3f6fb;
    --hud-muted: #aeb8c7;
    --hud-border: #3b4454;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0; width: 100%; height: 100%;
    overflow: hidden;
    background: var(--bg);
    color: var(--fg);
    font-family: var(--orca-font, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif);
    font-size: 13px;
  }
  .app {
    display: grid;
    grid-template-rows: 48px 1fr 88px;
    grid-template-columns: 1fr 310px;
    height: 100vh;
    width: 100vw;
  }
  .topbar {
    grid-column: 1 / 3;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 0 12px;
    background: var(--panel);
    color: var(--fg);
    border-bottom: 1px solid var(--border);
    overflow-x: auto;
  }
  .brand {
    font-weight: 700;
    font-size: 14px;
    letter-spacing: 0.2px;
    color: var(--accent);
    margin-right: 6px;
    white-space: nowrap;
  }
  .sep { width: 1px; height: 22px; background: var(--border); margin: 0 2px; flex-shrink: 0; }
  .app button, .app select, .app label.btn {
    background-color: var(--input-bg);
    color: var(--fg);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 5px 10px;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    white-space: nowrap;
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
  .app select option, .app select optgroup {
    background-color: var(--panel);
    color: var(--fg);
  }
  .app button:hover:not(:disabled), .app select:hover, .app label.btn:hover {
    background-color: var(--input-hover);
    border-color: var(--accent);
    color: var(--fg);
  }
  .app button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .app button.primary, .app label.btn.primary {
    background-color: var(--accent);
    color: var(--accent-fg);
    border-color: var(--accent);
    font-weight: 600;
  }
  .app button.primary:hover:not(:disabled), .app label.btn.primary:hover {
    filter: brightness(1.08);
    background-color: var(--accent);
    color: var(--accent-fg);
  }
  .app button.active {
    background-color: var(--accent-dim);
    border-color: var(--accent);
    color: var(--fg);
  }
  #file-input { display: none; }
  .viewport-wrap {
    position: relative;
    overflow: hidden;
    background: radial-gradient(circle at 50% 45%, #1d222b 0%, #101217 100%);
  }
  canvas#glcanvas {
    width: 100%; height: 100%; display: block;
    cursor: grab;
  }
  canvas#glcanvas:active { cursor: grabbing; }
  .drop-overlay {
    position: absolute; inset: 0;
    background: rgba(0, 174, 66, 0.22);
    border: 2px dashed var(--accent);
    color: #ffffff;
    display: none;
    align-items: center;
    justify-content: center;
    font-size: 18px;
    font-weight: 600;
    pointer-events: none;
    z-index: 20;
  }
  .drop-overlay.show { display: flex; }
  .empty-state {
    position: absolute; inset: 0;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    text-align: center;
    padding: 24px;
    pointer-events: none;
    color: #c5cedb;
  }
  .empty-state h2 { color: #f8fafc; margin: 0 0 8px 0; font-size: 17px; }
  .empty-state p { max-width: 440px; margin: 4px 0; line-height: 1.5; }
  .empty-state strong { color: #ffffff; }
  .empty-state code {
    background: rgba(255, 255, 255, 0.12);
    color: #f8fafc;
    padding: 1px 5px;
    border-radius: 4px;
  }
  .hud-tl {
    position: absolute; top: 10px; left: 10px;
    background: var(--hud-bg);
    color: var(--hud-fg);
    border: 1px solid var(--hud-border);
    border-radius: 7px;
    padding: 8px 11px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11.5px;
    line-height: 1.55;
    pointer-events: none;
    backdrop-filter: blur(4px);
    min-width: 240px;
  }
  .hud-row { display: flex; justify-content: space-between; gap: 14px; }
  .hud-k { color: var(--hud-muted); }
  .hud-v { color: var(--hud-fg); font-weight: 600; }
  .hud-v.on { color: #38bdf8; }
  .hud-v.hot { color: #fb923c; }
  .legend {
    position: absolute; bottom: 10px; left: 10px;
    background: var(--hud-bg);
    color: var(--hud-fg);
    border: 1px solid var(--hud-border);
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 11px;
    pointer-events: none;
  }
  .legend-bar {
    width: 150px; height: 8px; border-radius: 4px; margin: 4px 0 2px 0;
    background: linear-gradient(90deg, #2563eb, #06b6d4, #22c55e, #eab308, #ef4444);
  }
  .legend-labels { display: flex; justify-content: space-between; color: var(--hud-muted); font-size: 10px; }
  .layer-rail {
    position: absolute; right: 12px; top: 14px; bottom: 14px;
    width: 46px;
    display: flex; flex-direction: column; align-items: center; gap: 6px;
    background: var(--hud-bg);
    color: var(--hud-fg);
    border: 1px solid var(--hud-border);
    border-radius: 8px;
    padding: 8px 4px;
  }
  .layer-rail span { font-size: 10.5px; color: var(--hud-fg); font-weight: 600; font-family: ui-monospace, monospace; }
  .layer-slider-wrap {
    flex: 1; display: flex; align-items: center; justify-content: center;
    width: 100%; overflow: hidden;
  }
  input[type="range"].vertical {
    writing-mode: vertical-lr;
    direction: rtl;
    height: 100%;
    width: 22px;
    accent-color: var(--accent);
    cursor: pointer;
  }
  .sidebar {
    background: var(--panel);
    color: var(--fg);
    border-left: 1px solid var(--border);
    display: flex; flex-direction: column;
    overflow: hidden;
  }
  .side-tabs {
    display: flex; border-bottom: 1px solid var(--border);
    background: var(--panel);
  }
  .side-tab {
    flex: 1; padding: 9px 6px; text-align: center;
    cursor: pointer; font-weight: 600; font-size: 12px;
    color: var(--muted); border-bottom: 2px solid transparent;
    background-color: transparent; border-radius: 0; border-top: 0; border-left: 0; border-right: 0;
  }
  .side-tab:hover { color: var(--fg); background-color: var(--input-hover); }
  .side-tab.active { color: var(--fg); border-bottom-color: var(--accent); background-color: transparent; }
  .side-body {
    flex: 1; overflow-y: auto; padding: 12px;
    background: var(--panel);
    color: var(--fg);
  }
  .section { margin-bottom: 14px; }
  .section h3 {
    margin: 0 0 8px 0; font-size: 11px; text-transform: uppercase;
    letter-spacing: 0.7px; color: var(--muted);
  }
  .kv-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 6px;
  }
  .kv-card {
    background: var(--card-bg);
    color: var(--fg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 7px 8px;
  }
  .kv-label { font-size: 10.5px; color: var(--muted); }
  .kv-val { font-size: 12.5px; font-weight: 600; color: var(--fg); margin-top: 2px; word-break: break-all; }
  .check-row {
    display: flex; align-items: center; justify-content: space-between;
    padding: 5px 0; font-size: 12.5px; color: var(--fg);
  }
  .check-row label { color: var(--fg); cursor: pointer; }
  .check-row input[type="checkbox"] { accent-color: var(--accent); cursor: pointer; width: 15px; height: 15px; }
  .event-list {
    display: flex; flex-direction: column; gap: 4px;
  }
  .event-item {
    background: var(--card-bg);
    color: var(--fg);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 6px 8px;
    cursor: pointer;
    font-size: 11.5px;
    display: flex; justify-content: space-between; gap: 6px;
  }
  .event-item:hover { border-color: var(--accent); background: var(--input-hover); }
  .event-time { color: var(--muted); font-family: ui-monospace, monospace; flex-shrink: 0; }
  .warn-box {
    background: var(--warn-bg);
    color: var(--fg);
    border: 1px solid var(--warn-border);
    border-radius: 6px;
    padding: 8px;
    font-size: 11.5px;
    line-height: 1.45;
  }
  .warn-box ul { margin: 4px 0 0 16px; padding: 0; }
  .controls {
    grid-column: 1 / 3;
    background: var(--panel);
    color: var(--fg);
    border-top: 1px solid var(--border);
    padding: 8px 14px;
    display: flex; flex-direction: column; justify-content: center; gap: 7px;
  }
  .scrubber-row {
    display: flex; align-items: center; gap: 10px;
  }
  .scrubber-row input[type="range"] {
    flex: 1; accent-color: var(--accent); cursor: pointer;
  }
  .time-label {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px; font-weight: 600; color: var(--fg); min-width: 145px; text-align: right;
  }
  .play-row {
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  }
  .status-pill {
    margin-left: auto; font-size: 11.5px; color: var(--muted);
    font-family: ui-monospace, monospace;
  }
</style>
</head>
<body>
<div class="app">
  <div class="topbar">
    <span class="brand">OrcaX3G 3D Viewer</span>
    <label class="btn primary" for="file-input">Open .x3g File...</label>
    <input id="file-input" type="file" accept=".x3g,.s3g,.bin">
    <button id="btn-load-last" type="button" title="Load the most recently exported .x3g from OrcaSlicer">Load Last Export</button>
    <div class="sep"></div>
    <select id="sel-profile" title="Machine steps/mm profile used to decode X3G steps to mm">
      <option value="r2">Replicator 2 (r2: 88.8889 steps/mm)</option>
      <option value="r2_factory">Replicator 2 Factory (or2: 88.5732 steps/mm)</option>
    </select>
    <select id="sel-color" title="Toolpath color mode">
      <option value="speed">Color: Print Speed (mm/s)</option>
      <option value="layer">Color: Layer Height (Z)</option>
      <option value="type">Color: Move Type</option>
    </select>
    <div class="sep"></div>
    <button id="cam-iso" type="button">Iso</button>
    <button id="cam-top" type="button">Top</button>
    <button id="cam-front" type="button">Front</button>
    <button id="cam-side" type="button">Side</button>
    <button id="cam-fit" type="button">Fit</button>
    <div class="sep"></div>
    <button id="btn-theme" type="button" title="Toggle Dark / Light UI theme">Theme: Dark</button>
  </div>

  <div class="viewport-wrap" id="viewport-wrap">
    <canvas id="glcanvas"></canvas>
    <div class="drop-overlay" id="drop-overlay">Drop .x3g file to simulate &amp; inspect</div>
    <div class="empty-state" id="empty-state">
      <h2>Interactive 3D X3G Toolpath Simulator</h2>
      <p>Export an <strong>.x3g</strong> file from OrcaSlicer and click <strong>Load Last Export</strong>, or click <strong>Open .x3g File...</strong> / drag-and-drop any <code>.x3g</code> file here.</p>
      <p style="font-size:11.5px">Decodes raw MightyBoard X3G binary packets (Commands 129–158) directly into timed 3D motion, speed, temperature, and fan telemetry.</p>
    </div>
    <div class="hud-tl" id="hud" style="display:none">
      <div class="hud-row"><span class="hud-k">Time:</span><span class="hud-v" id="hud-time">00:00 / 00:00</span></div>
      <div class="hud-row"><span class="hud-k">Layer / Z:</span><span class="hud-v" id="hud-layer">0 / 0 (0.00 mm)</span></div>
      <div class="hud-row"><span class="hud-k">Nozzle XYZ:</span><span class="hud-v" id="hud-xyz">0.00, 0.00, 0.00</span></div>
      <div class="hud-row"><span class="hud-k">Speed:</span><span class="hud-v" id="hud-speed">0.0 mm/s</span></div>
      <div class="hud-row"><span class="hud-k">Nozzle Temp:</span><span class="hud-v hot" id="hud-temp">0 °C</span></div>
      <div class="hud-row"><span class="hud-k">Part Fan (13):</span><span class="hud-v" id="hud-fan">OFF</span></div>
      <div class="hud-row"><span class="hud-k">Filament:</span><span class="hud-v" id="hud-fil">0.00 mm</span></div>
      <div class="hud-row"><span class="hud-k">X3G Packet:</span><span class="hud-v" id="hud-pkt">—</span></div>
    </div>
    <div class="legend" id="legend" style="display:none">
      <div id="legend-title">Print Speed (mm/s)</div>
      <div class="legend-bar" id="legend-bar"></div>
      <div class="legend-labels"><span id="leg-min">0</span><span id="leg-mid">25</span><span id="leg-max">50</span></div>
    </div>
    <div class="layer-rail" id="layer-rail" style="display:none">
      <span id="layer-top-lbl">L0</span>
      <div class="layer-slider-wrap">
        <input id="layer-slider" class="vertical" type="range" min="0" max="0" value="0">
      </div>
      <span>L1</span>
    </div>
  </div>

  <div class="sidebar">
    <div class="side-tabs">
      <button class="side-tab active" data-tab="info" type="button">Inspect</button>
      <button class="side-tab" data-tab="events" type="button">Events</button>
      <button class="side-tab" data-tab="summary" type="button">Export Log</button>
    </div>
    <div class="side-body" id="tab-info">
      <div class="section">
        <h3>Decoded X3G Metrics</h3>
        <div class="kv-grid">
          <div class="kv-card"><div class="kv-label">File / Build</div><div class="kv-val" id="m-name">—</div></div>
          <div class="kv-card"><div class="kv-label">X3G Size</div><div class="kv-val" id="m-size">—</div></div>
          <div class="kv-card"><div class="kv-label">Motion Packets</div><div class="kv-val" id="m-moves">—</div></div>
          <div class="kv-card"><div class="kv-label">Layers</div><div class="kv-val" id="m-layers">—</div></div>
          <div class="kv-card"><div class="kv-label">Max Print Speed</div><div class="kv-val" id="m-maxspd">—</div></div>
          <div class="kv-card"><div class="kv-label">Max Travel Speed</div><div class="kv-val" id="m-maxtrv">—</div></div>
          <div class="kv-card"><div class="kv-label">Total Filament</div><div class="kv-val" id="m-fil">—</div></div>
          <div class="kv-card"><div class="kv-label">Est. Duration</div><div class="kv-val" id="m-dur">—</div></div>
        </div>
      </div>
      <div class="section">
        <h3>Display Options</h3>
        <div class="check-row"><label for="chk-travel">Show travel moves</label><input id="chk-travel" type="checkbox" checked></div>
        <div class="check-row"><label for="chk-retract">Show retractions (markers)</label><input id="chk-retract" type="checkbox" checked></div>
        <div class="check-row"><label for="chk-single-layer">Single layer only</label><input id="chk-single-layer" type="checkbox"></div>
        <div class="check-row"><label for="chk-dim-lower">Dim completed lower layers</label><input id="chk-dim-lower" type="checkbox" checked></div>
        <div class="check-row"><label for="chk-bed">Show Replicator 2 build box</label><input id="chk-bed" type="checkbox" checked></div>
      </div>
      <div class="section">
        <h3>Bounds (mm)</h3>
        <div class="kv-card"><div class="kv-val" id="m-bounds" style="font-family:ui-monospace,monospace;font-size:11px">—</div></div>
      </div>
    </div>
    <div class="side-body" id="tab-events" style="display:none">
      <div class="section">
        <h3>Control Events (Click to Jump)</h3>
        <div class="event-list" id="event-list"><div style="color:var(--muted)">Load an .x3g file to inspect temperature, fan, homing, pause, and build events.</div></div>
      </div>
    </div>
    <div class="side-body" id="tab-summary" style="display:none">
      <div class="section" id="export-summary-panel">
        <div style="color:var(--muted)">No OrcaSlicer export summary loaded yet.</div>
      </div>
    </div>
  </div>

  <div class="controls">
    <div class="scrubber-row">
      <input id="time-slider" type="range" min="0" max="1000" value="1000" step="1" disabled>
      <span class="time-label" id="time-readout">00:00:00 / 00:00:00</span>
    </div>
    <div class="play-row">
      <button id="btn-play" class="primary" type="button" disabled>Play</button>
      <button id="btn-reset" type="button" disabled>Reset</button>
      <button id="btn-step-back" type="button" disabled>&larr; Move</button>
      <button id="btn-step-fwd" type="button" disabled>Move &rarr;</button>
      <select id="sel-speed" title="Playback speed multiplier">
        <option value="1">1&times; (Real-time)</option>
        <option value="5">5&times;</option>
        <option value="20" selected>20&times;</option>
        <option value="50">50&times;</option>
        <option value="200">200&times;</option>
        <option value="1000">1000&times;</option>
      </select>
      <span class="status-pill" id="status-pill">Ready</span>
    </div>
  </div>
</div>

<script>
// === Embedded Payload (non-empty for standalone x3gview HTML exports) ===
const EMBEDDED_X3G_B64 = __EMBEDDED_X3G_B64__;
const EMBEDDED_FILENAME = __EMBEDDED_FILENAME__;
const EMBEDDED_PROFILE = __EMBEDDED_PROFILE__;

// === Verified Replicator 2 Machine Profiles (docs/SOURCES.md) ===
const PROFILES = {
  r2: {
    id: "r2",
    spmX: __R2_SPM_X__, spmY: __R2_SPM_Y__, spmZ: __R2_SPM_Z__, spmA: __R2_SPM_A__,
    homeX: 152.0, homeY: 75.0, homeZ: 0.0,
    minX: -142.5, maxX: 142.5, minY: -76.0, maxY: 76.0, minZ: 0.0, maxZ: 155.0
  },
  r2_factory: {
    id: "r2_factory",
    spmX: __R2F_SPM_X__, spmY: __R2F_SPM_Y__, spmZ: __R2F_SPM_Z__, spmA: __R2F_SPM_A__,
    homeX: 152.0, homeY: 75.0, homeZ: 0.0,
    minX: -142.5, maxX: 142.5, minY: -76.0, maxY: 76.0, minZ: 0.0, maxZ: 155.0
  }
};

function roundHalfAway(v) { return v >= 0 ? Math.floor(v + 0.5) : -Math.floor(-v + 0.5); }

function fmtTime(sec) {
  if (!isFinite(sec) || sec < 0) sec = 0;
  const s = Math.floor(sec % 60);
  const m = Math.floor((sec / 60) % 60);
  const h = Math.floor(sec / 3600);
  return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
}

function b64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

// === Independent Spec-Driven X3G/S3G Binary Decoder & VM ===
function unframeIfS3G(bytes) {
  if (bytes.length >= 4 && bytes[0] === 0xD5) {
    // Validate first packet framing before treating as framed S3G
    const len = bytes[1];
    if (len >= 1 && len <= 32 && 2 + len + 1 <= bytes.length) {
      const payloads = [];
      let off = 0, totalLen = 0;
      let ok = true;
      while (off < bytes.length) {
        if (bytes[off] !== 0xD5 || off + 2 > bytes.length) { ok = false; break; }
        const plen = bytes[off + 1];
        if (plen < 1 || plen > 32 || off + 3 + plen > bytes.length) { ok = false; break; }
        const slice = bytes.subarray(off + 2, off + 2 + plen);
        payloads.push(slice);
        totalLen += plen;
        off += 3 + plen;
      }
      if (ok && payloads.length > 0) {
        const merged = new Uint8Array(totalLen);
        let pos = 0;
        for (const p of payloads) { merged.set(p, pos); pos += p.length; }
        return merged;
      }
    }
  }
  return bytes;
}

function readNulAscii(bytes, off, maxLen) {
  let end = off;
  const limit = Math.min(bytes.length, off + maxLen + 1);
  while (end < limit && bytes[end] !== 0) end++;
  let s = "";
  for (let i = off; i < end; i++) s += String.fromCharCode(bytes[i]);
  return { text: s, consumed: (end - off) + 1 };
}

function decodeX3G(rawBytes, profileId, fileName) {
  const prof = PROFILES[profileId] || PROFILES.r2;
  const bytes = unframeIfS3G(rawBytes);
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const total = bytes.length;

  let xS = 0, yS = 0, zS = 0, aS = 0, bS = 0, cumA = 0;
  let xKnown = false, yKnown = false, zKnown = false;
  let curTemp = 0, curFan = false, curTime = 0, buildName = fileName || "Untitled.x3g";

  // Dynamic arrays (packed into TypedArrays after decode for 60fps WebGL)
  const segX0 = [], segY0 = [], segZ0 = [];
  const segX1 = [], segY1 = [], segZ1 = [];
  const segSpeed = [], segType = []; // 0=extrude, 1=travel, 2=retract
  const segT0 = [], segT1 = [], segFil = [], segTemp = [], segFan = [], segPkt = [], segOp = [];
  const segLayer = [];
  const events = [];

  let pktIdx = 0, off = 0;
  let maxExtSpeed = 0, maxTrvSpeed = 0;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity, minZ = Infinity, maxZ = -Infinity;
  let lastExtZ = -999, curLayerIdx = 0;
  const layerZ = [];
  const layerEndSeg = [];

  function recordMotion(opcode, newX, newY, newZ, newA, newB, ddaRate, fMmS, durS, nowXKnown, nowYKnown, nowZKnown) {
    const prevXmm = xS / prof.spmX, prevYmm = yS / prof.spmY, prevZmm = zS / prof.spmZ;
    const dx = newX - xS, dy = newY - yS, dz = newZ - zS, da = newA - aS;
    const prevKnown = xKnown && yKnown && zKnown;

    xS = newX; yS = newY; zS = newZ; aS = newA; bS = newB;
    xKnown = nowXKnown; yKnown = nowYKnown; zKnown = nowZKnown;
    cumA += da;

    const x1 = xS / prof.spmX, y1 = yS / prof.spmY, z1 = zS / prof.spmZ;
    const deMm = -da / prof.spmA;
    const cumE = -cumA / prof.spmA;
    const t0 = curTime;
    const t1 = curTime + durS;
    curTime = t1;

    let mtype = 1; // travel
    if (deMm > 1e-6 && (dx !== 0 || dy !== 0 || dz !== 0)) {
      mtype = 0; // extrude
      if (fMmS > maxExtSpeed) maxExtSpeed = fMmS;
      if (x1 < minX) minX = x1; if (x1 > maxX) maxX = x1;
      if (y1 < minY) minY = y1; if (y1 > maxY) maxY = y1;
      if (z1 < minZ) minZ = z1; if (z1 > maxZ) maxZ = z1;
      if (Math.abs(z1 - lastExtZ) > 0.025) {
        if (z1 > lastExtZ + 0.025 || layerZ.length === 0) {
          curLayerIdx = layerZ.length;
          layerZ.push(z1);
          layerEndSeg.push(segX0.length);
          lastExtZ = z1;
        }
      }
    } else if (deMm < -1e-6) {
      mtype = 2; // retract
    } else {
      if (fMmS > maxTrvSpeed && (dx !== 0 || dy !== 0)) maxTrvSpeed = fMmS;
    }

    const x0 = prevKnown ? prevXmm : x1;
    const y0 = prevKnown ? prevYmm : y1;
    const z0 = prevKnown ? prevZmm : z1;

    segX0.push(x0); segY0.push(y0); segZ0.push(z0);
    segX1.push(x1); segY1.push(y1); segZ1.push(z1);
    segSpeed.push(fMmS);
    segType.push(mtype);
    segT0.push(t0); segT1.push(t1);
    segFil.push(cumE);
    segTemp.push(curTemp);
    segFan.push(curFan ? 1 : 0);
    segPkt.push(pktIdx);
    segOp.push(opcode);
    segLayer.push(curLayerIdx);
    if (layerEndSeg.length > 0) layerEndSeg[curLayerIdx] = segX0.length - 1;
  }

  while (off < total) {
    const op = bytes[off++];
    if (op === 155) { // QUEUE_POINT_NEW_EXT (32 B)
      if (off + 31 > total) break;
      const fx = dv.getInt32(off, true), fy = dv.getInt32(off + 4, true), fz = dv.getInt32(off + 8, true);
      const fa = dv.getInt32(off + 12, true), fb = dv.getInt32(off + 16, true);
      const dda = dv.getUint32(off + 20, true);
      const rel = bytes[off + 24];
      const f64 = dv.getInt16(off + 29, true);
      off += 31;

      const nx = (rel & 1) ? xS + fx : fx;
      const ny = (rel & 2) ? yS + fy : fy;
      const nz = (rel & 4) ? zS + fz : fz;
      const na = (rel & 8) ? aS + fa : fa;
      const nb = (rel & 16) ? bS + fb : fb;
      const master = Math.max(Math.abs(nx - xS), Math.abs(ny - yS), Math.abs(nz - zS), Math.abs(na - aS), Math.abs(nb - bS));
      const dur = (master > 0 && dda > 0) ? (master / dda) : 0;
      recordMotion(155, nx, ny, nz, na, nb, dda, f64 / 64.0, dur, xKnown || !(rel & 1), yKnown || !(rel & 2), zKnown || !(rel & 4));
    } else if (op === 139) { // QUEUE_POINT_EXT (25 B)
      if (off + 24 > total) break;
      const nx = dv.getInt32(off, true), ny = dv.getInt32(off + 4, true), nz = dv.getInt32(off + 8, true);
      const na = dv.getInt32(off + 12, true), nb = dv.getInt32(off + 16, true);
      const ddaUs = dv.getUint32(off + 20, true);
      off += 24;
      const master = Math.max(Math.abs(nx - xS), Math.abs(ny - yS), Math.abs(nz - zS), Math.abs(na - aS), Math.abs(nb - bS));
      const dur = (master * ddaUs) / 1e6;
      const dist = Math.hypot((nx - xS) / prof.spmX, (ny - yS) / prof.spmY, (nz - zS) / prof.spmZ);
      recordMotion(139, nx, ny, nz, na, nb, ddaUs > 0 ? 1e6 / ddaUs : 0, dur > 0 ? dist / dur : 0, dur, true, true, true);
    } else if (op === 142) { // QUEUE_POINT_NEW (26 B)
      if (off + 25 > total) break;
      const fx = dv.getInt32(off, true), fy = dv.getInt32(off + 4, true), fz = dv.getInt32(off + 8, true);
      const fa = dv.getInt32(off + 12, true), fb = dv.getInt32(off + 16, true);
      const durUs = dv.getUint32(off + 20, true);
      const rel = bytes[off + 24];
      off += 25;
      const nx = (rel & 1) ? xS + fx : fx, ny = (rel & 2) ? yS + fy : fy, nz = (rel & 4) ? zS + fz : fz;
      const na = (rel & 8) ? aS + fa : fa, nb = (rel & 16) ? bS + fb : fb;
      const dur = durUs / 1e6;
      const dist = Math.hypot((nx - xS) / prof.spmX, (ny - yS) / prof.spmY, (nz - zS) / prof.spmZ);
      recordMotion(142, nx, ny, nz, na, nb, 0, dur > 0 ? dist / dur : 0, dur, xKnown || !(rel & 1), yKnown || !(rel & 2), zKnown || !(rel & 4));
    } else if (op === 129) { // QUEUE_POINT_ABS (17 B)
      if (off + 16 > total) break;
      const nx = dv.getInt32(off, true), ny = dv.getInt32(off + 4, true), nz = dv.getInt32(off + 8, true);
      const ddaUs = dv.getUint32(off + 12, true);
      off += 16;
      const master = Math.max(Math.abs(nx - xS), Math.abs(ny - yS), Math.abs(nz - zS));
      const dur = (master * ddaUs) / 1e6;
      recordMotion(129, nx, ny, nz, aS, bS, 0, 0, dur, true, true, true);
    } else if (op === 131 || op === 132) {
      if (off + 7 > total) break;
      const mask = bytes[off]; off += 7;
      if (mask & 1) xKnown = false; if (mask & 2) yKnown = false; if (mask & 4) zKnown = false;
      const axes = ["X","Y","Z","A","B"].filter((_, i) => mask & (1 << i)).join("");
      events.push({ time: curTime, seg: segX0.length, label: (op === 131 ? "Home MIN " : "Home MAX ") + axes, op });
    } else if (op === 133) {
      if (off + 4 > total) break;
      const ms = dv.getUint32(off, true); off += 4;
      curTime += ms / 1000.0;
      events.push({ time: curTime, seg: segX0.length, label: "Dwell " + ms + " ms", op });
    } else if (op === 134) {
      if (off + 1 > total) break;
      const tid = bytes[off++];
      events.push({ time: curTime, seg: segX0.length, label: "Select Tool T" + tid, op });
    } else if (op === 135 || op === 141) {
      if (off + 5 > total) break;
      const tid = bytes[off]; off += 5;
      events.push({ time: curTime, seg: segX0.length, label: (op === 135 ? "Wait Tool T" : "Wait Bed ") + tid, op });
    } else if (op === 136) {
      if (off + 3 > total) break;
      const tid = bytes[off], action = bytes[off + 1], slen = bytes[off + 2];
      off += 3;
      if (off + slen > total) break;
      if (action === 3 && slen === 2) {
        curTemp = dv.getInt16(off, true);
        events.push({ time: curTime, seg: segX0.length, label: "Nozzle Temp " + curTemp + " °C (T" + tid + ")", op });
      } else if ((action === 13 || action === 12) && slen === 1) {
        curFan = !!(bytes[off] & 1);
        events.push({ time: curTime, seg: segX0.length, label: "Fan " + (curFan ? "ON" : "OFF") + " (Act " + action + ")", op });
      } else if (action === 31 && slen === 2) {
        const bt = dv.getInt16(off, true);
        events.push({ time: curTime, seg: segX0.length, label: "Bed Temp " + bt + " °C", op });
      }
      off += slen;
    } else if (op === 137) {
      if (off + 1 > total) break;
      const bf = bytes[off++];
      events.push({ time: curTime, seg: segX0.length, label: (bf & 0x80) ? "Enable Steppers" : "Disable Steppers", op });
    } else if (op === 140) {
      if (off + 20 > total) break;
      xS = dv.getInt32(off, true); yS = dv.getInt32(off + 4, true); zS = dv.getInt32(off + 8, true);
      aS = dv.getInt32(off + 12, true); bS = dv.getInt32(off + 16, true);
      xKnown = yKnown = zKnown = true;
      off += 20;
    } else if (op === 143) {
      if (off + 1 > total) break; off += 1;
    } else if (op === 144) {
      if (off + 1 > total) break;
      const mask = bytes[off++];
      if (mask & 1) { xS = roundHalfAway(prof.homeX * prof.spmX); xKnown = true; }
      if (mask & 2) { yS = roundHalfAway(prof.homeY * prof.spmY); yKnown = true; }
      if (mask & 4) { zS = roundHalfAway(prof.homeZ * prof.spmZ); zKnown = true; }
      events.push({ time: curTime, seg: segX0.length, label: "Recall Home Offsets (M132)", op });
    } else if (op === 145) {
      if (off + 2 > total) break; off += 2;
    } else if (op === 146 || op === 147) {
      if (off + 5 > total) break; off += 5;
    } else if (op === 148) {
      if (off + 4 > total) break; off += 4;
      events.push({ time: curTime, seg: segX0.length, label: "Pause For Button", op });
    } else if (op === 149) {
      if (off + 4 > total) break; off += 4;
      const r = readNulAscii(bytes, off, 26); off += r.consumed;
      events.push({ time: curTime, seg: segX0.length, label: "LCD: \"" + r.text + "\"", op });
    } else if (op === 150) {
      if (off + 2 > total) break; off += 2;
    } else if (op === 151) {
      if (off + 1 > total) break;
      const song = bytes[off++];
      events.push({ time: curTime, seg: segX0.length, label: "Play Song #" + song, op });
    } else if (op === 152) {
      // 1 byte total
    } else if (op === 153) {
      if (off + 4 > total) break; off += 4;
      const r = readNulAscii(bytes, off, 26); off += r.consumed;
      if (r.text) buildName = r.text;
      events.push({ time: curTime, seg: segX0.length, label: "Build Start: \"" + r.text + "\"", op });
    } else if (op === 154) {
      if (off + 1 > total) break; off += 1;
      events.push({ time: curTime, seg: segX0.length, label: "Build End", op });
    } else if (op === 156) {
      if (off + 1 > total) break;
      const en = !!bytes[off++];
      events.push({ time: curTime, seg: segX0.length, label: "Acceleration " + (en ? "ON" : "OFF"), op });
    } else if (op === 157) {
      if (off + 20 > total) break; off += 20;
    } else if (op === 158) {
      if (off + 4 > total) break;
      const pz = dv.getFloat32(off, true); off += 4;
      events.push({ time: curTime, seg: segX0.length, label: "Pause at Z=" + pz.toFixed(2) + " mm", op });
    } else {
      throw new Error("Unknown X3G opcode " + op + " at byte offset " + (off - 1));
    }
    pktIdx++;
  }

  const count = segX0.length;
  if (!isFinite(minX)) { minX = -20; maxX = 20; minY = -20; maxY = 20; minZ = 0; maxZ = 20; }
  return {
    profile: prof,
    fileName: fileName || buildName,
    buildName,
    byteLength: rawBytes.length,
    packetCount: pktIdx,
    count,
    x0: new Float32Array(segX0), y0: new Float32Array(segY0), z0: new Float32Array(segZ0),
    x1: new Float32Array(segX1), y1: new Float32Array(segY1), z1: new Float32Array(segZ1),
    speed: new Float32Array(segSpeed),
    type: new Uint8Array(segType),
    t0: new Float32Array(segT0), t1: new Float32Array(segT1),
    fil: new Float32Array(segFil),
    temp: new Int16Array(segTemp),
    fan: new Uint8Array(segFan),
    pkt: new Int32Array(segPkt),
    op: new Uint8Array(segOp),
    layer: new Int32Array(segLayer),
    layerZ, layerEndSeg,
    totalTime: curTime,
    totalFilament: -cumA / prof.spmA,
    maxExtSpeed: maxExtSpeed || 50,
    maxTrvSpeed: maxTrvSpeed || 80,
    bounds: { minX, maxX, minY, maxY, minZ, maxZ },
    events
  };
}

// === Color Mapping ===
function heatColor(t) {
  t = Math.max(0, Math.min(1, t));
  let r, g, b;
  if (t < 0.25) { const u = t / 0.25; r = 0.15 * (1 - u) + 0.02 * u; g = 0.39 * (1 - u) + 0.71 * u; b = 0.92 * (1 - u) + 0.83 * u; }
  else if (t < 0.5) { const u = (t - 0.25) / 0.25; r = 0.02 * (1 - u) + 0.13 * u; g = 0.71 * (1 - u) + 0.77 * u; b = 0.83 * (1 - u) + 0.37 * u; }
  else if (t < 0.75) { const u = (t - 0.5) / 0.25; r = 0.13 * (1 - u) + 0.92 * u; g = 0.77 * (1 - u) + 0.70 * u; b = 0.37 * (1 - u) + 0.03 * u; }
  else { const u = (t - 0.75) / 0.25; r = 0.92 * (1 - u) + 0.94 * u; g = 0.70 * (1 - u) + 0.27 * u; b = 0.03 * (1 - u) + 0.27 * u; }
  return [r, g, b];
}

// === Minimal 4x4 Matrix Math (Column-Major for WebGL) ===
function mat4Mul(a, b) {
  const o = new Float32Array(16);
  for (let c = 0; c < 4; c++) {
    for (let r = 0; r < 4; r++) {
      o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    }
  }
  return o;
}
function mat4Perspective(fovRad, aspect, near, far) {
  const f = 1.0 / Math.tan(fovRad / 2), nf = 1 / (near - far);
  const o = new Float32Array(16);
  o[0] = f / aspect; o[5] = f; o[10] = (far + near) * nf; o[11] = -1; o[14] = 2 * far * near * nf;
  return o;
}
function mat4LookAt(eye, target, up) {
  let zx = eye[0] - target[0], zy = eye[1] - target[1], zz = eye[2] - target[2];
  let zl = Math.hypot(zx, zy, zz) || 1; zx /= zl; zy /= zl; zz /= zl;
  let xx = up[1] * zz - up[2] * zy, xy = up[2] * zx - up[0] * zz, xz = up[0] * zy - up[1] * zx;
  let xl = Math.hypot(xx, xy, xz) || 1; xx /= xl; xy /= xl; xz /= xl;
  const yx = zy * xz - zz * xy, yy = zz * xx - zx * xz, yz = zx * xy - zy * xx;
  const o = new Float32Array(16);
  o[0] = xx; o[1] = yx; o[2] = zx; o[3] = 0;
  o[4] = xy; o[5] = yy; o[6] = zy; o[7] = 0;
  o[8] = xz; o[9] = yz; o[10] = zz; o[11] = 0;
  o[12] = -(xx * eye[0] + xy * eye[1] + xz * eye[2]);
  o[13] = -(yx * eye[0] + yy * eye[1] + yz * eye[2]);
  o[14] = -(zx * eye[0] + zy * eye[1] + zz * eye[2]);
  o[15] = 1;
  return o;
}

// === Viewer State ===
const state = {
  rawBytes: null,
  fileName: "",
  model: null,
  currentTime: 0,
  playing: false,
  playSpeed: 20,
  lastFrameMs: 0,
  maxLayer: 0,
  colorMode: "speed",
  showTravel: true,
  showRetract: true,
  singleLayer: false,
  dimLower: true,
  showBed: true,
  cam: { yaw: 0.72, pitch: 0.58, dist: 240, tx: 0, ty: 0, tz: 15 },
  gl: null,
  ctx2d: null,
  prog: null,
  bedBuf: null, bedCount: 0,
  pathBuf: null, pathColorBuf: null, pathSegCount: 0,
  retractBuf: null, retractCount: 0,
  nozzleBuf: null,
  activeSegIdx: 0,
  nozzlePos: [0, 0, 0]
};

const canvas = document.getElementById("glcanvas");

function initRenderer() {
  const gl = canvas.getContext("webgl", { antialias: true, alpha: false });
  if (gl) {
    state.gl = gl;
    const vsSrc = `
      attribute vec3 aPos;
      attribute vec4 aCol;
      uniform mat4 uMvp;
      uniform float uPointSize;
      varying vec4 vCol;
      void main() {
        gl_Position = uMvp * vec4(aPos, 1.0);
        gl_PointSize = uPointSize;
        vCol = aCol;
      }`;
    const fsSrc = `
      precision mediump float;
      varying vec4 vCol;
      void main() {
        if (vCol.a < 0.01) discard;
        gl_FragColor = vCol;
      }`;
    function compile(type, src) {
      const sh = gl.createShader(type);
      gl.shaderSource(sh, src);
      gl.compileShader(sh);
      return sh;
    }
    const prog = gl.createProgram();
    gl.attachShader(prog, compile(gl.VERTEX_SHADER, vsSrc));
    gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, fsSrc));
    gl.linkProgram(prog);
    state.prog = prog;
    state.aPos = gl.getAttribLocation(prog, "aPos");
    state.aCol = gl.getAttribLocation(prog, "aCol");
    state.uMvp = gl.getUniformLocation(prog, "uMvp");
    state.uPointSize = gl.getUniformLocation(prog, "uPointSize");
    state.bedBuf = gl.createBuffer();
    state.bedColBuf = gl.createBuffer();
    state.pathBuf = gl.createBuffer();
    state.pathColorBuf = gl.createBuffer();
    state.retractBuf = gl.createBuffer();
    state.retractColBuf = gl.createBuffer();
    state.nozzleBuf = gl.createBuffer();
    state.nozzleColBuf = gl.createBuffer();
    buildBedGeometry();
    gl.enable(gl.DEPTH_TEST);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  } else {
    state.ctx2d = canvas.getContext("2d");
  }
}

function buildBedGeometry() {
  const p = PROFILES.r2;
  const verts = [], cols = [];
  function pushLine(x0, y0, z0, x1, y1, z1, r, g, b, a) {
    verts.push(x0, y0, z0, x1, y1, z1);
    cols.push(r, g, b, a, r, g, b, a);
  }
  // 10mm grid on Replicator 2 plate [-142.5..142.5] x [-76..76]
  for (let x = -140; x <= 140; x += 10) {
    const major = (x % 50 === 0);
    const c = major ? 0.30 : 0.18;
    pushLine(x, p.minY, 0, x, p.maxY, 0, c, c, c + 0.04, 0.85);
  }
  for (let y = -70; y <= 70; y += 10) {
    const major = (y % 50 === 0);
    const c = major ? 0.30 : 0.18;
    pushLine(p.minX, y, 0, p.maxX, y, 0, c, c, c + 0.04, 0.85);
  }
  // Outer build plate border + front highlight
  pushLine(p.minX, p.minY, 0, p.maxX, p.minY, 0, 0.0, 0.72, 0.35, 0.95); // front edge
  pushLine(p.maxX, p.minY, 0, p.maxX, p.maxY, 0, 0.42, 0.46, 0.55, 0.9);
  pushLine(p.maxX, p.maxY, 0, p.minX, p.maxY, 0, 0.42, 0.46, 0.55, 0.9);
  pushLine(p.minX, p.maxY, 0, p.minX, p.minY, 0, 0.42, 0.46, 0.55, 0.9);
  // Build volume vertical corners & top frame (Z = 155 mm)
  const corners = [[p.minX,p.minY],[p.maxX,p.minY],[p.maxX,p.maxY],[p.minX,p.maxY]];
  for (const [cx, cy] of corners) pushLine(cx, cy, 0, cx, cy, p.maxZ, 0.25, 0.29, 0.36, 0.5);
  for (let i = 0; i < 4; i++) {
    const [x0, y0] = corners[i], [x1, y1] = corners[(i + 1) % 4];
    pushLine(x0, y0, p.maxZ, x1, y1, p.maxZ, 0.25, 0.29, 0.36, 0.5);
  }
  // Center origin axes (X red, Y green, Z blue)
  pushLine(0, 0, 0.05, 25, 0, 0.05, 0.95, 0.25, 0.25, 1.0);
  pushLine(0, 0, 0.05, 0, 25, 0.05, 0.20, 0.90, 0.35, 1.0);
  pushLine(0, 0, 0.05, 0, 0, 25, 0.25, 0.55, 1.0, 1.0);

  state.bedVerts = new Float32Array(verts);
  state.bedCols = new Float32Array(cols);
  state.bedCount = verts.length / 3;
  if (state.gl) {
    const gl = state.gl;
    gl.bindBuffer(gl.ARRAY_BUFFER, state.bedBuf);
    gl.bufferData(gl.ARRAY_BUFFER, state.bedVerts, gl.STATIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, state.bedColBuf);
    gl.bufferData(gl.ARRAY_BUFFER, state.bedCols, gl.STATIC_DRAW);
  }
}

function uploadToolpathGeometry() {
  const m = state.model;
  if (!m || m.count === 0) return;

  const verts = new Float32Array(m.count * 6);
  const retVerts = [];
  const retCols = [];
  const retSegs = [];
  for (let i = 0; i < m.count; i++) {
    const o = i * 6;
    verts[o] = m.x0[i]; verts[o + 1] = m.y0[i]; verts[o + 2] = m.z0[i];
    verts[o + 3] = m.x1[i]; verts[o + 4] = m.y1[i]; verts[o + 5] = m.z1[i];
    if (m.type[i] === 2) {
      retVerts.push(m.x1[i], m.y1[i], m.z1[i]);
      retCols.push(0.95, 0.25, 0.85, 0.95);
      retSegs.push(i);
    }
  }
  state.pathVerts = verts;
  state.pathSegCount = m.count;
  state.retractVerts = new Float32Array(retVerts);
  state.retractCols = new Float32Array(retCols);
  state.retractSegs = new Int32Array(retSegs);
  state.retractCount = retSegs.length;

  if (state.gl) {
    const gl = state.gl;
    gl.bindBuffer(gl.ARRAY_BUFFER, state.pathBuf);
    gl.bufferData(gl.ARRAY_BUFFER, verts, gl.STATIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, state.retractBuf);
    gl.bufferData(gl.ARRAY_BUFFER, state.retractVerts, gl.STATIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, state.retractColBuf);
    gl.bufferData(gl.ARRAY_BUFFER, state.retractCols, gl.STATIC_DRAW);
  }
  rebuildToolpathColors();
}

function rebuildToolpathColors() {
  const m = state.model;
  if (!m || m.count === 0) return;
  const cols = new Float32Array(m.count * 8);
  const maxSpd = Math.max(10, m.maxExtSpeed);
  const zSpan = Math.max(0.2, m.bounds.maxZ - m.bounds.minZ);
  const activeLayer = m.layer[Math.min(state.activeSegIdx, m.count - 1)] || 0;

  for (let i = 0; i < m.count; i++) {
    const tp = m.type[i];
    const lay = m.layer[i];
    let r = 0.2, g = 0.8, b = 0.4, a = 1.0;

    if (state.singleLayer && lay !== state.maxLayer) {
      a = 0.0;
    } else if (lay > state.maxLayer) {
      a = 0.0;
    } else if (tp === 1) { // travel
      r = 0.22; g = 0.56; b = 0.95; a = state.showTravel ? 0.28 : 0.0;
    } else if (tp === 2) { // retract
      r = 0.95; g = 0.25; b = 0.85; a = state.showRetract ? 0.85 : 0.0;
    } else {
      if (state.colorMode === "speed") {
        [r, g, b] = heatColor(m.speed[i] / maxSpd);
      } else if (state.colorMode === "layer") {
        [r, g, b] = heatColor((m.z1[i] - m.bounds.minZ) / zSpan);
      } else {
        r = 0.98; g = 0.55; b = 0.14;
      }
      if (state.dimLower && lay < activeLayer && !state.singleLayer) {
        r *= 0.52; g *= 0.52; b *= 0.52; a = 0.72;
      }
    }

    const o = i * 8;
    cols[o] = r; cols[o + 1] = g; cols[o + 2] = b; cols[o + 3] = a;
    cols[o + 4] = r; cols[o + 5] = g; cols[o + 6] = b; cols[o + 7] = a;
  }

  state.pathCols = cols;
  if (state.gl) {
    const gl = state.gl;
    gl.bindBuffer(gl.ARRAY_BUFFER, state.pathColorBuf);
    gl.bufferData(gl.ARRAY_BUFFER, cols, gl.DYNAMIC_DRAW);
  }
  updateLegend();
}

function updateLegend() {
  const leg = document.getElementById("legend");
  const m = state.model;
  if (!m) { leg.style.display = "none"; return; }
  leg.style.display = "block";
  const title = document.getElementById("legend-title");
  const bar = document.getElementById("legend-bar");
  const lmin = document.getElementById("leg-min");
  const lmid = document.getElementById("leg-mid");
  const lmax = document.getElementById("leg-max");
  if (state.colorMode === "speed") {
    title.textContent = "Extrusion Speed (mm/s)";
    bar.style.display = "block";
    lmin.textContent = "0";
    lmid.textContent = (m.maxExtSpeed * 0.5).toFixed(0);
    lmax.textContent = m.maxExtSpeed.toFixed(1);
  } else if (state.colorMode === "layer") {
    title.textContent = "Layer Height Z (mm)";
    bar.style.display = "block";
    lmin.textContent = m.bounds.minZ.toFixed(1);
    lmid.textContent = ((m.bounds.minZ + m.bounds.maxZ) * 0.5).toFixed(1);
    lmax.textContent = m.bounds.maxZ.toFixed(1);
  } else {
    title.textContent = "Orange=Extrude · Blue=Travel · Pink=Retract";
    bar.style.display = "none";
    lmin.textContent = ""; lmid.textContent = ""; lmax.textContent = "";
  }
}

function findSegmentAtTime(t) {
  const m = state.model;
  if (!m || m.count === 0) return 0;
  let lo = 0, hi = m.count - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (m.t1[mid] < t) lo = mid + 1; else hi = mid;
  }
  return lo;
}

function computeMvp() {
  const w = canvas.clientWidth || 800, h = canvas.clientHeight || 600;
  const c = state.cam;
  const cp = Math.cos(c.pitch), sp = Math.sin(c.pitch);
  const eye = [
    c.tx + c.dist * cp * Math.cos(c.yaw),
    c.ty + c.dist * cp * Math.sin(c.yaw),
    c.tz + c.dist * sp
  ];
  const proj = mat4Perspective(42 * Math.PI / 180, w / Math.max(1, h), 0.5, 4000);
  const view = mat4LookAt(eye, [c.tx, c.ty, c.tz], [0, 0, 1]);
  return mat4Mul(proj, view);
}

function renderScene() {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.floor((canvas.clientWidth || 800) * dpr);
  const h = Math.floor((canvas.clientHeight || 600) * dpr);
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }

  const mvp = computeMvp();
  const m = state.model;
  let drawSegs = 0;
  let nx = 0, ny = 0, nz = 0;

  if (m && m.count > 0) {
    const idx = findSegmentAtTime(state.currentTime);
    const prevLayer = m.layer[Math.min(state.activeSegIdx, m.count - 1)] || 0;
    state.activeSegIdx = idx;
    const newLayer = m.layer[idx] || 0;
    if (!state.singleLayer && newLayer > state.maxLayer) {
      state.maxLayer = newLayer;
      document.getElementById("layer-slider").value = state.maxLayer;
      document.getElementById("layer-top-lbl").textContent = "L" + (state.maxLayer + 1);
      rebuildToolpathColors();
    } else if (state.dimLower && newLayer !== prevLayer) {
      rebuildToolpathColors();
    }

    const segDur = m.t1[idx] - m.t0[idx];
    const frac = segDur > 1e-6 ? Math.max(0, Math.min(1, (state.currentTime - m.t0[idx]) / segDur)) : 1.0;
    nx = m.x0[idx] + (m.x1[idx] - m.x0[idx]) * frac;
    ny = m.y0[idx] + (m.y1[idx] - m.y0[idx]) * frac;
    nz = m.z0[idx] + (m.z1[idx] - m.z0[idx]) * frac;
    state.nozzlePos = [nx, ny, nz];
    drawSegs = idx + 1;
    updateHud(idx, nx, ny, nz);
  }

  if (state.gl) {
    const gl = state.gl;
    gl.viewport(0, 0, w, h);
    gl.clearColor(0.085, 0.098, 0.122, 1.0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.useProgram(state.prog);
    gl.uniformMatrix4fv(state.uMvp, false, mvp);
    gl.uniform1f(state.uPointSize, 4.5 * dpr);
    gl.enableVertexAttribArray(state.aPos);
    gl.enableVertexAttribArray(state.aCol);

    if (state.showBed && state.bedCount > 0) {
      gl.bindBuffer(gl.ARRAY_BUFFER, state.bedBuf);
      gl.vertexAttribPointer(state.aPos, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, state.bedColBuf);
      gl.vertexAttribPointer(state.aCol, 4, gl.FLOAT, false, 0, 0);
      gl.drawArrays(gl.LINES, 0, state.bedCount);
    }

    if (m && drawSegs > 0) {
      gl.bindBuffer(gl.ARRAY_BUFFER, state.pathBuf);
      gl.vertexAttribPointer(state.aPos, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, state.pathColorBuf);
      gl.vertexAttribPointer(state.aCol, 4, gl.FLOAT, false, 0, 0);
      gl.drawArrays(gl.LINES, 0, drawSegs * 2);

      // Progressive retraction markers (zero-length XYZ moves rendered as pink points)
      if (state.showRetract && !state.singleLayer && state.retractCount > 0) {
        let lo = 0, hi = state.retractCount;
        while (lo < hi) {
          const mid = (lo + hi) >> 1;
          if (state.retractSegs[mid] < drawSegs) lo = mid + 1; else hi = mid;
        }
        if (lo > 0) {
          gl.bindBuffer(gl.ARRAY_BUFFER, state.retractBuf);
          gl.vertexAttribPointer(state.aPos, 3, gl.FLOAT, false, 0, 0);
          gl.bindBuffer(gl.ARRAY_BUFFER, state.retractColBuf);
          gl.vertexAttribPointer(state.aCol, 4, gl.FLOAT, false, 0, 0);
          gl.drawArrays(gl.POINTS, 0, lo);
        }
      }

      // Animated 3D nozzle cone + vertical pointer
      const nh = 12, nr = 3.2;
      const nVerts = new Float32Array([
        nx, ny, nz, nx + nr, ny, nz + nh,
        nx, ny, nz, nx - nr, ny, nz + nh,
        nx, ny, nz, nx, ny + nr, nz + nh,
        nx, ny, nz, nx, ny - nr, nz + nh,
        nx + nr, ny, nz + nh, nx, ny + nr, nz + nh,
        nx, ny + nr, nz + nh, nx - nr, ny, nz + nh,
        nx - nr, ny, nz + nh, nx, ny - nr, nz + nh,
        nx, ny - nr, nz + nh, nx + nr, ny, nz + nh
      ]);
      const nCols = new Float32Array(16 * 4);
      for (let i = 0; i < 16; i++) {
        nCols[i * 4] = 1.0; nCols[i * 4 + 1] = 0.92; nCols[i * 4 + 2] = 0.25; nCols[i * 4 + 3] = 1.0;
      }
      gl.bindBuffer(gl.ARRAY_BUFFER, state.nozzleBuf);
      gl.bufferData(gl.ARRAY_BUFFER, nVerts, gl.DYNAMIC_DRAW);
      gl.vertexAttribPointer(state.aPos, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, state.nozzleColBuf);
      gl.bufferData(gl.ARRAY_BUFFER, nCols, gl.DYNAMIC_DRAW);
      gl.vertexAttribPointer(state.aCol, 4, gl.FLOAT, false, 0, 0);
      gl.drawArrays(gl.LINES, 0, 16);
    }
  } else if (state.ctx2d) {
    // Graceful Canvas 2D software 3D projection fallback
    const ctx = state.ctx2d;
    ctx.fillStyle = "#16191f";
    ctx.fillRect(0, 0, w, h);
    function projPt(x, y, z) {
      const cx = mvp[0]*x + mvp[4]*y + mvp[8]*z + mvp[12];
      const cy = mvp[1]*x + mvp[5]*y + mvp[9]*z + mvp[13];
      const cw = mvp[3]*x + mvp[7]*y + mvp[11]*z + mvp[15];
      if (cw <= 0.1) return null;
      return [(cx / cw * 0.5 + 0.5) * w, (0.5 - cy / cw * 0.5) * h];
    }
    if (state.showBed && state.bedVerts) {
      ctx.strokeStyle = "rgba(120,135,160,0.35)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let i = 0; i < state.bedVerts.length; i += 6) {
        const a = projPt(state.bedVerts[i], state.bedVerts[i+1], state.bedVerts[i+2]);
        const b = projPt(state.bedVerts[i+3], state.bedVerts[i+4], state.bedVerts[i+5]);
        if (a && b) { ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); }
      }
      ctx.stroke();
    }
    if (m && drawSegs > 0) {
      ctx.strokeStyle = "#f97316";
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      const stride = drawSegs > 25000 ? Math.ceil(drawSegs / 25000) : 1;
      for (let i = 0; i < drawSegs; i += stride) {
        if (m.type[i] !== 0 || m.layer[i] > state.maxLayer) continue;
        if (state.singleLayer && m.layer[i] !== state.maxLayer) continue;
        const a = projPt(m.x0[i], m.y0[i], m.z0[i]);
        const b = projPt(m.x1[i], m.y1[i], m.z1[i]);
        if (a && b) { ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); }
      }
      ctx.stroke();
      const np = projPt(nx, ny, nz);
      if (np) {
        ctx.fillStyle = "#fde047";
        ctx.beginPath(); ctx.arc(np[0], np[1], 5, 0, Math.PI * 2); ctx.fill();
      }
    }
  }
}

function updateHud(idx, nx, ny, nz) {
  const m = state.model;
  if (!m) return;
  const lay = (m.layer[idx] || 0) + 1;
  document.getElementById("hud-time").textContent = fmtTime(state.currentTime) + " / " + fmtTime(m.totalTime);
  document.getElementById("hud-layer").textContent = lay + " / " + Math.max(1, m.layerZ.length) + " (" + nz.toFixed(2) + " mm)";
  document.getElementById("hud-xyz").textContent = nx.toFixed(2) + ", " + ny.toFixed(2) + ", " + nz.toFixed(2);
  document.getElementById("hud-speed").textContent = m.speed[idx].toFixed(1) + " mm/s";
  document.getElementById("hud-temp").textContent = m.temp[idx] + " °C";
  const fanEl = document.getElementById("hud-fan");
  fanEl.textContent = m.fan[idx] ? "ON" : "OFF";
  fanEl.className = "hud-v" + (m.fan[idx] ? " on" : "");
  document.getElementById("hud-fil").textContent = m.fil[idx].toFixed(2) + " mm";
  document.getElementById("hud-pkt").textContent = "#" + m.pkt[idx] + " (CMD " + m.op[idx] + ")";
  document.getElementById("time-readout").textContent = fmtTime(state.currentTime) + " / " + fmtTime(m.totalTime);
  const slider = document.getElementById("time-slider");
  if (m.totalTime > 0) slider.value = Math.round((state.currentTime / m.totalTime) * 10000);
}

function fitCamera() {
  const m = state.model;
  if (!m) { state.cam = { yaw: 0.72, pitch: 0.58, dist: 240, tx: 0, ty: 0, tz: 15 }; return; }
  const b = m.bounds;
  state.cam.tx = (b.minX + b.maxX) * 0.5;
  state.cam.ty = (b.minY + b.maxY) * 0.5;
  state.cam.tz = (b.minZ + b.maxZ) * 0.5;
  const span = Math.max(b.maxX - b.minX, b.maxY - b.minY, b.maxZ - b.minZ, 25);
  state.cam.dist = Math.max(45, span * 1.9);
  renderScene();
}

function loadBytes(rawBytes, fileName) {
  const statusEl = document.getElementById("status-pill");
  try {
    statusEl.textContent = "Decoding X3G...";
    const tStart = performance.now();
    const profId = document.getElementById("sel-profile").value;
    const model = decodeX3G(rawBytes, profId, fileName);
    const dtMs = (performance.now() - tStart).toFixed(1);

    state.rawBytes = rawBytes;
    state.fileName = fileName;
    state.model = model;
    state.playing = false;
    state.currentTime = model.totalTime;
    state.maxLayer = Math.max(0, model.layerZ.length - 1);
    document.getElementById("btn-play").textContent = "Play";

    document.getElementById("empty-state").style.display = "none";
    document.getElementById("hud").style.display = "block";
    document.getElementById("layer-rail").style.display = "flex";

    const ts = document.getElementById("time-slider");
    ts.disabled = false; ts.max = 10000; ts.value = 10000;
    for (const id of ["btn-play", "btn-reset", "btn-step-back", "btn-step-fwd"]) {
      document.getElementById(id).disabled = false;
    }

    const ls = document.getElementById("layer-slider");
    ls.min = 0; ls.max = state.maxLayer; ls.value = state.maxLayer;
    document.getElementById("layer-top-lbl").textContent = "L" + (state.maxLayer + 1);

    // Populate metrics sidebar
    document.getElementById("m-name").textContent = model.buildName;
    document.getElementById("m-size").textContent = model.byteLength.toLocaleString() + " B";
    document.getElementById("m-moves").textContent = model.count.toLocaleString();
    document.getElementById("m-layers").textContent = Math.max(1, model.layerZ.length).toString();
    document.getElementById("m-maxspd").textContent = model.maxExtSpeed.toFixed(1) + " mm/s";
    document.getElementById("m-maxtrv").textContent = model.maxTrvSpeed.toFixed(1) + " mm/s";
    document.getElementById("m-fil").textContent = model.totalFilament.toFixed(2) + " mm";
    document.getElementById("m-dur").textContent = fmtTime(model.totalTime);
    const b = model.bounds;
    document.getElementById("m-bounds").textContent =
      `X [${b.minX.toFixed(1)}, ${b.maxX.toFixed(1)}]\nY [${b.minY.toFixed(1)}, ${b.maxY.toFixed(1)}]\nZ [${b.minZ.toFixed(2)}, ${b.maxZ.toFixed(2)}]`;

    // Populate events tab
    const evList = document.getElementById("event-list");
    evList.innerHTML = "";
    for (const ev of model.events.slice(0, 300)) {
      const div = document.createElement("div");
      div.className = "event-item";
      div.innerHTML = `<span>${ev.label.replace(/</g, "&lt;")}</span><span class="event-time">${fmtTime(ev.time)}</span>`;
      div.addEventListener("click", () => {
        state.playing = false;
        document.getElementById("btn-play").textContent = "Play";
        state.currentTime = ev.time;
        renderScene();
      });
      evList.appendChild(div);
    }

    uploadToolpathGeometry();
    fitCamera();
    statusEl.textContent = `Decoded ${model.packetCount.toLocaleString()} pkts in ${dtMs} ms (${state.gl ? "WebGL" : "2D"})`;
  } catch (err) {
    statusEl.textContent = "Decode error: " + err.message;
  }
}

function renderSummaryPanel(summary) {
  const el = document.getElementById("export-summary-panel");
  if (!summary) {
    el.innerHTML = '<div style="color:var(--muted)">No OrcaSlicer export summary recorded yet.</div>';
    return;
  }
  const esc = s => String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  const ignored = Object.entries(summary.ignored_commands || {}).map(([k, v]) => `${esc(k)} × ${v}`).join(", ") || "none";
  const warns = (summary.warnings || []).map(w => `<li>${esc(w)}</li>`).join("");
  el.innerHTML = `
    <h3>Last OrcaSlicer Export</h3>
    <div class="kv-grid" style="margin-bottom:10px">
      <div class="kv-card"><div class="kv-label">Profile / FW</div><div class="kv-val">${esc(summary.machine_profile)} / ${esc(summary.firmware)}</div></div>
      <div class="kv-card"><div class="kv-label">X3G Bytes</div><div class="kv-val">${Number(summary.x3g_bytes || 0).toLocaleString()} B</div></div>
      <div class="kv-card"><div class="kv-label">Motions</div><div class="kv-val">${Number(summary.motion_commands || 0).toLocaleString()}</div></div>
      <div class="kv-card"><div class="kv-label">Layers</div><div class="kv-val">${Number(summary.layers_detected || 0)}</div></div>
    </div>
    <div class="kv-card" style="margin-bottom:8px"><div class="kv-label">Output Path</div><div class="kv-val" style="font-size:11px">${esc(summary.output_path || "")}</div></div>
    <div class="kv-card" style="margin-bottom:8px"><div class="kv-label">Ignored Commands</div><div class="kv-val" style="font-size:11.5px">${ignored}</div></div>
    ${warns ? `<div class="warn-box"><strong>Warnings (${summary.warnings.length})</strong><ul>${warns}</ul></div>` : '<div class="kv-card"><div class="kv-label">Warnings</div><div class="kv-val">None</div></div>'}
  `;
}

// === Animation Loop ===
function tick(nowMs) {
  if (state.playing && state.model) {
    if (state.lastFrameMs > 0) {
      const dt = Math.min(0.1, (nowMs - state.lastFrameMs) / 1000.0);
      state.currentTime += dt * state.playSpeed;
      if (state.currentTime >= state.model.totalTime) {
        state.currentTime = state.model.totalTime;
        state.playing = false;
        document.getElementById("btn-play").textContent = "Play";
      }
      renderScene();
    }
    state.lastFrameMs = nowMs;
  } else {
    state.lastFrameMs = 0;
  }
  requestAnimationFrame(tick);
}

// === Camera Mouse Interaction ===
let dragMode = null, lastMx = 0, lastMy = 0;
canvas.addEventListener("contextmenu", e => e.preventDefault());
canvas.addEventListener("mousedown", e => {
  dragMode = (e.button === 2 || e.shiftKey) ? "pan" : "orbit";
  lastMx = e.clientX; lastMy = e.clientY;
});
window.addEventListener("mouseup", () => { dragMode = null; });
window.addEventListener("mousemove", e => {
  if (!dragMode) return;
  const dx = e.clientX - lastMx, dy = e.clientY - lastMy;
  lastMx = e.clientX; lastMy = e.clientY;
  if (dragMode === "orbit") {
    state.cam.yaw -= dx * 0.008;
    state.cam.pitch = Math.max(-1.45, Math.min(1.52, state.cam.pitch + dy * 0.008));
  } else {
    const s = state.cam.dist * 0.0018;
    const sy = Math.sin(state.cam.yaw), cy = Math.cos(state.cam.yaw);
    state.cam.tx += (-sy * dx - cy * dy * Math.sin(state.cam.pitch)) * s;
    state.cam.ty += (cy * dx - sy * dy * Math.sin(state.cam.pitch)) * s;
    state.cam.tz += dy * Math.cos(state.cam.pitch) * s;
  }
  renderScene();
});
canvas.addEventListener("wheel", e => {
  e.preventDefault();
  const factor = e.deltaY > 0 ? 1.12 : 0.89;
  state.cam.dist = Math.max(15, Math.min(1400, state.cam.dist * factor));
  renderScene();
}, { passive: false });

// === UI Controls ===
document.getElementById("file-input").addEventListener("change", e => {
  const f = e.target.files && e.target.files[0];
  if (!f) return;
  const reader = new FileReader();
  reader.onload = () => loadBytes(new Uint8Array(reader.result), f.name);
  reader.readAsArrayBuffer(f);
});

const wrap = document.getElementById("viewport-wrap");
const dropEl = document.getElementById("drop-overlay");
wrap.addEventListener("dragover", e => { e.preventDefault(); dropEl.classList.add("show"); });
wrap.addEventListener("dragleave", () => dropEl.classList.remove("show"));
wrap.addEventListener("drop", e => {
  e.preventDefault(); dropEl.classList.remove("show");
  const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
  if (!f) return;
  const reader = new FileReader();
  reader.onload = () => loadBytes(new Uint8Array(reader.result), f.name);
  reader.readAsArrayBuffer(f);
});

document.getElementById("sel-profile").addEventListener("change", () => {
  if (state.rawBytes) loadBytes(state.rawBytes, state.fileName);
});
document.getElementById("sel-color").addEventListener("change", e => {
  state.colorMode = e.target.value; rebuildToolpathColors(); renderScene();
});
document.getElementById("sel-speed").addEventListener("change", e => {
  state.playSpeed = Number(e.target.value) || 20;
});
document.getElementById("btn-play").addEventListener("click", () => {
  if (!state.model) return;
  if (!state.playing && state.currentTime >= state.model.totalTime - 1e-4) state.currentTime = 0;
  state.playing = !state.playing;
  document.getElementById("btn-play").textContent = state.playing ? "Pause" : "Play";
});
document.getElementById("btn-reset").addEventListener("click", () => {
  if (!state.model) return;
  state.playing = false; document.getElementById("btn-play").textContent = "Play";
  state.currentTime = 0; renderScene();
});
document.getElementById("btn-step-fwd").addEventListener("click", () => {
  if (!state.model) return;
  state.playing = false; document.getElementById("btn-play").textContent = "Play";
  const next = Math.min(state.model.count - 1, state.activeSegIdx + 1);
  state.currentTime = state.model.t1[next]; renderScene();
});
document.getElementById("btn-step-back").addEventListener("click", () => {
  if (!state.model) return;
  state.playing = false; document.getElementById("btn-play").textContent = "Play";
  const prev = Math.max(0, state.activeSegIdx - 1);
  state.currentTime = state.model.t1[prev]; renderScene();
});
document.getElementById("time-slider").addEventListener("input", e => {
  if (!state.model) return;
  state.playing = false; document.getElementById("btn-play").textContent = "Play";
  state.currentTime = (Number(e.target.value) / 10000) * state.model.totalTime;
  renderScene();
});
document.getElementById("layer-slider").addEventListener("input", e => {
  if (!state.model) return;
  state.maxLayer = Number(e.target.value);
  document.getElementById("layer-top-lbl").textContent = "L" + (state.maxLayer + 1);
  const endSeg = state.model.layerEndSeg[state.maxLayer];
  if (endSeg !== undefined) state.currentTime = state.model.t1[endSeg];
  rebuildToolpathColors(); renderScene();
});
for (const [id, key] of [
  ["chk-travel", "showTravel"], ["chk-retract", "showRetract"],
  ["chk-single-layer", "singleLayer"], ["chk-dim-lower", "dimLower"], ["chk-bed", "showBed"]
]) {
  document.getElementById(id).addEventListener("change", e => {
    state[key] = e.target.checked;
    if (key !== "showBed") rebuildToolpathColors();
    renderScene();
  });
}
document.getElementById("cam-iso").addEventListener("click", () => { state.cam.yaw = 0.72; state.cam.pitch = 0.58; renderScene(); });
document.getElementById("cam-top").addEventListener("click", () => { state.cam.yaw = -Math.PI/2; state.cam.pitch = 1.52; renderScene(); });
document.getElementById("cam-front").addEventListener("click", () => { state.cam.yaw = -Math.PI/2; state.cam.pitch = 0.08; renderScene(); });
document.getElementById("cam-side").addEventListener("click", () => { state.cam.yaw = 0.0; state.cam.pitch = 0.08; renderScene(); });
document.getElementById("cam-fit").addEventListener("click", fitCamera);

document.querySelectorAll(".side-tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".side-tab").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    const target = btn.getAttribute("data-tab");
    for (const t of ["info", "events", "summary"]) {
      document.getElementById("tab-" + t).style.display = (t === target) ? "block" : "none";
    }
  });
});

window.addEventListener("keydown", e => {
  if (e.code === "Space" && state.model && e.target.tagName !== "INPUT" && e.target.tagName !== "SELECT") {
    e.preventDefault();
    document.getElementById("btn-play").click();
  }
});
window.addEventListener("resize", renderScene);

// === OrcaSlicer window.orca Chunked Binary Bridge ===
let chunkBuf = [];
let chunkMeta = null;

function handleOrcaMessage(msg) {
  if (!msg || typeof msg !== "object") return;
  if (msg.summary !== undefined) renderSummaryPanel(msg.summary);
  if (msg.type === "x3g_begin") {
    chunkBuf = new Array(msg.total_chunks || 0);
    chunkMeta = msg;
    if (msg.profile && PROFILES[msg.profile]) {
      document.getElementById("sel-profile").value = msg.profile;
    }
    document.getElementById("status-pill").textContent = "Loading " + (msg.name || "export") + "...";
  } else if (msg.type === "x3g_chunk") {
    chunkBuf[msg.index] = b64ToBytes(msg.b64);
    if (chunkMeta && msg.index + 1 === chunkMeta.total_chunks) {
      let totalLen = 0;
      for (const c of chunkBuf) totalLen += c.length;
      const full = new Uint8Array(totalLen);
      let off = 0;
      for (const c of chunkBuf) { full.set(c, off); off += c.length; }
      const fname = chunkMeta.name || "last_export.x3g";
      chunkBuf = []; chunkMeta = null;
      loadBytes(full, fname);
    }
  } else if (msg.type === "x3g_none") {
    document.getElementById("status-pill").textContent = "No cached export yet — export an .x3g or open a file";
  }
}

document.getElementById("btn-load-last").addEventListener("click", () => {
  if (window.orca) {
    document.getElementById("status-pill").textContent = "Requesting last export...";
    window.orca.postMessage({ action: "load_last" });
  }
});

// === Theme Detection & Toggle (prevents OrcaSlicer light/dark variable mismatch) ===
function applyTheme(mode) {
  const m = (mode === "light") ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", m);
  const btn = document.getElementById("btn-theme");
  if (btn) btn.textContent = (m === "dark") ? "Theme: Dark" : "Theme: Light";
}
function detectHostTheme() {
  try {
    const raw = getComputedStyle(document.documentElement).getPropertyValue("--orca-bg").trim();
    if (raw) {
      const tmp = document.createElement("div");
      tmp.style.color = raw;
      document.body.appendChild(tmp);
      const rgb = getComputedStyle(tmp).color;
      document.body.removeChild(tmp);
      const m = rgb.match(/\d+/g);
      if (m && m.length >= 3) {
        const lum = (0.299 * (+m[0]) + 0.587 * (+m[1]) + 0.114 * (+m[2])) / 255;
        return lum > 0.55 ? "light" : "dark";
      }
    }
  } catch (e) {}
  return "dark";
}
document.getElementById("btn-theme").addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme") || "dark";
  applyTheme(cur === "dark" ? "light" : "dark");
});

// === Bootstrap ===
applyTheme(detectHostTheme());
initRenderer();
renderScene();
requestAnimationFrame(tick);

if (EMBEDDED_PROFILE && PROFILES[EMBEDDED_PROFILE]) {
  document.getElementById("sel-profile").value = EMBEDDED_PROFILE;
}
if (EMBEDDED_X3G_B64) {
  document.getElementById("btn-load-last").style.display = "none";
  loadBytes(b64ToBytes(EMBEDDED_X3G_B64), EMBEDDED_FILENAME || "model.x3g");
} else if (window.orca) {
  window.orca.onMessage(handleOrcaMessage);
  window.orca.postMessage({ action: "load_last" });
} else {
  document.getElementById("btn-load-last").style.display = "none";
}
</script>
</body>
</html>
"""


def build_viewer_html(
    *,
    x3g_bytes: bytes | None = None,
    filename: str = "",
    profile_id: str = "r2",
) -> str:
    """Build the self-contained interactive 3D X3G viewer HTML document."""
    b64_literal = (
        json.dumps(base64.b64encode(x3g_bytes).decode("ascii"))
        if x3g_bytes is not None
        else "null"
    )
    return (
        _VIEWER_HTML_TEMPLATE
        .replace("__EMBEDDED_X3G_B64__", b64_literal)
        .replace("__EMBEDDED_FILENAME__", json.dumps(filename))
        .replace("__EMBEDDED_PROFILE__", json.dumps(profile_id))
        .replace("__R2_SPM_X__", repr(REPLICATOR_2.x.steps_per_mm))
        .replace("__R2_SPM_Y__", repr(REPLICATOR_2.y.steps_per_mm))
        .replace("__R2_SPM_Z__", repr(REPLICATOR_2.z.steps_per_mm))
        .replace("__R2_SPM_A__", repr(REPLICATOR_2.extruder_a.steps_per_mm))
        .replace("__R2F_SPM_X__", repr(REPLICATOR_2_FACTORY.x.steps_per_mm))
        .replace("__R2F_SPM_Y__", repr(REPLICATOR_2_FACTORY.y.steps_per_mm))
        .replace("__R2F_SPM_Z__", repr(REPLICATOR_2_FACTORY.z.steps_per_mm))
        .replace("__R2F_SPM_A__", repr(REPLICATOR_2_FACTORY.extruder_a.steps_per_mm))
    )


def export_viewer_html(
    x3g_path: str | Path,
    output_html: str | Path,
    *,
    profile_id: str = "r2",
) -> Path:
    """Embed an ``.x3g`` file into a standalone self-contained interactive 3D HTML viewer."""
    src = Path(x3g_path)
    dst = Path(output_html)
    html_str = build_viewer_html(
        x3g_bytes=src.read_bytes(),
        filename=src.name,
        profile_id=profile_id,
    )
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(html_str, encoding="utf-8")
    return dst


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="x3gview",
        description="Open an interactive 3D WebGL viewer & time-playback simulator for a MakerBot .x3g file.",
    )
    parser.add_argument("input_x3g", type=Path, help="Path to .x3g (or framed .s3g) file")
    parser.add_argument(
        "--profile",
        default="r2",
        choices=("r2", "r2_factory"),
        help="Replicator 2 steps/mm profile used for mm conversion (default: r2).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional path to save the standalone interactive HTML viewer.",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Write HTML without opening a web browser.",
    )
    args = parser.parse_args(argv)

    if not args.input_x3g.is_file():
        print(f"error: file not found: {args.input_x3g}", file=sys.stderr)
        return 2

    if args.out is not None:
        html_path = export_viewer_html(args.input_x3g, args.out, profile_id=args.profile)
    else:
        tmp_dir = Path(tempfile.gettempdir()) / "orca_x3g_viewer"
        html_path = export_viewer_html(
            args.input_x3g,
            tmp_dir / f"{args.input_x3g.stem}_viewer.html",
            profile_id=args.profile,
        )

    print(f"Generated interactive 3D X3G viewer: {html_path}")
    if not args.no_open:
        webbrowser.open(html_path.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
