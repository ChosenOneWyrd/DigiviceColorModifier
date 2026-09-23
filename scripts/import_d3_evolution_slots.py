#!/usr/bin/env python3
"""
import_d3_evolution_slots.py

Import a simple 7-row D-3 25th Color evolution-line CSV and automatically
synchronize the three original evolution membership/order structures plus the
battle selector's per-line scan order:

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

4) A short-branch-only in-place battle-selector patch plus a 44-word raw
   candidate table.  The table contains the pre-normalized scan values expected
   by the stock candidate helper, rather than final Digimon IDs.  This lets the
   selector follow any CSV slot order while retaining the hardware-confirmed
   stock lookup/eligibility path.  It handles wrapped shared/Jogress candidates,
   such as the Wormmon line's IDs 6..9, and baby forms without code caves.  The
   reader uses the firmware's verified +0x9000-word data-address mapping.

CSV format:
    line_id,line_name,slot_1,slot_2,slot_3,slot_4,slot_5,slot_6,slot_7,slot_8,slot_9,slot_10

Important derivation rules
--------------------------
- The CSV is the single source of truth for COMPLETE line membership/order.
- Stage-0/baby IDs and Imperialdramon Paladin Mode (ID 9) remain excluded from
  the internal target arrays, matching stock D3 behavior.  They are still full
  CSV slots and may be reordered/moved; the Viewer and battle selector use the
  complete 44-entry order.
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
- Shared/Jogress forms may be reordered independently inside each of their
  required paired lines.  Their IDs and shared slot_type values do not change.
- The importer upgrades stock firmware, generic revisions v1 through v6, and
  both hardware-confirmed inline probes to the generated v7 selector.
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

# ---------------------------------------------------------------------------
# Generic battle-selector order patch, hardware-confirmed v7
# ---------------------------------------------------------------------------
#
# The stock selector scans candidates numerically, so changing the flat Viewer
# order does not change battle-selector order.  The v7 patch keeps all code
# inside the original selector routine and changes only its candidate source:
#
#   global_slot  = sum(lengths before selected_line) + scan_index
#   eligible     = global_slot < end_of_selected_line
#   raw_candidate = selector_raw_order[global_slot]
#
# The stock helper turns each raw candidate into the final Digimon ID and loads
# its Partner Table record.  For a line with stock base B, Digimon ID D is
# encoded as B + ((D - B) mod 38).  This is why Wormmon's shared IDs 6..9 are
# represented by raw values 44..47.  The dedicated table is uint16, contiguous,
# and contains exactly the same 44 logical slots as the CSV/Viewer order.
SELECTOR_HOOK_OFFSET = 0x00086F8A       # runtime word 0x0437C5
SELECTOR_BODY_OFFSET = 0x00086FB2       # runtime word 0x0437D9
SELECTOR_CANDIDATE_OFFSET = 0x00086FB4  # runtime word 0x0437DA
SELECTOR_METADATA_OFFSET = 0x0008700A   # runtime word 0x043805
SELECTOR_COMMON_OFFSET = 0x00087056     # runtime word 0x04382B
SELECTOR_SKIP_OFFSET = 0x00087008       # runtime word 0x043804

# Legacy v1 table.  v7 reuses its first 44 words for the compact raw table.
SELECTOR_TABLE_OFFSET = 0x000ACE00      # physical BIN byte offset
SELECTOR_ROW_WORDS = 64
SELECTOR_LENGTH_WORD = 63
SELECTOR_TABLE_WORDS = 7 * SELECTOR_ROW_WORDS

# This data region is addressed at file_word + 0x9000 at runtime.  The mapping
# is anchored by the stock Partner Table: file byte 0x9D968 is runtime word
# 0x57CB4.  Consequently file byte 0xACE00 is runtime word 0x5F700.
DATA_RUNTIME_BIAS_WORDS = 0x9000
PARTNER_TABLE_RUNTIME_WORD = 0x57CB4

# v7 reuses the start of the old v1 allocation as one contiguous 44-word table.
SELECTOR_RAW_TABLE_OFFSET = SELECTOR_TABLE_OFFSET
SELECTOR_RAW_TABLE_WORDS = ORDER_COUNT
SELECTOR_RAW_TABLE_RUNTIME_WORD = (
    SELECTOR_RAW_TABLE_OFFSET // 2 + DATA_RUNTIME_BIAS_WORDS
)
SELECTOR_RAW_TABLE_SEGMENT = SELECTOR_RAW_TABLE_RUNTIME_WORD >> 16
SELECTOR_RAW_TABLE_LOW_WORD = SELECTOR_RAW_TABLE_RUNTIME_WORD & 0xFFFF
SELECTOR_LINE_BASES = (0, 33, 23, 10, 28, 16, 35)

SELECTOR_STOCK_HOOK = 0x9801
SELECTOR_TAILMON_ONLY_HOOK = 0xEE2F
SELECTOR_GENERIC_V3_HOOK = 0xEE14       # legacy v3: short jmp 0x0437DA
SELECTOR_GENERIC_HOOK = 0xEE16          # v4/v5/v6/v7: short jmp 0x0437DC
SELECTOR_SKIP_WORD = 0xEE26             # 0x043804 -> 0x04382B

# Hardware-unsafe v1 body.  Kept solely for detection and automatic upgrade.
SELECTOR_GENERIC_V1_BODY = (
    0xEE2A,              # 437d9: jmp 43804; existing jmp there reaches 4382b
    0x980F,              # 437da: ld r4,[bp+15]       selected line
    0x995C,              #         ld r4,r4 lsl 4
    0x994C,              #         ld r4,r4 lsl 2     line * 64
    0x970B, 0x6700,      #         ld r3,#0x6700      table low word
    0x0704,              #         add r3,r4           row address
    0xD608,              #         st r3,[bp+8]        preserve row address
    0x067F,              #         add r3,#63          length word
    0x9845,              #         ld r4,#5            table segment
    0xF02C,              #         ds = r4
    0x98E3,              #         ld r4,D:[r3]        line length
    0x9601,              #         ld r3,[bp+1]        scan index
    0x4704,              #         cmp r3,r4
    0x0E03,              #         jb eligible
    0x9840,              #         ld r4,#0
    0xD803,              #         st r4,[bp+3]
    0xEE02,              #         jmp load_candidate
    0x9841,              # eligible: ld r4,#1
    0xD803,              #           st r4,[bp+3]
    0x9608,              # load_candidate: ld r3,[bp+8]
    0x9801,              #         ld r4,[bp+1]
    0x0704,              #         add r3,r4
    0x9845,              #         ld r4,#5
    0xF02C,              #         ds = r4
    0x98E3,              #         ld r4,D:[r3]        candidate Digimon ID
    0xEE6D,              #         jmp 437c7 (backward 45 words)
)

# 0x0437D9..0x0437F8.  The first word skips the displaced slot_type chain after
# the normal partner lookup returns.  The local cave reads line metadata from
# code segment 4, reads valid candidates from the existing flat table in data
# segment 5, supplies harmless ID 0 for out-of-range scan positions, and always
# leaves DS=5 before returning to 0x0437C7.
# Buggy v2 emitted by the immediately previous importer.  Its ``0x0677`` word
# is ``add r3,#0x37`` (55), not ``add r3,#7``.  Keep this signature so affected
# BINs can be identified and repaired automatically.
SELECTOR_GENERIC_V2_BUGGY_BODY = (
    0xEE2A,              # 437d9: jmp 43804; 43804 reaches common 4382b
    0x980F,              # 437da: ld r4,[bp+15]       selected line
    0x970B, 0x3805,      #         ld r3,#0x3805      metadata low word
    0x0704,              #         add r3,r4           start[line]
    0x9844,              #         ld r4,#4
    0xF02C,              #         ds = r4             selector code segment
    0x98E3,              #         ld r4,D:[r3]        flat start index
    0xD808,              #         st r4,[bp+8]
    0x0677,              # BUG:    add r3,#55, not #7
    0x98E3,              #         ld r4,D:[r3]        line length
    0x9601,              #         ld r3,[bp+1]        scan index
    0x4704,              #         cmp r3,r4
    0x0E06,              #         jb eligible
    0x9840,              #         ld r4,#0
    0xD803,              #         st r4,[bp+3]        ineligible
    0x9845,              #         ld r4,#5
    0xF02C,              #         ds = r4             restore expected segment
    0x9840,              #         ld r4,#0            harmless candidate
    0xEE66,              #         jmp 437c7 (backward 38 words)
    0x9841,              # eligible: ld r4,#1
    0xD803,              #           st r4,[bp+3]
    0x9808,              #         ld r4,[bp+8]        flat start index
    0x0801,              #         add r4,[bp+1]       + scan index
    0x9944,              #         ld r4,r4 lsl 1     uint32 word stride
    0x970B, 0x56EF,      #         ld r3,#0x56ef      flat order low word
    0x0704,              #         add r3,r4
    0x9845,              #         ld r4,#5
    0xF02C,              #         ds = r4
    0x98E3,              #         ld r4,D:[r3]        candidate Digimon ID
    0xEE72,              #         jmp 437c7 (backward 50 words)
)

SELECTOR_GENERIC_V2_BODY = list(SELECTOR_GENERIC_V2_BUGGY_BODY)
SELECTOR_GENERIC_V2_BODY[9] = 0x0647  # add r3,#7; immediate is low six bits
SELECTOR_GENERIC_V2_BODY = tuple(SELECTOR_GENERIC_V2_BODY)

SELECTOR_METADATA_WORDS = 14

# Hardware-confirmed three-line inline probe.  It is accepted as an upgrade
# source and replaced by the generated v7 raw-candidate selector.
SELECTOR_INLINE_PROBE_BODY = (
    0xEE2B,
    0x9801, 0x0802, 0x960F,
    0x4641, 0x4E08, 0x486C, 0x5E20, 0x486D, 0x5E1E, 0x486E,
    0x4E1D, 0x986C, 0xEE1B,
    0x4643, 0x4E08, 0x484D, 0x5E16, 0x484E, 0x5E14, 0x484F,
    0x4E13, 0x984D, 0xEE11,
    0x4640, 0x4E0F, 0x4846, 0x5E0C, 0x4847, 0x4E02, 0x9849,
    0xEE09, 0x4848, 0x4E02, 0x9846, 0xEE05, 0x4849, 0x4E03,
    0x9848, 0xEE01, 0x0841, 0xEE7C,
)
SELECTOR_INLINE_PROBE_ELIGIBILITY = (
    0x9604, 0x9805, 0x0642, 0x1840, 0xF02C, 0x98E3,
    0x4852, 0x0E0B, 0x4854, 0x9E09, 0x2852, 0x9944,
    0x960F, 0x4704, 0x5E0A, 0x0841, 0x4704, 0x5E07,
    0xEE03, 0x960F, 0x4903, 0x5E03,
    0x9840, 0xD803, 0xEE0D,
    0x9841, 0xD803, 0xEE0A,
)

# Hardware-confirmed all-seven-line probe generated for D3_test_multi5.  It
# hooks one word later than the older three-line probe and uses 0x43821..82A as
# a small spill block.  v7 recognizes it so the GUI can upgrade that exact BIN.
SELECTOR_ALL7_PROBE_SECOND_HOOK = 0xEE13
SELECTOR_ALL7_PROBE_MAIN = (
    0x960F, 0x4640, 0x5E19, 0x4641, 0x5E20, 0x4642, 0x5E0E, 0x4643,
    0x5E1E, 0x4644, 0x5E0A, 0x4645, 0x4E3A, 0x9644, 0x4903, 0x0E37,
    0x0642, 0x4903, 0x9E34, 0x5E3B, 0xEE34, 0x4842, 0x0E30, 0x4844,
    0x9E2E, 0x4842, 0x5E30, 0xEE31, 0x4846, 0x5E2B, 0x4847, 0x5E2B,
    0x4848, 0x5E2D, 0x4849, 0x4E23, 0xEE28, 0x964B, 0xEE59, 0x9643,
    0xEE5B, 0xEE7D,
)
SELECTOR_ALL7_PROBE_SPILL = (
    0x0802, 0xEE60, 0x0841, 0xEE44, 0x0842,
    0xEE46, 0x2841, 0xEE48, 0x2842, 0xEE4A,
)

SELECTOR_V3_FIRST_WORD = 0x0437D9
SELECTOR_V3_LAST_WORD = 0x04380E
SELECTOR_V3_RESERVED_WORD = 0x043804

SELECTOR_V4_LAST_WORD = 0x043812


def u16(data, off):
    return struct.unpack_from("<H", data, off)[0]


def u32(data, off):
    return struct.unpack_from("<I", data, off)[0]


def put_u16(data, off, value):
    struct.pack_into("<H", data, off, value)


def put_u32(data, off, value):
    struct.pack_into("<I", data, off, value)


def words_at(data, off, count):
    return struct.unpack_from("<" + "H" * count, data, off)


def encode_short_branch(source_word, target_word, forward_base, backward_base):
    """Encode a u'nSP six-bit relative branch between runtime word addresses."""
    delta = target_word - (source_word + 1)
    if delta == 0 or abs(delta) > 0x3F:
        raise RuntimeError(
            f"Internal selector branch out of range: {source_word:#x} -> "
            f"{target_word:#x}"
        )
    if delta > 0:
        return forward_base | delta
    return backward_base | (-delta)


def build_selector_v3_words(lengths):
    """
    Build the generic all-line selector as runtime-address -> uint16.

    The selector calculates a global slot index by adding the lengths of all
    lines before the selected line.  The seven line-end values and the first
    six lengths are instruction immediates, so hardware never has to read
    metadata from the program/code segment.
    """
    lengths = list(lengths)
    if len(lengths) != 7 or any(not 1 <= x <= MAX_CSV_SLOTS for x in lengths):
        raise RuntimeError("Internal selector lengths must be seven values in 1..10")
    if sum(lengths) != ORDER_COUNT:
        raise RuntimeError(
            f"Internal selector lengths cover {sum(lengths)} entries, "
            f"expected {ORDER_COUNT}"
        )

    ends = []
    cursor = 0
    for length in lengths:
        cursor += length
        ends.append(cursor)

    words = {}

    def put(address, value):
        if address in words:
            raise RuntimeError(f"Internal selector overlap at {address:#x}")
        words[address] = value

    def jmp(address, target):
        put(address, encode_short_branch(address, target, 0xEE00, 0xEE40))

    def je(address, target):
        put(address, encode_short_branch(address, target, 0x5E00, 0x5E40))

    def jb(address, target):
        put(address, encode_short_branch(address, target, 0x0E00, 0x0E40))

    # The normal candidate-normalization helper returns to 0x437D9.  Skip to
    # the stock continuation through the original 0x43804 short jump.
    jmp(0x0437D9, 0x043804)

    # Candidate-generation entry and a nearby return trampoline.  The latter
    # keeps both return paths within the six-bit branch range.
    jmp(0x0437DA, 0x0437DC)
    jmp(0x0437DB, 0x0437C7)

    put(0x0437DC, 0x9801)  # ld r4,[bp+1]  scan index
    put(0x0437DD, 0x960F)  # ld r3,[bp+15] selected line

    # For line N, add every preceding line length to r4.  A matching line
    # branches to the corresponding end constant before adding its own length.
    endpoint_addresses = [0x0437F2 + i * 2 for i in range(5)] + [0x0437FC]
    for line_id in range(6):
        address = 0x0437DE + line_id * 3
        put(address, 0x4640 | line_id)          # cmp r3,#line_id
        je(address + 1, endpoint_addresses[line_id])
        put(address + 2, 0x0840 | lengths[line_id])  # add r4,#length

    # Falling through all six checks means Terriermon line 6.
    put(0x0437F0, 0x9640 | ORDER_COUNT)  # ld r3,#44 (line end)
    jmp(0x0437F1, 0x0437FD)

    # End constants for lines 0..5.  The final endpoint falls directly into
    # the common range check.
    for line_id in range(5):
        address = endpoint_addresses[line_id]
        put(address, 0x9640 | ends[line_id])
        jmp(address + 1, 0x0437FD)
    put(endpoint_addresses[5], 0x9640 | ends[5])

    # r4 is the global slot, r3 is this line's exclusive end.
    put(0x0437FD, 0x4903)  # cmp r4,r3
    jb(0x0437FE, 0x043805)

    # Out-of-range scan positions are marked ineligible and use harmless ID 0.
    put(0x0437FF, 0x9840)
    put(0x043800, 0xD803)
    put(0x043801, 0x9840)
    jmp(0x043802, 0x0437DB)

    # Unreachable bridge keeps 0x43804 reserved for the post-helper path.
    jmp(0x043803, 0x043805)
    put(0x043804, SELECTOR_SKIP_WORD)

    # Valid global slot: load uint32 flat_order[slot]'s low word from DS=5.
    put(0x043805, 0x9841)
    put(0x043806, 0xD803)
    put(0x043807, 0x9944)
    put(0x043808, 0x970B)
    put(0x043809, 0x56EF)
    put(0x04380A, 0x0704)
    put(0x04380B, 0x9845)
    put(0x04380C, 0xF02C)
    put(0x04380D, 0x98E3)
    jmp(0x04380E, 0x0437DB)

    return words


def build_selector_v4_words(lengths):
    """
    Build the legacy generic all-line selector v4 as
    runtime-address -> uint16.

    v3 correctly calculated line boundaries and candidate IDs, but left DS=5
    after reading the flat order table.  The stock routine subsequently uses
    DS for Partner Table/name data, so the battle menu rendered ``?`` entries
    on hardware.  v4 restores DS from the current partner-record pointer
    ([bp+5]) before calling the stock helper and again when that helper returns.
    Hardware testing later showed that v4 still damaged the shift buffer while
    doubling the uint32 table index.
    """
    lengths = list(lengths)
    if len(lengths) != 7 or any(not 1 <= x <= MAX_CSV_SLOTS for x in lengths):
        raise RuntimeError("Internal selector lengths must be seven values in 1..10")
    if sum(lengths) != ORDER_COUNT:
        raise RuntimeError(
            f"Internal selector lengths cover {sum(lengths)} entries, "
            f"expected {ORDER_COUNT}"
        )

    ends = []
    cursor = 0
    for length in lengths:
        cursor += length
        ends.append(cursor)

    words = {}

    def put(address, value):
        if address in words:
            raise RuntimeError(f"Internal selector overlap at {address:#x}")
        words[address] = value

    def jmp(address, target):
        put(address, encode_short_branch(address, target, 0xEE00, 0xEE40))

    def je(address, target):
        put(address, encode_short_branch(address, target, 0x5E00, 0x5E40))

    def jb(address, target):
        put(address, encode_short_branch(address, target, 0x0E00, 0x0E40))

    # The stock helper returns here.  Restore the Partner Table/name segment
    # before rejoining the original common continuation through 0x43804.
    put(0x0437D9, 0x9805)  # ld r4,[bp+5]  partner-record high word
    put(0x0437DA, 0xF02C)  # ds = r4
    jmp(0x0437DB, 0x043804)

    put(0x0437DC, 0x9801)  # ld r4,[bp+1]  scan index
    put(0x0437DD, 0x960F)  # ld r3,[bp+15] selected line

    endpoint_addresses = [0x0437F2 + i * 2 for i in range(5)] + [0x0437FC]
    for line_id in range(6):
        address = 0x0437DE + line_id * 3
        put(address, 0x4640 | line_id)
        je(address + 1, endpoint_addresses[line_id])
        put(address + 2, 0x0840 | lengths[line_id])

    put(0x0437F0, 0x9640 | ORDER_COUNT)
    jmp(0x0437F1, 0x0437FD)

    for line_id in range(5):
        address = endpoint_addresses[line_id]
        put(address, 0x9640 | ends[line_id])
        jmp(address + 1, 0x0437FD)
    put(endpoint_addresses[5], 0x9640 | ends[5])

    put(0x0437FD, 0x4903)  # cmp r4,r3
    jb(0x0437FE, 0x043805)

    # Invalid scan positions retain an ineligible flag and harmless ID 0.
    put(0x0437FF, 0x9840)
    put(0x043800, 0xD803)
    put(0x043801, 0x9840)
    jmp(0x043802, 0x0437C7)  # nearby return trampoline

    # Unreachable guard and the stock common-continuation trampoline.
    jmp(0x043803, 0x043805)
    put(0x043804, SELECTOR_SKIP_WORD)

    # Valid global slot: load uint32 flat_order[slot]'s low word from DS=5.
    put(0x043805, 0x9841)
    put(0x043806, 0xD803)
    put(0x043807, 0x9944)
    put(0x043808, 0x970B)
    put(0x043809, 0x56EF)
    put(0x04380A, 0x0704)
    put(0x04380B, 0x9845)
    put(0x04380C, 0xF02C)
    put(0x04380D, 0x98E3)

    # Preserve the candidate while restoring the original partner-record
    # segment before entering the stock normalization helper.
    put(0x04380E, 0xD808)  # st r4,[bp+8]
    put(0x04380F, 0x9805)  # ld r4,[bp+5]
    put(0x043810, 0xF02C)  # ds = r4
    put(0x043811, 0x9808)  # ld r4,[bp+8]
    jmp(0x043812, 0x043802)

    return words


def build_selector_v5_words(lengths):
    """
    Build v5 from v4 while preserving the stock helper's shift-buffer input.

    ``0x9944`` doubles r4 with a shift, but a u'nSP shift also rewrites the
    four-bit SB register.  The stock helper called at 0x0437C7 immediately uses
    SB-dependent shifted operations.  ``0x0904`` (add r4,r4) computes the same
    uint32 table stride without touching SB.
    """
    words = build_selector_v4_words(lengths)
    words[0x043807] = 0x0904  # add r4,r4; preserve SB
    return words


def build_selector_v6_words(lengths):
    """
    Build v6: load a uint16 raw candidate from the dedicated selector table.

    Earlier generic revisions loaded the final Digimon ID and then passed that
    value through the stock normalization helper.  The helper expects the raw
    ``line_base + scan_position`` value instead, so wrapped candidates became
    invalid Partner Table/name records and rendered as ``?``.  v6 stores and
    loads the expected raw values while retaining v5's DS restoration.
    """
    words = build_selector_v4_words(lengths)

    # Replace v4's uint32 flat-order reader with a uint16 raw-candidate reader.
    # r4 already holds the global slot; r3 receives 0x6700 (DS=5), the word
    # address corresponding to byte offset 0x000ACE00.
    replacement = {
        0x043807: 0x970B,  # ld r3,#imm16
        0x043808: 0x6700,
        0x043809: 0x0704,  # add r3,r4
        0x04380A: 0x9845,  # ld r4,#5
        0x04380B: 0xF02C,  # ds = r4
        0x04380C: 0x98E3,  # ld r4,ds:[r3] raw candidate
        0x04380D: 0xD808,  # preserve candidate
        0x04380E: 0x9805,  # partner-record high word
        0x04380F: 0xF02C,  # restore ds
        0x043810: 0x9808,  # restore candidate
    }
    for address in range(0x043807, 0x043813):
        words.pop(address, None)
    words.update(replacement)
    words[0x043811] = encode_short_branch(
        0x043811, 0x043802, 0xEE00, 0xEE40
    )
    return words


def build_selector_v7_words(lengths):
    """
    Build the hardware-confirmed v7 raw-candidate selector.

    v6 had the right raw-candidate representation, but contained two separate
    runtime bugs:

    * ``ld r4,#1`` replaced the calculated global slot before address addition,
      so every entry read raw-table slot 1 and appeared as V-mon.
    * file word 0x56700 was treated as runtime 5:6700.  This firmware maps the
      data region at file_word + 0x9000, so the real address is 5:F700.

    v7 writes the eligibility flag through r3, preserves the global slot in r4,
    reads 5:F700, restores the incoming DS, and uses no shift instruction that
    could disturb the stock helper's shift buffer.
    """
    words = build_selector_v4_words(lengths)

    for address in range(0x043805, 0x043814):
        words.pop(address, None)

    replacement = {
        0x043805: 0x9641,  # ld r3,#1; preserve r4 global slot
        0x043806: 0xD603,  # st r3,[bp+3] eligibility
        0x043807: 0xF023,  # r3 = ds
        0x043808: 0xD609,  # st r3,[bp+9] incoming DS
        0x043809: 0x970B,  # ld r3,#imm16
        0x04380A: SELECTOR_RAW_TABLE_LOW_WORD,
        0x04380B: 0x0704,  # add r3,r4; raw table + global slot
        0x04380C: 0x9840 | SELECTOR_RAW_TABLE_SEGMENT,
        0x04380D: 0xF02C,  # ds = r4
        0x04380E: 0x98E3,  # ld r4,ds:[r3] raw candidate
        0x04380F: 0xD808,  # st r4,[bp+8]
        0x043810: 0x9609,  # ld r3,[bp+9]
        0x043811: 0xF02B,  # ds = r3
        0x043812: 0x9808,  # ld r4,[bp+8]
        0x043813: encode_short_branch(
            0x043813, 0x043802, 0xEE00, 0xEE40
        ),
    }
    words.update(replacement)

    if (
        PARTNER_TABLE_OFFSET // 2 + DATA_RUNTIME_BIAS_WORDS
        != PARTNER_TABLE_RUNTIME_WORD
        or SELECTOR_RAW_TABLE_RUNTIME_WORD != 0x5F700
        or SELECTOR_RAW_TABLE_SEGMENT != 5
        or SELECTOR_RAW_TABLE_LOW_WORD != 0xF700
    ):
        raise RuntimeError("Internal D-3 data-address mapping validation failed")

    return words


def build_selector_raw_candidates(lines):
    """Encode every desired Digimon ID as the raw value expected by the helper."""
    raw = []
    for line_id in range(7):
        base = SELECTOR_LINE_BASES[line_id]
        for digimon_id in lines[line_id]:
            raw.append(base + ((digimon_id - base) % PARTNER_COUNT))

    if len(raw) != SELECTOR_RAW_TABLE_WORDS:
        raise RuntimeError(
            f"Internal selector raw table has {len(raw)} entries, "
            f"expected {SELECTOR_RAW_TABLE_WORDS}"
        )
    if any(not 0 <= value <= 0xFFFF for value in raw):
        raise RuntimeError("Internal selector raw candidate is outside uint16 range")
    return raw


def selector_v3_lengths(data):
    """Return v3 line lengths when its complete generated code validates."""
    lengths = []
    for line_id in range(6):
        address = 0x0437E0 + line_id * 3
        opcode = u16(data, address * 2)
        if opcode & 0xFFC0 != 0x0840:
            return None
        lengths.append(opcode & 0x003F)

    last = ORDER_COUNT - sum(lengths)
    lengths.append(last)
    if any(not 1 <= value <= MAX_CSV_SLOTS for value in lengths):
        return None

    expected = build_selector_v3_words(lengths)
    if any(u16(data, address * 2) != value for address, value in expected.items()):
        return None
    return lengths


def selector_v4_lengths(data):
    """Return v4 line lengths when its complete generated code validates."""
    lengths = []
    for line_id in range(6):
        address = 0x0437E0 + line_id * 3
        opcode = u16(data, address * 2)
        if opcode & 0xFFC0 != 0x0840:
            return None
        lengths.append(opcode & 0x003F)

    last = ORDER_COUNT - sum(lengths)
    lengths.append(last)
    if any(not 1 <= value <= MAX_CSV_SLOTS for value in lengths):
        return None

    expected = build_selector_v4_words(lengths)
    if any(u16(data, address * 2) != value for address, value in expected.items()):
        return None
    return lengths


def selector_v5_lengths(data):
    """Return v5 line lengths when its complete generated code validates."""
    lengths = []
    for line_id in range(6):
        address = 0x0437E0 + line_id * 3
        opcode = u16(data, address * 2)
        if opcode & 0xFFC0 != 0x0840:
            return None
        lengths.append(opcode & 0x003F)

    last = ORDER_COUNT - sum(lengths)
    lengths.append(last)
    if any(not 1 <= value <= MAX_CSV_SLOTS for value in lengths):
        return None

    expected = build_selector_v5_words(lengths)
    if any(u16(data, address * 2) != value for address, value in expected.items()):
        return None
    return lengths


def selector_v6_lengths(data):
    """Return v6 line lengths when its complete generated code validates."""
    lengths = []
    for line_id in range(6):
        address = 0x0437E0 + line_id * 3
        opcode = u16(data, address * 2)
        if opcode & 0xFFC0 != 0x0840:
            return None
        lengths.append(opcode & 0x003F)

    last = ORDER_COUNT - sum(lengths)
    lengths.append(last)
    if any(not 1 <= value <= MAX_CSV_SLOTS for value in lengths):
        return None

    expected = build_selector_v6_words(lengths)
    if any(u16(data, address * 2) != value for address, value in expected.items()):
        return None
    return lengths


def selector_v7_lengths(data):
    """Return v7 line lengths when its complete generated code validates."""
    lengths = []
    for line_id in range(6):
        address = 0x0437E0 + line_id * 3
        opcode = u16(data, address * 2)
        if opcode & 0xFFC0 != 0x0840:
            return None
        lengths.append(opcode & 0x003F)

    last = ORDER_COUNT - sum(lengths)
    lengths.append(last)
    if any(not 1 <= value <= MAX_CSV_SLOTS for value in lengths):
        return None

    expected = build_selector_v7_words(lengths)
    if any(u16(data, address * 2) != value for address, value in expected.items()):
        return None
    return lengths


def is_all7_inline_probe(data):
    return (
        u16(data, SELECTOR_HOOK_OFFSET) == SELECTOR_STOCK_HOOK
        and u16(data, SELECTOR_HOOK_OFFSET + 2) == SELECTOR_ALL7_PROBE_SECOND_HOOK
        and u16(data, SELECTOR_BODY_OFFSET) == 0xEE2B
        and words_at(data, SELECTOR_CANDIDATE_OFFSET, len(SELECTOR_ALL7_PROBE_MAIN))
        == SELECTOR_ALL7_PROBE_MAIN
        and words_at(data, 0x043821 * 2, len(SELECTOR_ALL7_PROBE_SPILL))
        == SELECTOR_ALL7_PROBE_SPILL
        and words_at(
            data,
            SELECTOR_METADATA_OFFSET,
            len(SELECTOR_INLINE_PROBE_ELIGIBILITY),
        ) == SELECTOR_INLINE_PROBE_ELIGIBILITY
    )


def selector_patch_state(data):
    """Identify stock and every selector revision that can be upgraded."""
    hook = u16(data, SELECTOR_HOOK_OFFSET)
    body0 = u16(data, SELECTOR_BODY_OFFSET)

    if hook == SELECTOR_GENERIC_HOOK:
        if selector_v7_lengths(data) is not None:
            return "generic-v7"
        if selector_v6_lengths(data) is not None:
            return "generic-v6"
        if selector_v5_lengths(data) is not None:
            return "generic-v5"
        if selector_v4_lengths(data) is not None:
            return "generic-v4"
        raise RuntimeError(
            "The v4/v5/v6/v7 battle-selector hook is present, but its in-place "
            "body "
            "does not match the supported patch."
        )

    if is_all7_inline_probe(data):
        return "inline-all7-probe"

    if hook == SELECTOR_GENERIC_V3_HOOK:
        if selector_v3_lengths(data) is not None:
            return "generic-v3"

        probe = words_at(data, SELECTOR_BODY_OFFSET, len(SELECTOR_INLINE_PROBE_BODY))
        probe_eligibility = words_at(
            data,
            SELECTOR_METADATA_OFFSET,
            len(SELECTOR_INLINE_PROBE_ELIGIBILITY),
        )
        if (
            probe == SELECTOR_INLINE_PROBE_BODY
            and probe_eligibility == SELECTOR_INLINE_PROBE_ELIGIBILITY
        ):
            return "inline-three-line-probe"

        v2 = words_at(data, SELECTOR_BODY_OFFSET, len(SELECTOR_GENERIC_V2_BODY))
        if v2 == SELECTOR_GENERIC_V2_BODY:
            return "generic-v2"

        buggy_v2 = words_at(
            data, SELECTOR_BODY_OFFSET, len(SELECTOR_GENERIC_V2_BUGGY_BODY)
        )
        if buggy_v2 == SELECTOR_GENERIC_V2_BUGGY_BODY:
            return "generic-v2-buggy"

        v1 = words_at(data, SELECTOR_BODY_OFFSET, len(SELECTOR_GENERIC_V1_BODY))
        if v1 == SELECTOR_GENERIC_V1_BODY:
            return "generic-v1"

        raise RuntimeError(
            "The generic battle-selector hook is present, but its in-place "
            "body does not match a supported patch version."
        )

    if hook == SELECTOR_STOCK_HOOK and body0 == 0x9604:
        return "stock"

    if hook == SELECTOR_TAILMON_ONLY_HOOK and body0 == 0x9604:
        return "tailmon-only"

    raise RuntimeError(
        "Unsupported battle-selector code at "
        f"0x{SELECTOR_HOOK_OFFSET:08X}: hook=0x{hook:04X}, "
        f"body=0x{body0:04X}. Restore a stock or supported in-place-patched "
        "D-3 BIN before importing Evolution Slots."
    )


def validate_selector_storage(data, state):
    if state == "generic-v7":
        if selector_v7_lengths(data) is None:
            raise RuntimeError("Generic selector v7 code did not validate")
        raw = words_at(
            data,
            SELECTOR_RAW_TABLE_OFFSET,
            SELECTOR_RAW_TABLE_WORDS,
        )
        if any(value >= PARTNER_COUNT * 2 for value in raw):
            raise RuntimeError("Generic selector v7 contains an invalid raw candidate")

    if state == "generic-v6":
        if selector_v6_lengths(data) is None:
            raise RuntimeError("Generic selector v6 code did not validate")
        raw = words_at(
            data,
            SELECTOR_RAW_TABLE_OFFSET,
            SELECTOR_RAW_TABLE_WORDS,
        )
        if any(value >= PARTNER_COUNT * 2 for value in raw):
            raise RuntimeError("Generic selector v6 contains an invalid raw candidate")

    if state == "generic-v5":
        if selector_v5_lengths(data) is None:
            raise RuntimeError("Generic selector v5 code did not validate")

    if state == "generic-v4":
        if selector_v4_lengths(data) is None:
            raise RuntimeError("Generic selector v4 code did not validate")

    if state == "generic-v3":
        if selector_v3_lengths(data) is None:
            raise RuntimeError("Generic selector v3 code did not validate")

    if state == "generic-v1":
        end = SELECTOR_TABLE_OFFSET + SELECTOR_TABLE_WORDS * 2
        if len(data) < end:
            raise RuntimeError("BIN is too small for the legacy selector table")
        for line_id in range(7):
            row = SELECTOR_TABLE_OFFSET + line_id * SELECTOR_ROW_WORDS * 2
            length = u16(data, row + SELECTOR_LENGTH_WORD * 2)
            if not 1 <= length <= MAX_CSV_SLOTS:
                raise RuntimeError(
                    f"Generic selector row {line_id} has invalid length {length}"
                )
            ids = list(words_at(data, row, length))
            if any(not 0 <= value < PARTNER_COUNT for value in ids):
                raise RuntimeError(
                    f"Generic selector row {line_id} contains an invalid Digimon ID"
                )
            if len(set(ids)) != len(ids):
                raise RuntimeError(
                    f"Generic selector row {line_id} contains duplicate Digimon IDs"
                )

    if state in {"generic-v2", "generic-v2-buggy"}:
        metadata = words_at(data, SELECTOR_METADATA_OFFSET, SELECTOR_METADATA_WORDS)
        starts = metadata[:7]
        lengths = metadata[7:]
        expected_start = 0
        for line_id, (start, length) in enumerate(zip(starts, lengths)):
            if start != expected_start:
                raise RuntimeError(
                    f"Generic selector v2 line {line_id} starts at {start}, "
                    f"expected {expected_start}"
                )
            if not 1 <= length <= MAX_CSV_SLOTS:
                raise RuntimeError(
                    f"Generic selector v2 line {line_id} has invalid length {length}"
                )
            expected_start += length
        if expected_start != ORDER_COUNT:
            raise RuntimeError(
                f"Generic selector v2 metadata covers {expected_start} entries, "
                f"expected {ORDER_COUNT}"
            )


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

    selector_state = selector_patch_state(data)
    validate_selector_storage(data, selector_state)

    if u16(data, SELECTOR_SKIP_OFFSET) != SELECTOR_SKIP_WORD:
        raise RuntimeError("Battle-selector skip signature did not match")

    if u16(data, SELECTOR_COMMON_OFFSET) != 0x9803:
        raise RuntimeError("Battle-selector common continuation signature did not match")

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

    return selector_state


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


def build_selector_lengths(lines):
    """Build the seven complete-line lengths encoded by selector v7."""
    lengths = []

    for line_id in range(7):
        length = len(lines[line_id])
        if not 1 <= length <= MAX_CSV_SLOTS:
            raise RuntimeError(
                f"{LINE_NAMES[line_id]} has unsupported selector length {length}"
            )
        lengths.append(length)

    if sum(lengths) != ORDER_COUNT:
        raise RuntimeError("Internal selector length error")

    return lengths


def apply_changes(data, lines, targets, partner_slot_types, selector_lengths, selector_state):
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

    # ------------------------------------------------------------------
    # 4) Generic all-line battle-selector scan order, generated v7
    # ------------------------------------------------------------------
    old = u16(data, SELECTOR_HOOK_OFFSET)
    if old != SELECTOR_GENERIC_HOOK:
        put_u16(data, SELECTOR_HOOK_OFFSET, SELECTOR_GENERIC_HOOK)
        changes.append(("selector.code", SELECTOR_HOOK_OFFSET, old, SELECTOR_GENERIC_HOOK))

    # The hardware-confirmed all-seven probe hooks at the second stock word.
    # v7 returns that word to its stock candidate-base addition.
    second_hook_offset = SELECTOR_HOOK_OFFSET + 2
    old = u16(data, second_hook_offset)
    if old != 0x0802:
        put_u16(data, second_hook_offset, 0x0802)
        changes.append(("selector.code", second_hook_offset, old, 0x0802))

    selector_words = build_selector_v7_words(selector_lengths)
    for address, new in sorted(selector_words.items()):
        off = address * 2
        old = u16(data, off)
        if old != new:
            put_u16(data, off, new)
            changes.append(("selector.code", off, old, new))

    # v7's table stores raw helper inputs, not final Digimon IDs.
    raw_candidates = build_selector_raw_candidates(lines)
    for i, new in enumerate(raw_candidates):
        off = SELECTOR_RAW_TABLE_OFFSET + i * 2
        old = u16(data, off)
        if old != new:
            put_u16(data, off, new)
            changes.append(("selector.raw_table", off, old, new))

    # Remove the unused tail of a recognized v1 allocation.  Preserve the
    # first 44 words, which now hold the v7 raw-candidate table.
    if selector_state == "generic-v1":
        for i in range(SELECTOR_RAW_TABLE_WORDS, SELECTOR_TABLE_WORDS):
            off = SELECTOR_TABLE_OFFSET + i * 2
            old = u16(data, off)
            if old:
                put_u16(data, off, 0)
                changes.append(("selector.v1_cleanup", off, old, 0))

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
    selector_changes = [x for x in changes if x[0].startswith("selector")]
    field_changes = [x for x in changes if not x[0].startswith("selector")]

    print("=== Battle-selector order (generated from CSV) ===")
    for line_id in range(7):
        print(f"  {line_id} {LINE_NAMES[line_id]}: {lines[line_id]}")

    print()
    print(f"=== Planned BIN field changes: {len(changes)} ===")
    for kind, off, old, new in field_changes:
        old_disp = "FFFF" if old == 0xFFFF else str(old)
        new_disp = "FFFF" if new == 0xFFFF else str(new)
        print(f"  {kind:24s} 0x{off:08X}: {old_disp} -> {new_disp}")

    if selector_changes:
        code_count = sum(1 for x in selector_changes if x[0] == "selector.code")
        raw_count = sum(1 for x in selector_changes if x[0] == "selector.raw_table")
        cleanup_count = sum(1 for x in selector_changes if x[0] == "selector.v1_cleanup")
        print(
            "  battle selector runtime : "
            f"{code_count} inline code word(s), "
            f"{raw_count} raw table word(s), "
            f"{cleanup_count} unused legacy word(s) cleared"
        )


def main():
    ap = argparse.ArgumentParser(
        description=(
            "Import simple D3 evolution-line CSV and synchronize flat order, "
            "target arrays, Partner Table slot_type, and battle-selector order"
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

    selector_state = validate_bin(data)
    lines = load_csv(args.csv)
    validate_membership(lines)

    targets = derive_target_arrays(data, lines)
    partner_slot_types = derive_partner_slot_types(data, lines)
    selector_lengths = build_selector_lengths(lines)

    before = sha256_bytes(data)
    changes = apply_changes(
        data,
        lines,
        targets,
        partner_slot_types,
        selector_lengths,
        selector_state,
    )

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
    print("Battle-selector order now follows the complete CSV line order.")
    print("evo_animation1..5 were NOT modified.")


if __name__ == "__main__":
    main()
