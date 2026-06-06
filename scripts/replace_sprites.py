#!/usr/bin/env python3
import argparse, os, re, struct, sys
from dataclasses import dataclass
from typing import List, Tuple, Dict
from PIL import Image

# --------------- helpers (LE/SE readers) ---------------
def le16(b, o): return struct.unpack_from("<H", b, o)[0]
def le32(b, o): return struct.unpack_from("<I", b, o)[0]
def se16(b, o): return struct.unpack_from("<h", b, o)[0]

# --------------- ARGB1555 convertors ---------------
def argb1555_normal(w):
    a = 255 if (w & 0x8000) else 0
    r = ((w >> 10) & 0x1F) * 255 // 31
    g = ((w >> 5)  & 0x1F) * 255 // 31
    b = ( w        & 0x1F) * 255 // 31
    return (r,g,b,a)

def argb1555_inverted(w):
    a = 0 if (w & 0x8000) else 255
    r = ((w >> 10) & 0x1F) * 255 // 31
    g = ((w >> 5)  & 0x1F) * 255 // 31
    b = ( w        & 0x1F) * 255 // 31
    return (r,g,b,a)

def bits_pp(sp_color): return sp_color*2 + 2

# --------------- package structs ---------------
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
    sp_depth   = (a >> 12) & 0x3
    sp_blend   = (a >> 14) & 0x1
    quadruple  = (a >> 15) & 0x1
    return sp_color, sp_flip, sp_hsize, sp_vsize, sp_palette, sp_depth, sp_blend, quadruple

def sprite_dims(attr):
    sp_color, sp_flip, sp_hsize, sp_vsize, *_ = unpack_attr(attr)
    return (8 << sp_hsize), (8 << sp_vsize), bits_pp(sp_color)

# --------------- parsing the sprites package ---------------
def parse_package(block: bytes, offsets):
    img_defs_offset, spr_defs_offset, palettes_offset, chars_offset = offsets
    img_len = spr_defs_offset - img_defs_offset
    spr_len = palettes_offset - spr_defs_offset
    pal_len = chars_offset    - palettes_offset
    assert img_len % 6 == 0 and spr_len % 8 == 0 and pal_len % 2 == 0
    num_images  = img_len // 6
    num_sprites = spr_len // 8
    num_colors  = pal_len // 2

    images=[]
    for i in range(num_images):
        o = img_defs_offset + i*6
        images.append(ImageDef(le16(block,o), block[o+2], block[o+3], le16(block,o+4)))
    sprites=[]
    for i in range(num_sprites):
        o = spr_defs_offset + i*8
        sprites.append(SpriteDef(le16(block,o), se16(block,o+2), se16(block,o+4), le16(block,o+6)))
    palette_words = [le16(block, palettes_offset + 2*i) for i in range(num_colors)]
    return images, sprites, palette_words, chars_offset

def robust_scan(data: bytes) -> Tuple[int, bytes, Tuple[int,int,int,int]]:
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
        try:
            images, sprites, palette_words, chars_offset = parse_package(block, (img_defs, spr_defs, palettes, chars))
        except Exception:
            continue
        if images[-1].sprite_start_index >= num_sprites:
            continue
        score = chars
        if best is None or score > best[0]:
            best = (score, off, block, (img_defs, spr_defs, palettes, chars))
    if not best:
        raise RuntimeError("No sprites package found with robust scan")
    _, off, block, offs = best
    return off, block, offs

# --------------- pixel packing (MSB first) ---------------
def pack_bits_msb(indexes, w, h, bpp):
    total = w*h*bpp
    nbytes = (total + 7)//8
    out = bytearray()
    acc = 0
    accbits = 0
    mask = (1<<bpp)-1
    it = iter(indexes)
    for _ in range(w*h):
        v = next(it) & mask
        acc = (acc << bpp) | v
        accbits += bpp
        while accbits >= 8:
            shift = accbits - 8
            out.append((acc >> shift) & 0xFF)
            acc &= (1<<shift)-1
            accbits -= 8
    if accbits > 0:
        out.append((acc << (8-accbits)) & 0xFF)
    if len(out) != nbytes:
        out = out[:nbytes] + b'\x00' * max(0, nbytes - len(out))
    return bytes(out)

# --------------- palette handling ---------------
def pick_palette_rgba(palette_words, base_index_quartets, bank, step, colors, alpha_mode):
    base = base_index_quartets * 4  # palette_start_index counts groups of 4 colors
    if alpha_mode == "normal":
        conv = argb1555_normal
    elif alpha_mode == "inverted":
        conv = argb1555_inverted
    else:
        off = base + bank*step
        off = min(off, max(0, len(palette_words)-colors))
        norm = [argb1555_normal(palette_words[off+i])[3] for i in range(colors)]
        inv  = [argb1555_inverted(palette_words[off+i])[3] for i in range(colors)]
        conv = argb1555_inverted if sum(inv) > sum(norm) else argb1555_normal
    return [conv(w) for w in palette_words], base

# --------------- filename parser ---------------
# Accepts: 342_0_1.png  OR 342_0-1.png (bank is last group)
RE_NAME_A = re.compile(r"^(\d+)_(\d+)_(\d+)\.png$", re.IGNORECASE)
RE_NAME_B = re.compile(r"^(\d+)_(\d+)-(\d+)\.png$", re.IGNORECASE)

def parse_name(fname: str):
    m = RE_NAME_A.match(fname)
    if not m:
        m = RE_NAME_B.match(fname)
    if not m:
        return None
    idx = int(m.group(1)); sub = int(m.group(2)); bank = int(m.group(3))
    return idx, sub, bank

# --------------- main ---------------
def main():
    ap = argparse.ArgumentParser(
        description="Batch replace sprites in a D-3/Tamagotchi Smart BIN using PNGs named INDEX_SUBIMAGE_BANK.png"
    )
    ap.add_argument("bin", help="Path to input BIN")
    ap.add_argument("--input-dir", required=True, help="Folder with PNGs named INDEX_SUBIMAGE_BANK.png")
    ap.add_argument("--out", required=True, help="Output BIN path (can be same as input)")
    ap.add_argument("--alpha", choices=["auto","normal","inverted"], default="inverted", help="ARGB1555 alpha bit mode")
    ap.add_argument("--palette-step", choices=["colors","4"], default="colors", help="Palette bank stepping")
    ap.add_argument("--use-attr-palette", action="store_true", help="Ignore filename bank and use each sprite's Attribute0 bank")
    ap.add_argument("--recursive", action="store_true", help="Recurse into subfolders")
    ap.add_argument("--package-offset", default=None, help="Override package base (e.g., 0x1EF000 or 2027520)")
    ap.add_argument("--dry-run", action="store_true", help="List planned changes only")
    args = ap.parse_args()

    # Load BIN
    with open(args.bin, "rb") as f:
        data = bytearray(f.read())

    # Resolve package
    if args.package_offset is not None:
        off_str = args.package_offset.strip().lower()
        base = int(off_str, 16) if off_str.startswith("0x") else int(off_str)
        pkg_off = base
        img_defs = le32(data, pkg_off+0)
        spr_defs = le32(data, pkg_off+4)
        palettes = le32(data, pkg_off+8)
        chars    = le32(data, pkg_off+12)
        block = data[pkg_off: pkg_off + chars + 1_000_000]
        offs = (img_defs, spr_defs, palettes, chars)
    else:
        pkg_off, block, offs = robust_scan(data)

    images, sprites, palette_words, chars_offset = parse_package(block, offs)
    bin_chars_base = pkg_off + offs[3]  # chars section absolute offset in BIN

    # Precompute subimage bounds and sprite-lists
    # map[(image_index, subimage)] = dict(W=,H=,min_x=,min_y=, spr_indices=[indices], dims=[(w,h,bpp),...])
    subinfo: Dict[Tuple[int,int], dict] = {}
    for img_idx, idef in enumerate(images):
        spp = idef.width * idef.height
        if spp == 0:
            continue
        if img_idx + 1 < len(images):
            total_for_img = images[img_idx + 1].sprite_start_index - idef.sprite_start_index
        else:
            total_for_img = len(sprites) - idef.sprite_start_index
        subimages = max(1, total_for_img // spp)
        for si in range(subimages):
            spr0 = idef.sprite_start_index + si * spp
            sprs = sprites[spr0: spr0 + spp]
            rects = []
            dims = []
            for s in sprs:
                w,h,bpp = sprite_dims(s.attr)
                dims.append((w,h,bpp))
                rects.append((s.ox, s.oy, s.ox+w, s.oy+h))
            min_x = min(r[0] for r in rects); min_y = min(r[1] for r in rects)
            max_x = max(r[2] for r in rects); max_y = max(r[3] for r in rects)
            W = max_x - min_x; H = max_y - min_y
            subinfo[(img_idx, si)] = {
                "W": W, "H": H, "min_x": min_x, "min_y": min_y,
                "spr0": spr0, "spp": spp, "dims": dims,
            }

    # Collect PNG files
    pngs = []
    if args.recursive:
        for root, _, files in os.walk(args.input_dir):
            for fn in files:
                if fn.lower().endswith(".png"):
                    pngs.append(os.path.join(root, fn))
    else:
        for fn in os.listdir(args.input_dir):
            if fn.lower().endswith(".png"):
                pngs.append(os.path.join(args.input_dir, fn))

    pngs.sort()
    if not pngs:
        print("No PNGs found.")
        return

    # Process each PNG
    successes = 0
    failures = 0
    for path in pngs:
        fname = os.path.basename(path)
        parsed = parse_name(fname)
        if not parsed:
            print(f"[skip] {fname}: name does not match INDEX_SUBIMAGE_BANK.png or INDEX_SUBIMAGE-BANK.png")
            continue
        image_index, subimage, file_bank = parsed
        if not (0 <= image_index < len(images)):
            print(f"[err] {fname}: image_index {image_index} out of range 0..{len(images)-1}")
            failures += 1
            continue
        idef = images[image_index]
        key = (image_index, subimage)
        if key not in subinfo:
            print(f"[err] {fname}: subimage {subimage} not found for image {image_index}")
            failures += 1
            continue

        # Target geometry
        W = subinfo[key]["W"]; H = subinfo[key]["H"]
        min_x = subinfo[key]["min_x"]; min_y = subinfo[key]["min_y"]
        spr0 = subinfo[key]["spr0"]; spp = subinfo[key]["spp"]
        dims = subinfo[key]["dims"]
        sprs = sprites[spr0: spr0 + spp]

        # Load PNG and validate size
        try:
            png = Image.open(path).convert("RGBA")
        except Exception as e:
            print(f"[err] {fname}: cannot open PNG ({e})")
            failures += 1
            continue
        if png.size != (W, H):
            print(f"[err] {fname}: PNG size {png.size} must match target composed size {(W,H)}")
            failures += 1
            continue

        # Palette mode & step
        first_bpp = dims[0][2]
        colors_first = 1 << first_bpp
        step = colors_first if args.palette_step == "colors" else 4
        # Choose bank we use for sampling alpha if auto; when --use-attr-palette, sample using filename bank anyway (just for deciding conv)
        sample_bank = file_bank
        pal_rgba, base = pick_palette_rgba(palette_words, idef.palette_start_index, sample_bank, step, colors_first, args.alpha)

        if args.dry_run:
            print(f"[DRY] {fname}: will write image_index={image_index}, subimage={subimage}, bank={file_bank}, size={W}x{H}")
            successes += 1
            continue

        # Helper: nearest-color quantization into palette slice
        def map_tile(tile_rgba: Image.Image, pal_rgba_slice: list):
            w,h = tile_rgba.size
            src = tile_rgba.load()
            out = [0]*(w*h)
            k = 0
            for y in range(h):
                for x in range(w):
                    r,g,b,a = src[x,y]
                    best = 10**12; idx = 0
                    for j,(pr,pg,pb,pa) in enumerate(pal_rgba_slice):
                        dr=r-pr; dg=g-pg; db=b-pb; da=a-pa
                        d=dr*dr+dg*dg+db*db+da*da
                        if d < best:
                            best = d; idx = j
                    out[k]=idx; k+=1
            return out

        # Write all sprites of this subimage
        for s, (w,h,bpp) in zip(sprs, dims):
            # palette bank for this sprite
            bank_idx = (unpack_attr(s.attr)[4] if args.use_attr_palette else file_bank)
            # step is per bpp when using "colors" stepping
            step_this = (1 << bpp) if args.palette_step == "colors" else 4
            pal_off = base + bank_idx * step_this
            pal_slice = pal_rgba[pal_off: pal_off + (1<<bpp)]

            dx = s.ox - min_x; dy = s.oy - min_y
            tile = png.crop((dx, dy, dx+w, dy+h))
            idxs = map_tile(tile, pal_slice)
            packed = pack_bits_msb(idxs, w, h, bpp)
            nbytes = (w*h*bpp + 7)//8
            char_off = bin_chars_base + s.charnum * nbytes
            data[char_off: char_off + nbytes] = packed

        print(f"[OK]  {fname}: replaced image {image_index}, subimage {subimage} using bank {file_bank}")
        successes += 1

    # Write output
    if args.dry_run:
        print(f"[DRY] Completed: {successes} planned, {failures} skipped/failed")
    else:
        with open(args.out, "wb") as f:
            f.write(data)
        print(f"[DONE] Replaced {successes} file(s); {failures} skipped/failed. Wrote: {args.out}")

if __name__ == "__main__":
    main()
