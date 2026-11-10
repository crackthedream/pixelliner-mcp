"""Face kit: anime-style pixel-art eyes, mouths, brows and blush as drawing ops.

Built from how hand-made sprites in this style construct a face (studied pixel by pixel on Alice in Cradle's Noel:
~150-px sprites, faces 16-20 px wide, 261 faces measured):
- An open eye is TALLER than wide. A dark lash runs across the top (soft ends) and continues down the OUTER side as
  a wing that flicks out past the eye; inside that corner sits a soft white. The iris hangs under the lash as a
  vertical body in a mid tone, the pupil dark, low and toward the nose, the bottom lighter. No lower outline: the
  eye simply ends. Both eyes are exact mirror images.
- Eyes and the gap between them span almost the whole face width (gap a little under one eye width); the eyes sit
  just under the fringe and low in the face. No nose; brows are usually hidden by the fringe.
- Expressions come from the lash: lowered (half), slanting up to the inner end (sad), slanting down to the inner
  end (angry), ^ arcs (happy), a soft line (closed); surprised eyes show more white.
- Mouths are small and soft: none or a 1-2 px line for calm faces, a round salmon blob for an open mouth (darker on
  top), never a dark outline.
- Skin is flat. The `skin` op flattens blotchy near-skin pixels in the face to one skin colour.

Template letters: L lash, A lash softened into the skin (wing, lash ends), D pupil, I iris, B iris light, F faint
iris, H highlight, W eye white, '.' unchanged. Templates are for the viewer's LEFT eye (outer corner on the left);
the right eye is its mirror image (a highlight H, if a template uses one, keeps its side: one light source).
Colours not given are derived from the iris, the lash and the skin under the face (sampled from the canvas)."""

EYES = {
    "s": {  # faces under ~12 px
        "open":   ["ALL", "WDI", "..B"],
        "half":   ["...", "ALL", "WDB"],
        "happy":  [".L.", "L.L"],
        "closed": ["...", "ALL"],
        "wide":   ["LLL", "WDW", ".W."],
        "sad":    ["..L", "AL.", ".D."],
        "angry":  ["L..", ".LL", ".D."],
    },
    "m": {  # faces ~12-15 px
        "open":   [".ALLA", "ALWII", ".AWDI", "...DB"],
        "half":   [".....", ".ALLA", "ALWDI", "...DB"],
        "happy":  [".LLL.", "L...L"],
        "closed": [".....", ".....", ".LLLA", "A...."],
        "wide":   [".ALLA", "ALWIW", ".AWDW", "...W."],
        "sad":    ["...LA", ".ALL.", "ALWDI", "...DB"],
        "angry":  ["LL...", ".ALLA", ".AWDI", "...DB"],
    },
    "l": {  # faces ~16-18 px
        "open":   [".ALLLA", "ALWIII", ".AWIDI", "...IDB", "....B."],
        "half":   ["......", ".ALLLA", "ALWIDI", "...IDB"],
        "happy":  [".LLLL.", "L....L"],
        "closed": ["......", "......", ".LLLLA", "A....."],
        "wide":   [".ALLLA", "ALWIIW", ".AWDIW", "...WW."],
        "sad":    ["....LA", ".ALLL.", "ALWIII", ".AWIDI", "...IDB"],
        "angry":  ["ALL...", "..LLLA", ".AWIDI", "...IDB"],
    },
    "xl": {  # faces ~19 px and wider
        "open":   ["..ALLLA", "AALWIII", ".AAWIII", "..AWIDB", "....IDB", ".....B."],
        "half":   [".......", ".......", "..ALLLA", "AALWIDI", "...WIDB", "....IB."],
        "happy":  ["..LLL..", ".L...L.", "L.....L"],
        "closed": [".......", ".......", ".......", "..LLLLA", ".A.....", "A......"],
        "wide":   ["..ALLLA", "AALWIIW", ".AAWDIW", "...WDIW", "....WW."],
        "sad":    [".....LA", "..ALLL.", "AALWIII", ".AAWIDI", "...WIDB", "....IB."],
        "angry":  ["AALL...", "...LLLA", "..AWIDI", "...WIDB", "....IB."],
    },
    "xxl": {  # faces ~22 px and wider (big-headed characters at ~160 px): the eye stays a third of the face
        "open":   ["...ALLLLA", "AALLWIIII", ".AAWWIIII", "..AWIIDDI", "...WIIDDB", "....IBDDB", ".....BBB."],
        "half":   [".........", ".........", "...ALLLLA", "AALLWIIII", "..AWIIDDI", "...WIIDDB", ".....BBB."],
        "happy":  ["...LLL...", ".LL...LL.", "L.......L"],
        "closed": [".........", ".........", ".........", "...LLLLLA", "..A......", "AA......."],
        "wide":   ["...ALLLLA", "AALLWWIIW", ".AAWWIDIW", "..AWWIDIW", "...WWIIW.", ".....WW.."],
        "sad":    ["......LLA", "...ALLL..", "AALLWIIII", ".AAWWIDDI", "..AWIIDDB", "....IBDDB", ".....BBB."],
        "angry":  ["AALL.....", "...LLLLLA", "...AWIIII", "..AWIIDDI", "...WIIDDB", "....IBDDB", ".....BBB."],
    },
}

GAPS = {"s": 3, "m": 4, "l": 5, "xl": 5, "xxl": 6}   # px between the eyes: a little under one eye width, as in the game

MOUTHS = {  # M soft line (skin, darkened), A softer end, K open-mouth top (deeper), T open-mouth inside (salmon)
    "none":       [],
    "dot":        ["M"],
    "line":       ["MM"],
    "smile":      ["A..A", ".MM."],
    "smile_wide": ["A...A", ".MMM."],
    "open_smile": [".KK.", "TTTT", ".TT."],
    "open_wide":  ["KKKK", "TTTT", ".TT."],
    "o":          ["KK", "TT"],
    "frown":      [".MM.", "A..A"],
    "cat":        ["M.M", ".A."],
}

BROWS = {  # for the viewer's LEFT brow (inner end on the right); the right brow is mirrored
    "flat":    ["BBB"],
    "raised":  [".B.", "B.B"],
    "angry":   ["B..", ".BB"],
    "worried": ["..B", "BB."],
    "soft":    ["BB.", "..B"],
}

DEFAULT_SKIN = (246, 214, 190, 255)


def _shift(c, f):
    return tuple(max(0, min(255, round(v * f))) for v in c[:3]) + (255,)


def _mix(c, d, t):
    return tuple(round(a + (b - a) * t) for a, b in zip(c[:3], d[:3])) + (255,)


def _skin(doc, op, x, y):
    """The skin colour: given, else sampled from the visible canvas at (x, y), else a default."""
    if op.get("skin"):
        return doc.color(op["skin"])
    try:
        if 0 <= x < doc.w and 0 <= y < doc.h:
            c = tuple(doc.composite(region=(x, y, 1, 1))[:4])
            if c[3] >= 200:
                return c[:3] + (255,)
    except Exception:  # noqa: BLE001 - sampling is a convenience; fall back to the default
        pass
    return DEFAULT_SKIN


def _stamp(p, x0, y0, rows, colors, mirror=False, keep_letter_side=None):
    w = max((len(r) for r in rows), default=0)
    kept = []
    for j, row in enumerate(rows):
        for i, ch in enumerate(row.ljust(w, ".")):
            if ch == "." or ch not in colors:
                continue
            if mirror and keep_letter_side and ch in keep_letter_side:
                # the highlight keeps its side (one light source): the mirrored slot gets the iris colour, and
                # the highlight itself is drawn last so no mirrored pixel can cover it
                kept.append((x0 + i, y0 + j, colors[ch]))
                ch = "I" if "I" in colors else ch
            p.plot(x0 + (w - 1 - i if mirror else i), y0 + j, colors[ch])
    for x, y, c in kept:
        p.plot(x, y, c)


def _pair_x(cx, w, gap):
    lx = cx - (2 * w + gap) // 2
    return lx, lx + w + gap


def op_eyes(doc, p, op):
    """{op: eyes, cx, y, size: s|m|l|xl|xxl (3/5/6/7/9 px wide), style: open|half|happy|closed|wide|sad|angry|wink_left|wink_right,
    turn?: front|left|right (a three-quarter face turned toward the viewer's left/right: that side's eye is narrower), gap?,
    iris, iris_dark?, iris_light?, lash?, highlight?, white?, skin?}  Both eyes, mirror-symmetric around cx."""
    size = str(op.get("size", "m")).lower()
    if size not in EYES:
        raise ValueError("eyes: size must be s, m, l, xl or xxl")
    style = str(op.get("style", "open")).lower()
    wink = None
    if style.startswith("wink"):
        wink, style = ("left" if style.endswith("left") else "right"), "open"
    if style not in EYES[size]:
        raise ValueError(f"eyes: style must be one of {sorted(EYES[size])} or wink_left / wink_right")
    cx, y = int(round(float(op["cx"]))), int(round(float(op["y"])))
    w = len(EYES[size]["open"][0])
    gap = int(op.get("gap", GAPS[size]))
    skin = _skin(doc, op, cx, y + 1)
    given = doc.color(op.get("iris", "#4a6fb0"))
    lash = doc.color(op.get("lash", "#1c1219"))
    # the iris body is a MID tone (the game's irises sit around luminance 110-120); a light or bright concept colour
    # is kept for the lower iris instead -- a bright flat block in the eye reads as goggles, not an eye
    lum = 0.2126 * given[0] + 0.7152 * given[1] + 0.0722 * given[2]
    iris = _shift(given, 118 / lum) if lum > 125 else given
    light = doc.color(op["iris_light"]) if op.get("iris_light") else (
        _mix(given, (255, 255, 255), 0.15) if lum > 125 else _mix(given, (255, 255, 255), 0.38))
    colors = {
        "L": lash,
        "A": _mix(lash, skin, 0.5),
        "D": doc.color(op["iris_dark"]) if op.get("iris_dark") else _mix(_shift(iris, 0.45), lash, 0.45),
        "I": iris,
        "B": light,
        "F": _mix(light, skin, 0.55),
        "H": doc.color(op.get("highlight", "#ffffff")),
        # a soft white tinted toward the iris: pure white beside a near-black lash reads as a hole or a sideways glance
        "W": doc.color(op["white"]) if op.get("white") else _mix((252, 251, 250), iris, 0.12),
    }
    left_rows = EYES[size]["closed" if wink == "left" else style]
    right_rows = EYES[size]["closed" if wink == "right" else style]
    turn = str(op.get("turn", "front")).lower()
    if turn in ("left", "right") and w >= 4:
        # a three-quarter face: the far eye (on the side the face turns to) is narrower -- its inner column is
        # dropped -- and sits a pixel closer to the near eye, as hand-made sprites draw a turned head
        far = _narrow(left_rows if turn == "left" else right_rows)
        if turn == "left":
            left_rows = far
        else:
            right_rows = far
        lw, rw = len(left_rows[0]), len(right_rows[0])
        g = max(1, gap - 1)
        lx = cx - (lw + g + rw) // 2
        rx = lx + lw + g
    else:
        lx, rx = _pair_x(cx, w, gap)
    _stamp(p, lx, y, left_rows, colors)
    _stamp(p, rx, y, right_rows, colors, mirror=True, keep_letter_side="H")


def _narrow(rows):
    """The template one column narrower: the column next to the inner edge goes (iris and lash are kept)."""
    k = len(rows[0]) - 2
    return [r[:k] + r[k + 1:] for r in rows]


def op_mouth(doc, p, op):
    """{op: mouth, cx, y, shape: none|dot|line|smile|smile_wide|open_smile|open_wide|o|frown|cat, color?, inner?, skin?}
    Small and soft: colours default to the skin under the mouth, darkened (line) or warmed (open mouth)."""
    shape = str(op.get("shape", "line")).lower()
    if shape not in MOUTHS:
        raise ValueError(f"mouth: shape must be one of {sorted(MOUTHS)}")
    rows = MOUTHS[shape]
    if not rows:
        return
    cx, y = int(round(float(op["cx"]))), int(round(float(op["y"])))
    skin = _skin(doc, op, cx, y - 1)
    m = doc.color(op["color"]) if op.get("color") else _mix(skin, (122, 46, 52), 0.5)
    t = doc.color(op["inner"]) if op.get("inner") else _mix(skin, (232, 104, 104), 0.55)
    colors = {"M": m, "A": _mix(m, skin, 0.5), "K": _mix(t, (150, 40, 52), 0.4), "T": t}
    w = max(len(r) for r in rows)
    _stamp(p, cx - (w - 1) // 2, y, rows, colors)


def op_brows(doc, p, op):
    """{op: brows, cx, y, gap?, shape: flat|raised|angry|worried|soft, color, soft?=true, skin?}  Both brows, mirrored
    around cx; softened into the skin by default."""
    shape = str(op.get("shape", "flat")).lower()
    if shape not in BROWS:
        raise ValueError(f"brows: shape must be one of {sorted(BROWS)}")
    rows = BROWS[shape]
    w = max(len(r) for r in rows)
    gap = int(op.get("gap", 3))
    cx, y = int(round(float(op["cx"]))), int(round(float(op["y"])))
    lx, rx = _pair_x(cx, w, gap)
    # brows are faint in this style (usually hidden by the fringe): the colour is softened into the skin unless
    # "soft": false; a hard dark brow is what makes a small face look stern
    c = doc.color(op.get("color", "#3a2420"))
    if op.get("soft", True):
        c = _mix(c, _skin(doc, op, cx, y + 1), 0.45)
    colors = {"B": c}
    _stamp(p, lx, y, rows, colors)
    _stamp(p, rx, y, rows, colors, mirror=True)


def op_blush(doc, p, op):
    """{op: blush, cx, y, gap, width?, color?, skin?}  Two short soft blush marks, mirrored around cx."""
    w = int(op.get("width", 2))
    gap = int(op.get("gap", 6))
    cx, y = int(round(float(op["cx"]))), int(round(float(op["y"])))
    lx, rx = _pair_x(cx, w, gap)
    c = doc.color(op["color"]) if op.get("color") else _mix(_skin(doc, op, lx, y), (238, 120, 130), 0.3)
    for i in range(w):
        p.plot(lx + i, y, c)
        p.plot(rx + i, y, c)


def op_skin(doc, p, op):
    """{op: skin, x, y, w, h, skin?, tolerance?=0.16}  Flatten the face: pixels in the box whose colour is close to the
    skin colour (blotches, specks, uneven shading left by the trace) become exactly the skin colour. Hair, outline,
    eyes and clothing are far from the skin colour and stay."""
    x0, y0, w, h = (int(round(float(op[k]))) for k in ("x", "y", "w", "h"))
    tol = float(op.get("tolerance", 0.16))
    region = doc.composite(region=(x0, y0, w, h))
    if op.get("skin"):
        skin = doc.color(op["skin"])
    else:  # the most common opaque light colour in the box
        counts = {}
        for i in range(0, len(region), 4):
            c = tuple(region[i:i + 4])
            if c[3] >= 200:
                counts[c[:3]] = counts.get(c[:3], 0) + 1
        if not counts:
            return
        skin = max(counts, key=lambda c: (counts[c], sum(c))) + (255,)
    for j in range(h):
        for i in range(w):
            k = (j * w + i) * 4
            c = tuple(region[k:k + 4])
            if c[3] < 200 or c[:3] == skin[:3]:
                continue
            d = sum(abs(a - b) for a, b in zip(c[:3], skin[:3])) / 765.0
            if d <= tol:
                p.plot(x0 + i, y0 + j, skin)


FACE_OPS = {"eyes": op_eyes, "mouth": op_mouth, "brows": op_brows, "blush": op_blush, "skin": op_skin}
