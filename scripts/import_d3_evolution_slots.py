#!/usr/bin/env python3
"""
import_d3_evolution_slots.py

Import a simple 7-row D-3 25th Color evolution-line CSV and automatically
synchronize the three known evolution membership/order structures:

1) 44-entry uint32 flat evolution-order table @ 0x000AADDE
   (this is the real BIN structure represented by "stock_fixed_segments"
    in the earlier JSON export)

2) Seven 9-entry uint16 target arrays:
       Vmon Line       0x000A939C
       Wormmon Line    0x000A93AE
       Hawkmon Line    0x000A93C0
       Tailmon Line    0x000A93D2
       Armadimon Line  0x000A93E4
       Patamon Line    0x000A93F6
       Terriermon Line 0x000A9408

3) Partner Table slot_type @
       0x0009D968 + digimon_id * 0x20 + 0x04

CSV format:
    line_id,line_name,slot_1,slot_2,slot_3,slot_4,slot_5,slot_6,slot_7,slot_8,slot_9,slot_10

Important derivation rules
--------------------------
- The CSV is the single source of truth for COMPLETE line membership/order.
- Stage-0/baby IDs and Imperialdramon Paladin Mode (ID 9) are intentionally
  excluded from target arrays, matching stock D3 behavior.
- Target-array order is NOT CSV order.  The program preserves all ordering
  constraints present in the stock target arrays and uses Partner Table
  progression metadata only to resolve otherwise-unrelated cross-line ties.
  Therefore an unchanged stock CSV rebuilds the exact stock target arrays.
- Known shared evolutions remain protected:
      IDs 6,7,8,9 -> Vmon Line + Wormmon Line
      ID 13       -> Hawkmon Line + Tailmon Line
      ID 20       -> Armadimon Line + Patamon Line
  All other Digimon IDs must occur exactly once and may be moved freely.
- For every non-shared Digimon, Partner Table slot_type is automatically set
  to the line_id containing that Digimon.
- Shared Partner slot_type values 18/19/20 are preserved.
- evo_animation1..5 are NOT modified.

Usage:
    python import_d3_evolution_slots.py \
        D3.bin d3_evolution_slots.csv D3_out.bin

Preview only:
    python import_d3_evolution_slots.py \
        D3.bin d3_evolution_slots.csv D3_out.bin --dry-run

In-place:
    python import_d3_evolution_slots.py \
        D3.bin d3_evolution_slots.csv D3.bin
"""

import argparse
import csv
import hashlib
import os
import struct
import tempfile
from collections import defaultdict
from pathlib import Path

GP_HEADER = b"GP-SPIF-HEADER"

ORDER_OFFSET = 0x000AADDE
ORDER_COUNT = 44

TARGET_ARRAY_DEFS = [
    (0, "Vmon Line",       0x000A939C),
    (1, "Wormmon Line",    0x000A93AE),
    (2, "Hawkmon Line",    0x000A93C0),
    (3, "Tailmon Line",    0x000A93D2),
    (4, "Armadimon Line",  0x000A93E4),
    (5, "Patamon Line",    0x000A93F6),
    (6, "Terriermon Line", 0x000A9408),
]
TARGET_SLOTS = 9
TARGET_EMPTY = 0xFFFF

LINE_NAMES = {line_id: name for line_id, name, _ in TARGET_ARRAY_DEFS}

PARTNER_TABLE_OFFSET = 0x0009D968
PARTNER_RECORD_SIZE = 0x20
PARTNER_COUNT = 38

P_STAGE = 0
P_DIGIMON_ID = 1
P_SLOT_TYPE = 2
P_WINS = 3
P_EVO1 = 6
P_EVO5 = 10

MAX_CSV_SLOTS = 10

# Shared forms whose Partner Table slot_type is NOT a direct 0..6 line_id.
SHARED_ID_LINES = {
    6:  {0, 1},
    7:  {0, 1},
    8:  {0, 1},
    9:  {0, 1},
    13: {2, 3},
    20: {4, 5},
}

SHARED_ID_SLOT_TYPE = {
    6: 18,
    7: 18,
    8: 18,
    9: 18,
    13: 19,
    20: 20,
}

# Stock D3 does not put these complete-line members into the normal
# target arrays:
#   0,10,16,23,28 = Stage-0/baby forms
#   9             = Imperialdramon Paladin Mode, handled by special logic
NON_TARGET_IDS = {0, 9, 10, 16, 23, 28}

# These are the stock D3 target-array sequences.  They establish the internal
# precedence relationships that must be preserved when deriving target arrays
# from the much simpler complete-line CSV.
#
# We intentionally do NOT just reverse the CSV slots: the stock order is
# semantically different.
STOCK_TARGET_SEQUENCES = [
    [8, 7, 6, 4, 3, 2, 5, 1],
    [8, 7, 6, 34, 33],
    [13, 26, 25, 27, 24],
    [15, 14, 13, 12, 11],
    [20, 31, 30, 32, 29],
    [22, 21, 20, 18, 19, 17],
    [37, 36, 35],
]

ORDER_PRE_OFFSET = 0x000AADDA
ORDER_PRE_BYTES = bytes.fromhex("C3 01 C4 01")
ORDER_POST_OFFSET = ORDER_OFFSET + ORDER_COUNT * 4
ORDER_POST_BYTES = bytes.fromhex(
    "6B 6D 03 00 87 6D 03 00 9A 6F 03 00 D2 74 03 00"
)

TARGET_PRE_OFFSET = 0x000A9390
TARGET_PRE_WORDS = (41071, 41073, 41028, 41060, 41059, 41169)
TARGET_POST_OFFSET = 0x000A941A
TARGET_POST_WORDS = (558, 559, 564, 565, 562, 563, 566, 567)


def u16(data, off):
    return struct.unpack_from("<H", data, off)[0]


def u32(data, off):
    return struct.unpack_from("<I", data, off)[0]


def put_u16(data, off, value):
    struct.pack_into("<H", data, off, value)


def put_u32(data, off, value):
    struct.pack_into("<I", data, off, value)


def sha256_bytes(data):
    return hashlib.sha256(bytes(data)).hexdigest()


def safe_write(src, dst, data):
    src = Path(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    if src.resolve() != dst.resolve():
        dst.write_bytes(data)
        if dst.stat().st_size != len(data):
            raise RuntimeError("Output BIN size validation failed")
        return

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{dst.name}.",
        suffix=".tmp",
        dir=str(dst.parent),
    )
    os.close(fd)
    temp_path = Path(temp_name)

    try:
        temp_path.write_bytes(data)
        if temp_path.stat().st_size != len(data):
            raise RuntimeError("Temporary BIN size validation failed")
        os.replace(temp_path, dst)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def validate_bin(data):
    if len(data) < ORDER_POST_OFFSET + len(ORDER_POST_BYTES):
        raise RuntimeError("BIN is too small for the known D3 layout")

    if not data.startswith(GP_HEADER):
        raise RuntimeError("Input does not look like a compatible D3 GP-SPIF BIN")

    if data[ORDER_PRE_OFFSET:ORDER_PRE_OFFSET + len(ORDER_PRE_BYTES)] != ORDER_PRE_BYTES:
        raise RuntimeError(
            "44-entry evolution-order pre-signature did not match. "
            "This may be a different firmware layout."
        )

    if data[ORDER_POST_OFFSET:ORDER_POST_OFFSET + len(ORDER_POST_BYTES)] != ORDER_POST_BYTES:
        raise RuntimeError(
            "44-entry evolution-order post-signature did not match. "
            "This may be a different firmware layout."
        )

    pre_words = struct.unpack_from(
        "<" + "H" * len(TARGET_PRE_WORDS), data, TARGET_PRE_OFFSET
    )
    post_words = struct.unpack_from(
        "<" + "H" * len(TARGET_POST_WORDS), data, TARGET_POST_OFFSET
    )

    if pre_words != TARGET_PRE_WORDS:
        raise RuntimeError("Seven target-array pre-signature did not match")

    if post_words != TARGET_POST_WORDS:
        raise RuntimeError("Seven target-array post-signature did not match")

    for digimon_id in range(PARTNER_COUNT):
        base = PARTNER_TABLE_OFFSET + digimon_id * PARTNER_RECORD_SIZE
        stored_id = u16(data, base + P_DIGIMON_ID * 2)
        if stored_id != digimon_id:
            raise RuntimeError(
                f"Partner Table identity mismatch at record {digimon_id}: "
                f"stored digimon_id={stored_id}"
            )

    # Shared slot types are a safety-critical invariant.
    for digimon_id, expected_slot_type in SHARED_ID_SLOT_TYPE.items():
        base = PARTNER_TABLE_OFFSET + digimon_id * PARTNER_RECORD_SIZE
        actual = u16(data, base + P_SLOT_TYPE * 2)
        if actual != expected_slot_type:
            raise RuntimeError(
                f"Shared Digimon ID {digimon_id} has slot_type={actual}; "
                f"expected {expected_slot_type}. This importer intentionally "
                "does not rewrite shared-evolution classification."
            )


def parse_slot(text, row_no, column):
    s = str(text).strip()
    if s == "":
        return None

    try:
        value = int(s, 0)
    except ValueError:
        raise RuntimeError(
            f"CSV row {row_no}, {column}: invalid Digimon ID {text!r}"
        )

    if not 0 <= value < PARTNER_COUNT:
        raise RuntimeError(
            f"CSV row {row_no}, {column}: Digimon ID must be 0..{PARTNER_COUNT - 1}"
        )

    return value


def load_csv(csv_path):
    required_headers = ["line_id", "line_name"] + [
        f"slot_{i}" for i in range(1, MAX_CSV_SLOTS + 1)
    ]

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        if reader.fieldnames is None:
            raise RuntimeError("CSV has no header")

        missing = [h for h in required_headers if h not in reader.fieldnames]
        if missing:
            raise RuntimeError(
                "CSV is missing required column(s): " + ", ".join(missing)
            )

        rows = list(reader)

    if len(rows) != 7:
        raise RuntimeError(f"CSV must contain exactly 7 line rows; found {len(rows)}")

    lines = {}
    seen_line_ids = set()

    for row_no, row in enumerate(rows, start=2):
        try:
            line_id = int(str(row["line_id"]).strip(), 0)
        except ValueError:
            raise RuntimeError(f"CSV row {row_no}: invalid line_id")

        if line_id not in LINE_NAMES:
            raise RuntimeError(f"CSV row {row_no}: line_id must be 0..6")

        if line_id in seen_line_ids:
            raise RuntimeError(f"CSV row {row_no}: duplicate line_id {line_id}")

        seen_line_ids.add(line_id)

        expected_name = LINE_NAMES[line_id]
        actual_name = str(row["line_name"]).strip()

        if actual_name != expected_name:
            raise RuntimeError(
                f"CSV row {row_no}: line_name for line_id {line_id} must be "
                f"{expected_name!r}; found {actual_name!r}"
            )

        slots = []
        hit_blank = False

        for i in range(1, MAX_CSV_SLOTS + 1):
            col = f"slot_{i}"
            value = parse_slot(row.get(col, ""), row_no, col)

            if value is None:
                hit_blank = True
                continue

            if hit_blank:
                raise RuntimeError(
                    f"CSV row {row_no}: {col} is filled after an earlier blank slot. "
                    "Slots must be contiguous from slot_1."
                )

            if value in slots:
                raise RuntimeError(
                    f"CSV row {row_no}: Digimon ID {value} appears twice in "
                    f"{expected_name}"
                )

            slots.append(value)

        if not slots:
            raise RuntimeError(f"CSV row {row_no}: {expected_name} cannot be empty")

        lines[line_id] = slots

    if seen_line_ids != set(range(7)):
        raise RuntimeError("CSV must contain line_id 0..6 exactly once")

    return lines


def validate_membership(lines):
    occurrences = defaultdict(list)

    for line_id in range(7):
        for digimon_id in lines[line_id]:
            occurrences[digimon_id].append(line_id)

    # Every D3 Partner Digimon must remain represented.
    missing = [i for i in range(PARTNER_COUNT) if i not in occurrences]
    if missing:
        raise RuntimeError(
            "Every Digimon ID 0..37 must remain in the complete evolution lines. "
            f"Missing ID(s): {missing}"
        )

    # Known shared forms must remain in exactly their established pair of lines.
    for digimon_id, expected_lines in SHARED_ID_LINES.items():
        actual_lines = set(occurrences.get(digimon_id, []))
        if actual_lines != expected_lines:
            raise RuntimeError(
                f"Shared Digimon ID {digimon_id} must remain in "
                f"{[LINE_NAMES[x] for x in sorted(expected_lines)]}; "
                f"found {[LINE_NAMES[x] for x in sorted(actual_lines)]}"
            )

    # Every non-shared form must occur exactly once.
    for digimon_id in range(PARTNER_COUNT):
        if digimon_id in SHARED_ID_LINES:
            continue

        actual = occurrences.get(digimon_id, [])
        if len(actual) != 1:
            raise RuntimeError(
                f"Normal Digimon ID {digimon_id} must appear exactly once; "
                f"found {len(actual)} occurrence(s)"
            )

    flat_count = sum(len(lines[line_id]) for line_id in range(7))
    if flat_count != ORDER_COUNT:
        raise RuntimeError(
            f"Complete line CSV contains {flat_count} total occurrences; "
            f"D3 requires exactly {ORDER_COUNT}"
        )


def read_partner_info(data):
    """
    Return progression metadata used ONLY as deterministic tie-breakers when
    two Digimon have no stock target-order relationship to each other.
    """
    info = {}

    for digimon_id in range(PARTNER_COUNT):
        base = PARTNER_TABLE_OFFSET + digimon_id * PARTNER_RECORD_SIZE

        stage = u16(data, base + P_STAGE * 2)
        wins = u16(data, base + P_WINS * 2)
        anims = [
            u16(data, base + word_index * 2)
            for word_index in range(P_EVO1, P_EVO5 + 1)
        ]
        animation_depth = sum(1 for x in anims if x != 0)

        info[digimon_id] = {
            "stage": stage,
            "wins": wins,
            "animation_depth": animation_depth,
        }

    return info


def build_global_target_priority(data):
    """
    Convert the seven stock target sequences into one deterministic global
    partial-order merge.

    All adjacent precedence relationships in every stock target array are kept.
    If two IDs were never related by a stock array, tie-break by:
        higher stage
        higher win requirement
        longer animation chain
        higher Digimon ID

    Filtering this global order back to any unchanged stock line reproduces
    the exact stock target array.  When a normal Digimon is moved to a new line,
    this gives it a sensible position without making CSV users maintain a
    second internal-order representation manually.
    """
    partner = read_partner_info(data)

    eligible = set(range(PARTNER_COUNT)) - NON_TARGET_IDS

    edges = {node: set() for node in eligible}
    indegree = {node: 0 for node in eligible}

    for seq in STOCK_TARGET_SEQUENCES:
        for left, right in zip(seq, seq[1:]):
            if right not in edges[left]:
                edges[left].add(right)
                indegree[right] += 1

    def priority_key(digimon_id):
        p = partner[digimon_id]
        return (
            -p["stage"],
            -p["wins"],
            -p["animation_depth"],
            -digimon_id,
        )

    available = [node for node in eligible if indegree[node] == 0]
    result = []

    while available:
        available.sort(key=priority_key)
        node = available.pop(0)
        result.append(node)

        for nxt in sorted(edges[node]):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                available.append(nxt)

    if len(result) != len(eligible):
        raise RuntimeError(
            "Internal target-priority graph contains a cycle; cannot derive "
            "target arrays safely."
        )

    # Self-test: filtering the global order through each stock membership must
    # exactly reproduce the known stock sequence.
    stock_complete_lines = [
        [0,1,2,3,4,5,6,7,8,9],
        [33,34,6,7,8,9],
        [23,24,25,26,27,13],
        [10,11,12,13,14,15],
        [28,29,30,31,32,20],
        [16,17,18,19,20,21,22],
        [35,36,37],
    ]

    for line_id in range(7):
        members = set(stock_complete_lines[line_id]) - NON_TARGET_IDS
        rebuilt = [x for x in result if x in members]
        if rebuilt != STOCK_TARGET_SEQUENCES[line_id]:
            raise RuntimeError(
                f"Internal target-priority self-test failed for "
                f"{LINE_NAMES[line_id]}:\n"
                f"  expected {STOCK_TARGET_SEQUENCES[line_id]}\n"
                f"  rebuilt  {rebuilt}"
            )

    return result


def derive_target_arrays(data, lines):
    global_priority = build_global_target_priority(data)
    targets = {}

    for line_id in range(7):
        eligible_members = set(lines[line_id]) - NON_TARGET_IDS
        ordered = [x for x in global_priority if x in eligible_members]

        if len(ordered) != len(eligible_members):
            missing = sorted(eligible_members - set(ordered))
            raise RuntimeError(
                f"Could not assign target priority to IDs {missing} "
                f"in {LINE_NAMES[line_id]}"
            )

        if len(ordered) > TARGET_SLOTS:
            raise RuntimeError(
                f"{LINE_NAMES[line_id]} would need {len(ordered)} normal target "
                f"entries, but D3 has only {TARGET_SLOTS} target slots."
            )

        targets[line_id] = ordered + [TARGET_EMPTY] * (TARGET_SLOTS - len(ordered))

    return targets


def derive_partner_slot_types(data, lines):
    """
    Direct/non-shared Digimon get slot_type = containing line_id.
    Shared IDs keep 18/19/20.
    """
    desired = {}

    for line_id in range(7):
        for digimon_id in lines[line_id]:
            if digimon_id in SHARED_ID_SLOT_TYPE:
                desired[digimon_id] = SHARED_ID_SLOT_TYPE[digimon_id]
            else:
                desired[digimon_id] = line_id

    if set(desired) != set(range(PARTNER_COUNT)):
        raise RuntimeError("Internal Partner slot_type derivation did not cover IDs 0..37")

    return desired


def apply_changes(data, lines, targets, partner_slot_types):
    changes = []

    # ------------------------------------------------------------------
    # 1) 44-entry complete-line flat order
    # ------------------------------------------------------------------
    flat = []
    for line_id in range(7):
        flat.extend(lines[line_id])

    if len(flat) != ORDER_COUNT:
        raise RuntimeError("Internal flat-order length error")

    for i, new in enumerate(flat):
        off = ORDER_OFFSET + i * 4
        old = u32(data, off)
        if old != new:
            put_u32(data, off, new)
            changes.append(("flat_order", off, old, new))

    # ------------------------------------------------------------------
    # 2) Seven target arrays
    # ------------------------------------------------------------------
    for line_id, line_name, base in TARGET_ARRAY_DEFS:
        values = targets[line_id]

        for i, new in enumerate(values):
            off = base + i * 2
            old = u16(data, off)

            if old != new:
                put_u16(data, off, new)
                changes.append((f"target:{line_name}", off, old, new))

    # ------------------------------------------------------------------
    # 3) Partner Table slot_type
    # ------------------------------------------------------------------
    for digimon_id in range(PARTNER_COUNT):
        base = PARTNER_TABLE_OFFSET + digimon_id * PARTNER_RECORD_SIZE
        off = base + P_SLOT_TYPE * 2
        old = u16(data, off)
        new = partner_slot_types[digimon_id]

        if old != new:
            put_u16(data, off, new)
            changes.append(("partner.slot_type", off, old, new))

    return changes


def print_plan(lines, targets, partner_slot_types, changes):
    print("=== Complete line membership/order from CSV ===")
    for line_id in range(7):
        print(f"  {line_id} {LINE_NAMES[line_id]}: {lines[line_id]}")

    print()
    print("=== Automatically derived target arrays ===")
    for line_id in range(7):
        shown = [
            None if x == TARGET_EMPTY else x
            for x in targets[line_id]
        ]
        print(f"  {line_id} {LINE_NAMES[line_id]}: {shown}")

    print()
    print("=== Automatically derived direct Partner slot_type ===")
    for digimon_id in range(PARTNER_COUNT):
        if digimon_id in SHARED_ID_SLOT_TYPE:
            continue
        print(f"  ID {digimon_id:2d} -> slot_type {partner_slot_types[digimon_id]}")

    print()
    print(f"=== Planned BIN field changes: {len(changes)} ===")
    for kind, off, old, new in changes:
        old_disp = "FFFF" if old == 0xFFFF else str(old)
        new_disp = "FFFF" if new == 0xFFFF else str(new)
        print(f"  {kind:24s} 0x{off:08X}: {old_disp} -> {new_disp}")


def main():
    ap = argparse.ArgumentParser(
        description=(
            "Import simple D3 evolution-line CSV and synchronize flat order, "
            "target arrays, and Partner Table slot_type"
        )
    )
    ap.add_argument("bin", help="Input D3.bin")
    ap.add_argument("csv", help="Edited evolution-line CSV")
    ap.add_argument("output_bin", help="Output BIN; may be the same as input")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Show derived structures and exact changes without writing output",
    )
    args = ap.parse_args()

    src = Path(args.bin)
    data = bytearray(src.read_bytes())

    validate_bin(data)
    lines = load_csv(args.csv)
    validate_membership(lines)

    targets = derive_target_arrays(data, lines)
    partner_slot_types = derive_partner_slot_types(data, lines)

    before = sha256_bytes(data)
    changes = apply_changes(data, lines, targets, partner_slot_types)

    # Re-check immutable signatures / table identity after planned edits.
    validate_bin(data)

    print_plan(lines, targets, partner_slot_types, changes)

    if args.dry_run:
        print()
        print("[DRY RUN] No output BIN written.")
        return

    safe_write(src, Path(args.output_bin), data)

    print()
    print(f"[DONE] Wrote: {args.output_bin}")
    print(f"  input SHA-256 : {before}")
    print(f"  output SHA-256: {sha256_bytes(data)}")
    print()
    print("evo_animation1..5 were NOT modified.")


if __name__ == "__main__":
    main()
