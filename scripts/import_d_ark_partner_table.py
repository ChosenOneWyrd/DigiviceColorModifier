#!/usr/bin/env python3
import csv
import os
import struct
import sys
import tempfile
from pathlib import Path

KNOWN_TABLE_START = 0x000D6ACA
RECORD_SIZE = 0x5E
PARTNER_COUNT = 21

FIELDS = [
    "stage", "digimon_id", "slot_type_id", "win_requirement_for_next_evo",
    "sprite_index", "string_index", "evo_animation1_id", "evo_animation2_id",
    "evo_animation3_id", "evo_animation4_id", "evo_animation5_id",
    "background_music_during_battle_id", "attack_voice_sound_id",
    "attack_shot_sprite_index", "attack_shot_sound_id", "special_unlock",
    "evo_unlock_conditions_id", "wins_after_previous_evo", "power",
]

REQUIRED_COLUMNS = ["offset"] + FIELDS
KNOWN_CONDITION_IDS = {0, 1, 2, 3, 4, 6}

GENERIC_TAIL = (
    0, 25, 1242, 12, 0, 25, 1253, 10, 0, 25, 1208, 10, 0, 25, 1242, 12,
    0, 25, 1245, 10, 0, 25, 1204, 12, 0, 25, 1212, 9,
)

def read_record(data, off):
    return struct.unpack_from("<47H", data, off)

def is_partner_table_at(data, base):
    if base < 0 or base + PARTNER_COUNT * RECORD_SIZE > len(data):
        return False
    for i in range(PARTNER_COUNT):
        rec = read_record(data, base + i * RECORD_SIZE)
        if tuple(rec[19:47]) != GENERIC_TAIL:
            return False
    return True

def find_partner_table(data):
    if is_partner_table_at(data, KNOWN_TABLE_START):
        return KNOWN_TABLE_START

    hits = []
    for base in range(0, len(data) - PARTNER_COUNT * RECORD_SIZE + 1, 2):
        try:
            first = read_record(data, base)
        except struct.error:
            continue
        if tuple(first[19:47]) != GENERIC_TAIL:
            continue
        if is_partner_table_at(data, base):
            hits.append(base)

    hits = sorted(set(hits))
    if not hits:
        raise RuntimeError("Compatible D-Ark partner table was not found.")
    if len(hits) != 1:
        where = ", ".join(f"0x{x:08X}" for x in hits)
        raise RuntimeError(f"Partner-table detection is ambiguous: {where}")
    return hits[0]

def parse_u16(text, field, row_number):
    s = str(text).strip()
    if not s:
        raise RuntimeError(f"CSV row {row_number}: {field} is blank")
    try:
        value = int(s, 0)
    except ValueError:
        raise RuntimeError(f"CSV row {row_number}: invalid {field} value {s!r}")
    if not 0 <= value <= 0xFFFF:
        raise RuntimeError(f"CSV row {row_number}: {field}={value} is outside 0..65535")
    return value

def load_rows(csv_path):
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise RuntimeError("CSV has no header")
        missing = [name for name in REQUIRED_COLUMNS if name not in reader.fieldnames]
        if missing:
            raise RuntimeError("CSV is missing required columns: " + ", ".join(missing))
        raw_rows = list(reader)

    if len(raw_rows) != PARTNER_COUNT:
        raise RuntimeError(f"CSV must contain exactly {PARTNER_COUNT} partner rows; found {len(raw_rows)}")

    rows = []
    for row_number, row in enumerate(raw_rows, start=2):
        parsed = {field: parse_u16(row.get(field, ""), field, row_number) for field in FIELDS}
        condition = parsed["evo_unlock_conditions_id"]
        if condition not in KNOWN_CONDITION_IDS:
            raise RuntimeError(
                f"CSV row {row_number}: evo_unlock_conditions_id={condition} is unknown. "
                f"Supported values are {sorted(KNOWN_CONDITION_IDS)}"
            )
        rows.append(parsed)
    return rows

def safe_write(src, dst, data):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dst.resolve():
        dst.write_bytes(data)
        return

    fd, temp_name = tempfile.mkstemp(prefix=f".{dst.name}.", suffix=".tmp", dir=str(dst.parent))
    os.close(fd)
    temp_path = Path(temp_name)

    try:
        temp_path.write_bytes(data)
        if temp_path.stat().st_size != len(data):
            raise RuntimeError("Temporary BIN size does not match expected output size")
        os.replace(temp_path, dst)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass

def main():
    if len(sys.argv) != 4:
        raise SystemExit(
            "Usage: python import_d_ark_partner_table.py "
            "D-Ark.bin d_ark_partner_table.csv D-Ark_out.bin"
        )

    src = Path(sys.argv[1])
    csv_path = Path(sys.argv[2])
    dst = Path(sys.argv[3])

    data = bytearray(src.read_bytes())
    table_start = find_partner_table(data)
    rows = load_rows(csv_path)
    changed_fields = 0

    for i, row in enumerate(rows):
        off = table_start + i * RECORD_SIZE
        current = read_record(data, off)
        if tuple(current[19:47]) != GENERIC_TAIL:
            raise RuntimeError(f"Partner record {i} failed structural validation")

        for field_index, field in enumerate(FIELDS):
            word_off = off + field_index * 2
            old = struct.unpack_from("<H", data, word_off)[0]
            new = row[field]
            if old != new:
                struct.pack_into("<H", data, word_off, new)
                changed_fields += 1

    safe_write(src, dst, data)
    print(f"[+] Partner table: 0x{table_start:08X}")
    print(f"[DONE] Updated {changed_fields} partner field(s)")
    print(f"[DONE] Wrote {dst}")

if __name__ == "__main__":
    main()
