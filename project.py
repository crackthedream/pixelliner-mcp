"""Layered project files: a folder with project.json + one PNG per layer per frame.

Lets canvases move between the MCP server, the pixelagent pipeline and PixelLiner (via exported PNGs)
without losing layers, frames, palette or flags.
"""
import json
import os

from canvas import Document, Layer, parse_hex, to_hex
from png import decode_png, encode_png

FORMAT = "pixelliner-mcp-project"
VERSION = 1


def _safe(name):
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)[:40] or "layer"


def save_project(doc, folder):
    """Write doc to folder (created if needed). Returns the path of project.json."""
    os.makedirs(os.path.join(folder, "layers"), exist_ok=True)
    layers = []
    keep = set()
    for li, layer in enumerate(doc.layers):
        cels = []
        for f, cel in enumerate(layer.cels):
            rel = f"layers/{li:02d}_{_safe(layer.name)}_f{f:02d}.png"
            with open(os.path.join(folder, rel), "wb") as fh:
                fh.write(encode_png(doc.w, doc.h, cel))
            cels.append(rel)
            keep.add(os.path.normcase(os.path.abspath(os.path.join(folder, rel))))
        layers.append({"name": layer.name, "visible": layer.visible, "opacity": layer.opacity,
                       "reference": layer.reference, "cels": cels})
    # remove stale layer PNGs from a previous save
    for fn in os.listdir(os.path.join(folder, "layers")):
        p = os.path.normcase(os.path.abspath(os.path.join(folder, "layers", fn)))
        if fn.endswith(".png") and p not in keep:
            os.remove(p)
    manifest = {
        "format": FORMAT, "version": VERSION, "name": doc.name, "width": doc.w, "height": doc.h,
        "durations": list(doc.durations), "frame": doc.frame, "active": doc.active,
        "palette": {k: to_hex(v) for k, v in doc.palette.items()}, "layers": layers,
    }
    path = os.path.join(folder, "project.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def load_project(folder, name=None):
    path = folder if folder.lower().endswith(".json") else os.path.join(folder, "project.json")
    folder = os.path.dirname(path)
    with open(path, encoding="utf-8") as fh:
        m = json.load(fh)
    if m.get("format") != FORMAT:
        raise ValueError(f"{path} is not a {FORMAT} file")
    doc = Document(name or m["name"], int(m["width"]), int(m["height"]))
    doc.durations = [int(d) for d in m["durations"]]
    doc.layers = []
    for spec in m["layers"]:
        layer = Layer(spec["name"], doc.w, doc.h, frames=0)
        for rel in spec["cels"]:
            with open(os.path.join(folder, rel), "rb") as fh:
                w, h, data = decode_png(fh.read())
            if (w, h) != (doc.w, doc.h):
                raise ValueError(f"{rel} is {w}x{h}, expected {doc.w}x{doc.h}")
            layer.cels.append(data)
        if len(layer.cels) != len(doc.durations):
            raise ValueError(f"layer '{layer.name}' has {len(layer.cels)} cels for {len(doc.durations)} frames")
        layer.visible = bool(spec.get("visible", True))
        layer.opacity = float(spec.get("opacity", 1.0))
        layer.reference = bool(spec.get("reference", False))
        doc.layers.append(layer)
    if not doc.layers:
        raise ValueError("project has no layers")
    doc.frame = min(int(m.get("frame", 0)), doc.nframes - 1)
    doc.active = min(int(m.get("active", 0)), len(doc.layers) - 1)
    doc.palette = {k: parse_hex(v) for k, v in m.get("palette", {}).items()}
    return doc
