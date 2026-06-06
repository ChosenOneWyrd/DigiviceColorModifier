#!/usr/bin/env python3
"""
Usage:

python digivice_transfer_led_colors.py Digivice1.bin Digivice2.bin Digivice1_output.bin

Test patcher for Digivice 25th ROMs.

IMPORTANT:
The previously tested 0x15FE000 / 0x15FF000 blocks behaved like save/config/progress
slots, not LED tables. This script intentionally does NOT copy those blocks.

It copies only the one remaining isolated 16-byte difference between the supplied
Digivice1.bin and Digivice2.bin:

    0x00100084 - 0x00100093 inclusive

Hypothesis: this may be a SKU/model/config value that causes firmware to select a
Taichi DX LED calibration/palette path. If this does not change LEDs, then the LED
color difference is probably not stored in the flash image differences between
these two dumps, or it is controlled by hardware/MCU state outside the dumped ROM.
"""
from __future__ import annotations

import argparse
from pathlib import Path

PATCH_RANGES = [
    (0x00100084, 0x00100094),  # end-exclusive
]
EXPECTED_SIZE = 0x02000000  # 32 MiB


def main() -> int:
    ap = argparse.ArgumentParser(description="Copy only the isolated Digivice2 model/config bytes into Digivice1.")
    ap.add_argument("digivice1", help="Base white Digivice 25th BIN")
    ap.add_argument("digivice2", help="Source Taichi DX Digivice 25th BIN")
    ap.add_argument("output", help="Output patched BIN")
    ap.add_argument("--force", action="store_true", help="Allow non-32MiB files")
    args = ap.parse_args()

    p1 = Path(args.digivice1)
    p2 = Path(args.digivice2)
    out = Path(args.output)

    b1 = bytearray(p1.read_bytes())
    b2 = p2.read_bytes()

    if not args.force:
        for p, b in [(p1, b1), (p2, b2)]:
            if len(b) != EXPECTED_SIZE:
                raise SystemExit(f"ERROR: {p} is {len(b)} bytes, expected {EXPECTED_SIZE}. Use --force to override.")

    print("Copying ONLY isolated model/config candidate bytes, not save/progress blocks:")
    for start, end in PATCH_RANGES:
        old = bytes(b1[start:end])
        new = b2[start:end]
        print(f"  0x{start:08X}-0x{end-1:08X} ({end-start} bytes)")
        print(f"    old: {old.hex(' ')}")
        print(f"    new: {new.hex(' ')}")
        b1[start:end] = new

    out.write_bytes(b1)

    verify = out.read_bytes()
    for start, end in PATCH_RANGES:
        if verify[start:end] != b2[start:end]:
            raise SystemExit(f"ERROR: verification failed at 0x{start:08X}-0x{end-1:08X}")

    print(f"\nWrote: {out}")
    print("Verification OK.")
    print("Reminder: do NOT copy 0x15FE000/0x15FF000 for LED testing; those behaved like save/config/progress data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
