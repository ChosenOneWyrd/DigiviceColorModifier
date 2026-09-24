#!/usr/bin/env python3
"""Core D-3 evolution-slot and Link-Battle safety logic (v15).

The important distinction is:

* ``record_index`` is the physical Partner Table slot, 0..37.  Evolution
  structures and the battle selector address this value.
* ``digimon_id`` is the logical Link Battle identity stored inside that
  physical record.  Several active records may deliberately share one logical
  identity, but the active set must still resolve to exactly the 33 identities
  represented by the fixed Partner Link table.

The hardware-confirmed v15 selector keeps the original shared selector body
stock for Link Battle.  A two-word hook dispatches ordinary map battles to a
compact reordered-candidate table only when the saved runtime return PC is
0xCF39.  Link Battle returns at runtime 1:39F5, executes the exact displaced
stock candidate operations, and resumes the untouched stock body.

This module also enforces the two hardware limits established on-device:

* active Partner records must resolve to exactly the fixed 33 Link identities;
* a map-battle line may expose at most nine active choices.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import struct
import tempfile
from typing import Iterable, Sequence


GP_HEADER = b"GP-SPIF-HEADER"
EXPECTED_BIN_SIZE = 0x02000000

PARTNER_TABLE_OFFSET = 0x0009D968
PARTNER_RECORD_SIZE = 0x20
PARTNER_COUNT = 38

P_STAGE = 0
P_DIGIMON_ID = 1
P_SLOT_TYPE = 2
P_WINS = 3
P_SPRITE_INDEX = 4
P_STRING_INDEX = 5
P_EVO1 = 6
P_EVO5 = 10
P_BATTLE_BGM = 11
P_ATTACK_VOICE = 12
P_ATTACK_SHOT_SPRITE = 13
P_ATTACK_SHOT_SOUND = 14

LINE_DEFS = (
    (0, "Vmon Line"),
    (1, "Wormmon Line"),
    (2, "Hawkmon Line"),
    (3, "Tailmon Line"),
    (4, "Armadimon Line"),
    (5, "Patamon Line"),
    (6, "Terriermon Line"),
)
LINE_NAMES = dict(LINE_DEFS)
LINE_COUNT = len(LINE_DEFS)
MAX_LINE_SLOTS = 10
MAP_OUTPUT_CAPACITY = 9

SHARED_SLOT_LINES = {
    18: (0, 1),
    19: (2, 3),
    20: (4, 5),
}
LINES_TO_SHARED_SLOT = {frozenset(lines): value for value, lines in SHARED_SLOT_LINES.items()}
ALLOWED_SLOT_TYPES = frozenset(range(LINE_COUNT)) | frozenset(SHARED_SLOT_LINES)

# The stock 44-entry table contains one extra occurrence of each of these six
# physical records.  When a shared membership is sacrificed, the now-unused
# occurrence remains a harmless "ghost" in the fixed-size Viewer/Link order
# table.  This is exactly what the user's working Partner-only BIN does.
STOCK_GHOST_RECORDS = (6, 7, 8, 9, 13, 20)

ORDER_OFFSET = 0x000AADDE
ORDER_CAPACITY = 44
ORDER_PRE_OFFSET = 0x000AADDA
ORDER_PRE_BYTES = bytes.fromhex("C3 01 C4 01")
ORDER_POST_OFFSET = ORDER_OFFSET + ORDER_CAPACITY * 4
ORDER_POST_BYTES = bytes.fromhex(
    "6B 6D 03 00 87 6D 03 00 9A 6F 03 00 D2 74 03 00"
)

TARGET_ARRAY_DEFS = (
    (0, "Vmon Line",       0x000A939C),
    (1, "Wormmon Line",    0x000A93AE),
    (2, "Hawkmon Line",    0x000A93C0),
    (3, "Tailmon Line",    0x000A93D2),
    (4, "Armadimon Line",  0x000A93E4),
    (5, "Patamon Line",    0x000A93F6),
    (6, "Terriermon Line", 0x000A9408),
)
TARGET_SLOTS = 9
TARGET_EMPTY = 0xFFFF
TARGET_PRE_OFFSET = 0x000A9390
TARGET_PRE_WORDS = (41071, 41073, 41028, 41060, 41059, 41169)
TARGET_POST_OFFSET = 0x000A941A
TARGET_POST_WORDS = (558, 559, 564, 565, 562, 563, 566, 567)

# Stock target-array precedence.  Stage-0 records are excluded dynamically;
# promoted baby records are included and receive a deterministic stage-based
# position.  Paladin Mode (physical record 9) remains firmware-special.
STOCK_TARGET_SEQUENCES = (
    (8, 7, 6, 4, 3, 2, 5, 1),
    (8, 7, 6, 34, 33),
    (13, 26, 25, 27, 24),
    (15, 14, 13, 12, 11),
    (20, 31, 30, 32, 29),
    (22, 21, 20, 18, 19, 17),
    (37, 36, 35),
)
SPECIAL_TARGET_EXCLUSIONS = frozenset({9})

LINK_TABLE_OFFSET = 0x000A21C8
LINK_RECORD_SIZE = 0x0A
LINK_PLAYER_COUNT = 33
LINK_ENEMY_COUNT = 91
LINK_ENEMY_OFFSET = LINK_TABLE_OFFSET + LINK_PLAYER_COUNT * LINK_RECORD_SIZE
LINK_TABLE_END = LINK_ENEMY_OFFSET + LINK_ENEMY_COUNT * LINK_RECORD_SIZE
LINK_CODE_ANCHORS = {
    0x00087478: 0xA0E4,
    0x000874A8: 0xA0E4,
    0x00087536: 0xA0E4,
    0x000875A0: 0xA189,
    0x00087634: 0xA189,
    0x0008750C: 0xA350,
}

# Legacy v9 selector definitions are retained only so existing BINs can be
# read and upgraded.  They must never be installed by the v15 writer.
SELECTOR_HOOK_WORD = 0x0437C5
SELECTOR_HELPER_WORD = 0x0437C7
SELECTOR_POST_HELPER_WORD = 0x0437D9
SELECTOR_MAIN_WORD = 0x0437DC
SELECTOR_COMMON_WORD = 0x04382B

SELECTOR_HOOK_OFFSET = SELECTOR_HOOK_WORD * 2
SELECTOR_BODY_OFFSET = SELECTOR_POST_HELPER_WORD * 2
SELECTOR_COMMON_OFFSET = SELECTOR_COMMON_WORD * 2

# The old v1 allocation is safe dedicated storage and was already used by v7.
SELECTOR_TABLE_OFFSET = 0x000ACE00
SELECTOR_RAW_CAPACITY = ORDER_CAPACITY
SELECTOR_STARTS_INDEX = 44
SELECTOR_LENGTHS_INDEX = 51
SELECTOR_METADATA_WORDS = 14
SELECTOR_STORAGE_WORDS = SELECTOR_RAW_CAPACITY + SELECTOR_METADATA_WORDS

DATA_RUNTIME_BIAS_WORDS = 0x9000
SELECTOR_TABLE_RUNTIME_WORD = SELECTOR_TABLE_OFFSET // 2 + DATA_RUNTIME_BIAS_WORDS
SELECTOR_TABLE_SEGMENT = SELECTOR_TABLE_RUNTIME_WORD >> 16
SELECTOR_TABLE_LOW_WORD = SELECTOR_TABLE_RUNTIME_WORD & 0xFFFF
SELECTOR_STARTS_LOW_WORD = SELECTOR_TABLE_LOW_WORD + SELECTOR_STARTS_INDEX
SELECTOR_LENGTHS_LOW_WORD = SELECTOR_TABLE_LOW_WORD + SELECTOR_LENGTHS_INDEX
SELECTOR_LINE_BASES = (0, 33, 23, 10, 28, 16, 35)

STOCK_LINE_LENGTHS = (10, 6, 6, 6, 6, 7, 3)

# Hardware-confirmed stock-equivalent slot_type eligibility block from the
# successful all-seven inline probe.  It accepts direct types 0..6 and shared
# types 18/19/20, then writes the eligibility flag at [bp+3].
SELECTOR_ELIGIBILITY_WORDS = (
    0x9604, 0x9805, 0x0642, 0x1840, 0xF02C, 0x98E3,
    0x4852, 0x0E0B, 0x4854, 0x9E09, 0x2852, 0x9944,
    0x960F, 0x4704, 0x5E0A, 0x0841, 0x4704, 0x5E07,
    0xEE03, 0x960F, 0x4903, 0x5E03,
    0x9840, 0xD803, 0xEE0D,
    0x9841, 0xD803, 0xEE0A,
)

# ---------------------------------------------------------------------------
# Hardware-confirmed v15 call-site-dispatched selector
# ---------------------------------------------------------------------------

MAP_CALL_FILE_WORD = 0x003F37
LINK_CALL_FILE_WORD = 0x00A9F3
CALL_RUNTIME_BIAS_WORDS = 0x9000
MAP_RETURN_RUNTIME_LOW = (
    MAP_CALL_FILE_WORD + 2 + CALL_RUNTIME_BIAS_WORDS
) & 0xFFFF
LINK_RETURN_RUNTIME_WORD = LINK_CALL_FILE_WORD + 2 + CALL_RUNTIME_BIAS_WORDS
LINK_RETURN_RUNTIME_SEGMENT = LINK_RETURN_RUNTIME_WORD >> 16
LINK_RETURN_RUNTIME_LOW = LINK_RETURN_RUNTIME_WORD & 0xFFFF

SELECTOR_PATCH_END_WORD = 0x04382B
SELECTOR_FILE_END_WORD = 0x04384C
STOCK_HELPER_SEGMENT = 4
STOCK_HELPER_LOW = 0xC7C7

DISPATCH_FILE_OFFSET = SELECTOR_TABLE_OFFSET
PROVEN_SAFE_END = 0x000AD052
DISPATCH_RUNTIME_WORD = (
    DISPATCH_FILE_OFFSET // 2 + DATA_RUNTIME_BIAS_WORDS
)
DISPATCH_SEGMENT = DISPATCH_RUNTIME_WORD >> 16
DISPATCH_LOW = DISPATCH_RUNTIME_WORD & 0xFFFF

V15_CODE_WORDS = 47
V15_LENGTHS_INDEX = V15_CODE_WORDS
V15_STARTS_INDEX = V15_LENGTHS_INDEX + LINE_COUNT
V15_FILLERS_INDEX = V15_STARTS_INDEX + LINE_COUNT
V15_CANDIDATES_INDEX = V15_FILLERS_INDEX + LINE_COUNT
V15_MAX_PAYLOAD_WORDS = V15_CANDIDATES_INDEX + ORDER_CAPACITY

# Exact stock words at physical file words 0x437C5..0x4382A.  Older v7/v9
# tools modified this region.  v15 restores it before installing only its
# two-word far-jump hook, which is what keeps the Link path stock-equivalent.
STOCK_SELECTOR_PATCH_WORDS = (
    0x9801, 0x0802, 0x9666, 0xD688, 0xD888, 0xF045, 0x71FC, 0x0042,
    0x9739, 0x973B, 0x973B, 0x973B, 0x9959, 0x979B, 0x9206, 0x9407,
    0x0901, 0x1702, 0xD804, 0xD605, 0x9604, 0x9805, 0x0642, 0x1840,
    0xF02C, 0x98E3, 0x4852, 0x4E0E, 0x980F, 0x4840, 0x5E03, 0x980F,
    0x4841, 0x4E03, 0x9841, 0xD808, 0xEE02, 0x9840, 0xD808, 0x9808,
    0xD803, 0xEE3C, 0x9604, 0x9805, 0x0642, 0x1840, 0xF02C, 0x98E3,
    0x4853, 0x4E0E, 0x980F, 0x4843, 0x5E03, 0x980F, 0x4842, 0x4E03,
    0x9841, 0xD809, 0xEE02, 0x9840, 0xD809, 0x9809, 0xD803, 0xEE26,
    0x9604, 0x9805, 0x0642, 0x1840, 0xF02C, 0x98E3, 0x4854, 0x4E0E,
    0x980F, 0x4845, 0x5E03, 0x980F, 0x4844, 0x4E03, 0x9841, 0xD80A,
    0xEE02, 0x9840, 0xD80A, 0x980A, 0xD803, 0xEE10, 0x9604, 0x9805,
    0x0642, 0x1840, 0xF02C, 0x96E3, 0x980F, 0x4704, 0x5E03, 0x9840,
    0xD80B, 0xEE02, 0x9841, 0xD80B, 0x980B, 0xD803,
)


@dataclass(frozen=True)
class PartnerRecord:
    record_index: int
    words: tuple[int, ...]

    @property
    def stage(self) -> int:
        return self.words[P_STAGE]

    @property
    def link_id(self) -> int:
        return self.words[P_DIGIMON_ID]

    @property
    def slot_type(self) -> int:
        return self.words[P_SLOT_TYPE]

    @property
    def active(self) -> bool:
        return self.stage > 0


@dataclass(frozen=True)
class LinkRecord:
    digimon_id: int
    string_index: int
    stage: int
    sprite_index: int
    power: int


@dataclass(frozen=True)
class SafetyReport:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    active_physical_records: tuple[int, ...]
    active_link_ids: tuple[int, ...]
    alias_groups: tuple[tuple[int, tuple[int, ...]], ...]
    active_line_counts: tuple[int, ...]
    target_line_counts: tuple[int, ...]

    @property
    def safe(self) -> bool:
        return not self.errors


def u16(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def put_u16(data: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<H", data, offset, value)


def put_u32(data: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<I", data, offset, value)


def words_at(data: bytes | bytearray, offset: int, count: int) -> tuple[int, ...]:
    return struct.unpack_from("<" + "H" * count, data, offset)


def sha256_bytes(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def atomic_write(path: str | os.PathLike[str], data: bytes | bytearray) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=destination.name + ".", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def encode_short_branch(
    source_word: int,
    target_word: int,
    forward_base: int,
    backward_base: int,
) -> int:
    delta = target_word - (source_word + 1)
    if delta == 0 or abs(delta) > 0x3F:
        raise RuntimeError(
            f"Internal short branch out of range: {source_word:#x} -> {target_word:#x}"
        )
    return (forward_base | delta) if delta > 0 else (backward_base | (-delta))


def build_selector_v9_words() -> dict[int, int]:
    """Return the deterministic v9 in-place selector patch.

    The main block reads per-line start/length metadata and a packed raw
    candidate from 5:F700.  It preserves incoming DS and never uses a shift
    instruction before the stock helper.  After that helper returns, execution
    enters the already hardware-tested stock-equivalent eligibility block.
    """

    if (
        SELECTOR_TABLE_RUNTIME_WORD != 0x5F700
        or SELECTOR_TABLE_SEGMENT != 5
        or SELECTOR_TABLE_LOW_WORD != 0xF700
        or SELECTOR_STARTS_LOW_WORD != 0xF72C
        or SELECTOR_LENGTHS_LOW_WORD != 0xF733
    ):
        raise RuntimeError("Internal selector storage mapping changed")

    words: dict[int, int] = {}
    labels: dict[str, int] = {}
    fixups: list[tuple[int, str, int, int]] = []
    pc = SELECTOR_MAIN_WORD

    def emit(value: int) -> None:
        nonlocal pc
        if pc in words:
            raise RuntimeError(f"Internal selector overlap at {pc:#x}")
        words[pc] = value
        pc += 1

    def label(name: str) -> None:
        labels[name] = pc

    def branch(name: str, forward: int, backward: int) -> None:
        fixups.append((pc, name, forward, backward))
        emit(0)

    # Save the caller's data segment first, including for an out-of-range line
    # that takes the invalid exit.  Then reject any non-line caller before
    # indexing metadata.
    emit(0xF023)                  # r3 = ds
    emit(0xD609)                  # st r3,[bp+9]
    emit(0x980F)                  # ld r4,[bp+15]
    emit(0x4846)                  # cmp r4,#6
    branch("invalid", 0x9E00, 0x9E40)  # ja invalid

    # Load start[line] from 5:F72C.
    emit(0x970B)                  # ld r3,#imm16
    emit(SELECTOR_STARTS_LOW_WORD)
    emit(0x0704)                  # add r3,r4
    emit(0x9840 | SELECTOR_TABLE_SEGMENT)
    emit(0xF02C)                  # ds = r4
    emit(0x98E3)                  # ld r4,ds:[r3]
    emit(0xD808)                  # st r4,[bp+8] (line start)

    # Load length[line] from 5:F733 and bounds-check scan_index.
    emit(0x980F)                  # ld r4,[bp+15]
    emit(0x970B)
    emit(SELECTOR_LENGTHS_LOW_WORD)
    emit(0x0704)
    emit(0x98E3)                  # r4 = line length
    emit(0x9601)                  # r3 = scan index
    emit(0x4704)                  # cmp r3,r4
    branch("valid", 0x0E00, 0x0E40)  # jb valid

    label("invalid")
    emit(0x9840)                  # ld r4,#0
    emit(0xD803)                  # st r4,[bp+3] (ineligible)
    emit(0x9609)                  # ld r3,[bp+9]
    emit(0xF02B)                  # ds = r3
    branch("common", 0xEE00, 0xEE40)

    label("valid")
    # r3 still holds scan_index.  Convert it to a packed global slot.
    emit(0x9808)                  # ld r4,[bp+8] (line start)
    emit(0x0704)                  # add r3,r4
    emit(0xD608)                  # st r3,[bp+8] (global slot)

    # Read raw_candidate[global_slot] from 5:F700.
    emit(0x970B)
    emit(SELECTOR_TABLE_LOW_WORD)
    emit(0x9808)
    emit(0x0704)
    emit(0x98E3)
    emit(0xD808)                  # preserve candidate
    emit(0x9609)
    emit(0xF02B)                  # restore caller DS
    emit(0x9808)
    branch("helper", 0xEE00, 0xEE40)

    if pc != 0x043802:
        raise RuntimeError(f"Internal selector main ended at {pc:#x}, expected 0x43802")

    labels["helper"] = SELECTOR_HELPER_WORD
    labels["common"] = SELECTOR_COMMON_WORD
    for source, name, forward, backward in fixups:
        words[source] = encode_short_branch(
            source, labels[name], forward, backward
        )

    # Hook and post-helper routing.  0x437DA/DB and 0x43802..04 are dead in v9;
    # restore their stock words so the patch is deterministic and auditable.
    words[SELECTOR_HOOK_WORD] = encode_short_branch(
        SELECTOR_HOOK_WORD, SELECTOR_MAIN_WORD, 0xEE00, 0xEE40
    )
    words[SELECTOR_HOOK_WORD + 1] = 0x0802
    words[SELECTOR_POST_HELPER_WORD] = encode_short_branch(
        SELECTOR_POST_HELPER_WORD, 0x043805, 0xEE00, 0xEE40
    )
    words[0x0437DA] = 0x9604
    words[0x0437DB] = 0x9805
    words[0x043802] = 0x9809
    words[0x043803] = 0xD803
    words[0x043804] = 0xEE26

    for index, value in enumerate(SELECTOR_ELIGIBILITY_WORDS):
        words[0x043805 + index] = value

    if words[SELECTOR_HOOK_WORD] != 0xEE16:
        raise RuntimeError("Internal selector hook encoding changed")
    if words[SELECTOR_POST_HELPER_WORD] != 0xEE2B:
        raise RuntimeError("Internal post-helper branch encoding changed")
    return words


SELECTOR_V9_WORDS = build_selector_v9_words()


class _V15DispatcherBuilder:
    """Small label/fixup builder for the u'nSP instructions used by v15."""

    def __init__(self) -> None:
        self.words: list[int] = []
        self.labels: dict[str, int] = {}
        self.branches: list[tuple[int, int, str]] = []
        self.addresses: list[tuple[int, str]] = []

    def emit(self, *values: int) -> None:
        self.words.extend(values)

    def label(self, name: str) -> None:
        if name in self.labels:
            raise RuntimeError(f"Duplicate v15 dispatcher label {name}")
        self.labels[name] = len(self.words)

    def branch_forward(self, opcode_base: int, target: str) -> None:
        self.branches.append((len(self.words), opcode_base, target))
        self.words.append(0)

    def address_operand(self, target: str) -> None:
        self.addresses.append((len(self.words), target))
        self.words.append(0)

    def finish(self, table_offsets: dict[str, int]) -> tuple[int, ...]:
        for source, opcode_base, target in self.branches:
            distance = self.labels[target] - (source + 1)
            if not 1 <= distance <= 0x3F:
                raise RuntimeError(
                    f"v15 branch {source}->{target} distance {distance} is invalid"
                )
            self.words[source] = opcode_base | distance
        for index, target in self.addresses:
            self.words[index] = DISPATCH_LOW + table_offsets[target]
        return tuple(self.words)


def build_v15_payload(
    lengths: Sequence[int],
    starts: Sequence[int],
    fillers: Sequence[int],
    candidates: Sequence[int],
) -> tuple[int, ...]:
    """Build the hardware-confirmed v15 dispatcher and compact tables."""

    lengths = tuple(lengths)
    starts = tuple(starts)
    fillers = tuple(fillers)
    candidates = tuple(candidates)
    if not (
        len(lengths) == len(starts) == len(fillers) == LINE_COUNT
        and all(1 <= value <= MAX_LINE_SLOTS for value in lengths)
    ):
        raise RuntimeError("Invalid v15 line metadata")
    if sum(lengths) != len(candidates) or len(candidates) > ORDER_CAPACITY:
        raise RuntimeError("Invalid v15 compact candidate count")

    code = _V15DispatcherBuilder()

    # [bp+14] is the low return PC saved by CALL16.  Only the ordinary map
    # caller returns at runtime 0:CF39.  Link returns at runtime 1:39F5 and
    # therefore follows the exact displaced stock operations below.
    code.emit(0x980E)                         # r4 = [bp+14]
    code.emit(0x490C, MAP_RETURN_RUNTIME_LOW) # cmp r4,#0xCF39
    code.branch_forward(0x5E00, "map")        # je map

    code.label("stock")
    code.emit(0x9801)                         # r4 = [bp+1]
    code.emit(0x0802)                         # r4 += [bp+2]
    code.emit(0xFE80 | STOCK_HELPER_SEGMENT, STOCK_HELPER_LOW)

    code.label("map")
    code.emit(0x980F)                         # r4 = [bp+15] line
    code.emit(0x4846)                         # cmp r4,#6
    code.branch_forward(0x9E00, "invalid")    # ja invalid
    code.emit(0xF021)                         # r1 = ds

    # requested candidate when scan < line length, otherwise a repeated
    # ineligible filler.  Keeping all 38 passes preserves the stock loop.
    code.emit(0x970B)
    code.address_operand("lengths")
    code.emit(0x0704)
    code.emit(0x9845)
    code.emit(0xF02C)
    code.emit(0x96E3)                         # r3 = length[line]
    code.emit(0x9801)                         # r4 = scan
    code.emit(0x4903)
    code.branch_forward(0x1E00, "filler")     # jae filler

    code.emit(0x970B)
    code.address_operand("starts")
    code.emit(0x980F)
    code.emit(0x0704)
    code.emit(0x98E3)                         # r4 = start[line]
    code.emit(0x970B)
    code.address_operand("candidates")
    code.emit(0x0704)
    code.emit(0x9801)
    code.emit(0x0704)
    code.emit(0x98E3)                         # r4 = raw candidate
    code.emit(0xF029)                         # ds = r1
    code.emit(0xFE80 | STOCK_HELPER_SEGMENT, STOCK_HELPER_LOW)

    code.label("filler")
    code.emit(0x970B)
    code.address_operand("fillers")
    code.emit(0x980F)
    code.emit(0x0704)
    code.emit(0x98E3)                         # r4 = raw filler
    code.emit(0xF029)
    code.emit(0xFE80 | STOCK_HELPER_SEGMENT, STOCK_HELPER_LOW)

    code.label("invalid")
    code.emit(0x9801, 0x0802)
    code.emit(0xFE80 | STOCK_HELPER_SEGMENT, STOCK_HELPER_LOW)

    if len(code.words) != V15_CODE_WORDS:
        raise RuntimeError(
            f"v15 dispatcher has {len(code.words)} words; expected {V15_CODE_WORDS}"
        )
    table_offsets = {
        "lengths": V15_LENGTHS_INDEX,
        "starts": V15_STARTS_INDEX,
        "fillers": V15_FILLERS_INDEX,
        "candidates": V15_CANDIDATES_INDEX,
    }
    dispatcher = code.finish(table_offsets)
    payload = dispatcher + lengths + starts + fillers + candidates
    if len(payload) > V15_MAX_PAYLOAD_WORDS:
        raise RuntimeError("v15 payload exceeds its compact maximum")
    if DISPATCH_FILE_OFFSET + len(payload) * 2 > PROVEN_SAFE_END:
        raise RuntimeError("v15 payload exceeds the hardware-proven allocation")
    if payload[:8] != (
        0x980E, 0x490C, 0xCF39, 0x5E04,
        0x9801, 0x0802, 0xFE84, 0xC7C7,
    ):
        raise RuntimeError("v15 dispatcher control-flow signature changed")
    return payload


def read_partner_records(data: bytes | bytearray) -> list[PartnerRecord]:
    return [
        PartnerRecord(
            index,
            struct.unpack_from(
                "<16H", data, PARTNER_TABLE_OFFSET + index * PARTNER_RECORD_SIZE
            ),
        )
        for index in range(PARTNER_COUNT)
    ]


def read_link_records(
    data: bytes | bytearray,
) -> tuple[list[LinkRecord], list[LinkRecord]]:
    def many(offset: int, count: int) -> list[LinkRecord]:
        return [
            LinkRecord(
                *struct.unpack_from("<5H", data, offset + i * LINK_RECORD_SIZE)
            )
            for i in range(count)
        ]

    return (
        many(LINK_TABLE_OFFSET, LINK_PLAYER_COUNT),
        many(LINK_ENEMY_OFFSET, LINK_ENEMY_COUNT),
    )


def read_flat_order(data: bytes | bytearray) -> list[int]:
    return list(struct.unpack_from("<44I", data, ORDER_OFFSET))


def membership_from_slot_types(
    records: Sequence[PartnerRecord],
) -> dict[int, set[int]]:
    members = {line_id: set() for line_id, _ in LINE_DEFS}
    for record in records:
        if record.slot_type in range(LINE_COUNT):
            members[record.slot_type].add(record.record_index)
        elif record.slot_type in SHARED_SLOT_LINES:
            for line_id in SHARED_SLOT_LINES[record.slot_type]:
                members[line_id].add(record.record_index)
        else:
            raise RuntimeError(
                f"Partner record {record.record_index} has unsupported "
                f"slot_type {record.slot_type}; expected 0..6, 18, 19, or 20"
            )
    return members


def derive_slot_types(lines: dict[int, list[int]]) -> dict[int, int]:
    occurrences: dict[int, list[int]] = defaultdict(list)
    for line_id in range(LINE_COUNT):
        values = lines[line_id]
        if not 1 <= len(values) <= MAX_LINE_SLOTS:
            raise RuntimeError(
                f"{LINE_NAMES[line_id]} must contain 1..{MAX_LINE_SLOTS} slots; "
                f"found {len(values)}"
            )
        if len(set(values)) != len(values):
            raise RuntimeError(f"{LINE_NAMES[line_id]} contains a duplicate slot")
        for record_index in values:
            if not 0 <= record_index < PARTNER_COUNT:
                raise RuntimeError(
                    f"{LINE_NAMES[line_id]} contains physical record "
                    f"{record_index}; expected 0..37"
                )
            occurrences[record_index].append(line_id)

    missing = sorted(set(range(PARTNER_COUNT)) - set(occurrences))
    if missing:
        raise RuntimeError(
            "Every physical Partner record 0..37 must appear at least once; "
            f"missing {missing}"
        )

    total = sum(len(values) for values in lines.values())
    if total > ORDER_CAPACITY:
        raise RuntimeError(
            f"Evolution lines contain {total} occurrences; fixed capacity is "
            f"{ORDER_CAPACITY}"
        )

    result: dict[int, int] = {}
    for record_index in range(PARTNER_COUNT):
        line_ids = occurrences[record_index]
        if len(line_ids) == 1:
            result[record_index] = line_ids[0]
            continue
        if len(line_ids) == 2:
            shared = LINES_TO_SHARED_SLOT.get(frozenset(line_ids))
            if shared is not None:
                result[record_index] = shared
                continue
        raise RuntimeError(
            f"Physical Partner record {record_index} appears in lines "
            f"{line_ids}. A record may appear once, or in exactly one supported "
            "shared pair: Vmon+Wormmon, Hawkmon+Tailmon, or "
            "Armadimon+Patamon."
        )
    return result


def active_line_counts(
    records: Sequence[PartnerRecord], lines: dict[int, list[int]]
) -> tuple[int, ...]:
    return tuple(
        sum(1 for index in lines[line_id] if records[index].active)
        for line_id in range(LINE_COUNT)
    )


def target_members(
    records: Sequence[PartnerRecord], lines: dict[int, list[int]]
) -> dict[int, set[int]]:
    return {
        line_id: {
            index
            for index in lines[line_id]
            if records[index].active and index not in SPECIAL_TARGET_EXCLUSIONS
        }
        for line_id in range(LINE_COUNT)
    }


def derive_target_arrays(
    records: Sequence[PartnerRecord], lines: dict[int, list[int]]
) -> dict[int, list[int]]:
    eligible = {
        record.record_index
        for record in records
        if record.active and record.record_index not in SPECIAL_TARGET_EXCLUSIONS
    }
    edges = {node: set() for node in eligible}
    indegree = {node: 0 for node in eligible}

    for sequence in STOCK_TARGET_SEQUENCES:
        filtered = [node for node in sequence if node in eligible]
        for left, right in zip(filtered, filtered[1:]):
            if right not in edges[left]:
                edges[left].add(right)
                indegree[right] += 1

    def priority(index: int) -> tuple[int, int, int, int]:
        record = records[index]
        animation_depth = sum(
            1 for value in record.words[P_EVO1:P_EVO5 + 1] if value != 0
        )
        return (-record.stage, -record.words[P_WINS], -animation_depth, -index)

    available = [node for node in eligible if indegree[node] == 0]
    global_order: list[int] = []
    while available:
        available.sort(key=priority)
        node = available.pop(0)
        global_order.append(node)
        for nxt in sorted(edges[node]):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                available.append(nxt)

    if len(global_order) != len(eligible):
        raise RuntimeError("Target-array precedence graph contains a cycle")

    wanted = target_members(records, lines)
    result: dict[int, list[int]] = {}
    for line_id in range(LINE_COUNT):
        ordered = [index for index in global_order if index in wanted[line_id]]
        if len(ordered) > TARGET_SLOTS:
            raise RuntimeError(
                f"{LINE_NAMES[line_id]} needs {len(ordered)} normal evolution "
                f"targets, but the firmware array has {TARGET_SLOTS}. "
                "Remove/move one active non-special slot from this line."
            )
        result[line_id] = ordered + [TARGET_EMPTY] * (TARGET_SLOTS - len(ordered))
    return result


def selector_v15_installed(data: bytes | bytearray) -> bool:
    if len(data) < PROVEN_SAFE_END:
        return False
    if (DISPATCH_RUNTIME_WORD, DISPATCH_SEGMENT, DISPATCH_LOW) != (
        0x5F700,
        5,
        0xF700,
    ):
        raise RuntimeError("v15 dispatcher runtime mapping changed")
    if (MAP_RETURN_RUNTIME_LOW, LINK_RETURN_RUNTIME_SEGMENT, LINK_RETURN_RUNTIME_LOW) != (
        0xCF39,
        1,
        0x39F5,
    ):
        raise RuntimeError("v15 return-PC mapping changed")
    if words_at(data, SELECTOR_HOOK_WORD * 2, 2) != (0xFE85, 0xF700):
        return False
    if words_at(data, DISPATCH_FILE_OFFSET, 8) != (
        0x980E, 0x490C, 0xCF39, 0x5E04,
        0x9801, 0x0802, 0xFE84, 0xC7C7,
    ):
        return False
    for word_offset in (MAP_CALL_FILE_WORD, LINK_CALL_FILE_WORD):
        if words_at(data, word_offset * 2, 3) != (0xF044, 0xC792, 0x0044):
            return False
    stock_tail = STOCK_SELECTOR_PATCH_WORDS[2:]
    if words_at(
        data,
        (SELECTOR_HOOK_WORD + 2) * 2,
        len(stock_tail),
    ) != stock_tail:
        return False
    return True


def read_v15_lines(data: bytes | bytearray) -> dict[int, list[int]]:
    if not selector_v15_installed(data):
        raise RuntimeError("hardware-confirmed v15 selector is not installed")
    lengths = words_at(
        data,
        DISPATCH_FILE_OFFSET + V15_LENGTHS_INDEX * 2,
        LINE_COUNT,
    )
    starts = words_at(
        data,
        DISPATCH_FILE_OFFSET + V15_STARTS_INDEX * 2,
        LINE_COUNT,
    )
    total = sum(lengths)
    if any(not 1 <= value <= MAX_LINE_SLOTS for value in lengths):
        raise RuntimeError(f"v15 has invalid line lengths {lengths}")
    if total > ORDER_CAPACITY:
        raise RuntimeError("v15 candidate tables exceed 44 occurrences")
    raw = words_at(
        data,
        DISPATCH_FILE_OFFSET + V15_CANDIDATES_INDEX * 2,
        total,
    )
    lines: dict[int, list[int]] = {}
    cursor = 0
    for line_id in range(LINE_COUNT):
        if starts[line_id] != cursor:
            raise RuntimeError(
                f"v15 start[{line_id}]={starts[line_id]}, expected {cursor}"
            )
        end = cursor + lengths[line_id]
        values = [value % PARTNER_COUNT for value in raw[cursor:end]]
        if len(set(values)) != len(values):
            raise RuntimeError(f"v15 {LINE_NAMES[line_id]} contains duplicates")
        lines[line_id] = values
        cursor = end
    storage_end = DISPATCH_FILE_OFFSET + (V15_CANDIDATES_INDEX + total) * 2
    if any(data[storage_end:PROVEN_SAFE_END]):
        raise RuntimeError("v15 unused selector-storage tail is not zero")
    return lines


def selector_v9_installed(data: bytes | bytearray) -> bool:
    return all(u16(data, address * 2) == value for address, value in SELECTOR_V9_WORDS.items())


def read_v9_lines(data: bytes | bytearray) -> dict[int, list[int]]:
    if not selector_v9_installed(data):
        raise RuntimeError("v9 selector is not installed")
    starts = words_at(
        data,
        SELECTOR_TABLE_OFFSET + SELECTOR_STARTS_INDEX * 2,
        LINE_COUNT,
    )
    lengths = words_at(
        data,
        SELECTOR_TABLE_OFFSET + SELECTOR_LENGTHS_INDEX * 2,
        LINE_COUNT,
    )
    raw = words_at(data, SELECTOR_TABLE_OFFSET, SELECTOR_RAW_CAPACITY)
    lines: dict[int, list[int]] = {}
    cursor = 0
    for line_id in range(LINE_COUNT):
        if starts[line_id] != cursor:
            raise RuntimeError(
                f"v9 selector start[{line_id}]={starts[line_id]}, expected {cursor}"
            )
        length = lengths[line_id]
        if not 1 <= length <= MAX_LINE_SLOTS:
            raise RuntimeError(f"v9 selector line {line_id} has invalid length {length}")
        end = cursor + length
        if end > SELECTOR_RAW_CAPACITY:
            raise RuntimeError("v9 selector metadata exceeds its raw table")
        values = [value % PARTNER_COUNT for value in raw[cursor:end]]
        if len(set(values)) != len(values):
            raise RuntimeError(f"v9 selector line {line_id} contains duplicates")
        lines[line_id] = values
        cursor = end
    if cursor > ORDER_CAPACITY:
        raise RuntimeError("v9 selector uses more than 44 candidates")
    return lines


def _read_v7_lines(data: bytes | bytearray) -> dict[int, list[int]] | None:
    # v4-v7 use the same 0xEE16 hook and six add-immediate length words.
    if u16(data, SELECTOR_HOOK_OFFSET) != 0xEE16:
        return None
    length_addresses = [0x0437E0 + line_id * 3 for line_id in range(6)]
    lengths: list[int] = []
    for address in length_addresses:
        opcode = u16(data, address * 2)
        if opcode & 0xFFC0 != 0x0840:
            return None
        lengths.append(opcode & 0x3F)
    total_opcode = u16(data, 0x0437F0 * 2)
    if total_opcode & 0xFFC0 != 0x9640:
        return None
    total = total_opcode & 0x3F
    lengths.append(total - sum(lengths))
    if total != ORDER_CAPACITY or any(not 1 <= n <= MAX_LINE_SLOTS for n in lengths):
        return None
    raw = words_at(data, SELECTOR_TABLE_OFFSET, ORDER_CAPACITY)
    lines: dict[int, list[int]] = {}
    cursor = 0
    for line_id, length in enumerate(lengths):
        lines[line_id] = [value % PARTNER_COUNT for value in raw[cursor:cursor + length]]
        cursor += length
    return lines


def _read_flat_stock_segments(data: bytes | bytearray) -> dict[int, list[int]]:
    flat = read_flat_order(data)
    if any(not 0 <= value < PARTNER_COUNT for value in flat):
        raise RuntimeError("Flat evolution-order table contains a value outside 0..37")
    result: dict[int, list[int]] = {}
    cursor = 0
    for line_id, length in enumerate(STOCK_LINE_LENGTHS):
        result[line_id] = flat[cursor:cursor + length]
        cursor += length
    return result


def read_source_lines(data: bytes | bytearray) -> tuple[dict[int, list[int]], str]:
    if selector_v15_installed(data):
        return read_v15_lines(data), "v15 hardware-confirmed"
    if selector_v9_installed(data):
        return read_v9_lines(data), "v9 legacy"
    v7 = _read_v7_lines(data)
    if v7 is not None:
        return v7, "v7"
    return _read_flat_stock_segments(data), "stock/legacy"


def reconcile_lines_to_partner(
    data: bytes | bytearray,
) -> tuple[dict[int, list[int]], str, list[str]]:
    """Preserve as much visible order as possible after Partner membership edits."""

    records = read_partner_records(data)
    wanted = membership_from_slot_types(records)
    source, source_kind = read_source_lines(data)
    flat_preference = [
        value
        for line_id in range(LINE_COUNT)
        for value in source.get(line_id, [])
        if 0 <= value < PARTNER_COUNT
    ]
    notes: list[str] = []
    result: dict[int, list[int]] = {}

    for line_id in range(LINE_COUNT):
        ordered: list[int] = []
        for value in source.get(line_id, []):
            if value in wanted[line_id] and value not in ordered:
                ordered.append(value)
        for value in flat_preference:
            if value in wanted[line_id] and value not in ordered:
                ordered.append(value)
        for value in sorted(wanted[line_id]):
            if value not in ordered:
                ordered.append(value)
        if set(ordered) != wanted[line_id]:
            raise RuntimeError(f"Could not reconcile {LINE_NAMES[line_id]} membership")
        if len(ordered) > MAX_LINE_SLOTS:
            raise RuntimeError(
                f"{LINE_NAMES[line_id]} has {len(ordered)} physical slots after "
                f"Partner edits; GUI/selector limit is {MAX_LINE_SLOTS}"
            )
        dropped = [value for value in source.get(line_id, []) if value not in wanted[line_id]]
        added = [value for value in ordered if value not in source.get(line_id, [])]
        if dropped or added:
            notes.append(
                f"{LINE_NAMES[line_id]}: removed stale {dropped or 'none'}, "
                f"added new {added or 'none'}"
            )
        result[line_id] = ordered

    derive_slot_types(result)  # complete structural validation
    return result, source_kind, notes


def build_flat_order(lines: dict[int, list[int]]) -> list[int]:
    packed = [value for line_id in range(LINE_COUNT) for value in lines[line_id]]
    if len(packed) > ORDER_CAPACITY:
        raise RuntimeError("Evolution line occurrences exceed the 44-entry flat table")
    ghosts_needed = ORDER_CAPACITY - len(packed)

    ghosts: list[int] = []
    counts = Counter(packed)
    for record_index in STOCK_GHOST_RECORDS:
        if len(ghosts) >= ghosts_needed:
            break
        if counts[record_index] < 2:
            ghosts.append(record_index)
            counts[record_index] += 1

    # The supported membership model has at most six shared occurrences, so
    # the six stock ghost records must always be enough.
    if len(ghosts) != ghosts_needed:
        raise RuntimeError(
            f"Could not create {ghosts_needed} safe flat-table ghost entries"
        )
    return packed + ghosts


def build_v15_storage(
    lines: dict[int, list[int]],
) -> tuple[list[int], list[int], list[int], list[int], tuple[int, ...]]:
    """Build compact map candidates plus one rejected filler per line.

    Candidate words are physical record indexes directly; the untouched stock
    helper normalizes them modulo 38.  After each requested line ends, the
    dispatcher repeats an ineligible candidate for the remaining stock scans.
    """

    desired_slot_types = derive_slot_types(lines)
    candidates: list[int] = []
    starts: list[int] = []
    lengths: list[int] = []
    fillers: list[int] = []
    for line_id in range(LINE_COUNT):
        starts.append(len(candidates))
        lengths.append(len(lines[line_id]))
        candidates.extend(lines[line_id])

        direct = [
            record_index
            for record_index, slot_type in desired_slot_types.items()
            if slot_type == line_id
        ]
        base = min(direct) if direct else lines[line_id][0]
        members = set(lines[line_id])
        filler = None
        for step in range(PARTNER_COUNT - 1, -1, -1):
            raw_value = base + step
            if raw_value % PARTNER_COUNT not in members:
                filler = raw_value
                break
        if filler is None:
            raise RuntimeError(f"{LINE_NAMES[line_id]} has no rejected filler")
        fillers.append(filler)

    if len(candidates) > ORDER_CAPACITY:
        raise RuntimeError("v15 selector candidates exceed 44-word capacity")
    payload = build_v15_payload(lengths, starts, fillers, candidates)
    return candidates, starts, lengths, fillers, payload


def validate_layout(data: bytes | bytearray) -> list[str]:
    errors: list[str] = []
    if len(data) != EXPECTED_BIN_SIZE:
        errors.append(
            f"BIN size is 0x{len(data):X}; expected 0x{EXPECTED_BIN_SIZE:X}"
        )
    if not data.startswith(GP_HEADER):
        errors.append("BIN does not begin with GP-SPIF-HEADER")
    if len(data) < LINK_TABLE_END:
        errors.append("BIN is too small for the fixed Link tables")
        return errors
    if data[ORDER_PRE_OFFSET:ORDER_PRE_OFFSET + len(ORDER_PRE_BYTES)] != ORDER_PRE_BYTES:
        errors.append("Evolution-order pre-signature does not match this firmware")
    if data[ORDER_POST_OFFSET:ORDER_POST_OFFSET + len(ORDER_POST_BYTES)] != ORDER_POST_BYTES:
        errors.append("Evolution-order post-signature does not match this firmware")
    if words_at(data, TARGET_PRE_OFFSET, len(TARGET_PRE_WORDS)) != TARGET_PRE_WORDS:
        errors.append("Target-array pre-signature does not match this firmware")
    if words_at(data, TARGET_POST_OFFSET, len(TARGET_POST_WORDS)) != TARGET_POST_WORDS:
        errors.append("Target-array post-signature does not match this firmware")
    if words_at(data, 0x043792 * 2, 4) != (
        0xDA88, 0x204C, 0x0B08, 0x0001,
    ):
        errors.append("Selector frame/prologue signature does not match this firmware")
    for label, word_offset in (
        ("map", MAP_CALL_FILE_WORD),
        ("Link", LINK_CALL_FILE_WORD),
    ):
        actual = words_at(data, word_offset * 2, 3)
        if actual != (0xF044, 0xC792, 0x0044):
            errors.append(
                f"{label} selector caller is {actual}; expected "
                "(0xF044, 0xC792, 0x0044)"
            )
    for offset, expected in LINK_CODE_ANCHORS.items():
        actual = u16(data, offset)
        if actual != expected:
            errors.append(
                f"Link code anchor 0x{offset:08X}=0x{actual:04X}; "
                f"expected 0x{expected:04X}"
            )
    return errors


def analyze_safety(
    data: bytes | bytearray,
    lines: dict[int, list[int]] | None = None,
) -> SafetyReport:
    errors = validate_layout(data)
    warnings: list[str] = []
    if len(data) < LINK_TABLE_END:
        return SafetyReport(tuple(errors), (), (), (), (), (), ())

    records = read_partner_records(data)
    player_rows, enemy_rows = read_link_records(data)

    for record in records:
        if not 0 <= record.stage <= 5:
            errors.append(
                f"Partner record {record.record_index} stage {record.stage}; expected 0..5"
            )
        if not 0 <= record.link_id < PARTNER_COUNT:
            errors.append(
                f"Partner record {record.record_index} Link ID {record.link_id}; expected 0..37"
            )
        if record.slot_type not in ALLOWED_SLOT_TYPES:
            errors.append(
                f"Partner record {record.record_index} slot_type {record.slot_type} is unsupported"
            )
        if record.active:
            if record.words[P_STRING_INDEX] in {0, 0xFFFF}:
                errors.append(f"Active record {record.record_index} has invalid string_index")
            if record.words[P_SPRITE_INDEX] in {0, 0xFFFF}:
                errors.append(f"Active record {record.record_index} has invalid sprite_index")
            missing_battle = [
                name
                for name, word_index in (
                    ("battle BGM", P_BATTLE_BGM),
                    ("attack voice", P_ATTACK_VOICE),
                    ("shot sprite", P_ATTACK_SHOT_SPRITE),
                    ("shot sound", P_ATTACK_SHOT_SOUND),
                )
                if record.words[word_index] == 0
            ]
            if missing_battle:
                warnings.append(
                    f"Active record {record.record_index} has zero "
                    + ", ".join(missing_battle)
                    + "; configure these before using its attack animation"
                )

    player_ids = [row.digimon_id for row in player_rows]
    if len(player_ids) != LINK_PLAYER_COUNT or len(set(player_ids)) != LINK_PLAYER_COUNT:
        errors.append("The fixed 33-row Partner Link table must contain 33 unique IDs")
    for index, row in enumerate(player_rows):
        if not 1 <= row.stage <= 5:
            errors.append(f"Partner Link row {index} has invalid stage {row.stage}")
        if row.string_index in {0, 0xFFFF} or row.sprite_index in {0, 0xFFFF}:
            errors.append(
                f"Partner Link row {index} has a zero/0xFFFF string or sprite"
            )
        if row.power > 255:
            errors.append(f"Partner Link row {index} has power {row.power} > 255")
    table_ids = set(player_ids)

    active = [record for record in records if record.active]
    active_ids = {record.link_id for record in active}
    if len(active_ids) != LINK_PLAYER_COUNT:
        errors.append(
            f"Active Partner records resolve to {len(active_ids)} unique Link IDs; "
            "firmware requires exactly 33"
        )
    if active_ids != table_ids:
        errors.append(
            "Active Partner Link-ID set does not exactly match the fixed 33-row "
            f"Link table (missing rows={sorted(active_ids - table_ids)}, "
            f"orphan rows={sorted(table_ids - active_ids)})"
        )

    enemy_ids = [row.digimon_id for row in enemy_rows]
    if len(set(enemy_ids)) != LINK_ENEMY_COUNT or set(enemy_ids) != set(range(LINK_ENEMY_COUNT)):
        errors.append("The 91 Link opponent rows must be a permutation of IDs 0..90")
    for index, row in enumerate(enemy_rows):
        if not 1 <= row.stage <= 5:
            errors.append(f"Opponent Link row {index} has invalid stage {row.stage}")
        if row.string_index in {0, 0xFFFF} or row.sprite_index in {0, 0xFFFF}:
            errors.append(
                f"Opponent Link row {index} has a zero/0xFFFF string or sprite"
            )
        if row.power > 255:
            errors.append(f"Opponent Link row {index} has power {row.power} > 255")

    groups: dict[int, list[int]] = defaultdict(list)
    for record in active:
        groups[record.link_id].append(record.record_index)
    aliases = tuple(
        (link_id, tuple(indices))
        for link_id, indices in sorted(groups.items())
        if len(indices) > 1
    )

    if lines is None:
        try:
            lines, _, _ = reconcile_lines_to_partner(data)
        except Exception as exc:
            errors.append(str(exc))
            lines = {line_id: [] for line_id in range(LINE_COUNT)}
    else:
        try:
            desired_slot_types = derive_slot_types(lines)
            mismatches = [
                index
                for index, slot_type in desired_slot_types.items()
                if records[index].slot_type != slot_type
            ]
            if mismatches:
                errors.append(
                    "Evolution-line membership and Partner slot_type disagree at "
                    f"physical records {mismatches}"
                )
        except Exception as exc:
            errors.append(str(exc))

    active_counts = active_line_counts(records, lines)
    target_sets = target_members(records, lines)
    target_counts = tuple(len(target_sets[i]) for i in range(LINE_COUNT))
    for line_id, count in enumerate(target_counts):
        if count > TARGET_SLOTS:
            errors.append(
                f"{LINE_NAMES[line_id]} needs {count} normal target entries; capacity is 9"
            )
    for line_id, count in enumerate(active_counts):
        if count > MAP_OUTPUT_CAPACITY:
            errors.append(
                f"{LINE_NAMES[line_id]} has {count} active battle candidates. "
                f"The map selector stores exactly {MAP_OUTPUT_CAPACITY}; move or "
                "deactivate at least one slot before importing."
            )

    return SafetyReport(
        tuple(dict.fromkeys(errors)),
        tuple(dict.fromkeys(warnings)),
        tuple(record.record_index for record in active),
        tuple(sorted(active_ids)),
        aliases,
        active_counts,
        target_counts,
    )


def require_safe(
    data: bytes | bytearray,
    lines: dict[int, list[int]] | None = None,
    label: str = "BIN",
) -> SafetyReport:
    report = analyze_safety(data, lines)
    if report.errors:
        raise RuntimeError(
            f"{label} failed the combined Partner/Link safety audit:\n  - "
            + "\n  - ".join(report.errors)
        )
    return report


def apply_synchronized_state(
    data: bytearray,
    lines: dict[int, list[int]],
) -> list[tuple[str, int, int, int]]:
    """Synchronize all evolution structures and install selector v15."""

    desired_slot_types = derive_slot_types(lines)
    records_before = read_partner_records(data)
    targets = derive_target_arrays(records_before, lines)
    flat = build_flat_order(lines)
    _candidates, _starts, _lengths, _fillers, payload = build_v15_storage(lines)
    changes: list[tuple[str, int, int, int]] = []

    def change16(kind: str, offset: int, new: int) -> None:
        old = u16(data, offset)
        if old != new:
            put_u16(data, offset, new)
            changes.append((kind, offset, old, new))

    def change32(kind: str, offset: int, new: int) -> None:
        old = u32(data, offset)
        if old != new:
            put_u32(data, offset, new)
            changes.append((kind, offset, old, new))

    for index, value in enumerate(flat):
        change32("flat_order", ORDER_OFFSET + index * 4, value)

    for line_id, _name, offset in TARGET_ARRAY_DEFS:
        for index, value in enumerate(targets[line_id]):
            change16("target_array", offset + index * 2, value)

    for record_index, slot_type in desired_slot_types.items():
        offset = (
            PARTNER_TABLE_OFFSET
            + record_index * PARTNER_RECORD_SIZE
            + P_SLOT_TYPE * 2
        )
        change16("partner.slot_type", offset, slot_type)

    # Remove every legacy in-place selector edit and install only v15's
    # two-word hook.  Construct the final region first so an already-correct
    # v15 BIN is byte-for-byte idempotent and reports zero changes.
    selector_words = list(STOCK_SELECTOR_PATCH_WORDS)
    selector_words[0:2] = [0xFE85, DISPATCH_LOW]
    for index, value in enumerate(selector_words):
        change16(
            "selector.v15_code",
            (SELECTOR_HOOK_WORD + index) * 2,
            value,
        )

    # The complete hardware-proven allocation is owned by selector tooling.
    # Obsolete v1-v14 data becomes zero; every byte at/after 0xAD052 is kept.
    storage_words = [0] * ((PROVEN_SAFE_END - DISPATCH_FILE_OFFSET) // 2)
    storage_words[:len(payload)] = payload
    for index, value in enumerate(storage_words):
        change16(
            "selector.v15_storage",
            DISPATCH_FILE_OFFSET + index * 2,
            value,
        )

    return changes


def validate_synchronized_state(
    data: bytes | bytearray,
    expected_lines: dict[int, list[int]] | None = None,
) -> SafetyReport:
    if not selector_v15_installed(data):
        raise RuntimeError("Hardware-confirmed selector v15 did not validate")
    stored_lines = read_v15_lines(data)
    if expected_lines is not None and stored_lines != expected_lines:
        raise RuntimeError(
            f"Stored selector lines differ from requested lines:\n"
            f"stored={stored_lines}\nrequested={expected_lines}"
        )
    records = read_partner_records(data)
    membership = membership_from_slot_types(records)
    for line_id in range(LINE_COUNT):
        if set(stored_lines[line_id]) != membership[line_id]:
            raise RuntimeError(
                f"{LINE_NAMES[line_id]} selector order does not match Partner membership"
            )

    # Exhaustive map-path model: the requested candidates appear first, all
    # remaining stock passes use an ineligible filler, and no line can write
    # more than the nine-pointer map output array.
    candidates, starts, lengths, fillers, payload = build_v15_storage(stored_lines)
    stored_payload = words_at(data, DISPATCH_FILE_OFFSET, len(payload))
    if stored_payload != payload:
        raise RuntimeError("Stored v15 dispatcher/tables differ from regenerated payload")

    for line_id in range(LINE_COUNT):
        normalized: list[int] = []
        visible: list[int] = []
        for scan_index in range(PARTNER_COUNT):
            raw_value = (
                candidates[starts[line_id] + scan_index]
                if scan_index < lengths[line_id]
                else fillers[line_id]
            )
            actual_record = raw_value % PARTNER_COUNT
            normalized.append(actual_record)
            slot_type = records[actual_record].slot_type
            eligible_lines = (
                (slot_type,)
                if slot_type in range(LINE_COUNT)
                else SHARED_SLOT_LINES.get(slot_type, ())
            )
            if line_id in eligible_lines and records[actual_record].active:
                visible.append(actual_record)

        if normalized[:lengths[line_id]] != stored_lines[line_id]:
            raise RuntimeError(
                f"{LINE_NAMES[line_id]} v15 candidate order did not normalize"
            )
        filler_record = fillers[line_id] % PARTNER_COUNT
        if filler_record in membership[line_id]:
            raise RuntimeError(
                f"{LINE_NAMES[line_id]} filler record {filler_record} is eligible"
            )
        expected_visible = [
            record_index
            for record_index in stored_lines[line_id]
            if records[record_index].active
        ]
        if visible != expected_visible:
            raise RuntimeError(
                f"{LINE_NAMES[line_id]} modeled visible order {visible} differs "
                f"from requested active order {expected_visible}"
            )
        if len(visible) > MAP_OUTPUT_CAPACITY:
            raise RuntimeError(
                f"{LINE_NAMES[line_id]} would write {len(visible)} map choices; "
                f"capacity is {MAP_OUTPUT_CAPACITY}"
            )
        for actual_record in stored_lines[line_id]:
            slot_type = records[actual_record].slot_type
            eligible_lines = (
                (slot_type,)
                if slot_type in range(LINE_COUNT)
                else SHARED_SLOT_LINES.get(slot_type, ())
            )
            if line_id not in eligible_lines:
                raise RuntimeError(
                    f"Selector candidate {actual_record} would fail eligibility "
                    f"for line {line_id}"
                )
        if lengths[line_id] != len(stored_lines[line_id]):
            raise RuntimeError("Selector line length model failed")

    # Link must miss the 0xCF39 comparison and execute the exact displaced
    # stock pair before returning to the untouched selector continuation.
    if LINK_RETURN_RUNTIME_LOW == MAP_RETURN_RUNTIME_LOW:
        raise RuntimeError("Link and map return-PC values collide")
    if payload[4:8] != (0x9801, 0x0802, 0xFE84, 0xC7C7):
        raise RuntimeError("v15 Link stock-fallback signature changed")

    report = require_safe(data, stored_lines, "Synchronized output")
    expected_targets = derive_target_arrays(records, stored_lines)
    for line_id, _name, offset in TARGET_ARRAY_DEFS:
        stored = list(words_at(data, offset, TARGET_SLOTS))
        if stored != expected_targets[line_id]:
            raise RuntimeError(f"Target array mismatch for {LINE_NAMES[line_id]}")

    expected_flat = build_flat_order(stored_lines)
    if read_flat_order(data) != expected_flat:
        raise RuntimeError("Flat evolution-order table did not validate")
    return report


def synchronize_existing_bin(
    data: bytearray,
    requested_lines: dict[int, list[int]] | None = None,
) -> tuple[dict[int, list[int]], str, list[str], list[tuple[str, int, int, int]], SafetyReport]:
    """Atomic core used after either Partner edits or Evolution Slot edits."""

    if requested_lines is None:
        lines, source_kind, notes = reconcile_lines_to_partner(data)
    else:
        lines = {line_id: list(requested_lines[line_id]) for line_id in range(LINE_COUNT)}
        derive_slot_types(lines)
        source_kind = "CSV"
        notes = []

    # Apply requested membership first, then audit Link IDs using the resulting
    # Partner state.  No digimon_id or Link-table byte is touched here.
    desired_slot_types = derive_slot_types(lines)
    for record_index, slot_type in desired_slot_types.items():
        put_u16(
            data,
            PARTNER_TABLE_OFFSET + record_index * PARTNER_RECORD_SIZE + P_SLOT_TYPE * 2,
            slot_type,
        )
    require_safe(data, lines, "Planned state")

    link_before = bytes(data[LINK_TABLE_OFFSET:LINK_TABLE_END])
    anchors_before = {offset: u16(data, offset) for offset in LINK_CODE_ANCHORS}
    caller_before = {
        word_offset: words_at(data, word_offset * 2, 3)
        for word_offset in (MAP_CALL_FILE_WORD, LINK_CALL_FILE_WORD)
    }
    protected_tail_before = bytes(data[PROVEN_SAFE_END:])
    changes = apply_synchronized_state(data, lines)
    if bytes(data[LINK_TABLE_OFFSET:LINK_TABLE_END]) != link_before:
        raise RuntimeError("Internal error: Link table bytes changed")
    if {offset: u16(data, offset) for offset in LINK_CODE_ANCHORS} != anchors_before:
        raise RuntimeError("Internal error: Link code anchors changed")
    if {
        word_offset: words_at(data, word_offset * 2, 3)
        for word_offset in (MAP_CALL_FILE_WORD, LINK_CALL_FILE_WORD)
    } != caller_before:
        raise RuntimeError("Internal error: a direct selector caller changed")
    if bytes(data[PROVEN_SAFE_END:]) != protected_tail_before:
        raise RuntimeError(
            "Internal error: a byte at or beyond 0xAD052 changed"
        )
    report = validate_synchronized_state(data, lines)
    return lines, source_kind, notes, changes, report


def format_report(report: SafetyReport) -> str:
    lines = [
        "SAFE" if report.safe else "UNSAFE",
        f"active physical records: {len(report.active_physical_records)}",
        f"unique active Link IDs: {len(report.active_link_ids)}/33",
        "active candidates by line: " + ", ".join(map(str, report.active_line_counts)),
        "normal targets by line: " + ", ".join(map(str, report.target_line_counts)),
    ]
    if report.alias_groups:
        lines.append(
            "Link-ID aliases: "
            + "; ".join(
                f"ID {link_id} <- physical {list(indices)}"
                for link_id, indices in report.alias_groups
            )
        )
    else:
        lines.append("Link-ID aliases: none")
    lines.extend("WARNING: " + value for value in report.warnings)
    lines.extend("ERROR: " + value for value in report.errors)
    return "\n".join(lines)


def changed_ranges(before: bytes, after: bytes) -> list[tuple[int, int]]:
    if len(before) != len(after):
        raise RuntimeError("Output size changed")
    changed = [i for i, (left, right) in enumerate(zip(before, after)) if left != right]
    if not changed:
        return []
    result: list[tuple[int, int]] = []
    start = previous = changed[0]
    for offset in changed[1:]:
        if offset != previous + 1:
            result.append((start, previous + 1))
            start = offset
        previous = offset
    result.append((start, previous + 1))
    return result
