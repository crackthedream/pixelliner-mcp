"""Pixel-art technique algorithms: perceptual colour (OKLab), ramps, palette extraction,
reference resampling, sel-out, anti-aliasing, cleanup, form shading and RotSprite-style transforms.

All functions work on flat RGBA bytearrays (w*h*4) or lists of (r, g, b, a) tuples.
"""
import math
import random

# ------------------------------------------------------------------ colour science (OKLab / OKLCh)


def _to_lin(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


_LIN = [_to_lin(i) for i in range(256)]


def _from_lin(x):
    x = min(1.0, max(0.0, x))
    v = 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055
    return int(round(v * 255))


def _cbrt(x):
    return math.copysign(abs(x) ** (1 / 3), x)


def rgb_to_lab(rgb):
    r, g, b = _LIN[rgb[0]], _LIN[rgb[1]], _LIN[rgb[2]]
    l = _cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m = _cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s = _cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _lab_to_lin(L, a, b):
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)


def lab_to_rgb(lab):
    return tuple(_from_lin(v) for v in _lab_to_lin(*lab))


def lch_to_rgb(L, C, h):
    """OKLCh -> sRGB, reducing chroma until the colour is inside the sRGB gamut."""
    for _ in range(40):
        a, b = C * math.cos(math.radians(h)), C * math.sin(math.radians(h))
        lin = _lab_to_lin(L, a, b)
        if all(-1e-4 <= v <= 1 + 1e-4 for v in lin):
            break
        C *= 0.92
    return tuple(_from_lin(v) for v in lin)


def rgb_to_lch(rgb):
    L, a, b = rgb_to_lab(rgb)
    return L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360


def _rotate_hue(h, target, amount):
    """Rotate hue h toward target by at most `amount` degrees along the shortest arc."""
    d = (target - h + 540) % 360 - 180
    step = max(-abs(amount), min(abs(amount), d))
    return (h + step) % 360


COOL_HUE = 275.0   # blue-violet in OKLCh: where shadows drift
WARM_HUE = 95.0    # warm yellow in OKLCh: where highlights drift


def shade_color(rgb, dl, hue_shift=0.0, chroma_mul=1.0):
    """Lighten (dl>0) / darken (dl<0) in OKLab with hue drift toward warm (light) or cool (dark)."""
    L, C, h = rgb_to_lch(rgb)
    target = WARM_HUE if dl > 0 else COOL_HUE
    if C < 0.02:  # near-grey: give it a faint tint so the drift is visible
        h, C = target, C + 0.015 * min(1.0, abs(dl) * 4)
    h = _rotate_hue(h, target, hue_shift)
    return lch_to_rgb(min(0.99, max(0.02, L + dl)), C * chroma_mul, h)


def make_ramp(base, steps=5, hue_shift=24.0, contrast=None, sat_falloff=0.35):
    """Hue-shifted colour ramp, dark -> light, that contains `base`. Returns (colors, base_index)."""
    steps = max(2, int(steps))
    L0, C0, h0 = rgb_to_lch(base)
    dL = float(contrast) if contrast else min(0.6 / (steps - 1), 0.11)
    lo, hi = 0.17, 0.96
    # base in the middle by default (shadows below, highlights above); slide it if the lightness range runs out
    bi = (steps - 1) // 2
    room_below = int(math.floor(max(0.0, L0 - lo) / dL + 1e-9))
    room_above = int(math.floor(max(0.0, hi - L0) / dL + 1e-9))
    if steps - 1 - bi > room_above:
        bi = steps - 1 - room_above
    if bi > room_below:
        bi = room_below
    bi = max(0, min(steps - 1, bi))
    # still not enough room on one side (very light/dark base): compress the step size
    if bi and L0 - bi * dL < lo:
        dL = max(0.03, (L0 - lo) / bi)
    if steps - 1 - bi and L0 + (steps - 1 - bi) * dL > hi:
        dL = max(0.03, (hi - L0) / (steps - 1 - bi))
    span = max(bi, steps - 1 - bi, 1)
    out = []
    for i in range(steps):
        t = (i - bi) / span            # -1 darkest .. 0 base .. +1 lightest
        if i == bi:
            out.append(tuple(base[:3]))
            continue
        L = min(hi, max(lo, L0 + (i - bi) * dL))
        target = WARM_HUE if t > 0 else COOL_HUE
        C, h = C0, h0
        if C < 0.02:
            h, C = target, 0.01 + 0.02 * abs(t)
        h = _rotate_hue(h, target, hue_shift * abs(t))
        C = C * (1 - sat_falloff * t) if t > 0 else C * (1 - 0.12 * abs(t))
        out.append(lch_to_rgb(L, C, h))
    return out, bi


class Matcher:
    """Nearest-colour lookup in OKLab with a cache."""

    def __init__(self, palette):
        self.pal = [tuple(c[:3]) for c in palette]
        self.lab = [rgb_to_lab(c) for c in self.pal]
        self.cache = {}

    def nearest_index(self, rgb, l_offset=0.0):
        key = (rgb[0], rgb[1], rgb[2], round(l_offset, 3))
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        L, a, b = rgb_to_lab(rgb)
        L += l_offset
        best, bd = 0, 1e9
        for i, (pl, pa, pb) in enumerate(self.lab):
            d = (L - pl) ** 2 + (a - pa) ** 2 + (b - pb) ** 2
            if d < bd:
                best, bd = i, d
        self.cache[key] = best
        return best

    def nearest(self, rgb, l_offset=0.0):
        return self.pal[self.nearest_index(rgb, l_offset)]


def mix(c1, c2, t):
    """Perceptual mix in OKLab (t=0 -> c1, t=1 -> c2)."""
    a, b = rgb_to_lab(c1), rgb_to_lab(c2)
    return lab_to_rgb(tuple(a[k] + (b[k] - a[k]) * t for k in range(3)))


def extract_palette(pixels, k, seed=7):
    """k-means in OKLab over opaque pixels (weighted by count). Returns colours sorted dark -> light."""
    counts = {}
    for p in pixels:
        if p[3] >= 128:
            counts[p[:3]] = counts.get(p[:3], 0) + 1
    if not counts:
        return []
    uniq = list(counts)
    if len(uniq) <= k:
        return sorted(uniq, key=lambda c: rgb_to_lab(c)[0])
    labs = [rgb_to_lab(c) for c in uniq]
    wts = [counts[c] for c in uniq]
    rnd = random.Random(seed)
    # k-means++ initialisation
    cents = [labs[max(range(len(labs)), key=lambda i: wts[i])]]
    d2 = [sum((l[j] - cents[0][j]) ** 2 for j in range(3)) for l in labs]
    while len(cents) < k:
        tot = sum(d * w for d, w in zip(d2, wts))
        if tot <= 0:
            break
        r, acc = rnd.random() * tot, 0.0
        for i, (d, w) in enumerate(zip(d2, wts)):
            acc += d * w
            if acc >= r:
                break
        cents.append(labs[i])
        d2 = [min(d2[j], sum((labs[j][q] - labs[i][q]) ** 2 for q in range(3))) for j in range(len(labs))]
    for _ in range(16):
        sums = [[0.0, 0.0, 0.0, 0.0] for _ in cents]
        for l, w in zip(labs, wts):
            ci = min(range(len(cents)), key=lambda c: (l[0] - cents[c][0]) ** 2 + (l[1] - cents[c][1]) ** 2 + (l[2] - cents[c][2]) ** 2)
            s = sums[ci]
            s[0] += l[0] * w
            s[1] += l[1] * w
            s[2] += l[2] * w
            s[3] += w
        new = [(s[0] / s[3], s[1] / s[3], s[2] / s[3]) if s[3] else cents[i] for i, s in enumerate(sums)]
        if max(sum((a[j] - b[j]) ** 2 for j in range(3)) for a, b in zip(new, cents)) < 1e-8:
            cents = new
            break
        cents = new
    out = []
    for c in sorted(cents, key=lambda c: c[0]):
        rgb = lab_to_rgb(c)
        if rgb not in out:
            out.append(rgb)
    return out


# ------------------------------------------------------------------ reference resampling

def resample(w, h, data, tw, th, method="area", alpha_threshold=128):
    """Downscale RGBA to tw x th. area = linear-light box average; mode = most common colour per block;
    nearest = centre sample. Output alpha is binary (pixel art)."""
    out = bytearray(tw * th * 4)
    for ty in range(th):
        y0 = ty * h // th
        y1 = max(y0 + 1, (ty + 1) * h // th)
        for tx in range(tw):
            x0 = tx * w // tw
            x1 = max(x0 + 1, (tx + 1) * w // tw)
            o = (ty * tw + tx) * 4
            if method == "nearest":
                i = (((y0 + y1) // 2) * w + (x0 + x1) // 2) * 4
                r, g, b, a = data[i:i + 4]
            elif method == "mode":
                cnt = {}
                for yy in range(y0, y1):
                    for xx in range(x0, x1):
                        i = (yy * w + xx) * 4
                        p = bytes(data[i:i + 4]) if data[i + 3] >= alpha_threshold else b"\0\0\0\0"
                        cnt[p] = cnt.get(p, 0) + 1
                r, g, b, a = max(cnt.items(), key=lambda kv: kv[1])[0]
            else:
                sr = sg = sb = sa = 0.0
                n = 0
                for yy in range(y0, y1):
                    for xx in range(x0, x1):
                        i = (yy * w + xx) * 4
                        al = data[i + 3] / 255.0
                        sr += _LIN[data[i]] * al
                        sg += _LIN[data[i + 1]] * al
                        sb += _LIN[data[i + 2]] * al
                        sa += al
                        n += 1
                a = int(round(sa / n * 255))
                if sa > 0:
                    r, g, b = _from_lin(sr / sa), _from_lin(sg / sa), _from_lin(sb / sa)
                else:
                    r = g = b = 0
            if a < alpha_threshold:
                continue
            out[o:o + 4] = bytes((r, g, b, 255))
    return out


ORDERED4 = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]


def quantize(w, h, data, palette, dither=None, strength=0.05, region=None):
    """Map opaque pixels to the nearest palette colour (OKLab). dither='ordered' adds a small Bayer
    lightness offset so smooth areas break into pixel-art dither patterns."""
    m = Matcher(palette)
    x0, y0, rw, rh = region or (0, 0, w, h)
    changed = 0
    for y in range(max(0, y0), min(h, y0 + rh)):
        for x in range(max(0, x0), min(w, x0 + rw)):
            i = (y * w + x) * 4
            if data[i + 3] == 0:
                continue
            off = (ORDERED4[y % 4][x % 4] / 15.0 - 0.5) * strength * 2 if dither == "ordered" else 0.0
            c = m.nearest((data[i], data[i + 1], data[i + 2]), off)
            if tuple(data[i:i + 3]) != c or data[i + 3] != 255:
                data[i:i + 4] = bytes((c[0], c[1], c[2], 255))
                changed += 1
    return changed


# ------------------------------------------------------------------ helpers on pixel grids

def to_pixels(data):
    return [tuple(data[i:i + 4]) for i in range(0, len(data), 4)]


def write_pixels(data, px, changes):
    for idx, c in changes.items():
        data[idx * 4:idx * 4 + 4] = bytes(c)


def _in_region(x, y, region):
    if region is None:
        return True
    rx, ry, rw, rh = region
    return rx <= x < rx + rw and ry <= y < ry + rh


LIGHT_DIRS = {
    "top-left": (-1, -1), "top": (0, -1), "top-right": (1, -1), "left": (-1, 0), "right": (1, 0),
    "bottom-left": (-1, 1), "bottom": (0, 1), "bottom-right": (1, 1),
}


def light_vector(light):
    if isinstance(light, (int, float)):
        ang = math.radians(light)
        return math.cos(ang), -math.sin(ang)
    lx, ly = LIGHT_DIRS.get(str(light).lower(), (-1, -1))
    n = math.hypot(lx, ly) or 1
    return lx / n, ly / n


# ------------------------------------------------------------------ finishing passes

def selout(w, h, data, amount=0.3, light="top-left", matcher=None, region=None):
    """Selective outline: recolour the silhouette edge pixels with a darker, cooler shade of the colour
    they enclose; edges facing the light are darkened less. Run after adding a plain outline (or
    directly on an un-outlined sprite to darken its rim)."""
    px = to_pixels(data)
    solid = [p[3] > 0 for p in px]
    lx, ly = light_vector(light)

    def is_edge(x, y):
        if not solid[y * w + x]:
            return False
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if not (0 <= nx < w and 0 <= ny < h) or not solid[ny * w + nx]:
                return True
        return False

    edge = [is_edge(i % w, i // w) for i in range(w * h)]
    changes = {}
    for y in range(h):
        for x in range(w):
            k = y * w + x
            if not edge[k] or not _in_region(x, y, region):
                continue
            # colour enclosed by this edge pixel: most common interior colour within 2 px
            cnt = {}
            nx_sum = ny_sum = 0
            for dy in (-2, -1, 0, 1, 2):
                for dx in (-2, -1, 0, 1, 2):
                    xx, yy = x + dx, y + dy
                    inside = 0 <= xx < w and 0 <= yy < h
                    if inside and solid[yy * w + xx] and not edge[yy * w + xx]:
                        c = px[yy * w + xx][:3]
                        cnt[c] = cnt.get(c, 0) + (3 if max(abs(dx), abs(dy)) == 1 else 1)
                    if abs(dx) <= 1 and abs(dy) <= 1 and (not inside or not solid[yy * w + xx]):
                        nx_sum += dx
                        ny_sum += dy
            base = max(cnt.items(), key=lambda kv: kv[1])[0] if cnt else px[k][:3]
            facing = 0.0
            nn = math.hypot(nx_sum, ny_sum)
            if nn:
                facing = (nx_sum * lx + ny_sum * ly) / nn   # +1 = faces the light
            amt = amount * (1.0 - 0.35 * facing)
            c = shade_color(base, -amt, hue_shift=12 + 10 * (1 - facing), chroma_mul=1.05)
            if matcher:
                c = matcher.nearest(c)
                if c == base:  # palette has nothing darker nearby: take the darkest step below it
                    c = _darker_in_palette(matcher, base) or c
            changes[k] = (c[0], c[1], c[2], 255)
    write_pixels(data, px, changes)
    return len(changes)


def _darker_in_palette(matcher, base):
    L0, a0, b0 = rgb_to_lab(base)
    best, bd = None, 1e9
    for c, (L, a, b) in zip(matcher.pal, matcher.lab):
        if L < L0 - 0.03:
            d = (L0 - 0.12 - L) ** 2 + (a - a0) ** 2 + (b - b0) ** 2
            if d < bd:
                best, bd = c, d
    return best


def antialias(w, h, data, strength=0.5, matcher=None, region=None, min_run=2):
    """Manual-style AA on jaggies: where an edge colour forms a 1:N staircase against a fill colour,
    put an intermediate colour in the fill pixel inside each step (and a lighter touch for long steps).
    Pure 45-degree diagonals and square corners are left alone. Transparent edges are not touched."""
    px = to_pixels(data)

    def at(x, y):
        if 0 <= x < w and 0 <= y < h:
            return px[y * w + x]
        return None

    changes = {}

    def put(x, y, base, edge_c, t):
        c = mix(base[:3], edge_c[:3], t)
        m = matcher(base[:3], edge_c[:3]) if callable(matcher) and not isinstance(matcher, Matcher) else matcher
        if m:
            c = m.nearest(c)
        if c in (base[:3], edge_c[:3]):
            return
        k = y * w + x
        if k not in changes:
            changes[k] = (c[0], c[1], c[2], 255)

    for y in range(h):
        for x in range(w):
            if not _in_region(x, y, region):
                continue
            A = px[y * w + x]
            if A[3] == 0:
                continue
            for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                V, H = at(x, y + dy), at(x + dx, y)
                if V is None or H is None or V != H or V == A or V[3] == 0:
                    continue
                C = V
                if at(x, y - dy) == C or at(x - dx, y) == C:
                    continue  # pocket, not a step
                hr = 0  # horizontal run of C in row y+dy going away from the corner
                xx = x
                while hr < 12 and at(xx, y + dy) == C and at(xx, y) is not None and at(xx, y) != C:
                    hr += 1
                    xx -= dx
                vr = 0
                yy = y
                while vr < 12 and at(x + dx, yy) == C and at(x, yy) is not None and at(x, yy) != C:
                    vr += 1
                    yy -= dy
                if min(hr, vr) != 1 or max(hr, vr) < min_run:
                    continue
                put(x, y, A, C, strength)
                if hr >= 4 and at(x - dx, y) == A:
                    put(x - dx, y, A, C, strength * 0.5)
                if vr >= 4 and at(x, y - dy) == A:
                    put(x, y - dy, A, C, strength * 0.5)
    write_pixels(data, px, changes)
    return len(changes)


def cleanup(w, h, data, doubles=True, orphans=True, orphan_min=6, region=None):
    """Pixel-perfect pass. doubles: remove L-shaped corner pixels from 1px lines/outlines.
    orphans: replace isolated single pixels (no same-colour 8-neighbour) sitting in an area where at
    least `orphan_min` of 8 neighbours share one colour. Returns (doubles_removed, orphans_removed)."""
    px = to_pixels(data)
    nd = no = 0
    N8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]

    def at(x, y):
        return px[y * w + x] if 0 <= x < w and 0 <= y < h else (0, 0, 0, 0)

    if doubles:
        for y in range(h):
            for x in range(w):
                C = px[y * w + x]
                if C[3] == 0 or not _in_region(x, y, region):
                    continue
                same = [(dx, dy) for dx, dy in N8 if at(x + dx, y + dy) == C]
                if len(same) < 2 or len(same) > 4:
                    continue
                elbow = None
                for hx in (-1, 1):  # one horizontal + one vertical neighbour forming an L ...
                    for vy in (-1, 1):
                        if (hx, 0) in same and (0, vy) in same and (hx, vy) not in same:  # ... with an open elbow
                            rest = [s for s in same if s not in ((hx, 0), (0, vy))]
                            # every other same-colour neighbour must touch the L, so removing P keeps the line connected
                            if all(max(abs(sx - hx), abs(sy)) <= 1 or max(abs(sx), abs(sy - vy)) <= 1 for sx, sy in rest):
                                elbow = (hx, vy)
                if elbow is None:
                    continue
                ox, oy = -elbow[0], -elbow[1]  # outer side of the elbow
                cnt = {}
                for c in (at(x + ox, y), at(x, y + oy), at(x + ox, y + oy)):
                    cnt[c] = cnt.get(c, 0) + 1
                px[y * w + x] = max(cnt.items(), key=lambda kv: kv[1])[0]
                nd += 1
    if orphans:
        out = list(px)
        for y in range(h):
            for x in range(w):
                if not _in_region(x, y, region):
                    continue
                P = px[y * w + x]
                nb = [at(x + dx, y + dy) for dx, dy in N8]
                if P in nb:
                    continue
                cnt = {}
                for c in nb:
                    cnt[c] = cnt.get(c, 0) + 1
                c, n = max(cnt.items(), key=lambda kv: kv[1])
                if n >= orphan_min:
                    out[y * w + x] = c
                    no += 1
        px = out
    for i, p in enumerate(px):
        data[i * 4:i * 4 + 4] = bytes(p)
    return nd, no


def shade(w, h, data, mask_colors, ramp, light="top-left", elevation=0.9, roundness=1.0,
          ambient=0.0, dither=False, region=None):
    """Form shading: treat the pixels of `mask_colors` as a rounded volume (height from distance to
    the shape's edge), light it from `light`, and paint it with `ramp` (dark -> light colours).
    Gives a solid base of light/shadow that follows the silhouette; refine by hand afterwards."""
    px = to_pixels(data)
    masks = set(tuple(c[:3]) for c in mask_colors)
    inside = [p[3] > 0 and p[:3] in masks and _in_region(i % w, i // w, region) for i, p in enumerate(px)]
    if not any(inside):
        return 0
    INF = 1e9
    d = [INF if m else 0.0 for m in inside]
    r2 = math.sqrt(2)
    for y in range(h):  # chamfer distance transform, forward + backward pass
        for x in range(w):
            k = y * w + x
            if d[k] == 0:
                continue
            best = d[k]
            for dx, dy, c in ((-1, 0, 1), (0, -1, 1), (-1, -1, r2), (1, -1, r2)):
                xx, yy = x + dx, y + dy
                v = d[yy * w + xx] if 0 <= xx < w and 0 <= yy < h else 0.0
                best = min(best, v + c)
            d[k] = best
    for y in range(h - 1, -1, -1):
        for x in range(w - 1, -1, -1):
            k = y * w + x
            if d[k] == 0:
                continue
            best = d[k]
            for dx, dy, c in ((1, 0, 1), (0, 1, 1), (1, 1, r2), (-1, 1, r2)):
                xx, yy = x + dx, y + dy
                v = d[yy * w + xx] if 0 <= xx < w and 0 <= yy < h else 0.0
                best = min(best, v + c)
            d[k] = best
    R = max(d) * max(0.2, roundness)
    H = [0.0] * (w * h)
    for k in range(w * h):
        if inside[k]:
            t = min(d[k] - 0.5, R) / R if R > 0 else 1.0
            t = max(0.0, t)
            H[k] = R * math.sqrt(max(0.0, 1 - (1 - t) ** 2))   # circular dome profile
    lx, ly = light_vector(light)
    L = (lx, ly, elevation)
    ln = math.sqrt(sum(v * v for v in L))
    L = tuple(v / ln for v in L)
    vals = {}
    for y in range(h):
        for x in range(w):
            k = y * w + x
            if not inside[k]:
                continue
            hx0 = H[k - 1] if x > 0 else 0.0
            hx1 = H[k + 1] if x < w - 1 else 0.0
            hy0 = H[k - w] if y > 0 else 0.0
            hy1 = H[k + w] if y < h - 1 else 0.0
            gx, gy = (hx1 - hx0) / 2, (hy1 - hy0) / 2
            n = (-gx, -gy, 1.0)
            nn = math.sqrt(sum(v * v for v in n))
            vals[k] = sum(n[i] * L[i] for i in range(3)) / nn + ambient
    lo, hi = min(vals.values()), max(vals.values())
    span = (hi - lo) or 1.0
    n = len(ramp)
    for k, v in vals.items():
        f = (v - lo) / span * (n - 1)
        i = int(f)
        frac = f - i
        if dither:
            from canvas import dither_on
            i = i + 1 if dither_on(frac, k % w, k // w) else i
        else:
            i = int(round(f))
        c = ramp[max(0, min(n - 1, i))]
        data[k * 4:k * 4 + 4] = bytes((c[0], c[1], c[2], 255))
    return len(vals)


# ------------------------------------------------------------------ transforms (RotSprite-style)

def _scale2x(w, h, px):
    out = [None] * (w * 2 * h * 2)
    W = w * 2
    for y in range(h):
        for x in range(w):
            P = px[y * w + x]
            A = px[(y - 1) * w + x] if y > 0 else P
            B = px[y * w + x + 1] if x < w - 1 else P
            C = px[y * w + x - 1] if x > 0 else P
            D = px[(y + 1) * w + x] if y < h - 1 else P
            e0 = A if (C == A and C != D and A != B) else P
            e1 = B if (A == B and A != C and B != D) else P
            e2 = C if (D == C and D != B and C != A) else P
            e3 = D if (B == D and B != A and D != C) else P
            o = (y * 2) * W + x * 2
            out[o], out[o + 1], out[o + W], out[o + W + 1] = e0, e1, e2, e3
    return W, h * 2, out


def rotate_sprite(w, h, px, degrees, smooth=True):
    """Rotate a list of RGBA tuples. Multiples of 90 are exact; other angles use RotSprite
    (Scale2x x3, nearest rotation, sample back down) which keeps pixel-art lines clean.
    Returns (w, h, px) of the rotated bounding box."""
    deg = degrees % 360
    if deg % 90 == 0:
        q = int(deg // 90)
        for _ in range(q):
            px = [px[(h - 1 - x) * w + y] for y in range(w) for x in range(h)]  # rotate 90 cw
            w, h = h, w
        return w, h, px
    f = 1
    sw, sh, sp = w, h, px
    if smooth:
        for _ in range(3):
            sw, sh, sp = _scale2x(sw, sh, sp)
        f = 8
    rad = math.radians(deg)
    cs, sn = math.cos(rad), math.sin(rad)
    nw = int(math.ceil(abs(w * cs) + abs(h * sn)))
    nh = int(math.ceil(abs(w * sn) + abs(h * cs)))
    cx, cy = w / 2.0, h / 2.0
    ncx, ncy = nw / 2.0, nh / 2.0
    out = []
    for y in range(nh):
        for x in range(nw):
            # centre of destination pixel, inverse-rotated into source space (clockwise rotation)
            dx, dy = x + 0.5 - ncx, y + 0.5 - ncy
            sx = dx * cs + dy * sn + cx
            sy = -dx * sn + dy * cs + cy
            ix, iy = int(math.floor(sx * f)), int(math.floor(sy * f))
            if 0 <= ix < sw and 0 <= iy < sh:
                out.append(sp[iy * sw + ix])
            else:
                out.append((0, 0, 0, 0))
    return nw, nh, out


def scale_sprite(w, h, px, sx, sy):
    nw, nh = max(1, int(round(w * sx))), max(1, int(round(h * sy)))
    out = [px[min(h - 1, int((y + 0.5) * h / nh)) * w + min(w - 1, int((x + 0.5) * w / nw))]
           for y in range(nh) for x in range(nw)]
    return nw, nh, out


# ------------------------------------------------------------------ shading transfer & layer split

def luminance_transfer(w, h, source, labels, ramps, mode="relative", base_index=None, contrast=1.0,
                       low_pct=0.05, high_pct=0.95):
    """Repaint a sprite from an unquantized source (e.g. a downscaled concept) using per-material ramps.

    source: RGBA bytes (w*h*4) whose lightness carries the concept's lighting.
    labels: list of material ids per pixel (None = leave transparent).
    ramps:  {material_id: [rgb dark .. light]}.
    mode 'relative' (default): the material's median source lightness maps to its ramp base colour and every
      other pixel picks the ramp step whose lightness offset is closest -> the concept's real contrast is kept.
    mode 'stretch': the material's source lightness range (percentiles) is stretched over the whole ramp.
    base_index: {material_id: index of the base colour in its ramp} (default: middle).
    Returns RGBA bytearray."""
    out = bytearray(w * h * 4)
    by_mat = {}
    for k, m in enumerate(labels):
        if m is not None and source[k * 4 + 3] > 0:
            by_mat.setdefault(m, []).append(k)
    for m, idxs in by_mat.items():
        ramp = ramps[m]
        n = len(ramp)
        Ls = [rgb_to_lab(tuple(source[k * 4:k * 4 + 3]))[0] for k in idxs]
        srt = sorted(Ls)
        if mode == "stretch":
            lo = srt[int(low_pct * (len(srt) - 1))]
            hi = srt[int(high_pct * (len(srt) - 1))]
            span = hi - lo
            pick = [min(n - 1, int(round((0.5 if span < 1e-6 else min(1.0, max(0.0, (L - lo) / span))) * (n - 1))))
                    for L in Ls]
        else:
            bi = (base_index or {}).get(m, (n - 1) // 2)
            rampL = [rgb_to_lab(c)[0] for c in ramp]
            med = srt[len(srt) // 2]
            pick = []
            for L in Ls:
                target = rampL[bi] + (L - med) * contrast
                pick.append(min(range(n), key=lambda i: abs(rampL[i] - target)))
        for k, i in zip(idxs, pick):
            c = ramp[i]
            out[k * 4:k * 4 + 4] = bytes((c[0], c[1], c[2], 255))
    return out


def split_by_labels(w, h, data, labels, order):
    """Split RGBA data into one layer per label (in `order`). Returns {label: bytearray}."""
    layers = {m: bytearray(w * h * 4) for m in order}
    for k, m in enumerate(labels):
        if m in layers and data[k * 4 + 3]:
            layers[m][k * 4:k * 4 + 4] = data[k * 4:k * 4 + 4]
    return layers


# ------------------------------------------------------------------ lint

def _components(w, h, mask):
    """8-connected components of a boolean mask. Returns list of pixel counts."""
    seen = bytearray(w * h)
    sizes = []
    for start in range(w * h):
        if not mask[start] or seen[start]:
            continue
        stack, n = [start], 0
        seen[start] = 1
        while stack:
            k = stack.pop()
            n += 1
            x, y = k % w, k // w
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < w and 0 <= yy < h:
                        j = yy * w + xx
                        if mask[j] and not seen[j]:
                            seen[j] = 1
                            stack.append(j)
        sizes.append(n)
    return sizes


def lint(w, h, data, palette=None, max_colors=None):
    """Objective quality numbers for one RGBA image, plus a list of issues.

    Returns {"metrics": {...}, "issues": [{"code", "severity", "message"}...], "score": 0..100}.
    The score is a rough heuristic for ranking candidates, not an aesthetic judgement."""
    px = to_pixels(data)
    opaque = [p[3] > 0 for p in px]
    colors = {p[:3] for p in px if p[3] > 0}
    semi = sum(1 for p in px if 0 < p[3] < 255)
    pal = {tuple(c[:3]) for c in palette} if palette else None
    off_palette = sum(1 for p in px if p[3] > 0 and pal is not None and p[:3] not in pal)
    tmp = bytearray(data)
    doubles, orphans = cleanup(w, h, tmp, doubles=True, orphans=True)
    jaggies = antialias(w, h, bytearray(data), 0.5, None)
    comps = sorted(_components(w, h, opaque), reverse=True)
    stray_fragments = sum(1 for s in comps[1:] if s <= 3)
    area = sum(opaque)
    xs = [k % w for k in range(w * h) if opaque[k]]
    ys = [k // w for k in range(w * h) if opaque[k]]
    bbox = (min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1) if xs else (0, 0, 0, 0)
    noise = shade_noise(w, h, px)
    metrics = {
        "size": [w, h], "opaque_pixels": area, "bbox": list(bbox), "colors": len(colors),
        "semi_transparent": semi, "off_palette": off_palette, "doubled_corners": doubles,
        "orphan_pixels": orphans, "jaggies": jaggies, "components": len(comps),
        "stray_fragments": stray_fragments, "shade_changes": noise["shade_changes"],
        "singletons": noise["singletons"],
        "touches_edge": bool(xs) and (bbox[0] == 0 or bbox[1] == 0 or bbox[0] + bbox[2] == w or bbox[1] + bbox[3] == h),
    }
    issues = []

    def add(code, sev, msg):
        issues.append({"code": code, "severity": sev, "message": msg})

    if area == 0:
        add("empty", "error", "image has no opaque pixels")
    if semi:
        add("semi_transparent", "error", f"{semi} semi-transparent pixels (sprites should use 0/255 alpha)")
    if off_palette:
        add("off_palette", "error", f"{off_palette} pixels use colours outside the palette")
    if max_colors and len(colors) > max_colors:
        add("too_many_colors", "warning", f"{len(colors)} colours > budget {max_colors}")
    if orphans:
        add("orphans", "warning", f"{orphans} isolated stray pixels")
    if stray_fragments:
        add("stray_fragments", "warning", f"{stray_fragments} tiny detached fragments")
    if doubles > max(2, area // 400):
        add("doubled_corners", "info", f"{doubles} doubled corners in single-colour lines")
    if jaggies > max(4, area // 150):
        add("jaggies", "info", f"{jaggies} unsmoothed stair steps on curves")
    if metrics["touches_edge"]:
        add("touches_edge", "warning", "sprite touches the canvas edge (no room for outline/animation)")
    if noise["shade_changes"] > 0.35:
        add("shade_noise", "warning", f"{noise['shade_changes']:.0%} of neighbouring pixels differ: fragmented, "
                                      "noisy shading instead of clusters")
    if noise["singleton_ratio"] > 0.05:
        add("singletons", "warning", f"{noise['singletons']} pixels ({noise['singleton_ratio']:.0%}) have no same-colour "
                                     "neighbour: speckle")
    weights = {"error": 15, "warning": 6, "info": 2}
    penalty = sum(weights[i["severity"]] for i in issues)
    penalty += min(20, orphans * 0.5 + stray_fragments * 2 + jaggies * 0.1 + doubles * 0.1)
    # fragmentation: pixel art is built from clusters; ~25% neighbour changes is normal for a detailed sprite
    penalty += min(25, max(0.0, noise["shade_changes"] - 0.25) * 60 + noise["singleton_ratio"] * 100)
    metrics_score = max(0, 100 - penalty)
    return {"metrics": metrics, "issues": issues, "score": round(metrics_score, 1)}


def lint_frames(w, h, frames):
    """Consistency checks across animation frames (list of RGBA bytes)."""
    areas, palettes = [], []
    for d in frames:
        areas.append(sum(1 for i in range(3, len(d), 4) if d[i]))
        palettes.append({tuple(d[i:i + 3]) for i in range(0, len(d), 4) if d[i + 3]})
    common = set.intersection(*palettes) if palettes else set()
    union = set.union(*palettes) if palettes else set()
    mean = sum(areas) / len(areas) if areas else 0
    spread = (max(areas) - min(areas)) / mean if mean else 0
    issues = []
    if spread > 0.25:
        issues.append({"code": "volume_drift", "severity": "warning",
                       "message": f"opaque area varies by {spread:.0%} between frames (volume not preserved?)"})
    rare = len(union - common)
    if rare > max(2, len(union) // 4):
        issues.append({"code": "palette_drift", "severity": "warning",
                       "message": f"{rare} colours appear in only some frames"})
    return {"metrics": {"areas": areas, "area_spread": round(spread, 3), "colors_union": len(union),
                        "colors_common": len(common)}, "issues": issues}


# ------------------------------------------------------------------ cluster quality & fidelity

N4 = ((1, 0), (-1, 0), (0, 1), (0, -1))


def shade_noise(w, h, px):
    """Fragmentation of an image (list of RGBA tuples): share of 4-neighbour pairs (both opaque) with different
    colours, and speckle = pixels with >= 2 opaque 4-neighbours and no same-colour pixel among all 8 neighbours
    (diagonal runs such as pixel-perfect outlines or diagonal highlights are legitimate structure)."""
    pairs = changes = singles = opaque = 0
    for y in range(h):
        for x in range(w):
            p = px[y * w + x]
            if not p[3]:
                continue
            opaque += 1
            same = nbrs = 0
            for dx, dy in N4:
                xx, yy = x + dx, y + dy
                if 0 <= xx < w and 0 <= yy < h:
                    q = px[yy * w + xx]
                    if q[3]:
                        nbrs += 1
                        same += q == p
                        if dx > 0 or dy > 0:
                            pairs += 1
                            changes += q != p
            if nbrs >= 2 and same == 0:
                diag = False
                for dx, dy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < w and 0 <= yy < h and px[yy * w + xx] == p:
                        diag = True
                        break
                if not diag:
                    singles += 1
    return {"shade_changes": round(changes / pairs, 3) if pairs else 0.0, "singletons": singles,
            "singleton_ratio": round(singles / opaque, 3) if opaque else 0.0}


def fidelity(w, h, data, ref, region=None, blur=1):
    """How far `data` is from a reference rendering `ref` (the concept downscaled to the same grid).

    The reference is a box-filtered, i.e. blurred, image: crisp pixel-art detail (a 1-px eye mark, a hard
    edge) differs from it pixel by pixel even when it is exactly right. So both images are compared after the
    same (2*blur+1)^2 box average in OKLab: rearranging pixels within a neighbourhood costs little, changing
    what colours are there (deleting a red emblem, greying a wooden bow) costs a lot.
    Returns mean OKLab distance over pixels opaque in both, and the share of reference pixels missing."""
    x0, y0, rw, rh = region or (0, 0, w, h)
    cache = {}

    def lab(c):
        v = cache.get(c)
        if v is None:
            v = cache[c] = rgb_to_lab(c)
        return v

    def labs(img):
        return [lab(tuple(img[i:i + 3])) if img[i + 3] else None for i in range(0, w * h * 4, 4)]

    A, B = labs(data), labs(ref)

    def avg(L, x, y):
        s0 = s1 = s2 = 0.0
        n = 0
        for yy in range(max(0, y - blur), min(h, y + blur + 1)):
            for xx in range(max(0, x - blur), min(w, x + blur + 1)):
                v = L[yy * w + xx]
                if v is not None:
                    s0 += v[0]
                    s1 += v[1]
                    s2 += v[2]
                    n += 1
        return (s0 / n, s1 / n, s2 / n)

    tot = n = missing = refn = 0
    for y in range(max(0, y0), min(h, y0 + rh)):
        for x in range(max(0, x0), min(w, x0 + rw)):
            k = y * w + x
            if B[k] is None:
                continue
            refn += 1
            if A[k] is None:
                missing += 1
                continue
            a, b = (avg(A, x, y), avg(B, x, y)) if blur else (A[k], B[k])
            tot += ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5
            n += 1
    return {"delta_e": round(tot / n, 4) if n else 0.0, "missing": round(missing / refn, 4) if refn else 0.0,
            "pixels": refn}


def lightness_levels(values, k, iters=25, extreme_pct=0.01):
    """Up to k sorted OKLab lightness levels for a material.

    Range-preserving: with k >= 3 the end levels sit at the extreme_pct / 1-extreme_pct percentiles (deepest
    shadow, brightest highlight) and 1-D k-means places only the interior levels. Plain k-means is weighted by
    pixel count, so the few highlight/shadow pixels that give pixel art its contrast get averaged away."""
    vals = sorted(values)
    if not vals:
        return []
    if k >= 3 and len(vals) >= 8:
        lo = vals[int(extreme_pct * (len(vals) - 1))]
        hi = vals[int((1 - extreme_pct) * (len(vals) - 1))]
        inner = [v for v in vals if lo + 0.02 < v < hi - 0.02]
        mids = lightness_levels(inner, k - 2, iters, extreme_pct) if inner else []
        out = []
        for c in [lo] + mids + [hi]:
            if not out or c - out[-1] > 0.02:
                out.append(c)
        return out
    k = max(1, min(k, len(set(round(v, 3) for v in vals))))
    cents = [vals[min(len(vals) - 1, int((i + 0.5) * len(vals) / k))] for i in range(k)]
    for _ in range(iters):
        groups = [[] for _ in cents]
        for v in vals:
            groups[min(range(len(cents)), key=lambda j: abs(v - cents[j]))].append(v)
        new = [sum(g) / len(g) if g else c for g, c in zip(groups, cents)]
        if max(abs(a - b) for a, b in zip(new, cents)) < 1e-5:
            break
        cents = new
    out = []
    for c in sorted(cents):
        if not out or c - out[-1] > 0.02:
            out.append(c)
    return out


def ramp_for_levels(base, levels, hue_shift=24.0, sat_falloff=0.35):
    """A hue-shifted ramp whose lightness steps sit exactly at `levels` (dark -> light): steps darker than the
    base drift toward cool, lighter ones toward warm, chroma eases off in the highlights."""
    L0, C0, h0 = rgb_to_lch(base)
    span = max([abs(L - L0) for L in levels] + [0.05])
    out = []
    for L in levels:
        t = max(-1.0, min(1.0, (L - L0) / span))
        target = WARM_HUE if t > 0 else COOL_HUE
        C, hh = C0, h0
        if C < 0.02:
            hh, C = target, 0.01 + 0.015 * abs(t)
        hh = _rotate_hue(hh, target, hue_shift * abs(t))
        C = C * (1 - sat_falloff * t) if t > 0 else C * (1 - 0.12 * abs(t))
        out.append(lch_to_rgb(min(0.98, max(0.04, L)), C, hh))
    return out


def smooth_lightness(w, h, data, labels, keep_contrast=0.09):
    """OKLab lightness per pixel after a 3x3 median taken only over neighbours of the SAME label: removes
    texture/aliasing speckle while keeping edges between materials and larger light/shadow shapes.
    Pixels deviating from that median by more than keep_contrast keep their own lightness: noise is
    low-amplitude, while thin highlights and crease lines are high-amplitude structure that must survive."""
    L = [rgb_to_lab(tuple(data[k * 4:k * 4 + 3]))[0] if labels[k] is not None and data[k * 4 + 3] else None
         for k in range(w * h)]
    out = list(L)
    for y in range(h):
        for x in range(w):
            k = y * w + x
            if L[k] is None:
                continue
            vals = []
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < w and 0 <= yy < h:
                        j = yy * w + xx
                        if L[j] is not None and labels[j] == labels[k]:
                            vals.append(L[j])
            vals.sort()
            med = vals[len(vals) // 2]
            out[k] = med if abs(L[k] - med) <= keep_contrast else L[k]
    return out


def merge_small_clusters(w, h, data, labels, min_size=3, passes=3, max_dl=0.13, connectivity=4):
    """Within each label, recolour same-colour clusters (4-connected) smaller than min_size with the most common
    colour of their same-label neighbours. Turns per-pixel quantisation noise into deliberate clusters.
    Only clusters within max_dl lightness of that neighbour colour are merged (adjacent-step flicker);
    small clusters far brighter/darker (glints, crease lines) are deliberate and stay.
    connectivity=8 treats diagonal steps as connected (use for 1-px lines such as outlines).
    Returns the number of pixels recoloured."""
    nbrs = N4 if connectivity == 4 else N4 + ((1, 1), (1, -1), (-1, 1), (-1, -1))
    Lc = {}

    def lum(c):
        v = Lc.get(c)
        if v is None:
            v = Lc[c] = rgb_to_lab(c[:3])[0]
        return v
    changed_total = 0
    for _ in range(passes):
        px = [tuple(data[i:i + 4]) for i in range(0, len(data), 4)]
        seen = bytearray(w * h)
        changed = 0
        for start in range(w * h):
            if seen[start] or labels[start] is None or not px[start][3]:
                continue
            col, lab_ = px[start], labels[start]
            # flood over CURRENT colours (pixels merged earlier in this pass join their new cluster);
            # `seen` only decides where new floods start
            comp, stack, inflood = [], [start], {start}
            seen[start] = 1
            while stack:
                k = stack.pop()
                comp.append(k)
                x, y = k % w, k // w
                for dx, dy in nbrs:
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < w and 0 <= yy < h:
                        j = yy * w + xx
                        if j not in inflood and labels[j] == lab_ and px[j] == col:
                            inflood.add(j)
                            seen[j] = 1
                            stack.append(j)
            if len(comp) >= min_size:
                continue
            votes = {}
            for k in comp:
                x, y = k % w, k // w
                for dx, dy in nbrs:
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < w and 0 <= yy < h:
                        j = yy * w + xx
                        if labels[j] == lab_ and px[j] != col and px[j][3]:
                            votes[px[j]] = votes.get(px[j], 0) + 1
            if not votes:
                continue
            new = max(votes.items(), key=lambda kv: kv[1])[0]
            if abs(lum(new) - lum(col)) > max_dl:
                continue
            for k in comp:
                data[k * 4:k * 4 + 4] = bytes(new)
                px[k] = new  # later clusters in this pass must see the merge (else neighbours swap colours)
            changed += len(comp)
        changed_total += changed
        if not changed:
            break
    return changed_total
