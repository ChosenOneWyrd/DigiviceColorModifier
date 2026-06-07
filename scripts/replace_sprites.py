#!/usr/bin/env python3
import argparse
import os
import re
import struct
from dataclasses import dataclass
from typing import Dict, Tuple, List, Optional
from PIL import Image

try:
    import numpy as np
except Exception:
    np = None

# ---------------- little-endian readers ----------------
def le16(b, o): return struct.unpack_from("<H", b, o)[0]
def le32(b, o): return struct.unpack_from("<I", b, o)[0]
def se16(b, o): return struct.unpack_from("<h", b, o)[0]

# ---------------- ARGB1555 converters ----------------
def argb1555_normal(w):
    a = 255 if (w & 0x8000) else 0
    r = ((w >> 10) & 0x1F) * 255 // 31
    g = ((w >> 5)  & 0x1F) * 255 // 31
    b = ( w        & 0x1F) * 255 // 31
    return (r, g, b, a)

def argb1555_inverted(w):
    a = 0 if (w & 0x8000) else 255
    r = ((w >> 10) & 0x1F) * 255 // 31
    g = ((w >> 5)  & 0x1F) * 255 // 31
    b = ( w        & 0x1F) * 255 // 31
    return (r, g, b, a)

def bits_pp(sp_color):
    return sp_color * 2 + 2

# ---------------- package structs ----------------
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
    w: int
    h: int
    bpp: int
    attr_bank: int
    nbytes: int


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


def sprite_dims_bank_nbytes(attr):
    sp_color, _sp_flip, sp_hsize, sp_vsize, sp_palette, *_ = unpack_attr(attr)
    w = 8 << sp_hsize
    h = 8 << sp_vsize
    bpp = bits_pp(sp_color)
    nbytes = (w * h * bpp + 7) // 8
    return w, h, bpp, sp_palette, nbytes

# ---------------- parsing ----------------
def parse_package(block: bytes, offsets):
    img_defs_offset, spr_defs_offset, palettes_offset, chars_offset = offsets
    img_len = spr_defs_offset - img_defs_offset
    spr_len = palettes_offset - spr_defs_offset
    pal_len = chars_offset - palettes_offset
    assert img_len % 6 == 0 and spr_len % 8 == 0 and pal_len % 2 == 0

    num_images = img_len // 6
    num_sprites = spr_len // 8
    num_colors = pal_len // 2

    images: List[ImageDef] = []
    for i in range(num_images):
        o = img_defs_offset + i * 6
        images.append(ImageDef(le16(block, o), block[o+2], block[o+3], le16(block, o+4)))

    sprites: List[SpriteDef] = []
    for i in range(num_sprites):
        o = spr_defs_offset + i * 8
        attr = le16(block, o+6)
        w, h, bpp, bank, nbytes = sprite_dims_bank_nbytes(attr)
        sprites.append(SpriteDef(le16(block, o), se16(block, o+2), se16(block, o+4), attr, w, h, bpp, bank, nbytes))

    palette_words = [le16(block, palettes_offset + 2*i) for i in range(num_colors)]
    return images, sprites, palette_words, chars_offset


def robust_scan(data: bytes) -> Tuple[int, bytes, Tuple[int, int, int, int]]:
    size = len(data)
    best = None
    for off in range(0, size - 16, 4):
        img_defs = le32(data, off + 0)
        spr_defs = le32(data, off + 4)
        palettes = le32(data, off + 8)
        chars    = le32(data, off + 12)
        if not (0 < img_defs < spr_defs < palettes < chars <= size - off):
            continue
        img_len = spr_defs - img_defs
        spr_len = palettes - spr_defs
        pal_len = chars - palettes
        if (img_len % 6) or (spr_len % 8) or (pal_len % 2):
            continue
        num_images = img_len // 6
        num_sprites = spr_len // 8
        if not (1000 <= num_images <= 5000):
            continue
        block = data[off: off + chars + 1_000_000]
        try:
            images, _sprites, _palette_words, _chars_offset = parse_package(block, (img_defs, spr_defs, palettes, chars))
        except Exception:
            continue
        if images[-1].sprite_start_index >= num_sprites:
            continue
        score = chars
        if best is None or score > best[0]:
            best = (score, off, block, (img_defs, spr_defs, palettes, chars))
    if not best:
        raise RuntimeError("No sprites package found with robust scan")
    _score, off, block, offs = best
    return off, block, offs

# ---------------- exact MSB-first packing ----------------
def pack_bits_msb_reference(indexes, bpp):
    """Original safe packer behavior, kept as fallback and reference."""
    out = bytearray()
    acc = 0
    accbits = 0
    mask = (1 << bpp) - 1
    for v in indexes:
        acc = (acc << bpp) | (int(v) & mask)
        accbits += bpp
        while accbits >= 8:
            shift = accbits - 8
            out.append((acc >> shift) & 0xFF)
            acc &= (1 << shift) - 1
            accbits -= 8
    if accbits > 0:
        out.append((acc << (8 - accbits)) & 0xFF)
    return bytes(out)


def pack_bits_msb_fast(indexes, bpp, expected_nbytes=None):
    """Fast paths that are byte-for-byte equivalent to pack_bits_msb_reference."""
    if np is not None:
        arr = np.asarray(indexes, dtype=np.uint8)
        if bpp == 8:
            out = arr.tobytes()
        elif bpp == 4:
            if len(arr) & 1:
                arr = np.concatenate([arr, np.zeros(1, dtype=np.uint8)])
            out = ((arr[0::2] << 4) | arr[1::2]).astype(np.uint8).tobytes()
        elif bpp == 2:
            pad = (-len(arr)) & 3
            if pad:
                arr = np.concatenate([arr, np.zeros(pad, dtype=np.uint8)])
            out = ((arr[0::4] << 6) | (arr[1::4] << 4) | (arr[2::4] << 2) | arr[3::4]).astype(np.uint8).tobytes()
        else:
            out = pack_bits_msb_reference(indexes, bpp)
    else:
        out = pack_bits_msb_reference(indexes, bpp)

    if expected_nbytes is not None and len(out) != expected_nbytes:
        out = out[:expected_nbytes] + b"\x00" * max(0, expected_nbytes - len(out))
    return out

# ---------------- palette handling ----------------
def build_palette_rgba_lists(palette_words):
    normal = [argb1555_normal(w) for w in palette_words]
    inverted = [argb1555_inverted(w) for w in palette_words]
    return normal, inverted


def choose_palette_for_image(alpha_mode, pal_normal, pal_inverted, base, sample_bank, step, colors):
    """Matches the original script: auto decides once per PNG using the first sprite's bpp."""
    if alpha_mode == "normal":
        return pal_normal
    if alpha_mode == "inverted":
        return pal_inverted
    off = base + sample_bank * step
    off = min(off, max(0, len(pal_normal) - colors))
    norm_alpha = sum(a for *_rgb, a in pal_normal[off:off+colors])
    inv_alpha = sum(a for *_rgb, a in pal_inverted[off:off+colors])
    return pal_inverted if inv_alpha > norm_alpha else pal_normal

# ---------------- quantization ----------------
def quantize_tile_numpy_safe(png_arr, x0, y0, w, h, pal_slice_arr):
    """
    Safe vectorized nearest palette lookup.

    IMPORTANT: Use int32, not int16. int16 overflows when squaring differences
    up to 255, causing wrong palette indexes and visible sprite corruption.
    """
    tile = png_arr[y0:y0+h, x0:x0+w, :].reshape(-1, 4)

    # Exact-color fast path. Exported/edited sprites often already use palette colors.
    pal_u8 = pal_slice_arr.astype(np.uint8, copy=False)
    if len(pal_u8) <= 256:
        # Pack RGBA into uint32 keys for exact matching.
        tile_keys = (tile[:, 0].astype(np.uint32) << 24) | (tile[:, 1].astype(np.uint32) << 16) | (tile[:, 2].astype(np.uint32) << 8) | tile[:, 3].astype(np.uint32)
        pal_keys = (pal_u8[:, 0].astype(np.uint32) << 24) | (pal_u8[:, 1].astype(np.uint32) << 16) | (pal_u8[:, 2].astype(np.uint32) << 8) | pal_u8[:, 3].astype(np.uint32)
        order = np.argsort(pal_keys)
        sorted_keys = pal_keys[order]
        pos = np.searchsorted(sorted_keys, tile_keys)
        exact = (pos < len(sorted_keys)) & (sorted_keys[np.minimum(pos, len(sorted_keys)-1)] == tile_keys)
        if exact.all():
            return order[pos].astype(np.uint8)

    # Nearest-color fallback. int32 prevents overflow in squared distance.
    tile_i = tile.astype(np.int32)
    pal_i = pal_slice_arr.astype(np.int32)
    diff = tile_i[:, None, :] - pal_i[None, :, :]
    dist = np.sum(diff * diff, axis=2, dtype=np.int32)
    return np.argmin(dist, axis=1).astype(np.uint8)


def quantize_tile_python(png, x0, y0, w, h, pal_slice):
    """Original safe Python nearest-color loop."""
    src = png.load()
    out = [0] * (w * h)
    k = 0
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            r, g, b, a = src[x, y]
            best = 10**12
            idx = 0
            for j, (pr, pg, pb, pa) in enumerate(pal_slice):
                dr = r - pr
                dg = g - pg
                db = b - pb
                da = a - pa
                d = dr*dr + dg*dg + db*db + da*da
                if d < best:
                    best = d
                    idx = j
            out[k] = idx
            k += 1
    return out

# ---------------- filename parser ----------------
RE_NAME_A = re.compile(r"^(\d+)_(\d+)_(\d+)\.png$", re.IGNORECASE)
RE_NAME_B = re.compile(r"^(\d+)_(\d+)-(\d+)\.png$", re.IGNORECASE)

def parse_name(fname: str):
    m = RE_NAME_A.match(fname) or RE_NAME_B.match(fname)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))

# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser(
        description="Safe fast batch replace sprites in a D-3/Digivice/Tamagotchi Smart BIN using PNGs named INDEX_SUBIMAGE_BANK.png"
    )
    ap.add_argument("bin", help="Path to input BIN")
    ap.add_argument("--input-dir", required=True, help="Folder with PNGs named INDEX_SUBIMAGE_BANK.png")
    ap.add_argument("--out", required=True, help="Output BIN path; can be same as input")
    ap.add_argument("--alpha", choices=["auto", "normal", "inverted"], default="inverted", help="ARGB1555 alpha bit mode")
    ap.add_argument("--palette-step", choices=["colors", "4"], default="colors", help="Palette bank stepping")
    ap.add_argument("--use-attr-palette", action="store_true", help="Ignore filename bank and use each sprite's Attribute0 bank")
    ap.add_argument("--recursive", action="store_true", help="Recurse into subfolders")
    ap.add_argument("--package-offset", default=None, help="Override package base, e.g. 0x1EF000")
    ap.add_argument("--no-numpy", action="store_true", help="Disable NumPy path and use original Python quantizer")
    ap.add_argument("--dry-run", action="store_true", help="List planned changes only")
    args = ap.parse_args()

    use_numpy = (np is not None) and (not args.no_numpy)

    with open(args.bin, "rb") as f:
        data = bytearray(f.read())

    if args.package_offset is not None:
        s = args.package_offset.strip().lower()
        pkg_off = int(s, 16) if s.startswith("0x") else int(s)
        img_defs = le32(data, pkg_off + 0)
        spr_defs = le32(data, pkg_off + 4)
        palettes = le32(data, pkg_off + 8)
        chars    = le32(data, pkg_off + 12)
        if not (0 < img_defs < spr_defs < palettes < chars <= len(data) - pkg_off):
            raise RuntimeError(f"Invalid sprite package header at --package-offset {args.package_offset}")
        block = data[pkg_off: pkg_off + chars + 1_000_000]
        offs = (img_defs, spr_defs, palettes, chars)
    else:
        pkg_off, block, offs = robust_scan(data)

    images, sprites, palette_words, _chars_offset = parse_package(block, offs)
    bin_chars_base = pkg_off + offs[3]

    # Precompute subimage geometry and sprite lists once.
    subinfo: Dict[Tuple[int, int], dict] = {}
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
            min_x = min(s.ox for s in sprs)
            min_y = min(s.oy for s in sprs)
            max_x = max(s.ox + s.w for s in sprs)
            max_y = max(s.oy + s.h for s in sprs)
            subinfo[(img_idx, si)] = {
                "W": max_x - min_x,
                "H": max_y - min_y,
                "min_x": min_x,
                "min_y": min_y,
                "sprs": sprs,
            }

    # Collect PNG files.
    paths = []
    if args.recursive:
        for root, _dirs, files in os.walk(args.input_dir):
            for fn in files:
                if fn.lower().endswith(".png"):
                    paths.append(os.path.join(root, fn))
    else:
        for fn in os.listdir(args.input_dir):
            if fn.lower().endswith(".png"):
                paths.append(os.path.join(args.input_dir, fn))
    paths.sort()
    if not paths:
        print("No PNGs found.")
        return

    pal_normal, pal_inverted = build_palette_rgba_lists(palette_words)
    pal_cache = {}

    successes = 0
    failures = 0

    for path in paths:
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

        key = (image_index, subimage)
        if key not in subinfo:
            print(f"[err] {fname}: subimage {subimage} not found for image {image_index}")
            failures += 1
            continue

        info = subinfo[key]
        W = info["W"]
        H = info["H"]
        min_x = info["min_x"]
        min_y = info["min_y"]
        sprs = info["sprs"]
        idef = images[image_index]

        try:
            with Image.open(path) as im:
                png = im.convert("RGBA")
        except Exception as e:
            print(f"[err] {fname}: cannot open PNG ({e})")
            failures += 1
            continue

        if png.size != (W, H):
            print(f"[err] {fname}: PNG size {png.size} must match target composed size {(W, H)}")
            failures += 1
            continue

        first_bpp = sprs[0].bpp
        colors_first = 1 << first_bpp
        step_first = colors_first if args.palette_step == "colors" else 4
        base = idef.palette_start_index * 4
        pal_rgba = choose_palette_for_image(args.alpha, pal_normal, pal_inverted, base, file_bank, step_first, colors_first)

        if args.dry_run:
            print(f"[DRY] {fname}: will write image_index={image_index}, subimage={subimage}, bank={file_bank}, size={W}x{H}")
            successes += 1
            continue

        png_arr = np.asarray(png, dtype=np.uint8) if use_numpy else None

        for s in sprs:
            bank_idx = s.attr_bank if args.use_attr_palette else file_bank
            colors = 1 << s.bpp
            step_this = colors if args.palette_step == "colors" else 4
            pal_off = base + bank_idx * step_this

            if pal_off < 0 or pal_off + colors > len(pal_rgba):
                print(f"[err] {fname}: palette slice out of range: off={pal_off}, colors={colors}, palette_len={len(pal_rgba)}")
                failures += 1
                break

            pal_key = (id(pal_rgba), pal_off, colors, use_numpy)
            pal_slice = pal_cache.get(pal_key)
            if pal_slice is None:
                raw_slice = pal_rgba[pal_off:pal_off + colors]
                pal_slice = np.asarray(raw_slice, dtype=np.uint8) if use_numpy else raw_slice
                pal_cache[pal_key] = pal_slice

            dx = s.ox - min_x
            dy = s.oy - min_y

            if use_numpy:
                idxs = quantize_tile_numpy_safe(png_arr, dx, dy, s.w, s.h, pal_slice)
            else:
                idxs = quantize_tile_python(png, dx, dy, s.w, s.h, pal_slice)

            packed = pack_bits_msb_fast(idxs, s.bpp, expected_nbytes=s.nbytes)
            char_off = bin_chars_base + s.charnum * s.nbytes

            if char_off < 0 or char_off + s.nbytes > len(data):
                print(f"[err] {fname}: char write out of range: char_off=0x{char_off:X}, nbytes={s.nbytes}")
                failures += 1
                break

            data[char_off:char_off + s.nbytes] = packed
        else:
            print(f"[OK]  {fname}: replaced image {image_index}, subimage {subimage} using bank {file_bank}")
            successes += 1
            continue

        # Only reached if the sprite loop broke because of an error.
        continue

    if args.dry_run:
        print(f"[DRY] Completed: {successes} planned, {failures} skipped/failed")
    else:
        # Safe write: if output equals input, write to temp then replace.
        if os.path.abspath(args.out) == os.path.abspath(args.bin):
            tmp_out = args.out + ".tmp"
            with open(tmp_out, "wb") as f:
                f.write(data)
            os.replace(tmp_out, args.out)
        else:
            with open(args.out, "wb") as f:
                f.write(data)
        print(f"[DONE] Replaced {successes} file(s); {failures} skipped/failed. Wrote: {args.out}")

if __name__ == "__main__":
    main()
