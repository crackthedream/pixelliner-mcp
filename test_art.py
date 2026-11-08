"""Visual + functional test of the technique tools. Writes previews to an output dir for inspection."""
import base64
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="pxlart_")
os.makedirs(OUT, exist_ok=True)
os.environ["PIXELLINER_MCP_OUT"] = OUT
import server  # noqa: E402
import art  # noqa: E402


def call(_tool, **a):
    r = server.HANDLERS[_tool](a)
    for c in r:
        if c["type"] == "text":
            print(f"[{_tool}] {c['text'][:260]}")
    return r


def save_img(r, fname):
    imgs = [c for c in r if c["type"] == "image"]
    with open(os.path.join(OUT, fname), "wb") as f:
        f.write(base64.b64decode(imgs[0]["data"]))


# 1. ramps
call("new_canvas", width=40, height=8, name="ramps")
call("palette", ramp=[{"base": "#e8a07a", "steps": 5, "keys": "abcde"}, {"base": "#3b5dc9", "steps": 5, "keys": "fghij"},
                      {"base": "#808080", "steps": 5, "keys": "klmno"}, {"base": "#6ab04c", "steps": 5, "keys": "pqrst"}])
call("draw", ops=[{"op": "pixel_map", "x": 0, "y": 0, "rows": [("aabbccddee" + "ffgghhiijj" + "kkllmmnnoo" + "ppqqrrsstt")] * 8}])
save_img(call("view", scale=12, grid=False), "1_ramps.png")

# 2. shading pipeline on a blob, one stage per frame
call("new_canvas", width=40, height=40, name="pipeline", palette={"o": "#222034"})
call("palette", ramp={"base": "#d95763", "steps": 5, "keys": "12345"})
call("draw", ops=[{"op": "ellipse", "x": 6, "y": 4, "w": 28, "h": 24, "color": "3", "fill": True},
                  {"op": "ellipse", "x": 12, "y": 22, "w": 18, "h": 14, "color": "3", "fill": True}])
call("frame", action="add")
call("draw", ops=[{"op": "shade", "color": "3", "ramp": ["1", "2", "3", "4", "5"], "light": "top-left"}])
call("frame", action="add")
call("draw", ops=[{"op": "outline", "color": "o"}])
call("frame", action="add")
call("draw", ops=[{"op": "selout"}])
call("frame", action="add")
call("draw", ops=[{"op": "antialias"}, {"op": "cleanup", "doubles": False}])
save_img(call("view", frames="all", scale=6), "2_pipeline.png")

# 3. reference import from a synthetic "painting"
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402
ref = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
d = ImageDraw.Draw(ref)
d.ellipse((96, 40, 416, 360), fill=(240, 200, 160, 255))        # head
d.ellipse((80, 20, 432, 230), fill=(120, 60, 40, 255))          # hair
d.ellipse((96, 120, 416, 360), fill=(240, 200, 160, 255))
d.ellipse((170, 200, 220, 260), fill=(40, 60, 140, 255))        # eyes
d.ellipse((292, 200, 342, 260), fill=(40, 60, 140, 255))
d.rectangle((150, 360, 362, 512), fill=(60, 120, 90, 255))      # body
ref = ref.filter(ImageFilter.GaussianBlur(6))
ref_path = os.path.join(OUT, "reference_src.png")
ref.save(ref_path)
call("import_reference", path=ref_path, width=32, colors=8, name="portrait")
save_img(call("view", scale=12, grid=False), "3_import.png")
call("import_reference", path=ref_path, width=32, colors=8, name="portrait_dither", dither="ordered")

# into a layer as a reference
call("new_canvas", width=40, height=40, name="trace")
call("import_reference", path=ref_path, width=32, into="layer", x=4, y=4, palette="none")
assert server.State.docs["trace"].layers[-1].reference
flat = server.State.docs["trace"].composite()
assert not any(flat[3::4]), "reference layer must not be exported"

# 4. RotSprite rotation vs naive
call("new_canvas", width=48, height=24, name="rot", palette={"o": "#222034", "y": "#fbf236", "w": "#ffffff"})
sword = ["....o.......", "...oyo......", "...oyo......", "...oyo......", "...oyo......", "...oyo......",
         "...oyo......", "..ooyoo.....", ".oyyyyyo....", "..ooyoo.....", "...ooo......", "...owo......", "...ooo......"]
call("draw", ops=[{"op": "pixel_map", "x": 2, "y": 4, "rows": sword},
                  {"op": "pixel_map", "x": 18, "y": 4, "rows": sword},
                  {"op": "pixel_map", "x": 34, "y": 4, "rows": sword}])
call("draw", ops=[{"op": "transform", "x": 18, "y": 4, "w": 12, "h": 13, "rotate": 30, "smooth": False},
                  {"op": "transform", "x": 34, "y": 4, "w": 12, "h": 13, "rotate": 30}])
save_img(call("view", scale=10, grid=False), "4_rotate.png")

# exact 90 degree rotation round trip
w, h, px = 3, 2, [(i, 0, 0, 255) for i in range(6)]
w2, h2, p2 = art.rotate_sprite(w, h, px, 90)
assert (w2, h2) == (2, 3) and p2[0] == (3, 0, 0, 255) and p2[1] == (0, 0, 0, 255), p2
_, _, p4 = art.rotate_sprite(*art.rotate_sprite(*art.rotate_sprite(w2, h2, p2, 90), 90), 90)
assert p4 == px

# 5. cleanup removes L-corner doubles in a hand-drawn line
call("new_canvas", width=12, height=8, name="lines", palette={"k": "#000000"})
call("draw", ops=[{"op": "pixel_map", "x": 1, "y": 1, "rows": ["kk......", ".kk.....", "..kk....", "...kkk..", ".....k.."]}])
before = call("view", mode="grid")[0]["text"]
call("draw", ops=[{"op": "cleanup", "orphans": False}])
after = call("view", mode="grid")[0]["text"]
print(before.split("\n", 3)[3])
print(after.split("\n", 3)[3])

# 6. prompt
res = server.handle({"jsonrpc": "2.0", "id": 1, "method": "prompts/get", "params": {"name": "pixel_art_workflow", "arguments": {"subject": "a knight"}}})
assert "Task: draw a knight." in res["messages"][0]["content"]["text"]
print("ALL OK ->", OUT)
