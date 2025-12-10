#!/usr/bin/env python3
"""
Full D-3 Audio Extraction Pipeline

This script:
  1. Extracts A18 chunks from BIN
  2. Decodes them using a1800.dll in current directory
  3. Renames WAVs using a CSV mapping file (provided on command line)
  4. Deletes the chunks folder

Usage:
    python export_sounds.py D-3.bin exported_sounds d3_sound_map.csv
    python export_sounds.py D-3.bin exported_sounds none
"""

import sys
import os
import csv
import shutil
import ctypes
from ctypes.wintypes import LPCSTR, UINT

A18_HEADER = bytes([0x00, 0x00, 0x80, 0x3E])
MAX_VALID_LEN = 0x200000  # 2 MB cap


# ==========================================
# STEP 1 — EXTRACT A18 CHUNKS
# ==========================================
def extract_a18_chunks(bin_path, chunks_dir):
    with open(bin_path, "rb") as f:
        data = bytearray(f.read())

    os.makedirs(chunks_dir, exist_ok=True)

    filesize = len(data)
    print(f"[*] Loaded {bin_path} ({filesize} bytes)")

    count = 0
    pos = 0

    while True:
        idx = data.find(A18_HEADER, pos)
        if idx == -1:
            break

        len_pos = idx - 2
        if len_pos < 0:
            pos = idx + 1
            continue

        length = data[len_pos] | (data[len_pos + 1] << 8)

        if length == 0 or length > MAX_VALID_LEN:
            pos = idx + 1
            continue

        start = len_pos
        end = start + 2 + 4 + length

        if end > filesize:
            pos = idx + 1
            continue

        out_name = f"chunk_{count:04X}.a18"
        out_path = os.path.join(chunks_dir, out_name)

        with open(out_path, "wb") as out_f:
            out_f.write(bytes(data[start:end]))

        print(f"[+] Extracted chunk {count:04} at 0x{start:06X}, len=0x{length:X}")
        count += 1

        pos = idx + 3

    print(f"[*] Extraction complete. Found {count} chunks.")
    return count


# ==========================================
# STEP 2 — DECODE A18 TO WAV
# ==========================================
def decode_a18_chunks(chunks_dir, out_base):
    dll_path = os.path.join(os.path.dirname(__file__), "a1800.dll")

    try:
        a1800dll = ctypes.WinDLL(dll_path)
    except OSError as e:
        print("ERROR: Could not load a1800.dll.")
        print(f"Expected at: {dll_path}")
        sys.exit(1)

    decproto = ctypes.WINFUNCTYPE(ctypes.c_uint, LPCSTR, LPCSTR, ctypes.POINTER(UINT), UINT, UINT)
    decfunc = decproto(
        ('dec', a1800dll),
        ((1, 'infile'), (1, 'outfile'), (2, 'fp'), (1, 'unk1', 16000), (1, 'unk2', 0))
    )

    files = [f for f in os.listdir(chunks_dir) if f.lower().endswith(".a18")]
    files.sort()

    print(f"[*] Decoding {len(files)} chunks...")

    generated = []

    for f in files:
        in_path = os.path.join(chunks_dir, f)
        wav_name = f + ".wav"
        out_path = os.path.join(out_base, wav_name)

        in_c = LPCSTR(in_path.encode("ascii", errors="replace"))
        out_c = LPCSTR(out_path.encode("ascii", errors="replace"))

        print(f"[+] Decoding {f} ... ", end="")
        ret = decfunc(infile=in_c, outfile=out_c)

        if ret == 0:
            print("FAILED")
        else:
            print(f"OK ({ret} Hz)")
            generated.append(wav_name)

    return generated


# ==========================================
# STEP 3 — RENAME USING CSV MAP
# ==========================================
def rename_wavs(out_base, wav_list, csv_path):
    if csv_path.lower() == "none" or csv_path.strip() == "":
        print("[*] No CSV provided, skipping rename.")
        return

    if not os.path.exists(csv_path):
        print(f"[!] CSV file not found: {csv_path}")
        return

    print(f"[*] Renaming WAVs using {csv_path}")

    rename_map = {}
    with open(csv_path, newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            orig = row["original"].strip()
            new = row["new"].strip()
            rename_map[orig] = new

    for wav in wav_list:
        src = os.path.join(out_base, wav)
        if wav in rename_map:
            dst = os.path.join(out_base, rename_map[wav])
            print(f"[+] {wav} -> {rename_map[wav]}")
            os.replace(src, dst)
        else:
            print(f"[ ] Keeping {wav} (no rename entry)")


# ==========================================
# MAIN
# ==========================================
if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python d3_extract_and_decode_audio.py D-3.bin output_dir sound_map.csv")
        sys.exit(1)

    bin_path = sys.argv[1]
    out_base = sys.argv[2]
    csv_path = sys.argv[3]

    chunks_dir = os.path.join(out_base, "chunks")

    print("\n=== STEP 1: EXTRACT A18 CHUNKS ===")
    extract_a18_chunks(bin_path, chunks_dir)

    print("\n=== STEP 2: DECODE A18 TO WAV ===")
    wav_list = decode_a18_chunks(chunks_dir, out_base)

    print("\n=== STEP 3: RENAME WAVS ===")
    rename_wavs(out_base, wav_list, csv_path)

    # print("\n=== Cleaning up chunks folder ===")
    # shutil.rmtree(chunks_dir, ignore_errors=True)

    print("\nAll done!")
