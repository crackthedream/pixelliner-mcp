# PixelLiner MCP

A local, headless pixel-art engine for MCP-compatible AI clients. Draw and inspect exact pixels, use layers and animation frames, compare against a reference, and export ordinary image files or a layered project.

**Source release: v1.1.0.** The engine does not contain an AI model, require an OpenAI account, or make model API calls. Your MCP host supplies the model, tools policy, and conversation. Model-provider fees, if any, belong to that host. The separate PixelLiner desktop editor is optional and is not distributed here.

## Start the server

The core engine uses the Python standard library. It targets Python 3.8 or newer; release checks are run with Python 3.13. Download or clone the source and keep the Python files together.

From the source directory:

```sh
python -I server.py
```

This starts a **stdio MCP server**, not a browser page or interactive drawing window. It waits for newline-delimited JSON-RPC messages on standard input and replies on standard output. Your MCP client normally starts and stops the process for you. Diagnostics go to standard error.

A common client configuration looks like this:

```json
{
  "mcpServers": {
    "pixelliner": {
      "command": "python",
      "args": ["-I", "/absolute/path/to/pixelliner-mcp/server.py"],
      "env": {
        "PIXELLINER_MCP_OUT": "/absolute/path/to/your/output"
      }
    }
  }
}
```

Replace both example paths with real absolute paths. On Windows, forward slashes work in JSON paths; alternatively, escape each backslash. If a desktop host cannot find `python`, set `command` to the absolute path of its Python executable. Some hosts use a different configuration schema or a settings form; preserve the command, arguments, environment, and **stdio** transport in that format.

The server negotiates MCP protocol versions `2025-06-18`, `2025-03-26`, and `2024-11-05`. A useful host must support tool calls and display or forward MCP image content to its model. Registration alone does not establish that a host uses image results, initialization instructions, or prompts correctly.

### Optional configuration

| Variable | Purpose |
| --- | --- |
| `PIXELLINER_MCP_OUT` | Base directory for relative export and project-save paths. Defaults to `~/Documents/PixelLinerMCP`. |
| `PIXELLINER_EXE` | Location of an independently installed PixelLiner editor, used only when explicitly opening an exported file. |

PNG decoding and PNG/GIF encoding are included. Install Pillow into the **same Python environment used by the MCP host** if you want JPG, WEBP, or other supported input formats:

```sh
python -m pip install pillow
```

Pillow is optional for the core server and required for the synthetic image fixture in `test_art.py`.

## The 17 tools

| Tool | Purpose |
| --- | --- |
| `new_canvas` | Create a named canvas, palette, and optional animation frames. |
| `open_image` | Open an existing PNG as an editable canvas. |
| `canvas` | Inspect, select, resize, crop, or manage canvases. |
| `palette` | Set colours, use presets, generate shading ramps, or extract a palette. |
| `import_reference` | Resize and optionally quantize an image; use it as a new canvas or a reference layer. |
| `draw` | Apply a batch of pixel maps, points, shapes, fills, shading, outline, cleanup, transforms, or face-kit operations. A failed operation rolls back the whole batch. |
| `layer` | Add, select, rename, duplicate, reorder, hide, or merge layers; adjust opacity and reference flags. |
| `frame` | Add, select, reorder, delete, or time animation frames. |
| `view` | Return a PNG preview, an exact coordinate grid, or both; inspect a region, multiple frames, or onion skin. |
| `undo` | Undo canvas changes. |
| `redo` | Redo undone changes. |
| `export` | Write PNG, animated GIF, a sprite sheet, per-frame PNGs, or per-layer PNGs. |
| `save_project` | Save layers, frames, palette, and reference flags in a project folder. |
| `load_project` | Restore a saved project folder or its manifest. |
| `lint` | Report palette, transparency, stray-pixel, cluster, edge, and animation-consistency diagnostics. |
| `compare_reference` | Compare artwork with an aligned reference, returning colour/silhouette diagnostics and a visual mismatch map. |
| `open_in_pixelliner` | Launch the separately installed editor with an exported image, when requested. |

Tool schemas returned by `tools/list` are the parameter reference. The detailed drawing operator catalogue is also carried in `draw.inputSchema.properties.ops.description`, including face and transform parameters. Custom clients should retain nested schema descriptions when forwarding tools to a model; truncating the catalogue makes supported operations harder to use correctly.

### A first drawing

Ask your MCP host to create a 32×32 canvas, draw a small two-colour icon, inspect it, and export it. For clients that expose tool calls directly, the corresponding arguments can be:

```text
new_canvas {"name":"badge","width":32,"height":32,"palette":{"b":"#224466","g":"#ffcc44"}}
draw {"ops":[{"op":"rect","x":6,"y":8,"w":20,"h":16,"color":"b","fill":true},{"op":"rect","x":20,"y":12,"w":4,"h":4,"color":"g","fill":true}]}
view {"mode":"image","include_reference":false,"onion":false,"scale":4,"grid":false}
export {"path":"badge.png"}
save_project {"path":"badge-project"}
```

These are tool names and JSON arguments, not shell commands. The exported image is written under the configured output directory; the project folder preserves editable structure.

## Check the artwork the user will receive

Reference layers help a model draw, but their pixels must not be mistaken for its finished artwork.

- **Image previews keep the existing default:** reference layers are included. This is useful for tracing and alignment.
- For an artwork-only composite, call `view` with **`include_reference:false`**, omit `layer`, and set **`onion:false`**. Use `grid:false` when checking the visual result without grid decoration.
- Text grids default to artwork only. Explicit `include_reference:true` includes the overlay in a composite grid.
- Selecting `layer` explicitly shows that layer, including a reference layer. `include_reference:false` is not a way to hide an explicitly selected layer.
- Normal exports exclude reference layers. Saved projects retain them so work can continue later.

For example, inspect the result at native size, then zoom into a doubtful area:

```text
view {"mode":"image","include_reference":false,"onion":false,"scale":1,"grid":false}
view {"mode":"both","include_reference":false,"onion":false,"x":8,"y":4,"width":16,"height":16,"scale":8}
```

`compare_reference` already compares the visible non-reference artwork to a reference layer. In v1.1.0 you can focus it on a face, prop, emblem, or other required feature:

```text
compare_reference {"region":[8,4,16,16],"cell":4,"top":5,"scale":8}
```

`region` is **`[x, y, width, height]`** in canvas pixels, not two corners. Diagnostic locations remain in absolute canvas coordinates. Reference and artwork must be aligned for these comparisons to be meaningful. If `layer` is specified for this tool, it selects the **reference** layer, not the artwork layer.

## A practical quality workflow

See [Drawing to a brief](QUALITY.md) for a reproducible example of a technically tidy drawing that still misses a requested detail, and a method for evaluating improvements.

The server supplies initialization guidance and an MCP prompt named **`pixel_art_workflow`**, with an optional `subject` argument. A host can list and request it through `prompts/list` and `prompts/get`. Hosts decide whether to use these instructions; the server does not enforce an autonomous drawing plan.

1. Translate the request into visible requirements: subject, pose, facing direction, silhouette, palette, and defining details. Decide the target pixel size before drawing.
2. Build a limited palette and place an aligned reference when available. Prefer a deliberate pixel-size guide over copying every colour from a high-detail illustration.
3. Draw in passes: silhouette, flat materials, clustered shading, focal features, and outlines. Batch related operations. Inspect after a meaningful pass rather than after every pixel.
4. Inspect the **artwork alone** at 1× and at a useful zoom. Use a small grid view for exact corrections. Check important details against the brief, not just against the downscaled reference.
5. Run `lint` for technical problems and `compare_reference` for misplaced or missing features. Correct a small region, then inspect it again. Export and save the project when the result is acceptable.

The technique operations are starting points for refinement. `shade` estimates volume from shapes; it does not understand anatomy or material intent. Face-kit parts are procedural drawing primitives, not a guarantee of likeness or expression quality. A high lint score does not prove that the correct character, pose, or emblem was drawn. A low reference distance does not prove clean shading or a readable face. Human review and the host model's visual judgement still matter.

### Exact grids and palette keys

A grid includes a colour legend for that particular view. Keys assigned to colours outside the canvas palette are not automatically installed in the canvas palette. When reusing a grid in `pixel_map`, supply a matching per-operation `palette` or map it to your established canvas keys.

A grid that contains too many distinct colours returns an image and a concrete smaller-region retry instead of an unusable character grid. A 6×6 region is a useful starting point. Do not repeatedly request the same oversized or highly varied region. In `pixel_map`, `.` and a space skip existing pixels; use an explicit transparent colour or `clear` when you intend to erase.

## Animation and saved projects

Every layer contains one image per frame. Layer names, visibility, and opacity are shared across frames. Start with key poses, use `frame` to copy or add frames, and change only what moves. `transform` can move, rotate, or scale a region, but thin details may need manual repair. Use frame timing to control motion and inspect the strip with `view {"frames":"all"}`. Onion skin is a preview aid, not part of the final export.

`save_project` writes `project.json` plus layer/frame PNGs. `load_project` restores this engine's project format. It is **not** the proprietary PixelLiner `.pxl` format. PNGs and GIFs are portable; a saved project is for continuing work in this engine or a compatible application. GIF transparency is binary and its palette is limited; use PNG for full alpha fidelity.

Canvas state lives in the server process. Export results and save a project before ending a session. Saving again to the same project directory replaces the manifest and cleans up obsolete layer PNGs owned by the previous saved project, preserving unrelated files.

## Local trust boundary

This server is intended for a trusted local MCP host. It has no user authentication, filesystem sandbox, remote HTTP service, or model-spending controls. Tools can read input images and project manifests, write or replace output files, and optionally launch the configured editor. Absolute paths can refer outside the output directory; `PIXELLINER_MCP_OUT` is a convenience, not an access boundary.

Run it with an account and filesystem permissions appropriate to the task. Treat third-party project manifests as untrusted input. A host or deployment wrapper must supply any approval policy, path restrictions, resource limits, and isolation required for untrusted users. Reference flags control compositing and export, not file confidentiality: the host can explicitly inspect a reference, and a saved project retains it. A host may send tool images to its model provider according to that host's settings.

## Tests and release scope

From the source directory:

```sh
python -B test_smoke.py
python -B -m unittest test_feedback test_project
python -B test_art.py
```

`test_smoke.py` exercises the stdio protocol and image/export workflow. `test_feedback.py` checks artwork-only views, local reference comparison, grid fallback, tool documentation, and export behavior. `test_project.py` checks project persistence, interrupted saves, and manifest validation. `test_art.py` checks technique operations using a synthetic reference and writes previews for visual inspection; it needs Pillow. Tests use no model API and incur no model charges. Output paths are printed or use temporary directories.

Passing these tests verifies the exercised engine behavior. It does not establish artistic quality for arbitrary prompts, compatibility with every MCP host, or acceptance of an entire game asset workflow.

The source release contains the engine, MCP server, tests, and documentation. It does not contain a model, the PixelAgent desktop application, the PixelLiner editor, game sprite atlases, or user-generated projects. The independent PixelAgent application has its own workflow and release process.

## Source map

| File | Responsibility |
| --- | --- |
| `server.py` | MCP protocol, tool schemas, previews, comparison, workflow prompt, and optional editor launch. |
| `canvas.py` | Canvas, layers, frames, drawing primitives, compositing, and undo/redo. |
| `art.py` | Colour ramps, resampling, shading, transforms, cleanup, lint, and fidelity measures. |
| `face_kit.py` | Procedural eyes, mouths, brows, skin, and blush. |
| `png.py`, `gif.py` | Image codecs. |
| `project.py` | Layered project persistence. |

## License and provenance

Project-owned source and documentation are available under the [MIT License](LICENSE). The grant does not license third-party artwork, trademarks, or the separate proprietary PixelLiner editor.

The named palette presets acknowledge their origins: **PICO-8** / Lexaloffle, **DawnBringer DB16** / DawnBringer, and **Game Boy-style** monochrome greens. These names describe palette references; they do not imply affiliation or endorsement.

The face kit is an original procedural implementation informed by observations of pixel-art face construction, including locally studied *Alice in Cradle* sprites. No game sprite atlases or extracted game graphics are distributed in this project. The project is not affiliated with or endorsed by those game, palette, or editor creators.
