#!/usr/bin/env python3
"""
D-Ark 25th Color - Current Inventory Setter v2

Sets the three cards currently held in the D-Ark's battle inventory and
recalculates the D-Ark save integrity words.

Important:
- Edits ONLY the three current inventory slots plus the save integrity words.
- Does NOT alter the 40-card unlocked/found state.
- Does NOT alter Search Points.
- Does NOT alter firmware patches such as No Search Cost / Keep Used Cards.
- Works on clean or already-patched compatible D-Ark 25th Color BINs.

Inventory:
    save +0x00D8 = slot 1
    save +0x00DA = slot 2
    save +0x00DC = slot 3

Stored card IDs are zero-based uint16 values:
    card 1  -> 0
    card 25 -> 24
    card 40 -> 39

Save integrity:
    checksum = (
        0x078D
        + sum(all little-endian uint16 words from save +0x0004 through +0x0FFF)
    ) & 0xFFFF

    save +0x0000 = checksum
    save +0x0002 = checksum ^ 0xFFFF

The D-Ark has two mirrored 0x1000-byte save blocks. Both are updated.
"""

from pathlib import Path
import argparse
import hashlib
import struct
import sys
from common import *

GP_HEADER = b"GP-SPIF-HEADER"
SAVE_SIZE = 0x1000
INVENTORY_OFFSET = 0x00D8
CHECKSUM_SEED = 0x078D
EMPTY_SLOT = 0x002A

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def card_label(card_id: int) -> str:
    return f"{card_id:2d} - {CARD_NAMES[card_id]}"

def decode_slot(raw_id: int) -> str:
    if raw_id == EMPTY_SLOT:
        return "EMPTY"
    if 0 <= raw_id <= 39:
        return card_label(raw_id + 1)
    return f"UNKNOWN raw value 0x{raw_id:04X}"

def list_cards():
    print("D-Ark 25th Color cards:")
    for card_id in range(1, 41):
        print(f"  {card_label(card_id)}")

def calculate_save_checksum(block: bytes) -> int:
    if len(block) != SAVE_SIZE:
        raise ValueError("save block must be exactly 0x1000 bytes")
    words = struct.unpack_from("<" + "H" * ((SAVE_SIZE - 4) // 2), block, 4)
    return (CHECKSUM_SEED + sum(words)) & 0xFFFF

def save_checksum_is_valid(block: bytes) -> bool:
    if len(block) != SAVE_SIZE:
        return False
    stored, complement = struct.unpack_from("<HH", block, 0)
    calculated = calculate_save_checksum(block)
    return stored == calculated and complement == (stored ^ 0xFFFF)

def is_plausible_save_block(block: bytes) -> bool:
    if len(block) != SAVE_SIZE:
        return False
    if block == b"\xFF" * SAVE_SIZE or block == b"\x00" * SAVE_SIZE:
        return False
    if not save_checksum_is_valid(block):
        return False
    slots = struct.unpack_from("<HHH", block, INVENTORY_OFFSET)
    return all((0 <= x <= 39) or x == EMPTY_SLOT for x in slots)

def find_save_pair(data: bytes) -> int:
    matches = []
    for base in range(0, len(data) - SAVE_SIZE * 2 + 1, SAVE_SIZE):
        a = data[base:base + SAVE_SIZE]
        b = data[base + SAVE_SIZE:base + SAVE_SIZE * 2]
        if a == b and is_plausible_save_block(a):
            matches.append(base)

    if not matches:
        raise RuntimeError(
            "No valid mirrored D-Ark save pair was found. "
            "The input save may already be corrupt or use a different layout."
        )
    if len(matches) != 1:
        where = ", ".join(f"0x{x:08X}" for x in matches)
        raise RuntimeError(
            f"Ambiguous save detection: found {len(matches)} candidates at {where}."
        )
    return matches[0]

def parse_card_id(text: str) -> int:
    try:
        value = int(text, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a valid card ID.")
    if not 1 <= value <= 40:
        raise argparse.ArgumentTypeError("Card IDs must be in the range 1..40.")
    return value

def default_output_path(src: Path, cards) -> Path:
    suffix = src.suffix or ".bin"
    ids = "_".join(str(x) for x in cards)
    return src.with_name(f"{src.stem}_inventory_{ids}{suffix}")

def prompt_for_cards():
    print()
    list_cards()
    print()
    while True:
        parts = input("Enter exactly 3 card IDs (example: 10 17 32): ").replace(",", " ").split()
        if len(parts) != 3:
            print("Please enter exactly 3 IDs.")
            continue
        try:
            return [parse_card_id(x) for x in parts]
        except argparse.ArgumentTypeError as e:
            print(f"ERROR: {e}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_bin")
    ap.add_argument("output_bin", nargs="?")
    ap.add_argument("--cards", nargs=3, type=parse_card_id,
                    metavar=("CARD1", "CARD2", "CARD3"))
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--list-cards", action="store_true")
    ap.add_argument("--allow-duplicates", action="store_true")
    args = ap.parse_args()

    if args.list_cards:
        list_cards()
        return

    src = Path(args.input_bin)
    if not src.is_file():
        print(f"ERROR: file not found: {src}", file=sys.stderr)
        sys.exit(1)

    data = bytearray(src.read_bytes())
    if not data.startswith(GP_HEADER):
        print("ERROR: not a compatible GP-SPIF D-Ark dump.", file=sys.stderr)
        sys.exit(1)

    try:
        save_base = find_save_pair(data)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    mirror_base = save_base + SAVE_SIZE
    current = struct.unpack_from("<HHH", data, save_base + INVENTORY_OFFSET)
    old_checksum = struct.unpack_from("<H", data, save_base)[0]

    print(f"Input:         {src}")
    print(f"SHA-256:       {sha256(data)}")
    print(f"Save block A:  0x{save_base:08X}")
    print(f"Save block B:  0x{mirror_base:08X}")
    print(f"Save checksum: 0x{old_checksum:04X} (valid)")
    print()
    print("Current inventory:")
    for i, raw in enumerate(current, 1):
        print(f"  Slot {i}: {decode_slot(raw)}")

    if args.show:
        print("\nShow only: no file written.")
        return

    cards = list(args.cards) if args.cards else prompt_for_cards()
    if not args.allow_duplicates and len(set(cards)) != 3:
        print("ERROR: select three distinct cards, or use --allow-duplicates.",
              file=sys.stderr)
        sys.exit(1)

    stored = tuple(card_id - 1 for card_id in cards)

    # First write inventory to both mirrors.
    for base in (save_base, mirror_base):
        struct.pack_into("<HHH", data, base + INVENTORY_OFFSET, *stored)

    # Then recompute integrity independently for each mirror.
    for base in (save_base, mirror_base):
        block = bytes(data[base:base + SAVE_SIZE])
        checksum = calculate_save_checksum(block)
        struct.pack_into("<HH", data, base, checksum, checksum ^ 0xFFFF)

    # Final validation.
    block_a = bytes(data[save_base:save_base + SAVE_SIZE])
    block_b = bytes(data[mirror_base:mirror_base + SAVE_SIZE])

    if block_a != block_b:
        print("ERROR: mirrored saves differ after patch.", file=sys.stderr)
        sys.exit(1)
    if not save_checksum_is_valid(block_a):
        print("ERROR: recalculated save checksum failed validation.", file=sys.stderr)
        sys.exit(1)

    new_checksum = struct.unpack_from("<H", block_a, 0)[0]

    dst = Path(args.output_bin) if args.output_bin else default_output_path(src, cards)
    if dst.resolve() == src.resolve():
        print("ERROR: refusing to overwrite input.", file=sys.stderr)
        sys.exit(1)

    dst.write_bytes(data)

    print()
    print("New inventory:")
    for i, card_id in enumerate(cards, 1):
        print(f"  Slot {i}: {card_label(card_id)}")
    print()
    print(f"Checksum: 0x{old_checksum:04X} -> 0x{new_checksum:04X}")
    print(f"Output:   {dst}")
    print(f"SHA-256:  {sha256(data)}")
    print("Done. Inventory and both mirrored save integrity words were updated.")

if __name__ == "__main__":
    main()
