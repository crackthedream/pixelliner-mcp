# Drawing to a brief

At 1×, can you tell which way the character faces, what they carry and what expression they have? These are decisions to check throughout a drawing session. The engine can measure pixel differences and point the artist at a region. A person still has to judge whether the drawing reads as intended.

## A small reproducible example

Give the artist a 16 × 12 blue badge with a two-by-two gold mark. Specify that the mark belongs on the viewer's right. In the offline fixture, the correct badge and the badge with the mark on the left both receive **97.7** from `lint`. Both have clean edges and an identical silhouette. Scoring technical defects alone would accept either drawing.

With the intended badge as the aligned reference, run `compare_reference` with `{"region":[9,4,4,4],"cell":2,"scale":1}`. On the wrong badge it reports zero missing and zero extra silhouette pixels, but a mean blurred OKLab distance of **0.183** in that region. The returned cells are too dark. Restoring the gold pixels on the right makes the regional drawing match. A whole-canvas review is still needed to catch an unwanted mark elsewhere. Reproduce this case with `python -m unittest test_feedback.FeedbackTests.test_mirrored_accent_needs_a_brief_check`.

## An artist's review loop

1. Translate the request into two or three visible checks. For a courier these might be a left-facing pose, a lantern in the visible hand and a scarf that remains readable at 1×. Clarify whether “left” means the character's left or the viewer's left.
2. Inspect the silhouette and prop before shading. Use `view` with `{"scale":1,"grid":false,"include_reference":false}` to see just the drawing. A tracing overlay is useful while editing; turn it off when judging the result.
3. If a reference is aligned to the canvas, compare the face or prop with `compare_reference`. Returned coordinates refer to the whole canvas, so they can be used directly in a local drawing operation. Differences from a photo or a differently posed reference need artistic interpretation. With text alone, evaluate the brief on the rendered drawing.
4. Apply one local correction and inspect the whole sprite at 1× again. For an expression edit, save before and after and check for unintended changes outside the face. Technical lint can then flag stray pixels and palette errors before export.

## How to test a model upgrade or workflow change

Use a fixed panel of original briefs covering orientation, required props, expression and local revisions. Include text-only tasks and tasks with redistributable aligned references. Run both versions with the same model, per-case budget, canvas size and reference when testing a workflow. When testing a model change, hold the workflow fixed.

Record every attempt, including unfinished ones. Compare required-detail pass rates, exported dimensions/palette, pixel changes outside the edit region, elapsed time, tool errors and cost per accepted drawing. Randomize left/right presentation to reviewers and allow ties. Ask reviewers to name the missing or changed detail before revealing which version produced each image. Judge silhouettes and expressions at game size first, then magnify for craftsmanship.

The badge fixture tests feedback correctness. A blinded comparison on representative characters is needed to measure any improvement in the art people prefer.
