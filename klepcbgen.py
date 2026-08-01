import argparse

from klepcbgenmod import KLEPCBGenerator, GeneratorOptions, CONTROLLERS, KEY_FOOTPRINTS, DIODE_FOOTPRINTS

PROGRAM_VERSION = "2.1"


def parse_command_line_arguments():
    """ Parse the command line and check that the correct number of arguments is given """
    parser = argparse.ArgumentParser(
        prog="klepcbgen",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Utility to generate a KiCad schematic and layout of the switch matrix of \
a keyboard designed using the Keyboard Layout Editor \
(http://www.keyboard-layout-editor.com/)",
    )
    parser.add_argument(
        "-v", "--version", action="version", version="%(prog)s " + PROGRAM_VERSION
    )
    parser.add_argument(
        "-V", "--verbose", dest="verbose", action="store_true",
        help="Log details about what is going on"
    )

    parser.add_argument(
        "-o", dest="outname", required=True,
        help='The directory and base name for the output files (e.g. "id80" will result in "id80.sch" and \
                "id80.kicad_pcb" located in the "id80" subdirectory',
    )

    parser.add_argument(
        "-m", dest="matrixfile", default=None,
        help='Path to write the duplex matrix wiring as a JSON file (e.g. "matrix.json"). '
             'This file maps each key to its (matrix_a, matrix_b) duplex line pair for firmware.',
    )

    parser.add_argument(
        "--pitch", dest="key_pitch", type=float, default=19.05,
        help="Distance between switch centers in mm.",
    )

    parser.add_argument(
        "--key-footprint", dest="key_footprint", choices=sorted(KEY_FOOTPRINTS),
        default="cherry_mx",
        help="Keyswitch footprint to use.",
    )

    parser.add_argument(
        "--diode-footprint", dest="diode_footprint", choices=sorted(DIODE_FOOTPRINTS),
        default="0805",
        help="Diode footprint to use.",
    )

    parser.add_argument(
        "--controller", dest="controller", choices=sorted(CONTROLLERS),
        default="atmega32u4",
        help="Target controller (affects control circuit + matrix pin mapping).",
    )

    parser.add_argument(
        "--edge-margin", dest="edge_margin", type=float, default=5.0,
        help="Board outline margin (mm) around the outermost keyswitch footprints.",
    )

    parser.add_argument(
        "--no-edge-cuts", dest="edge_cuts", action="store_false",
        help="Do not emit a board outline (Edge.Cuts).",
    )

    parser.add_argument(
        "-n", dest="routing", action="store_false",
        help='Do not add traces connecting matrix lines',
    )

    parser.add_argument(
        "--firmware", dest="firmware_type", choices=["none", "qmk", "zmk", "both"],
        default="both",
        help="Generate firmware source for the selected controller from the matrix.",
    )

    parser.add_argument(
        "infile",
        help="A JSON file containing a keyboard layout in the KLE JSON format",
    )

    args = parser.parse_args()

    return args


# Program entry
if __name__ == "__main__":
    arguments = parse_command_line_arguments()
    options = GeneratorOptions(
        key_pitch=arguments.key_pitch,
        key_footprint=arguments.key_footprint,
        diode_footprint=arguments.diode_footprint,
        controller=arguments.controller,
        edge_margin=arguments.edge_margin,
        edge_cuts=arguments.edge_cuts,
        do_routing=arguments.routing,
        matrixfile=arguments.matrixfile,
        firmware_type=arguments.firmware_type,
    )
    kbpcbgen = KLEPCBGenerator(options)
    kbpcbgen.generate_kicadproject(arguments.infile, arguments.outname)
    kbpcbgen.keyboard.print_key_info()
