#!/usr/bin/env python3
"""
Batch palette updater (folder mode) for D-3 25th color / Tamagotchi Smart BIN.

Reads PNG files from a folder. Each PNG must be named:
    INDEX_SUBIMAGE_BANK.png
e.g.
    342_0_0.png
    342_1_0.png
    343_0_0.png

For each PNG:
  - INDEX     -> image index
  - SUBIMAGE  -> subimage index
  - BANK      -> palette bank to write (0..15). If --set-sprite-bank is used,
                 sprites in that subimage are updated to use this bank.

Other behavior matches the earlier single-file script:
  - Colors are taken from the PNG (up to the image bpp: 4/16/64/256).
  - Palettes are written in-place; character data untouched.
  - Alpha handling: ARGB1555 with 'inverted' alpha by default (0=opaque, 1=transparent).

Usage:
  python update_d3_palette_batch.py BIN --input-dir ./folder \
    --out out.bin --alpha-mode inverted --set-sprite-bank

Tips:
  - Always write to a new --out file instead of overwriting the input BIN.
  - Use --dry-run first to preview what will be changed.
"""

import argparse, os, re, struct
from dataclasses import dataclass
from typing import List, Tuple, Set
from PIL import Image

# ---------- low-level helpers ----------
def le16(b, o): return struct.unpack_from("<H", b, o)[0]
def le32(b, o): return struct.unpack_from("<I", b, o)[0]
def se16(b, o): return struct.unpack_from("<h", b, o)[0]

def argb_to_1555(r,g,b,a, inverted=True):
    r5 = max(0,min(31, (r*31 + 127)//255))
    g5 = max(0,min(31, (g*31 + 127)//255))
    b5 = max(0,min(31, (b*31 + 127)//255))
    # On Smart, alpha bit inverted: 0=opaque, 1=transparent
    a_bit = 0 if inverted and a >= 128 else (1 if inverted else (1 if a >= 128 else 0))
    return (a_bit<<15) | (r5<<10) | (g5<<5) | b5

@dataclass
class ImageDef:
    sprite_start_index: int
    width: int
    height: int
    palette_start_index: int

@dataclass
class SpriteDef:
    charnum: int
    ox: int
    oy: int
    attr: int

def unpack_attr(a):
    sp_color   =  a        & 0x3
    sp_flip    = (a >> 2)  & 0x3
    sp_hsize   = (a >> 4)  & 0x3
    sp_vsize   = (a >> 6)  & 0x3
    sp_palette = (a >> 8)  & 0xF
    return sp_color, sp_flip, sp_hsize, sp_vsize, sp_palette

def bits_pp(sp_color): return sp_color*2 + 2

def sprite_dims(attr):
    sp_color, sp_flip, sp_hsize, sp_vsize, _ = unpack_attr(attr)
    return (8 << sp_hsize), (8 << sp_vsize), bits_pp(sp_color)

# ---------- package scan / parse ----------
def robust_scan(data: bytes):
    size = len(data)
    best = None
    for off in range(0, size-16, 4):
        img_defs = le32(data, off+0)
        spr_defs = le32(data, off+4)
        palettes = le32(data, off+8)
        chars    = le32(data, off+12)
        if not (0 < img_defs < spr_defs < palettes < chars <= size - off):
            continue
        img_len = spr_defs - img_defs
        spr_len = palettes - spr_defs
        pal_len = chars    - palettes
        if (img_len % 6) or (spr_len % 8) or (pal_len % 2):
            continue
        num_images  = img_len // 6
        num_sprites = spr_len // 8
        if not (1000 <= num_images <= 5000):
            continue
        block = data[off: off + chars + 1_000_000]
        last_img_spr = le16(block, img_defs + (num_images-1)*6)
        if last_img_spr >= num_sprites:
            continue
        score = chars
        if best is None or score > best[0]:
            best = (score, off, block, (img_defs, spr_defs, palettes, chars))
    if not best:
        raise RuntimeError("No sprites package found")
    _, pkg_off, block, offs = best
    return pkg_off, block, offs

def parse(block: bytes, offs):
    img_defs, spr_defs, palettes, chars = offs
    num_images  = (spr_defs - img_defs)//6
    num_sprites = (palettes - spr_defs)//8
    images=[]
    for i in range(num_images):
        o = img_defs + i*6
        images.append(ImageDef(le16(block,o), block[o+2], block[o+3], le16(block,o+4)))
    sprites=[]
    for i in range(num_sprites):
        o = spr_defs + i*8
        sprites.append(SpriteDef(le16(block,o), se16(block,o+2), se16(block,o+4), le16(block,o+6)))
    return images, sprites, palettes

# ---------- palette helpers ----------
def collect_bank_usage(images, sprites, target_pal_start):
    """Return set of banks (0..15) referenced by any image sharing palette_start_index."""
    used: Set[int] = set()
    for img_idx, idef in enumerate(images):
        if idef.palette_start_index == target_pal_start:
            spp = idef.width * idef.height
            spr0 = idef.sprite_start_index
            total_for_img = (images[img_idx+1].sprite_start_index - spr0) if (img_idx+1 < len(images)) else (len(sprites) - spr0)
            subimages = max(1, total_for_img // max(1,spp))
            for si in range(subimages):
                s0 = spr0 + si*spp
                for s in sprites[s0:s0+spp]:
                    used.add((s.attr >> 8) & 0xF)
            if len(used) >= 8:
                return set(range(16))
    return used

def build_palette_from_png(png_path: str, max_colors: int, inverted_alpha: bool):
    png = Image.open(png_path).convert("RGBA")
    pix = list(png.getdata())
    unique = []
    seen = set()
    for (r,g,b,a) in pix:
        key = (r,g,b,a>=128)
        if key not in seen:
            seen.add(key)
            unique.append((r,g,b,255 if key[3] else 0))
        if len(unique) >= max_colors:
            break
    if len(unique) == 0:
        unique = [(0,0,0,0)]
    while len(unique) < max_colors:
        unique.append(unique[-1])
    words = [argb_to_1555(r,g,b,a, inverted=inverted_alpha) for (r,g,b,a) in unique]
    return words, png.size

def update_one(data, pkg_off, offs, images, sprites, image_index, subimage, png_path, target_bank, alpha_mode, set_sprite_bank, dry_run):
    img_defs_off, spr_defs_off, palettes_off, chars_off = offs
    if not (0 <= image_index < len(images)):
        raise SystemExit(f"image-index {image_index} out of range 0..{len(images)-1}")
    idef = images[image_index]
    spp = idef.width * idef.height
    if spp == 0:
        raise SystemExit(f"image {image_index}: zero sprites per subimage")
    if image_index + 1 < len(images):
        total_for_img = images[image_index + 1].sprite_start_index - idef.sprite_start_index
    else:
        total_for_img = len(sprites) - idef.sprite_start_index
    subimages = max(1, total_for_img // max(1,spp))
    if not (0 <= subimage < subimages):
        raise SystemExit(f"image {image_index}: subimage {subimage} out of range 0..{subimages-1}")

    spr0 = idef.sprite_start_index + subimage * spp
    first_attr = sprites[spr0].attr
    _,_,bpp = sprite_dims(first_attr)
    colors = 1 << bpp
    step = colors
    base_index = idef.palette_start_index * 4

    if isinstance(target_bank, str) and target_bank == "auto":
        used = collect_bank_usage(images, sprites, idef.palette_start_index)
        candidates = [b for b in range(16) if b not in used]
        bank = candidates[0] if candidates else 15
    else:
        bank = int(target_bank)
        if not (0 <= bank <= 15):
            raise SystemExit(f"bank {bank} out of range 0..15")

    inverted = (alpha_mode == "inverted")
    words, png_size = build_palette_from_png(png_path, colors, inverted)

    bank_off = base_index + bank*step
    pal_bytes_off = pkg_off + palettes_off + bank_off*2

    if dry_run:
        print(f"[DRY] img {image_index} si {subimage}: PNG {png_size}, "
              f"colors={colors}, write bank={bank} @ 0x{pal_bytes_off:X}")
        if set_sprite_bank:
            print(f"[DRY]    set sp_palette={bank} for {spp} sprite(s) in that subimage")
        return

    # write palette words
    for i, w in enumerate(words):
        off = pal_bytes_off + i*2
        data[off:off+2] = struct.pack("<H", w)

    # optionally set per-sprite bank nibble
    if set_sprite_bank:
        for idx in range(spr0, spr0 + spp):
            s_off = pkg_off + spr_defs_off + idx*8 + 6  # attribute field
            attr = le16(data, s_off)
            attr = (attr & ~(0xF<<8)) | ((bank & 0xF) << 8)
            data[s_off:s_off+2] = struct.pack("<H", attr)

    print(f"[OK] img {image_index} si {subimage}: wrote {colors} colors to bank {bank}")

# ---------- batch driver ----------
FNAME_RE = re.compile(r"^(\d+)_(\d+)_(\d+)\.(?:png|PNG)$")

def main():
    ap = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("bin", help="Path to BIN")
    ap.add_argument("--input-dir", required=True, help="Folder with PNGs named INDEX_SUBIMAGE_BANK.png")
    ap.add_argument("--out", required=True, help="Output BIN path")
    ap.add_argument("--alpha-mode", choices=["inverted","normal"], default="inverted",
                    help="ARGB1555 alpha bit meaning; inverted=Smart default (0=opaque,1=transparent)")
    ap.add_argument("--set-sprite-bank", action="store_true",
                    help="Force sprites in that subimage to use BANK parsed from filename")
    ap.add_argument("--dry-run", action="store_true", help="Do not write; just print actions")
    args = ap.parse_args()

    # Collect jobs from folder
    jobs: List[Tuple[int,int,int,str]] = []  # (index, subimage, bank, path)
    for root, _, files in os.walk(args.input_dir):
        for fn in files:
            m = FNAME_RE.match(fn)
            if not m:
                continue
            idx = int(m.group(1))
            sub = int(m.group(2))
            bank = int(m.group(3))
            p = os.path.join(root, fn)
            jobs.append((idx, sub, bank, p))

    if not jobs:
        raise SystemExit(f"No files matching INDEX_SUBIMAGE_BANK.png found in {args.input_dir}")

    # Sort for deterministic processing (index, subimage, bank, path)
    jobs.sort(key=lambda t: (t[0], t[1], t[2], t[3]))

    with open(args.bin, "rb") as f:
        data = bytearray(f.read())

    pkg_off, block, offs = robust_scan(data)
    images, sprites, palettes_off = parse(block, offs)

    # Execute
    for (idx, sub, bank, png_path) in jobs:
        update_one(data, pkg_off, offs, images, sprites,
                   idx, sub, png_path, bank,
                   args.alpha_mode, args.set_sprite_bank, args.dry_run)

    if args.dry_run:
        print(f"[DRY] Processed {len(jobs)} file(s). No output written.")
    else:
        with open(args.out, "wb") as f:
            f.write(data)
        print(f"[DONE] Updated {len(jobs)} palette bank(s). Wrote: {args.out}")

if __name__ == "__main__":
    main()
