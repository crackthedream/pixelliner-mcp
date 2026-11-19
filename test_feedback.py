"""Regression checks for the feedback a drawing model sees. No model or Pillow required."""
import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import server
from png import decode_png


def call(tool, **args):
    response = server.handle({"id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
    if response.get("isError"):
        raise AssertionError(response["content"][0]["text"])
    return response["content"]


def preview_pixel(blocks, x, y):
    image = next(block for block in blocks if block["type"] == "image")
    width, _, pixels = decode_png(base64.b64decode(image["data"]))
    offset = (width * y + x) * 4
    return tuple(pixels[offset:offset+4])


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        server.State.docs.clear()
        self.addCleanup(server.State.docs.clear)
        call("new_canvas", name="badge", width=16, height=12, frames=2)
        self.base = {"op": "rect", "x": 3, "y": 3, "w": 10, "h": 6, "fill": True, "color": "#224466"}
        self.mark = {"op": "rect", "x": 10, "y": 5, "w": 2, "h": 2, "fill": True, "color": "#ffcc44"}
        call("draw", ops=[self.base, self.mark])
        self.reference = Path(self.tmp.name)/"ref.png"
        call("export", path=str(self.reference))
        self.matching_score = json.loads(call("lint")[0]["text"])["score"]
        call("draw", ops=[self.base, dict(self.mark, x=4)])
        call("import_reference", path=str(self.reference), into="layer", opacity=1,
             palette=["#224466", "#ffcc44"], all_frames=True)

    def test_mirrored_accent_needs_a_brief_check(self):
        # Both badges are equally tidy. The brief requires the gold mark on the viewer's right.
        self.assertEqual(self.matching_score, json.loads(call("lint")[0]["text"])["score"])
        region = [9, 4, 4, 4]
        mismatch = call("compare_reference", region=region, cell=2, scale=1)[0]["text"]
        self.assertIn("colour off", mismatch)
        self.assertIn("[", mismatch.split("worst ")[1])
        self.assertIn("region 9,4 4x4", mismatch)
        call("draw", ops=[self.base, self.mark])
        self.assertIn("matches the reference", call("compare_reference", region=region)[0]["text"])

    def test_reference_overlay_and_artwork_only(self):
        options = dict(scale=1, grid=False, mode="both")
        self.assertEqual(preview_pixel(call("view", **options), 10, 5), (255, 204, 68, 255))
        for extra in ({}, {"onion": True}, {"frames": "all"}):
            blocks = call("view", include_reference=False, **options, **extra)
            self.assertEqual(preview_pixel(blocks, 10, 5), (34, 68, 102, 255))
        full = call("view", include_reference=True, **options)[-1]["text"]
        art = call("view", include_reference=False, **options)[-1]["text"]
        self.assertNotEqual(art, full)
        self.assertEqual(art, call("view", **options)[-1]["text"])

    def test_per_layer_export_omits_the_reference(self):
        call("export", layers="both", path=str(Path(self.tmp.name)/"final.png"))
        self.assertEqual(sorted(p.name for p in Path(self.tmp.name).glob("final*")),
                         ["final.png", "final_00_Layer 1.png"])

    def test_full_colour_reference_returns_an_image_and_a_working_grid_retry(self):
        call("new_canvas", width=12, height=12)
        pixels = [[x, y, "#%02x6633" % (12*y+x)] for y in range(12) for x in range(12)]
        call("draw", ops=[{"op": "pixels", "pixels": pixels}])
        for mode in ("grid", "both"):
            response = call("view", mode=mode, scale=1)
            self.assertEqual(sum(b["type"] == "image" for b in response), 1)
            self.assertIn("Exact grid omitted", response[-1]["text"])
        exact = call("view", mode="grid", x=3, y=3, width=6, height=6)
        self.assertIn("region x=3 y=3 w=6 h=6", exact[-1]["text"])

    def test_gif_flattens_once_per_selected_frame(self):
        doc = server.doc_or_fail()
        with patch.object(doc, "composite", wraps=doc.composite) as composite:
            call("export", format="gif", path=str(Path(self.tmp.name)/"badge.gif"))
            self.assertEqual(composite.call_count, 2)

    def test_operations_are_discoverable_with_a_short_tool_description(self):
        tool = next(t for t in server.handle({"method": "tools/list"})["tools"] if t["name"] == "draw")
        self.assertLessEqual(len(tool["description"]), 1024)
        catalog = tool["inputSchema"]["properties"]["ops"]["description"]
        for operation in ("eyes {cx", "shade {color", "transform {x", "mouth {"):
            self.assertIn(operation, catalog)


if __name__ == "__main__":
    unittest.main()
