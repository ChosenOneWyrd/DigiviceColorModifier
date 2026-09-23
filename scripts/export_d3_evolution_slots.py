#!/usr/bin/env python3
"""
export_d3_evolution_slots.py

Export the D-3 25th Color complete evolution-line membership/order to a simple CSV:

    line_id,line_name,slot_1,...,slot_10

Line names:
    0 Vmon Line
    1 Wormmon Line
    2 Hawkmon Line
    3 Tailmon Line
    4 Armadimon Line
    5 Patamon Line
    6 Terriermon Line

This reads the actual D3 structures rather than assuming the original stock
line lengths.  It reconstructs each line from:
  - Partner Table slot_type membership
  - known shared-evolution slot types 18/19/20
  - the 44-entry flat evolution-order table at 0x000AADDE

That means it can also export a BIN in which, for example, one normal Digimon
has been moved from Tailmon Line to Wormmon Line and the line lengths changed.
For BINs using the generic battle-selector patch, it also verifies the inline
line-boundary instructions and v7's 44-word raw-candidate table.  That table
encodes the pre-normalized values expected by the stock helper, including
wrapped shared/Jogress candidates, while the normal flat table remains the
Viewer's final Digimon-ID order.  Its runtime reader uses the firmware's
verified +0x9000-word data mapping.  Older patches remain readable so the GUI
can upgrade them on save.

Usage:
    python export_d3_evolution_slots.py D3.bin d3_evolution_slots.csv
"""

import argparse
import csv
import struct
from pathlib import Path

GP_HEADER = b"GP-SPIF-HEADER"

ORDER_OFFSET = 0x000AADDE
ORDER_COUNT = 44

PARTNER_TABLE_OFFSET = 0x0009D968
PARTNER_RECORD_SIZE = 0x20
PARTNER_COUNT = 38
P_DIGIMON_ID = 1
P_SLOT_TYPE = 2

LINE_DEFS = [
    (0, "Vmon Line"),
    (1, "Wormmon Line"),
    (2, "Hawkmon Line"),
    (3, "Tailmon Line"),
    (4, "Armadimon Line"),
    (5, "Patamon Line"),
    (6, "Terriermon Line"),
]

SPECIAL_SLOT_LINES = {
    18: (0, 1),  # Paildramon / Imperialdramon forms
    19: (2, 3),  # Silphymon
    20: (4, 5),  # Shakkoumon
}

MAX_CSV_SLOTS = 10

ORDER_PRE_OFFSET = 0x000AADDA
ORDER_PRE_BYTES = bytes.fromhex("C3 01 C4 01")
ORDER_POST_OFFSET = ORDER_OFFSET + ORDER_COUNT * 4
ORDER_POST_BYTES = bytes.fromhex(
    "6B 6D 03 00 87 6D 03 00 9A 6F 03 00 D2 74 03 00"
)

SELECTOR_HOOK_OFFSET = 0x00086F8A
SELECTOR_BODY_OFFSET = 0x00086FB2
SELECTOR_METADATA_OFFSET = 0x0008700A
SELECTOR_TABLE_OFFSET = 0x000ACE00
SELECTOR_ROW_WORDS = 64
SELECTOR_LENGTH_WORD = 63
SELECTOR_RAW_TABLE_OFFSET = SELECTOR_TABLE_OFFSET
SELECTOR_RAW_TABLE_WORDS = ORDER_COUNT
DATA_RUNTIME_BIAS_WORDS = 0x9000
PARTNER_TABLE_RUNTIME_WORD = 0x57CB4
SELECTOR_RAW_TABLE_RUNTIME_WORD = (
    SELECTOR_RAW_TABLE_OFFSET // 2 + DATA_RUNTIME_BIAS_WORDS
)
SELECTOR_RAW_TABLE_SEGMENT = SELECTOR_RAW_TABLE_RUNTIME_WORD >> 16
SELECTOR_RAW_TABLE_LOW_WORD = SELECTOR_RAW_TABLE_RUNTIME_WORD & 0xFFFF
SELECTOR_LINE_BASES = (0, 33, 23, 10, 28, 16, 35)
SELECTOR_GENERIC_V3_HOOK = 0xEE14
SELECTOR_GENERIC_HOOK = 0xEE16
SELECTOR_GENERIC_V1_BODY = (
    0xEE2A,
    0x980F, 0x995C, 0x994C, 0x970B, 0x6700, 0x0704, 0xD608,
    0x067F, 0x9845, 0xF02C, 0x98E3, 0x9601, 0x4704, 0x0E03,
    0x9840, 0xD803, 0xEE02, 0x9841, 0xD803, 0x9608, 0x9801,
    0x0704, 0x9845, 0xF02C, 0x98E3, 0xEE6D,
)
SELECTOR_GENERIC_V2_BUGGY_BODY = (
    0xEE2A,
    0x980F, 0x970B, 0x3805, 0x0704, 0x9844, 0xF02C, 0x98E3,
    0xD808, 0x0677, 0x98E3, 0x9601, 0x4704, 0x0E06, 0x9840,
    0xD803, 0x9845, 0xF02C, 0x9840, 0xEE66, 0x9841, 0xD803,
    0x9808, 0x0801, 0x9944, 0x970B, 0x56EF, 0x0704, 0x9845,
    0xF02C, 0x98E3, 0xEE72,
)
SELECTOR_GENERIC_V2_BODY = list(SELECTOR_GENERIC_V2_BUGGY_BODY)
SELECTOR_GENERIC_V2_BODY[9] = 0x0647
SELECTOR_GENERIC_V2_BODY = tuple(SELECTOR_GENERIC_V2_BODY)
SELECTOR_METADATA_WORDS = 14
SELECTOR_SKIP_WORD = 0xEE26

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


def u16(data, off):
    return struct.unpack_from("<H", data, off)[0]


def words_at(data, off, count):
    return struct.unpack_from("<" + "H" * count, data, off)


def encode_short_branch(source_word, target_word, forward_base, backward_base):
    delta = target_word - (source_word + 1)
    if delta == 0 or abs(delta) > 0x3F:
        raise RuntimeError("Internal selector branch is out of range")
    return (forward_base | delta) if delta > 0 else (backward_base | (-delta))


def build_selector_v3_words(lengths):
    lengths = list(lengths)
    if len(lengths) != 7 or any(not 1 <= x <= MAX_CSV_SLOTS for x in lengths):
        raise RuntimeError("Selector lengths must be seven values in 1..10")
    if sum(lengths) != ORDER_COUNT:
        raise RuntimeError("Selector lengths must cover exactly 44 slots")

    ends = []
    cursor = 0
    for length in lengths:
        cursor += length
        ends.append(cursor)

    words = {}

    def put(address, value):
        words[address] = value

    def branch(address, target, forward_base, backward_base):
        put(
            address,
            encode_short_branch(address, target, forward_base, backward_base),
        )

    branch(0x0437D9, 0x043804, 0xEE00, 0xEE40)
    branch(0x0437DA, 0x0437DC, 0xEE00, 0xEE40)
    branch(0x0437DB, 0x0437C7, 0xEE00, 0xEE40)
    put(0x0437DC, 0x9801)
    put(0x0437DD, 0x960F)

    endpoints = [0x0437F2 + i * 2 for i in range(5)] + [0x0437FC]
    for line_id in range(6):
        address = 0x0437DE + line_id * 3
        put(address, 0x4640 | line_id)
        branch(address + 1, endpoints[line_id], 0x5E00, 0x5E40)
        put(address + 2, 0x0840 | lengths[line_id])

    put(0x0437F0, 0x9640 | ORDER_COUNT)
    branch(0x0437F1, 0x0437FD, 0xEE00, 0xEE40)
    for line_id in range(5):
        address = endpoints[line_id]
        put(address, 0x9640 | ends[line_id])
        branch(address + 1, 0x0437FD, 0xEE00, 0xEE40)
    put(endpoints[5], 0x9640 | ends[5])

    put(0x0437FD, 0x4903)
    branch(0x0437FE, 0x043805, 0x0E00, 0x0E40)
    put(0x0437FF, 0x9840)
    put(0x043800, 0xD803)
    put(0x043801, 0x9840)
    branch(0x043802, 0x0437DB, 0xEE00, 0xEE40)
    branch(0x043803, 0x043805, 0xEE00, 0xEE40)
    put(0x043804, SELECTOR_SKIP_WORD)
    put(0x043805, 0x9841)
    put(0x043806, 0xD803)
    put(0x043807, 0x9944)
    put(0x043808, 0x970B)
    put(0x043809, 0x56EF)
    put(0x04380A, 0x0704)
    put(0x04380B, 0x9845)
    put(0x04380C, 0xF02C)
    put(0x04380D, 0x98E3)
    branch(0x04380E, 0x0437DB, 0xEE00, 0xEE40)
    return words


def build_selector_v4_words(lengths):
    lengths = list(lengths)
    if len(lengths) != 7 or any(not 1 <= x <= MAX_CSV_SLOTS for x in lengths):
        raise RuntimeError("Selector lengths must be seven values in 1..10")
    if sum(lengths) != ORDER_COUNT:
        raise RuntimeError("Selector lengths must cover exactly 44 slots")

    ends = []
    cursor = 0
    for length in lengths:
        cursor += length
        ends.append(cursor)

    words = {}

    def put(address, value):
        words[address] = value

    def branch(address, target, forward_base, backward_base):
        put(
            address,
            encode_short_branch(address, target, forward_base, backward_base),
        )

    # Restore the Partner Table/name data segment after the stock helper.
    put(0x0437D9, 0x9805)
    put(0x0437DA, 0xF02C)
    branch(0x0437DB, 0x043804, 0xEE00, 0xEE40)

    put(0x0437DC, 0x9801)
    put(0x0437DD, 0x960F)

    endpoints = [0x0437F2 + i * 2 for i in range(5)] + [0x0437FC]
    for line_id in range(6):
        address = 0x0437DE + line_id * 3
        put(address, 0x4640 | line_id)
        branch(address + 1, endpoints[line_id], 0x5E00, 0x5E40)
        put(address + 2, 0x0840 | lengths[line_id])

    put(0x0437F0, 0x9640 | ORDER_COUNT)
    branch(0x0437F1, 0x0437FD, 0xEE00, 0xEE40)
    for line_id in range(5):
        address = endpoints[line_id]
        put(address, 0x9640 | ends[line_id])
        branch(address + 1, 0x0437FD, 0xEE00, 0xEE40)
    put(endpoints[5], 0x9640 | ends[5])

    put(0x0437FD, 0x4903)
    branch(0x0437FE, 0x043805, 0x0E00, 0x0E40)
    put(0x0437FF, 0x9840)
    put(0x043800, 0xD803)
    put(0x043801, 0x9840)
    branch(0x043802, 0x0437C7, 0xEE00, 0xEE40)
    branch(0x043803, 0x043805, 0xEE00, 0xEE40)
    put(0x043804, SELECTOR_SKIP_WORD)

    put(0x043805, 0x9841)
    put(0x043806, 0xD803)
    put(0x043807, 0x9944)
    put(0x043808, 0x970B)
    put(0x043809, 0x56EF)
    put(0x04380A, 0x0704)
    put(0x04380B, 0x9845)
    put(0x04380C, 0xF02C)
    put(0x04380D, 0x98E3)

    # Preserve the candidate while restoring DS before the stock helper.
    put(0x04380E, 0xD808)
    put(0x04380F, 0x9805)
    put(0x043810, 0xF02C)
    put(0x043811, 0x9808)
    branch(0x043812, 0x043802, 0xEE00, 0xEE40)
    return words


def build_selector_v5_words(lengths):
    """Build v5 without clobbering the stock helper's shift buffer."""
    words = build_selector_v4_words(lengths)
    words[0x043807] = 0x0904  # add r4,r4; same stride, SB unchanged
    return words


def build_selector_v6_words(lengths):
    """Build the uint16 raw-candidate selector reader used by v6."""
    words = build_selector_v4_words(lengths)
    for address in range(0x043807, 0x043813):
        words.pop(address, None)
    words.update({
        0x043807: 0x970B,
        0x043808: 0x6700,
        0x043809: 0x0704,
        0x04380A: 0x9845,
        0x04380B: 0xF02C,
        0x04380C: 0x98E3,
        0x04380D: 0xD808,
        0x04380E: 0x9805,
        0x04380F: 0xF02C,
        0x043810: 0x9808,
        0x043811: encode_short_branch(
            0x043811, 0x043802, 0xEE00, 0xEE40
        ),
    })
    return words


def build_selector_v7_words(lengths):
    """Build the hardware-confirmed v7 raw-candidate selector reader."""
    words = build_selector_v4_words(lengths)
    for address in range(0x043805, 0x043814):
        words.pop(address, None)
    words.update({
        0x043805: 0x9641,  # eligibility through r3; retain r4 global slot
        0x043806: 0xD603,
        0x043807: 0xF023,  # preserve incoming DS
        0x043808: 0xD609,
        0x043809: 0x970B,
        0x04380A: SELECTOR_RAW_TABLE_LOW_WORD,
        0x04380B: 0x0704,
        0x04380C: 0x9840 | SELECTOR_RAW_TABLE_SEGMENT,
        0x04380D: 0xF02C,
        0x04380E: 0x98E3,
        0x04380F: 0xD808,
        0x043810: 0x9609,
        0x043811: 0xF02B,
        0x043812: 0x9808,
        0x043813: encode_short_branch(
            0x043813, 0x043802, 0xEE00, 0xEE40
        ),
    })
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
    raw = []
    for line_id, _ in LINE_DEFS:
        base = SELECTOR_LINE_BASES[line_id]
        raw.extend(
            base + ((digimon_id - base) % PARTNER_COUNT)
            for digimon_id in lines[line_id]
        )
    if len(raw) != SELECTOR_RAW_TABLE_WORDS:
        raise RuntimeError("Selector raw table must contain exactly 44 words")
    return raw


def is_all7_inline_probe(data):
    return (
        u16(data, SELECTOR_HOOK_OFFSET) == 0x9801
        and u16(data, SELECTOR_HOOK_OFFSET + 2) == SELECTOR_ALL7_PROBE_SECOND_HOOK
        and u16(data, SELECTOR_BODY_OFFSET) == 0xEE2B
        and words_at(data, 0x0437DA * 2, len(SELECTOR_ALL7_PROBE_MAIN))
        == SELECTOR_ALL7_PROBE_MAIN
        and words_at(data, 0x043821 * 2, len(SELECTOR_ALL7_PROBE_SPILL))
        == SELECTOR_ALL7_PROBE_SPILL
        and words_at(
            data,
            SELECTOR_METADATA_OFFSET,
            len(SELECTOR_INLINE_PROBE_ELIGIBILITY),
        ) == SELECTOR_INLINE_PROBE_ELIGIBILITY
    )


def read_order(data):
    return list(struct.unpack_from("<" + "I" * ORDER_COUNT, data, ORDER_OFFSET))


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

    for digimon_id in range(PARTNER_COUNT):
        base = PARTNER_TABLE_OFFSET + digimon_id * PARTNER_RECORD_SIZE
        stored_id = u16(data, base + P_DIGIMON_ID * 2)
        if stored_id != digimon_id:
            raise RuntimeError(
                f"Partner Table identity mismatch at record {digimon_id}: "
                f"stored digimon_id={stored_id}"
            )


def derive_expected_line_members(data):
    """
    Build the membership SET for each line from Partner Table slot_type.

    Direct slot_type 0..6:
        belongs to exactly that line.

    Shared slot_type:
        18 -> Vmon + Wormmon
        19 -> Hawkmon + Tailmon
        20 -> Armadimon + Patamon
    """
    members = {line_id: set() for line_id, _ in LINE_DEFS}

    for digimon_id in range(PARTNER_COUNT):
        base = PARTNER_TABLE_OFFSET + digimon_id * PARTNER_RECORD_SIZE
        slot_type = u16(data, base + P_SLOT_TYPE * 2)

        if 0 <= slot_type <= 6:
            members[slot_type].add(digimon_id)
        elif slot_type in SPECIAL_SLOT_LINES:
            for line_id in SPECIAL_SLOT_LINES[slot_type]:
                members[line_id].add(digimon_id)
        else:
            raise RuntimeError(
                f"Unsupported Partner Table slot_type={slot_type} for "
                f"digimon_id={digimon_id}. This exporter currently understands "
                "direct line types 0..6 and shared types 18/19/20."
            )

    total_occurrences = sum(len(v) for v in members.values())
    if total_occurrences != ORDER_COUNT:
        raise RuntimeError(
            f"Partner-derived line membership contains {total_occurrences} "
            f"occurrences, but the D3 flat order table has {ORDER_COUNT}."
        )

    return members


def split_flat_order_by_membership(data):
    """
    The flat order is line-concatenated.  Dynamic line lengths can be recovered
    from Partner Table membership counts.

    We then verify that each physical segment contains exactly the IDs expected
    for that line.  This catches inconsistent BINs instead of silently exporting
    a misleading CSV.
    """
    flat = read_order(data)

    for i, value in enumerate(flat):
        if not 0 <= value < PARTNER_COUNT:
            raise RuntimeError(
                f"Unexpected flat-order value 0x{value:08X} at global slot {i}"
            )

    expected = derive_expected_line_members(data)

    result = {}
    cursor = 0

    for line_id, line_name in LINE_DEFS:
        count = len(expected[line_id])
        segment = flat[cursor:cursor + count]

        if len(segment) != count:
            raise RuntimeError(
                f"Flat order ended while reading {line_name}: "
                f"wanted {count} entries"
            )

        if len(set(segment)) != len(segment):
            raise RuntimeError(
                f"{line_name} flat segment contains duplicate IDs: {segment}"
            )

        if set(segment) != expected[line_id]:
            missing = sorted(expected[line_id] - set(segment))
            extra = sorted(set(segment) - expected[line_id])
            raise RuntimeError(
                f"{line_name} flat-order segment does not match Partner Table "
                f"membership.\n  segment={segment}\n  missing={missing}\n  extra={extra}"
            )

        if count > MAX_CSV_SLOTS:
            raise RuntimeError(
                f"{line_name} has {count} entries, but this CSV format supports "
                f"only {MAX_CSV_SLOTS} slots per line."
            )

        result[line_id] = segment
        cursor += count

    if cursor != ORDER_COUNT:
        raise RuntimeError(
            f"Internal split error: consumed {cursor} of {ORDER_COUNT} flat entries"
        )

    return result


def validate_generic_selector_order(data, lines):
    """Verify v7 code/table or recognize an upgradeable older patch."""
    hook = u16(data, SELECTOR_HOOK_OFFSET)

    if is_all7_inline_probe(data):
        return "hardware-confirmed all-seven inline probe; saving upgrades it to v7"

    if hook not in {SELECTOR_GENERIC_HOOK, SELECTOR_GENERIC_V3_HOOK}:
        return "stock/legacy"

    lengths = [len(lines[line_id]) for line_id, _ in LINE_DEFS]
    if hook == SELECTOR_GENERIC_HOOK:
        expected_v7 = build_selector_v7_words(lengths)
        if all(u16(data, address * 2) == value for address, value in expected_v7.items()):
            expected_raw = build_selector_raw_candidates(lines)
            stored_raw = list(words_at(
                data,
                SELECTOR_RAW_TABLE_OFFSET,
                SELECTOR_RAW_TABLE_WORDS,
            ))
            if stored_raw != expected_raw:
                raise RuntimeError(
                    "v7 battle-selector raw table does not match the complete "
                    f"line order.\n  selector={stored_raw}\n  expected={expected_raw}"
                )
            return "hardware-confirmed v7 raw-candidate selector verified"

        expected_v6 = build_selector_v6_words(lengths)
        if all(u16(data, address * 2) == value for address, value in expected_v6.items()):
            expected_raw = build_selector_raw_candidates(lines)
            stored_raw = list(words_at(
                data,
                SELECTOR_RAW_TABLE_OFFSET,
                SELECTOR_RAW_TABLE_WORDS,
            ))
            if stored_raw != expected_raw:
                raise RuntimeError(
                    "v6 battle-selector raw table does not match the complete "
                    f"line order.\n  selector={stored_raw}\n  expected={expected_raw}"
                )
            return (
                "v6 slot-register/runtime-address bugs detected; "
                "saving upgrades it to v7"
            )

        expected_v5 = build_selector_v5_words(lengths)
        if all(u16(data, address * 2) == value for address, value in expected_v5.items()):
            return "v5 final-ID helper-input bug detected; saving upgrades it to v7"

        expected_v4 = build_selector_v4_words(lengths)
        if all(u16(data, address * 2) == value for address, value in expected_v4.items()):
            return "v4 shift-buffer bug detected; saving upgrades it to v7"
        raise RuntimeError(
            "The v4/v5/v6/v7 battle-selector hook is present, but its in-place "
            "body does not match the supported patch."
        )

    expected_v3 = build_selector_v3_words(lengths)
    if all(u16(data, address * 2) == value for address, value in expected_v3.items()):
        return "v3 DS-restore bug detected; saving upgrades it to v7"

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
        return "three-line inline probe detected; saving upgrades it to v7"

    actual_v2 = words_at(
        data, SELECTOR_BODY_OFFSET, len(SELECTOR_GENERIC_V2_BODY)
    )
    if actual_v2 in {
        SELECTOR_GENERIC_V2_BODY,
        SELECTOR_GENERIC_V2_BUGGY_BODY,
    }:
        metadata = words_at(
            data, SELECTOR_METADATA_OFFSET, SELECTOR_METADATA_WORDS
        )
        starts = metadata[:7]
        lengths = metadata[7:]
        cursor = 0
        for line_id, line_name in LINE_DEFS:
            expected = lines[line_id]
            if starts[line_id] != cursor or lengths[line_id] != len(expected):
                raise RuntimeError(
                    f"{line_name} selector metadata is start={starts[line_id]}, "
                    f"length={lengths[line_id]}, expected start={cursor}, "
                    f"length={len(expected)}."
                )
            cursor += len(expected)
        if cursor != ORDER_COUNT:
            raise RuntimeError("Generic selector v2 metadata does not cover 44 entries")
        if actual_v2 == SELECTOR_GENERIC_V2_BUGGY_BODY:
            return "v2 length-offset bug detected; saving repairs it"
        return "self-contained v2 detected; saving upgrades it to v7"

    actual_v1 = words_at(
        data, SELECTOR_BODY_OFFSET, len(SELECTOR_GENERIC_V1_BODY)
    )
    if actual_v1 != SELECTOR_GENERIC_V1_BODY:
        raise RuntimeError(
            "Generic battle-selector hook is present, but its in-place body "
            "does not match a supported patch version."
        )

    for line_id, line_name in LINE_DEFS:
        base = SELECTOR_TABLE_OFFSET + line_id * SELECTOR_ROW_WORDS * 2
        stored_length = u16(data, base + SELECTOR_LENGTH_WORD * 2)
        expected = lines[line_id]

        if stored_length != len(expected):
            raise RuntimeError(
                f"{line_name} battle-selector length is {stored_length}, but "
                f"the complete line contains {len(expected)} entries."
            )

        stored = list(words_at(data, base, stored_length))
        if stored != expected:
            raise RuntimeError(
                f"{line_name} battle-selector order does not match the complete "
                f"line order.\n  selector={stored}\n  complete={expected}"
            )

    return "legacy v1 detected; saving upgrades it to v7"


def export_csv(bin_path, csv_path):
    data = Path(bin_path).read_bytes()
    validate_bin(data)
    lines = split_flat_order_by_membership(data)
    selector_status = validate_generic_selector_order(data, lines)

    headers = ["line_id", "line_name"] + [
        f"slot_{i}" for i in range(1, MAX_CSV_SLOTS + 1)
    ]

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()

        for line_id, line_name in LINE_DEFS:
            row = {
                "line_id": line_id,
                "line_name": line_name,
            }

            slots = lines[line_id]
            for i in range(MAX_CSV_SLOTS):
                row[f"slot_{i + 1}"] = slots[i] if i < len(slots) else ""

            writer.writerow(row)

    print(f"[DONE] Exported evolution lines to: {csv_path}")
    print(
        "  Battle selector: " + selector_status
    )
    for line_id, line_name in LINE_DEFS:
        print(f"  {line_id} {line_name}: {lines[line_id]}")


def main():
    ap = argparse.ArgumentParser(
        description="Export D3 complete evolution lines to a simple CSV"
    )
    ap.add_argument("bin", help="Input D3.bin")
    ap.add_argument("csv", help="Output CSV")
    args = ap.parse_args()

    export_csv(args.bin, args.csv)


if __name__ == "__main__":
    main()
