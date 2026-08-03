"""Web UI for klepcbgen: paste KLE JSON, pick options, download the generated
KiCad project + matrix file + firmware, with a live PCB thumbnail.

Run with:
    uvicorn webui:app --host 0.0.0.0 --port 8088
or:
    python3 webui.py
"""
import io
import json
import os
import shutil
import subprocess
import tempfile
import zipfile

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from klepcbgenmod import (
    KLEPCBGenerator,
    GeneratorOptions,
    parse_kle_json,
    CONTROLLERS,
    KEY_FOOTPRINTS,
    DIODE_FOOTPRINTS,
)
from render import render_pcb_svg

app = FastAPI(title="klepcbgen Web UI")

# Serve bundled three.js + GLB viewer assets (self-contained, no CDN needed).
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    # GLTFLoader.module.js has a relative `../utils/BufferGeometryUtils.js`
    # import that resolves to /utils/ (not /static/utils/). Serve it there.
    _UTILS_DIR = os.path.join(_STATIC_DIR, "utils")
    if os.path.isdir(_UTILS_DIR):
        app.mount("/utils", StaticFiles(directory=_UTILS_DIR), name="utils")

# Self-contained example layout (JB82, 82-key 75%-ish) so users can try it
# without hunting for a KLE JSON.
EXAMPLE_LAYOUT = [
    {
        "name": "JB82",
        "author": "Jeroen Bouwens",
        "notes": "75-ish% layout",
        "switchMount": "cherry",
        "pcb": True,
    },
    [{"a": 6}, "Esc", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
     {"a": 7}, "Home", "End", {"x": 0.25}, "Insert"],
    [{"y": 0.25, "a": 4}, "~\n`", "!\n1", "@\n2", "#\n3", "$\n4", "%\n5", "^\n6", "&\n7", "*\n8",
     "(\n9", ")\n0", "_\n-", "+\n=", {"a": 6, "w": 2}, "Backspace", {"x": 0.25, "a": 7}, "Delete"],
    [{"a": 4, "w": 1.5}, "Tab", "Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P", "{\n[", "}\n]",
     {"w": 1.5}, "|\n\\", {"x": 0.25, "a": 7}, "PgUp"],
    [{"a": 4, "w": 1.75}, "Caps Lock", "A", "S", "D", "F", "G", "H", "J", "K", "L", ":\n;", "\"\n'",
     {"a": 6, "w": 2.25}, "Enter", {"x": 0.25, "a": 7}, "PgDn"],
    [{"a": 4, "w": 2.25}, "Shift", "Z", "X", "C", "V", "B", "N", "M", "<\n,", ">\n.", "?\n/",
     {"a": 6, "w": 2.75}, "Shift", {"x": 0.25, "a": 7}, "Up"],
    [{"a": 4, "w": 1.25}, "Ctrl", "Win", {"w": 1.25}, "Alt", {"w": 6.25}, "Space",
     {"a": 6, "w": 1.25}, "Alt", "Fn", {"x": 0.25, "a": 7}, "Left", "Down", "Right"],
]


def _index_html() -> str:
    controller_opts = "".join(
        f'<option value="{k}">{v}</option>' for k, v in CONTROLLERS.items()
    )
    key_opts = "".join(
        f'<option value="{k}">{v}</option>' for k, v in KEY_FOOTPRINTS.items()
    )
    diode_opts = "".join(
        f'<option value="{k}">{v}</option>' for k, v in DIODE_FOOTPRINTS.items()
    )
    example = json.dumps(EXAMPLE_LAYOUT)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>klepcbgen — Keyboard PCB generator</title>
<style>
  :root {{
    --bg: #0f1115;
    --panel: #171a21;
    --panel-2: #1d212b;
    --border: #2a2f3a;
    --text: #e6e9ef;
    --muted: #9aa3b2;
    --accent: #4f8cff;
    --accent-hover: #6ba0ff;
    --ok: #37c47a;
    --err: #ff5c5c;
    --radius: 10px;
    --mono: "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;
    --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }}
  header {{
    padding: 22px 28px;
    border-bottom: 1px solid var(--border);
    background: linear-gradient(180deg, #13161d, #0f1115);
  }}
  .logo {{
    display: flex; align-items: center; gap: 12px;
    font-size: 18px; font-weight: 700; letter-spacing: .2px;
  }}
  .logo .mark {{
    width: 34px; height: 34px; border-radius: 8px;
    background: linear-gradient(135deg, var(--accent), #7a5cff);
    display: grid; place-items: center; color: #fff;
    font-family: var(--mono); font-weight: 700; font-size: 15px;
    flex-shrink: 0;
  }}
  .logo .tag {{
    color: var(--muted); font-weight: 500; font-size: 13px; margin-left: 4px;
  }}
  main {{
    max-width: 1200px; margin: 0 auto; padding: 28px;
    display: grid; grid-template-columns: 420px 1fr; gap: 28px;
  }}
  @media (max-width: 900px) {{ main {{ grid-template-columns: 1fr; }} }}
  .card {{
    background: var(--panel); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 20px;
  }}
  .card h2 {{
    margin: 0 0 16px; font-size: 14px; font-weight: 600;
    text-transform: uppercase; letter-spacing: .06em; color: var(--muted);
  }}
  label {{ display: block; margin: 14px 0 6px; font-size: 13px; font-weight: 600; }}
  .row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
  textarea, select, input[type=number] {{
    width: 100%; padding: 9px 11px;
    background: var(--panel-2); color: var(--text);
    border: 1px solid var(--border); border-radius: 7px;
    font-size: 13px; font-family: inherit;
    transition: border-color .15s, box-shadow .15s;
  }}
  textarea {{
    font-family: var(--mono); font-size: 12px; line-height: 1.5;
    min-height: 220px; resize: vertical;
  }}
  textarea:focus, select:focus, input:focus {{
    outline: none; border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(79,140,255,.18);
  }}
  select option {{ background: var(--panel-2); }}
  .check {{ display: flex; align-items: center; gap: 8px; margin-top: 12px; font-weight: 500; }}
  .check input {{ width: 16px; height: 16px; accent-color: var(--accent); }}
  .actions {{ display: flex; gap: 10px; margin-top: 20px; }}
  button {{
    padding: 10px 18px; border: none; border-radius: 7px;
    font-size: 14px; font-weight: 600; cursor: pointer;
    font-family: inherit;
  }}
  .primary {{
    flex: 1; background: var(--accent); color: #fff;
    transition: background .15s, transform .05s;
  }}
  .primary:hover {{ background: var(--accent-hover); }}
  .primary:active {{ transform: translateY(1px); }}
  .primary:disabled {{ background: #33405e; cursor: not-allowed; }}
  .ghost {{
    background: transparent; color: var(--muted);
    border: 1px solid var(--border); font-weight: 500;
  }}
  .ghost:hover {{ color: var(--text); border-color: #3d4553; }}
  #preview {{
    min-height: 420px; height: 480px; position: relative;
    display: grid; place-items: center;
    background: var(--panel); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 8px; overflow: hidden;
  }}
  #preview img {{ max-width: 100%; max-height: 560px; }}
  #preview .placeholder {{ color: var(--muted); text-align: center; position: absolute; inset: 0; display: grid; place-items: center; }}
  #preview .placeholder svg {{ margin-bottom: 8px; opacity: .5; }}
  #preview .placeholder img {{ max-width: 96%; max-height: 96%; border-radius: 6px; }}
  .gerber-link {{
    position: absolute; bottom: 12px; right: 12px; z-index: 3;
    display: inline-flex; align-items: center; gap: 7px;
    padding: 8px 14px; border-radius: 8px;
    background: var(--accent); color: #fff; text-decoration: none;
    font-size: 13px; font-weight: 600; font-family: inherit;
    box-shadow: 0 4px 14px rgba(0,0,0,.35);
    transition: opacity .12s, transform .12s;
  }}
  .gerber-link:hover {{ opacity: .92; transform: translateY(-1px); }}
  #status {{
    margin-top: 14px; font-size: 13px; line-height: 1.6;
    display: none; border-radius: 7px; padding: 10px 12px;
  }}
  #status.ok {{ display: block; background: rgba(55,196,122,.1); color: var(--ok); border: 1px solid rgba(55,196,122,.3); }}
  #status.err {{ display: block; background: rgba(255,92,92,.1); color: var(--err); border: 1px solid rgba(255,92,92,.3); }}
  #status.loading {{ display: block; background: var(--panel-2); color: var(--muted); border: 1px solid var(--border); }}
  #status a {{ color: inherit; font-weight: 700; }}
  .meta {{ display: flex; flex-wrap: wrap; gap: 16px; margin-top: 8px; font-size: 13px; }}
  .subhead {{
    margin: 18px 0 8px; font-size: 14px; font-weight: 700;
    color: var(--text); letter-spacing: .02em;
  }}
  .subhead:first-of-type {{ margin-top: 4px; }}
  .meta span {{ display: flex; align-items: center; gap: 6px; color: var(--muted); }}
  .dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; }}
  .dot.green {{ background: var(--ok); }}
  .spinner {{
    width: 18px; height: 18px; border: 2px solid var(--border);
    border-top-color: var(--accent); border-radius: 50%;
    animation: spin .7s linear infinite; display: inline-block;
    vertical-align: -4px; margin-right: 8px;
  }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  .errorbox {{
    margin-top: 16px; background: rgba(255,92,92,.08);
    border: 1px solid rgba(255,92,92,.3); color: var(--err);
    border-radius: 7px; padding: 12px; font-size: 13px; white-space: pre-wrap;
  }}
  footer {{
    max-width: 1200px; margin: 40px auto 0; padding: 0 28px 28px;
    color: var(--muted); font-size: 12px; text-align: center;
  }}
  footer code {{ background: var(--panel-2); padding: 2px 6px; border-radius: 4px; }}
</style>
</head>
<body>
<header>
  <div class="logo">
    <div class="mark">K</div>
    <div>klepcbgen<span class="tag">— Keyboard PCB generator</span></div>
  </div>
</header>

<main>
  <!-- Form column -->
  <div>
    <div class="card">
      <h2>Layout</h2>
      <div class="actions" style="margin-top:0">
        <button type="button" class="ghost" onclick="loadExample()">Load example layout</button>
      </div>
      <label for="kle">Keyboard Layout Editor (KLE) JSON</label>
      <textarea id="kle" spellcheck="false" placeholder='Paste KLE raw data here (outer brackets optional). e.g. [{{"name":"My Board"}}, ["A","B"]]'></textarea>

      <div class="row">
        <div>
          <label for="controller">Controller</label>
          <select id="controller">{controller_opts}</select>
        </div>
        <div>
          <label for="firmware">Firmware</label>
          <select id="firmware">
            <option value="both">QMK + ZMK</option>
            <option value="qmk">QMK</option>
            <option value="zmk">ZMK</option>
            <option value="none">None</option>
          </select>
        </div>
      </div>

      <div class="row">
        <div>
          <label for="keyfp">Key switch footprint</label>
          <select id="keyfp">{key_opts}</select>
        </div>
        <div>
          <label for="diodfp">Diode footprint</label>
          <select id="diodfp">{diode_opts}</select>
        </div>
      </div>

      <h3 class="subhead">Diode placement</h3>
      <div class="row">
        <div>
          <label for="diode_offset_x">Diode X offset (mm)</label>
          <input id="diode_offset_x" type="number" step="0.1" value="-5.8">
        </div>
        <div>
          <label for="diode_offset_y">Diode Y offset (mm)</label>
          <input id="diode_offset_y" type="number" step="0.1" value="8.89">
        </div>
        <div>
          <label for="diode_rotation">Diode rotation (°)</label>
          <input id="diode_rotation" type="number" step="5" value="90">
        </div>
      </div>

      <div class="row">
        <div>
          <label for="pitch">Key pitch (mm)</label>
          <input id="pitch" type="number" step="0.01" value="19.05">
        </div>
        <div>
          <label for="margin">Edge margin (mm)</label>
          <input id="margin" type="number" step="0.1" value="3.0">
        </div>
        <div>
          <label for="radius">Edge radius (mm)</label>
          <input id="radius" type="number" step="0.1" value="3.0">
        </div>
      </div>

      <label class="check"><input id="edgecuts" type="checkbox" checked> Board outline (Edge.Cuts)</label>

      <h3 class="subhead">Switch plate</h3>
      <label class="check"><input id="plate_enabled" type="checkbox" checked> Generate plate file (drives edge cuts)</label>
      <div class="row">
        <div>
          <label for="plate_cutout">Switch cutout</label>
          <select id="plate_cutout">
            <option value="MX">MX</option>
            <option value="Alps">Alps</option>
            <option value="MX/Alps">MX/Alps</option>
            <option value="Support Plate">Support Plate</option>
            <option value="Custom Rectangle">Custom Rectangle</option>
            <option value="Choc V2">Choc V2</option>
          </select>
        </div>
        <div>
          <label for="plate_cutout_radius">Cutout radius (mm)</label>
          <input id="plate_cutout_radius" type="number" step="0.1" value="0.5">
        </div>
      </div>
      <div class="row">
        <div>
          <label for="plate_stab_type">Stabilizer</label>
          <select id="plate_stab_type">
            <option value="Large">Large</option>
            <option value="Normal">Normal</option>
            <option value="3mm Plate">3mm Plate</option>
            <option value="3mm Plate for Screw-ins">3mm Plate for Screw-ins</option>
            <option value="5mm Plate">5mm Plate</option>
            <option value="Choc V1">Choc V1</option>
            <option value="Choc V2">Choc V2</option>
            <option value="Gateron LP">Gateron LP</option>
            <option value="Custom Rectangles">Custom Rectangles</option>
            <option value="Single Rectangle">Single Rectangle</option>
          </select>
        </div>
        <div>
          <label for="plate_kerf">Kerf (mm)</label>
          <input id="plate_kerf" type="number" step="0.01" value="0.0">
        </div>
        <div>
          <label for="plate_margin">Plate margin (mm)</label>
          <input id="plate_margin" type="number" step="0.1" value="5.0">
        </div>
      </div>

      <div class="actions">
        <button type="button" class="primary" id="genbtn" onclick="generate()">Generate</button>
      </div>

      <div id="status"></div>
    </div>
  </div>

  <!-- Preview column -->
  <div>
    <div class="card">
      <h2>Preview</h2>
      <div id="preview">
        <div class="placeholder" id="placeholder">
          <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <rect x="3" y="5" width="18" height="14" rx="2"/>
            <path d="M3 9h18M7 5v14M17 5v14"/>
          </svg><br>
          Generate a board to see a live PCB preview here.
        </div>
        <a id="gerberlink" class="gerber-link" target="_blank" rel="noopener" style="display:none">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
            <path d="M15 3h6v6"/>
            <path d="M10 14L21 3"/>
          </svg>
          Open full Gerber viewer
        </a>
      </div>
      <div class="meta">
        <span><span class="dot green"></span>Generated locally on your tailnet</span>
      </div>
    </div>
  </div>
</main>

<footer>
  Fork of <code>jeroen94704/klepcbgen</code> — generates a KiCad project, duplex
  matrix JSON, and firmware from a KLE layout. No files leave your network.
</footer>

<script>
  const example = {example};

  function setStatus(msg, kind) {{
    const st = document.getElementById('status');
    st.className = kind || '';
    st.innerHTML = msg;
  }}

  function loadExample() {{
    document.getElementById('kle').value = JSON.stringify(example, null, 2);
    document.getElementById('kle').focus();
    setStatus('Example layout loaded. Click Generate.', 'ok');
  }}

  function esc(s) {{
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }}

  async function generate() {{
    const btn = document.getElementById('genbtn');
    const placeholder = document.getElementById('placeholder');
    const link = document.getElementById('gerberlink');
    btn.disabled = true;
    setStatus('<span class="spinner"></span>Generating board…', 'loading');
    placeholder.style.display = 'grid';
    placeholder.innerHTML = '<span class="spinner"></span>';
    link.style.display = 'none';

    const body = {{
      kle: document.getElementById('kle').value,
      controller: document.getElementById('controller').value,
      key_footprint: document.getElementById('keyfp').value,
      diode_footprint: document.getElementById('diodfp').value,
      diode_offset_x: parseFloat(document.getElementById('diode_offset_x').value),
      diode_offset_y: parseFloat(document.getElementById('diode_offset_y').value),
      diode_rotation: parseFloat(document.getElementById('diode_rotation').value),
      key_pitch: parseFloat(document.getElementById('pitch').value),
      edge_margin: parseFloat(document.getElementById('margin').value),
      edge_radius: parseFloat(document.getElementById('radius').value),
      edge_cuts: document.getElementById('edgecuts').checked,
      firmware_type: document.getElementById('firmware').value,
      plate_enabled: document.getElementById('plate_enabled').checked,
      plate_cutout: document.getElementById('plate_cutout').value,
      plate_cutout_radius: parseFloat(document.getElementById('plate_cutout_radius').value),
      plate_stab_type: document.getElementById('plate_stab_type').value,
      plate_kerf: parseFloat(document.getElementById('plate_kerf').value),
      plate_margin: parseFloat(document.getElementById('plate_margin').value),
    }};

    try {{
      const r = await fetch('/generate', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(body)
      }});
      const data = await r.json();
      if (!r.ok) {{
        const detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || 'Unknown error');
        setStatus('', '');
        placeholder.innerHTML = '<div class="errorbox">' + esc(detail) + '</div>';
        placeholder.style.display = 'grid';
        return;
      }}
      setStatus(
        'Generated <b>' + esc(data.keyboard) + '</b> — ' + data.num_keys + ' keys, ' +
        data.matrix_lines + ' matrix lines. &nbsp;<a href="' + data.download + '">Download ZIP</a>',
        'ok'
      );
      // Preview: show the front copper image as a static picture.
      const frontUrl = data.viewer_front || data.thumbnail;
      if (frontUrl) {{
        placeholder.innerHTML = '<img src="' + frontUrl + '" alt="PCB front preview">';
        placeholder.style.display = 'grid';
      }} else {{
        placeholder.innerHTML = '<div class="errorbox">Preview unavailable.</div>';
        placeholder.style.display = 'grid';
      }}
      // Link to the full GerberViewer in a new tab.
      if (data.gerbers) {{
        link.href = '/static/gerberviewer/index.html?gerber=' + encodeURIComponent(data.gerbers);
        link.style.display = 'inline-flex';
      }}
    }} catch (e) {{
      setStatus('', '');
      placeholder.innerHTML = '<div class="errorbox">Network error: ' + esc(String(e)) + '</div>';
      placeholder.style.display = 'grid';
    }} finally {{
      btn.disabled = false;
    }}
  }}

</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return _index_html()


@app.post("/generate")
async def generate(payload: dict):
    kle = (payload.get("kle") or "").strip()
    if not kle:
        raise HTTPException(400, "No KLE JSON provided")
    try:
        kle_data = parse_kle_json(kle)
    except ValueError as e:
        raise HTTPException(400, str(e))

    options = GeneratorOptions(
        key_pitch=float(payload.get("key_pitch", 19.05)),
        key_footprint=payload.get("key_footprint", "cherry_mx"),
        diode_footprint=payload.get("diode_footprint", "0805"),
        diode_offset_x=float(payload.get("diode_offset_x", -5.8)),
        diode_offset_y=float(payload.get("diode_offset_y", 8.89)),
        diode_rotation=float(payload.get("diode_rotation", 90.0)),
        controller=payload.get("controller", "atmega32u4"),
        edge_margin=float(payload.get("edge_margin", 3.0)),
        edge_cuts=bool(payload.get("edge_cuts", True)),
        edge_radius=float(payload.get("edge_radius", 3.0)),
        do_routing=bool(payload.get("do_routing", False)),
        firmware_type=payload.get("firmware_type", "both"),
        plate_enabled=bool(payload.get("plate_enabled", True)),
        plate_cutout=payload.get("plate_cutout", "MX"),
        plate_cutout_radius=float(payload.get("plate_cutout_radius", 0.5)),
        plate_cutout_width=float(payload.get("plate_cutout_width", 14.0)),
        plate_cutout_height=float(payload.get("plate_cutout_height", 14.0)),
        plate_stab_type=payload.get("plate_stab_type", "Large"),
        plate_stab_radius=float(payload.get("plate_stab_radius", 0.5)),
        plate_stab_width=float(payload.get("plate_stab_width", 7.0)),
        plate_stab_height=float(payload.get("plate_stab_height", 15.0)),
        plate_stab_offset=float(payload.get("plate_stab_offset", -0.5)),
        plate_kerf=float(payload.get("plate_kerf", 0.0)),
        plate_combine=bool(payload.get("plate_combine", False)),
        plate_margin=float(payload.get("plate_margin", 5.0)),
    )
    try:
        options.validate()
    except ValueError as e:
        raise HTTPException(400, str(e))

    workdir = tempfile.mkdtemp(prefix="klepcbgen_")
    kle_path = os.path.join(workdir, "layout.json")
    with open(kle_path, "w") as f:
        json.dump(kle_data, f)
    outname = os.path.join(workdir, "kb")
    matrix_path = os.path.join(workdir, "matrix.json")

    opts = GeneratorOptions(
        key_pitch=options.key_pitch,
        key_footprint=options.key_footprint,
        diode_footprint=options.diode_footprint,
        diode_offset_x=options.diode_offset_x,
        diode_offset_y=options.diode_offset_y,
        diode_rotation=options.diode_rotation,
        controller=options.controller,
        edge_margin=options.edge_margin,
        edge_cuts=options.edge_cuts,
        edge_radius=options.edge_radius,
        do_routing=options.do_routing,
        matrixfile=matrix_path,
        firmware_type=options.firmware_type,
        plate_enabled=options.plate_enabled,
        plate_cutout=options.plate_cutout,
        plate_cutout_radius=options.plate_cutout_radius,
        plate_cutout_width=options.plate_cutout_width,
        plate_cutout_height=options.plate_cutout_height,
        plate_stab_type=options.plate_stab_type,
        plate_stab_radius=options.plate_stab_radius,
        plate_stab_width=options.plate_stab_width,
        plate_stab_height=options.plate_stab_height,
        plate_stab_offset=options.plate_stab_offset,
        plate_kerf=options.plate_kerf,
        plate_combine=options.plate_combine,
        plate_margin=options.plate_margin,
    )
    gen = KLEPCBGenerator(opts)
    try:
        gen.generate_kicadproject(kle_path, outname)
    except ValueError as e:
        raise HTTPException(400, str(e))

    pcb_path = os.path.join(outname, "kb.kicad_pcb")
    svg = render_pcb_svg(pcb_path)
    thumb_name = "preview.svg"
    thumb_path = os.path.join(workdir, thumb_name)
    with open(thumb_path, "w") as f:
        f.write(svg)

    # 3D viewer: export a binary glTF of the PCB solid model (board + copper +
    # solder mask + holes) via KiCad, for the embedded three.js viewer.
    glb_name = "board.glb"
    glb_path = os.path.join(workdir, glb_name)
    glb_ok = _export_glb(pcb_path, glb_path)

    # 2D copper viewers: render front and back copper layers as SVGs via
    # kicad-cli. Back layer is mirrored so it reads correctly from the rear.
    front_ok = _export_layer_svg(pcb_path, "F.Cu,Edge.Cuts", False,
                                 os.path.join(workdir, "front.svg"))
    back_ok = _export_layer_svg(pcb_path, "B.Cu,Edge.Cuts", True,
                                os.path.join(workdir, "back.svg"))

    # GerberViewer: export a fabrication Gerber + drill set and cache it as a
    # zip so the preview panel (and the download) can serve it.
    gerber_dir = os.path.join(workdir, "gerbers")
    os.makedirs(gerber_dir, exist_ok=True)
    gfiles = _export_gerbers(pcb_path, gerber_dir)
    gerbers_ok = bool(gfiles)
    if gerbers_ok:
        with open(os.path.join(workdir, "gerbers.zip"), "wb") as f:
            f.write(_zip_gerbers(gerber_dir, gfiles))

    # Switch plate: write the DXF + SVG thumbnails to the workdir for download.
    from plategen import to_dxf, to_svg
    if getattr(gen, "_plate", None) is not None:
        _plate = gen._plate
        with open(os.path.join(workdir, "plate.dxf"), "w") as f:
            f.write(to_dxf(_plate))
        with open(os.path.join(workdir, "plate.svg"), "w") as f:
            f.write(to_svg(_plate))

    kb = gen.keyboard
    return {
        "keyboard": kb.name,
        "num_keys": len(kb.keys),
        "matrix_lines": kb.matrix_lines,
        "download": f"/download?d={workdir}",
        "thumbnail": f"/thumb?d={workdir}",
        "viewer3d": f"/glb?d={workdir}" if glb_ok else None,
        "viewer_front": f"/layer?d={workdir}&n=front" if front_ok else None,
        "viewer_back": f"/layer?d={workdir}&n=back" if back_ok else None,
        "gerbers": f"/gerbers?d={workdir}" if gerbers_ok else None,
        "zip_bytes": len(_build_zip(workdir)),
    }


def _build_zip(workdir):
    """Zip the whole generated project: the KiCad files live at the archive
    root (so opening the zip directly in KiCad works), and the extra viewer
    outputs (3D model, layer SVGs, preview, matrix, raw KLE input) sit
    alongside them so the download is the complete project."""
    zipbuf = io.BytesIO()
    with zipfile.ZipFile(zipbuf, "w", zipfile.ZIP_DEFLATED) as zf:
        outname = os.path.join(workdir, "kb")
        for root, _dirs, files in os.walk(outname):
            for fn in files:
                full = os.path.join(root, fn)
                arc = os.path.relpath(full, outname)
                zf.write(full, arc)
        for extra in ("matrix.json", "preview.svg", "board.glb",
                      "front.svg", "back.svg", "layout.json", "gerbers.zip",
                      "plate.dxf", "plate.svg"):
            p = os.path.join(workdir, extra)
            if os.path.exists(p):
                zf.write(p, extra)
        # Individual gerber/drill files under a gerbers/ subdir (for fab).
        gdir = os.path.join(workdir, "gerbers")
        if os.path.isdir(gdir):
            for fn in sorted(os.listdir(gdir)):
                p = os.path.join(gdir, fn)
                if os.path.isfile(p):
                    zf.write(p, os.path.join("gerbers", fn))
    zipbuf.seek(0)
    return zipbuf.getvalue()


@app.get("/download")
async def download(d: str):
    return Response(
        _build_zip(d),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=keyboard.zip"},
    )


@app.get("/thumb")
async def thumb(d: str):
    svg_path = os.path.join(d, "preview.svg")
    if not os.path.exists(svg_path):
        raise HTTPException(404, "preview not found")
    with open(svg_path) as f:
        return Response(f.read(), media_type="image/svg+xml")


@app.get("/glb")
async def glb(d: str):
    glb_path = os.path.join(d, "board.glb")
    if not os.path.exists(glb_path):
        raise HTTPException(404, "3D board model not found")
    with open(glb_path, "rb") as f:
        return Response(
            f.read(),
            media_type="model/gltf-binary",
            headers={"Content-Disposition": "inline; filename=board.glb"},
        )


@app.get("/layer")
async def layer(d: str, n: str):
    svg_path = os.path.join(d, f"{n}.svg")
    if n not in ("front", "back") or not os.path.exists(svg_path):
        raise HTTPException(404, "layer view not found")
    with open(svg_path) as f:
        return Response(f.read(), media_type="image/svg+xml")


@app.get("/gerbers")
async def gerbers(d: str):
    """Return a zip of the generated fabrication Gerbers + drill files, for
    the GerberViewer preview panel. Export happens once at generate time and
    the zip is cached in the workdir; this just serves it."""
    zip_path = os.path.join(d, "gerbers.zip")
    if not os.path.exists(zip_path):
        raise HTTPException(404, "gerbers not found")
    with open(zip_path, "rb") as f:
        data = f.read()
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f"inline; filename=gerbers.zip"},
    )


def _export_layer_svg(pcb_path, layers, mirror, out_path):
    """Render a single copper layer (front or back) to an SVG via kicad-cli.

    The output is a clean board-only render (no drawing sheet) showing the
    copper traces/pads on the requested layer plus the Edge.Cuts outline.
    The back layer is mirrored so text/traces read correctly when viewed
    from the rear of the board.
    """
    kicad_cli = shutil.which("kicad-cli")
    if not kicad_cli:
        return False
    try:
        cmd = [
            kicad_cli, "pcb", "export", "svg", pcb_path, "-o", out_path,
            "--layers", layers,
            "--exclude-drawing-sheet",
            "--fit-page-to-board",
            "--page-size-mode", "2",
        ]
        if mirror:
            cmd.append("--mirror")
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120
        )
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except (subprocess.SubprocessError, OSError):
        return False



def _export_glb(pcb_path, out_path):
    """Export a binary glTF (GLB) of the full PCB using kicad-cli.

    Includes the board solid model, copper (tracks/pads/zones), silkscreen,
    soldermask, and footprint 3D models so the viewer shows the complete PCB
    (not just the bare FR4 board).

    Returns True on success (file written), False if kicad-cli is unavailable.
    """
    kicad_cli = shutil.which("kicad-cli")
    if not kicad_cli:
        return False
    try:
        env = dict(os.environ)
        # Point KiCad at the standard 3D model library so component models load.
        env.setdefault("KISYS3DMOD", "/usr/share/kicad/3dmodels")
        proc = subprocess.run(
            [
                kicad_cli, "pcb", "export", "glb", pcb_path, "-o", out_path,
                "--include-tracks", "--include-pads", "--include-zones",
                "--include-inner-copper", "--include-silkscreen",
                "--include-soldermask",
            ],
            capture_output=True, text=True, timeout=120, env=env,
        )
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except (subprocess.SubprocessError, OSError):
        return False


# Gerber layers that make up a real fabrication set, in the order GerberViewer
# likes. F/B copper, silkscreen, soldermask, solder paste, and the board
# outline. (Adhes/User/CrtYd/Fab are dropped - junk for a fabrication preview.)
_GERBER_LAYERS = (
    "F.Cu,B.Cu,F.SilkS,B.SilkS,F.Mask,B.Mask,F.Paste,B.Paste,Edge.Cuts"
)


def _export_gerbers(pcb_path, out_dir, drill=True):
    """Export a fabrication Gerber set + Excellon drill files into out_dir.

    Returns the list of generated filenames (or empty on failure). Used to
    feed GerberViewer, which consumes standard KiCad Gerber/Excellon names
    (.gtl/.gbl copper, .gto/.gbo silk, .gts/.gbs mask, .gtp/.gbp paste,
    Edge_Cuts outline, .drl drill).
    """
    kicad_cli = shutil.which("kicad-cli")
    if not kicad_cli:
        return []
    try:
        ger = subprocess.run(
            [kicad_cli, "pcb", "export", "gerbers", pcb_path,
             "-o", out_dir, "-l", _GERBER_LAYERS],
            capture_output=True, text=True, timeout=120,
        )
        if ger.returncode != 0:
            return []
        if drill:
            dr = subprocess.run(
                [kicad_cli, "pcb", "export", "drill", pcb_path,
                 "-o", out_dir],
                capture_output=True, text=True, timeout=120,
            )
            if dr.returncode != 0:
                return []
        # Collect just the gerber/drill files (skip the .gbrjob manifest).
        files = sorted(
            f for f in os.listdir(out_dir)
            if f.endswith((".gtl", ".gbl", ".gto", ".gbo", ".gts", ".gbs",
                           ".gtp", ".gbp", ".gm1", ".drl"))
        )
        return files
    except (subprocess.SubprocessError, OSError):
        return []


def _zip_gerbers(out_dir, files):
    """Zip a set of gerber files in out_dir into an in-memory archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            p = os.path.join(out_dir, f)
            if os.path.isfile(p):
                zf.write(p, f)
    buf.seek(0)
    return buf.getvalue()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8088)
