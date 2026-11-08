"""In-memory layered pixel canvas with pixel-art drawing primitives."""
import math

TRANSPARENT = (0, 0, 0, 0)

NAMED_COLORS = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#00ff00",
    "blue": "#0000ff", "yellow": "#ffff00", "cyan": "#00ffff", "magenta": "#ff00ff",
    "gray": "#808080", "grey": "#808080", "orange": "#ff8000", "purple": "#800080",
    "brown": "#8b4513", "pink": "#ffc0cb",
}

PRESET_PALETTES = {
    # Keys are single characters so presets can be used directly in pixel_map rows.
    "pico8": ["000000", "1d2b53", "7e2553", "008751", "ab5236", "5f574f", "c2c3c7", "fff1e8",
              "ff004d", "ffa300", "ffec27", "00e436", "29adff", "83769c", "ff77a8", "ffccaa"],
    "db16": ["140c1c", "442434", "30346d", "4e4a4e", "854c30", "346524", "d04648", "757161",
             "597dce", "d27d2c", "8595a1", "6daa2c", "d2aa99", "6dc2ca", "dad45e", "deeed6"],
    "gameboy": ["0f380f", "306230", "8bac0f", "9bbc0f"],
}

def dither_on(frac, x, y):
    """Quarter-step ordered dither with even density on every row and column (pixel-art friendly)."""
    q = round(frac * 4)
    if q <= 0:
        return False
    if q >= 4:
        return True
    if q == 2:
        return (x + y) % 2 == 0
    sparse = (x + 2 * (y % 2)) % 4 == 0
    return sparse if q == 1 else not sparse


def parse_hex(s):
    s = s.strip().lstrip("#")
    if len(s) in (3, 4):
        s = "".join(ch * 2 for ch in s)
    if len(s) == 6:
        s += "ff"
    if len(s) != 8:
        raise ValueError(f"bad hex color '#{s}'")
    v = int(s, 16)
    return ((v >> 24) & 255, (v >> 16) & 255, (v >> 8) & 255, v & 255)


def to_hex(c):
    r, g, b, a = c
    return f"#{r:02x}{g:02x}{b:02x}" + ("" if a == 255 else f"{a:02x}")


class Layer:
    """A layer holds one RGBA image ("cel") per animation frame; name/visibility/opacity are shared."""

    def __init__(self, name, w, h, data=None, frames=1):
        self.name = name
        if data is None:
            self.cels = [bytearray(w * h * 4) for _ in range(frames)]
        else:
            self.cels = [bytearray(data)] + [bytearray(len(data)) for _ in range(frames - 1)]
        self.visible = True
        self.opacity = 1.0
        self.reference = False  # shown in previews, never exported

    def clone(self, name=None):
        n = Layer(name or self.name, 0, 0, frames=0)
        n.cels = [bytearray(c) for c in self.cels]
        n.visible, n.opacity, n.reference = self.visible, self.opacity, self.reference
        return n


class Document:
    MAX_HISTORY = 40

    def __init__(self, name, w, h):
        if not (1 <= w <= 2048 and 1 <= h <= 2048):
            raise ValueError("canvas size must be between 1 and 2048")
        self.name = name
        self.w, self.h = w, h
        self.layers = [Layer("Layer 1", w, h)]
        self.durations = [100]  # ms per frame
        self.frame = 0
        self.active = 0
        self.palette = {}
        self.undo_stack = []
        self.redo_stack = []
        self.last_export = None  # most recent file written (for open_in_pixelliner)
        self.export_paths = {}   # format -> base path used last time

    @property
    def nframes(self):
        return len(self.durations)

    def frame_index(self, ref):
        if ref is None:
            return self.frame
        i = int(ref)
        if i < 0:
            i += self.nframes
        if not 0 <= i < self.nframes:
            raise ValueError(f"no frame {ref} (frames 0..{self.nframes - 1})")
        return i

    def cel(self, layer_idx, frame=None):
        return self.layers[layer_idx].cels[self.frame if frame is None else frame]

    # ---------- history ----------
    def _state(self):
        return (self.w, self.h, self.active, self.frame, list(self.durations), [l.clone() for l in self.layers])

    def snapshot(self):
        self.undo_stack.append(self._state())
        if len(self.undo_stack) > self.MAX_HISTORY:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def _restore(self, st):
        self.w, self.h, self.active, self.frame, durations, layers = st
        self.durations = list(durations)
        self.layers = [l.clone() for l in layers]

    def undo(self, steps=1):
        n = 0
        while n < steps and self.undo_stack:
            self.redo_stack.append(self._state())
            self._restore(self.undo_stack.pop())
            n += 1
        return n

    def redo(self, steps=1):
        n = 0
        while n < steps and self.redo_stack:
            self.undo_stack.append(self._state())
            self._restore(self.redo_stack.pop())
            n += 1
        return n

    # ---------- frames ----------
    def add_frame(self, index=None, copy_from=None, duration=None):
        index = self.nframes if index is None else max(0, min(self.nframes, int(index)))
        for l in self.layers:
            src = l.cels[copy_from] if copy_from is not None else None
            l.cels.insert(index, bytearray(src) if src is not None else bytearray(self.w * self.h * 4))
        self.durations.insert(index, int(duration or self.durations[min(index, self.nframes - 1)]))
        self.frame = index
        return index

    def delete_frame(self, i):
        if self.nframes == 1:
            raise ValueError("cannot delete the only frame")
        for l in self.layers:
            l.cels.pop(i)
        self.durations.pop(i)
        self.frame = min(self.frame, self.nframes - 1)

    def move_frame(self, i, to):
        to = max(0, min(self.nframes - 1, int(to)))
        for l in self.layers:
            l.cels.insert(to, l.cels.pop(i))
        self.durations.insert(to, self.durations.pop(i))
        self.frame = to

    # ---------- colors ----------
    def color(self, c):
        if c is None:
            raise ValueError("color is required")
        if isinstance(c, (list, tuple)):
            vals = [int(v) for v in c]
            if len(vals) == 3:
                vals.append(255)
            return tuple(max(0, min(255, v)) for v in vals[:4])
        s = str(c).strip()
        if s in self.palette:
            return self.palette[s]
        low = s.lower()
        if low in ("transparent", "none", "clear", "erase"):
            return TRANSPARENT
        if low in NAMED_COLORS:
            return parse_hex(NAMED_COLORS[low])
        if s.startswith("#") or all(ch in "0123456789abcdefABCDEF" for ch in s) and len(s) in (3, 4, 6, 8):
            return parse_hex(s)
        raise ValueError(f"unknown color '{c}' (use #rrggbb[aa], a palette key, or 'transparent')")

    def load_preset(self, name):
        cols = PRESET_PALETTES[name]
        keys = "0123456789abcdef" if len(cols) <= 16 else None
        for i, hx in enumerate(cols):
            self.palette[keys[i] if keys else str(i)] = parse_hex(hx)

    # ---------- layers ----------
    def layer_index(self, ref):
        if ref is None:
            return self.active
        if isinstance(ref, int) or (isinstance(ref, str) and ref.lstrip("-").isdigit()):
            i = int(ref)
            if i < 0:
                i += len(self.layers)
            if 0 <= i < len(self.layers):
                return i
        for i, l in enumerate(self.layers):
            if l.name == ref:
                return i
        raise ValueError(f"no layer '{ref}' (layers: {[l.name for l in self.layers]})")

    def composite(self, region=None, only_layer=None, frame=None, include_reference=False):
        """Flatten visible layers of a frame. Returns bytearray RGBA for region (x, y, w, h).
        Reference layers are skipped unless include_reference (previews) or asked for by only_layer."""
        x0, y0, rw, rh = region or (0, 0, self.w, self.h)
        f = self.frame if frame is None else frame
        out = bytearray(rw * rh * 4)
        layers = [self.layers[only_layer]] if only_layer is not None else [
            l for l in self.layers if l.visible and (include_reference or not l.reference)]
        for l in layers:
            op = l.opacity if only_layer is None else 1.0
            src = l.cels[f]
            for yy in range(rh):
                sy = y0 + yy
                if not (0 <= sy < self.h):
                    continue
                for xx in range(rw):
                    sx = x0 + xx
                    if not (0 <= sx < self.w):
                        continue
                    si = (sy * self.w + sx) * 4
                    sa = src[si + 3]
                    if sa == 0:
                        continue
                    di = (yy * rw + xx) * 4
                    a = sa * op / 255.0
                    if a >= 0.999:
                        out[di:di + 4] = src[si:si + 4]
                        continue
                    da = out[di + 3] / 255.0
                    oa = a + da * (1 - a)
                    if oa <= 0:
                        continue
                    for k in range(3):
                        out[di + k] = int(round((src[si + k] * a + out[di + k] * da * (1 - a)) / oa))
                    out[di + 3] = int(round(oa * 255))
        return out


class Painter:
    """Applies drawing ops to one layer of a document, with optional mirroring."""

    def __init__(self, doc, layer_idx, mirror=None, blend=False, frame=None):
        self.doc = doc
        self.layer = doc.layers[layer_idx]
        self.data = doc.cel(layer_idx, frame)  # mutated in place
        self.li, self.frame = layer_idx, (doc.frame if frame is None else frame)
        self.w, self.h = doc.w, doc.h
        self.mirror = (mirror or "").lower()
        self.blend = blend
        self.count = 0

    # --- primitive plot ---
    def _put(self, x, y, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 4
            d = self.data
            if self.blend and 0 < c[3] < 255:
                sa = c[3] / 255.0
                da = d[i + 3] / 255.0
                oa = sa + da * (1 - sa)
                for k in range(3):
                    d[i + k] = int(round((c[k] * sa + d[i + k] * da * (1 - sa)) / oa))
                d[i + 3] = int(round(oa * 255))
            else:
                d[i:i + 4] = bytes(c)
            self.count += 1

    def plot(self, x, y, c):
        self._put(x, y, c)
        if "x" in self.mirror:
            self._put(self.w - 1 - x, y, c)
        if "y" in self.mirror:
            self._put(x, self.h - 1 - y, c)
        if "x" in self.mirror and "y" in self.mirror:
            self._put(self.w - 1 - x, self.h - 1 - y, c)

    def get(self, x, y):
        i = (y * self.w + x) * 4
        return tuple(self.data[i:i + 4])

    def hspan(self, x0, x1, y, c):
        for x in range(min(x0, x1), max(x0, x1) + 1):
            self.plot(x, y, c)

    # --- shapes ---
    def line(self, x0, y0, x1, y1, c, width=1):
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        r = (width - 1) // 2
        while True:
            if width <= 1:
                self.plot(x0, y0, c)
            else:
                for oy in range(-r, width - r):
                    for ox in range(-r, width - r):
                        self.plot(x0 + ox, y0 + oy, c)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def rect(self, x, y, w, h, c, fill):
        if w <= 0 or h <= 0:
            return
        if fill:
            for yy in range(y, y + h):
                self.hspan(x, x + w - 1, yy, c)
        else:
            self.hspan(x, x + w - 1, y, c)
            self.hspan(x, x + w - 1, y + h - 1, c)
            for yy in range(y + 1, y + h - 1):
                self.plot(x, yy, c)
                self.plot(x + w - 1, yy, c)

    def ellipse(self, x, y, w, h, c, fill):
        if w <= 0 or h <= 0:
            return
        cx, cy = x + (w - 1) / 2.0, y + (h - 1) / 2.0
        rx, ry = w / 2.0, h / 2.0
        spans = []
        for yy in range(y, y + h):
            dy = (yy - cy) / ry
            rem = 1 - dy * dy
            if rem < 0:
                rem = 0
            half = rx * math.sqrt(rem)
            l = math.ceil(cx - half - 1e-9)
            r = math.floor(cx + half + 1e-9)
            # keep span inside the box and non-empty
            l, r = max(x, l), min(x + w - 1, r)
            if l > r:
                l = r = int(round(cx))
            spans.append((l, r))
        for i, (l, r) in enumerate(spans):
            yy = y + i
            if fill or i == 0 or i == len(spans) - 1:
                self.hspan(l, r, yy, c)
                continue
            pl, pr = spans[i - 1]
            nl, nr = spans[i + 1]
            le = max(l, pl - 1, nl - 1)
            rs = min(r, pr + 1, nr + 1)
            if le >= rs:
                self.hspan(l, r, yy, c)
            else:
                self.hspan(l, le, yy, c)
                self.hspan(rs, r, yy, c)

    def polygon(self, pts, c, fill):
        if fill and len(pts) >= 3:
            ys = [p[1] for p in pts]
            n = len(pts)
            for yy in range(min(ys), max(ys) + 1):
                sy = yy + 0.5
                xs = []
                for i in range(n):
                    (xa, ya), (xb, yb) = pts[i], pts[(i + 1) % n]
                    if (ya <= sy < yb) or (yb <= sy < ya):
                        xs.append(xa + (sy - ya) * (xb - xa) / (yb - ya))
                xs.sort()
                for k in range(0, len(xs) - 1, 2):
                    a, b = math.ceil(xs[k] - 0.5), math.floor(xs[k + 1] - 0.5)
                    if a <= b:
                        self.hspan(a, b, yy, c)
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            if i == len(pts) - 1 and len(pts) < 3:
                break
            self.line(a[0], a[1], b[0], b[1], c)

    def flood(self, x, y, c, contiguous=True, tolerance=0, diagonal=False):
        if not (0 <= x < self.w and 0 <= y < self.h):
            return
        target = self.get(x, y)

        def match(p):
            return all(abs(p[k] - target[k]) <= tolerance for k in range(4))

        if not contiguous:
            for yy in range(self.h):
                for xx in range(self.w):
                    if match(self.get(xx, yy)):
                        self._put(xx, yy, c)
            return
        if target == tuple(c) and tolerance == 0:
            return
        seen = bytearray(self.w * self.h)
        stack = [(x, y)]
        nbrs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        if diagonal:
            nbrs += [(1, 1), (1, -1), (-1, 1), (-1, -1)]
        pts = []
        while stack:
            px, py = stack.pop()
            if not (0 <= px < self.w and 0 <= py < self.h):
                continue
            k = py * self.w + px
            if seen[k]:
                continue
            seen[k] = 1
            if not match(self.get(px, py)):
                continue
            pts.append((px, py))
            for dx, dy in nbrs:
                stack.append((px + dx, py + dy))
        for px, py in pts:
            self._put(px, py, c)

    def replace_color(self, src, dst, tolerance=0):
        d = self.data
        for i in range(0, len(d), 4):
            if all(abs(d[i + k] - src[k]) <= tolerance for k in range(4)):
                d[i:i + 4] = bytes(dst)
                self.count += 1

    def outline(self, c, diagonal=False, inside=False, clean=True):
        """Add a 1px outline around opaque pixels (outside), or recolor the opaque border (inside).
        clean: thin the outline to pixel-perfect (8-connected, no doubled L corners)."""
        d = self.data
        w, h = self.w, self.h
        solid = [d[i * 4 + 3] > 0 for i in range(w * h)]
        nb = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        if diagonal:
            nb += [(1, 1), (1, -1), (-1, 1), (-1, -1)]
        targets = []
        for yy in range(h):
            for xx in range(w):
                s = solid[yy * w + xx]
                if s == inside:
                    for dx, dy in nb:
                        nx, ny = xx + dx, yy + dy
                        n_solid = 0 <= nx < w and 0 <= ny < h and solid[ny * w + nx]
                        if n_solid != inside:
                            targets.append((xx, yy))
                            break
        if clean and not diagonal and targets:
            import art
            tmp = bytearray(w * h * 4)
            for xx, yy in targets:
                i = (yy * w + xx) * 4
                tmp[i:i + 4] = b"\xff\x00\xff\xff"
            art.cleanup(w, h, tmp, doubles=True, orphans=False)
            targets = [(xx, yy) for xx, yy in targets if tmp[(yy * w + xx) * 4 + 3]]
        for xx, yy in targets:
            self._put(xx, yy, c)

    def gradient(self, x, y, w, h, colors, direction="vertical", dither=True):
        n = len(colors)
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                if direction == "horizontal":
                    t = (xx - x) / max(1, w - 1)
                elif direction == "diagonal":
                    t = ((xx - x) + (yy - y)) / max(1, w + h - 2)
                elif direction == "radial":
                    cx, cy = x + (w - 1) / 2, y + (h - 1) / 2
                    t = min(1.0, math.hypot((xx - cx) / max(1, w / 2), (yy - cy) / max(1, h / 2)))
                else:
                    t = (yy - y) / max(1, h - 1)
                f = t * (n - 1)
                i = int(f)
                frac = f - i
                if i >= n - 1:
                    i, frac = n - 1, 0
                if dither:
                    idx = i + 1 if dither_on(frac, xx - x, yy - y) else i
                else:
                    idx = i + 1 if frac >= 0.5 else i
                self.plot(xx, yy, colors[min(idx, n - 1)])

    def pixel_map(self, x, y, rows, keymap, skip=".", ):
        for j, row in enumerate(rows):
            for i, ch in enumerate(row):
                if ch == skip or ch == " ":
                    continue
                if ch not in keymap:
                    raise ValueError(f"pixel_map char '{ch}' (row {j}) has no color; define it in 'palette' or the canvas palette")
                self.plot(x + i, y + j, keymap[ch])

    # --- transforms on the whole layer ---
    def flip(self, axis):
        w, h, d = self.w, self.h, self.data
        new = bytearray(len(d))
        for yy in range(h):
            for xx in range(w):
                sx = w - 1 - xx if axis == "x" else xx
                sy = h - 1 - yy if axis == "y" else yy
                si, di = (sy * w + sx) * 4, (yy * w + xx) * 4
                new[di:di + 4] = d[si:si + 4]
        self.data[:] = new

    def shift(self, dx, dy, wrap=False):
        w, h, d = self.w, self.h, self.data
        new = bytearray(len(d))
        for yy in range(h):
            for xx in range(w):
                sx, sy = xx - dx, yy - dy
                if wrap:
                    sx, sy = sx % w, sy % h
                elif not (0 <= sx < w and 0 <= sy < h):
                    continue
                si, di = (sy * w + sx) * 4, (yy * w + xx) * 4
                new[di:di + 4] = d[si:si + 4]
        self.data[:] = new

    def mirror_copy(self, axis, from_side="left"):
        """Copy one half of the layer onto the other (symmetry)."""
        w, h, d = self.w, self.h, self.data
        for yy in range(h):
            for xx in range(w):
                if axis == "x":
                    src_half = xx < w // 2 if from_side in ("left", "top") else xx >= (w + 1) // 2
                    if not src_half:
                        continue
                    tx, ty = w - 1 - xx, yy
                else:
                    src_half = yy < h // 2 if from_side in ("top", "left") else yy >= (h + 1) // 2
                    if not src_half:
                        continue
                    tx, ty = xx, h - 1 - yy
                si, di = (yy * w + xx) * 4, (ty * w + tx) * 4
                d[di:di + 4] = d[si:si + 4]
