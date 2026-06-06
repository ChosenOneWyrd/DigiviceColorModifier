#!/usr/bin/env python3
"""
D-3 device sound exporter using:
- parsed pack traversal (correct 38 real device sounds)
- simple decode rule that matches the user's better-sounding exporter:
    * start at 0x40
    * low nibble then high nibble
    * predictor = 0
    * step_index = 0

WAVs are written directly into the output folder.
Raw SPF2 files are written into out_dir/raw_spf2/

Usage:
    python export_device_sounds.py D3.bin exported_device_sounds
"""

import os
import csv
import wave
import struct
import argparse
from dataclasses import dataclass
from typing import List, Optional, Tuple, Set


PACK_BASE_DEFAULT = 0x140000
MAGIC_LOW16 = 0x3232
SPF2_MIN_HEADER = 0x28
DECODE_START = 0x40


@dataclass
class PackChild:
    parent_abs: int
    index: int
    rel_off: int
    abs_off: int
    size: int
    is_table_region: bool


def u32le(buf: bytes, off: int) -> int:
    return int.from_bytes(buf[off:off + 4], "little")


def safe_slice(buf: bytes, off: int, size: int) -> bytes:
    if off < 0 or size < 0 or off + size > len(buf):
        return b""
    return buf[off:off + size]


def is_pack_at(buf: bytes, abs_off: int) -> bool:
    if abs_off < 0 or abs_off + 16 > len(buf):
        return False
    w0 = u32le(buf, abs_off)
    low = w0 & 0xFFFF
    count = (w0 >> 16) & 0xFFFF
    return low == MAGIC_LOW16 and 0 < count <= 4096


def parse_pack_children(buf: bytes, pack_abs: int) -> Optional[List[PackChild]]:
    if not is_pack_at(buf, pack_abs):
        return None

    w0 = u32le(buf, pack_abs + 0x00)
    count = (w0 >> 16) & 0xFFFF
    child0_size = u32le(buf, pack_abs + 0x08)
    child0_rel = u32le(buf, pack_abs + 0x0C)

    out: List[PackChild] = []

    if child0_rel > 0 and child0_size > 0:
        abs_off = pack_abs + child0_rel
        if abs_off + child0_size <= len(buf):
            out.append(PackChild(pack_abs, 0, child0_rel, abs_off, child0_size, True))

    table = pack_abs + 0x10
    for i in range(1, count):
        rec = table + (i - 1) * 0x10
        if rec + 0x10 > len(buf):
            break

        rel_off = u32le(buf, rec + 0x08)
        size = u32le(buf, rec + 0x0C)
        if rel_off == 0 or size == 0:
            continue

        abs_off = pack_abs + rel_off
        if abs_off < 0 or abs_off + size > len(buf):
            continue

        out.append(PackChild(pack_abs, i, rel_off, abs_off, size, False))

    return out


def walk_packs(buf: bytes, root_abs: int) -> Tuple[List[PackChild], List[int]]:
    stack = [root_abs]
    seen: Set[int] = set()
    leaves: List[PackChild] = []
    packs_visited: List[int] = []

    while stack:
        pack_abs = stack.pop()
        if pack_abs in seen:
            continue
        seen.add(pack_abs)

        children = parse_pack_children(buf, pack_abs)
        if children is None:
            continue

        packs_visited.append(pack_abs)

        for child in children:
            if child.is_table_region:
                continue
            if is_pack_at(buf, child.abs_off):
                stack.append(child.abs_off)
            else:
                leaves.append(child)

    return leaves, packs_visited


def is_spf2alp(blob: bytes) -> bool:
    return len(blob) >= SPF2_MIN_HEADER and blob.startswith(b"SPF2ALP")


def parse_spf2alp(blob: bytes) -> dict:
    if not is_spf2alp(blob):
        raise ValueError("Not a valid SPF2ALP blob")

    audio_words = u32le(blob, 0x14)
    return {
        "sample_rate": u32le(blob, 0x10),
        "audio_length_words": audio_words,
        "unknown1": u32le(blob, 0x18),
        "unknown2": u32le(blob, 0x1C),
        "id1": u32le(blob, 0x20),
        "id2": u32le(blob, 0x24),
        "declared_total_size": 0x28 + audio_words * 2,
    }


def gp_adpcm_decode(adpcm_bytes: bytes) -> List[int]:
    step_table = [
        16, 17, 19, 21, 23, 25, 28, 31,
        34, 37, 41, 45, 50, 55, 60, 66
    ]
    max_amp = 2047

    predictor = 0
    step_index = 0
    pcm: List[int] = []

    for byte in adpcm_bytes:
        for nib in (byte & 0x0F, byte >> 4):  # low then high
            step = step_table[step_index]

            diff = step >> 3
            if nib & 1:
                diff += step >> 2
            if nib & 2:
                diff += step >> 1
            if nib & 4:
                diff += step

            if nib & 8:
                predictor -= diff
            else:
                predictor += diff

            predictor = max(-max_amp, min(max_amp, predictor))

            step_index += ((nib & 7) - 4)
            step_index = max(0, min(15, step_index))

            pcm.append(int(predictor * 16))

    return pcm


def write_wav(path: str, samples: List[int], rate: int):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        pcm16 = struct.pack("<" + "h" * len(samples), *samples)
        w.writeframes(pcm16)


def export_device_sounds(bin_path: str, out_dir: str, pack_base: int):
    with open(bin_path, "rb") as f:
        buf = f.read()

    if not is_pack_at(buf, pack_base):
        raise RuntimeError(f"0x{pack_base:08X} does not look like a valid pack root")

    os.makedirs(out_dir, exist_ok=True)
    raw_dir = os.path.join(out_dir, "raw_spf2")
    os.makedirs(raw_dir, exist_ok=True)

    leaves, packs = walk_packs(buf, pack_base)

    print(f"[*] Parsed pack tree from 0x{pack_base:08X}")
    print(f"[*] Packs visited: {len(packs)}")
    for p in packs:
        print(f"    pack @ 0x{p:08X}")

    spf2_leaves = []
    for leaf in sorted(leaves, key=lambda x: x.abs_off):
        blob = safe_slice(buf, leaf.abs_off, leaf.size)
        if is_spf2alp(blob):
            spf2_leaves.append(leaf)

    print(f"[*] Total non-table leaves: {len(leaves)}")
    print(f"[*] SPF2ALP leaves found by parsed traversal: {len(spf2_leaves)}")

    rows = []

    for i, leaf in enumerate(spf2_leaves):
        blob = safe_slice(buf, leaf.abs_off, leaf.size)
        info = parse_spf2alp(blob)

        raw_name = f"device_{i:03d}_off_{leaf.abs_off:08X}_pack_{leaf.parent_abs:08X}_ent_{leaf.index:03d}.spf2"
        wav_name = f"device_{i:03d}.wav"

        with open(os.path.join(raw_dir, raw_name), "wb") as f:
            f.write(blob)

        try:
            rate = info["sample_rate"]
            if rate <= 0 or rate > 192000:
                rate = 12000

            adpcm = blob[DECODE_START:] if len(blob) > DECODE_START else b""
            pcm = gp_adpcm_decode(adpcm)

            wav_path = os.path.join(out_dir, wav_name)
            write_wav(wav_path, pcm, rate)

            dur = len(pcm) / float(rate) if rate > 0 else 0.0
            decode_ok = 1
            notes = "decode=off40_low_high_zero_zero"

            print(f"[+] {i:03d} {raw_name} -> {wav_name} ({rate} Hz, {dur:.3f}s)")
        except Exception as e:
            rate = info["sample_rate"] if 0 < info["sample_rate"] <= 192000 else 0
            dur = 0.0
            decode_ok = 0
            notes = f"exception:{e}"
            print(f"[!] {i:03d} {raw_name} -> exception: {e}")

        rows.append({
            "index": i,
            "raw_file": raw_name,
            "wav_file": wav_name,
            "decode_ok": decode_ok,
            "sample_rate": rate,
            "duration_seconds": round(dur, 3),
            "absolute_offset_hex": f"0x{leaf.abs_off:08X}",
            "size_bytes": leaf.size,
            "pack_offset_hex": f"0x{leaf.parent_abs:08X}",
            "entry_index": leaf.index,
            "id1": f"0x{info['id1']:08X}",
            "id2": f"0x{info['id2']:08X}",
            "notes": notes,
        })

    manifest_path = os.path.join(out_dir, "device_sound_manifest.csv")
    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "index", "raw_file", "wav_file", "decode_ok", "sample_rate",
            "duration_seconds", "absolute_offset_hex", "size_bytes",
            "pack_offset_hex", "entry_index", "id1", "id2", "notes"
        ])
        w.writeheader()
        w.writerows(rows)

    print()
    print("[*] Export complete")
    print(f"[*] Raw files: {raw_dir}")
    print(f"[*] WAV files are directly in: {out_dir}")
    print(f"[*] Manifest: {manifest_path}")
    print(f"[*] Total real device sounds exported: {len(rows)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bin_path", help="Path to D3.bin")
    ap.add_argument("out_dir", help="Output directory")
    ap.add_argument("--pack-base", default=f"0x{PACK_BASE_DEFAULT:X}",
                    help="Pack base address, default 0x140000")
    args = ap.parse_args()

    export_device_sounds(
        bin_path=args.bin_path,
        out_dir=args.out_dir,
        pack_base=int(str(args.pack_base), 0),
    )


if __name__ == "__main__":
    main()