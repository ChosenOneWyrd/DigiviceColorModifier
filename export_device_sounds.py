#!/usr/bin/env python3
"""
Export SPF2ALP GeneralPlus ADPCM sounds from a device BIN.

This version exports block indices from start_idx (default 8)
through end_idx (user-specified via --end, default 43).

Example:

    python export_device_sounds.py D3.bin --out exported_device_sounds --end 43
    python export_device_sounds.py Digivice.bin --out exported_device_sounds --end 40
"""

import os, struct, wave, argparse

MAGIC = b"SPF2ALP\x00"
HEADER_SIZE = 0x40

# ---------- utils ----------

def le32(b, o):
    return struct.unpack_from("<I", b, o)[0]

def ensure_dir(d):
    if not os.path.isdir(d):
        os.makedirs(d)

# ---------- GP ADPCM decode ----------

def gp_adpcm_decode(adpcm_bytes):
    step_table = [
        16,17,19,21,23,25,28,31,
        34,37,41,45,50,55,60,66
    ]
    max_amp = 2047

    predictor = 0
    step_index = 0
    pcm = []

    for byte in adpcm_bytes:
        # low nibble then high nibble
        for nib in (byte & 0x0F, byte >> 4):
            step = step_table[step_index]

            diff = step >> 3
            if nib & 1: diff += step >> 2
            if nib & 2: diff += step >> 1
            if nib & 4: diff += step

            if nib & 8:
                predictor -= diff
            else:
                predictor += diff

            predictor = max(-max_amp, min(max_amp, predictor))

            step_index += ((nib & 7) - 4)
            step_index = max(0, min(15, step_index))

            pcm.append(int(predictor * 16))

    return pcm

# ---------- WAV writer ----------

def write_wav(path, samples, rate):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        pcm16 = struct.pack("<" + "h" * len(samples), *samples)
        w.writeframes(pcm16)

# ---------- Extraction logic ----------

def extract_packs(bin_path, out_dir, start_idx=8, end_idx=43):
    """
    Only exports SPF2ALP blocks whose index is in [start_idx, end_idx].
    """
    with open(bin_path, "rb") as f:
        buf = f.read()

    ensure_dir(out_dir)

    # locate all SPF2ALP headers
    positions = []
    pos = 0
    while True:
        pos = buf.find(MAGIC, pos)
        if pos == -1:
            break
        positions.append(pos)
        pos += 1

    print(f"[*] Found {len(positions)} SPF2ALP blocks total")
    print(f"[*] Exporting indices {start_idx} through {end_idx}")

    for idx, off in enumerate(positions):
        if idx < start_idx or idx > end_idx:
            continue

        # determine block end
        if idx + 1 < len(positions):
            end = positions[idx + 1]
        else:
            end = len(buf)

        pack = buf[off:end]

        if len(pack) < HEADER_SIZE + 4:
            print(f"[!] Block {idx:03d} too small, skipping")
            continue

        rate = le32(pack, 0x10)
        if rate == 0 or rate > 192000:
            rate = 44100

        adpcm = pack[HEADER_SIZE:]
        pcm = gp_adpcm_decode(adpcm)

        out_name = os.path.join(out_dir, f"spf2alp_{idx:03d}.wav")
        write_wav(out_name, pcm, rate)

        print(f"[+] Exported block {idx:03d} -> {out_name} "
              f"(offset=0x{off:X}, rate={rate}, samples={len(pcm)})")

    print("\n[*] Done.")


# ---------- CLI ----------

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("bin", help="Path to device BIN")
    ap.add_argument("--out", default="exported_spf2alp", help="Output directory")

    # NEW ARGUMENT: end index
    ap.add_argument("--end", type=int, default=43,
                    help="Last SPF2ALP block index to export (default: 43)")

    args = ap.parse_args()

    extract_packs(args.bin, args.out, start_idx=8, end_idx=args.end)
