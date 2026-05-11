---
name: pet-fringe-cleanup
description: Clean chroma-key fringe, halos, matte spill, and key-colored outline artifacts from Codex hatch-pet spritesheets and run folders. Use after the hatch-pet skill finalizes a pet when transparent frames still show magenta/green/blue or other chroma-key marks around the silhouette, when a contact sheet or zoomed inspection reveals colored edge pixels, or before installing/reinstalling a cleaned pet package.
---

# Pet Fringe Cleanup

## Overview

Use this skill after `$hatch-pet` has produced a run directory with `pet_request.json`, `frames/`, `final/spritesheet.webp`, and QA artifacts. It repairs residual chroma-key contamination without regenerating pet art.

The paired script is deterministic and key-aware. It does not assume magenta backgrounds or brown pets: it reads the key color from `pet_request.json` when available, or accepts `--key-color`.

## Workflow

1. Confirm the hatch-pet run is finalized:

```bash
test -f /path/to/run/pet_request.json
test -f /path/to/run/final/spritesheet.webp
```

2. Run cleanup:

```bash
python /path/to/pet-fringe-cleanup/scripts/clean_pet_fringes.py \
  --run-dir /path/to/run \
  --install
```

Use `--key-color '#RRGGBB'` only when the run has no reliable `pet_request.json` key. Use `--source-atlas` when cleaning an atlas outside a normal hatch-pet run.

3. Rebuild QA and validation:

```bash
python ${CODEX_HOME:-$HOME/.codex}/skills/hatch-pet/scripts/validate_atlas.py \
  /path/to/run/final/spritesheet.webp \
  --json-out /path/to/run/final/validation.json

python ${CODEX_HOME:-$HOME/.codex}/skills/hatch-pet/scripts/make_contact_sheet.py \
  /path/to/run/final/spritesheet.webp \
  --output /path/to/run/qa/contact-sheet.png

python ${CODEX_HOME:-$HOME/.codex}/skills/hatch-pet/scripts/render_animation_videos.py \
  /path/to/run/final/spritesheet.webp \
  --output-dir /path/to/run/qa/videos
```

4. Visually inspect against a dark or gray background and at high zoom. Do not accept purely because validation passed.

## Algorithm

The script works cell-by-cell on used 192x208 atlas cells. It keeps the hatch-pet atlas geometry intact.

The repair is edge-first. After the hatch-pet chroma-key pass, the spritesheet already has alpha transparency, so the script derives a silhouette edge band from each cell's alpha channel. Visible pixels outside that alpha edge band are treated as protected sprite interior and must not be recolored, deleted, or classified as chroma-key contamination. This protects legitimate key-hue-adjacent interior colors such as pink mouths, purple tongues, green markings, blue eyes, or other character features.

It applies three targeted operations:

- Clear RGB residue from fully transparent pixels anywhere in the cell.
- Remove very low-alpha key-colored noise only when it is in the alpha edge band.
- Recolor key-hue edge and halo pixels from nearby clean sprite pixels only when they are in the alpha edge band.
- Recolor fully opaque key-hue pixels only when they are in the alpha edge band and appear to be embedded in the silhouette outline or halo.

It preserves pixels that do not look like the chroma key, and also preserves visible non-edge pixels even if they do look like the chroma key. This is important for pets whose real palette is close to the key color. For example, a magenta chroma key can be close to legitimate pink or purple mouth colors; those interior pixels must survive cleanup.

## General-Case Rules

- Do not assume the key is magenta. Hatch-pet may choose green, blue, magenta, or another removable key.
- Do not assume the pet is brown. The repair must be based on distance and hue relative to the key, not on expected pet colors.
- Always gate visible-pixel cleanup by the alpha-derived silhouette edge band before applying key-hue classification. Do not classify or recolor the whole visible sprite.
- Treat visible non-edge pixels as protected interior art even when their hue is close to the chroma key. Mouths, tongues, eyes, markings, accessories, highlights, and state effects can legitimately use key-adjacent hues.
- Prefer recoloring contaminated pixels from nearby clean sprite pixels over deleting them. Deletion can thin outlines and change silhouettes.
- Remove only low-alpha key noise outright, and only inside the edge band.
- Use the original pre-clean atlas as source when iterating. The script automatically creates `final/spritesheet-before-fringe-clean.png` and `.webp` backups on first run, and uses the PNG backup for repeatable re-cleaning.
- Reinstall only after visual QA when the pet is user-facing. Use `--install` to copy the cleaned WebP into `${CODEX_HOME:-$HOME/.codex}/pets/<slug>/spritesheet.webp`.

## Outputs

Expected outputs:

```text
run/
  final/spritesheet-before-fringe-clean.png
  final/spritesheet-before-fringe-clean.webp
  final/spritesheet.png
  final/spritesheet.webp
  qa/fringe-cleanup.json
```

When `--install` is set:

```text
${CODEX_HOME:-$HOME/.codex}/pets/<pet-slug>/spritesheet.webp
```

## Failure Modes

- If interior sprite colors still change after cleanup, the edge band is too broad or the source alpha is incorrect. Stop, inspect the source, and narrow the edge operation rather than loosening global key detection.
- If the pet genuinely uses the chroma-key hue on the silhouette boundary, automatic cleanup may still damage those boundary details. Stop, inspect the source, and either regenerate with a safer key or run with a narrower `--hue-threshold`.
- If the artifact is a large copied background region rather than a fringe, use hatch-pet repair/regeneration instead of this skill.
- If validation fails after cleanup, restore from `spritesheet-before-fringe-clean.*`, adjust thresholds, and rerun.
