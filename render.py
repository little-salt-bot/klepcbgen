import re


def render_pcb_svg(pcb_path):
    with open(pcb_path) as f:
        data = f.read()

    # Edge cuts (board outline): ordered gr_line segments emitted clockwise
    # around the outline (rounded corners are already a polyline of lines).
    edge_lines = [
        (float(x1), float(y1), float(x2), float(y2))
        for x1, y1, x2, y2 in re.findall(
            r"\(gr_line \(start ([\d.\-]+) ([\d.\-]+)\) \(end ([\d.\-]+) ([\d.\-]+)\).*?Edge\.Cuts",
            data,
        )
    ]

    # Switch footprints (centers): the (at x y) line holds plain "x y"
    switch_pts = []
    for at in re.findall(r"\(module Button_Switch_Keyboard[^\n]*\n\s*\(at ([^)]+)\)", data):
        parts = at.split()
        if len(parts) >= 2:
            switch_pts.append((float(parts[0]), float(parts[1])))

    # Traces (segments) as copper lines
    traces = re.findall(
        r"\(segment \(start ([\d.\-]+) ([\d.\-]+)\) \(end ([\d.\-]+) ([\d.\-]+)\) \(width [\d.\-]+\) \(layer (F\.Cu|B\.Cu)\)",
        data,
    )

    # Compute bounds (include switches + edge cuts)
    all_x = [p[0] for p in switch_pts]
    all_y = [p[1] for p in switch_pts]
    for x1, y1, x2, y2 in edge_lines:
        all_x += [x1, x2]
        all_y += [y1, y2]
    if not all_x:
        all_x, all_y = [0], [0]
    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    pad = 10
    min_x -= pad; max_x += pad
    min_y -= pad; max_y += pad
    W = max_x - min_x
    H = max_y - min_y

    # SVG scale: map board mm to a ~600px image
    scale = 600 / max(W, H)

    def tx(x):
        return (x - min_x) * scale

    def ty(y):
        return (max_y - y) * scale  # flip Y for SVG

    parts = []
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W*scale}" height="{H*scale}" viewBox="0 0 {W*scale} {H*scale}">')
    parts.append(f'<rect width="100%" height="100%" fill="#111"/>')

    # Board outline (fill + stroke): chain line segments in order.
    if edge_lines:
        path = f"M {tx(edge_lines[0][0])} {ty(edge_lines[0][1])} "
        for x1, y1, x2, y2 in edge_lines:
            path += f"L {tx(x2)} {ty(y2)} "
        parts.append(f'<path d="{path}Z" fill="#1a1a1a" stroke="#0f0" stroke-width="2"/>')

    # Copper traces (B.Cu dark green, F.Cu bright green)
    for x1, y1, x2, y2, layer in traces:
        color = "#0a8a0a" if layer == "B.Cu" else "#22e622"
        parts.append(
            f'<line x1="{tx(float(x1))}" y1="{ty(float(y1))}" x2="{tx(float(x2))}" '
            f'y2="{ty(float(y2))}" stroke="{color}" stroke-width="2" opacity="0.7"/>'
        )

    # Switch footprints as green squares
    for x, y in switch_pts:
        half = 9.5 * scale
        parts.append(
            f'<rect x="{tx(x)-half}" y="{ty(y)-half}" width="{2*half}" height="{2*half}" '
            f'fill="none" stroke="#4a4" stroke-width="1.5"/>'
        )

    parts.append("</svg>")
    return "".join(parts)


if __name__ == "__main__":
    import sys
    print(render_pcb_svg(sys.argv[1]))
