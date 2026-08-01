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
        self.assertIn("Invalid KLE JSON", r.json()["detail"])

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


if __name__ == "__main__":
    unittest.main()
