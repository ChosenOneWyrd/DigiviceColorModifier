#!/usr/bin/env python3
import csv
import sys
from pathlib import Path
from PIL import Image

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}

def count_colors(path: Path) -> int:
    with Image.open(path) as img:
        img = img.convert("RGBA")
        colors = img.getcolors(maxcolors=img.width * img.height)
        return len(colors) if colors is not None else img.width * img.height

def main():
    if len(sys.argv) < 3:
        print("Usage: python image_color_count_csv.py input_folder output.csv")
        return

    folder = Path(sys.argv[1])
    out_csv = Path(sys.argv[2])

    rows = []

    for path in folder.rglob("*"):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
            try:
                rows.append({
                    "filename": path.name,
                    "relative_path": str(path.relative_to(folder)),
                    "color_count": count_colors(path),
                })
            except Exception as e:
                rows.append({
                    "filename": path.name,
                    "relative_path": str(path.relative_to(folder)),
                    "color_count": f"ERROR: {e}",
                })

    rows.sort(key=lambda r: int(r["color_count"]) if str(r["color_count"]).isdigit() else 999999999)

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["filename", "relative_path", "color_count"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"[+] Wrote {len(rows)} image records to {out_csv}")

if __name__ == "__main__":
    main()