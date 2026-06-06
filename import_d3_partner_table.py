import sys
import csv
import struct
from pathlib import Path

if len(sys.argv) >= 4:
    BIN_IN = sys.argv[1]
    CSV_IN = sys.argv[2]
    BIN_OUT = sys.argv[3]
else:
    BIN_IN = "D3.bin"
    CSV_IN = "d3_partner_table.csv"
    BIN_OUT = "D3.bin"

TABLE_START = 0x0009D950
BLOCK_SIZE = 0x20
MAX_RECORDS = 38

HEADERS = [
    "meta_offset",
    "data_offset",
    "stage",
    "digimon_id",
    "jogress_win_partner_id",
    "win_requirement_for_next_evo",
    "sprite_index",
    "string_index",
    "evo_animation1_id",
    "evo_animation2_id",
    "evo_animation3_id",
    "evo_animation4_id",
    "evo_animation5_id",
    "background_music_during_battle_id",
    "attack_voice_sound_id",
    "attack_shot_sprite_index",
    "attack_shot_sound_id",
    "special_unlock",
]

data = bytearray(Path(BIN_IN).read_bytes())

with open(CSV_IN, newline="", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    rows = list(reader)

missing = [h for h in HEADERS if h not in reader.fieldnames]
if missing:
    raise RuntimeError(f"CSV missing columns: {missing}")

if len(rows) > MAX_RECORDS:
    raise RuntimeError(f"CSV has {len(rows)} rows, max allowed is {MAX_RECORDS}")

def read_u16(row, name, row_num):
    value = int(str(row[name]).strip(), 0)
    if not (0 <= value <= 65535):
        raise ValueError(f"Row {row_num}, {name}: {value} is outside uint16 range")
    return value

for i, row in enumerate(rows):
    row_num = i + 1

    meta_off = TABLE_START + i * BLOCK_SIZE
    data_off = TABLE_START + (i + 1) * BLOCK_SIZE

    stage = read_u16(row, "stage", row_num)
    digimon_id = read_u16(row, "digimon_id", row_num)
    jogress_win_partner_id = read_u16(row, "jogress_win_partner_id", row_num)
    win_req = read_u16(row, "win_requirement_for_next_evo", row_num)

    struct.pack_into(
        "<4H",
        data,
        meta_off + 12 * 2,
        stage,
        digimon_id,
        jogress_win_partner_id,
        win_req,
    )

    visual_values = [
        read_u16(row, "sprite_index", row_num),
        read_u16(row, "string_index", row_num),
        read_u16(row, "evo_animation1_id", row_num),
        read_u16(row, "evo_animation2_id", row_num),
        read_u16(row, "evo_animation3_id", row_num),
        read_u16(row, "evo_animation4_id", row_num),
        read_u16(row, "evo_animation5_id", row_num),
        read_u16(row, "background_music_during_battle_id", row_num),
        read_u16(row, "attack_voice_sound_id", row_num),
        read_u16(row, "attack_shot_sprite_index", row_num),
        read_u16(row, "attack_shot_sound_id", row_num),
        read_u16(row, "special_unlock", row_num),
    ]

    struct.pack_into("<12H", data, data_off, *visual_values)

Path(BIN_OUT).write_bytes(data)

print(f"Imported {len(rows)} rows")
print(f"Wrote {BIN_OUT}")