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
        self._generate()
        pcb = open(os.path.join(self.workdir, "out.kicad_pcb")).read()
        self.assertEqual(len(re.findall(r"gr_line.*Edge\.Cuts", pcb)), 4)

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

    def test_matrix_file(self):
        mpath = os.path.join(self.tmp, "matrix.json")
        self._generate(matrixfile=mpath)
        mf = json.load(open(mpath))
        self.assertEqual(mf["matrix_type"], "duplex")
        self.assertEqual(mf["matrix_lines"], 14)
        self.assertEqual(mf["num_keys"], 82)


if __name__ == "__main__":
    unittest.main()
