# /// script
# requires-python = ">=3.9"
# dependencies = ["pillow>=10", "numpy>=1.24", "opencv-python>=4.8"]
# ///
"""Cut an anime-style character out of a solid (white) background WITHOUT a
white fringe, optionally guided by an already-finished transparent PNG of the
same character in the same framing (a "reference").

Pipeline
  1. sure-background = pixels close to the background colour that are
     connected to the image border (flood).  Enclosed near-background blobs
     ("holes": gap between arm and body, etc.) are NOT cut blindly; they are
     reported and handled by --holes.
  2. reference (optional): the finished PNG is aligned globally (phase
     correlation) and locally (block-wise template matching, so a few px of
     generation drift per region does not matter).  It decides holes, gates
     the "wedge" growth below, supplies alpha where the foreground is too
     light to solve, and flags silhouette differences for a human.
  3. wedges: the flood stops where a thin gap between two hair strands narrows
     to a blend of white and hair.  Pixels that are mostly background when
     solved against the darkest nearby colour, AND that the reference calls
     background, are added to the background (--seed does the same from a
     hand-picked point without a reference).
  4. band = a few px ring just inside the background edge (the only place a
     fringe can live).  Each band pixel is modelled as p = a*F + (1-a)*B with
     B = background colour and F = darkest surely-opaque colour nearby (the
     outline).  a is solved by projection; colour is decontaminated
     (B + (p-B)/a) for mostly-opaque pixels and replaced by F otherwise.
  5. suspects: opaque near-white specks that touch transparency are listed
     (and drawn magenta in the preview) so leftovers are easy to find.

Usage
  uv run --no-project defringe.py <input.png> [output.png]
        [--ref auto|none|PATH] [--profile NAME|PATH]
        [--bg R,G,B] [--tol N] [--band N] [--holes keep|cut|ref]
        [--cut 2,5] [--keep 3] [--seed x,y;x,y] [--no-wedge]
        [--soften SIGMA] [--preview] [--alpha-out PATH]
Default output: <input_stem>_cut.png beside the input (never overwrites input).
"""
import sys, os, json, argparse, glob
import numpy as np
import cv2
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(os.path.dirname(HERE), "profiles")

DEFAULTS = dict(bg=(255, 255, 255), tol=22, band=3, holes="ref",
                soften=0.0, min_hole=24, max_shift=24, light_fg=48,
                wedge_radius=4, wedge_gate=2, wedge_chroma=45, wedge_protect=2)


# ----------------------------------------------------------------- helpers
def load_profile(name_or_path):
    if not name_or_path:
        return {}
    p = name_or_path
    if not os.path.exists(p):
        p = os.path.join(PROFILE_DIR, name_or_path + ".json")
    if not os.path.exists(p):
        sys.exit(f"profile not found: {name_or_path}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def is_transparent_png(path):
    try:
        im = Image.open(path)
        return im.mode == "RGBA" and int(np.asarray(im)[..., 3].min()) == 0
    except Exception:
        return False


def find_reference(inp, spec):
    """spec: 'none' | 'auto' | explicit path. auto = finished sibling of same expression, else neutral."""
    if spec == "none":
        return None
    if spec and spec != "auto":
        return spec
    d = os.path.dirname(os.path.abspath(inp))
    base = os.path.basename(inp)
    cands = []
    if "-cropped" in base:
        cands.append(os.path.join(d, base.replace("-cropped", "")))
    cands += sorted(glob.glob(os.path.join(d, "*-portrait-neutral-*.png")))
    cands += sorted(glob.glob(os.path.join(d, "*-neutral*.png")))
    for c in cands:
        if os.path.abspath(c) != os.path.abspath(inp) and os.path.exists(c) and is_transparent_png(c):
            return c
    return None


def edge_map(rgb, alpha, bg):
    a = alpha[..., None] / 255.0
    comp = rgb * a + np.array(bg, dtype=np.float64) * (1 - a)
    g = cv2.cvtColor(comp.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(gx, gy)


def shift_alpha(ref_a, dx, dy):
    """out(y, x) = ref(y - dy, x - dx), zero outside."""
    H, W = ref_a.shape
    out = np.zeros_like(ref_a)
    ys, ye = max(0, dy), min(H, H + dy)
    xs, xe = max(0, dx), min(W, W + dx)
    out[ys:ye, xs:xe] = ref_a[ys - dy:ye - dy, xs - dx:xe - dx]
    return out


def global_align(e_in, e_ref, max_shift):
    H, W = e_in.shape
    win = cv2.createHanningWindow((W, H), cv2.CV_32F)
    (dx, dy), resp = cv2.phaseCorrelate(e_ref, e_in, win)
    dx, dy = int(round(dx)), int(round(dy))
    if abs(dx) > max_shift or abs(dy) > max_shift:
        print(f"  ! reference shift ({dx},{dy}) exceeds --max-shift {max_shift}; using 0,0")
        dx = dy = 0
    return (dx, dy), float(resp)


def local_align_field(e_in, e_ref, gdx, gdy, block=64, margin=16, search=8, min_score=0.3):
    """Block-wise shift field (dx, dy per pixel) aligning the reference onto the input.
    Blocks without texture or without a confident match fall back to the global shift."""
    H, W = e_in.shape
    pad = search + abs(gdx) + abs(gdy)
    e_ref_p = np.pad(e_ref, pad)
    ny, nx = max(1, round(H / block)), max(1, round(W / block))
    fdx = np.full((ny, nx), float(gdx), np.float32)
    fdy = np.full((ny, nx), float(gdy), np.float32)
    for by in range(ny):
        for bx in range(nx):
            y0 = max(0, int(by * H / ny) - margin); y1 = min(H, int((by + 1) * H / ny) + margin)
            x0 = max(0, int(bx * W / nx) - margin); x1 = min(W, int((bx + 1) * W / nx) + margin)
            tpl = e_in[y0:y1, x0:x1]
            if tpl.std() < 1.0:
                continue
            # candidate ref region: around the globally shifted position, +/- search
            ry0, ry1 = y0 - gdy - search + pad, y1 - gdy + search + pad
            rx0, rx1 = x0 - gdx - search + pad, x1 - gdx + search + pad
            img = e_ref_p[ry0:ry1, rx0:rx1]
            if img.std() < 1.0:
                continue
            res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
            _, score, _, (j, i) = cv2.minMaxLoc(res)
            if score < min_score:
                continue
            fdx[by, bx] = gdx + (search - j)
            fdy[by, bx] = gdy + (search - i)
    if ny >= 3 and nx >= 3:
        fdx = cv2.medianBlur(fdx, 3)
        fdy = cv2.medianBlur(fdy, 3)
    fdx = cv2.resize(fdx, (W, H), interpolation=cv2.INTER_LINEAR)
    fdy = cv2.resize(fdy, (W, H), interpolation=cv2.INTER_LINEAR)
    return fdx, fdy


def warp_alpha(img, fdx, fdy):
    H, W = img.shape[:2]
    xs, ys = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
    return cv2.remap(img, xs - fdx, ys - fdy, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def local_ref_fraction(ref_a, e_in, e_ref, m, bbox, search=10, pad=28):
    """Fraction of blob m the reference calls background after re-aligning the reference
    in a window around the blob (exhaustive integer search)."""
    H, W = m.shape
    x, y, w, h = bbox
    y0, y1 = max(0, y - pad), min(H, y + h + pad)
    x0, x1 = max(0, x - pad), min(W, x + w + pad)
    win_in = e_in[y0:y1, x0:x1]
    best, best_s = -1.0, (0, 0)
    for dy in range(-search, search + 1):
        for dx in range(-search, search + 1):
            sy0, sy1, sx0, sx1 = y0 - dy, y1 - dy, x0 - dx, x1 - dx
            if sy0 < 0 or sx0 < 0 or sy1 > H or sx1 > W:
                continue
            win_ref = e_ref[sy0:sy1, sx0:sx1]
            s = float((win_in * win_ref).sum()) / (float(np.sqrt((win_in ** 2).sum() * (win_ref ** 2).sum())) + 1e-6)
            if s > best:
                best, best_s = s, (dx, dy)
    dx, dy = best_s
    ys, xs = np.nonzero(m)
    ys2, xs2 = ys - dy, xs - dx
    ok = (ys2 >= 0) & (ys2 < H) & (xs2 >= 0) & (xs2 < W)
    if not ok.any():
        return float((ref_a[m] < 128).mean()), (0, 0)
    return float((ref_a[ys2[ok], xs2[ok]] < 128).mean()), (dx, dy)


def components(mask, connectivity=4):
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=connectivity)
    return n, lab, stats


def ellipse(r):
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def darkest_within(rgb, radius, mask=None):
    """Per pixel: colour of the darkest (lowest luminance) pixel within `radius`, restricted to
    `mask` when given. Lineart silhouettes are closed by a dark outline, so this is the right
    foreground colour to solve edge alpha against. Returns (colour HxWx3, found HxW bool)."""
    H, W = rgb.shape[:2]
    lum = rgb @ np.array([0.299, 0.587, 0.114])
    if mask is not None:
        lum = np.where(mask, lum, np.inf)
    best = np.full((H, W), np.inf)
    col = np.zeros_like(rgb)
    pad = radius
    lum_p = np.pad(lum, pad, constant_values=np.inf)
    rgb_p = np.pad(rgb, ((pad, pad), (pad, pad), (0, 0)))
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx * dx + dy * dy > radius * radius:
                continue
            l = lum_p[pad + dy:pad + dy + H, pad + dx:pad + dx + W]
            better = l < best
            if better.any():
                best = np.where(better, l, best)
                col[better] = rgb_p[pad + dy:pad + dy + H, pad + dx:pad + dx + W][better]
    return col, np.isfinite(best)


def project_alpha(p, F, bg):
    """alpha of p on the segment bg -> F (p = a*F + (1-a)*bg), clipped to [0,1]."""
    fb = F - bg
    denom = np.sum(fb * fb, axis=-1)
    a = np.sum((p - bg) * fb, axis=-1) / np.maximum(denom, 1e-6)
    return np.clip(a, 0.0, 1.0), np.sqrt(denom)


def ridge_mask(lum, blocked, dark_lum=70, dists=(2, 3)):
    """Thin bright structures squeezed between dark pixels: for some orientation, both pixels at
    distance d on either side are dark (and not background). A light rim next to the background
    fails this (one side is background), a gap between two hair strands passes."""
    H, W = lum.shape
    dark = (lum < dark_lum) & ~blocked
    out = np.zeros((H, W), bool)
    for d in dists:
        pd = np.pad(dark, d)
        for (uy, ux) in ((0, 1), (1, 0), (1, 1), (1, -1)):
            oy, ox = d * uy, d * ux
            a = pd[d + oy:d + oy + H, d + ox:d + ox + W]
            b = pd[d - oy:d - oy + H, d - ox:d - ox + W]
            out |= a & b
    return out


def grow_from(seed, allowed):
    """All pixels of `allowed` 8-connected to `seed` (plus seed itself)."""
    n, lab = cv2.connectedComponents((allowed | seed).astype(np.uint8), connectivity=8)
    ids = np.unique(lab[seed])
    ids = ids[ids != 0]
    return np.isin(lab, ids) if len(ids) else seed.copy()


def parse_points(s):
    pts = []
    for item in s.replace(";", " ").split():
        x, y = item.split(",")
        pts.append((int(x), int(y)))
    return pts


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("output", nargs="?")
    ap.add_argument("--ref", default="auto", help="auto | none | path to finished RGBA PNG of same framing")
    ap.add_argument("--profile", default=None, help="profile name (profiles/<name>.json) or path")
    ap.add_argument("--bg", default=None, help="background colour R,G,B (default 255,255,255)")
    ap.add_argument("--tol", type=int, default=None, help="max channel distance from bg to count as background")
    ap.add_argument("--band", type=int, default=None, help="width (px) of the edge band that is re-solved")
    ap.add_argument("--holes", choices=["keep", "cut", "ref"], default=None,
                    help="enclosed background-coloured blobs: keep opaque / cut / follow reference (default ref)")
    ap.add_argument("--cut", default="", help="hole numbers (from the printed list) to cut regardless, e.g. 2,5")
    ap.add_argument("--keep", default="", help="hole numbers to keep opaque regardless")
    ap.add_argument("--seed", default="", help="points 'x,y;x,y' inside leftover gaps: background grows from there")
    ap.add_argument("--no-wedge", action="store_true", help="disable reference-gated growth into thin gaps")
    ap.add_argument("--protect", default="", help="rectangles 'x,y,w,h;x,y,w,h' the wedge step must leave alone")
    ap.add_argument("--soften", type=float, default=None, help="gaussian sigma applied to alpha in the band (0=off)")
    ap.add_argument("--min-hole", type=int, default=None, help="list kept enclosed blobs only from this size (px)")
    ap.add_argument("--max-shift", type=int, default=None, help="max px the reference may be shifted globally")
    ap.add_argument("--light-fg", type=int, default=None,
                    help="if the darkest nearby opaque colour is closer than this to bg, use reference alpha instead")
    ap.add_argument("--alpha-out", default=None, help="also write the alpha channel as a grayscale PNG")
    ap.add_argument("--preview", action="store_true", help="write <out_stem>_preview.png (result + flags + edge zooms)")
    ap.add_argument("--debug-window", default=None, help="x,y,w,h: print the wedge-growth masks for this window")
    args = ap.parse_args()

    cfg = dict(DEFAULTS)
    prof = load_profile(args.profile)
    cfg.update({k: v for k, v in prof.items() if k in cfg})
    for k in ("tol", "band", "holes", "soften", "min_hole", "max_shift", "light_fg"):
        v = getattr(args, k)
        if v is not None:
            cfg[k] = v
    if args.bg:
        cfg["bg"] = tuple(int(x) for x in args.bg.split(","))
    bg = np.array(cfg["bg"], dtype=np.float64)
    ref_spec = args.ref if args.ref != "auto" else prof.get("ref", "auto")

    inp = args.input
    out = args.output or os.path.splitext(inp)[0] + "_cut.png"
    if os.path.abspath(out) == os.path.abspath(inp):
        sys.exit("refusing to overwrite the input; give a different output path")

    im = Image.open(inp).convert("RGBA")
    rgba = np.asarray(im).astype(np.float64)
    rgb, a_in = rgba[..., :3], rgba[..., 3]
    H, W = rgb.shape[:2]
    print(f"[defringe] {inp} ({W}x{H}) bg={tuple(int(x) for x in bg)} tol={cfg['tol']} band={cfg['band']}")

    # ---- 1. background: near-bg colour connected to the border (plus pre-existing transparency)
    dist_bg = np.max(np.abs(rgb - bg), axis=2)
    near_bg = (dist_bg <= cfg["tol"]) | (a_in < 8)
    n, lab, stats = components(near_bg, 4)
    border_ids = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])).tolist())
    border_ids.discard(0)
    sure_bg = np.isin(lab, list(border_ids)) if border_ids else np.zeros((H, W), bool)
    sure_bg |= (a_in < 8)
    hole_ids = [i for i in range(1, n) if i not in border_ids and stats[i, cv2.CC_STAT_AREA] >= 3]

    # ---- 2. reference: global + local alignment
    ref_path = find_reference(inp, ref_spec)
    ref_a = ref_g = None
    if ref_path:
        ref = np.asarray(Image.open(ref_path).convert("RGBA"))
        if ref.shape[:2] != (H, W):
            print(f"  ! reference {ref_path} is {ref.shape[1]}x{ref.shape[0]}, input is {W}x{H}; ignoring reference")
        else:
            ref_a_raw = ref[..., 3]
            e_in = edge_map(rgb, np.full((H, W), 255, np.uint8), bg)
            e_ref = edge_map(ref[..., :3].astype(np.float64), ref_a_raw, bg)
            (gdx, gdy), resp = global_align(e_in, e_ref, cfg["max_shift"])
            ref_g = shift_alpha(ref_a_raw, gdx, gdy)
            fdx, fdy = local_align_field(e_in, e_ref, gdx, gdy)
            ref_a = warp_alpha(ref_a_raw, fdx, fdy)          # locally aligned alpha (primary)
            ref_rgb_w = warp_alpha(np.ascontiguousarray(ref[..., :3]), fdx, fdy)
            print(f"  reference: {os.path.basename(ref_path)} global shift=({gdx},{gdy}) response={resp:.3f}; "
                  f"local shift range x[{fdx.min():+.0f},{fdx.max():+.0f}] y[{fdy.min():+.0f},{fdy.max():+.0f}]")
    else:
        print("  reference: none")

    # ---- 3. enclosed holes (numbered; override with --cut / --keep)
    force_cut = {int(s) for s in args.cut.split(",") if s.strip()}
    force_keep = {int(s) for s in args.keep.split(",") if s.strip()}
    hole_mask = np.zeros((H, W), bool)
    unsure_mask = np.zeros((H, W), bool)
    hole_labels = []  # (number, x, y) for the preview
    for num, i in enumerate(hole_ids, 1):
        m = lab == i
        x, y, w, h, area = (int(v) for v in stats[i])
        decision, note = cfg["holes"], ""
        if decision == "ref":
            if ref_a is None:
                decision, note = "keep", "no reference"
            else:
                # interior features (teeth, eye whites) are ~0% under ANY plausible shift, so take
                # the largest of the global / field / per-blob local estimates
                f_g = float((ref_g[m] < 128).mean())
                f_f = float((ref_a[m] < 128).mean())
                f_l, (ldx, ldy) = local_ref_fraction(ref_a_raw, e_in, e_ref, m, (x, y, w, h))
                frac_bg = max(f_g, f_f, f_l)
                note = f"reference: {frac_bg*100:.0f}% background"
                decision = "cut" if frac_bg >= 0.30 else ("keep?" if frac_bg >= 0.05 else "keep")
        if num in force_cut:
            decision, note = "cut", "forced by --cut"
        elif num in force_keep:
            decision, note = "keep", "forced by --keep"
        if area >= cfg["min_hole"] or decision != "keep":
            print(f"  hole #{num}: {w}x{h}+{x}+{y} area={area} -> {decision}" + (f" ({note})" if note else ""))
            hole_labels.append((num, x, y))
        if decision == "cut":
            hole_mask |= m
        elif decision == "keep?":
            unsure_mask |= m
    if unsure_mask.any():
        print("  'keep?' = kept opaque but uncertain; check the preview (orange) and rerun with --cut N if it is background")
    sure_bg_all = sure_bg | hole_mask

    # ---- 4. wedges: thin gaps between strands where the flood stopped at a blended neck
    F_all, found = darkest_within(rgb, cfg["wedge_radius"])
    a_all, strength = project_alpha(rgb, F_all, bg)
    bg_like = (a_all < 0.5) & found & (strength >= cfg["light_fg"])
    wedge_mask = np.zeros((H, W), bool)
    user_protect = np.zeros((H, W), bool)
    for rect in args.protect.replace(";", " ").split():
        px, py, pw, ph = (int(v) for v in rect.split(","))
        user_protect[max(0, py):py + ph, max(0, px):px + pw] = True
    bg_like &= ~user_protect
    if ref_a is not None and not args.no_wedge:
        # the (locally aligned) reference must call it background, the pixel must sit on a thin
        # bright ridge between dark strands, and a light opaque feature the reference has nearby
        # (metal clasp, highlight) is never eaten
        g = cfg["wedge_gate"]
        gate = cv2.dilate((ref_a < 128).astype(np.uint8), ellipse(g)).astype(bool)
        ref_lum = ref_rgb_w.astype(np.float64) @ np.array([0.299, 0.587, 0.114])
        protect = cv2.dilate(((ref_a >= 128) & (ref_lum > 110)).astype(np.uint8),
                             ellipse(cfg["wedge_protect"])).astype(bool)
        lum_in = rgb @ np.array([0.299, 0.587, 0.114])
        ridge = ridge_mask(lum_in, sure_bg_all)
        # a gap is a blend of background and outline, i.e. nearly neutral; coloured trim is not
        neutral = (rgb.max(axis=2) - rgb.min(axis=2)) < cfg["wedge_chroma"]
        # walk through strongly blended pixels (a < 0.7) so a 1 px gap stays connected, but only
        # pixels that are mostly background (a < 0.5) become transparent; the rest are band pixels
        walk = (a_all < 0.7) & found & (strength >= cfg["light_fg"]) & ridge & gate & ~protect & neutral
        grown = grow_from(sure_bg_all, walk) & (bg_like | sure_bg_all)
        if args.debug_window:
            dx0, dy0, dw, dh = (int(v) for v in args.debug_window.split(","))
            sl = (slice(dy0, dy0 + dh), slice(dx0, dx0 + dw))
            np.set_printoptions(linewidth=250)
            for name, mk in (("sure_bg_all", sure_bg_all), ("bg_like", bg_like), ("ridge", ridge),
                             ("gate", gate),
                             ("protect", protect), ("grown", grown)):
                print(f"  [debug] {name} @ {dx0},{dy0}"); print(mk[sl].astype(int))
            print("  [debug] a_all*10"); print((a_all[sl] * 10).astype(int))
        wedge_mask |= grown & ~sure_bg_all
    seeds = parse_points(args.seed)
    if seeds:
        seed_mask = np.zeros((H, W), bool)
        for (sx, sy) in seeds:
            if 0 <= sx < W and 0 <= sy < H:
                seed_mask[sy, sx] = True
        neutral = (rgb.max(axis=2) - rgb.min(axis=2)) < cfg["wedge_chroma"]
        walk = (a_all < 0.7) & found & ridge_mask(rgb @ np.array([0.299, 0.587, 0.114]), sure_bg_all) & neutral
        grown = grow_from(seed_mask, walk) & (bg_like | seed_mask)
        wedge_mask |= grown & ~sure_bg_all
        print(f"  seeds: {len(seeds)} point(s) -> {int((grown & ~sure_bg_all).sum())} px")
    if wedge_mask.any():
        print(f"  wedges: {int(wedge_mask.sum())} px added to the background at thin gaps")
    sure_bg_all |= wedge_mask

    # ---- 5. band (ring inside the bg edge) and core (surely opaque)
    r = cfg["band"]
    near_edge = cv2.dilate(sure_bg_all.astype(np.uint8), ellipse(r)).astype(bool)
    band = near_edge & ~sure_bg_all
    core = ~cv2.dilate(sure_bg_all.astype(np.uint8), ellipse(r + 1)).astype(bool)
    # foreground colour for each band pixel = darkest core pixel nearby (the outline);
    # fall back to the nearest core pixel where no core pixel is within reach
    F_dark, found_c = darkest_within(rgb, r + 2, mask=core)
    _, labels = cv2.distanceTransformWithLabels((~core).astype(np.uint8), cv2.DIST_L2, 5,
                                                labelType=cv2.DIST_LABEL_PIXEL)
    core_yx = np.argwhere(core)  # DIST_LABEL_PIXEL numbers zero pixels 1..N in raster order
    idx = labels[band] - 1
    F = np.where(found_c[band][:, None], F_dark[band], rgb[core_yx[idx, 0], core_yx[idx, 1]])

    # ---- 6. alpha in the band:  p = a*F + (1-a)*B
    alpha = np.where(sure_bg_all, 0.0, 1.0)
    p = rgb[band]
    a_est, strength_b = project_alpha(p, F, bg)
    light = strength_b < cfg["light_fg"]
    n_light = int(light.sum())
    if n_light:
        if ref_a is not None:
            a_est[light] = ref_a[band][light] / 255.0
            fb_mode = "reference"
        else:
            a_est[light] = (dist_bg[band][light] > cfg["tol"]).astype(np.float64)
            fb_mode = "hard"
        print(f"  band: {int(band.sum())} px; {n_light} px have light foreground -> {fb_mode} fallback")
    else:
        print(f"  band: {int(band.sum())} px")
    alpha[band] = a_est

    # ---- 7. colour in the band: decontaminate where mostly opaque, outline colour otherwise
    out_rgb = rgb.copy()
    a_safe = np.maximum(a_est, 1e-3)[:, None]
    decont = np.clip(bg + (p - bg) / a_safe, 0, 255)
    w_dec = np.clip((a_est - 0.35) / 0.4, 0, 1)[:, None]   # 0 at a<=0.35 … 1 at a>=0.75
    out_rgb[band] = decont * w_dec + F * (1 - w_dec)
    out_rgb[sure_bg_all] = bg

    if cfg["soften"] > 0:
        blurred = cv2.GaussianBlur(alpha.astype(np.float32), (0, 0), cfg["soften"]).astype(np.float64)
        alpha[band] = blurred[band]

    a8 = np.clip(np.round(alpha * 255), 0, 255).astype(np.uint8)
    result = np.dstack([np.clip(np.round(out_rgb), 0, 255).astype(np.uint8), a8])
    Image.fromarray(result, "RGBA").save(out)
    print(f"  -> {out}")
    if args.alpha_out:
        Image.fromarray(a8, "L").save(args.alpha_out)
        print(f"  -> alpha {args.alpha_out}")

    # ---- 8. suspects: opaque near-background specks touching transparency (possible leftovers)
    whiteish = (dist_bg <= 30) & (a8 >= 128)
    near_t = cv2.dilate((a8 < 128).astype(np.uint8), ellipse(4)).astype(bool)
    n3, lab3, st3 = components(whiteish, 8)
    sus_ids = [i for i in range(1, n3) if 3 <= st3[i, cv2.CC_STAT_AREA] <= 600 and (near_t[lab3 == i]).any()]
    suspects = np.isin(lab3, sus_ids)
    if sus_ids:
        print(f"  suspects: {len(sus_ids)} opaque near-white speck(s) touching transparency (magenta in preview; "
              f"use --seed x,y or --cut if they are background):")
        for i in sorted(sus_ids, key=lambda i: -st3[i, cv2.CC_STAT_AREA])[:10]:
            x, y, w, h, area = (int(v) for v in st3[i])
            note = ""
            if ref_a is not None:
                note = f"  reference: {100 * float((ref_a[lab3 == i] < 128).mean()):.0f}% background"
            print(f"    {w}x{h}+{x}+{y} area={area}{note}")

    # ---- 9. flags: silhouette differences vs (locally aligned) reference
    flags = np.zeros((H, W), bool)
    if ref_a is not None:
        diff = (a8 > 128) != (ref_a > 128)
        diff[:8, :] = diff[-8:, :] = diff[:, :8] = diff[:, -8:] = False   # warp border, not a real difference
        diff = cv2.morphologyEx(diff.astype(np.uint8), cv2.MORPH_OPEN, ellipse(3)).astype(bool)
        n2, lab2, st2 = components(diff, 8)
        keep = [i for i in range(1, n2) if st2[i, cv2.CC_STAT_AREA] >= 100]
        flags = np.isin(lab2, keep)
        boxes = sorted((tuple(int(v) for v in st2[i]) for i in keep), key=lambda b: -b[4])
        print(f"  differs from reference in {len(boxes)} region(s)" + (":" if boxes else ""))
        for x, y, w, h, area in boxes[:12]:
            print(f"    {w}x{h}+{x}+{y} area={area}")

    if args.preview:
        write_preview(out, result, band, flags, hole_mask, unsure_mask, wedge_mask, suspects, hole_labels)


def write_preview(out, result, band, flags, hole_mask, unsure_mask, wedge_mask, suspects, hole_labels):
    H, W = result.shape[:2]
    rgb = result[..., :3].astype(np.float64)
    a = result[..., 3:4] / 255.0
    green = np.array([40, 190, 40], np.float64)
    over = rgb * a + green * (1 - a)
    marked = over.copy()
    marked[band] = marked[band] * 0.4 + np.array([60, 60, 255]) * 0.6
    marked[hole_mask] = marked[hole_mask] * 0.3 + np.array([255, 220, 0]) * 0.7
    marked[wedge_mask] = marked[wedge_mask] * 0.3 + np.array([0, 230, 230]) * 0.7
    marked[unsure_mask] = marked[unsure_mask] * 0.3 + np.array([255, 120, 0]) * 0.7
    marked[flags] = marked[flags] * 0.2 + np.array([255, 0, 0]) * 0.8
    sus_d = cv2.dilate(suspects.astype(np.uint8), ellipse(3)).astype(bool)
    marked[sus_d] = np.array([255, 0, 255])
    marked = np.ascontiguousarray(marked.astype(np.uint8))
    for num, x, y in hole_labels:
        pos = (max(0, min(W - 24, x - 4)), max(14, y - 2))
        cv2.putText(marked, f"#{num}", pos, cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
        cv2.putText(marked, f"#{num}", pos, cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    marked = marked.astype(np.float64)

    zooms = []
    pts = []
    ys, xs = np.nonzero(band)
    if len(ys):
        pts += [(int(ys[i]), int(xs[i])) for i in (ys.argmin(), ys.argmax(), xs.argmin(), xs.argmax())]
    for mask in (suspects, flags):
        if mask.any():
            n, lab, st = components(mask, 8)
            for i in sorted(range(1, n), key=lambda i: -st[i, cv2.CC_STAT_AREA])[:2]:
                pts.append((int(st[i, cv2.CC_STAT_TOP] + st[i, cv2.CC_STAT_HEIGHT] // 2),
                            int(st[i, cv2.CC_STAT_LEFT] + st[i, cv2.CC_STAT_WIDTH] // 2)))
    for (cy, cx) in pts[:8]:
        y0, x0 = max(0, min(H - 64, cy - 32)), max(0, min(W - 64, cx - 32))
        tile = cv2.resize(over[y0:y0 + 64, x0:x0 + 64].astype(np.uint8), (256, 256),
                          interpolation=cv2.INTER_NEAREST)
        cv2.putText(tile, f"{x0},{y0}", (4, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 2)
        cv2.putText(tile, f"{x0},{y0}", (4, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
        zooms.append(tile)

    strip_h = 256 + 8 if zooms else 0
    canvas = np.full((H + strip_h, W * 2 + 8, 3), 32, np.uint8)
    canvas[:H, :W] = over.astype(np.uint8)
    canvas[:H, W + 8:] = marked.astype(np.uint8)
    x = 0
    for z in zooms:
        if x + 256 > canvas.shape[1]:
            break
        canvas[H + 8:H + 8 + 256, x:x + 256] = z
        x += 256 + 4
    p = os.path.splitext(out)[0] + "_preview.png"
    Image.fromarray(canvas, "RGB").save(p)
    print(f"  -> preview {p}  (left: result on green / right: band=blue, cut holes=yellow, wedges=cyan, "
          f"uncertain=orange, suspects=magenta, differs-from-ref=red)")


if __name__ == "__main__":
    main()
