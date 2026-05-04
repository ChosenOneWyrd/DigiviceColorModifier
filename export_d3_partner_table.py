import csv
import struct
import sys
from pathlib import Path

# ------------------ ARGUMENTS ------------------

if len(sys.argv) >= 3:
    BIN_IN = sys.argv[1]
    CSV_OUT = sys.argv[2]
else:
    # fallback for manual run
    BIN_IN = "D3.bin"
    CSV_OUT = "d3_partner_table.csv"

print(f"[+] Using BIN: {BIN_IN}")
print(f"[+] Writing CSV: {CSV_OUT}")

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

data = Path(BIN_IN).read_bytes()
rows = []

for i in range(MAX_RECORDS):
    meta_off = TABLE_START + i * BLOCK_SIZE
    data_off = TABLE_START + (i + 1) * BLOCK_SIZE

    if data_off + BLOCK_SIZE > len(data):
        break

    meta = struct.unpack_from("<16H", data, meta_off)
    visual = struct.unpack_from("<16H", data, data_off)

    stage = meta[12]
    digimon_id = meta[13]
    jogress_win_partner_id = meta[14]
    win_req = meta[15]

    sprite_index = visual[0]
    string_index = visual[1]

    if i > 0 and stage == 0 and digimon_id == 0 and sprite_index == 0 and string_index == 0:
        break

    rows.append({
        "meta_offset": f"0x{meta_off:08X}",
        "data_offset": f"0x{data_off:08X}",
        "stage": stage,
        "digimon_id": digimon_id,
        "jogress_win_partner_id": jogress_win_partner_id,
        "win_requirement_for_next_evo": win_req,
        "sprite_index": sprite_index,
        "string_index": string_index,
        "evo_animation1_id": visual[2],
        "evo_animation2_id": visual[3],
        "evo_animation3_id": visual[4],
        "evo_animation4_id": visual[5],
        "evo_animation5_id": visual[6],
        "background_music_during_battle_id": visual[7],
        "attack_voice_sound_id": visual[8],
        "attack_shot_sprite_index": visual[9],
        "attack_shot_sound_id": visual[10],
        "special_unlock": visual[11],
    })

with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=HEADERS)
    writer.writeheader()
    writer.writerows(rows)

print(f"Exported {len(rows)} rows")
print(f"Wrote {CSV_OUT}")