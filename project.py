"""Layered v1 projects: project.json plus PNG cels beneath the project folder.

A save writes unique cels before atomically replacing the manifest. Interrupted
saves leave the previous project usable; abrupt termination can leave unreferenced
files. Cleanup never scans for arbitrary PNGs and is limited to prior tracked cels.
"""
import json
import math
import ntpath
import os
import re
import struct
import tempfile
import uuid

from canvas import Document, Layer, parse_hex, to_hex
from png import decode_png, encode_png

FORMAT = "pixelliner-mcp-project"
VERSION = 1


def _safe(name):
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)[:40] or "layer"


def _integer(value, label, minimum, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"invalid project {label}")
    return value


def _validate_manifest(m):
    """Validate structure before allocating canvases or reading cel files."""
    if not isinstance(m, dict) or m.get("format") != FORMAT:
        raise ValueError(f"not a {FORMAT} file")
    if type(m.get("version", VERSION)) is not int or m.get("version", VERSION) != VERSION:
        raise ValueError("unsupported project version")
    if not isinstance(m.get("name"), str):
        raise ValueError("project name must be text")
    _integer(m.get("width"), "width", 1, 2048)
    _integer(m.get("height"), "height", 1, 2048)
    durations, layers = m.get("durations"), m.get("layers")
    if not isinstance(durations, list) or not durations:
        raise ValueError("project must have at least one frame")
    for duration in durations:
        _integer(duration, "frame duration", 1)
    if not isinstance(layers, list) or not layers:
        raise ValueError("project has no layers")
    _integer(m.get("frame", 0), "selected frame", 0, len(durations) - 1)
    _integer(m.get("active", 0), "active layer", 0, len(layers) - 1)
    for layer in layers:
        if not isinstance(layer, dict) or not isinstance(layer.get("name"), str):
            raise ValueError("layer name must be text")
        cels = layer.get("cels")
        if not isinstance(cels, list) or len(cels) != len(durations):
            raise ValueError("layer cel count must match project frame count")
        if not all(isinstance(cel, str) and cel for cel in cels):
            raise ValueError("cel references must be relative paths")
        opacity = layer.get("opacity", 1.0)
        if type(opacity) not in (int, float) or not math.isfinite(opacity) or not 0 <= opacity <= 1:
            raise ValueError("layer opacity must be between 0 and 1")
        if any(type(layer.get(flag, default)) is not bool for flag, default in
               (("visible", True), ("reference", False))):
            raise ValueError("layer flags must be true or false")
    palette = m.get("palette", {})
    if not isinstance(palette, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                              for k, v in palette.items()):
        raise ValueError("project palette must map text keys to hex colors")
    for color in palette.values():
        parse_hex(color)


def _cel_path(folder, relative):
    """Accept portable relative names only, resolving links within the project."""
    if not isinstance(relative, str) or not relative or "\0" in relative:
        raise ValueError("invalid cel path")
    portable = relative.replace("\\", "/")
    if ntpath.splitdrive(portable)[0] or portable.startswith("/") or ".." in portable.split("/"):
        raise ValueError("cel path must stay inside the project")
    root = os.path.realpath(folder)
    path = os.path.abspath(os.path.join(folder, *portable.split("/")))
    resolved = os.path.realpath(path)
    if os.path.commonpath([root, resolved]) != root or resolved == root:
        raise ValueError("cel path must stay inside the project")
    return path


def _old_owned_cels(folder, manifest):
    """Only recognized files referenced by the last manifest are cleanup candidates."""
    owned = set()
    for li, layer in enumerate(manifest["layers"]):
        for frame, relative in enumerate(layer["cels"]):
            stem = f"layers/{li:02d}_{_safe(layer['name'])}_f{frame:02d}"
            portable = relative.replace("\\", "/")
            if portable == stem + ".png" or re.fullmatch(re.escape(stem) + r"_[0-9a-f]{32}\.png", portable):
                path = _cel_path(folder, relative)
                if not os.path.islink(path):
                    owned.add(path)
    return owned


def save_project(doc, folder):
    """Commit a complete project; prior cels survive until the manifest switches.

    The returned path and the v1 manifest's layers/ relative names remain compatible
    with existing callers. One writer per folder is recommended: overlapping writers
    can leave unused unique cels, which are deliberately not swept up.
    """
    result_path = os.path.join(os.fspath(folder), "project.json")
    folder = os.path.abspath(os.fspath(folder))
    token = uuid.uuid4().hex
    layers = []
    for li, layer in enumerate(doc.layers):
        cels = [f"layers/{li:02d}_{_safe(layer.name)}_f{frame:02d}_{token}.png"
                for frame in range(len(layer.cels))]
        layers.append({"name": layer.name, "visible": layer.visible, "opacity": layer.opacity,
                       "reference": layer.reference, "cels": cels})
    manifest = {
        "format": FORMAT, "version": VERSION, "name": doc.name, "width": doc.w, "height": doc.h,
        "durations": list(doc.durations), "frame": doc.frame, "active": doc.active,
        "palette": {k: to_hex(v) for k, v in doc.palette.items()}, "layers": layers,
    }
    _validate_manifest(manifest)
    if any(len(cel) != doc.w * doc.h * 4 for layer in doc.layers for cel in layer.cels):
        raise ValueError("cel data length does not match project dimensions")
    path = os.path.join(folder, "project.json")
    old_owned = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            previous = json.load(fh)
        _validate_manifest(previous)
        old_owned = _old_owned_cels(folder, previous)
    layer_dir = _cel_path(folder, "layers")
    os.makedirs(layer_dir, exist_ok=True)
    created, temporary, committed = [], None, False
    try:
        for layer, spec in zip(doc.layers, layers):
            for cel, relative in zip(layer.cels, spec["cels"]):
                target = _cel_path(folder, relative)
                encoded = encode_png(doc.w, doc.h, cel)
                with open(target, "xb") as fh:
                    created.append(target)
                    fh.write(encoded)
                    fh.flush()
                    os.fsync(fh.fileno())
        fd, temporary = tempfile.mkstemp(prefix=".project-", suffix=".tmp", dir=folder)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
        committed = True
    finally:
        # Never undo a completed manifest switch, even if a later cleanup fails.
        if not committed:
            try:
                with open(path, encoding="utf-8") as fh:
                    current = json.load(fh)
                new_refs = {cel for layer in layers for cel in layer["cels"]}
                committed = any(cel in new_refs for layer in current.get("layers", [])
                                for cel in layer.get("cels", []))
            except FileNotFoundError:
                pass
            except (OSError, ValueError, TypeError, AttributeError):
                committed = True  # On uncertainty retain new files rather than break a published manifest.
        for candidate in ([temporary] if temporary else []) + ([] if committed else created):
            try:
                os.remove(candidate)
            except OSError:
                pass
    for candidate in old_owned:
        try:
            # Recheck containment immediately before deletion, including directory links.
            _cel_path(folder, os.path.relpath(candidate, folder))
            if not os.path.islink(candidate):
                os.remove(candidate)
        except (OSError, ValueError):
            pass  # The new project is already committed; harmless old files can remain.
    return result_path


def load_project(folder, name=None):
    folder = os.fspath(folder)
    path = folder if folder.lower().endswith(".json") else os.path.join(folder, "project.json")
    folder = os.path.dirname(os.path.abspath(path))
    with open(path, encoding="utf-8") as fh:
        m = json.load(fh)
    _validate_manifest(m)
    paths = [[_cel_path(folder, relative) for relative in layer["cels"]] for layer in m["layers"]]
    doc = Document(name or m["name"], m["width"], m["height"])
    doc.durations = list(m["durations"])
    doc.layers = []
    for spec, cel_paths in zip(m["layers"], paths):
        layer = Layer(spec["name"], doc.w, doc.h, frames=0)
        for relative, cel_path in zip(spec["cels"], cel_paths):
            with open(cel_path, "rb") as fh:
                header = fh.read(24)
                if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
                    raise ValueError(f"{relative} is not a project PNG")
                if struct.unpack(">II", header[16:24]) != (doc.w, doc.h):
                    raise ValueError(f"{relative} dimensions do not match the project")
                data = header + fh.read()
            w, h, data = decode_png(data)
            if (w, h) != (doc.w, doc.h) or len(data) != doc.w * doc.h * 4:
                raise ValueError(f"{relative} dimensions do not match the project")
            layer.cels.append(data)
        layer.visible = spec.get("visible", True)
        layer.opacity = float(spec.get("opacity", 1.0))
        layer.reference = spec.get("reference", False)
        doc.layers.append(layer)
    doc.frame = m.get("frame", 0)
    doc.active = m.get("active", 0)
    doc.palette = {k: parse_hex(v) for k, v in m.get("palette", {}).items()}
    return doc
