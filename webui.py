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
  #pcb3d {{ width: 100%; height: 100%; display: block; }}
  #preview .placeholder {{ color: var(--muted); text-align: center; position: absolute; inset: 0; display: grid; place-items: center; }}
  #preview .placeholder svg {{ margin-bottom: 8px; opacity: .5; }}
  #status {{
    margin-top: 14px; font-size: 13px; line-height: 1.6;
    display: none; border-radius: 7px; padding: 10px 12px;
  }}
  #status.ok {{ display: block; background: rgba(55,196,122,.1); color: var(--ok); border: 1px solid rgba(55,196,122,.3); }}
  #status.err {{ display: block; background: rgba(255,92,92,.1); color: var(--err); border: 1px solid rgba(255,92,92,.3); }}
  #status.loading {{ display: block; background: var(--panel-2); color: var(--muted); border: 1px solid var(--border); }}
  #status a {{ color: inherit; font-weight: 700; }}
  .meta {{ display: flex; flex-wrap: wrap; gap: 16px; margin-top: 8px; font-size: 13px; }}
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

      <label class="check"><input id="routing" type="checkbox" checked> Auto-route traces</label>
      <label class="check"><input id="edgecuts" type="checkbox" checked> Board outline (Edge.Cuts)</label>

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
        <canvas id="pcb3d" style="width:100%;height:100%;display:none"></canvas>
        <div class="placeholder" id="placeholder">
          <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <rect x="3" y="5" width="18" height="14" rx="2"/>
            <path d="M3 9h18M7 5v14M17 5v14"/>
          </svg><br>
          Generate a board to see a live 3D PCB preview here.
        </div>
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

<script type="importmap">
{{
  "imports": {{
    "three": "/static/three.module.js",
    "three/addons/": "/static/"
  }}
}}
</script>
<script type="module" src="/static/viewer.js"></script>
<script>
  const example = {example};

  function loadExample() {{
    document.getElementById('kle').value = JSON.stringify(example, null, 2);
    document.getElementById('kle').focus();
    setStatus('Example layout loaded. Click Generate.', 'ok');
  }}

  function setStatus(msg, kind) {{
    const st = document.getElementById('status');
    st.className = kind || '';
    st.innerHTML = msg;
  }}

  async function generate() {{
    const btn = document.getElementById('genbtn');
    const preview = document.getElementById('preview');
    const canvas = document.getElementById('pcb3d');
    const placeholder = document.getElementById('placeholder');
    btn.disabled = true;
    setStatus('<span class="spinner"></span>Generating board…', 'loading');
    // Keep the canvas + placeholder elements alive (show3D needs them later).
    // Show a spinner overlay without clobbering the DOM.
    placeholder.style.display = 'grid';
    placeholder.innerHTML = '<span class="spinner"></span>';
    canvas.style.display = 'none';

    const body = {{
      kle: document.getElementById('kle').value,
      controller: document.getElementById('controller').value,
      key_footprint: document.getElementById('keyfp').value,
      diode_footprint: document.getElementById('diodfp').value,
      key_pitch: parseFloat(document.getElementById('pitch').value),
      edge_margin: parseFloat(document.getElementById('margin').value),
      edge_radius: parseFloat(document.getElementById('radius').value),
      do_routing: document.getElementById('routing').checked,
      edge_cuts: document.getElementById('edgecuts').checked,
      firmware_type: document.getElementById('firmware').value,
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
      if (data.viewer3d) {{
        window.show3D(data.viewer3d);
      }} else {{
        // fallback: flat SVG thumbnail if 3D export unavailable
        placeholder.style.display = 'grid';
        placeholder.innerHTML = '<img src="' + data.thumbnail + '" alt="PCB preview">';
      }}
    }} catch (e) {{
      setStatus('', '');
      placeholder.innerHTML = '<div class="errorbox">Network error: ' + esc(String(e)) + '</div>';
      placeholder.style.display = 'grid';
    }} finally {{
      btn.disabled = false;
    }}
  }}

  function esc(s) {{
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
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
        controller=payload.get("controller", "atmega32u4"),
        edge_margin=float(payload.get("edge_margin", 3.0)),
        edge_cuts=bool(payload.get("edge_cuts", True)),
        edge_radius=float(payload.get("edge_radius", 3.0)),
        do_routing=bool(payload.get("do_routing", True)),
        firmware_type=payload.get("firmware_type", "both"),
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
        controller=options.controller,
        edge_margin=options.edge_margin,
        edge_cuts=options.edge_cuts,
        edge_radius=options.edge_radius,
        do_routing=options.do_routing,
        matrixfile=matrix_path,
        firmware_type=options.firmware_type,
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

    zipbuf = io.BytesIO()
    with zipfile.ZipFile(zipbuf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(outname):
            for fn in files:
                full = os.path.join(root, fn)
                arc = os.path.relpath(full, outname)
                zf.write(full, arc)
        if os.path.exists(matrix_path):
            zf.write(matrix_path, "matrix.json")
    zipbuf.seek(0)
    zip_data = zipbuf.getvalue()

    kb = gen.keyboard
    return {
        "keyboard": kb.name,
        "num_keys": len(kb.keys),
        "matrix_lines": kb.matrix_lines,
        "download": f"/download?d={workdir}",
        "thumbnail": f"/thumb?d={workdir}",
        "viewer3d": f"/glb?d={workdir}" if glb_ok else None,
        "zip_bytes": len(zip_data),
    }


@app.get("/download")
async def download(d: str):
    outname = os.path.join(d, "kb")
    matrix_path = os.path.join(d, "matrix.json")
    zipbuf = io.BytesIO()
    with zipfile.ZipFile(zipbuf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(outname):
            for fn in files:
                full = os.path.join(root, fn)
                arc = os.path.relpath(full, outname)
                zf.write(full, arc)
        if os.path.exists(matrix_path):
            zf.write(matrix_path, "matrix.json")
    zipbuf.seek(0)
    return Response(
        zipbuf.getvalue(),
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8088)
