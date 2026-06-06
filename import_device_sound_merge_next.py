#!/usr/bin/env python3
"""
Risky D-3 importer:
merge one device sound with the next slot so the first sound can become longer.

Default behavior:
- replace device_033.wav
- allow it to consume device_034's slot
- patch pack metadata so device_033 becomes the merged sound
- disable device_034's pack entry
- do NOT touch anything after device_034

This importer matches export_device_sounds_bestlisten.py assumptions:
    * decode/import data begins at 0x40
    * low nibble then high nibble
    * predictor = 0
    * step_index = 0

Usage:
    python import_device_sound_merge_next.py D3.bin D3_patched.bin exported_device_sounds --dry-run
    python import_device_sound_merge_next.py D3.bin D3_patched.bin exported_device_sounds
"""

import os
import csv
import wave
import argparse
import numpy as np


SPF2_HEADER_SIZE = 0x40
SPF2_LENGTH_FIELD_OFF = 0x14
MAX_SAMPLE_RATE = 192000
DEFAULT_TARGET_INDEX = 33
DEFAULT_NEXT_INDEX = 34


def u32le(buf: bytes, off: int) -> int:
    return int.from_bytes(buf[off:off + 4], "little")


def put_u32le(buf: bytearray, off: int, value: int):
    buf[off:off + 4] = int(value).to_bytes(4, "little", signed=False)


def parse_spf2alp_header(blob: bytes) -> dict:
    if len(blob) < 0x28 or not blob.startswith(b"SPF2ALP"):
        raise ValueError("Not a valid SPF2ALP header/blob")

    audio_words = u32le(blob, 0x14)
    return {
        "sample_rate": u32le(blob, 0x10),
        "audio_length_words": audio_words,
        "declared_total_size": 0x28 + audio_words * 2,
        "id1": u32le(blob, 0x20),
        "id2": u32le(blob, 0x24),
    }


def convert_wav_to_mono_16bit_resampled(
    in_path: str,
    target_rate: int,
    target_rms_db: float = -12.0,
    limit_ceiling: float = 0.98,
):
    with wave.open(in_path, "rb") as wf:
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)

    if sw == 1:
        pcm = np.frombuffer(raw, dtype=np.uint8).astype(np.int16)
        pcm = (pcm - 128) * 256
    elif sw == 2:
        pcm = np.frombuffer(raw, dtype=np.int16)
    else:
        raise ValueError(f"Unsupported sample width {sw} in {in_path}")

    if nch == 2:
        pcm = pcm.reshape(-1, 2).mean(axis=1).astype(np.int16)
    elif nch != 1:
        raise ValueError(f"Unsupported channel count {nch} in {in_path}")

    if sr != target_rate:
        duration = len(pcm) / float(sr)
        new_len = int(round(duration * target_rate))
        if new_len <= 0:
            raise ValueError(f"Resampled length <= 0 for {in_path}")
        pcm = np.interp(
            np.linspace(0, len(pcm), new_len, endpoint=False),
            np.arange(len(pcm)),
            pcm.astype(np.float32),
        ).astype(np.int16)

    fpcm = pcm.astype(np.float32)

    rms = np.sqrt(np.mean(fpcm ** 2)) if len(fpcm) > 0 else 0.0
    if rms > 0:
        target_rms = (10.0 ** (target_rms_db / 20.0)) * 32767.0
        gain = target_rms / rms
    else:
        gain = 1.0

    fpcm *= gain

    peak = float(np.max(np.abs(fpcm))) if len(fpcm) > 0 else 0.0
    if peak > 0:
        peak_limit = limit_ceiling * 32767.0
        if peak > peak_limit:
            limiter_gain = peak_limit / peak
            fpcm *= limiter_gain

    fpcm = np.clip(fpcm, -32768, 32767)
    return fpcm.astype(np.int16)


def gp_adpcm_encode(
    pcm16,
    initial_predictor: int = 0,
    initial_step_index: int = 0,
):
    step_table = [
        16, 17, 19, 21, 23, 25, 28, 31,
        34, 37, 41, 45, 50, 55, 60, 66
    ]
    max_amp = 2047

    predictor = int(initial_predictor)
    step_index = int(initial_step_index)

    pcm_q = np.clip((pcm16.astype(np.int32) // 16), -max_amp, max_amp).astype(np.int32)

    nibbles = []

    for sample in pcm_q:
        diff = int(sample) - predictor
        nib = 0

        if diff < 0:
            nib |= 0x8
            diff = -diff

        step = step_table[step_index]
        contrib = step >> 3

        if diff >= step:
            nib |= 0x4
            contrib += step
            diff -= step

        if diff >= (step >> 1):
            nib |= 0x2
            contrib += (step >> 1)
            diff -= (step >> 1)

        if diff >= (step >> 2):
            nib |= 0x1
            contrib += (step >> 2)

        if nib & 0x8:
            predictor -= contrib
        else:
            predictor += contrib

        predictor = max(-max_amp, min(max_amp, predictor))

        step_index += (nib & 0x7) - 4
        step_index = max(0, min(15, step_index))

        nibbles.append(nib & 0xF)

    out_bytes = bytearray()
    for i in range(0, len(nibbles), 2):
        lo = nibbles[i]
        hi = nibbles[i + 1] if i + 1 < len(nibbles) else 0
        out_bytes.append((lo & 0xF) | ((hi & 0xF) << 4))

    return bytes(out_bytes)


def load_manifest_rows(manifest_path: str):
    rows = []
    with open(manifest_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def get_row_by_index(rows, idx: int):
    for row in rows:
        if int(row["index"]) == idx:
            return row
    raise KeyError(f"Manifest row with index {idx} not found")


def pack_entry_record_offset(pack_abs: int, entry_index: int) -> int:
    """
    parse_pack_children() used:
        table = pack_abs + 0x10
        record for entry i>=1 is table + (i-1)*0x10
        rel_off at +0x08
        size    at +0x0C
    """
    if entry_index <= 0:
        raise ValueError("This helper only supports non-zero entries")
    return pack_abs + 0x10 + (entry_index - 1) * 0x10


def import_merge_next(
    original_bin: str,
    output_bin: str,
    export_dir: str,
    target_index: int = DEFAULT_TARGET_INDEX,
    next_index: int = DEFAULT_NEXT_INDEX,
    dry_run: bool = False,
):
    manifest_path = os.path.join(export_dir, "device_sound_manifest.csv")
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    rows = load_manifest_rows(manifest_path)
    print(f"[*] Loaded manifest rows: {len(rows)}")

    target_row = get_row_by_index(rows, target_index)
    next_row = get_row_by_index(rows, next_index)

    target_wav_name = target_row["wav_file"]
    target_wav_path = os.path.join(export_dir, target_wav_name)
    if not os.path.exists(target_wav_path):
        raise FileNotFoundError(f"Replacement WAV not found: {target_wav_path}")

    target_abs_off = int(target_row["absolute_offset_hex"], 16)
    target_size = int(target_row["size_bytes"])
    target_pack_off = int(target_row["pack_offset_hex"], 16)
    target_entry_index = int(target_row["entry_index"])

    next_abs_off = int(next_row["absolute_offset_hex"], 16)
    next_size = int(next_row["size_bytes"])
    next_pack_off = int(next_row["pack_offset_hex"], 16)
    next_entry_index = int(next_row["entry_index"])

    if target_pack_off != next_pack_off:
        raise RuntimeError("Target and next entries are not in the same pack; refusing")
    if next_abs_off <= target_abs_off:
        raise RuntimeError("Next slot is not after target slot; refusing")

    with open(original_bin, "rb") as f:
        data = bytearray(f.read())

    target_blob = bytes(data[target_abs_off:target_abs_off + target_size])
    next_blob = bytes(data[next_abs_off:next_abs_off + next_size])

    if len(target_blob) != target_size or not target_blob.startswith(b"SPF2ALP"):
        raise RuntimeError(f"Target slot {target_index} is not valid SPF2ALP")
    if len(next_blob) != next_size or not next_blob.startswith(b"SPF2ALP"):
        raise RuntimeError(f"Next slot {next_index} is not valid SPF2ALP")

    target_header = parse_spf2alp_header(target_blob)
    target_rate = target_header["sample_rate"]
    if target_rate <= 0 or target_rate > MAX_SAMPLE_RATE:
        target_rate = 12000

    pcm16 = convert_wav_to_mono_16bit_resampled(target_wav_path, target_rate)
    encoded = gp_adpcm_encode(pcm16, 0, 0)

    # Keep encoded payload even-length so the SPF2 word length is integral.
    if len(encoded) & 1:
        encoded += b"\x00"

    # Available merged region is from target start through end of next slot.
    merged_total_capacity = (next_abs_off + next_size) - target_abs_off
    merged_payload_capacity = merged_total_capacity - SPF2_HEADER_SIZE

    original_encoded_len = len(encoded)
    trimmed = False
    if len(encoded) > merged_payload_capacity:
        encoded = encoded[:merged_payload_capacity]
        if len(encoded) & 1:
            encoded = encoded[:-1]
        trimmed = True

    new_total_size = SPF2_HEADER_SIZE + len(encoded)
    new_audio_words = (new_total_size - 0x28) // 2

    target_payload_start = target_abs_off + SPF2_HEADER_SIZE
    target_write_end = target_payload_start + len(encoded)

    overwritten_into_next = max(0, target_write_end - next_abs_off)

    print(f"[*] Target WAV: {target_wav_name}")
    print(f"[*] Target index: {target_index}")
    print(f"[*] Next index to consume: {next_index}")
    print(f"[*] Target rate: {target_rate} Hz")
    print(f"[*] Original encoded bytes: {original_encoded_len}")
    print(f"[*] Merged payload capacity: {merged_payload_capacity}")
    print(f"[*] New merged total size: {new_total_size}")
    print(f"[*] New audio_length_words: {new_audio_words}")
    print(f"[*] Bytes written into next slot: {overwritten_into_next}")
    if trimmed:
        print("[!] Replacement was too large for the merged two-slot region and was trimmed")

    # Pack entry metadata offsets
    target_rec = pack_entry_record_offset(target_pack_off, target_entry_index)
    next_rec = pack_entry_record_offset(next_pack_off, next_entry_index)

    if not dry_run:
        # 1. Write encoded payload starting at target's payload start
        data[target_payload_start:target_payload_start + len(encoded)] = encoded

        # 2. Update target SPF2 header length
        put_u32le(data, target_abs_off + SPF2_LENGTH_FIELD_OFF, new_audio_words)

        # 3. Update target pack entry size so target now spans both slots (or part thereof)
        put_u32le(data, target_rec + 0x0C, new_total_size)

        # 4. Disable next entry so it is no longer treated as a separate sound
        put_u32le(data, next_rec + 0x08, 0)  # rel_off = 0
        put_u32le(data, next_rec + 0x0C, 0)  # size = 0

        with open(output_bin, "wb") as f:
            f.write(data)

        print(f"[✓] Output BIN written -> {output_bin}")
    else:
        print("[*] Dry run only. Output BIN not written.")

    print("\n--- Summary ---")
    print(f"  Target index merged:      {target_index}")
    print(f"  Next index disabled:      {next_index}")
    print(f"  Encoded bytes used:       {len(encoded)}")
    print(f"  New target total size:    {new_total_size}")
    print(f"  Bytes into next slot:     {overwritten_into_next}")
    print(f"  Trimmed:                  {trimmed}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("original_bin", help="Input BIN")
    ap.add_argument("output_bin", help="Output BIN")
    ap.add_argument("export_dir", help="Folder created by export_device_sounds_bestlisten.py")
    ap.add_argument("--target-index", type=int, default=DEFAULT_TARGET_INDEX,
                    help="Target sound index to replace, default 33")
    ap.add_argument("--next-index", type=int, default=DEFAULT_NEXT_INDEX,
                    help="Next sound index to consume, default 34")
    ap.add_argument("--dry-run", action="store_true", help="Simulate without writing output BIN")
    args = ap.parse_args()

    import_merge_next(
        original_bin=args.original_bin,
        output_bin=args.output_bin,
        export_dir=args.export_dir,
        target_index=args.target_index,
        next_index=args.next_index,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()