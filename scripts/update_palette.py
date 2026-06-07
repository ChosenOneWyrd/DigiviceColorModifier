#!/usr/bin/env python3
"""
Fast batch palette updater for D-3 25th Color / Digivice 25th Color / Tamagotchi Smart style BINs.

Compatible with digimon_tool_gui.py:
  - keeps FNAME_RE
  - keeps robust_scan(data)
  - keeps parse(block, offs)
  - keeps update_one(...)

Speedups:
  1. robust_scan() first checks the known D-3 and Digivice sprite package offsets.
  2. PNGs that already contain <= required colors skip expensive quantize().
  3. Reuses/caches subimage metadata and auto-bank usage.
  4. Uses getcolors() instead of converting the whole image to a Python list first.
"""

import argparse
import os
import re
import struct
from dataclasses import dataclass
from typing import Dict, List, Tuple, Set, Optional
from PIL import Image

# ---------- low-level helpers ----------
def le16(b, o): return struct.unpack_from("<H", b, o)[0]
def le32(b, o): return struct.unpack_from("<I", b, o)[0]
def se16(b, o): return struct.unpack_from("<h", b, o)[0]

def argb_to_1555(r, g, b, a, inverted=True):
    r5 = max(0, min(31, (int(r) * 31 + 127) // 255))
    g5 = max(0, min(31, (int(g) * 31 + 127) // 255))
    b5 = max(0, min(31, (int(b) * 31 + 127) // 255))

    if inverted:
        # Smart/D-3/Digivice style: 0 = opaque, 1 = transparent
        a_bit = 0 if int(a) >= 128 else 1
    else:
        # Normal ARGB1555 style: 1 = opaque, 0 = transparent
        a_bit = 1 if int(a) >= 128 else 0

    return (a_bit << 15) | (r5 << 10) | (g5 << 5) | b5

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
    w: int = 0
    h: int = 0
    bpp: int = 0
    attr_bank: int = 0

def unpack_attr(a):
    sp_color   =  a        & 0x3
    sp_flip    = (a >> 2)  & 0x3
    sp_hsize   = (a >> 4)  & 0x3
    sp_vsize   = (a >> 6)  & 0x3
    sp_palette = (a >> 8)  & 0xF
    return sp_color, sp_flip, sp_hsize, sp_vsize, sp_palette

def bits_pp(sp_color):
    return sp_color * 2 + 2

def sprite_dims_and_bank(attr):
    sp_color, _sp_flip, sp_hsize, sp_vsize, sp_palette = unpack_attr(attr)
    return (8 << sp_hsize), (8 << sp_vsize), bits_pp(sp_color), sp_palette

def sprite_dims(attr):
    w, h, bpp, _bank = sprite_dims_and_bank(attr)
    return w, h, bpp

# ---------- package scan / parse ----------
def _validate_package_at(data: bytes, off: int):
    size = len(data)
    if off < 0 or off + 16 > size:
        return None

    img_defs = le32(data, off + 0)
    spr_defs = le32(data, off + 4)
    palettes = le32(data, off + 8)
    chars    = le32(data, off + 12)

    if not (0 < img_defs < spr_defs < palettes < chars <= size - off):
        return None

    img_len = spr_defs - img_defs
    spr_len = palettes - spr_defs
    pal_len = chars - palettes

    if (img_len % 6) or (spr_len % 8) or (pal_len % 2):
        return None

    num_images = img_len // 6
    num_sprites = spr_len // 8

    if not (1000 <= num_images <= 5000):
        return None

    block = data[off: off + chars + 1_000_000]

    if len(block) < chars:
        return None

    try:
        last_img_spr = le16(block, img_defs + (num_images - 1) * 6)
    except Exception:
        return None

    if last_img_spr >= num_sprites:
        return None

    return block, (img_defs, spr_defs, palettes, chars)

def load_package_at_offset(data: bytes, package_offset):
    """Load package directly from known offset. package_offset may be int, decimal str, or hex str."""
    if isinstance(package_offset, str):
        s = package_offset.strip().lower()
        off = int(s, 16) if s.startswith("0x") else int(s)
    else:
        off = int(package_offset)

    result = _validate_package_at(data, off)
    if result is None:
        raise RuntimeError(f"No valid sprites package found at offset 0x{off:X}")

    block, offs = result
    return off, block, offs

def robust_scan(data: bytes):
    """
    Fast-compatible robust scan.

    This keeps the old function name used by digimon_tool_gui.py, but first tries
    the known package offsets:
      D-3       0x1EF000
      Digivice  0x196000
    If neither validates, it falls back to the old full scan.
    """
    for known_off in (0x1EF000, 0x196000):
        result = _validate_package_at(data, known_off)
        if result is not None:
            block, offs = result
            return known_off, block, offs

    size = len(data)
    best = None

    for off in range(0, size - 16, 4):
        result = _validate_package_at(data, off)
        if result is None:
            continue
        block, offs = result
        _img_defs, _spr_defs, _palettes, chars = offs
        score = chars
        if best is None or score > best[0]:
            best = (score, off, block, offs)

    if not best:
        raise RuntimeError("No sprites package found")

    _score, pkg_off, block, offs = best
    return pkg_off, block, offs

def parse(block: bytes, offs):
    img_defs, spr_defs, palettes, _chars = offs
    num_images  = (spr_defs - img_defs) // 6
    num_sprites = (palettes - spr_defs) // 8

    images = []
    for i in range(num_images):
        o = img_defs + i * 6
        images.append(ImageDef(
            le16(block, o),
            block[o + 2],
            block[o + 3],
            le16(block, o + 4),
        ))

    sprites = []
    for i in range(num_sprites):
        o = spr_defs + i * 8
        attr = le16(block, o + 6)
        w, h, bpp, bank = sprite_dims_and_bank(attr)
        sprites.append(SpriteDef(
            le16(block, o),
            se16(block, o + 2),
            se16(block, o + 4),
            attr,
            w,
            h,
            bpp,
            bank,
        ))

    return images, sprites, palettes

# ---------- cached metadata ----------
_SUBINFO_CACHE: Dict[Tuple[int, int, int, int], Dict[Tuple[int, int], dict]] = {}
_BANK_USAGE_CACHE: Dict[Tuple[int, int, int], Set[int]] = {}

def _subinfo_cache_key(images, sprites, offs):
    # Good enough for one run inside the GUI worker.
    return (id(images), id(sprites), int(offs[1]), len(images))

def get_subinfo(images, sprites, offs):
    key = _subinfo_cache_key(images, sprites, offs)
    cached = _SUBINFO_CACHE.get(key)
    if cached is not None:
        return cached

    subinfo = {}

    for img_idx, idef in enumerate(images):
        spp = idef.width * idef.height
        if spp == 0:
            continue

        if img_idx + 1 < len(images):
            total_for_img = images[img_idx + 1].sprite_start_index - idef.sprite_start_index
        else:
            total_for_img = len(sprites) - idef.sprite_start_index

        subimages = max(1, total_for_img // max(1, spp))

        for si in range(subimages):
            spr0 = idef.sprite_start_index + si * spp
            first = sprites[spr0]
            subinfo[(img_idx, si)] = {
                "spr0": spr0,
                "spp": spp,
                "bpp": first.bpp,
                "colors": 1 << first.bpp,
            }

    _SUBINFO_CACHE[key] = subinfo
    return subinfo

# ---------- palette helpers ----------
def collect_bank_usage(images, sprites, target_pal_start):
    """Return set of banks (0..15) referenced by any image sharing palette_start_index."""
    cache_key = (id(images), id(sprites), int(target_pal_start))
    cached = _BANK_USAGE_CACHE.get(cache_key)
    if cached is not None:
        return set(cached)

    used: Set[int] = set()

    for img_idx, idef in enumerate(images):
        if idef.palette_start_index != target_pal_start:
            continue

        spp = idef.width * idef.height
        if spp == 0:
            continue

        spr0 = idef.sprite_start_index
        if img_idx + 1 < len(images):
            total_for_img = images[img_idx + 1].sprite_start_index - spr0
        else:
            total_for_img = len(sprites) - spr0

        subimages = max(1, total_for_img // max(1, spp))

        for si in range(subimages):
            s0 = spr0 + si * spp
            for s in sprites[s0:s0 + spp]:
                used.add(s.attr_bank if hasattr(s, "attr_bank") else ((s.attr >> 8) & 0xF))
                if len(used) >= 16:
                    _BANK_USAGE_CACHE[cache_key] = set(range(16))
                    return set(range(16))

    _BANK_USAGE_CACHE[cache_key] = set(used)
    return used

def _rgba_key_from_color(rgba):
    r, g, b, a = rgba
    return (int(r), int(g), int(b), 255 if int(a) >= 128 else 0)

def _colors_from_getcolors(img: Image.Image, max_colors: int):
    """
    Fast path: returns ordered unique RGBA colors if img already has <= max_colors colors.
    Returns None if more than max_colors.
    """
    colors = img.getcolors(maxcolors=max_colors + 1)
    if colors is None or len(colors) > max_colors:
        return None

    unique = []
    seen = set()

    # getcolors order is not guaranteed to match pixel scan order, so scan only
    # in the cheap <= max_colors case to preserve first-appearance-ish behavior.
    for rgba in img.getdata():
        key = _rgba_key_from_color(rgba)
        if key not in seen:
            seen.add(key)
            unique.append(key)
            if len(unique) >= max_colors:
                break

    return unique

def build_palette_words_from_png(png_path: str, max_colors: int, inverted_alpha: bool):
    """
    Returns (words, png_size).

    Behavior matches the previous script's intent:
      - use PNG colors if already within max_colors
      - otherwise quantize RGB down to max_colors and restore original alpha
      - alpha is thresholded to 0/255 before ARGB1555 conversion
    """
    with Image.open(png_path) as im:
        raw = im.convert("RGBA")

    png_size = raw.size

    unique = _colors_from_getcolors(raw, max_colors)

    if unique is None:
        # Slow path only when actually needed.
        alpha = raw.getchannel("A")
        reduced = raw.convert("RGB").quantize(
            colors=max_colors,
            method=Image.MEDIANCUT,
        ).convert("RGBA")
        reduced.putalpha(alpha)

        unique = []
        seen = set()
        for rgba in reduced.getdata():
            key = _rgba_key_from_color(rgba)
            if key not in seen:
                seen.add(key)
                unique.append(key)
                if len(unique) >= max_colors:
                    break

    if not unique:
        unique = [(0, 0, 0, 0)]

    while len(unique) < max_colors:
        unique.append(unique[-1])

    words = [argb_to_1555(r, g, b, a, inverted=inverted_alpha) for (r, g, b, a) in unique[:max_colors]]
    return words, png_size

# Old public function name kept for compatibility with any direct usage.
def build_palette_from_png(png_path: str, max_colors: int, inverted_alpha: bool):
    return build_palette_words_from_png(png_path, max_colors, inverted_alpha)

def update_one(data, pkg_off, offs, images, sprites, image_index, subimage, png_path, target_bank, alpha_mode, set_sprite_bank, dry_run):
    img_defs_off, spr_defs_off, palettes_off, chars_off = offs

    if not (0 <= image_index < len(images)):
        raise SystemExit(f"image-index {image_index} out of range 0..{len(images)-1}")

    subinfo = get_subinfo(images, sprites, offs)
    key = (image_index, subimage)

    if key not in subinfo:
        # Build a useful old-style error message.
        idef = images[image_index]
        spp = idef.width * idef.height
        if spp == 0:
            raise SystemExit(f"image {image_index}: zero sprites per subimage")
        if image_index + 1 < len(images):
            total_for_img = images[image_index + 1].sprite_start_index - idef.sprite_start_index
        else:
            total_for_img = len(sprites) - idef.sprite_start_index
        subimages = max(1, total_for_img // max(1, spp))
        raise SystemExit(f"image {image_index}: subimage {subimage} out of range 0..{subimages-1}")

    idef = images[image_index]
    info = subinfo[key]
    spr0 = info["spr0"]
    spp = info["spp"]
    colors = info["colors"]
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
    words, png_size = build_palette_words_from_png(png_path, colors, inverted)

    bank_off = base_index + bank * step
    pal_bytes_off = pkg_off + palettes_off + bank_off * 2

    if dry_run:
        print(f"[DRY] img {image_index} si {subimage}: PNG {png_size}, "
              f"colors={colors}, write bank={bank} @ 0x{pal_bytes_off:X}")
        if set_sprite_bank:
            print(f"[DRY]    set sp_palette={bank} for {spp} sprite(s) in that subimage")
        return

    # Write palette words faster than looping with struct.pack each time.
    pal_bytes = struct.pack("<" + "H" * len(words), *words)
    data[pal_bytes_off:pal_bytes_off + len(pal_bytes)] = pal_bytes

    # Optionally set per-sprite bank nibble.
    if set_sprite_bank:
        bank_bits = (bank & 0xF) << 8
        for idx in range(spr0, spr0 + spp):
            s_off = pkg_off + spr_defs_off + idx * 8 + 6
            attr = le16(data, s_off)
            attr = (attr & ~(0xF << 8)) | bank_bits
            data[s_off:s_off + 2] = struct.pack("<H", attr)

            # Keep parsed sprite objects in sync for later updates in same GUI run.
            try:
                sprites[idx].attr = attr
                sprites[idx].attr_bank = bank
            except Exception:
                pass

    print(f"[OK] img {image_index} si {subimage}: wrote {colors} colors to bank {bank}")

# ---------- batch driver ----------
FNAME_RE = re.compile(r"^(\d+)_(\d+)_(\d+)\.(?:png|PNG)$")

def collect_jobs(input_dir: str):
    jobs: List[Tuple[int, int, int, str]] = []
    for root, _dirs, files in os.walk(input_dir):
        for fn in files:
            m = FNAME_RE.match(fn)
            if not m:
                continue
            idx = int(m.group(1))
            sub = int(m.group(2))
            bank = int(m.group(3))
            jobs.append((idx, sub, bank, os.path.join(root, fn)))

    jobs.sort(key=lambda t: (t[0], t[1], t[2], t[3]))
    return jobs

def main():
    ap = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("bin", help="Path to BIN")
    ap.add_argument("--input-dir", required=True, help="Folder with PNGs named INDEX_SUBIMAGE_BANK.png")
    ap.add_argument("--out", required=True, help="Output BIN path")
    ap.add_argument("--alpha-mode", choices=["inverted", "normal"], default="inverted",
                    help="ARGB1555 alpha bit meaning; inverted=Smart default (0=opaque,1=transparent)")
    ap.add_argument("--set-sprite-bank", action="store_true",
                    help="Force sprites in that subimage to use BANK parsed from filename")
    ap.add_argument("--package-offset", default=None,
                    help="Override package base, e.g. 0x1EF000 for D-3 or 0x196000 for Digivice")
    ap.add_argument("--dry-run", action="store_true", help="Do not write; just print actions")
    args = ap.parse_args()

    jobs = collect_jobs(args.input_dir)
    if not jobs:
        raise SystemExit(f"No files matching INDEX_SUBIMAGE_BANK.png found in {args.input_dir}")

    with open(args.bin, "rb") as f:
        data = bytearray(f.read())

    if args.package_offset is not None:
        pkg_off, block, offs = load_package_at_offset(data, args.package_offset)
    else:
        pkg_off, block, offs = robust_scan(data)

    images, sprites, _palettes_off = parse(block, offs)

    for idx, sub, bank, png_path in jobs:
        update_one(
            data,
            pkg_off,
            offs,
            images,
            sprites,
            idx,
            sub,
            png_path,
            bank,
            args.alpha_mode,
            args.set_sprite_bank,
            args.dry_run,
        )

    if args.dry_run:
        print(f"[DRY] Processed {len(jobs)} file(s). No output written.")
    else:
        with open(args.out, "wb") as f:
            f.write(data)
        print(f"[DONE] Updated {len(jobs)} palette bank(s). Wrote: {args.out}")

if __name__ == "__main__":
    main()
