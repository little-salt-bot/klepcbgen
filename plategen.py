"""Switch-plate generation for klepcbgen.

A Python port of Keebio's kb-plategen (https://github.com/keebio/kb-plategen,
MIT) core geometry. Given the parsed keyboard keys, it emits a switch plate
DXF (R12) containing:

  - a rounded-rectangle switch cutout centered on every key
  - stabilizer cutouts for keys >= 2 units wide/tall
  - an outer plate border sized from the switch bounding box + a margin

The plate is generated FIRST (it's more stable than the footprints) and its
outer border is what drives the PCB Edge.Cuts outline, so the plate and board
agree exactly.

Pure Python / no external dependencies: geometry is computed with plain math
and the DXF is written by hand (R12 ASCII).
"""

import math


# --- cutout types (mirrors kb-plategen SwitchCutoutType) -----------------
MX = "MX"
ALPS = "Alps"
MX_ALPS = "MX/Alps"
SUPPORT_PLATE = "Support Plate"
CUSTOM_RECTANGLE = "Custom Rectangle"
CHOC_V2 = "Choc V2"

# switch cutout WxH (mm) per type, mirroring KeyCutouts.switchCutout()
CUTOUT_SIZE = {
    MX: (14.0, 14.0),
    ALPS: (15.5, 12.8),
    SUPPORT_PLATE: (14.0, 14.0),  # + center notch (5x16) unioned
    CUSTOM_RECTANGLE: (14.0, 14.0),  # overridable via custom width/height
    CHOC_V2: (14.2, 14.0),
    MX_ALPS: (14.0, 14.0),  # union of MX + Alps
}

# --- stabilizer styles ----------------------------------------------------
STAB_NORMAL = "Normal"
STAB_LARGE = "Large"
STAB_3MM = "3mm Plate"
STAB_3MM_SCREW = "3mm Plate for Screw-ins"
STAB_5MM = "5mm Plate"
STAB_CHOC = "Choc V1"
STAB_CHOCV2 = "Choc V2"
STAB_GATERON = "Gateron LP"
STAB_CUSTOM = "Custom Rectangles"
STAB_SINGLE = "Single Rectangle"

# (w, h, heightOffset) per stabilizer style — mirrors loadStyle()
STAB_STYLE = {
    STAB_LARGE: (7.0, 15.0, -0.5),
    STAB_3MM: (7.0, 16.0, -1.0),
    STAB_3MM_SCREW: (7.0, 19.5, 0.75),
    STAB_5MM: (7.0, 20.15, -0.325),
    STAB_GATERON: (6.0, 12.5, -0.45),
    STAB_NORMAL: (6.75, 14.0, -1.0),
    STAB_CHOC: (0.0, 0.0, 0.0),
    STAB_CHOCV2: (0.0, 0.0, 0.0),
}


class PlateConfig:
    """All knobs for plate generation, mirroring kb-plategen's params."""

    def __init__(self):
        self.cutout_type = MX
        self.cutout_radius = 0.0        # switch cutout corner fillet (mm)
        self.cutout_width = 14.0        # for Custom Rectangle
        self.cutout_height = 14.0
        self.stab_type = STAB_LARGE
        self.stab_radius = 0.0
        self.stab_width = 7.0           # custom stab cutout
        self.stab_height = 15.0
        self.stab_offset = -0.5
        self.h_spacing = 19.05          # key center spacing X (mm)
        self.v_spacing = 19.05          # key center spacing Y (mm)
        self.kerf = 0.0                 # laser kerf compensation (mm)
        self.combine_overlaps = False
        self.margin = 5.0               # plate border margin around cutouts


def _rr_points(cx, cy, width, height, radius, kerf=0.0, n=8):
    """Points of a centered rounded rectangle (cutout) in mm.

    Width/height are the outer dims; kerf shrinks them (laser burn). The
    rectangle is centered on (cx, cy). Returns an open list of (x, y)
    vertices tracing the perimeter clockwise (caller closes it). Every
    straight edge AND corner arc is emitted, so the polygon bbox equals the
    full width/height regardless of corner radius (a common plate bug is
    dropping the straight edges, which shrinks the outline by `radius`).
    """
    w = (width - kerf) / 2.0
    h = (height - kerf) / 2.0
    r = max(0.0, min(radius, w, h))
    pts = []
    if r <= 0:
        # Plain rectangle, 4 corners (clockwise from bottom-right).
        return [(cx + w, cy + h), (cx - w, cy + h),
                (cx - w, cy - h), (cx + w, cy - h)]

    def arc(cx_, cy_, a0, a1):
        for s in range(n):
            a = math.radians(a0 + (a1 - a0) * (s + 1) / n)
            pts.append((cx_ + r * math.cos(a), cy_ + r * math.sin(a)))

    # Clockwise from bottom-right: bottom edge, BR arc, right edge, TR arc,
    # top edge, TL arc, left edge, BL arc.
    pts.append((cx + w - r, cy + h))            # bottom edge start (bottom-right inner)
    pts.append((cx - w + r, cy + h))            # bottom edge end (bottom-left inner)
    arc(cx - w + r, cy + h - r, 270, 180)       # bottom-left corner
    pts.append((cx - w, cy + h - r))            # left edge start
    pts.append((cx - w, cy - h + r))            # left edge end (top-left inner)
    arc(cx - w + r, cy - h + r, 180, 90)        # top-left corner
    pts.append((cx - w + r, cy - h))            # top edge start
    pts.append((cx + w - r, cy - h))            # top edge end (top-right inner)
    arc(cx + w - r, cy - h + r, 90, 0)          # top-right corner
    pts.append((cx + w, cy - h + r))            # right edge start
    pts.append((cx + w, cy + h - r))            # right edge end (bottom-right inner)
    arc(cx + w - r, cy + h - r, 0, -90)         # bottom-right corner
    return pts


def _stab_offsets(unit_width, stab_type):
    """Return the +/- X offsets (mm from key center) for a stabilizer pair,
    mirroring StabilizerCutout's offset table."""
    if unit_width >= 8:
        return [-66.675, 66.675]
    if unit_width >= 7:
        return [-57.15, 57.15]
    if unit_width == 6.25:
        return [-50.0, 50.0]
    if unit_width == 6:
        return [-57.15, 38.1]
    if unit_width == 5.5 and stab_type == STAB_CHOC:
        return [-38.0, 38.0]
    if unit_width >= 3:
        return [-19.05, 19.05]
    if unit_width >= 2:
        if stab_type in (STAB_CHOC, STAB_CHOCV2):
            return [-12.0, 12.0]
        return [-11.938, 11.938]
    return [0.0, 0.0]


def _stab_cutout_rect(stab_type, config, radius):
    """Return (w, h) of a single stabilizer cutout rect (before offset)."""
    if stab_type in (STAB_CHOC,):
        # Two stacked rects — approximate as one (6.3 x 6.85 @ +0.375)
        # unioned with (3.6 x 8.45 @ +4.225). For a plate cutout the union
        # is well-approximated by the wider top band.
        return (6.3, 6.85)
    if stab_type == STAB_CHOCV2:
        return (5.95, 7.95)
    if stab_type in (STAB_CUSTOM, STAB_SINGLE):
        return (config.stab_width, config.stab_height)
    return STAB_STYLE[stab_type][:2]


def _stab_height_offset(stab_type, config):
    if stab_type in (STAB_CUSTOM, STAB_SINGLE):
        return config.stab_offset
    return STAB_STYLE[stab_type][2]


class Plate:
    """A generated switch plate: list of closed polygon outlines.

    Each polygon is [(x, y), ...] in mm, switch cutouts CCW, outer border CW
    so DXF fill rules / fabrication treat cutouts as holes.
    """

    def __init__(self, config=None):
        self.config = config or PlateConfig()
        self.cutouts = []       # list of closed polygons (switch+stab holes)
        self.border = None      # outer border polygon (list of pts) or None
        self.min_x = self.min_y = 0.0
        self.max_x = self.max_y = 0.0

    def add_key(self, cx, cy, unit_width, unit_height, rotation=0.0,
                vertical=False, nub=False):
        """Add a single key's cutout(s) at key-center (cx, cy) in mm."""
        cfg = self.config
        polys = []
        # switch cutout
        r = cfg.cutout_radius
        if cfg.cutout_type == CUSTOM_RECTANGLE:
            w, h = cfg.cutout_width, cfg.cutout_height
        else:
            w, h = CUTOUT_SIZE.get(cfg.cutout_type, (14.0, 14.0))
        pts = _rr_points(cx, cy, w, h, r, cfg.kerf)
        if rotation:
            pts = _rotate_poly(pts, cx, cy, -rotation)
        polys.append(pts)

        # stabilizer cutout for >= 2u keys
        size = unit_width if unit_width >= 2 else unit_height
        if size >= 2 and cfg.stab_type != STAB_SINGLE:
            is_vertical = unit_height >= 2 and unit_width < 2
            sw, sh = _stab_cutout_rect(cfg.stab_type, cfg, r)
            soff = _stab_height_offset(cfg.stab_type, cfg)
            offs = _stab_offsets(size, cfg.stab_type)
            for ox in offs:
                if ox == 0 and len(offs) == 1:
                    # single centered stab
                    sp = _rr_points(cx, cy, sw, sh, cfg.stab_radius, cfg.kerf)
                    sp = _shift_poly(sp, 0, soff)
                else:
                    sp = _rr_points(cx + ox, cy, sw, sh, cfg.stab_radius, cfg.kerf)
                    sp = _shift_poly(sp, 0, soff)
                if is_vertical:
                    sp = _rotate_poly(sp, cx, cy, 90 if rotation >= 0 else -90)
                if rotation:
                    sp = _rotate_poly(sp, cx, cy, -rotation)
                polys.append(sp)

        for p in polys:
            self.cutouts.append(p)

    def finalize_border(self):
        """Compute the outer plate border from the union of cutout bbox +
        margin, then expand the bbox tracked for edge cuts."""
        if not self.cutouts:
            return
        xs = [x for p in self.cutouts for x, _ in p]
        ys = [y for p in self.cutouts for _, y in p]
        m = self.config.margin
        x0, y0, x1, y1 = min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m
        self.min_x, self.min_y = x0, y0
        self.max_x, self.max_y = x1, y1
        self.border = [(x1, y1), (x1, y0), (x0, y0), (x0, y1)]

    def outline_polyline(self, radius=0.0, n=8):
        """Return the outer border as a rounded-rectangle polyline (for PCB
        Edge.Cuts), optionally with filleted corners."""
        if self.border is None:
            return []
        x0, y0 = self.min_x, self.min_y
        x1, y1 = self.max_x, self.max_y
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        w = (x1 - x0) / 2
        h = (y1 - y0) / 2
        return _rr_points(cx, cy, w * 2, h * 2, radius, n=n)


# --- DXF (R12 ASCII) writer ----------------------------------------------

def _write_dxf_polyline(f, points, close=True):
    """Emit a LWPOLYLINE entity. points is a list of (x, y)."""
    f.write("  0\nLWPOLYLINE\n  8\n0\n 90\n%d\n  70\n%d\n"
            % (len(points) + (1 if close else 0), 1 if close else 0))
    for x, y in points:
        f.write(" 10\n%.6f\n 20\n%.6f\n" % (x, y))
    if close and points:
        x, y = points[0]
        f.write(" 10\n%.6f\n 20\n%.6f\n" % (x, y))


def to_dxf(plate):
    """Serialize a Plate to DXF R12 ASCII."""
    from io import StringIO
    buf = StringIO()
    buf.write("0\nSECTION\n  2\nHEADER\n  9\n$ACADVER\n  1\nAC1009\n"
              "  9\n$INSUNITS\n 70\n4\n  0\nENDSEC\n")
    buf.write("0\nSECTION\n  2\nENTITIES\n")
    # outer border first (draw on top), then cutouts
    if plate.border:
        _write_dxf_polyline(buf, plate.border, close=True)
    for poly in plate.cutouts:
        _write_dxf_polyline(buf, poly, close=True)
    buf.write("  0\nENDSEC\n  0\nEOF\n")
    return buf.getvalue()


def to_svg(plate, view_pad=10.0):
    """Render the plate as a simple SVG (for a thumbnail preview)."""
    from io import StringIO
    if not plate.cutouts and plate.border is None:
        return "<svg xmlns='http://www.w3.org/2000/svg'/>"
    x0, y0 = plate.min_x, plate.min_y
    x1, y1 = plate.max_x, plate.max_y
    w = (x1 - x0) or 1
    h = (y1 - y0) or 1
    pad = view_pad
    scale = 100.0 / max(w, h)
    def tx(px, py):
        return (px - x0) * scale + pad, (py - y0) * scale + pad
    out = StringIO()
    out.write("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 %d %d' "
              "width='100%%' height='100%%'>" % (w * scale + pad * 2, h * scale + pad * 2))
    # cutouts (holes) in light fill
    for poly in plate.cutouts:
        pts = " ".join("%.2f,%.2f" % tx(px, py) for px, py in poly)
        out.write("<polygon points='%s' fill='#263238' stroke='#70A0AF' stroke-width='1'/>" % pts)
    out.write("</svg>")
    return out.getvalue()


# --- geometry helpers -----------------------------------------------------

def _rotate_poly(pts, cx, cy, deg):
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + (x - cx) * ca - (y - cy) * sa,
             cy + (x - cx) * sa + (y - cy) * ca) for x, y in pts]


def _shift_poly(pts, dx, dy):
    return [(x + dx, y + dy) for x, y in pts]
