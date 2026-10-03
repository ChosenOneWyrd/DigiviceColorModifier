#!/usr/bin/env python3
"""
for 96×96px images in the current directory:
python scripts/list_images_by_size.py ./d3_color_sprites 96 96
"""

import argparse
from pathlib import Path
from PIL import Image


SUPPORTED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".gif",
    ".webp",
    ".tif",
    ".tiff",
}


def find_images_by_size(directory, width, height, recursive=False):
    directory = Path(directory)

    if not directory.is_dir():
        raise ValueError(f"Directory does not exist: {directory}")

    iterator = directory.rglob("*") if recursive else directory.iterdir()

    matches = []

    for file_path in iterator:
        if not file_path.is_file():
            continue

        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        try:
            with Image.open(file_path) as image:
                if image.size == (width, height):
                    matches.append(file_path)

        except (OSError, ValueError):
            # Ignore unreadable/corrupt/non-image files
            pass

    return sorted(matches)


def main():
    parser = argparse.ArgumentParser(
        description="List all images with a specific pixel size."
    )

    parser.add_argument(
        "directory",
        help="Directory containing the images",
    )

    parser.add_argument(
        "width",
        type=int,
        help="Required image width in pixels",
    )

    parser.add_argument(
        "height",
        type=int,
        help="Required image height in pixels",
    )

    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Also search inside subdirectories",
    )

    args = parser.parse_args()

    matches = find_images_by_size(
        args.directory,
        args.width,
        args.height,
        recursive=args.recursive,
    )

    print(f"\nImages that are exactly {args.width}x{args.height}px:\n")

    if not matches:
        print("No matching images found.")
        return

    for path in matches:
        print(path)

    print(f"\nTotal: {len(matches)} image(s)")


if __name__ == "__main__":
    main()