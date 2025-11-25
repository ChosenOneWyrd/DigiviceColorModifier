
#!/usr/bin/env python3
"""
D-3 25th Color / Tamagotchi Smart sprite extractor

This script locates the sprites package inside a GeneralPlus SPI flash image
(GP-SPIF-HEADER-- ...), parses the package (image defs, sprite defs, palettes, characters),
and exports composed subimages as PNGs, using the naming convention:
    <image_index>_<subimage_index>_<bank>.png

It follows the "common sprites package" format as described by community notes:
- 4 LE 32-bit offsets at the start of the package (relative to package base):
    img_defs_offset, sprite_defs_offset, palettes_offset, chars_offset
- image_def (6 bytes): u16 sprite_start_index, u8 width, u8 height, u16 palette_start_index
- sprite_def (8 bytes): u16 charnum, s16 offset_x, s16 offset_y, u16 attribute0
- ARGB1555 palettes (little-endian words)
- Character data is a packed stream of palette indexes, MSB-first within bytes

Heuristics / options:
- Some assets appear to use "normal" ARGB1555 alpha (1=opaque), others use the "inverted"
  variant (0=opaque). Use --alpha auto (default) to pick per-image based on a simple heuristic.
- The third number in the filename corresponds to a palette "bank" index (0..15). The engine's
  Attribute0.sp_palette is 4 bits; many packs are authored to support 16 banks. By default,
  we export banks 0..15 for each subimage (use --banks to limit).
- Palette stepping: we assume each bank adds (colors_per_pixel) to the palette index (i.e., 4/16/64).
  You can switch to --palette-step 4 to advance by 4 colors per bank if you find a pack that expects it.

Optional validation:
    --validate-zip path.zip    # If you have a reference zip, we compare a random sample.

Dependencies: Pillow
"""

import argparse, os, struct, random, io, zipfile
from dataclasses import dataclass
from typing import List, Tuple, Optional
from PIL import Image, ImageChops, ImageStat

# ------------------ low-level utils ------------------

def le16(b, off): return struct.unpack_from("<H", b, off)[0]
def le32(b, off): return struct.unpack_from("<I", b, off)[0]
def se16(b, off): return struct.unpack_from("<h", b, off)[0]

def align(n, a): return (n + (a-1)) & ~(a-1)

def argb1555_normal(word):
    """ARGB1555, 1=opaque"""
    a = 255 if (word & 0x8000) else 0
    r = ((word >> 10) & 0x1F) * 255 // 31
    g = ((word >> 5)  & 0x1F) * 255 // 31
    b = ( word        & 0x1F) * 255 // 31
    return (r,g,b,a)

def argb1555_inverted(word):
    """ARGB1555, inverted alpha (0=opaque, 1=transparent)"""
    a = 0 if (word & 0x8000) else 255
    r = ((word >> 10) & 0x1F) * 255 // 31
    g = ((word >> 5)  & 0x1F) * 255 // 31
    b = ( word        & 0x1F) * 255 // 31
    return (r,g,b,a)

def bits_pp_from_sp_color(sp_color: int) -> int:
    # 0:2bpp, 1:4bpp, 2:6bpp, 3:8bpp
    return sp_color*2 + 2

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

def parse_package(block: bytes) -> Tuple[int,int,int,int,List[ImageDef],List[SpriteDef],List[int]]:
    img_defs_offset = le32(block, 0)
    spr_defs_offset = le32(block, 4)
    palettes_offset = le32(block, 8)
    chars_offset    = le32(block, 12)

    # basic checks
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
        o = img_defs_offset + i*6
        images.append(ImageDef(
            sprite_start_index = le16(block, o),
            width              = block[o+2],
            height             = block[o+3],
            palette_start_index= le16(block, o+4),
        ))

    sprites: List[SpriteDef] = []
    for i in range(num_sprites):
        o = spr_defs_offset + i*8
        sprites.append(SpriteDef(
            charnum = le16(block, o),
            ox      = se16(block, o+2),
            oy      = se16(block, o+4),
            attr    = le16(block, o+6),
        ))

    palette_words = [le16(block, palettes_offset + 2*i) for i in range(num_colors)]
    return img_defs_offset, spr_defs_offset, palettes_offset, chars_offset, images, sprites, palette_words

def scan_for_package(bin_data: bytes) -> Tuple[int, Tuple]:
    """
    Scan the full .bin for a sprites package using heuristics.
    Returns (package_offset, parsed_tuple_from_parse_package)
    """
    size = len(bin_data)
    for off in range(0, size-16, 4):
        try:
            # candidate offsets (relative to off)
            img_defs = le32(bin_data, off+0)
            spr_defs = le32(bin_data, off+4)
            palettes = le32(bin_data, off+8)
            chars    = le32(bin_data, off+12)
            if not (0 < img_defs < spr_defs < palettes < chars <= size - off):
                continue
            # quickly validate alignment & counts
            img_len = spr_defs - img_defs
            spr_len = palettes - spr_defs
            pal_len = chars - palettes
            if (img_len % 6) or (spr_len % 8) or (pal_len % 2):
                continue
            num_images  = img_len // 6
            num_sprites = spr_len // 8
            num_colors  = pal_len // 2
            if num_images < 500 or num_images > 10000:
                continue
            if num_colors < 64:
                continue
            # parse more thoroughly from this base
            block = bin_data[off: off + chars + 1_000_000]  # include trailing char data headroom
            parsed = parse_package(block)
            # additional sanity: sprite_start_index of last image < num_sprites
            if parsed[4][-1].sprite_start_index < num_sprites:
                return off, parsed
        except Exception:
            continue
    raise RuntimeError("No sprites package found")

# attribute bits
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

def sprite_dims(attr: int) -> Tuple[int,int,int]:
    sp_color, sp_flip, sp_hsize, sp_vsize, *_ = unpack_attr(attr)
    w = 8 << sp_hsize
    h = 8 << sp_vsize
    bpp = bits_pp_from_sp_color(sp_color)
    return w, h, bpp

# bit unpack, MSB-first within bytes (as per docs)
def decode_character(block: bytes, chars_offset: int, charnum: int, attr: int) -> Tuple[int,int,List[int]]:
    w, h, bpp = sprite_dims(attr)
    total_bits = w*h*bpp
    nbytes = (total_bits + 7)//8
    # Character data is tightly packed; no per-character alignment in this format
    off = chars_offset + charnum * nbytes
    buf = block[off: off+nbytes]
    vals: List[int] = []
    acc = 0
    accbits = 0
    for byte in buf:
        acc = (acc << 8) | byte
        accbits += 8
        while accbits >= bpp and len(vals) < (w*h):
            shift = accbits - bpp
            vals.append((acc >> shift) & ((1<<bpp)-1))
            acc &= (1<<shift)-1
            accbits -= bpp
    if len(vals) < w*h:
        vals += [0]*(w*h - len(vals))
    return w, h, vals

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

    idef = images[image_index]
    sprites_per_sub = idef.width * idef.height
    spr0 = idef.sprite_start_index + subimage_index * sprites_per_sub
    if spr0 >= len(sprites):
        return None
    sprs = sprites[spr0: spr0 + sprites_per_sub]
    # Decode bounds first
    rects = []
    decoded = []
    for s in sprs:
        w, h, _ = sprite_dims(s.attr)
        decoded.append((s, *decode_character(block, chars_offset, s.charnum, s.attr)))
        rects.append((s.ox, s.oy, s.ox + w, s.oy + h))
    if not rects:
        return None
    min_x = min(r[0] for r in rects); min_y = min(r[1] for r in rects)
    max_x = max(r[2] for r in rects); max_y = max(r[3] for r in rects)
    W = max_x - min_x; H = max_y - min_y
    if W <= 0 or H <= 0:
        return None

    # Alpha palette selection
    def build_palette(mode: str):
        if mode == "normal":
            return [argb1555_normal(w) for w in palette_words]
        else:
            return [argb1555_inverted(w) for w in palette_words]

    # auto: choose per-image by checking opacity sum for bank 0
    pal_mode = alpha_mode
    if alpha_mode == "auto":
        # Heuristic: prefer inverted if normal produces fully transparent colors for a typical bank set
        base = idef.palette_start_index * 4
        # test both
        pal_norm = [argb1555_normal(w) for w in palette_words]
        pal_inv  = [argb1555_inverted(w) for w in palette_words]
        # sample the first sprite's palette set
        sp_color, *_ = unpack_attr(sprs[0].attr)
        colors = 1 << bits_pp_from_sp_color(sp_color)
        step = (colors if palette_step_mode=="colors" else 4)
        off0 = base + 0*step
        # clip
        off0 = min(off0, max(0, len(palette_words) - colors))
        sum_norm = sum(p[3] for p in pal_norm[off0:off0+colors])
        sum_inv  = sum(p[3] for p in pal_inv [off0:off0+colors])
        pal_mode = "inverted" if sum_inv > sum_norm else "normal"

    palette_rgba = build_palette(pal_mode)

    img = Image.new("RGBA", (W, H), (0,0,0,0))
    base_index = idef.palette_start_index * 4
    for (s, w, h, vals) in decoded:
        sp_color, sp_flip, sp_hsize, sp_vsize, sp_palette, *_ = unpack_attr(s.attr)
        bpp = bits_pp_from_sp_color(sp_color)
        colors = 1 << bpp
        step = colors if palette_step_mode=="colors" else 4
        bank_idx = (sp_palette if use_attr_palette else bank)
        pal_offset = base_index + bank_idx * step
        # clamp
        if pal_offset + colors > len(palette_rgba):
            pal_offset = base_index
        spr_img = Image.new("RGBA", (w, h))
        px = spr_img.load()
        idx = 0
        for y in range(h):
            for x in range(w):
                val = vals[idx]; idx += 1
                px[x, y] = palette_rgba[pal_offset + val] if (pal_offset + val) < len(palette_rgba) else (0,0,0,0)
        # flips
        if (sp_flip & 1): spr_img = spr_img.transpose(Image.FLIP_LEFT_RIGHT)
        if (sp_flip & 2): spr_img = spr_img.transpose(Image.FLIP_TOP_BOTTOM)

        img.alpha_composite(spr_img, (s.ox - min_x, s.oy - min_y))

    return img

# ------------------ main ------------------

def main():
    ap = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("bin", help="Path to D-3 color .bin (full dump)")
    ap.add_argument("--out", default="sprites_out", help="Output directory")
    ap.add_argument("--start", type=int, default=0, help="Start image index (inclusive)")
    ap.add_argument("--end", type=int, default=None, help="End image index (exclusive). Default = all")
    ap.add_argument("--banks", default="0-15", help="Banks to export, e.g., '0-15' or '0,1,2,3'")
    ap.add_argument("--alpha", choices=["auto","normal","inverted"], default="auto", help="ARGB1555 alpha mode")
    ap.add_argument("--palette-step", choices=["colors","4"], default="colors", help="Palette step per bank")
    ap.add_argument("--use-attr-palette", action="store_true", help="Use per-sprite Attribute0 sp_palette instead of a uniform bank override")
    ap.add_argument("--validate-zip", default=None, help="Optional: reference sprites zip to sample-compare")
    ap.add_argument("--sample", type=int, default=50, help="Validation sample size if --validate-zip is set")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    with open(args.bin, "rb") as f:
        data = f.read()

    # Find the sprites package
    pkg_off, parsed = scan_for_package(data)
    img_defs_offset, spr_defs_offset, palettes_offset, chars_offset, images, sprites, palette_words = parsed
    # Slice a working view of the file starting from package
    block = data[pkg_off:]

    # Export range
    end = args.end if args.end is not None else len(images)
    end = min(end, len(images))
    bank_list: List[int] = []
    if "-" in args.banks:
        a,b = args.banks.split("-")
        bank_list = list(range(int(a), int(b)+1))
    else:
        bank_list = [int(x) for x in args.banks.split(",") if x.strip()!=""]

    # Export
    for i in range(args.start, end):
        idef = images[i]
        sprites_per_sub = idef.width * idef.height
        # infer number of subimages by looking at the sprite index of the next image
        if i+1 < len(images):
            total_sprites_for_image = images[i+1].sprite_start_index - idef.sprite_start_index
        else:
            total_sprites_for_image = len(sprites) - idef.sprite_start_index
        if sprites_per_sub == 0:
            continue
        subimages = max(1, total_sprites_for_image // sprites_per_sub)
        for si in range(subimages):
            for bank in bank_list:
                img = compose_subimage(block, images, sprites, palette_words, chars_offset,
                                       image_index=i, subimage_index=si, bank=bank,
                                       alpha_mode=args.alpha, palette_step_mode=args.palette_step,
                                       use_attr_palette=args.use_attr_palette)
                if img is None:
                    continue
                out_name = f"{i}_{si}_{bank}.png"
                out_path = os.path.join(args.out, out_name)
                img.save(out_path)

    # Optional validation
    if args.validate_zip:
        z = zipfile.ZipFile(args.validate_zip, "r")
        names = [n for n in z.namelist() if n.endswith(".png")]
        if not names:
            print("No PNGs found in reference zip")
            return
        sample = random.sample(names, min(args.sample, len(names)))
        diffs = []
        for name in sample:
            # parse indices
            try:
                base = os.path.basename(name)
                parts = os.path.splitext(base)[0].split("_")
                ii, si, bank = map(int, parts)
            except Exception:
                continue
            # load ours
            my_path = os.path.join(args.out, f"{ii}_{si}_{bank}.png")
            if not os.path.exists(my_path):
                continue
            our = Image.open(my_path).convert("RGBA")
            ref = Image.open(io.BytesIO(z.read(name))).convert("RGBA")
            if our.size != ref.size:
                continue
            diff = ImageChops.difference(our, ref)
            stat = ImageStat.Stat(diff)
            diffs.append(sum(stat.mean[:3])/3.0)
        if diffs:
            print(f"Validation sample {len(diffs)} images; average RGB diff = {sum(diffs)/len(diffs):.2f}")
        else:
            print("No comparable images found for validation")

if __name__ == "__main__":
    main()
