#!/usr/bin/env python3
"""
Fast + safe D-3 / Digivice / Tamagotchi Smart sprite extractor.

Drop-in compatible with your existing digimon_tool_gui.py because it keeps:
    scan_for_package(data) -> (pkg_off, parsed)
    compose_subimage(...)

Speedups vs the old exporter:
- Optional package-offset shortcut for CLI.
- Faster package scan when no offset is provided.
- Sprite dimensions/bpp/palette bank are precomputed in SpriteDef.
- Palette RGBA conversion is cached instead of rebuilt for every image/bank.
- Character decode uses safe lookup tables for 2bpp/4bpp/8bpp and safe generic decode for 6bpp.
- Tile RGBA creation uses NumPy palette indexing when available, with a safe Python fallback.

Correctness/safety choices:
- Keeps the original MSB-first bit order.
- Keeps original sprite placement, alpha-composite, flip handling, bank stepping, and alpha heuristic.
- Does not approximate colors or resample images during export.
"""

import argparse
import io
import os
import random
import struct
import zipfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageChops, ImageStat

try:
    import numpy as np
except Exception:
    np = None

# ------------------ low-level utils ------------------

def le16(b, off): return struct.unpack_from("<H", b, off)[0]
def le32(b, off): return struct.unpack_from("<I", b, off)[0]
def se16(b, off): return struct.unpack_from("<h", b, off)[0]

def argb1555_normal(word):
    return (((word >> 10) & 0x1F) * 255 // 31,
            ((word >> 5)  & 0x1F) * 255 // 31,
            ( word        & 0x1F) * 255 // 31,
            255 if (word & 0x8000) else 0)

def argb1555_inverted(word):
    return (((word >> 10) & 0x1F) * 255 // 31,
            ((word >> 5)  & 0x1F) * 255 // 31,
            ( word        & 0x1F) * 255 // 31,
            0 if (word & 0x8000) else 255)

def bits_pp_from_sp_color(sp_color: int) -> int:
    # 0:2bpp, 1:4bpp, 2:6bpp, 3:8bpp
    return sp_color * 2 + 2

# ------------------ format structs ------------------

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
    sp_palette: int = 0
    sp_flip: int = 0

# ------------------ attr helpers ------------------

def unpack_attr(a: int):
    sp_color   =  a        & 0x3
    sp_flip    = (a >> 2)  & 0x3
    sp_hsize   = (a >> 4)  & 0x3
    sp_vsize   = (a >> 6)  & 0x3
    sp_palette = (a >> 8)  & 0xF
    sp_depth   = (a >> 12) & 0x3
    sp_blend   = (a >> 14) & 0x1
    quadruple  = (a >> 15) & 0x1
    return sp_color, sp_flip, sp_hsize, sp_vsize, sp_palette, sp_depth, sp_blend, quadruple

def sprite_dims(attr: int) -> Tuple[int, int, int]:
    sp_color, _sp_flip, sp_hsize, sp_vsize, *_ = unpack_attr(attr)
    return 8 << sp_hsize, 8 << sp_vsize, bits_pp_from_sp_color(sp_color)

def sprite_details(attr: int) -> Tuple[int, int, int, int, int]:
    sp_color, sp_flip, sp_hsize, sp_vsize, sp_palette, *_ = unpack_attr(attr)
    return 8 << sp_hsize, 8 << sp_vsize, bits_pp_from_sp_color(sp_color), sp_palette, sp_flip

# ------------------ package parsing ------------------

def parse_package(block: bytes) -> Tuple[int, int, int, int, List[ImageDef], List[SpriteDef], List[int]]:
    img_defs_offset = le32(block, 0)
    spr_defs_offset = le32(block, 4)
    palettes_offset = le32(block, 8)
    chars_offset    = le32(block, 12)

    assert 0 < img_defs_offset < spr_defs_offset < palettes_offset < chars_offset <= len(block), "Bad package offsets"

    img_len = spr_defs_offset - img_defs_offset
    spr_len = palettes_offset - spr_defs_offset
    pal_len = chars_offset - palettes_offset

    assert img_len % 6 == 0 and spr_len % 8 == 0 and pal_len % 2 == 0, "Bad section alignment"
    num_images  = img_len // 6
    num_sprites = spr_len // 8
    num_colors  = pal_len // 2

    images: List[ImageDef] = []
    for i in range(num_images):
        o = img_defs_offset + i * 6
        images.append(ImageDef(le16(block, o), block[o + 2], block[o + 3], le16(block, o + 4)))

    sprites: List[SpriteDef] = []
    for i in range(num_sprites):
        o = spr_defs_offset + i * 8
        attr = le16(block, o + 6)
        w, h, bpp, sp_palette, sp_flip = sprite_details(attr)
        sprites.append(SpriteDef(le16(block, o), se16(block, o + 2), se16(block, o + 4), attr, w, h, bpp, sp_palette, sp_flip))

    palette_words = [le16(block, palettes_offset + 2 * i) for i in range(num_colors)]
    return img_defs_offset, spr_defs_offset, palettes_offset, chars_offset, images, sprites, palette_words

def _validate_candidate(bin_data: bytes, off: int):
    size = len(bin_data)
    img_defs = le32(bin_data, off + 0)
    spr_defs = le32(bin_data, off + 4)
    palettes = le32(bin_data, off + 8)
    chars    = le32(bin_data, off + 12)
    if not (0 < img_defs < spr_defs < palettes < chars <= size - off):
        return None
    img_len = spr_defs - img_defs
    spr_len = palettes - spr_defs
    pal_len = chars - palettes
    if (img_len % 6) or (spr_len % 8) or (pal_len % 2):
        return None
    num_images  = img_len // 6
    num_sprites = spr_len // 8
    num_colors  = pal_len // 2
    if num_images < 500 or num_images > 10000 or num_colors < 64:
        return None
    block = bin_data[off: off + chars + 1_000_000]
    parsed = parse_package(block)
    if parsed[4] and parsed[4][-1].sprite_start_index < num_sprites:
        return parsed
    return None

def scan_for_package(bin_data: bytes) -> Tuple[int, Tuple]:
    """Compatible with old GUI: returns (package_offset, parsed_tuple)."""
    size = len(bin_data)

    # Known offsets first. This makes the GUI faster even if it does not pass --package-offset.
    for off in (0x1EF000, 0x196000):
        if 0 <= off < size - 16:
            try:
                parsed = _validate_candidate(bin_data, off)
                if parsed is not None:
                    return off, parsed
            except Exception:
                pass

    # General scan fallback.
    for off in range(0, size - 16, 4):
        try:
            parsed = _validate_candidate(bin_data, off)
            if parsed is not None:
                return off, parsed
        except Exception:
            continue
    raise RuntimeError("No sprites package found")

def parse_package_at(bin_data: bytes, package_offset: int) -> Tuple[int, Tuple, bytes]:
    parsed = _validate_candidate(bin_data, package_offset)
    if parsed is None:
        raise RuntimeError(f"No valid sprites package found at 0x{package_offset:X}")
    block = bin_data[package_offset:]
    return package_offset, parsed, block

# ------------------ decode caches ------------------

_DECODE_TABLES: Dict[int, List[Tuple[int, ...]]] = {}
_PALETTE_CACHE: Dict[Tuple[int, str], List[Tuple[int, int, int, int]]] = {}
_PALETTE_NP_CACHE: Dict[Tuple[int, str], object] = {}
_AUTO_MODE_CACHE: Dict[Tuple[int, int, int, int, str], str] = {}

# id reuse is theoretically possible across runs, but these are process-local short-lived caches.
def _palette_key(palette_words: List[int], mode: str):
    return (id(palette_words), mode)

def get_decode_table(bpp: int):
    table = _DECODE_TABLES.get(bpp)
    if table is not None:
        return table
    mask = (1 << bpp) - 1
    table = []
    for byte in range(256):
        vals = []
        bits = 8
        while bits >= bpp:
            bits -= bpp
            vals.append((byte >> bits) & mask)
        table.append(tuple(vals))
    _DECODE_TABLES[bpp] = table
    return table

def get_palette_rgba(palette_words: List[int], mode: str):
    key = _palette_key(palette_words, mode)
    pal = _PALETTE_CACHE.get(key)
    if pal is not None:
        return pal
    conv = argb1555_normal if mode == "normal" else argb1555_inverted
    pal = [conv(w) for w in palette_words]
    _PALETTE_CACHE[key] = pal
    return pal

def get_palette_np(palette_words: List[int], mode: str):
    if np is None:
        return None
    key = _palette_key(palette_words, mode)
    arr = _PALETTE_NP_CACHE.get(key)
    if arr is not None:
        return arr
    arr = np.asarray(get_palette_rgba(palette_words, mode), dtype=np.uint8)
    _PALETTE_NP_CACHE[key] = arr
    return arr

# ------------------ character decode ------------------

def decode_character(block: bytes, chars_offset: int, charnum: int, attr: int) -> Tuple[int, int, List[int]]:
    """Old-compatible decoder. Returns list[int] indexes."""
    w, h, bpp = sprite_dims(attr)
    return w, h, decode_character_values(block, chars_offset, charnum, w, h, bpp)

def decode_character_values(block: bytes, chars_offset: int, charnum: int, w: int, h: int, bpp: int):
    total = w * h
    nbytes = (total * bpp + 7) // 8
    off = chars_offset + charnum * nbytes
    buf = block[off: off + nbytes]

    if bpp in (2, 4, 8):
        if np is not None:
            raw = np.frombuffer(buf, dtype=np.uint8)
            if bpp == 8:
                vals = raw[:total]
            elif bpp == 4:
                vals = np.empty(raw.size * 2, dtype=np.uint8)
                vals[0::2] = raw >> 4
                vals[1::2] = raw & 0x0F
                vals = vals[:total]
            else:  # 2
                vals = np.empty(raw.size * 4, dtype=np.uint8)
                vals[0::4] = (raw >> 6) & 0x03
                vals[1::4] = (raw >> 4) & 0x03
                vals[2::4] = (raw >> 2) & 0x03
                vals[3::4] = raw & 0x03
                vals = vals[:total]
            if vals.size < total:
                vals = np.pad(vals, (0, total - vals.size), constant_values=0)
            return vals

        table = get_decode_table(bpp)
        vals = []
        extend = vals.extend
        for byte in buf:
            extend(table[byte])
        if len(vals) < total:
            vals.extend([0] * (total - len(vals)))
        return vals[:total]

    # Safe generic path for 6bpp or unusual bpp.
    vals: List[int] = []
    acc = 0
    accbits = 0
    mask = (1 << bpp) - 1
    for byte in buf:
        acc = (acc << 8) | byte
        accbits += 8
        while accbits >= bpp and len(vals) < total:
            shift = accbits - bpp
            vals.append((acc >> shift) & mask)
            acc &= (1 << shift) - 1
            accbits -= bpp
    if len(vals) < total:
        vals += [0] * (total - len(vals))
    return vals

# ------------------ compose ------------------

def _choose_alpha_mode(palette_words, idef, first_sprite, alpha_mode, palette_step_mode):
    if alpha_mode != "auto":
        return alpha_mode

    colors = 1 << first_sprite.bpp
    step = colors if palette_step_mode == "colors" else 4
    base = idef.palette_start_index * 4
    off0 = min(base, max(0, len(palette_words) - colors))
    key = (id(palette_words), base, colors, step, palette_step_mode)
    cached = _AUTO_MODE_CACHE.get(key)
    if cached is not None:
        return cached

    pal_norm = get_palette_rgba(palette_words, "normal")
    pal_inv  = get_palette_rgba(palette_words, "inverted")
    sum_norm = sum(p[3] for p in pal_norm[off0:off0 + colors])
    sum_inv  = sum(p[3] for p in pal_inv[off0:off0 + colors])
    mode = "inverted" if sum_inv > sum_norm else "normal"
    _AUTO_MODE_CACHE[key] = mode
    return mode

def _make_sprite_image(vals, pal_offset, colors, w, h, palette_rgba, palette_np):
    if np is not None and hasattr(vals, "shape") and palette_np is not None:
        # Clip is a safety guard against malformed data. Valid values should already be in range.
        idxs = np.asarray(vals, dtype=np.int64) + pal_offset
        max_idx = len(palette_np) - 1
        if idxs.size != w * h:
            idxs = np.resize(idxs, w * h)
        idxs = np.clip(idxs, 0, max_idx)
        arr = palette_np[idxs].reshape((h, w, 4))
        return Image.fromarray(arr, "RGBA")

    out = []
    append = out.append
    plen = len(palette_rgba)
    for v in vals:
        p = pal_offset + int(v)
        append(palette_rgba[p] if 0 <= p < plen else (0, 0, 0, 0))
    if len(out) < w * h:
        out.extend([(0, 0, 0, 0)] * (w * h - len(out)))
    img = Image.new("RGBA", (w, h))
    img.putdata(out[:w * h])
    return img

def compose_subimage(block: bytes,
                     images: List[ImageDef],
                     sprites: List[SpriteDef],
                     palette_words: List[int],
                     chars_offset: int,
                     image_index: int,
                     subimage_index: int,
                     bank: int,
                     alpha_mode: str = "auto",
                     palette_step_mode: str = "colors",
                     use_attr_palette: bool = False) -> Optional[Image.Image]:

    if not (0 <= image_index < len(images)):
        return None
    idef = images[image_index]
    sprites_per_sub = idef.width * idef.height
    if sprites_per_sub == 0:
        return None

    spr0 = idef.sprite_start_index + subimage_index * sprites_per_sub
    if spr0 >= len(sprites):
        return None
    sprs = sprites[spr0: spr0 + sprites_per_sub]
    if not sprs:
        return None

    min_x = min(s.ox for s in sprs)
    min_y = min(s.oy for s in sprs)
    max_x = max(s.ox + s.w for s in sprs)
    max_y = max(s.oy + s.h for s in sprs)
    W = max_x - min_x
    H = max_y - min_y
    if W <= 0 or H <= 0:
        return None

    pal_mode = _choose_alpha_mode(palette_words, idef, sprs[0], alpha_mode, palette_step_mode)
    palette_rgba = get_palette_rgba(palette_words, pal_mode)
    palette_np = get_palette_np(palette_words, pal_mode)
    base_index = idef.palette_start_index * 4

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    for s in sprs:
        vals = decode_character_values(block, chars_offset, s.charnum, s.w, s.h, s.bpp)
        colors = 1 << s.bpp
        step = colors if palette_step_mode == "colors" else 4
        bank_idx = s.sp_palette if use_attr_palette else bank
        pal_offset = base_index + bank_idx * step
        if pal_offset + colors > len(palette_rgba):
            pal_offset = base_index

        spr_img = _make_sprite_image(vals, pal_offset, colors, s.w, s.h, palette_rgba, palette_np)

        if s.sp_flip & 1:
            spr_img = spr_img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if s.sp_flip & 2:
            spr_img = spr_img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)

        img.alpha_composite(spr_img, (s.ox - min_x, s.oy - min_y))

    return img

# ------------------ helpers for CLI/export loop ------------------

def parse_banks(banks: str) -> List[int]:
    banks = banks.strip()
    if "-" in banks:
        a, b = banks.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in banks.split(",") if x.strip() != ""]

def export_range(bin_path: str,
                 out_dir: str,
                 start: int = 0,
                 end: Optional[int] = None,
                 banks: str = "0-15",
                 alpha: str = "auto",
                 palette_step: str = "colors",
                 use_attr_palette: bool = False,
                 package_offset: Optional[int] = None,
                 progress_cb=None):
    os.makedirs(out_dir, exist_ok=True)
    with open(bin_path, "rb") as f:
        data = f.read()

    if package_offset is None:
        pkg_off, parsed = scan_for_package(data)
        block = data[pkg_off:]
    else:
        pkg_off, parsed, block = parse_package_at(data, package_offset)

    _img_defs_offset, _spr_defs_offset, _palettes_offset, chars_offset, images, sprites, palette_words = parsed

    if end is None or end > len(images):
        end = len(images)
    start = max(0, min(start, len(images)))
    end = max(start, min(end, len(images)))
    bank_list = parse_banks(banks)

    jobs = []
    for i in range(start, end):
        idef = images[i]
        sprites_per_sub = idef.width * idef.height
        if sprites_per_sub == 0:
            continue
        if i + 1 < len(images):
            total_sprites_for_image = images[i + 1].sprite_start_index - idef.sprite_start_index
        else:
            total_sprites_for_image = len(sprites) - idef.sprite_start_index
        subimages = max(1, total_sprites_for_image // sprites_per_sub)
        for si in range(subimages):
            for bank in bank_list:
                jobs.append((i, si, bank))

    total = len(jobs)
    for n, (i, si, bank) in enumerate(jobs, start=1):
        img = compose_subimage(block, images, sprites, palette_words, chars_offset,
                               image_index=i, subimage_index=si, bank=bank,
                               alpha_mode=alpha, palette_step_mode=palette_step,
                               use_attr_palette=use_attr_palette)
        if img is None:
            continue
        out_name = f"{i}_{si}_{bank}.png"
        img.save(os.path.join(out_dir, out_name), optimize=False)
        if progress_cb:
            progress_cb(n / total if total else 1.0, f"Exported {out_name}")

    return pkg_off, len(jobs)

# ------------------ main ------------------

def main():
    ap = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("bin", help="Path to D-3/Digivice color .bin/full dump")
    ap.add_argument("--out", default="sprites_out", help="Output directory")
    ap.add_argument("--start", type=int, default=0, help="Start image index inclusive")
    ap.add_argument("--end", type=int, default=None, help="End image index exclusive")
    ap.add_argument("--banks", default="0-15", help="Banks to export, e.g. 0-15 or 0,1,2")
    ap.add_argument("--alpha", choices=["auto", "normal", "inverted"], default="auto", help="ARGB1555 alpha mode")
    ap.add_argument("--palette-step", choices=["colors", "4"], default="colors", help="Palette step per bank")
    ap.add_argument("--use-attr-palette", action="store_true", help="Use Attribute0 sp_palette instead of uniform bank override")
    ap.add_argument("--package-offset", default=None, help="Optional package base, e.g. 0x1EF000 or 0x196000")
    ap.add_argument("--validate-zip", default=None, help="Optional reference sprites zip to sample-compare")
    ap.add_argument("--sample", type=int, default=50, help="Validation sample size")
    args = ap.parse_args()

    package_offset = None
    if args.package_offset:
        s = args.package_offset.strip().lower()
        package_offset = int(s, 16) if s.startswith("0x") else int(s)

    pkg_off, count = export_range(
        bin_path=args.bin,
        out_dir=args.out,
        start=args.start,
        end=args.end,
        banks=args.banks,
        alpha=args.alpha,
        palette_step=args.palette_step,
        use_attr_palette=args.use_attr_palette,
        package_offset=package_offset,
    )
    print(f"[DONE] Exported {count} sprite image(s). Package offset: 0x{pkg_off:X}. Output: {args.out}")

    if args.validate_zip:
        z = zipfile.ZipFile(args.validate_zip, "r")
        names = [n for n in z.namelist() if n.lower().endswith(".png")]
        if not names:
            print("No PNGs found in reference zip")
            return
        sample = random.sample(names, min(args.sample, len(names)))
        diffs = []
        for name in sample:
            try:
                base = os.path.basename(name)
                parts = os.path.splitext(base)[0].split("_")
                ii, si, bank = map(int, parts)
            except Exception:
                continue
            my_path = os.path.join(args.out, f"{ii}_{si}_{bank}.png")
            if not os.path.exists(my_path):
                continue
            our = Image.open(my_path).convert("RGBA")
            ref = Image.open(io.BytesIO(z.read(name))).convert("RGBA")
            if our.size != ref.size:
                continue
            diff = ImageChops.difference(our, ref)
            stat = ImageStat.Stat(diff)
            diffs.append(sum(stat.mean[:3]) / 3.0)
        if diffs:
            print(f"Validation sample {len(diffs)} images; average RGB diff = {sum(diffs)/len(diffs):.2f}")
        else:
            print("No comparable images found for validation")

if __name__ == "__main__":
    main()
