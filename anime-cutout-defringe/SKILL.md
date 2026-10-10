---
name: anime-cutout-defringe
description: Cut an anime / cel-shaded character out of a solid white (or any single-colour) background with NO white fringe (白フチ・フリンジ), optionally guided by an already-finished transparent PNG of the same character in the same framing. Use when the user wants to 透過 / 切り抜き / 背景除去 / フリンジ除去 / デフリンジ an AI-generated portrait, expression variant (表情差分), bust-up or standing art, or asks to "make the edges clean". Solves the edge alpha from the known background colour instead of eroding, so lineart edges stay sharp.
---

# anime-cutout-defringe

Turns `character-on-white.png` into a clean RGBA cut-out. The fringe is removed by
*solving* each edge pixel as `p = a·F + (1−a)·B` (B = background colour, F = nearest
surely-opaque colour) — not by shrinking the alpha — so outlines keep their width and
anti-aliasing but carry no background colour.

A finished transparent PNG of the **same character in the same framing** (e.g. the
`neutral` portrait when cutting a new expression) is used as a *reference*:

- decides enclosed background blobs ("holes": gaps between hair strands, arm and body)
  that a border flood cannot reach — the reference is aligned globally *and* locally
  around each hole, so a few px of generation drift does not matter;
- supplies alpha where the foreground is too light to solve against white (silver hair,
  white cloth touching the edge);
- flags where the new silhouette differs from the reference, so a human only inspects
  those spots.

Without a reference everything still works; holes are then kept opaque and listed so
they can be cut by number.

## Usage

`uv` must be on PATH (the script declares its dependencies, PEP 723; first run builds an
ephemeral env — on Windows set `UV_LINK_MODE=copy` to silence the hardlink warning).

```
uv run --no-project "<this-skill-folder>/scripts/defringe.py" <input.png> [output.png] [options]
```

- project install (run from repo root): `.claude/skills/anime-cutout-defringe/scripts/defringe.py`
- user-global install: `~/.claude/skills/anime-cutout-defringe/scripts/defringe.py`
- Default output: `<input_stem>_cut.png` beside the input. The input is never overwritten.

### Recommended flow (when run through Claude)

1. Run with `--preview`. Read the printed hole list and open `<out>_preview.png`
   (left: result on green; right: edge band = blue, cut holes = yellow, uncertain
   holes = orange, differs-from-reference = red; bottom: 4× zooms of edge points).
2. Look at every `keep?` hole and every red region. If a `keep?` hole is background,
   rerun with `--cut N`; if a cut hole was actually part of the figure, `--keep N`.
3. Confirm the destination before writing into a project folder: the default name
   (`_cut.png`) is a scratch name — ask the user whether to save it under the project's
   final name (e.g. `mao-portrait-pained-a.png`) or keep the scratch file.

The output is still full-resolution (no resize). Resizing / WebP conversion is a
separate step and must happen **after** this cut (resizing on white is what creates the
fringe in the first place).

### Options

| flag | effect |
|---|---|
| `--ref auto\|none\|PATH` | Reference PNG. `auto` (default) = a finished sibling of the same name without `-cropped`, else `*-portrait-neutral-*.png` / `*-neutral*.png` in the input's folder, provided it is actually transparent. |
| `--profile NAME\|PATH` | Load defaults from `profiles/NAME.json` (keys: `bg`, `tol`, `band`, `holes`, `soften`, `min_hole`, `max_shift`, `light_fg`, `ref`). CLI flags override. |
| `--bg R,G,B` | Background colour (default `255,255,255`). |
| `--tol N` | Chebyshev distance from `bg` still counted as background (default 22). Raise for JPEG-ish noisy backgrounds, lower if light clothing gets eaten. |
| `--band N` | Width of the edge ring that is re-solved (default 3 px). 2 for very crisp lineart, 4–5 for soft/blurry edges. |
| `--holes keep\|cut\|ref` | Policy for enclosed background-coloured blobs (default `ref`: ≥30 % reference-background → cut, 5–30 % → `keep?`, else keep). |
| `--cut 2,5` / `--keep 3` | Force holes by their printed number. |
| `--light-fg N` | If the nearest opaque colour is closer than N (Euclidean RGB) to `bg`, use reference alpha instead of solving (default 48). |
| `--soften SIGMA` | Gaussian on the band alpha (default 0 = off; 0.5 if edges look stair-stepped). |
| `--min-hole N` | Ignore enclosed blobs smaller than N px (default 24; they stay opaque). |
| `--alpha-out PATH` | Also write the alpha as a grey PNG (to continue in Photopea / for masks). |
| `--preview` | Write `<out_stem>_preview.png`. |

### What it does not do

- It does not decide ambiguous holes on its own when there is no reference; it lists
  them. Teeth, eye whites and sweat drops are enclosed white blobs too — never cut
  holes blindly with `--holes cut` on a face.
- It does not crop, resize or convert to WebP. Keep the project's own order:
  crop → **this** → resize → WebP.
- Backgrounds must be a single flat colour. Gradients / textured backgrounds need a
  different tool.

## Profiles

`profiles/example.json` shows the keys. A per-character profile is only worth adding
when a character needs non-default values (e.g. light hair → `"light_fg": 70`, or a
coloured generation background → `"bg": [0, 255, 0]`).
