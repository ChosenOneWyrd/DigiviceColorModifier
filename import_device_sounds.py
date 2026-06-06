#!/usr/bin/env python3
"""
Safe importer for D-3 device sounds exported by export_device_sounds_bestlisten.py.

It:
- reads device_sound_manifest.csv
- imports replacement WAVs named device_000.wav, device_001.wav, ...
- patches only the exact manifest-owned device sound slots
- preserves BIN size
- preserves original SPF2ALP headers and slot boundaries
- trims or pads encoded payload safely

This importer matches export_device_sounds.py:
    * offset = 0x40
    * nibble order = low_high
    * predictor = 0
    * step_index = 0

Usage:
    python import_device_sounds.py D3.bin D3_patched.bin input_device_sounds
    python import_device_sounds.py D3.bin D3_patched.bin input_device_sounds --dry-run
"""

import os
import csv
import wave
import argparse
import numpy as np


SPF2_DATA_OFFSET = 0x40
MAX_SAMPLE_RATE = 192000


def u32le(buf: bytes, off: int) -> int:
    return int.from_bytes(buf[off:off + 4], "little")


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
    """
    Encode int16 PCM into GP-style ADPCM bytes.

    Matches export_device_sounds_bestlisten.py assumptions:
        - low nibble then high nibble
        - predictor = 0
        - step_index = 0
    """
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
        out_bytes.append((lo & 0xF) | ((hi & 0xF) << 4))  # low then high

    return bytes(out_bytes)


def import_device_sounds(
    original_bin: str,
    output_bin: str,
    export_dir: str,
    dry_run: bool = False,
    verbose: bool = False,
):
    manifest_path = os.path.join(export_dir, "device_sound_manifest.csv")
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    with open(original_bin, "rb") as f:
        data = bytearray(f.read())

    rows = []
    with open(manifest_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    print(f"[*] Loaded manifest rows: {len(rows)}")

    replaced = 0
    trimmed = 0
    padded = 0
    skipped_missing_wav = 0
    skipped_bad_slot = 0
    skipped_resample = 0
    skipped_encode = 0

    for row in rows:
        idx = int(row["index"])
        wav_name = row["wav_file"]
        wav_path = os.path.join(export_dir, wav_name)

        if not os.path.exists(wav_path):
            skipped_missing_wav += 1
            if verbose:
                print(f"[ ] No WAV for index {idx:03d}: {wav_name}")
            continue

        abs_off = int(row["absolute_offset_hex"], 16)
        total_size = int(row["size_bytes"])
        blob = bytes(data[abs_off:abs_off + total_size])

        if len(blob) != total_size or total_size < 0x28 or not blob.startswith(b"SPF2ALP"):
            skipped_bad_slot += 1
            print(f"[!] {idx:03d} invalid slot/header at 0x{abs_off:08X}, skipping")
            continue

        header_info = parse_spf2alp_header(blob)
        target_rate = header_info["sample_rate"]
        if target_rate <= 0 or target_rate > MAX_SAMPLE_RATE:
            target_rate = 12000

        adpcm_start = SPF2_DATA_OFFSET
        if adpcm_start >= total_size:
            skipped_bad_slot += 1
            print(f"[!] {idx:03d} invalid data offset 0x{adpcm_start:X}, skipping")
            continue

        slot_bytes = total_size - adpcm_start
        if slot_bytes <= 0:
            skipped_bad_slot += 1
            print(f"[!] {idx:03d} non-positive slot size, skipping")
            continue

        print(
            f"[+] {idx:03d} {wav_name} -> 0x{abs_off:08X} "
            f"(rate={target_rate}, slot={slot_bytes}, offset=0x{adpcm_start:X}, nibble=low_high)"
        )

        try:
            pcm16 = convert_wav_to_mono_16bit_resampled(wav_path, target_rate)
        except Exception as e:
            skipped_resample += 1
            print(f"    [!] WAV normalization/resample failed: {e}")
            continue

        try:
            encoded = gp_adpcm_encode(
                pcm16,
                initial_predictor=0,
                initial_step_index=0,
            )
        except Exception as e:
            skipped_encode += 1
            print(f"    [!] ADPCM encode failed: {e}")
            continue

        if not encoded:
            skipped_encode += 1
            print("    [!] Encoded ADPCM is empty, skipping")
            continue

        if len(encoded) > slot_bytes:
            encoded = encoded[:slot_bytes]
            trimmed += 1
            print(f"    [!] Encoded ADPCM too large, trimmed to {slot_bytes} bytes")
        elif len(encoded) < slot_bytes:
            pad_amount = slot_bytes - len(encoded)
            encoded += bytes(pad_amount)
            padded += 1
            if verbose:
                print(f"    [pad] padded by {pad_amount} bytes")

        if len(encoded) != slot_bytes:
            skipped_encode += 1
            print("    [!] Size mismatch after trim/pad, skipping")
            continue

        if not dry_run:
            data[abs_off + adpcm_start:abs_off + total_size] = encoded

        replaced += 1
        print("    [✓] Patched safely")

    if dry_run:
        print("\n[*] Dry run only. Output BIN not written.")
    else:
        with open(output_bin, "wb") as f:
            f.write(data)
        print(f"\n[✓] Output BIN written -> {output_bin}")

    print("\n--- Summary ---")
    print(f"  Replaced sounds:         {replaced}")
    print(f"  Trimmed (clipped):       {trimmed}")
    print(f"  Padded:                  {padded}")
    print(f"  Skipped (no WAV):        {skipped_missing_wav}")
    print(f"  Skipped (bad slot):      {skipped_bad_slot}")
    print(f"  Skipped (resample):      {skipped_resample}")
    print(f"  Skipped (encode):        {skipped_encode}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("original_bin", help="Input BIN")
    ap.add_argument("output_bin", help="Output BIN")
    ap.add_argument("export_dir", help="Folder created by export_device_sounds_bestlisten.py")
    ap.add_argument("--dry-run", action="store_true", help="Simulate without writing output BIN")
    ap.add_argument("--verbose", action="store_true", help="Verbose logs")
    args = ap.parse_args()

    import_device_sounds(
        original_bin=args.original_bin,
        output_bin=args.output_bin,
        export_dir=args.export_dir,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
