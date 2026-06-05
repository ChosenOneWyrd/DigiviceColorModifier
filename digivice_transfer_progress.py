#!/usr/bin/env python3
"""
digivice_transfer_progress.py

Copies the suspected LED color/pattern configuration blocks from the
Taichi DX Digivice 25th ROM (Digivice2.bin) into the white Digivice 25th ROM
(Digivice1.bin), producing a new patched output file.

This does NOT modify either input file.

Usage:
    python digivice_transfer_progress.py Digivice1.bin Digivice2.bin Digivice1_output.bin
"""

from pathlib import Path
import sys
import hashlib

# Start offsets are inclusive. End offsets are exclusive.
# These are the two near-duplicate config blocks that differ between the two bins.
LED_COLOR_BLOCKS = [
    (0x15FDFF8, 0x15FE097),
    (0x15FF000, 0x15FF097),
]

EXPECTED_SIZE = 0x2000000  # 32 MiB


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    if len(sys.argv) != 4:
        print("Usage:")
        print("  python copy_digivice_led_colors.py Digivice1.bin Digivice2.bin Digivice1_led_test.bin")
        return 1

    target_path = Path(sys.argv[1])  # Digivice1.bin / white device base
    source_path = Path(sys.argv[2])  # Digivice2.bin / Taichi DX color source
    output_path = Path(sys.argv[3])

    if not target_path.is_file():
        print(f"ERROR: target/base file not found: {target_path}")
        return 1
    if not source_path.is_file():
        print(f"ERROR: source/color file not found: {source_path}")
        return 1

    target = bytearray(target_path.read_bytes())
    source = source_path.read_bytes()

    if len(target) != len(source):
        print(f"ERROR: file sizes differ: target={len(target)} source={len(source)}")
        return 1

    if len(target) != EXPECTED_SIZE:
        print(f"WARNING: expected 0x{EXPECTED_SIZE:X} bytes, got 0x{len(target):X} bytes.")
        print("Continuing anyway because the files are the same size.")

    print("Base/target:", target_path)
    print("Color source:", source_path)
    print("Output:     ", output_path)
    print()

    for start, end in LED_COLOR_BLOCKS:
        before = bytes(target[start:end])
        replacement = source[start:end]
        target[start:end] = replacement

        print(f"Copied 0x{end - start:X} bytes: 0x{start:08X} - 0x{end - 1:08X}")
        print(f"  old block sha256: {sha256(before)}")
        print(f"  new block sha256: {sha256(replacement)}")

        if bytes(target[start:end]) != replacement:
            print("ERROR: verification failed after patching this block.")
            return 1

    output_path.write_bytes(target)

    print()
    print("Done.")
    print(f"Wrote patched file: {output_path}")
    print(f"Patched ROM sha256: {sha256(bytes(target))}")
    print()
    print("Flash/import this output file for testing, not the original Digivice1.bin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
