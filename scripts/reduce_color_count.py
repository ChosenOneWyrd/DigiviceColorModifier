#!/usr/bin/env python3
# Usage: python reduce_color_count.py image.png -c 63
from pathlib import Path
from PIL import Image
import imagequant
import argparse

def reduce_colors(input_png, output_png, colors):
    img = Image.open(input_png).convert("RGBA")

    w, h = img.size
    rgba = img.tobytes()

    result = imagequant.quantize_raw_rgba_bytes(
        rgba,
        w,
        h,
        dithering_level=0.0,
        max_colors=colors,
    )

    index_bytes = result[0]
    palette = result[1]

    if len(palette) > 0 and isinstance(palette[0], int):
        flat = list(palette)

        if len(flat) % 4 == 0 and len(flat) // 4 <= 256:
            flat_palette = []
            for i in range(0, len(flat), 4):
                flat_palette.extend(flat[i:i+3])
        else:
            flat_palette = flat
    else:
        flat_palette = []
        for color in palette:
            flat_palette.extend([color[0], color[1], color[2]])

    flat_palette = flat_palette[:768]
    flat_palette += [0] * (768 - len(flat_palette))

    out = Image.frombytes("P", (w, h), index_bytes)
    out.putpalette(flat_palette)

    # Convert back to RGBA and restore the original alpha channel exactly.
    # This fixes transparent pixels turning into green/opaque pixels.
    out_rgba = out.convert("RGBA")
    out_rgba.putalpha(img.getchannel("A"))

    out_rgba.save(output_png, "PNG", optimize=False)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("-c", "--colors", type=int, required=True)
    parser.add_argument("-o", "--output")
    args = parser.parse_args()

    inp = Path(args.input)

    if inp.is_dir():
        outdir = Path(args.output or f"{inp.name}")
        outdir.mkdir(exist_ok=True)

        for png in inp.glob("*.png"):
            out = outdir / png.name
            reduce_colors(png, out, args.colors)
            print("Saved:", out)

    else:
        out = Path(args.output or f"{inp.stem}.png")
        reduce_colors(inp, out, args.colors)
        print("Saved:", out)

if __name__ == "__main__":
    main()