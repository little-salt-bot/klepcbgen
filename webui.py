"""Web UI for klepcbgen: paste KLE JSON, pick options, download the generated
KiCad project + matrix file + firmware, with a live PCB thumbnail.

Run with:
    uvicorn webui:app --host 0.0.0.0 --port 8000
or:
    python3 webui.py
"""
import io
import json
import os
import shutil
import tempfile
import zipfile

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from klepcbgenmod import (
    KLEPCBGenerator, GeneratorOptions,
    CONTROLLERS, KEY_FOOTPRINTS, DIODE_FOOTPRINTS,
)
from render import render_pcb_svg

app = FastAPI(title="klepcbgen Web UI")


def _index_html():
    controller_opts = "".join(
        f'<option value="{k}">{v}</option>' for k, v in CONTROLLERS.items()
    )
    key_opts = "".join(
        f'<option value="{k}">{v}</option>' for k, v in KEY_FOOTPRINTS.items()
    )
    diode_opts = "".join(
        f'<option value="{k}">{v}</option>' for k, v in DIODE_FOOTPRINTS.items()
    )
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>klepcbgen</title>
<style>
  body {{ font-family: sans-serif; margin: 2rem; background: #111; color: #eee; }}
  .wrap {{ max-width: 720px; margin: auto; }}
  textarea {{ width: 100%; height: 220px; background: #000; color: #0f0; font: 12px monospace;
             border: 1px solid #333; padding: .5rem; }}
  label {{ display: block; margin: .75rem 0 .25rem; font-weight: bold; }}
  select, input {{ padding: .4rem; background: #222; color: #eee; border: 1px solid #444; }}
  button {{ margin-top: 1rem; padding: .6rem 1.2rem; font-size: 1rem; cursor: pointer; }}
  #thumb {{ margin-top: 1rem; background: #000; border: 1px solid #333; }}
  #thumb svg {{ max-width: 100%; height: auto; }}
  #status {{ margin-top: .5rem; color: #9f9; }}
</style></head><body><div class="wrap">
<h1>klepcbgen — Keyboard PCB generator</h1>
<label>Paste KLE JSON</label>
<textarea id="kle" placeholder='Paste your Keyboard Layout Editor raw data (JSON) here...'></textarea>
<label>Controller</label>
<select id="controller">{controller_opts}</select>
<label>Key switch footprint</label>
<select id="keyfp">{key_opts}</select>
<label>Diode footprint</label>
<select id="diodfp">{diode_opts}</select>
<label>Key pitch (mm)</label>
<input id="pitch" type="number" step="0.01" value="19.05">
<label>Edge margin (mm)</label>
<input id="margin" type="number" step="0.1" value="5.0">
<label><input id="routing" type="checkbox" checked> Auto-route traces</label>
<label><input id="edgecuts" type="checkbox" checked> Board outline (Edge.Cuts)</label>
<label>Firmware</label>
<select id="firmware">
  <option value="both">QMK + ZMK</option>
  <option value="qmk">QMK</option>
  <option value="zmk">ZMK</option>
  <option value="none">None</option>
</select>
<div><button onclick="generate()">Generate</button></div>
<div id="status"></div>
<div id="thumb"></div>
</div>
<script>
async function generate() {{
  const body = {{
    kle: document.getElementById('kle').value,
    controller: document.getElementById('controller').value,
    key_footprint: document.getElementById('keyfp').value,
    diode_footprint: document.getElementById('diodfp').value,
    key_pitch: parseFloat(document.getElementById('pitch').value),
    edge_margin: parseFloat(document.getElementById('margin').value),
    do_routing: document.getElementById('routing').checked,
    edge_cuts: document.getElementById('edgecuts').checked,
    firmware_type: document.getElementById('firmware').value,
  }};
  const st = document.getElementById('status');
  st.textContent = 'Generating...';
  try {{
    const r = await fetch('/generate', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(body)
    }});
    if (!r.ok) {{ const e = await r.json(); st.textContent = 'Error: ' + (e.detail||e); return; }}
    const data = await r.json();
    st.innerHTML = 'Generated <b>' + data.keyboard + '</b> — ' + data.num_keys + ' keys, ' +
      data.matrix_lines + ' matrix lines. <a href="' + data.download + '">Download zip</a>';
    const thumb = document.getElementById('thumb');
    thumb.innerHTML = '<img src="' + data.thumbnail + '" alt="PCB preview">';
  }} catch (e) {{ st.textContent = 'Error: ' + e; }}
}}
</script>
</body></html>""")


@app.get("/", response_class=HTMLResponse)
async def index():
    return _index_html()


@app.post("/generate")
async def generate(payload: dict):
    kle = payload.get("kle", "").strip()
    if not kle:
        raise HTTPException(400, "No KLE JSON provided")
    try:
        kle_data = json.loads(kle)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"Invalid KLE JSON: {e}")

    options = GeneratorOptions(
        key_pitch=float(payload.get("key_pitch", 19.05)),
        key_footprint=payload.get("key_footprint", "cherry_mx"),
        diode_footprint=payload.get("diode_footprint", "0805"),
        controller=payload.get("controller", "atmega32u4"),
        edge_margin=float(payload.get("edge_margin", 5.0)),
        edge_cuts=bool(payload.get("edge_cuts", True)),
        do_routing=bool(payload.get("do_routing", True)),
        firmware_type=payload.get("firmware_type", "both"),
    )
    try:
        options.validate()
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Write KLE JSON to a temp file, generate project, build zip + thumbnail
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
        do_routing=options.do_routing,
        matrixfile=matrix_path,
        firmware_type=options.firmware_type,
    )
    gen = KLEPCBGenerator(opts)
    try:
        gen.generate_kicadproject(kle_path, outname)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Thumbnail SVG
    pcb_path = os.path.join(outname, "kb.kicad_pcb")
    svg = render_pcb_svg(pcb_path)
    thumb_name = "preview.svg"
    thumb_path = os.path.join(workdir, thumb_name)
    with open(thumb_path, "w") as f:
        f.write(svg)

    # Build downloadable zip
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
        "zip_bytes": len(zip_data),
    }


@app.get("/download")
async def download(d: str):
    # Rebuild zip for the given workdir (kept on disk)
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
