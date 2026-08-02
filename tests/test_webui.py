"""Tests for the klepcbgen web UI (FastAPI app)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from fastapi.testclient import TestClient

from webui import app

ROOT = os.path.dirname(os.path.dirname(__file__))
EXAMPLE = os.path.join(ROOT, "example_layout.json")


def _kle_str():
    with open(EXAMPLE) as f:
        return json.dumps(json.load(f))


class TestWebUI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_index_serves_html(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])
        body = r.text
        # Professional UI markers
        self.assertIn("Load example layout", body)
        self.assertIn("Preview", body)
        self.assertIn("Keyboard Layout Editor (KLE) JSON", body)
        # Controller / footprint options present
        self.assertIn("RP2040", body)
        self.assertIn("Kailh Choc", body)
        self.assertIn("SOD-123", body)
        # No leftover placeholder braces from f-string escaping
        self.assertNotIn("{{controller_opts}}", body)

    def test_generate_valid_layout(self):
        payload = {
            "kle": _kle_str(),
            "controller": "rp2040",
            "key_footprint": "cherry_mx",
            "diode_footprint": "0805",
            "key_pitch": 19.05,
            "edge_margin": 5.0,
            "do_routing": True,
            "edge_cuts": True,
            "firmware_type": "both",
        }
        r = self.client.post("/generate", json=payload)
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["keyboard"], "JB82")
        self.assertEqual(data["num_keys"], 82)
        self.assertEqual(data["matrix_lines"], 14)
        self.assertIn("download", data)
        self.assertIn("thumbnail", data)

    def test_generate_missing_kle(self):
        r = self.client.post("/generate", json={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("No KLE JSON", r.json()["detail"])

    def test_generate_invalid_json(self):
        r = self.client.post("/generate", json={"kle": "this is not json"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Could not parse KLE", r.json()["detail"])

    def test_download_and_thumb(self):
        payload = {
            "kle": _kle_str(),
            "controller": "rp2040",
            "key_footprint": "cherry_mx",
            "diode_footprint": "0805",
            "key_pitch": 19.05,
            "edge_margin": 5.0,
            "do_routing": True,
            "edge_cuts": True,
            "firmware_type": "both",
        }
        data = self.client.post("/generate", json=payload).json()
        d = data["download"].split("d=")[1]

        # Thumbnail is SVG
        tr = self.client.get(f"/thumb?d={d}")
        self.assertEqual(tr.status_code, 200)
        self.assertIn("image/svg+xml", tr.headers["content-type"])
        self.assertIn(b"<svg", tr.content)

        # Download is a zip containing the KiCad files + matrix.json
        dr = self.client.get(f"/download?d={d}")
        self.assertEqual(dr.status_code, 200)
        self.assertEqual(dr.headers["content-type"], "application/zip")
        import io
        import zipfile
        zf = zipfile.ZipFile(io.BytesIO(dr.content))
        names = zf.namelist()
        self.assertTrue(any(n.endswith(".kicad_pcb") for n in names))
        self.assertIn("matrix.json", names)
        # The download must be the complete project, not just the KiCad dir:
        # the viewer/thumbnail outputs are included alongside it.
        for extra in ("matrix.json", "preview.svg", "board.glb",
                      "front.svg", "back.svg", "layout.json",
                      "gerbers.zip", "plate.dxf", "plate.svg"):
            self.assertIn(extra, names)
        # Individual gerber files ship under gerbers/ for fabrication.
        self.assertTrue(any(n.startswith("gerbers/") for n in names))

    def test_plate_generated_and_drives_edge_cuts(self):
        # Plate generation must produce plate.dxf in the download and its
        # border must drive the PCB Edge.Cuts outline (not the raw switch bbox).
        from webui import GeneratorOptions, KLEPCBGenerator
        import tempfile, shutil, os
        import plategen
        tmp = tempfile.mkdtemp()
        try:
            kle_path = os.path.join(tmp, "layout.json")
            with open(kle_path, "w") as f:
                f.write(_kle_str())
            gen = KLEPCBGenerator(GeneratorOptions(plate_enabled=True))
            gen.generate_kicadproject(kle_path, os.path.join(tmp, "kb"))
            plate = getattr(gen, "_plate", None)
            self.assertIsNotNone(plate, "plate was not generated")
            self.assertGreater(len(plate.cutouts), 0)
            self.assertIsNotNone(plate.border)
            # The edge cuts must match the plate border in X.
            pcb = open(os.path.join(tmp, "kb", "kb.kicad_pcb")).read()
            import re
            xs = [float(m) for m in re.findall(r"gr_line \(start ([\d.-]+) ", pcb)]
            if xs:
                self.assertAlmostEqual(min(xs), plate.min_x, places=0)
            # DXF export is valid R12.
            dxf = plategen.to_dxf(plate)
            self.assertIn("AC1009", dxf)
            self.assertIn("LWPOLYLINE", dxf)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_generate_produces_gerbers(self):
        # When kicad-cli is available, the generate response must include a
        # gerbers URL, the /gerbers endpoint serves a zip, and the bundled
        # GerberViewer asset is reachable for the embedded preview.
        payload = {
            "kle": _kle_str(),
            "controller": "rp2040",
            "key_footprint": "cherry_mx",
            "diode_footprint": "0805",
            "key_pitch": 19.05,
            "edge_margin": 5.0,
            "do_routing": True,
            "edge_cuts": True,
            "firmware_type": "both",
        }
        data = self.client.post("/generate", json=payload).json()
        self.assertIn("gerbers", data)
        if data["gerbers"] is not None:
            d = data["download"].split("d=")[1]
            gr = self.client.get(f"/gerbers?d={d}")
            self.assertEqual(gr.status_code, 200)
            self.assertEqual(gr.headers["content-type"], "application/zip")
            self.assertGreater(len(gr.content), 100)
            import io
            import zipfile
            zf = zipfile.ZipFile(io.BytesIO(gr.content))
            names = zf.namelist()
            # Must contain both copper layers + a drill file for a real preview.
            self.assertTrue(any(n.endswith(".gtl") for n in names), names)
            self.assertTrue(any(n.endswith(".gbl") for n in names), names)
            self.assertTrue(any(n.endswith(".drl") for n in names), names)
        # The GerberViewer bundle is hosted for the embedded iframe preview.
        r = self.client.get("/static/gerberviewer/index.html")
        self.assertEqual(r.status_code, 200)
        self.assertIn("gerberviewer", r.text)
        self.assertIn("assets/", r.text)

    def test_generate_produces_3d_viewer(self):
        # When kicad-cli is available, the generate response must include a
        # viewer3d URL and the /glb endpoint must serve a real GLB.
        from webui import _export_glb, _STATIC_DIR  # noqa
        payload = {
            "kle": _kle_str(),
            "controller": "rp2040",
            "key_footprint": "cherry_mx",
            "diode_footprint": "0805",
            "key_pitch": 19.05,
            "edge_margin": 5.0,
            "do_routing": True,
            "edge_cuts": True,
            "firmware_type": "both",
        }
        data = self.client.post("/generate", json=payload).json()
        self.assertIn("viewer3d", data)
        if data["viewer3d"] is not None:
            d = data["download"].split("d=")[1]
            gr = self.client.get(f"/glb?d={d}")
            self.assertEqual(gr.status_code, 200)
            self.assertEqual(gr.headers["content-type"], "model/gltf-binary")
            self.assertGreater(len(gr.content), 100)
            # glTF magic header
            self.assertEqual(gr.content[:4], b"glTF")

    def test_static_assets_served(self):
        # three.js viewer assets must be reachable (self-contained UI).
        for path in ["three.module.js", "GLTFLoader.module.js",
                     "OrbitControls.module.js", "viewer.js",
                     "utils/BufferGeometryUtils.js"]:
            r = self.client.get(f"/static/{path}")
            self.assertEqual(r.status_code, 200, path)
            self.assertGreater(len(r.content), 0, path)


if __name__ == "__main__":
    unittest.main()
