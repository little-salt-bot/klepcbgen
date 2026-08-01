"""Generate a KiCad project from a Keyboard Leyout Editor json input layout"""
import sys

import json
import datetime
import os

from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined

# Program version
PROGRAM_VERSION = "0.2"


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
    keyboard = Keyboard()

    def __init__(self):
        """ Set-up directories """
        self.project_dir = Path(__file__).resolve().parent
        self.jinja_env = Environment(
            loader=FileSystemLoader([self.project_dir / "templates"]),
            undefined=StrictUndefined,
        )
        self.nets = Nets()

    def generate_kicadproject(self, infile, outname, do_routing, matrixfile=None):
        """Generate the kicad project. Main entry point"""

        if not os.path.exists(outname):
            os.mkdir(outname)

        self.read_kle_json(infile)
        self.assign_duplex_matrix()
        self.generate_schematic(outname)
        self.generate_layout(outname, do_routing)
        self.generate_project(outname)
        if matrixfile:
            self.generate_matrix_file(matrixfile)

    def read_kle_json(self, infile):
        """ Read the provided KLE input file and create a list of all the keyswitches that should
            be on the board """

        print("Reading input file '" + infile + "' ...")

        if infile == "-":
            kle_json = json.load(sys.stdin)
        else:
            with open(infile, "r", encoding="utf-8") as read_file:
                kle_json = json.load(read_file)

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
        control_circuit = self.jinja_env.get_template("schematic/controlcircuit.tpl")
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
                    controlcircuit=control_circuit.render(),
                    title=self.keyboard.name,
                    author=self.keyboard.author,
                    date=now,
                    comment=comment,
                )
            )

    def place_layout_components(self, do_routing):
        """ Place footprint components, traces and vias """
        switch = self.jinja_env.get_template("layout/keyswitch.tpl")
        diode = self.jinja_env.get_template("layout/diode.tpl")
        component_count = 0
        components_section = ""

        # Load templates for netnames
        diodetpl = self.jinja_env.get_template("layout/diodenetname.tpl")
        matrixtpl = self.jinja_env.get_template("layout/matrixnetname.tpl")
        tracetpl = self.jinja_env.get_template("layout/trace.tpl")

        # Place keyswitches, diodes, vias and traces
        key_pitch = 19.05
        # Origin is zeroed so the top-left key's switch center sits at PCB (0,0).
        # The first key has x_unit/y_unit = 0.5 (its center), so shift by half a
        # key pitch to bring that center exactly onto the origin.
        key_origin_x = -0.5 * key_pitch
        key_origin_y = -0.5 * key_pitch

        # Several offsets that are relative to the 0,0 point inside the switch layout template
        diode_offset = [-5.8, 8.89]                         # Position of the diode 
        diode_trace_offsets = [[-5.8, 2.54], [-5.8, 7.77]]  # Start/end-points for the trace connecting the diode to the switch
        line_b_pad_offset = [-5.8, 9.83]                    # Position of the matrix_b pad of the diode

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

        if do_routing:
            # For each duplex matrix line, chain the contact points of all keys
            # that use it. Each key contributes two contact points:
            #   - its switch pad1 contact on line A (matrix_a)
            #   - its diode matrix_b pad on line B (matrix_b)
            # Chaining them keeps every key on a line electrically connected and
            # eventually connected to the controller pin for that line.
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
                                ref_y + diode_offset[1] + 0.94,
                                key.matrix_b_netnum,
                            )
                        )

                # Chain the contact points in sequence
                prev = None
                for contact in contacts:
                    if prev is not None:
                        components_section = (
                            components_section
                            + tracetpl.render(
                                x1=prev[0],
                                y1=prev[1],
                                x2=contact[0],
                                y2=contact[1],
                                layer="B.Cu",
                                netnum=prev[2],
                            )
                            + "\n"
                        )
                    prev = contact

        return components_section, component_count


    def define_nets(self):
        """Define all the nets for this layout"""
        self.nets.add_net("GND")
        self.nets.add_net("VCC")
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

    def generate_layout(self, outname, do_routing):
        """ Generate layout """

        print("Generating PCB layout ...")

        self.define_nets()
        nets = self.create_layout_nets()

        components, numcomponents = self.place_layout_components(do_routing)

        layout = self.jinja_env.get_template("layout/layout.tpl")
        controlcircuit = self.jinja_env.get_template("layout/controlcircuit.tpl")
        layout_output_file_path = outname + "/" + os.path.basename(os.path.normpath(outname)) + ".kicad_pcb"
        with open(layout_output_file_path, "w+", newline="\n", encoding="utf-8") as out_file:
            out_file.write(
                layout.render(
                    modules=components,
                    nummodules=numcomponents,
                    nets=nets,
                    numnets=self.nets.number_of_nets(),
                    controlcircuit=controlcircuit.render(nets=self.nets, startnet=0),
                )
            )

    def generate_project(self, outname):
        """Generate the project file"""
        prj = self.jinja_env.get_template("kicadproject.tpl")
        with open(
                outname + "/" + os.path.basename(os.path.normpath(outname)) + ".pro", "w+", newline="\n", encoding="utf-8"
        ) as out_file:
            out_file.write(prj.render())

