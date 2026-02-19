#!/usr/bin/env python3
import csv
import sys
import shutil
from pathlib import Path

CSV_FILE = Path("rename_helper_list_digivice.csv")
SOURCE_DIR = Path("to_rename")
SOURCE_WAV = SOURCE_DIR / "audio.wav"


def main():
    if not CSV_FILE.exists():
        print(f"ERROR: {CSV_FILE} not found")
        sys.exit(1)

    if not SOURCE_DIR.exists():
        print(f"ERROR: folder '{SOURCE_DIR}' not found")
        sys.exit(1)

    if not SOURCE_WAV.exists():
        print(f"ERROR: source file '{SOURCE_WAV.name}' not found in {SOURCE_DIR}")
        sys.exit(1)

    # Read new filenames from CSV
    with CSV_FILE.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if "new" not in reader.fieldnames:
            print("ERROR: CSV must contain a column named 'new'")
            sys.exit(1)

        new_names = [row["new"].strip() for row in reader if row["new"].strip()]

    if not new_names:
        print("ERROR: No filenames found in CSV")
        sys.exit(1)

    # Copy audio.wav for each new name
    for new_name in new_names:
        dst = SOURCE_DIR / new_name

        if dst.exists():
            print(f"ERROR: target file already exists: {dst}")
            sys.exit(1)

        print(f"audio.wav -> {dst.name}")
        shutil.copyfile(SOURCE_WAV, dst)

    print("All copies created successfully.")


if __name__ == "__main__":
    main()
