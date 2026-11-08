# PixelLiner MCP

A headless pixel-art canvas exposed as an MCP server. The model draws in memory (no screen, mouse or
window use) and exports PNGs that PixelLiner opens natively. PixelLiner itself is never modified — the
only interaction is launching `PixelLiner.exe <file.png>` when you ask for it.

Pure Python standard library (3.8+). No `pip install` needed.

## Files
- `server.py` — MCP stdio server and tool definitions
- `canvas.py` — layered canvas and drawing algorithms (Bresenham lines, pixel-perfect ellipses, flood fill, outline, dithered gradients, mirror symmetry)
- `png.py` — PNG encoder/decoder
- `gif.py` — animated GIF encoder (exact colours, LZW)
- `art.py` — technique algorithms: OKLab colour, hue-shifted ramps, k-means palettes, reference resampling, form shading, sel-out, anti-aliasing, pixel-perfect cleanup, RotSprite rotation
- `test_smoke.py` — end-to-end protocol test (`python test_smoke.py`)
- `test_art.py` — technique tests; writes preview images (`python test_art.py <out_dir>`, needs Pillow for the synthetic reference)

## Tools
| Tool | Purpose |
|---|---|
| `new_canvas` | width/height, optional background colour, palette (`pico8`, `db16`, `gameboy`, or `{key: color}`), `frames`, `duration` |
| `open_image` | load an existing PNG to edit |
| `import_reference` | turn any picture (sketch, concept art, render) into pixels: downscale (`area`/`mode`/`nearest`), map to an auto (k-means), preset or canvas palette, optional ordered dither; as a new canvas or a reference layer that shows in previews but is never exported |
| `draw` | **batch** of ops in one call: `pixel_map` (ASCII rows → pixels), `pixels`, `line`, `polyline`, `rect`, `ellipse`, `circle`, `polygon`, `fill`, `replace_color`, `outline` (pixel-perfect), `gradient`, `clear`, `flip`, `shift`, `mirror`, plus technique ops: `shade` (form shading of a flat area from a light direction), `selout`, `antialias`, `cleanup`, `quantize`, `transform` (move/copy/rotate/scale a region, across frames and layers, RotSprite rotation). Optional `mirror: "x"` symmetry; `frame` or `frames: "all"` to target animation frames. Atomic: a failing op rolls back the whole batch |
| `frame` | list / select / add (copies the source frame by default) / delete / move / duration |
| `view` | zoomed PNG preview (so the model can see) and/or exact text grid with coordinates; `frames: "all"` shows frames side by side, `onion` ghosts the previous frame |
| `layer` | add / select / delete / rename / duplicate / move / set visibility & opacity / merge_down |
| `palette` | presets, `set`, `ramp` (hue-shifted shading ramps: shadows drift cool, highlights warm, built in OKLCh), `extract` (k-means palette from an image or the canvas) |
| `canvas`, `undo`, `redo` | housekeeping (resize, crop, switch canvases) |
| `export` | `format`: `png` (one frame; flattened and/or per layer), `gif` (animated, per-frame durations), `sheet` (sprite-sheet PNG), `frames` (PNG per frame); optional integer upscale |
| `open_in_pixelliner` | launch PixelLiner with the exported PNG/GIF (if it is already running, PixelLiner asks: New Character / New Pose / New Frame) |

## Configuration
Registered in `~/.claude.json` (user scope) as `pixelliner`:

```json
"pixelliner": { "type": "stdio", "command": "D:\\python\\Python313\\python.exe",
                "args": ["-I", "C:\\Users\\孙炜\\pixelliner-mcp\\server.py"] }
```

Environment variables (optional):
- `PIXELLINER_MCP_OUT` — default export folder (default `~/Documents/PixelLinerMCP`)
- `PIXELLINER_EXE` — path to PixelLiner (default `C:\Program Files\PixelLiner\PixelLiner.exe`)

The same JSON block works for other MCP clients (Claude Desktop's `claude_desktop_config.json`, Cursor, etc.).

## Quality workflow
The server exposes an MCP prompt, `pixel_art_workflow` (in Claude Code: `/mcp__pixelliner__pixel_art_workflow`), that walks the model through a professional pass-based method:
plan palette ramps → reference → silhouette → flats → `shade` + hand refinement → highlights → `outline` + `selout` →
`antialias` + `cleanup` → animation via key poses and `transform` in-betweens. A condensed version is in the server instructions.

The technique ops give a solid, consistent base; character art at a professional level still benefits from hand refinement
(by the model with `view` grid inspection, or by you in PixelLiner).

## Animation model
Every layer has one image per frame; layer names, visibility and opacity are shared across frames.
Typical flow: `new_canvas {frames: 4}` → draw the static parts once with `frames: "all"` on a background layer →
draw each pose with `frame: n` (or `frame add` to copy a frame and change only what moves) → `view {frames: "all"}` →
`export {format: "gif"}`.

## Limits
- Output is PNG, not native `.pxl` (PixelLiner's project format is proprietary). Per-layer PNGs can be
  imported back as layers in PixelLiner.
- RotSprite rotation keeps outlines connected but very thin details (1px cores) can break at large angles on small sprites; prefer small angles and touch up.
- GIF transparency is 1-bit and limited to 255 colours (exact for normal pixel art).
- Canvas state lives in the server process; export before the session ends.
