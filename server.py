"""PixelLiner MCP server.

A headless pixel-art canvas exposed over the Model Context Protocol (stdio).
The model draws into in-memory layered canvases, can look at a rendered preview,
and exports PNG files that PixelLiner opens natively. PixelLiner itself is never
modified or automated; it is only (optionally) launched with an exported file.

Dependency-free: Python 3.8+ standard library only.
"""
import base64
import json
import os
import subprocess
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from canvas import Document, Layer, Painter, PRESET_PALETTES, TRANSPARENT, to_hex  # noqa: E402
from png import decode_png, encode_png  # noqa: E402
from project import load_project, save_project  # noqa: E402
from gif import encode_gif  # noqa: E402
import art  # noqa: E402

SERVER_NAME = "pixelliner"
SERVER_VERSION = "1.0.0"
SUPPORTED_PROTOCOLS = ["2025-06-18", "2025-03-26", "2024-11-05"]

OUTPUT_DIR = os.environ.get("PIXELLINER_MCP_OUT") or os.path.join(os.path.expanduser("~"), "Documents", "PixelLinerMCP")
PIXELLINER_EXE = os.environ.get("PIXELLINER_EXE") or r"C:\Program Files\PixelLiner\PixelLiner.exe"

GRID_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789#@$%&*+=?!<>~^"


class State:
    docs = {}
    current = None


def doc_or_fail(name=None):
    key = name or State.current
    if key is None or key not in State.docs:
        raise ValueError("no canvas open; call new_canvas or open_image first")
    return State.docs[key]


def unique_name(base):
    name, i = base, 2
    while name in State.docs:
        name = f"{base}-{i}"
        i += 1
    return name


def resolve_path(path, default_name, ext=".png"):
    if not path:
        path = default_name
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(OUTPUT_DIR, path)
    if not path.lower().endswith(ext):
        path = os.path.splitext(path)[0] + ext if path.lower().endswith((".png", ".gif")) else path + ext
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def scale_rgba(w, h, data, s):
    if s == 1:
        return bytes(data)
    out = bytearray()
    stride = w * 4
    for y in range(h):
        row = data[y * stride:(y + 1) * stride]
        big = b"".join(bytes(row[i:i + 4]) * s for i in range(0, stride, 4))
        out += big * s
    return bytes(out)


def doc_summary(doc):
    lines = [f"canvas '{doc.name}' {doc.w}x{doc.h}, active layer #{doc.active} '{doc.layers[doc.active].name}', "
             f"frame {doc.frame} of {doc.nframes} (0-based)"]
    if doc.nframes > 1:
        lines.append("frame durations ms: " + ", ".join(f"{i}:{d}" for i, d in enumerate(doc.durations)))
    for i, l in enumerate(doc.layers):
        flags = [] if l.visible else ["hidden"]
        if l.reference:
            flags.append("reference: preview only, not exported")
        if l.opacity < 1:
            flags.append(f"opacity {l.opacity:.2f}")
        mark = "*" if i == doc.active else " "
        lines.append(f" {mark}#{i} {l.name}" + (f" ({', '.join(flags)})" if flags else ""))
    if doc.palette:
        lines.append("palette: " + ", ".join(f"{k}={to_hex(v)}" for k, v in doc.palette.items()))
    return "\n".join(lines)


# ---------------------------------------------------------------- rendering

def sheet_rgba(doc, frames, columns=None, gap=0, region=None, layer=None, preview=False):
    """Lay out frames left-to-right, wrapping after `columns`. Returns (w, h, rgba)."""
    x, y, fw, fh = region or (0, 0, doc.w, doc.h)
    cols = max(1, min(len(frames), int(columns or len(frames))))
    rows = (len(frames) + cols - 1) // cols
    W, H = cols * fw + (cols - 1) * gap, rows * fh + (rows - 1) * gap
    out = bytearray(W * H * 4)
    for n, f in enumerate(frames):
        flat = doc.composite((x, y, fw, fh), only_layer=layer, frame=f, include_reference=preview)
        ox, oy = (n % cols) * (fw + gap), (n // cols) * (fh + gap)
        for yy in range(fh):
            di = ((oy + yy) * W + ox) * 4
            out[di:di + fw * 4] = flat[yy * fw * 4:(yy + 1) * fw * 4]
    return W, H, out


def onion_rgba(doc, region, layer, frame, alpha=0.3):
    """Current frame with the previous frame ghosted underneath where the current frame is empty."""
    cur = doc.composite(region, only_layer=layer, frame=frame, include_reference=True)
    prev = doc.composite(region, only_layer=layer, frame=(frame - 1) % doc.nframes)
    for i in range(0, len(cur), 4):
        if cur[i + 3] == 0 and prev[i + 3]:
            cur[i:i + 3] = prev[i:i + 3]
            cur[i + 3] = int(prev[i + 3] * alpha)
    return cur


def render_preview(doc, scale=None, grid=None, region=None, layer=None, background="checker", flat=None, size=None):
    x, y, w, h = region or (0, 0, doc.w, doc.h)
    if size:
        w, h = size
    if scale is None:
        scale = max(1, min(24, 640 // max(w, h)))
    if grid is None:
        grid = scale >= 8
    if flat is None:
        flat = doc.composite((x, y, w, h), only_layer=layer, include_reference=True)
    W, H = w * scale, h * scale
    out = bytearray(W * H * 4)
    if background == "checker":
        bg1, bg2 = (236, 236, 236), (214, 214, 214)
    else:
        c = doc.color(background)
        bg1 = bg2 = c[:3]
    cell = max(4, scale)  # checker cell = one source pixel when zoomed, so it never looks like art
    for py in range(H):
        sy = py // scale
        gy = grid and (py % scale == 0)
        gy_major = grid and py % (scale * 8) == 0
        for px in range(W):
            sx = px // scale
            si = (sy * w + sx) * 4
            a = flat[si + 3] / 255.0
            bg = bg1 if ((px // cell) + (py // cell)) % 2 == 0 else bg2
            r = flat[si] * a + bg[0] * (1 - a)
            g = flat[si + 1] * a + bg[1] * (1 - a)
            b = flat[si + 2] * a + bg[2] * (1 - a)
            if grid:
                major = gy_major or px % (scale * 8) == 0
                if major:
                    r, g, b = r * 0.55 + 40, g * 0.55 + 40, b * 0.55 + 200 * 0.45
                elif gy or px % scale == 0:
                    r, g, b = r * 0.82, g * 0.82, b * 0.82
            di = (py * W + px) * 4
            out[di] = int(r)
            out[di + 1] = int(g)
            out[di + 2] = int(b)
            out[di + 3] = 255
    return encode_png(W, H, out), scale


def render_grid(doc, region=None, layer=None):
    x, y, w, h = region or (0, 0, doc.w, doc.h)
    if w > 160:
        raise ValueError("grid view is limited to 160 columns; pass x/y/width/height to view a region")
    flat = doc.composite((x, y, w, h), only_layer=layer)
    by_color = {}
    for k, v in doc.palette.items():
        if len(k) == 1 and k != "." and v not in by_color:
            by_color[v] = k
    used_chars = set(by_color.values()) | {"."}
    pool = [c for c in GRID_CHARS if c not in used_chars]
    rows = []
    legend = {}
    for yy in range(h):
        row = []
        for xx in range(w):
            i = (yy * w + xx) * 4
            c = tuple(flat[i:i + 4])
            if c[3] == 0:
                row.append(".")
                continue
            ch = by_color.get(c)
            if ch is None:
                if not pool:
                    raise ValueError("too many distinct colors for grid view; use mode 'image' or a smaller region")
                ch = pool.pop(0)
                by_color[c] = ch
            legend[ch] = c
            row.append(ch)
        rows.append("".join(row))
    tens = "".join(str(((x + i) // 10) % 10) if (x + i) % 10 == 0 else " " for i in range(w))
    ones = "".join(str((x + i) % 10) for i in range(w))
    pad = len(str(y + h - 1))
    out = [f"region x={x} y={y} w={w} h={h}  ('.' = transparent)",
           "legend: " + ", ".join(f"{k}={to_hex(v)}" for k, v in sorted(legend.items())),
           " " * (pad + 1) + tens, " " * (pad + 1) + ones]
    for j, r in enumerate(rows):
        out.append(str(y + j).rjust(pad) + " " + r)
    return "\n".join(out)


# ---------------------------------------------------------------- draw ops

def _i(op, key, default=None):
    v = op.get(key, default)
    if v is None:
        raise ValueError(f"op '{op.get('op')}' needs '{key}'")
    return int(round(float(v)))


def _pts(op, key="points"):
    pts = op.get(key)
    if not pts:
        raise ValueError(f"op '{op.get('op')}' needs '{key}'")
    return [(int(p[0]), int(p[1])) for p in pts]


def apply_op(doc, p, op):
    kind = str(op.get("op", "")).lower()
    col = (lambda key="color": doc.color(op.get(key)))
    fill = bool(op.get("fill", False))
    if kind in ("pixel", "pixels", "points"):
        if "pixels" in op:  # [[x, y, color], ...]
            for item in op["pixels"]:
                p.plot(int(item[0]), int(item[1]), doc.color(item[2] if len(item) > 2 else op.get("color")))
        else:
            c = col()
            pts = _pts(op) if "points" in op else [(_i(op, "x"), _i(op, "y"))]
            for x, y in pts:
                p.plot(x, y, c)
    elif kind == "line":
        p.line(_i(op, "x1"), _i(op, "y1"), _i(op, "x2"), _i(op, "y2"), col(), int(op.get("width", 1)))
    elif kind == "polyline":
        pts = _pts(op)
        c = col()
        for a, b in zip(pts, pts[1:]):
            p.line(a[0], a[1], b[0], b[1], c, int(op.get("width", 1)))
        if op.get("closed") and len(pts) > 2:
            p.line(pts[-1][0], pts[-1][1], pts[0][0], pts[0][1], c, int(op.get("width", 1)))
    elif kind == "rect":
        p.rect(_i(op, "x"), _i(op, "y"), _i(op, "w"), _i(op, "h"), col(), fill)
    elif kind in ("ellipse", "circle"):
        if kind == "circle" and "r" in op:
            r = _i(op, "r")
            cx, cy = _i(op, "cx"), _i(op, "cy")
            p.ellipse(cx - r, cy - r, 2 * r + 1, 2 * r + 1, col(), fill)
        else:
            p.ellipse(_i(op, "x"), _i(op, "y"), _i(op, "w"), _i(op, "h"), col(), fill)
    elif kind == "polygon":
        p.polygon(_pts(op), col(), fill)
    elif kind in ("fill", "bucket", "flood"):
        p.flood(_i(op, "x"), _i(op, "y"), col(), contiguous=op.get("contiguous", True),
                tolerance=int(op.get("tolerance", 0)), diagonal=bool(op.get("diagonal", False)))
    elif kind == "replace_color":
        p.replace_color(doc.color(op.get("from")), doc.color(op.get("to")), int(op.get("tolerance", 0)))
    elif kind == "outline":
        p.outline(col(), diagonal=bool(op.get("diagonal", False)), inside=bool(op.get("inside", False)),
                  clean=bool(op.get("clean", True)))
    elif kind == "gradient":
        cols = [doc.color(c) for c in op.get("colors", [])]
        if len(cols) < 2:
            raise ValueError("gradient needs 'colors' with at least 2 entries")
        p.gradient(_i(op, "x", 0), _i(op, "y", 0), _i(op, "w", doc.w), _i(op, "h", doc.h), cols,
                   op.get("direction", "vertical"), bool(op.get("dither", True)))
    elif kind in ("pixel_map", "map", "sprite"):
        rows = op.get("rows")
        if isinstance(rows, str):
            rows = rows.strip("\n").split("\n")
        if not rows:
            raise ValueError("pixel_map needs 'rows'")
        keymap = {k: v for k, v in doc.palette.items() if len(k) == 1}
        for k, v in (op.get("palette") or {}).items():
            keymap[k] = doc.color(v)
        p.pixel_map(_i(op, "x", 0), _i(op, "y", 0), rows, keymap, op.get("skip", "."))
    elif kind == "clear":
        if any(k in op for k in ("x", "y", "w", "h")):
            p.rect(_i(op, "x", 0), _i(op, "y", 0), _i(op, "w", doc.w), _i(op, "h", doc.h), TRANSPARENT, True)
        else:
            p.data[:] = bytes(len(p.data))
    elif kind == "flip":
        p.flip(op.get("axis", "x"))
    elif kind == "shift":
        p.shift(int(op.get("dx", 0)), int(op.get("dy", 0)), bool(op.get("wrap", False)))
    elif kind in ("mirror", "symmetrize"):
        p.mirror_copy(op.get("axis", "x"), op.get("from", "left"))
    elif kind in ART_OPS:
        ART_OPS[kind](doc, p, op)
    else:
        raise ValueError(f"unknown op '{kind}'")


# ---------------------------------------------------------------- technique ops (art.py)

def palette_colors(doc, op):
    """Colours for snapping: op['palette'] (list of colours) or the canvas palette."""
    if op.get("palette"):
        return [doc.color(c)[:3] for c in op["palette"]]
    return [v[:3] for v in doc.palette.values() if v[3] > 0]


def matcher_for(doc, op):
    snap = op.get("snap")
    cols = palette_colors(doc, op)
    if snap is None:
        snap = bool(cols)
    if snap and not cols:
        raise ValueError("snap needs a palette (canvas palette or op 'palette')")
    return art.Matcher(cols) if snap else None


def op_region(op):
    if any(k in op for k in ("x", "y", "w", "h")):
        return (int(op.get("x", 0)), int(op.get("y", 0)), int(op.get("w", 10 ** 6)), int(op.get("h", 10 ** 6)))
    return None


def _count(p, n):
    p.count += n if isinstance(n, int) else sum(n)


def op_quantize(doc, p, op):
    cols = palette_colors(doc, op)
    if not cols:
        raise ValueError("quantize needs a palette (canvas palette or op 'palette')")
    _count(p, art.quantize(doc.w, doc.h, p.data, cols, op.get("dither"), float(op.get("strength", 0.05)), op_region(op)))


def op_selout(doc, p, op):
    _count(p, art.selout(doc.w, doc.h, p.data, float(op.get("amount", 0.3)), op.get("light", "top-left"),
                         matcher_for(doc, op), op_region(op)))


def op_antialias(doc, p, op):
    _count(p, art.antialias(doc.w, doc.h, p.data, float(op.get("strength", 0.5)), matcher_for(doc, op),
                            op_region(op), int(op.get("min_run", 2))))


def op_cleanup(doc, p, op):
    _count(p, art.cleanup(doc.w, doc.h, p.data, bool(op.get("doubles", True)), bool(op.get("orphans", True)),
                          int(op.get("orphan_min", 6)), op_region(op)))


def op_shade(doc, p, op):
    targets = op.get("colors") or ([op["color"]] if op.get("color") else None)
    if not targets:
        raise ValueError("shade needs 'color' or 'colors': the flat colour(s) of the area to shade")
    if op.get("ramp"):
        ramp = [doc.color(c)[:3] for c in op["ramp"]]
    else:  # build a hue-shifted ramp around the (first) flat colour
        ramp, _ = art.make_ramp(doc.color(targets[0])[:3], int(op.get("steps", 4)), float(op.get("hue_shift", 24)))
    _count(p, art.shade(doc.w, doc.h, p.data, [doc.color(c) for c in targets], ramp, op.get("light", "top-left"),
                        float(op.get("elevation", 0.9)), float(op.get("roundness", 1.0)), float(op.get("ambient", 0.0)),
                        bool(op.get("dither", False)), op_region(op)))


def op_transform(doc, p, op):
    src_li = doc.layer_index(op["from_layer"]) if op.get("from_layer") is not None else p.li
    src_f = doc.frame_index(op["from_frame"]) if op.get("from_frame") is not None else p.frame
    src = doc.cel(src_li, src_f)
    x, y = int(op.get("x", 0)), int(op.get("y", 0))
    w, h = int(op.get("w", doc.w - x)), int(op.get("h", doc.h - y))
    px = []
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            if 0 <= xx < doc.w and 0 <= yy < doc.h:
                i = (yy * doc.w + xx) * 4
                px.append(tuple(src[i:i + 4]))
            else:
                px.append((0, 0, 0, 0))
    copy = bool(op.get("copy", src is not p.data))
    if not copy:  # move: lift the pixels off the source
        for yy in range(max(0, y), min(doc.h, y + h)):
            for xx in range(max(0, x), min(doc.w, x + w)):
                i = (yy * doc.w + xx) * 4
                src[i:i + 4] = bytes(4)
    cw, ch = w, h
    if op.get("flip_x"):
        px = [px[r * cw + (cw - 1 - c)] for r in range(ch) for c in range(cw)]
    if op.get("flip_y"):
        px = [px[(ch - 1 - r) * cw + c] for r in range(ch) for c in range(cw)]
    sc = op.get("scale")
    if sc:
        sx, sy = (sc, sc) if isinstance(sc, (int, float)) else (sc[0], sc[1])
        cw, ch, px = art.scale_sprite(cw, ch, px, float(sx), float(sy))
    if op.get("rotate"):
        cw, ch, px = art.rotate_sprite(cw, ch, px, float(op["rotate"]), bool(op.get("smooth", True)))
    cx, cy = x + w / 2.0 + int(op.get("dx", 0)), y + h / 2.0 + int(op.get("dy", 0))
    ox, oy = int(round(cx - cw / 2.0)), int(round(cy - ch / 2.0))
    for r in range(ch):
        for c in range(cw):
            v = px[r * cw + c]
            if v[3]:
                p._put(ox + c, oy + r, v)


ART_OPS = {
    "quantize": op_quantize, "selout": op_selout, "antialias": op_antialias, "aa": op_antialias,
    "cleanup": op_cleanup, "shade": op_shade, "transform": op_transform,
}


# ---------------------------------------------------------------- tool handlers

def text(s):
    return {"type": "text", "text": s}


def image(png_bytes):
    return {"type": "image", "data": base64.b64encode(png_bytes).decode("ascii"), "mimeType": "image/png"}


def maybe_preview(doc, args):
    if args.get("preview"):
        png, s = render_preview(doc)
        return [image(png)]
    return []


def apply_palette(doc, pal):
    if not pal:
        return
    if isinstance(pal, str):
        if pal not in PRESET_PALETTES:
            raise ValueError(f"unknown preset '{pal}' (presets: {list(PRESET_PALETTES)})")
        doc.load_preset(pal)
    else:
        for k, v in pal.items():
            doc.palette[str(k)] = doc.color(v)


def t_new_canvas(a):
    w, h = int(a["width"]), int(a["height"])
    name = unique_name(a.get("name") or "sprite")
    doc = Document(name, w, h)
    apply_palette(doc, a.get("palette"))
    doc.durations = [int(a.get("duration", 100))]
    if a.get("background"):
        bg = doc.color(a["background"])
        doc.layers[0].name = "Background"
        doc.layers[0].cels[0] = bytearray(bytes(bg) * (w * h))
        doc.layers.append(Layer("Layer 1", w, h))
        doc.active = 1
    for _ in range(max(1, int(a.get("frames", 1))) - 1):
        doc.add_frame(copy_from=0)  # background (if any) is copied; drawing layer starts with the same (empty) content
    doc.frame = 0
    State.docs[name] = doc
    State.current = name
    return [text("created " + doc_summary(doc))]


def t_open_image(a):
    path = os.path.expanduser(a["path"])
    with open(path, "rb") as f:
        w, h, data = decode_png(f.read())
    base = a.get("name") or os.path.splitext(os.path.basename(path))[0]
    name = unique_name(base)
    doc = Document(name, w, h)
    doc.layers[0] = Layer(os.path.basename(path), w, h, data)
    if a.get("overwrite_on_export"):
        doc.export_paths["png"] = path
    State.docs[name] = doc
    State.current = name
    return [text("opened " + doc_summary(doc))]


def t_canvas(a):
    action = a.get("action", "info")
    if action == "list":
        if not State.docs:
            return [text("no canvases open")]
        return [text("\n".join(("* " if k == State.current else "  ") + f"{k} {d.w}x{d.h}, {len(d.layers)} layers"
                               for k, d in State.docs.items()))]
    if action == "switch":
        if a.get("name") not in State.docs:
            raise ValueError(f"no canvas '{a.get('name')}'")
        State.current = a["name"]
        return [text(doc_summary(doc_or_fail()))]
    doc = doc_or_fail(a.get("name"))
    if action == "info":
        return [text(doc_summary(doc))]
    if action == "close":
        del State.docs[doc.name]
        State.current = next(iter(State.docs), None)
        return [text(f"closed '{doc.name}'")]
    if action in ("resize", "crop"):
        nw, nh = int(a.get("width", doc.w)), int(a.get("height", doc.h))
        if action == "crop":
            ox, oy = -int(a.get("x", 0)), -int(a.get("y", 0))
        else:
            anchor = a.get("anchor", "top-left")
            ox = {"left": 0, "center": (nw - doc.w) // 2, "right": nw - doc.w}[
                "left" if "left" in anchor else "right" if "right" in anchor else "center"]
            oy = {"top": 0, "center": (nh - doc.h) // 2, "bottom": nh - doc.h}[
                "top" if "top" in anchor else "bottom" if "bottom" in anchor else "center"]
        doc.snapshot()
        for l in doc.layers:
            for f, old in enumerate(l.cels):
                new = bytearray(nw * nh * 4)
                for y in range(doc.h):
                    ty = y + oy
                    if not (0 <= ty < nh):
                        continue
                    for x in range(doc.w):
                        tx = x + ox
                        if 0 <= tx < nw:
                            si, di = (y * doc.w + x) * 4, (ty * nw + tx) * 4
                            new[di:di + 4] = old[si:si + 4]
                l.cels[f] = new
        doc.w, doc.h = nw, nh
        return [text(doc_summary(doc))]
    raise ValueError(f"unknown action '{action}'")


def t_palette(a):
    doc = doc_or_fail()
    if a.get("clear"):
        doc.palette.clear()
    if a.get("preset"):
        apply_palette(doc, a["preset"])
    if a.get("set"):
        apply_palette(doc, a["set"])
    for k in a.get("remove") or []:
        doc.palette.pop(k, None)
    notes = []
    for spec in ([a["ramp"]] if isinstance(a.get("ramp"), dict) else a.get("ramp") or []):
        cols, bi = art.make_ramp(doc.color(spec["base"])[:3], int(spec.get("steps", 5)),
                                 float(spec.get("hue_shift", 24)), spec.get("contrast"), float(spec.get("sat_falloff", 0.35)))
        keys = assign_keys(doc, spec.get("keys"), len(cols))
        for k, c in zip(keys, cols):
            doc.palette[k] = c + (255,)
        notes.append(f"ramp {''.join(keys) if all(len(k) == 1 for k in keys) else keys} (dark->light, base at {keys[bi]})")
    if a.get("extract"):
        spec = a["extract"]
        n = int(spec.get("colors", 16))
        if spec.get("path"):
            w, h, data = load_image(spec["path"], max_side=256)
        else:
            w, h, data = doc.w, doc.h, doc.composite(include_reference=True)
        cols = art.extract_palette([tuple(data[i:i + 4]) for i in range(0, len(data), 4)], n)
        if spec.get("replace", True):
            doc.palette.clear()
        keys = assign_keys(doc, spec.get("keys"), len(cols))
        for k, c in zip(keys, cols):
            doc.palette[k] = tuple(c) + (255,)
        notes.append(f"extracted {len(cols)} colours (dark->light)")
    if not doc.palette:
        return [text("palette is empty")]
    return [text("\n".join(notes + ["palette: " + ", ".join(f"{k}={to_hex(v)}" for k, v in doc.palette.items())]))]


KEY_POOL = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def assign_keys(doc, keys, n):
    """Palette keys for n new colours: explicit string/list, or the next free single characters."""
    if keys:
        keys = list(keys) if isinstance(keys, str) else [str(k) for k in keys]
        if len(keys) != n:
            raise ValueError(f"'keys' must give {n} keys, got {len(keys)}")
        return keys
    free = [c for c in KEY_POOL if c not in doc.palette]
    if len(free) < n:
        raise ValueError("palette is out of single-character keys; pass 'keys'")
    return free[:n]


def load_image(path, max_side=None, keep_colors=False):
    """Decode an image to RGBA. PNG is native; other formats (JPG, WEBP, BMP...) use Pillow if installed.
    max_side pre-shrinks very large images with Pillow (box filter) to keep pure-Python steps fast."""
    path = os.path.expanduser(path)
    try:
        from PIL import Image
    except ImportError:
        Image = None
    if Image is not None:
        im = Image.open(path)
        im.load()
        im = im.convert("RGBA")
        if max_side and max(im.size) > max_side:
            r = max_side / max(im.size)
            im = im.resize((max(1, round(im.width * r)), max(1, round(im.height * r))),
                           Image.NEAREST if keep_colors else Image.BOX)
        return im.width, im.height, bytearray(im.tobytes())
    if not path.lower().endswith(".png"):
        raise ValueError("only PNG can be read without Pillow (pip install pillow for JPG/WEBP/...)")
    with open(path, "rb") as f:
        return decode_png(f.read())


def t_import_reference(a):
    """Turn any picture into pixel-art-ready pixels: downscale, map to a palette, place as canvas or layer."""
    tw, th = a.get("width"), a.get("height")
    target_side = max(int(tw or 0), int(th or 0)) or 64
    w, h, data = load_image(a["path"], max_side=target_side * 4)
    if tw and not th:
        th = max(1, round(h * int(tw) / w))
    elif th and not tw:
        tw = max(1, round(w * int(th) / h))
    elif not tw and not th:
        r = min(1.0, 64 / max(w, h))
        tw, th = max(1, round(w * r)), max(1, round(h * r))
    tw, th = int(tw), int(th)
    method = a.get("resample", "area")
    if method != "area":  # re-read without blending so 'mode'/'nearest' see the original colours
        w, h, data = load_image(a["path"], max_side=target_side * 4, keep_colors=True)
    small = art.resample(w, h, data, tw, th, method, int(a.get("alpha_threshold", 128))) if (tw, th) != (w, h) or method != "area" else bytearray(data)
    if (tw, th) == (w, h):
        for i in range(3, len(small), 4):
            small[i] = 255 if small[i] >= int(a.get("alpha_threshold", 128)) else 0

    into = a.get("into", "canvas")
    if into == "layer":
        doc = doc_or_fail()
    else:
        name = unique_name(a.get("name") or os.path.splitext(os.path.basename(a["path"]))[0])
        doc = Document(name, tw, th)
    pal_spec = a.get("palette", "auto")
    notes = []
    if pal_spec == "auto":
        cols = art.extract_palette([tuple(small[i:i + 4]) for i in range(0, len(small), 4)], int(a.get("colors", 16)))
        if a.get("set_palette", True):
            for k, c in zip(assign_keys(doc, None, len(cols)), cols):
                doc.palette[k] = tuple(c) + (255,)
        notes.append(f"auto palette of {len(cols)} colours added to the canvas palette")
    elif pal_spec in PRESET_PALETTES:
        if into != "layer":
            doc.load_preset(pal_spec)
        cols = [tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4)) for hx in PRESET_PALETTES[pal_spec]]
    elif pal_spec == "canvas":
        cols = [v[:3] for v in doc.palette.values() if v[3] > 0]
        if not cols:
            raise ValueError("canvas palette is empty")
    elif pal_spec in (None, "none", False):
        cols = None
    else:
        cols = [doc.color(c)[:3] for c in pal_spec]
    if cols:
        art.quantize(tw, th, small, cols, a.get("dither") if a.get("dither") != "none" else None,
                     float(a.get("dither_strength", 0.05)))

    if into == "layer":
        doc.snapshot()
        lay = Layer(a.get("layer_name") or "reference", doc.w, doc.h, frames=doc.nframes)
        ox, oy = int(a.get("x", 0)), int(a.get("y", 0))
        for f in range(doc.nframes) if a.get("all_frames") else [doc.frame]:
            cel = lay.cels[f]
            for yy in range(th):
                ty = yy + oy
                if not 0 <= ty < doc.h:
                    continue
                for xx in range(tw):
                    tx = xx + ox
                    if 0 <= tx < doc.w:
                        si, di = (yy * tw + xx) * 4, (ty * doc.w + tx) * 4
                        cel[di:di + 4] = small[si:si + 4]
        lay.reference = bool(a.get("reference", True))
        lay.opacity = float(a.get("opacity", 0.5 if lay.reference else 1.0))
        doc.layers.append(lay)
        if lay.reference:
            notes.append("placed as a reference layer (shows in view, never exported); trace on another layer, "
                         "or layer set {reference:false, opacity:1} to keep it as real pixels")
        else:
            doc.active = len(doc.layers) - 1
    else:
        doc.layers[0] = Layer("Layer 1", tw, th, small)
        State.docs[doc.name] = doc
        State.current = doc.name
    return [text(f"imported {os.path.basename(a['path'])} ({w}x{h} source) as {tw}x{th} using '{method}'\n"
                 + "\n".join(notes) + "\n" + doc_summary(doc))] + maybe_preview(doc, a)


def t_draw(a):
    doc = doc_or_fail()
    ops = a.get("ops")
    if isinstance(ops, str):
        ops = json.loads(ops)
    if not isinstance(ops, list) or not ops:
        raise ValueError("'ops' must be a non-empty array")
    li = doc.layer_index(a.get("layer"))
    targets = frame_list(doc, a.get("frames"), a.get("frame"))
    doc.snapshot()
    done = writes = 0
    op = None
    try:
        for f in targets:
            p = Painter(doc, li, mirror=a.get("mirror"), blend=bool(a.get("blend", False)), frame=f)
            done = 0
            for op in ops:
                apply_op(doc, p, op)
                done += 1
            writes += p.count
    except Exception as e:
        # roll back the whole batch so the canvas never ends up half-drawn
        doc.undo(1)
        doc.redo_stack.pop()
        raise ValueError(f"op #{done} {json.dumps(op)[:200]} failed: {e}. Batch rolled back; nothing was drawn.")
    where = f"frame {targets[0]}" if len(targets) == 1 else f"frames {targets}"
    msg = f"applied {done} op(s) to layer #{li} '{doc.layers[li].name}', {where} ({writes} pixel writes)"
    return [text(msg)] + maybe_preview(doc, a)


def frame_list(doc, frames=None, frame=None):
    """Resolve a 'frames' value ("all", list of indices) or a single 'frame' into frame indices."""
    if frames is not None:
        if frames == "all":
            return list(range(doc.nframes))
        if isinstance(frames, str):
            frames = json.loads(frames)
        return [doc.frame_index(f) for f in frames]
    return [doc.frame_index(frame)]


def t_layer(a):
    doc = doc_or_fail()
    action = a.get("action", "list")
    if action == "list":
        return [text(doc_summary(doc))]
    doc.snapshot()
    if action == "add":
        l = Layer(a.get("name") or f"Layer {len(doc.layers) + 1}", doc.w, doc.h, frames=doc.nframes)
        pos = int(a["index"]) if a.get("index") is not None else doc.active + 1
        doc.layers.insert(pos, l)
        doc.active = pos
    else:
        i = doc.layer_index(a.get("layer"))
        if action == "select":
            doc.undo_stack.pop()
            doc.active = i
        elif action == "delete":
            if len(doc.layers) == 1:
                raise ValueError("cannot delete the only layer")
            doc.layers.pop(i)
            doc.active = min(doc.active, len(doc.layers) - 1)
        elif action == "rename":
            doc.layers[i].name = a["name"]
        elif action == "duplicate":
            src = doc.layers[i]
            doc.layers.insert(i + 1, src.clone(a.get("name") or src.name + " copy"))
            doc.active = i + 1
        elif action == "move":
            to = max(0, min(len(doc.layers) - 1, int(a["index"])))
            doc.layers.insert(to, doc.layers.pop(i))
            doc.active = to
        elif action == "set":
            if "visible" in a:
                doc.layers[i].visible = bool(a["visible"])
            if "opacity" in a:
                doc.layers[i].opacity = max(0.0, min(1.0, float(a["opacity"])))
            if "reference" in a:
                doc.layers[i].reference = bool(a["reference"])
            if a.get("name"):
                doc.layers[i].name = a["name"]
        elif action == "merge_down":
            if i == 0:
                raise ValueError("bottom layer cannot be merged down")
            upper, lower = doc.layers[i], doc.layers[i - 1]
            saved = [(l.visible) for l in doc.layers]
            for l in doc.layers:
                l.visible = False
            upper.visible = lower.visible = True
            lower_op = lower.opacity
            lower.opacity = 1.0
            merged = [doc.composite(frame=f) for f in range(doc.nframes)]
            for l, v in zip(doc.layers, saved):
                l.visible = v
            lower.cels, lower.opacity = merged, lower_op
            doc.layers.pop(i)
            doc.active = i - 1
        else:
            raise ValueError(f"unknown action '{action}'")
    return [text(doc_summary(doc))]


def region_of(doc, a):
    if any(k in a for k in ("x", "y", "width", "height")):
        x, y = int(a.get("x", 0)), int(a.get("y", 0))
        return (x, y, int(a.get("width", doc.w - x)), int(a.get("height", doc.h - y)))
    return None


def t_view(a):
    doc = doc_or_fail(a.get("canvas"))
    mode = a.get("mode", "image")
    region = region_of(doc, a)
    r = region or (0, 0, doc.w, doc.h)
    layer = doc.layer_index(a["layer"]) if a.get("layer") is not None else None
    bg = a.get("background", "checker")
    if a.get("frames") is not None:  # several frames side by side, left to right
        fl = frame_list(doc, a.get("frames"))
        W, H, flat = sheet_rgba(doc, fl, a.get("columns"), 1, region, layer, preview=True)
        scale = a.get("scale") or max(1, min(16, 960 // max(W, 1), 640 // max(H, 1)))
        png, s = render_preview(doc, scale, a.get("grid", False), None, None, bg, flat=flat, size=(W, H))
        return [text(f"frames {fl} of '{doc.name}' left-to-right (1px gap between frames) at {s}x zoom"), image(png)]
    f = doc.frame_index(a.get("frame"))
    out = []
    if mode in ("image", "both"):
        flat = onion_rgba(doc, r, layer, f) if a.get("onion") and doc.nframes > 1 else doc.composite(r, layer, f, include_reference=True)
        png, s = render_preview(doc, a.get("scale"), a.get("grid"), r, None, bg, flat=flat)
        out.append(text(f"preview of '{doc.name}' frame {f} region x={r[0]} y={r[1]} {r[2]}x{r[3]} at {s}x zoom"
                        + (" (blue lines every 8 px)" if (a.get("grid") if a.get("grid") is not None else s >= 8) else "")
                        + (" (previous frame ghosted)" if a.get("onion") else "")))
        out.append(image(png))
    if mode in ("grid", "both"):
        saved = doc.frame
        doc.frame = f
        try:
            out.append(text(f"frame {f}\n" + render_grid(doc, region, layer)))
        finally:
            doc.frame = saved
    return out


def t_frame(a):
    doc = doc_or_fail()
    action = a.get("action", "list")
    if action == "list":
        return [text(doc_summary(doc))]
    if action == "select":
        doc.frame = doc.frame_index(a.get("frame"))
        return [text(doc_summary(doc))]
    doc.snapshot()
    if action == "add":
        copy = a.get("copy", True)
        src = doc.frame_index(a.get("frame")) if copy else None
        idx = a.get("index")
        doc.add_frame(src + 1 if idx is None and src is not None else idx, src, a.get("duration"))
    elif action == "delete":
        doc.delete_frame(doc.frame_index(a.get("frame")))
    elif action == "move":
        doc.move_frame(doc.frame_index(a.get("frame")), a["index"])
    elif action == "duration":
        ms = int(a["duration"])
        for f in frame_list(doc, a.get("frames"), a.get("frame")):
            doc.durations[f] = ms
    else:
        raise ValueError(f"unknown action '{action}'")
    return [text(doc_summary(doc))]


def t_undo(a):
    doc = doc_or_fail()
    n = doc.undo(int(a.get("steps", 1)))
    return [text(f"undid {n} step(s); {len(doc.undo_stack)} more available")] + maybe_preview(doc, a)


def t_redo(a):
    doc = doc_or_fail()
    n = doc.redo(int(a.get("steps", 1)))
    return [text(f"redid {n} step(s)")] + maybe_preview(doc, a)


def t_export(a):
    doc = doc_or_fail(a.get("canvas"))
    scale = max(1, int(a.get("scale", 1)))
    mode = a.get("layers", "flatten")
    fmt = a.get("format") or ("gif" if str(a.get("path", "")).lower().endswith(".gif") else "png")
    ext = ".gif" if fmt == "gif" else ".png"
    base = a.get("path") or doc.export_paths.get(fmt)
    path = resolve_path(base, doc.name + ("_sheet" if fmt == "sheet" else ""), ext)
    fl = frame_list(doc, a.get("frames")) if a.get("frames") is not None else list(range(doc.nframes))
    written = []
    notes = []

    def write_png(p, w, h, data):
        with open(p, "wb") as fh:
            fh.write(encode_png(w * scale, h * scale, scale_rgba(w, h, data, scale)))
        written.append(p)

    if fmt == "gif":
        frames = [scale_rgba(doc.w, doc.h, doc.composite(frame=f), scale) for f in fl]
        data, exact = encode_gif(doc.w * scale, doc.h * scale, frames, [doc.durations[f] for f in fl],
                                 loop=int(a.get("loop", 0)))
        with open(path, "wb") as fh:
            fh.write(data)
        written.append(path)
        notes.append(f"{len(fl)} frame(s), durations {[doc.durations[f] for f in fl]} ms")
        if not exact:
            notes.append("more than 255 colors: colors were reduced for GIF")
        if any(doc.composite(frame=f)[i] not in (0, 255) for f in fl for i in range(3, doc.w * doc.h * 4, 4)):
            notes.append("GIF transparency is 1-bit: semi-transparent pixels were made opaque or transparent")
    elif fmt == "sheet":
        W, H, data = sheet_rgba(doc, fl, a.get("columns"), int(a.get("gap", 0)))
        write_png(path, W, H, data)
        notes.append(f"sprite sheet {len(fl)} frame(s) of {doc.w}x{doc.h}, {a.get('columns') or len(fl)} per row")
    elif fmt == "frames":
        stem = path[:-4]
        for f in fl:
            write_png(f"{stem}_f{f:02d}.png", doc.w, doc.h, doc.composite(frame=f))
    elif fmt == "png":
        f = doc.frame_index(a.get("frame"))
        if mode in ("flatten", "both"):
            write_png(path, doc.w, doc.h, doc.composite(frame=f))
        if mode in ("separate", "both"):
            stem = path[:-4]
            for i, l in enumerate(doc.layers):
                safe = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in l.name)
                write_png(f"{stem}_{i:02d}_{safe}.png", doc.w, doc.h, doc.composite(only_layer=i, frame=f))
        if doc.nframes > 1:
            notes.append(f"exported frame {f} only; use format 'gif', 'sheet' or 'frames' for the animation")
    else:
        raise ValueError(f"unknown format '{fmt}'")
    doc.export_paths[fmt] = path  # remembered per format so e.g. a png export never lands on a frames file
    doc.last_export = written[0]
    msg = "wrote:\n" + "\n".join(written) + ("\n" + "\n".join(notes) if notes else "")
    if a.get("open"):
        msg += "\n" + launch_pixelliner(written[0])
    return [text(msg)]


def launch_pixelliner(path):
    if not os.path.exists(PIXELLINER_EXE):
        return f"PixelLiner not found at {PIXELLINER_EXE} (set PIXELLINER_EXE)"
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([PIXELLINER_EXE, os.path.abspath(path)], cwd=os.path.dirname(PIXELLINER_EXE),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags, close_fds=True)
    return f"launched PixelLiner with {path}"


def project_dir(path, doc_name):
    path = os.path.expanduser(path or doc_name)
    if not os.path.isabs(path):
        path = os.path.join(OUTPUT_DIR, path)
    if path.lower().endswith("project.json"):
        path = os.path.dirname(path)
    return path


def t_save_project(a):
    doc = doc_or_fail(a.get("canvas"))
    folder = project_dir(a.get("path"), doc.name + ".pxproj")
    return [text("saved project " + save_project(doc, folder))]


def t_load_project(a):
    folder = project_dir(a["path"], "")
    doc = load_project(folder)
    doc.name = unique_name(a.get("name") or doc.name)
    State.docs[doc.name] = doc
    State.current = doc.name
    return [text("loaded " + doc_summary(doc))]


def t_lint(a):
    doc = doc_or_fail(a.get("canvas"))
    pal = [v[:3] for v in doc.palette.values() if v[3] > 0] if a.get("check_palette", True) and doc.palette else None
    if a.get("frames") is not None:
        fl = frame_list(doc, a.get("frames"))
        res = art.lint_frames(doc.w, doc.h, [doc.composite(frame=f) for f in fl])
        return [text(json.dumps(res, indent=1))]
    f = doc.frame_index(a.get("frame"))
    data = doc.composite(only_layer=doc.layer_index(a["layer"]) if a.get("layer") is not None else None, frame=f)
    res = art.lint(doc.w, doc.h, data, pal, a.get("max_colors"))
    return [text(json.dumps(res, indent=1))]


def t_open_in_pixelliner(a):
    path = a.get("path")
    if not path:
        doc = doc_or_fail()
        if not doc.last_export:
            raise ValueError("canvas has not been exported yet; call export first or pass 'path'")
        path = doc.last_export
    return [text(launch_pixelliner(os.path.expanduser(path)))]


# ---------------------------------------------------------------- tool schemas

COLOR_DOC = ("Colors: '#rgb', '#rrggbb', '#rrggbbaa', a canvas palette key, a basic name (black, white, red...), "
             "or 'transparent' (erases).")

OPS_DOC = """Batch of drawing operations, applied in order to one layer. All coordinates are integer pixels, origin top-left.
Ops (field 'op'):
- pixel_map {x,y,rows:[str],palette?:{char:color}} -- FASTEST way to draw sprites: one string per row, one char per pixel; '.' or space = skip. Chars resolve via 'palette' then the canvas palette.
- pixels {points:[[x,y],...], color} or {pixels:[[x,y,color],...]}
- line {x1,y1,x2,y2,color,width?}   polyline {points,color,closed?}
- rect {x,y,w,h,color,fill?}   ellipse {x,y,w,h,color,fill?} (bounding box)   circle {cx,cy,r,color,fill?}
- polygon {points,color,fill?}
- fill {x,y,color,contiguous?=true,tolerance?=0,diagonal?=false} (bucket fill on this layer)
- replace_color {from,to,tolerance?}
- outline {color,diagonal?=false,inside?=false,clean?=true} -- 1px outline around all opaque pixels of the layer (clean = pixel-perfect, no doubled corners)
- gradient {x?,y?,w?,h?,colors:[...],direction?:vertical|horizontal|diagonal|radial,dither?=true} -- ordered-dither ramp using only the given colors
- clear {} whole layer, or {x,y,w,h} region
- flip {axis:x|y}   shift {dx,dy,wrap?}   mirror {axis:x|y, from?:left|right|top|bottom} (copy one half onto the other)
Technique ops (whole layer, or limit with x,y,w,h). 'snap' (default: true when the canvas has a palette) keeps new colours inside the palette:
- shade {color|colors, ramp?:[dark..light], steps?=4, light?=top-left|top|...|angle, elevation?=0.9, roundness?=1, ambient?=0, dither?} -- form shading: treats the area of the given flat colour(s) as a rounded volume lit from 'light' and paints it with the ramp (auto hue-shifted ramp if none). A base to refine, not a final render.
- selout {amount?=0.3, light?} -- selective outline: edge pixels become a darker, cooler shade of the colour they enclose; lighter on the lit side. Use after 'outline'.
- antialias {strength?=0.5, min_run?=2} -- intermediate colours inside staircase steps between two colours (not on 45-degree lines, square corners, or transparent edges).
- cleanup {doubles?=true, orphans?=true, orphan_min?=6} -- pixel-perfect pass: removes L-shaped doubled corners in 1px lines and isolated stray pixels.
- quantize {palette?:[colors], dither?:ordered, strength?} -- snap every pixel to the palette.
- transform {x?,y?,w?,h? (source region, default whole layer), dx?,dy?, rotate?:deg, scale?:n|[sx,sy], flip_x?, flip_y?, copy?, from_frame?, from_layer?, smooth?=true} -- move/copy a region with optional RotSprite rotation (clean pixel-art rotation). From another frame/layer it copies; within the same layer it moves unless copy=true. Great for in-betweens: copy a limb from the previous frame and rotate it a few degrees.
""" + COLOR_DOC

TOOLS = [
    {
        "name": "new_canvas",
        "description": "Create a new blank pixel-art canvas (becomes the current canvas). Optionally fill a 'Background' layer and load a palette.",
        "inputSchema": {"type": "object", "properties": {
            "width": {"type": "integer", "minimum": 1, "maximum": 2048},
            "height": {"type": "integer", "minimum": 1, "maximum": 2048},
            "name": {"type": "string", "description": "Canvas name, also the default export filename."},
            "background": {"type": "string", "description": "Optional fill color; creates a Background layer plus an empty drawing layer."},
            "palette": {"description": "Preset name (pico8, db16, gameboy; keys 0-9a-f) or an object {key: color}. Single-char keys can be used in pixel_map rows.",
                        "anyOf": [{"type": "string"}, {"type": "object", "additionalProperties": {"type": "string"}}]},
            "frames": {"type": "integer", "minimum": 1, "description": "Number of animation frames (default 1). More can be added later with the frame tool."},
            "duration": {"type": "integer", "minimum": 10, "description": "Default frame duration in ms (default 100)."},
        }, "required": ["width", "height"]},
    },
    {
        "name": "open_image",
        "description": "Load an existing PNG (e.g. one saved from PixelLiner) as a new canvas for editing.",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string"},
            "name": {"type": "string"},
            "overwrite_on_export": {"type": "boolean", "description": "If true, export without a path overwrites the source file. Default false."},
        }, "required": ["path"]},
    },
    {
        "name": "canvas",
        "description": "Manage canvases: list | info | switch {name} | close | resize {width,height,anchor: top-left|center|bottom-right|...} | crop {x,y,width,height}.",
        "inputSchema": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["list", "info", "switch", "close", "resize", "crop"]},
            "name": {"type": "string"}, "width": {"type": "integer"}, "height": {"type": "integer"},
            "x": {"type": "integer"}, "y": {"type": "integer"}, "anchor": {"type": "string"},
        }, "required": ["action"]},
    },
    {
        "name": "palette",
        "description": "View or edit the current canvas palette. Keys are names you can use as colors; single-character keys also work in pixel_map rows. "
                       "'ramp' builds professional hue-shifted shading ramps (shadows drift cool, highlights warm); 'extract' derives a palette from an image or the canvas.",
        "inputSchema": {"type": "object", "properties": {
            "preset": {"type": "string", "enum": list(PRESET_PALETTES)},
            "set": {"type": "object", "additionalProperties": {"type": "string"}, "description": "{key: color}"},
            "remove": {"type": "array", "items": {"type": "string"}},
            "clear": {"type": "boolean"},
            "ramp": {"description": "Generate hue-shifted shading ramp(s): {base, steps?=5, hue_shift?=24 (degrees toward cool shadows / warm highlights), contrast? (lightness step), sat_falloff?, keys? (e.g. 'abcde')}. Keys are listed dark->light.",
                     "anyOf": [{"type": "object"}, {"type": "array", "items": {"type": "object"}}]},
            "extract": {"type": "object", "description": "Build a palette by k-means: {colors?=16, path? (image file; default: the current canvas), replace?=true, keys?}."},
        }},
    },
    {
        "name": "import_reference",
        "description": "Turn an image (PNG; JPG/WEBP/etc. if Pillow is installed) into pixel-art pixels: downscale it, map it to a palette, and open it as a new canvas or put it in the current canvas as a reference layer. "
                       "Typical: sketch/concept/3D render -> import at sprite size -> clean up shapes, outline, shading by hand.",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string"},
            "width": {"type": "integer", "description": "Target width in pixels (height follows the aspect ratio if omitted). Default: fit 64."},
            "height": {"type": "integer"},
            "resample": {"type": "string", "enum": ["area", "mode", "nearest"],
                         "description": "area = average (photos, paintings; default); mode = most common colour per block (upscaled pixel art, flat-colour art); nearest = sample."},
            "palette": {"description": "'auto' (k-means, default), 'canvas', a preset (pico8, db16, gameboy), 'none', or a list of colours.",
                        "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            "colors": {"type": "integer", "description": "Colour count for palette 'auto' (default 16)."},
            "dither": {"type": "string", "enum": ["none", "ordered"]},
            "dither_strength": {"type": "number"},
            "alpha_threshold": {"type": "integer", "description": "Pixels with alpha below this become transparent (default 128)."},
            "into": {"type": "string", "enum": ["canvas", "layer"], "description": "canvas = new canvas (default); layer = add to the current canvas."},
            "reference": {"type": "boolean", "description": "into=layer: mark as reference (preview only, default true)."},
            "opacity": {"type": "number"}, "x": {"type": "integer"}, "y": {"type": "integer"},
            "layer_name": {"type": "string"}, "name": {"type": "string"},
            "all_frames": {"type": "boolean", "description": "into=layer: put the image on every frame."},
            "set_palette": {"type": "boolean", "description": "Add the auto palette to the canvas palette (default true)."},
            "preview": {"type": "boolean"},
        }, "required": ["path"]},
    },
    {
        "name": "draw",
        "description": "Draw on the current canvas with a batch of operations (one undo step; the whole batch is rolled back if any op fails). Put as much as possible into one call.\n" + OPS_DOC,
        "inputSchema": {"type": "object", "properties": {
            "ops": {"type": "array", "items": {"type": "object"}, "description": "List of op objects, e.g. {\"op\":\"rect\",\"x\":0,\"y\":0,\"w\":4,\"h\":4,\"color\":\"#ff0000\",\"fill\":true}"},
            "layer": {"description": "Layer index or name. Default: active layer.", "anyOf": [{"type": "integer"}, {"type": "string"}]},
            "frame": {"type": "integer", "description": "Frame to draw on. Default: current frame."},
            "frames": {"description": "Apply the same ops to several frames: \"all\" or a list of indices (e.g. a static background).",
                       "anyOf": [{"type": "string", "enum": ["all"]}, {"type": "array", "items": {"type": "integer"}}]},
            "mirror": {"type": "string", "enum": ["x", "y", "xy"], "description": "Symmetry: also plot mirrored across the vertical (x) and/or horizontal (y) center."},
            "blend": {"type": "boolean", "description": "Alpha-blend semi-transparent colors instead of replacing pixels. Default false."},
            "preview": {"type": "boolean", "description": "Return a preview image after drawing. Default false."},
        }, "required": ["ops"]},
    },
    {
        "name": "layer",
        "description": "Layer management: list | add {name?,index?} | select | delete | rename {name} | duplicate | move {index} | set {visible?,opacity?,name?,reference?} | merge_down. 'layer' is an index (0 = bottom) or name; default active layer.",
        "inputSchema": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["list", "add", "select", "delete", "rename", "duplicate", "move", "set", "merge_down"]},
            "layer": {"anyOf": [{"type": "integer"}, {"type": "string"}]},
            "name": {"type": "string"}, "index": {"type": "integer"},
            "visible": {"type": "boolean"}, "opacity": {"type": "number", "minimum": 0, "maximum": 1},
            "reference": {"type": "boolean", "description": "Reference layers show in view previews but are never exported."},
        }, "required": ["action"]},
    },
    {
        "name": "frame",
        "description": "Animation frames (every layer has its own image in every frame; layer names/visibility are shared). "
                       "list | select {frame} | add {frame?=current, copy?=true, index?, duration?} (inserts after the source frame, copying it so you only redraw what moves; copy=false = blank) | "
                       "delete {frame} | move {frame, index} | duration {duration, frame? | frames?}. The new/selected frame becomes current.",
        "inputSchema": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["list", "select", "add", "delete", "move", "duration"]},
            "frame": {"type": "integer"}, "index": {"type": "integer"},
            "frames": {"anyOf": [{"type": "string", "enum": ["all"]}, {"type": "array", "items": {"type": "integer"}}]},
            "copy": {"type": "boolean"}, "duration": {"type": "integer", "minimum": 10, "description": "ms"},
        }, "required": ["action"]},
    },
    {
        "name": "view",
        "description": "Look at the canvas. mode 'image' returns a zoomed PNG preview (checkerboard = transparent; grid lines when zoomed, blue every 8px). mode 'grid' returns exact pixels as text with coordinates and a color legend (best for precise edits). mode 'both' returns both. Pass 'frames' (\"all\" or indices) to see animation frames side by side in one image; 'onion' ghosts the previous frame.",
        "inputSchema": {"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["image", "grid", "both"]},
            "frame": {"type": "integer", "description": "Frame to show. Default: current frame."},
            "frames": {"anyOf": [{"type": "string", "enum": ["all"]}, {"type": "array", "items": {"type": "integer"}}],
                       "description": "Show several frames side by side (image only)."},
            "columns": {"type": "integer", "description": "Frames per row when showing several frames."},
            "onion": {"type": "boolean", "description": "Ghost the previous frame underneath (onion skin)."},
            "x": {"type": "integer"}, "y": {"type": "integer"}, "width": {"type": "integer"}, "height": {"type": "integer"},
            "layer": {"description": "Show only this layer.", "anyOf": [{"type": "integer"}, {"type": "string"}]},
            "scale": {"type": "integer", "minimum": 1, "maximum": 32, "description": "Zoom factor; default fits ~640px."},
            "grid": {"type": "boolean"},
            "background": {"type": "string", "description": "'checker' (default) or a color."},
            "canvas": {"type": "string"},
        }},
    },
    {
        "name": "undo",
        "description": "Undo the last draw/layer/resize step(s).",
        "inputSchema": {"type": "object", "properties": {"steps": {"type": "integer", "minimum": 1}, "preview": {"type": "boolean"}}},
    },
    {
        "name": "redo",
        "description": "Redo undone step(s).",
        "inputSchema": {"type": "object", "properties": {"steps": {"type": "integer", "minimum": 1}, "preview": {"type": "boolean"}}},
    },
    {
        "name": "export",
        "description": f"Save the canvas (PixelLiner opens PNG and GIF directly). Relative paths go to {OUTPUT_DIR}. Default filename: <canvas name>.png/.gif. "
                       "format: png = one frame (default: current); gif = animated GIF of all frames with their durations; sheet = sprite-sheet PNG; frames = one PNG per frame.",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string"},
            "format": {"type": "string", "enum": ["png", "gif", "sheet", "frames"]},
            "frame": {"type": "integer", "description": "png format: which frame."},
            "frames": {"anyOf": [{"type": "string", "enum": ["all"]}, {"type": "array", "items": {"type": "integer"}}],
                       "description": "gif/sheet/frames: subset and order of frames (default all)."},
            "columns": {"type": "integer", "description": "sheet: frames per row (default all in one row)."},
            "gap": {"type": "integer", "description": "sheet: transparent pixels between frames (default 0)."},
            "loop": {"type": "integer", "description": "gif: loop count, 0 = forever (default)."},
            "scale": {"type": "integer", "minimum": 1, "maximum": 32, "description": "Integer upscale (nearest neighbour). Default 1."},
            "layers": {"type": "string", "enum": ["flatten", "separate", "both"], "description": "flatten = one merged PNG (default); separate = one PNG per layer; both."},
            "open": {"type": "boolean", "description": "Open the result in PixelLiner afterwards. Only when the user asks for it."},
            "canvas": {"type": "string"},
        }},
    },
    {
        "name": "save_project",
        "description": f"Save the canvas with all layers, frames, palette and flags as a project folder (project.json + PNGs). Relative paths go to {OUTPUT_DIR}. Default: <canvas name>.pxproj.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "canvas": {"type": "string"}}},
    },
    {
        "name": "load_project",
        "description": "Open a project folder saved by save_project (or by the pixelagent pipeline) as a new canvas.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "name": {"type": "string"}}, "required": ["path"]},
    },
    {
        "name": "lint",
        "description": "Objective quality check: colour count, off-palette pixels, semi-transparency, stray pixels, detached fragments, doubled corners, jaggies, edge contact, plus a 0-100 heuristic score. Pass 'frames' to check animation consistency instead (volume and palette drift).",
        "inputSchema": {"type": "object", "properties": {
            "frame": {"type": "integer"}, "layer": {"anyOf": [{"type": "integer"}, {"type": "string"}]},
            "frames": {"anyOf": [{"type": "string", "enum": ["all"]}, {"type": "array", "items": {"type": "integer"}}]},
            "max_colors": {"type": "integer"}, "check_palette": {"type": "boolean"}, "canvas": {"type": "string"},
        }},
    },
    {
        "name": "open_in_pixelliner",
        "description": "Launch PixelLiner with an exported PNG or GIF (default: the current canvas's last export). Only when the user wants to see/edit it in PixelLiner. If PixelLiner is already running it shows an import dialog (New Character / New Pose / New Frame) for the user to choose.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
    },
]

HANDLERS = {
    "new_canvas": t_new_canvas, "open_image": t_open_image, "import_reference": t_import_reference, "canvas": t_canvas, "palette": t_palette,
    "draw": t_draw, "layer": t_layer, "frame": t_frame, "view": t_view, "undo": t_undo, "redo": t_redo,
    "export": t_export, "open_in_pixelliner": t_open_in_pixelliner,
    "save_project": t_save_project, "load_project": t_load_project, "lint": t_lint,
}

INSTRUCTIONS = """Headless pixel-art canvas for PixelLiner. Workflow: new_canvas (or open_image) -> palette -> draw (batch many ops; pixel_map rows are the fastest way to lay down a sprite) -> view to check -> fix with draw/undo -> export (PNG). Use layers for outline/colour/shading separation. Use view mode 'grid' for exact pixel coordinates.
Animation: new_canvas {frames} or frame add (copies the current frame) -> draw {frame} only what changes -> view {frames:"all"} to compare -> export {format:"gif"} (or "sheet").
Quality: work in passes on separate layers -- silhouette, flat colours, shade (form shading with a palette ramp), hand-refine light/shadow shapes, highlights, outline + selout, antialias, cleanup -- and view (zoomed) after every pass. A reference image (import_reference) gives far better shapes than drawing from memory. The 'pixel_art_workflow' prompt has the full method."""

WORKFLOW = """You are drawing high-quality pixel art with the pixelliner tools. Work like a professional pixel artist, in passes, checking a zoomed `view` after each one and fixing problems before moving on.

1. Plan: decide canvas size (character sprites usually 32-96 px tall), light direction (default top-left), and a limited palette. Build it with `palette` ramps: one hue-shifted ramp (4-6 steps) per material (skin, hair, cloth, metal), plus 1-2 accents. Fewer colours read better; reuse shadow tones across ramps.
2. Reference: if any picture exists (a sketch, concept art, photo, a 3D/AI render), `import_reference` it at the target size into a reference layer (or as the canvas) instead of inventing shapes. Proportions and pose come from there.
3. Silhouette: on its own layer, block the whole figure in one flat colour. Check readability at 1x (view scale 1-3): the pose must read from the silhouette alone. Fix it before going further.
4. Flats: fill each material with its base (middle) ramp colour. Keep clusters clean; avoid single-pixel noise.
5. Volume: run `shade` per material (color = the flat, ramp = that material's ramp, light = your light direction). Then hand-refine: simplify banding into deliberate shapes, add cast shadows (under hair, chin, arms, folds), push contrast at focal points (face), keep the background-facing side darker.
6. Highlights and details: a few lightest-colour pixels on the lit edges, eyes, metal. Less is more.
7. Lines: `outline` on the sprite layer (pixel-perfect by default), then `selout` so outlines take a darker shade of what they enclose and soften toward the light. Interior lines should use darker ramp colours, not black.
8. Polish: `antialias` (snap to palette) on curved edges, then `cleanup` (orphans) to remove stray pixels; run `cleanup` doubles on hand-drawn single-colour lines before recolouring them. Inspect with view mode 'grid' around the face/hands and fix individual pixels.
9. Animation: draw key poses first on separate frames, then in-betweens with `frame add` (copy) + `transform` (move/rotate limbs with RotSprite) + touch-ups. Keep volumes and colours consistent; use `view {frames:'all'}` and `onion` to check motion arcs. Use timing (frame durations) for weight: hold key poses longer.
10. Export: `export` png (and gif/sheet for animation); open in PixelLiner only when the user asks.
Common mistakes to avoid: pillow shading (lighting from the outline inward), banding (parallel stair-step colour bands), jaggies on curves, too many colours, black outlines everywhere, noisy single pixels, inconsistent light direction."""

PROMPTS = [{
    "name": "pixel_art_workflow",
    "description": "Professional pixel-art method for these tools (passes, palette ramps, shading, outlines, AA, animation).",
    "arguments": [{"name": "subject", "description": "What to draw (optional).", "required": False}],
}]


# ---------------------------------------------------------------- JSON-RPC over stdio

def send(msg):
    sys.stdout.buffer.write(json.dumps(msg, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def handle(req):
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}
    if method == "initialize":
        want = params.get("protocolVersion")
        ver = want if want in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
        return {"protocolVersion": ver, "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}, "instructions": INSTRUCTIONS}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "prompts/list":
        return {"prompts": PROMPTS}
    if method == "prompts/get":
        if params.get("name") != "pixel_art_workflow":
            raise ValueError(f"unknown prompt {params.get('name')}")
        subject = (params.get("arguments") or {}).get("subject")
        body = WORKFLOW + (f"\n\nTask: draw {subject}." if subject else "")
        return {"description": PROMPTS[0]["description"],
                "messages": [{"role": "user", "content": {"type": "text", "text": body}}]}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = HANDLERS.get(name)
        if fn is None:
            return {"content": [text(f"unknown tool '{name}'")], "isError": True}
        try:
            return {"content": fn(args)}
        except Exception as e:  # tool errors are reported to the model, not as protocol errors
            print(traceback.format_exc(), file=sys.stderr)
            return {"content": [text(f"error: {e}")], "isError": True}
    if rid is None:
        return None  # notification
    raise LookupError(method)


def main():
    for raw in sys.stdin.buffer:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line.decode("utf-8"))
        except Exception:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        rid = req.get("id")
        try:
            result = handle(req)
            if rid is not None:
                send({"jsonrpc": "2.0", "id": rid, "result": result if result is not None else {}})
        except LookupError as e:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {e}"}})
        except Exception as e:
            print(traceback.format_exc(), file=sys.stderr)
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": str(e)}})


if __name__ == "__main__":
    main()
