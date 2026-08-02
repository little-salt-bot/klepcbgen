"""Tests for klepcbgen core generator."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from klepcbgenmod import (
    KLEPCBGenerator, GeneratorOptions, parse_kle_json,
    min_lines_for_keys, CONTROLLER_PINS,
)

ROOT = os.path.dirname(os.path.dirname(__file__))
EXAMPLE = os.path.join(ROOT, "example_layout.json")


def _kle():
    with open(EXAMPLE) as f:
        return json.load(f)


class TestParseKleJson(unittest.TestCase):
    """parse_kle_json must tolerate KLE raw-data quirks: JS-style unquoted keys,
    single-quoted strings, and the outer [ ] wrapper often being omitted."""

    def test_strict_json(self):
        self.assertEqual(parse_kle_json('[["A","B"]]'), [["A", "B"]])

    def test_unquoted_keys_json5(self):
        # {x:0.25} and {h:2} have unquoted keys -- invalid strict JSON
        data = parse_kle_json('[["7","8",{h:2},"+"]]')
        self.assertEqual(data[0][2], {"h": 2})

    def test_missing_outer_brackets(self):
        # KLE raw data often omits the top-level [ ]
        data = parse_kle_json('["A","B"],\n["C","D"]')
        self.assertEqual(data, [["A", "B"], ["C", "D"]])

    def test_single_quoted_strings(self):
        data = parse_kle_json("['Num Lock','/']")
        self.assertEqual(data, ["Num Lock", "/"])

    def test_realistic_kle_paste(self):
        # The exact style of KLE raw-data paste that previously failed: JS keys,
        # missing outer wrapper, HTML block string.
        kle = """["Num Lock","/","*","-",{x:0.25,f:4,w:14,h:5,d:true},"<h5><b>Getting Started</b></h5>"],
[{f:3},"7\\nHome","8\\n\\u2191",{h:2},"+"],
["4\\n\\u2190","5","6\\n\\u2192"]"""
        data = parse_kle_json(kle)
        self.assertEqual(len(data), 3)
        self.assertEqual(data[0][4], {"x": 0.25, "f": 4, "w": 14, "h": 5, "d": True})
        self.assertEqual(data[1][3], {"h": 2})

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            parse_kle_json("this is not a layout at all")


class TestMinLines(unittest.TestCase):
    def test_min_lines(self):
        self.assertEqual(min_lines_for_keys(1), 2)
        self.assertEqual(min_lines_for_keys(2), 3)
        self.assertEqual(min_lines_for_keys(3), 3)
        self.assertEqual(min_lines_for_keys(78), 13)
        self.assertEqual(min_lines_for_keys(82), 14)
        self.assertEqual(min_lines_for_keys(91), 14)
        self.assertEqual(min_lines_for_keys(92), 15)


class TestOptions(unittest.TestCase):
    def test_validation(self):
        GeneratorOptions().validate()
        with self.assertRaises(ValueError):
            GeneratorOptions(key_pitch=-1).validate()
        with self.assertRaises(ValueError):
            GeneratorOptions(key_footprint="bogus").validate()
        with self.assertRaises(ValueError):
            GeneratorOptions(diode_footprint="bogus").validate()
        with self.assertRaises(ValueError):
            GeneratorOptions(controller="bogus").validate()
        with self.assertRaises(ValueError):
            GeneratorOptions(firmware_type="bogus").validate()


class TestGenerator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.workdir = os.path.join(self.tmp, "out")
        os.makedirs(self.workdir)
        self.kle_path = os.path.join(self.tmp, "layout.json")
        with open(self.kle_path, "w") as f:
            json.dump(_kle(), f)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _generate(self, **kwargs):
        opts = GeneratorOptions(**kwargs)
        gen = KLEPCBGenerator(opts)
        gen.generate_kicadproject(self.kle_path, self.workdir)
        return gen

    def test_generates_all_files(self):
        gen = self._generate(matrixfile=os.path.join(self.tmp, "matrix.json"))
        base = os.path.join(self.workdir, "out")
        self.assertTrue(os.path.exists(base + ".kicad_pcb"))
        self.assertTrue(os.path.exists(base + ".sch"))
        self.assertTrue(os.path.exists(base + ".pro"))
        # firmware default both
        fw = os.path.join(self.workdir, "firmware")
        self.assertTrue(os.path.isfile(os.path.join(fw, "config.h")))
        self.assertTrue(os.path.isfile(os.path.join(fw, "keymap.c")))
        self.assertTrue(os.path.isfile(os.path.join(fw, "default.keymap")))

    def test_matrix_lines_and_pairs(self):
        gen = self._generate()
        self.assertEqual(gen.keyboard.matrix_lines, 14)
        pairs = [tuple(gen.keyboard.matrix_pairs[n]) for n in gen.keyboard.matrix_pairs]
        self.assertEqual(len(set(pairs)), len(pairs))
        for a, b in pairs:
            self.assertLess(a, b)

    def test_origin_zero(self):
        gen = self._generate()
        pcb = open(os.path.join(self.workdir, "out.kicad_pcb")).read()
        m = re.search(
            r"\(module (?:Button_)?Switch_Keyboard:\S+ \(layer F\.Cu\)[^\n]*\n\s*\(at ([\d.\-]+) ([\d.\-]+)\)",
            pcb,
        )
        self.assertIsNotNone(m)
        x, y = float(m.group(1)), float(m.group(2))
        self.assertAlmostEqual(x, 0.0, places=3)
        self.assertAlmostEqual(y, 0.0, places=3)

    def test_no_stale_row_col_nets(self):
        self._generate()
        pcb = open(os.path.join(self.workdir, "out.kicad_pcb")).read()
        self.assertEqual(len(re.findall(r"/Row_|/Col_", pcb)), 0)
        sch = open(os.path.join(self.workdir, "out.sch")).read()
        self.assertEqual(len(re.findall(r"\bRow_|\bCol_", sch)), 0)

    def test_edge_cuts(self):
        self._generate(edge_radius=0)
        pcb = open(os.path.join(self.workdir, "out.kicad_pcb")).read()
        self.assertEqual(len(re.findall(r"gr_line.*Edge\.Cuts", pcb)), 4)

    def test_edge_cuts_rounded(self):
        # Rounded corners (default radius) emit a polyline with more segments.
        self._generate(edge_radius=3.0)
        pcb = open(os.path.join(self.workdir, "out.kicad_pcb")).read()
        self.assertGreater(len(re.findall(r"gr_line.*Edge\.Cuts", pcb)), 4)
        # The outline must still be closed: first and last points coincide.
        segs = [
            (float(a), float(b), float(c), float(d))
            for a, b, c, d in re.findall(
                r"gr_line \(start ([\d.\-]+) ([\d.\-]+)\) \(end ([\d.\-]+) ([\d.\-]+)\).*?Edge\.Cuts",
                pcb,
            )
        ]
        self.assertGreater(len(segs), 4)
        first = (segs[0][0], segs[0][1])
        last = (segs[-1][2], segs[-1][3])
        self.assertAlmostEqual(first[0], last[0], places=6)
        self.assertAlmostEqual(first[1], last[1], places=6)

    def test_no_edge_cuts(self):
        self._generate(edge_cuts=False)
        pcb = open(os.path.join(self.workdir, "out.kicad_pcb")).read()
        self.assertEqual(len(re.findall(r"gr_line.*Edge\.Cuts", pcb)), 0)

    def test_footprint_selection(self):
        self._generate(key_footprint="choc", diode_footprint="0603")
        pcb = open(os.path.join(self.workdir, "out.kicad_pcb")).read()
        self.assertIn("SW_Kailh_Choc", pcb)
        self.assertIn("D_0603", pcb)

    def test_key_pitch(self):
        self._generate(key_pitch=18.0)
        pcb = open(os.path.join(self.workdir, "out.kicad_pcb")).read()
        # Parse all switch (x, y) positions, group by row (rounded y), and
        # check the first row's consecutive 1u keys are spaced exactly 18mm.
        ats = re.findall(
            r"\(module (?:Button_)?Switch_Keyboard:\S+ \(layer F\.Cu\)[^\n]*\n\s*\(at ([\d.\-]+) ([\d.\-]+)",
            pcb,
        )
        rows = {}
        for x, y in ats:
            rows.setdefault(round(float(y)), []).append(float(x))
        first_row = min(rows, key=lambda r: (abs(r), r))
        xs = sorted(set(rows[first_row]))
        self.assertAlmostEqual(xs[0], 0.0, places=2)
        # First key row (F-keys) is all 1u starting at origin: spacing is pitch.
        self.assertAlmostEqual(xs[1] - xs[0], 18.0, places=2)
        self.assertAlmostEqual(xs[2] - xs[1], 18.0, places=2)

    def test_controller_pin_mapping(self):
        gen = self._generate(controller="rp2040")
        fw_cfg = open(os.path.join(self.workdir, "firmware", "config.h")).read()
        self.assertIn("GP0", fw_cfg)
        self.assertIn("GP13", fw_cfg)

    def test_controller_limit(self):
        # A huge layout needing more lines than atmega32u4 provides should fail
        with self.assertRaises(ValueError):
            opts = GeneratorOptions(controller="atmega32u4")
            # fake: assign a keyboard needing > 18 lines
            gen = KLEPCBGenerator(opts)
            gen.keyboard.keys = [object() for _ in range(500)]
            gen.assign_duplex_matrix()

    def test_controller_anchor_no_collisions(self):
        # The resolved controller anchor must not overlap any switch/diode
        # footprint, for every controller.
        for controller in ("atmega32u4", "promicro", "rp2040"):
            gen = self._generate(controller=controller)
            anchor = gen.controller_anchor_resolved()
            cols = gen.controller_collisions(anchor)
            self.assertEqual(cols, [], f"{controller} collides: {cols}")
            # anchor must sit on the board (positive, within reason)
            x, y, w, h = anchor
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)

    def test_controller_collision_detection(self):
        # Force a colliding anchor and confirm the checker reports it.
        gen = KLEPCBGenerator(GeneratorOptions())
        gen.read_kle_json(self.kle_path)
        gen.assign_duplex_matrix()
        # Overlap the controller region with the switch matrix area.
        sx0, sy0, sx1, sy1 = gen._switch_bbox()
        fake_anchor = (sx0, sy0, sx1 - sx0, 10)  # covers the top switch strip
        cols = gen.controller_collisions(fake_anchor)
        self.assertGreater(len(cols), 0)
        # Every collision is a labeled footprint rectangle.
        for label, box in cols:
            self.assertIn(label[0], ("K", "D"))
            self.assertEqual(len(box), 4)

    def test_matrix_file(self):
        mpath = os.path.join(self.tmp, "matrix.json")
        self._generate(matrixfile=mpath)
        mf = json.load(open(mpath))
        self.assertEqual(mf["matrix_type"], "duplex")
        self.assertEqual(mf["matrix_lines"], 14)
        self.assertEqual(mf["num_keys"], 82)

    def test_library_tables_generated(self):
        # Every controller output must carry project-local lib tables plus a
        # .pro that lists exactly the stock symbol libraries it references.
        for controller in ("atmega32u4", "rp2040", "promicro"):
            gen = self._generate(controller=controller)
            for f in ("sym-lib-table", "fp-lib-table"):
                p = os.path.join(self.workdir, f)
                self.assertTrue(os.path.exists(p),
                                f"{controller} missing {f}")
            pro = os.path.join(self.workdir, "out.pro")
            pro_txt = open(pro).read()
            libs = KLEPCBGenerator.schematic_libs(controller)
            self.assertIn("[eeschema/libraries]", pro_txt)
            for i, lib in enumerate(libs, 1):
                self.assertIn(f"LibName{i}={lib}", pro_txt,
                              f"{controller} missing lib {lib}")
            # No lib beyond the declared set should be listed.
            n = len(libs)
            self.assertNotIn(f"LibName{n+1}=", pro_txt,
                             f"{controller} has extra libs")

    def test_schematic_libs_exact(self):
        # atmega32u4 has the MCU + mechanical, rp2040 the module, promicro only
        # generic base libs.
        self.assertEqual(KLEPCBGenerator.schematic_libs("atmega32u4")[-1],
                         "Mechanical")
        self.assertIn("MCU_Microchip_ATmega",
                      KLEPCBGenerator.schematic_libs("atmega32u4"))
        self.assertIn("MCU_Module", KLEPCBGenerator.schematic_libs("rp2040"))
        self.assertNotIn("MCU_Module",
                         KLEPCBGenerator.schematic_libs("promicro"))


if __name__ == "__main__":
    unittest.main()
