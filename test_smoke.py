"""End-to-end smoke test: talks to server.py over stdio exactly like an MCP client."""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from png import decode_png  # noqa: E402

OUT = tempfile.mkdtemp(prefix="pxlmcp_")
env = dict(os.environ, PIXELLINER_MCP_OUT=OUT)
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")], stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, env=env)
_id = 0


def rpc(method, params=None, notify=False):
    global _id
    msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if not notify:
        _id += 1
        msg["id"] = _id
    proc.stdin.write((json.dumps(msg) + "\n").encode())
    proc.stdin.flush()
    if notify:
        return None
    resp = json.loads(proc.stdout.readline())
    assert resp["id"] == _id, resp
    return resp


def call(_tool, **args):
    t = time.perf_counter()
    r = rpc("tools/call", {"name": _tool, "arguments": args})["result"]
    dt = (time.perf_counter() - t) * 1000
    texts = [c["text"] for c in r["content"] if c["type"] == "text"]
    print(f"[{_tool} {dt:.0f}ms] " + " | ".join(texts)[:300])
    assert not r.get("isError"), r
    return r


init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
print(init["result"]["serverInfo"], init["result"]["protocolVersion"])
rpc("notifications/initialized", notify=True)
tools = rpc("tools/list")["result"]["tools"]
print("tools:", [t["name"] for t in tools])

call("new_canvas", width=32, height=32, name="slime", palette={"o": "#1a1c2c", "g": "#38b764", "l": "#a7f070", "d": "#257179", "w": "#ffffff"})
call("draw", ops=[
    {"op": "ellipse", "x": 6, "y": 10, "w": 20, "h": 16, "color": "g", "fill": True},
    {"op": "rect", "x": 6, "y": 18, "w": 20, "h": 8, "color": "g", "fill": True},
    {"op": "pixel_map", "x": 11, "y": 15, "rows": ["ww....ww", "wo....wo", "oo....oo"]},
    {"op": "line", "x1": 13, "y1": 21, "x2": 18, "y2": 21, "color": "d"},
    {"op": "gradient", "x": 6, "y": 22, "w": 20, "h": 4, "colors": ["g", "d"]},
])
call("layer", action="add", name="outline", index=0)
# outline on its own layer requires the shape there; demonstrate outline op on the main layer instead
call("layer", action="select", layer="Layer 1")
call("draw", ops=[{"op": "outline", "color": "o"}, {"op": "pixels", "points": [[10, 13], [11, 12]], "color": "l"}])
r = call("view", mode="both", x=4, y=8, width=24, height=20)
img = [c for c in r["content"] if c["type"] == "image"][0]
with open(os.path.join(OUT, "preview.png"), "wb") as f:
    f.write(base64.b64decode(img["data"]))
call("draw", ops=[{"op": "clear"}])
call("undo")
# failing op must roll back atomically
bad = rpc("tools/call", {"name": "draw", "arguments": {"ops": [{"op": "rect", "x": 0, "y": 0, "w": 3, "h": 3, "color": "w", "fill": True}, {"op": "nope"}]}})["result"]
print("bad batch ->", bad["content"][0]["text"][:120], bad["isError"])
r = call("export", scale=4, layers="both")
path = r["content"][0]["text"].split("\n")[1]
w, h, data = decode_png(open(path, "rb").read())
assert (w, h) == (128, 128)
assert tuple(data[0:4]) == (0, 0, 0, 0), "top-left must be transparent (bad batch rolled back)"
call("open_image", path=path, name="reloaded")
call("canvas", action="resize", width=160, height=140, anchor="center")
call("canvas", action="list")

# speed: a 128x128 canvas with a big batch
call("new_canvas", width=128, height=128, name="perf", palette="db16")
ops = [{"op": "circle", "cx": (i * 13) % 128, "cy": (i * 29) % 128, "r": 6, "color": "0123456789abcdef"[i % 16], "fill": True} for i in range(200)]
call("draw", ops=ops)
call("view")

# ---- animation: 4-frame bouncing ball over a static background
call("new_canvas", width=16, height=16, name="bounce", frames=4, duration=120, background="#29adff", palette="pico8")
call("draw", layer="Background", frames="all", ops=[{"op": "rect", "x": 0, "y": 13, "w": 16, "h": 3, "color": "3", "fill": True}])
for f, y in enumerate([2, 5, 8, 9]):
    call("draw", frame=f, ops=[{"op": "circle", "cx": 8, "cy": y + 2, "r": 2, "color": "8", "fill": True}])
call("frame", action="add", frame=2)          # copy of frame 2 inserted at 3 -> ping-pong
call("frame", action="duration", frame=3, duration=60)
call("layer", action="add", name="fx")          # new layer must exist in all 5 frames
call("draw", layer="fx", frame=4, ops=[{"op": "pixels", "points": [[5, 12], [11, 12]], "color": "7"}])
call("layer", action="duplicate", layer="fx")
call("layer", action="merge_down", layer="fx copy")
call("frame", action="delete", frame=0)
call("undo")                                    # frame 0 back
r = call("view", frames="all")
with open(os.path.join(OUT, "frames_preview.png"), "wb") as f:
    f.write(base64.b64decode([c for c in r["content"] if c["type"] == "image"][0]["data"]))
call("view", frame=1, onion=True)
call("view", mode="grid", frame=4, y=8, height=8)
gif_path = call("export", format="gif", scale=2)["content"][0]["text"].split("\n")[1]
sheet_path = call("export", format="sheet", columns=3, gap=1)["content"][0]["text"].split("\n")[1]
call("export", format="frames")
call("export", frame=4)
try:
    from PIL import Image
    im = Image.open(gif_path)
    assert im.n_frames == 5 and im.size == (32, 32), (im.n_frames, im.size)
    durs = []
    for i in range(im.n_frames):
        im.seek(i)
        durs.append(im.info["duration"])
        px = im.convert("RGBA").getpixel((16, 2 * (2 + [2, 5, 8, 8, 9][i])))  # ball centre
        assert px[:3] == (255, 0, 77), (i, px)
    assert durs == [120, 120, 120, 60, 120], durs
    sw, sh, _ = decode_png(open(sheet_path, "rb").read())
    assert (sw, sh) == (3 * 16 + 2, 2 * 16 + 1), (sw, sh)
    print("GIF/sheet verified with Pillow:", durs)
except ImportError:
    print("Pillow not installed; skipped GIF verification")
proc.stdin.close()
proc.wait(timeout=5)
print("OK, outputs in", OUT)
