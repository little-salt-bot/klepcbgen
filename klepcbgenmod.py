"""Generate a KiCad project from a Keyboard Leyout Editor json input layout"""
import sys

import json
import datetime
import os

from dataclasses import dataclass, field
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined

try:
    import json5
except ImportError:
    json5 = None


def parse_kle_json(text):
    """Parse KLE raw-data text into a list of key rows.

    KLE's 'raw data' export is JavaScript-ish rather than strict JSON: keys may
    be unquoted ({x:0.25}, {h:2}), strings may use single quotes, and the outer
    [ ] array wrapper is often omitted. Try strict JSON first, then a lenient
    json5 parse (auto-wrapping the top-level array). Raises ValueError if the
    text can't be parsed at all.
    """
    candidates = [text, "[" + text + "]"]
    for c in candidates:
        try:
            return json.loads(c)
        except (json.JSONDecodeError, ValueError):
            pass
    if json5 is not None:
        for c in candidates:
            try:
                return json5.loads(c)
            except ValueError:
                pass
    raise ValueError("Could not parse KLE layout data (expected KLE JSON/raw data).")


# Program version
PROGRAM_VERSION = "0.3"

# Available controllers: name -> (human label, matrix lines available)
CONTROLLERS = {
    "atmega32u4": "ATmega32U4",
    "promicro": "Pro Micro (ATmega32U4)",
    "rp2040": "RP2040",
}

# Available key switch footprints
KEY_FOOTPRINTS = {
    "cherry_mx": "Cherry MX",
    "alps": "Alps/Matias",
    "choc": "Kailh Choc",
}

# Available diode footprints
DIODE_FOOTPRINTS = {
    "0805": "SMD 0805",
    "0603": "SMD 0603",
    "sod123": "SOD-123",
}

# Controller GPIO pin names, indexed by matrix line. Each controller can drive
# this many duplex lines. These map matrix line N to a physical GPIO label.
CONTROLLER_PINS = {
    "atmega32u4": [
        "PD0", "PD1", "PD2", "PD3", "PD4", "PD5", "PD6", "PD7",
        "PB0", "PB1", "PB2", "PB3", "PB4", "PB5", "PB6", "PB7",
        "PC6", "PC7",
    ],
    "promicro": [
        "D3", "D2", "D1", "D0", "D4", "C6", "D7", "E6",
        "B4", "B5", "B6", "B2", "B3", "B1", "F7", "F6",
        "F5", "F4",
    ],
    "rp2040": [
        "GP0", "GP1", "GP2", "GP3", "GP4", "GP5", "GP6", "GP7",
        "GP8", "GP9", "GP10", "GP11", "GP12", "GP13", "GP14", "GP15",
        "GP16", "GP17", "GP18", "GP19", "GP20", "GP21", "GP22", "GP26",
        "GP27", "GP28",
    ],
}

# Physical footprint footprint size (w, h in mm) of the controller + its
# support circuit, used to size the board outline around it. These are
# conservative (slightly larger than the rendered block) so edge cuts always
# clear the parts.
CONTROLLER_REGIONS = {
    "atmega32u4": (50, 48),   # bare TQFP-44 + USB/crystal/reset support block
    "promicro":   (36, 22),   # Pro Micro module via 2x12 pin header
    "rp2040":     (56, 24),   # Raspberry Pi Pico module via 2x20 pin header
}

# Gap (mm) between the switch matrix area and the controller block on the PCB.
CONTROLLER_GAP = 6.0


def controller_lines_available(controller):
    """Return how many duplex matrix lines a controller can drive."""
    return len(CONTROLLER_PINS.get(controller, []))


@dataclass
class GeneratorOptions:
    """Configurable options that control how a project is generated."""
    key_pitch: float = 19.05           # mm between switch centers
    key_footprint: str = "cherry_mx"   # KEY_FOOTPRINTS key
    diode_footprint: str = "0805"      # DIODE_FOOTPRINTS key
    controller: str = "atmega32u4"     # CONTROLLERS key
    edge_margin: float = 3.0           # mm of board outline around switch bbox
    edge_cuts: bool = True             # emit an Edge.Cuts board outline
    edge_radius: float = 3.0           # mm corner radius on the board outline
    do_routing: bool = True            # auto-route matrix lines
    matrixfile: str = None             # path to emit matrix wiring JSON
    firmware_type: str = "both"        # none|qmk|zmk|both

    def validate(self):
        if self.key_pitch <= 0:
            raise ValueError("key_pitch must be > 0")
        if self.key_footprint not in KEY_FOOTPRINTS:
            raise ValueError(
                f"Unknown key_footprint '{self.key_footprint}'. "
                f"Choose from {sorted(KEY_FOOTPRINTS)}"
            )
        if self.diode_footprint not in DIODE_FOOTPRINTS:
            raise ValueError(
                f"Unknown diode_footprint '{self.diode_footprint}'. "
                f"Choose from {sorted(DIODE_FOOTPRINTS)}"
            )
        if self.controller not in CONTROLLERS:
            raise ValueError(
                f"Unknown controller '{self.controller}'. "
                f"Choose from {sorted(CONTROLLERS)}"
            )
        if self.edge_margin < 0:
            raise ValueError("edge_margin must be >= 0")
        if self.edge_radius < 0:
            raise ValueError("edge_radius must be >= 0")
        if self.firmware_type not in ("none", "qmk", "zmk", "both"):
            raise ValueError("firmware_type must be none|qmk|zmk|both")


def min_lines_for_keys(num_keys):
    """Return the smallest number of bidirectional matrix lines M such that
       M lines can uniquely address num_keys keys via M(M-1)/2 unordered pairs.

       This is the duplex (square) matrix pin budget: with M duplex GPIO lines
       you can scan up to M(M-1)/2 keys, far fewer than the R+C of a classic
       row/column matrix.
    """
    m = 0
    while m * (m - 1) // 2 < num_keys:
        m += 1
    return m

class Keyboard:
    """Represents an entire keyboard layout with all the keys positioned and
       wired in a duplex (square) matrix. Each key is assigned a unique pair
       (matrix_a, matrix_b) of bidirectional matrix lines, a<b."""
    def __init__(self):
        self.keys = []
        self.name = ""
        self.author = ""
        self.matrix_lines = 0          # number of duplex matrix lines (GPIO)
        self.matrix_pairs = {}         # key.num -> (a, b) unique unordered pair

    def print_key_info(self):
        """ Print information for this keyboard """

        print("")
        print("Keyboard information: ")
        print("Name: " + self.name)
        print("Author: " + self.author)
        print(
            "Contains: "
            + str(len(self.keys))
            + " keys, wired in a duplex matrix using "
            + str(self.matrix_lines)
            + " lines (max "
            + str(self.matrix_lines * (self.matrix_lines - 1) // 2)
            + " keys)"
        )


class Key:
    """All required information about a single keyboard key"""
    x_unit = 0
    y_unit = 0
    width = 0
    height = 0
    rot = 0
    diodenetnum = 0
    matrix_a_netnum = 0
    matrix_b_netnum = 0
    matrix_a = 0
    matrix_b = 0
    num = 0
    legend = "<N/A>"


def unit_width_to_available_footprint(unit_width):
    """Convert a key width in standard keyboard units to the width of the kicad
       footprint to use"""
    if unit_width < 1.25:
        return "1.00"
    elif unit_width < 1.5:
        return "1.25"
    elif unit_width < 1.75:
        return "1.50"
    elif unit_width < 2:
        return "1.75"
    elif unit_width < 2.25:
        return "2.00"
    elif unit_width < 2.75:
        return "2.25"
    elif unit_width < 6.25:
        # This may not be the appropriate size for everything between 2.75 and
        # 6.25, but this is what we have
        return "2.75"

    # This may not be the appropriate size for everything >= 6.25, but this
    # is what we have
    return "6.25"

class Nets:
    """Maintains a collection of nets for use in the schematic"""
    def __init__(self):
        self.nets = []

    def number_of_nets(self):
        """Get the number of nets in the collection"""
        return len(self.nets)

    def add_net(self, net_name):
        """Add a net to the collection"""
        if not net_name in self.nets:
            self.nets.append(net_name)

        return self.get_net_num(net_name)

    def get_net_num(self, net_name):
        """Get the net number of the net with the specified name"""
        for index, name in enumerate(self.nets):
            if name == net_name:
                return index + 1

        return 0

    def get_net_name(self, index):
        """Get the name of the net with the specified net number"""
        if (index >= 0) and index < len(self.nets):
            return self.nets[index]
        else:
            return "UNKNOWN"

class KLEPCBGenerator:
    """Wrapper around the entire generator parses arguments, load json and generate kicad project"""

    def __init__(self, options=None):
        """ Set-up directories """
        self.keyboard = Keyboard()
        self.options = options or GeneratorOptions()
        self.options.validate()
        self.project_dir = Path(__file__).resolve().parent
        self.jinja_env = Environment(
            loader=FileSystemLoader([self.project_dir / "templates"]),
            undefined=StrictUndefined,
            # Strip the blank lines that Jinja block tags ({% for %}, {% if %})
            # would otherwise emit. The EESchema v4 loader rejects stray blank
            # lines inside Text Label / $Comp blocks, so control templates that
            # loop over matrix lines MUST render with no leading/trailing blanks.
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.nets = Nets()
        self._controller_nudge = 0.0
        self._controller_anchor = None

    def generate_kicadproject(self, infile, outname):
        """Generate the kicad project. Main entry point"""

        if not os.path.exists(outname):
            os.mkdir(outname)

        self.read_kle_json(infile)
        self.assign_duplex_matrix()
        self.generate_schematic(outname)
        self.generate_layout(outname)
        self.generate_project(outname)
        if self.options.matrixfile:
            self.generate_matrix_file(self.options.matrixfile)
        if self.options.firmware_type != "none":
            self.generate_firmware(outname)

    def read_kle_json(self, infile):
        """ Read the provided KLE input file and create a list of all the keyswitches that should
            be on the board """

        print("Reading input file '" + infile + "' ...")

        if infile == "-":
            kle_json = parse_kle_json(sys.stdin.read())
        else:
            with open(infile, "r", encoding="utf-8") as read_file:
                kle_json = parse_kle_json(read_file.read())

        # First create a list of switches, each with its own X,Y coordinate
        current_x = 0.0
        current_y = 0.0
        key_num = 0
        for row in kle_json:
            if isinstance(row, list):
                # Default keysize is 1x1
                key_width = 1
                key_height = 1
                # Extract all keys in a row
                for item in row:
                    if isinstance(item, dict):
                        for key, value in item.items():
                            if key == "x":
                                current_x += value
                            elif key == "y":
                                current_y += value
                            elif key == "w":
                                key_width = value
                            elif key == "h":
                                key_height = value
                    elif isinstance(item, str):
                        new_key = Key()
                        new_key.num = key_num
                        new_key.x_unit = current_x + key_width / 2.0
                        new_key.y_unit = current_y + key_height / 2.0
                        if item == "":
                            new_key.legend = "Blank"
                        elif item == " ":
                            new_key.legend = "Space"
                        else:
                            new_key.legend = item

                        ## Perform some escaping on the legend text to satisfy KiCad
                        new_key.legend = new_key.legend.replace('\n', ",")
                        new_key.legend = new_key.legend.replace('~', '~~')
                        new_key.legend = new_key.legend.replace('\\', '\\\\')
                        new_key.legend = new_key.legend.replace('"', '\\\"')
                        
                        new_key.width = key_width
                        new_key.height = key_height
                        self.keyboard.keys.append(new_key)
                        current_x += key_width
                        key_num += 1
                        key_width = 1
                        key_height = 1
                    else:
                        print("Found unexpected JSON element (", item, "). Exiting")
                        exit()
                current_y += 1
                current_x = 0
            else:
                # Found the meta-info block.
                if "name" in row:
                    self.keyboard.name = row["name"]
                if "author" in row:
                    self.keyboard.author = row["author"]

    def assign_duplex_matrix(self):
        """ Assign each key a unique unordered pair (a, b), a<b, of bidirectional
            matrix lines. M lines are chosen such that M(M-1)/2 >= number of keys,
            so every key maps to its own (a,b) combination (duplex/square matrix).
            The assignment is deterministic and independent of physical position.
        """

        print("Assigning duplex matrix lines ... ")

        num_keys = len(self.keyboard.keys)
        m = min_lines_for_keys(num_keys)
        avail = controller_lines_available(self.options.controller)
        if m > avail:
            raise ValueError(
                f"Layout needs {m} matrix lines but controller "
                f"'{self.options.controller}' only provides {avail}. "
                f"Choose a controller with more GPIO or a smaller layout."
            )
        self.keyboard.matrix_lines = m

        # Iterate pairs in the order (0,1),(0,2),...,(0,M-1),(1,2),... so each
        # key is simply the n-th pair in lexicographic order.
        pair_gen = (
            (a, b) for a in range(m) for b in range(a + 1, m)
        )
        for key_index, (a, b) in zip(range(num_keys), pair_gen):
            key = self.keyboard.keys[key_index]
            key.matrix_a = a
            key.matrix_b = b
            self.keyboard.matrix_pairs[key.num] = (a, b)

    def generate_matrix_file(self, outfile):
        """ Write the duplex matrix wiring as a generic, self-describing JSON file
            that firmware (QMK, ZMK, custom) can consume. Each key is mapped to its
            (matrix_a, matrix_b) line pair.
        """

        print("Writing matrix wiring to '" + outfile + "' ...")

        rows = []
        for key in self.keyboard.keys:
            rows.append(
                {
                    "key": key.num,
                    "legend": key.legend,
                    "matrix_a": key.matrix_a,
                    "matrix_b": key.matrix_b,
                    "diode_net": "D" + str(key.num),
                }
            )

        matrix = {
            "name": self.keyboard.name,
            "author": self.keyboard.author,
            "matrix_type": "duplex",
            "matrix_lines": self.keyboard.matrix_lines,
            "max_keys": self.keyboard.matrix_lines
            * (self.keyboard.matrix_lines - 1)
            // 2,
            "num_keys": len(rows),
            "notes": "Duplex (square) matrix: each key connects matrix_a and "
            "matrix_b via a diode (pad1->lineA, pad2->diode->lineB). "
            "All lines are bidirectional GPIO.",
            "keys": rows,
        }

        with open(outfile, "w", newline="\n", encoding="utf-8") as out_file:
            json.dump(matrix, out_file, indent=2, ensure_ascii=False)
            out_file.write("\n")
    def place_schematic_components(self):
        """Place schematic components determined by the layout(keyswitches and diodes)"""
        switch_tpl = self.jinja_env.get_template("schematic/keyswitch.tpl")

        component_count = 0
        components_section = ""

        # Place keyswitches and diodes
        for key in self.keyboard.keys:
            placement_x = int(600 + key.x_unit * 800)
            placement_y = int(800 + key.y_unit * 500)

            components_section = components_section + switch_tpl.render(
                num=component_count,
                legend=key.legend,
                x=placement_x,
                y=placement_y,
                matrix_a=key.matrix_a,
                matrix_b=key.matrix_b,
                keywidth=unit_width_to_available_footprint(key.width),
            )
            components_section = components_section + "\n"
            component_count += 1

        return components_section

    def generate_schematic(self, outname):
        """ Generate schematic """

        print("Generating schematic ...")

        components = self.place_schematic_components()
        control_circuit = self.jinja_env.get_template(
            f"schematic/control_{self.options.controller}.tpl"
        )
        schematic = self.jinja_env.get_template("schematic/schematic.tpl")
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        comment = (
            "Generated by " + os.path.basename(sys.argv[0]) + " v" + PROGRAM_VERSION
        )
        with open(
                outname + "/" + os.path.basename(os.path.normpath(outname)) + ".sch", "w+", newline="\n", encoding="utf-8"
        ) as out_file:
            out_file.write(
                schematic.render(
                    components=components,
                    controlcircuit=control_circuit.render(
                        matrix_lines=self.keyboard.matrix_lines,
                        controller=self.options.controller,
                    ),
                    title=self.keyboard.name,
                    author=self.keyboard.author,
                    date=now,
                    comment=comment,
                )
            )

    def place_layout_components(self):
        """ Place footprint components, traces and vias """
        switch = self.jinja_env.get_template(
            f"layout/keyswitch_{self.options.key_footprint}.tpl"
        )
        diode = self.jinja_env.get_template(
            f"layout/diode_{self.options.diode_footprint}.tpl"
        )
        component_count = 0
        components_section = ""

        key_pitch = self.options.key_pitch

        # Load templates for netnames
        diodetpl = self.jinja_env.get_template("layout/diodenetname.tpl")
        matrixtpl = self.jinja_env.get_template("layout/matrixnetname.tpl")
        tracetpl = self.jinja_env.get_template("layout/trace.tpl")
        viatpl = self.jinja_env.get_template("layout/via.tpl")

        # Origin is zeroed so the top-left key's switch center sits at PCB (0,0).
        # The first key has x_unit/y_unit = 0.5 (its center), so shift by half a
        # key pitch to bring that center exactly onto the origin.
        key_origin_x = -0.5 * key_pitch
        key_origin_y = -0.5 * key_pitch

        # Diode placement offsets relative to the switch center, in mm.
        # These scale with key pitch so larger pitches keep the diode in place.
        s = key_pitch / 19.05
        diode_offset = [-5.8 * s, 8.89 * s]                  # Position of the diode
        diode_trace_offsets = [[-5.8 * s, 2.54 * s], [-5.8 * s, 7.77 * s]]
        # (matrix_b pad of the diode, used for chaining traces)

        for key in self.keyboard.keys:
            # Place switch
            ref_x = key_origin_x + key.x_unit * key_pitch
            ref_y = key_origin_y + key.y_unit * key_pitch
            components_section = (
                components_section
                + switch.render(
                    num=component_count,
                    legend=key.legend,
                    x=ref_x,
                    y=ref_y,
                    diodenetnum=key.diodenetnum,
                    diodenetname=diodetpl.render(diodenum=key.num),
                    matrix_a_netnum=key.matrix_a_netnum,
                    matrix_a_netname=matrixtpl.render(line=key.matrix_a),
                    keywidth=unit_width_to_available_footprint(key.width),
                    keyfootprint=self.options.key_footprint,
                )
                + "\n"
            )
            # Place diode
            diode_x = ref_x + diode_offset[0]
            diode_y = ref_y + diode_offset[1]
            components_section = (
                components_section
                + diode.render(
                    num=component_count,
                    x=diode_x,
                    y=diode_y,
                    diodenetnum=key.diodenetnum,
                    diodenetname=diodetpl.render(diodenum=key.num),
                    matrix_b_netnum=key.matrix_b_netnum,
                    matrix_b_netname=matrixtpl.render(line=key.matrix_b),
                    diodefootprint=self.options.diode_footprint,
                )
                + "\n"
            )

            # Connect diode to switch
            components_section = (
                components_section
                + tracetpl.render(
                    x1=ref_x + diode_trace_offsets[0][0],
                    y1=ref_y + diode_trace_offsets[0][1],
                    x2=ref_x + diode_trace_offsets[1][0],
                    y2=ref_y + diode_trace_offsets[1][1],
                    layer="B.Cu",
                    netnum=key.diodenetnum,
                )
                + "\n"
            )

            component_count += 1

        if self.options.do_routing:
            # For each duplex matrix line, route a clean orthogonal (Manhattan)
            # path through the contact points of all keys that use it. Each key
            # contributes two contact points:
            #   - its switch pad1 contact on line A (matrix_a)
            #   - its diode matrix_b pad on line B (matrix_b)
            # Orthogonal routing keeps traces DRC-clean (no diagonal shorts) and
            # uses a via at each direction change to hop between layers when a
            # straight run would cross another net.
            for line in range(self.keyboard.matrix_lines):
                # Collect contact points (x,y) and the netnum for this line
                contacts = []
                for key in self.keyboard.keys:
                    ref_x = key_origin_x + key.x_unit * key_pitch
                    ref_y = key_origin_y + key.y_unit * key_pitch
                    if key.matrix_a == line:
                        # switch pad1 contact (center of switch)
                        contacts.append(
                            (ref_x, ref_y, key.matrix_a_netnum)
                        )
                    if key.matrix_b == line:
                        # diode matrix_b pad
                        contacts.append(
                            (
                                ref_x + diode_offset[0],
                                ref_y + diode_offset[1] + 0.94 * s,
                                key.matrix_b_netnum,
                            )
                        )

                # Chain the contact points with L-shaped (Manhattan) segments.
                # Alternate layer per leg and drop a via at the bend so crossing
                # nets use different copper layers and stay DRC-clean.
                prev = None
                leg_layer = "B.Cu"
                for contact in contacts:
                    if prev is not None:
                        cx, cy, cnet = contact
                        px, py, pnet = prev
                        # First leg: horizontal
                        components_section = (
                            components_section
                            + tracetpl.render(
                                x1=px, y1=py,
                                x2=cx, y2=py,
                                layer=leg_layer,
                                netnum=pnet,
                            )
                            + "\n"
                        )
                        # Via at the bend
                        components_section = (
                            components_section
                            + viatpl.render(x=cx, y=py, netnum=pnet)
                            + "\n"
                        )
                        # Second leg: vertical
                        components_section = (
                            components_section
                            + tracetpl.render(
                                x1=cx, y1=py,
                                x2=cx, y2=cy,
                                layer=leg_layer,
                                netnum=pnet,
                            )
                            + "\n"
                        )
                        # Alternate layer for the next leg
                        leg_layer = "F.Cu" if leg_layer == "B.Cu" else "B.Cu"
                    prev = contact

        return components_section, component_count

    def _switch_bbox(self):
        """Return (min_x, min_y, max_x, max_y) of the switch footprints (half a
        key pitch around each switch center) in PCB mm."""
        key_pitch = self.options.key_pitch
        key_origin_x = -0.5 * key_pitch
        key_origin_y = -0.5 * key_pitch
        xs = [key_origin_x + k.x_unit * key_pitch for k in self.keyboard.keys]
        ys = [key_origin_y + k.y_unit * key_pitch for k in self.keyboard.keys]
        return (
            min(xs) - 0.5 * key_pitch,
            min(ys) - 0.5 * key_pitch,
            max(xs) + 0.5 * key_pitch,
            max(ys) + 0.5 * key_pitch,
        )

    def _shift_control_region(self, control_text, cx, cy):
        """Translate a rendered control-circuit block so its module bbox center
        lands on (cx, cy).

        Only module top-level `(at x y)` lines and global `(segment ...)` tracks
        are shifted. Module-internal geometry (fp_line/pad/fp_text coordinates
        relative to each module origin) stays untouched, so the block's shape is
        preserved and only its on-board position changes."""
        import re as _re

        def _shift_at(m):
            def _repl(sub):
                x = float(sub.group(1)) + cx
                y = float(sub.group(2)) + cy
                suffix = sub.group(3)
                return f"(at {x:.6g} {y:.6g}{suffix})"
            return _re.sub(r"\(at (-?[\d.]+) (-?[\d.]+)([^\n]*)\)", _repl, m.group(0))

        # Match the module header line plus the immediately following (at ...) line.
        text = _re.sub(
            r"\(module ([^\n]*\(layer (?:B|F)\.Cu\)[^\n]*\n\s*\(at [^\n]*\))",
            _shift_at, control_text,
        )

        def _shift_seg(m):
            def _repl(sub):
                return (f"(start {float(sub.group(1)) + cx:.6g} "
                        f"{float(sub.group(2)) + cy:.6g}) "
                        f"(end {float(sub.group(3)) + cx:.6g} "
                        f"{float(sub.group(4)) + cy:.6g})")
            return _re.sub(
                r"\(start (-?[\d.]+) (-?[\d.]+)\) \(end (-?[\d.]+) (-?[\d.]+)\)",
                _repl, m.group(0),
            )

        return _re.sub(r"\(segment [^\n]*", _shift_seg, text)

    def _control_bbox_center(self, control_text):
        """Return (cx, cy) = bounding-box center of the control block's module
        placements in template coordinate space (the (at ...) right after each
        module header)."""
        import re as _re
        xs, ys = [], []
        for m in _re.finditer(
            r"\(module [^\n]*\(layer (?:B|F)\.Cu\)[^\n]*\n\s*\(at (-?[\d.]+) (-?[\d.]+)",
            control_text,
        ):
            xs.append(float(m.group(1)))
            ys.append(float(m.group(2)))
        if not xs:
            return 0.0, 0.0
        return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0

    def _key_bboxes(self):
        """Return a list of (label, x0, y0, x1, y1) footprint rectangles for every
        switch and diode on the board, in PCB mm. Used to detect controller
        placement collisions."""
        key_pitch = self.options.key_pitch
        key_origin_x = -0.5 * key_pitch
        key_origin_y = -0.5 * key_pitch
        s = key_pitch / 19.05
        diode_offset = [-5.8 * s, 8.89 * s]
        # Conservative footprint boxes: a key's body is width x height in units;
        # the diode (0805/0603/SOD-123) is ~3 x 2 mm including its pads.
        diode_half_w = 1.5 * s
        diode_half_h = 1.0 * s
        boxes = []
        for key in self.keyboard.keys:
            cx = key_origin_x + key.x_unit * key_pitch
            cy = key_origin_y + key.y_unit * key_pitch
            hw = (key.width * key_pitch) / 2.0
            hh = (key.height * key_pitch) / 2.0
            boxes.append((f"K{key.num}", cx - hw, cy - hh, cx + hw, cy + hh))
            dx = cx + diode_offset[0]
            dy = cy + diode_offset[1]
            boxes.append((
                f"D{key.num}", dx - diode_half_w, dy - diode_half_h,
                dx + diode_half_w, dy + diode_half_h,
            ))
        return boxes

    def controller_collisions(self, anchor=None):
        """Return a list of (label, [x0,y0,x1,y1]) footprints that the controller
        + support region overlaps. Empty list means the placement is clean.

        anchor is the (x, y, w, h) controller region; defaults to controller_anchor()."""
        ax, ay, aw, ah = anchor or self.controller_anchor()
        reg = (ax, ay, ax + aw, ay + ah)
        collisions = []
        for label, x0, y0, x1, y1 in self._key_bboxes():
            # AABB overlap test
            if not (x1 < reg[0] or x0 > reg[2] or y1 < reg[1] or y0 > reg[3]):
                collisions.append((label, [x0, y0, x1, y1]))
        return collisions

    def find_clear_controller_anchor(self):
        """Return an (x, y, w, h) controller anchor that doesn't collide with any
        switch or diode footprint.

        Starting from the nominal anchor (below the switch matrix), the controller
        block is nudged further below the matrix until it clears every footprint.
        Returns None if a clear spot can't be found (practically impossible given
        unlimited downward space)."""
        step = 2.0  # mm to nudge per attempt
        for _ in range(1000):  # hard ceiling; downward space is unbounded in reality
            anchor = self.controller_anchor()
            if not self.controller_collisions(anchor):
                return anchor
            # nudge the block further below the switch matrix
            self._controller_nudge = self._controller_nudge + step
        return None

    def controller_anchor(self):
        """Return (x, y, w, h) for the controller + support region on the PCB.

        The block is placed centered on the switch matrix's X extent and below
        its bottom edge (larger Y), separated by CONTROLLER_GAP. Returning the
        anchor lets both the layout templates (to position parts) and the edge
        cuts (to include the block in the outline) agree on placement."""
        min_x, min_y, max_x, max_y = self._switch_bbox()
        w, h = CONTROLLER_REGIONS[self.options.controller]
        cx = (min_x + max_x) / 2.0
        x = cx - w / 2.0
        # _controller_nudge (mm) moves the block further below the switch matrix
        # when auto-collision-finding needs to push it clear of footprints.
        nudge = getattr(self, "_controller_nudge", 0.0)
        y = max_y + CONTROLLER_GAP + nudge
        return x, y, w, h

    def resolve_controller_anchor(self):
        """Compute and cache a controller anchor with no footprint collisions.

        Nudges the block below the switch matrix until it clears every switch
        and diode. The resolved anchor is cached so the edge cuts and the layout
        placement both use the same final position. Returns (x, y, w, h)."""
        step = 2.0  # mm per nudge attempt
        for _ in range(1000):
            anchor = self.controller_anchor()
            if not self.controller_collisions(anchor):
                self._controller_anchor = anchor
                return anchor
            self._controller_nudge = self._controller_nudge + step
        # Fall back to the nominal anchor (should never happen).
        self._controller_anchor = self.controller_anchor()
        return self._controller_anchor

    def controller_anchor_resolved(self):
        """Return the collision-free controller anchor, resolving it if needed."""
        if self._controller_anchor is None:
            return self.resolve_controller_anchor()
        return self._controller_anchor

    def compute_edge_cuts(self):
        """Compute the board outline (Edge.Cuts) based on the bounding box of
           all keyswitch footprints plus a configurable margin, with optional
           rounded corners.

           Corners are approximated with a short polyline of gr_line segments
           rather than gr_arc. The generated board file is KiCad 5.x format
           (version 20171130); kicad-cli 9.x fails to load gr_arc in that older
           format, so polylines are the version-safe way to round corners.

           Returns a string of (gr_line) entries, or empty string if edge_cuts
           is disabled.
        """
        import math
        if not self.options.edge_cuts:
            return ""
        if not self.keyboard.keys:
            return ""

        key_pitch = self.options.key_pitch
        margin = self.options.edge_margin
        radius = self.options.edge_radius

        min_x, min_y, max_x, max_y = self._switch_bbox()

        # Include the controller + support block in the outline so it always
        # lands on the board, regardless of layout size. Uses the collision-free
        # (resolved) anchor so the outline clears the controller parts.
        cx, cy, cw, ch = self.controller_anchor_resolved()
        min_x = min(min_x, cx)
        min_y = min(min_y, cy)
        max_x = max(max_x, cx + cw)
        max_y = max(max_y, cy + ch)

        # Add configurable margin
        x0 = min_x - margin
        y0 = min_y - margin
        x1 = max_x + margin
        y1 = max_y + margin

        # Cap the corner radius at half the smaller side so it never overlaps.
        r = max(0.0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
        w = 0.1

        if r <= 0:
            # No rounding: four straight lines (square outline).
            return "\n".join([
                f"  (gr_line (start {x0} {y0}) (end {x1} {y0}) (angle 0) (layer Edge.Cuts) (width {w}))",
                f"  (gr_line (start {x1} {y0}) (end {x1} {y1}) (angle 0) (layer Edge.Cuts) (width {w}))",
                f"  (gr_line (start {x1} {y1}) (end {x0} {y1}) (angle 0) (layer Edge.Cuts) (width {w}))",
                f"  (gr_line (start {x0} {y1}) (end {x0} {y0}) (angle 0) (layer Edge.Cuts) (width {w}))",
            ]) + "\n"

        # Number of straight segments per quarter-circle corner.
        ARC_SEGS = 8

        def corner_points(cx, cy, from_angle_deg, to_angle_deg):
            """Return [cx,cy]+ list of points tracing a 90-deg arc from
               from_angle to to_angle (0 deg = +X, CCW)."""
            pts = []
            steps = ARC_SEGS
            for i in range(1, steps + 1):
                a = math.radians(from_angle_deg +
                                 (to_angle_deg - from_angle_deg) * i / steps)
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
            return pts

        lines = []
        # Walk the outline clockwise from the bottom-left straight edge start.
        # Each corner arc is centered INSET by r from the box corner so the arc
        # cuts the sharp corner inward (proper rounding) instead of bulging
        # outward past the edge.
        outline = []
        outline.append((x0 + r, y0))                       # bottom-left inner
        outline.append((x1 - r, y0))                       # bottom-right inner
        outline += corner_points(x1 - r, y0 + r, -90, 0)   # bottom-right corner
        outline.append((x1, y1 - r))                       # right-top inner
        outline += corner_points(x1 - r, y1 - r, 0, 90)    # top-right corner
        outline.append((x0 + r, y1))                       # top-left inner
        outline += corner_points(x0 + r, y1 - r, 90, 180)  # top-left corner
        outline.append((x0, y0 + r))                       # left-bottom inner
        outline += corner_points(x0 + r, y0 + r, 180, 270) # bottom-left corner

        # Emit line segments connecting consecutive outline points.
        for i in range(len(outline) - 1):
            ax, ay = outline[i]
            bx, by = outline[i + 1]
            lines.append(
                f"  (gr_line (start {ax} {ay}) (end {bx} {by}) (angle 0) (layer Edge.Cuts) (width {w}))"
            )
        # Close the loop back to the first point.
        ax, ay = outline[-1]
        bx, by = outline[0]
        lines.append(
            f"  (gr_line (start {ax} {ay}) (end {bx} {by}) (angle 0) (layer Edge.Cuts) (width {w}))"
        )
        return "\n".join(lines) + "\n"


    def define_nets(self):
        """Define all the nets for this layout"""
        self.nets.add_net("GND")
        self.nets.add_net("VCC")

        # The bare ATmega32U4 carries its own support circuit (USB, crystal,
        # reset, decoupling), so those nets only exist for that controller.
        # The Pro Micro and RP2040 are pre-built modules: we only wire matrix
        # lines and power/reset to their headers.
        if self.options.controller == "atmega32u4":
            self.nets.add_net('"Net-(C6-Pad1)"')
            self.nets.add_net('"Net-(C7-Pad1)"')
            self.nets.add_net('"Net-(C8-Pad1)"')
            self.nets.add_net('"Net-(J1-Pad4)"')
            self.nets.add_net('"Net-(J1-Pad3)"')
            self.nets.add_net('"Net-(J1-Pad2)"')
            self.nets.add_net('"Net-(R1-Pad1)"')
            self.nets.add_net('"Net-(R2-Pad1)"')
            self.nets.add_net('"Net-(R3-Pad1)"')
            self.nets.add_net('"Net-(R4-Pad2)"')
            self.nets.add_net('"Net-(U1-Pad42)"')
            self.nets.add_net('/Reset')

        matrix_tpl = self.jinja_env.get_template("layout/matrixnetname.tpl")
        # Declare one net per duplex matrix line, since the control circuit
        # template refers to them
        for line in range(self.keyboard.matrix_lines):
            self.nets.add_net(matrix_tpl.render(line=line))

        diode_tpl = self.jinja_env.get_template("layout/diodenetname.tpl")
        for diode_num in range(len(self.keyboard.keys)):
            self.nets.add_net(diode_tpl.render(diodenum=diode_num))

    def create_layout_nets(self):
        """ Create the list of nets in the layout """
        addnets = ""
        declarenets = ""

        # Create a declaration and addition for each net
        for netnum in range(0, 1 + self.nets.number_of_nets()):
            netname = self.nets.get_net_name(netnum)
            declarenets = (
                declarenets + "  (net " + str(netnum + 1) + " " + netname + ")\n"
            )
            addnets = addnets + "    (add_net " + netname + ")\n"

        # make each key in the board aware in which matrix line and diode net it resides
        matrixtpl = self.jinja_env.get_template("layout/matrixnetname.tpl")
        for key in self.keyboard.keys:
            key.matrix_a_netnum = self.nets.get_net_num(
                matrixtpl.render(line=key.matrix_a)
            )
            key.matrix_b_netnum = self.nets.get_net_num(
                matrixtpl.render(line=key.matrix_b)
            )

        diodetpl = self.jinja_env.get_template("layout/diodenetname.tpl")
        for diodenum in range(len(self.keyboard.keys)):
            diodenetname = diodetpl.render(diodenum=diodenum)
            self.keyboard.keys[diodenum].diodenetnum = self.nets.get_net_num(
                diodenetname
            )

        nets = self.jinja_env.get_template("layout/nets.tpl")

        return nets.render(netdeclarations=declarenets, addnets=addnets)

    def generate_layout(self, outname):
        """ Generate layout """

        print("Generating PCB layout ...")

        self.define_nets()
        nets = self.create_layout_nets()

        components, numcomponents = self.place_layout_components()
        edge_cuts = self.compute_edge_cuts()

        layout = self.jinja_env.get_template("layout/layout.tpl")
        controlcircuit = self.jinja_env.get_template(
            f"layout/control_{self.options.controller}.tpl"
        )
        control_text = controlcircuit.render(nets=self.nets, startnet=0,
                                              matrix_lines=self.keyboard.matrix_lines)

        # Position the control block: center its rendered bbox on the resolved
        # (collision-free) anchor.
        cx, cy = self._control_bbox_center(control_text)
        ax, ay, _w, _h = self.controller_anchor_resolved()
        # Anchor (ax, ay) is the top-left of the controller region; center it.
        ax_c = ax + _w / 2.0
        ay_c = ay + _h / 2.0
        control_text = self._shift_control_region(control_text, ax_c - cx, ay_c - cy)

        layout_output_file_path = outname + "/" + os.path.basename(os.path.normpath(outname)) + ".kicad_pcb"
        with open(layout_output_file_path, "w+", newline="\n", encoding="utf-8") as out_file:
            out_file.write(
                layout.render(
                    modules=components,
                    nummodules=numcomponents,
                    nets=nets,
                    numnets=self.nets.number_of_nets(),
                    edgecuts=edge_cuts,
                    controlcircuit=control_text,
                )
            )

    def generate_project(self, outname):
        """Generate the project file"""
        prj = self.jinja_env.get_template("kicadproject.tpl")
        with open(
                outname + "/" + os.path.basename(os.path.normpath(outname)) + ".pro", "w+", newline="\n", encoding="utf-8"
        ) as out_file:
            out_file.write(prj.render())

    def generate_firmware(self, outname):
        """Generate firmware source files from the duplex matrix wiring and the
           selected controller. Writes QMK and/or ZMK files into a 'firmware'
           subdirectory of the project output.
        """
        from firmware import generate_qmk, generate_zmk

        fw_dir = os.path.join(outname, "firmware")
        os.makedirs(fw_dir, exist_ok=True)
        ft = self.options.firmware_type
        if ft in ("qmk", "both"):
            generate_qmk(fw_dir, self.keyboard, self.options)
        if ft in ("zmk", "both"):
            generate_zmk(fw_dir, self.keyboard, self.options)
        print("Firmware written to '" + fw_dir + "'")

