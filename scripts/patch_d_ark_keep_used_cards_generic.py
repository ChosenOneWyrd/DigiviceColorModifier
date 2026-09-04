#!/usr/bin/env python3
"""
Generic D-Ark 25th Color patcher

Patches:
  1. Digimon Search costs 0 Search Points.
  2. Cards used/slashed in battle are not removed afterward.
  3. Search card discard/replacement remains normal.

Unlike the older v1 script, this version does NOT require one exact full-file
SHA-256 and does NOT depend on save data such as current cards, Search Points,
unlocks, progress, etc.

Instead, it finds the two firmware routines by unique code signatures and
patches the instruction inside each matched routine.

Safety:
  - Requires the GeneralPlus "GP-SPIF-HEADER" firmware header.
  - Requires each target routine signature to match exactly once.
  - Recognizes both original and already-patched instructions.
  - Refuses unknown/ambiguous firmware instead of guessing.
  - Never modifies the input file in place.

This is generic across D-Ark 25th Color dumps that use the same compatible
firmware code, even when their save data and SHA-256 differ. A genuinely
recompiled/different firmware revision may need a new signature.
"""

from pathlib import Path
import argparse
import hashlib
import sys

GP_HEADER = b"GP-SPIF-HEADER"

# Search-cost instruction context:
#   41 20 [4A 66] 08 09 01 00 C4 D6 42 F0 AD 7E 41 20
SEARCH_PREFIX = bytes.fromhex("41 20")
SEARCH_SUFFIX = bytes.fromhex("08 09 01 00 C4 D6 42 F0 AD 7E 41 20")
SEARCH_TARGET_LEN = 2
SEARCH_ORIGINAL = bytes.fromhex("4A 66")  # r3 = -10
SEARCH_PATCHED = bytes.fromhex("40 66")   # r3 = 0

# Used-card-removal call context:
#   08 09 01 00 C4 D6 [45 F0 9A 5D] 41 00 13 96 14 98
CONSUME_PREFIX = bytes.fromhex("08 09 01 00 C4 D6")
CONSUME_SUFFIX = bytes.fromhex("41 00 13 96 14 98")
CONSUME_TARGET_LEN = 4
CONSUME_ORIGINAL = bytes.fromhex("45 F0 9A 5D")
CONSUME_PATCHED = bytes.fromhex("40 00 40 00")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def default_output(src: Path) -> Path:
    suffix = src.suffix or ".bin"
    return src.with_name(src.stem + "_no_search_cost_keep_used_cards" + suffix)


def find_context_targets(data: bytes, prefix: bytes, suffix: bytes, target_len: int):
    hits = []
    start = 0
    while True:
        pos = data.find(prefix, start)
        if pos < 0:
            break
        target = pos + len(prefix)
        suffix_pos = target + target_len
        if data[suffix_pos:suffix_pos + len(suffix)] == suffix:
            hits.append(target)
        start = pos + 1
    return hits


def locate_unique(data, feature, prefix, suffix, target_len, original, patched):
    hits = find_context_targets(data, prefix, suffix, target_len)
    if not hits:
        raise RuntimeError(
            f"{feature}: compatible code signature was not found.\n"
            "This may be a different firmware revision. No patch was written."
        )
    if len(hits) != 1:
        where = ", ".join(f"0x{x:08X}" for x in hits)
        raise RuntimeError(
            f"{feature}: signature is ambiguous ({len(hits)} matches: {where}).\n"
            "No patch was written."
        )

    off = hits[0]
    current = bytes(data[off:off + target_len])
    if current == original:
        state = "original"
    elif current == patched:
        state = "already_patched"
    else:
        raise RuntimeError(
            f"{feature}: routine found at 0x{off:08X}, but target instruction is unknown.\n"
            f"Expected original:      {original.hex(' ').upper()}\n"
            f"Expected already patch: {patched.hex(' ').upper()}\n"
            f"Found:                  {current.hex(' ').upper()}\n"
            "No patch was written."
        )
    return off, state, current


def main():
    ap = argparse.ArgumentParser(
        description=(
            "Patch a compatible D-Ark 25th Color BIN so Search costs 0 points "
            "and cards remain after use while Search discard/replacement stays normal."
        )
    )
    ap.add_argument("input_bin", help="D-Ark 25th Color flash BIN")
    ap.add_argument("output_bin", nargs="?", help="Output BIN path")
    ap.add_argument(
        "--check-only",
        action="store_true",
        help="Validate and show patch locations without writing an output file.",
    )
    args = ap.parse_args()

    src = Path(args.input_bin)
    if not src.is_file():
        print(f"ERROR: input file does not exist: {src}", file=sys.stderr)
        sys.exit(1)

    data = bytearray(src.read_bytes())
    if len(data) < 0x100000:
        print(f"ERROR: file is unexpectedly small ({len(data)} bytes).", file=sys.stderr)
        sys.exit(1)
    if not data.startswith(GP_HEADER):
        print(
            "ERROR: file does not begin with GP-SPIF-HEADER; "
            "it does not look like a supported D-Ark 25th Color dump.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        search_off, search_state, search_bytes = locate_unique(
            data, "No Search Cost", SEARCH_PREFIX, SEARCH_SUFFIX,
            SEARCH_TARGET_LEN, SEARCH_ORIGINAL, SEARCH_PATCHED
        )
        consume_off, consume_state, consume_bytes = locate_unique(
            data, "Keep Used Cards", CONSUME_PREFIX, CONSUME_SUFFIX,
            CONSUME_TARGET_LEN, CONSUME_ORIGINAL, CONSUME_PATCHED
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Input:   {src}")
    print(f"Size:    {len(data)} bytes")
    print(f"SHA-256: {digest(data)}")
    print()
    print("Compatible routines found:")
    print(f"  No Search Cost : 0x{search_off:08X} [{search_state}] {search_bytes.hex(' ').upper()}")
    print(f"  Keep Used Cards: 0x{consume_off:08X} [{consume_state}] {consume_bytes.hex(' ').upper()}")

    if args.check_only:
        print("\nCheck only: no output file was written.")
        return

    changes = []
    if search_state == "original":
        old = bytes(data[search_off:search_off + SEARCH_TARGET_LEN])
        data[search_off:search_off + SEARCH_TARGET_LEN] = SEARCH_PATCHED
        changes.append((search_off, old, SEARCH_PATCHED, "Search cost -10 -> 0"))

    if consume_state == "original":
        old = bytes(data[consume_off:consume_off + CONSUME_TARGET_LEN])
        data[consume_off:consume_off + CONSUME_TARGET_LEN] = CONSUME_PATCHED
        changes.append((consume_off, old, CONSUME_PATCHED, "disable used-card removal"))

    dst = Path(args.output_bin) if args.output_bin else default_output(src)
    if dst.resolve() == src.resolve():
        print("ERROR: refusing to overwrite the input file.", file=sys.stderr)
        sys.exit(1)

    dst.write_bytes(data)

    print("\nApplied patches:")
    if changes:
        for off, old, new, desc in changes:
            print(f"  0x{off:08X}: {old.hex(' ').upper()} -> {new.hex(' ').upper()} ({desc})")
    else:
        print("  Both features were already patched; output is an identical copy.")

    print(f"\nOutput:  {dst}")
    print(f"SHA-256: {digest(data)}")
    print("\nBehavior:")
    print("  - Digimon Search costs 0 Search Points.")
    print("  - A card used/slashed in battle remains in inventory.")
    print("  - Search discard/replacement remains enabled.")


if __name__ == "__main__":
    main()
