#!/usr/bin/env python3
"""
D-3: Safely re-import SPF2ALP GeneralPlus ADPCM WAVs into BIN (no CSV)

This script:

  - Scans a BIN for SPF2ALP blocks (magic b"SPF2ALP\\x00")
  - Treats each block as:
        [SPF2ALP header (0x40 bytes)] + [ADPCM payload ...]
    where:
        sample_rate = LE u32 at header offset +0x10
  - Replaces ONLY the ADPCM payload with re-encoded data from WAV

Safety guarantees:

  - BIN size is unchanged.
  - Each SPF2ALP block keeps its original total size and header bytes.
  - New ADPCM is:
        * Trimmed if it would overflow the slot  (=> audio is clipped in time)
        * Padded if shorter                     (=> trailing silence)
  - If anything looks wrong, the block is skipped with a warning.

Naming convention (no CSV):

  For SPF2ALP block index i (0-based), the replacement WAV is:

      spf2alp_{i:03d}.wav

  placed inside the WAV input directory.

Example:

    python import_device_sounds.py D3.bin D3.bin input_device_sounds --dry-run
    python import_device_sounds.py D3.bin D3.bin input_device_sounds
"""

import os
import struct
import wave
import argparse
import numpy as np

MAGIC_SPF2ALP = b"SPF2ALP\x00"
SPF2_HEADER_SIZE = 0x40  # known good for D-3 SPF2 headers
MAX_SAMPLE_RATE = 192000  # sanity cap


# ---------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------

def ensure_dir(d):
    if not os.path.isdir(d):
        os.makedirs(d)


def le32(buf, off):
    return struct.unpack_from("<I", buf, off)[0]


# ---------------------------------------------------------
# SPF2ALP scanning
# ---------------------------------------------------------

def scan_spf2alp_blocks(data):
    """
    Scan BIN for SPF2ALP blocks.

    Returns a list of dicts:
      {
        "index": idx,          # 0-based block index
        "start": start,        # offset of magic 'S'
        "end": end,            # end offset (exclusive)
        "adpcm_start": adpcm_start,
        "adpcm_end": adpcm_end,
        "slot_bytes": slot_bytes,
        "sample_rate": rate,
      }
    """
    buf = data
    filesize = len(buf)

    positions = []
    pos = 0
    while True:
        pos = buf.find(MAGIC_SPF2ALP, pos)
        if pos == -1:
            break
        positions.append(pos)
        pos += 1

    blocks = []
    for idx, start in enumerate(positions):
        end = positions[idx + 1] if idx + 1 < len(positions) else filesize
        if end <= start + SPF2_HEADER_SIZE:
            # too small / corrupt block
            continue

        header_start = start
        header_end = start + SPF2_HEADER_SIZE
        adpcm_start = header_end
        adpcm_end = end
        slot_bytes = adpcm_end - adpcm_start

        if slot_bytes <= 0:
            continue

        # sample rate at offset +0x10 from header start (same as extractor)
        if header_end - header_start >= 0x14:
            rate = le32(buf, header_start + 0x10)
            if rate == 0 or rate > MAX_SAMPLE_RATE:
                rate = 44100
        else:
            rate = 44100

        blocks.append({
            "index": idx,
            "start": start,
            "end": end,
            "adpcm_start": adpcm_start,
            "adpcm_end": adpcm_end,
            "slot_bytes": slot_bytes,
            "sample_rate": rate,
        })

    return blocks


# ---------------------------------------------------------
# WAV normalization / resampling
# ---------------------------------------------------------

def convert_wav_to_mono_16bit_resampled(
    in_path,
    target_rate,
    target_rms_db=-12.0,
    limit_ceiling=0.98,
):
    """
    Convert WAV to:
        mono, 16-bit PCM, target_rate Hz

    Adds:
        - Loudness normalization (RMS -> target_rms_db)
        - Limiting to avoid clipping

    Returns a numpy.int16 array.
    """

    with wave.open(in_path, "rb") as wf:
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)

    # Convert sample width
    if sw == 1:
        # 8-bit unsigned -> signed int16
        pcm = np.frombuffer(raw, dtype=np.uint8).astype(np.int16)
        pcm = (pcm - 128) * 256
    elif sw == 2:
        pcm = np.frombuffer(raw, dtype=np.int16)
    else:
        raise ValueError(f"Unsupported sample width {sw} in {in_path}")

    # Mix channels
    if nch == 2:
        pcm = pcm.reshape(-1, 2).mean(axis=1).astype(np.int16)
    elif nch != 1:
        raise ValueError(f"Unsupported channel count {nch} in {in_path}")

    # Resample to target_rate
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

    # Convert to float for DSP
    fpcm = pcm.astype(np.float32)

    # Loudness normalization
    rms = np.sqrt(np.mean(fpcm**2)) if len(fpcm) > 0 else 0.0
    if rms > 0:
        target_rms = (10.0 ** (target_rms_db / 20.0)) * 32767.0
        gain = target_rms / rms
    else:
        gain = 1.0

    fpcm *= gain

    # Limiting
    peak = float(np.max(np.abs(fpcm))) if len(fpcm) > 0 else 0.0
    if peak > 0:
        peak_limit = limit_ceiling * 32767.0
        if peak > peak_limit:
            limiter_gain = peak_limit / peak
            fpcm *= limiter_gain

    # Back to int16
    fpcm = np.clip(fpcm, -32768, 32767)
    return fpcm.astype(np.int16)


# ---------------------------------------------------------
# GeneralPlus ADPCM encoder (inverse of your decoder)
# ---------------------------------------------------------

def gp_adpcm_encode(pcm16, initial_predictor=0, initial_step_index=0):
    """
    Encode int16 PCM into GeneralPlus ADPCM bytes.

    Mirrors gp_adpcm_decode:
        - step_table[16]
        - predictor range [-2047, 2047]
        - step_index range [0, 15]
        - nibble order: low nibble then high nibble in each byte
    """
    step_table = [
        16,17,19,21,23,25,28,31,
        34,37,41,45,50,55,60,66
    ]
    max_amp = 2047

    predictor = int(initial_predictor)
    step_index = int(initial_step_index)

    # Quantize PCM to ~the same scale decoder uses (predictor * 16)
    # predictor operates on roughly sample/16 domain
    pcm_q = np.clip((pcm16.astype(np.int32) // 16), -max_amp, max_amp).astype(np.int32)

    nibbles = []

    for sample in pcm_q:
        # desired difference
        diff = int(sample) - predictor
        nib = 0

        if diff < 0:
            nib |= 0x8
            diff = -diff

        step = step_table[step_index]

        # Build nibble bits and approximate diff as in decoder
        # base contribution
        contrib = step >> 3

        # largest term: step
        if diff >= step:
            nib |= 0x4
            contrib += step
            diff -= step

        # mid term: step >> 1
        if diff >= (step >> 1):
            nib |= 0x2
            contrib += (step >> 1)
            diff -= (step >> 1)

        # smallest term: step >> 2
        if diff >= (step >> 2):
            nib |= 0x1
            contrib += (step >> 2)

        # Apply sign
        if nib & 0x8:
            predictor -= contrib
        else:
            predictor += contrib

        # Clamp predictor
        if predictor < -max_amp:
            predictor = -max_amp
        elif predictor > max_amp:
            predictor = max_amp

        # Update step_index
        step_index += (nib & 0x7) - 4
        if step_index < 0:
            step_index = 0
        elif step_index > 15:
            step_index = 15

        nibbles.append(nib & 0xF)

    # Pack two nibbles per byte: low then high
    out_bytes = bytearray()
    for i in range(0, len(nibbles), 2):
        lo = nibbles[i]
        hi = nibbles[i+1] if i+1 < len(nibbles) else 0
        out_bytes.append((lo & 0xF) | ((hi & 0xF) << 4))

    return bytes(out_bytes)


# ---------------------------------------------------------
# Main reinjection logic (no CSV)
# ---------------------------------------------------------

def import_spf2alp_sounds(
    original_bin,
    output_bin,
    wav_dir,
    dry_run=False,
    verbose=False,
):
    # Load BIN
    with open(original_bin, "rb") as f:
        data = bytearray(f.read())

    # Scan SPF2ALP blocks
    blocks = scan_spf2alp_blocks(data)
    print(f"[*] Found {len(blocks)} SPF2ALP blocks")

    replaced = 0
    trimmed = 0
    padded = 0
    skipped_missing_wav = 0
    skipped_resample = 0
    skipped_encode = 0

    for b in blocks:
        idx = b["index"]
        adpcm_start = b["adpcm_start"]
        adpcm_end = b["adpcm_end"]
        slot_bytes = b["slot_bytes"]
        rate = b["sample_rate"]

        orig_name = f"spf2alp_{idx:03d}.wav"
        wav_path = os.path.join(wav_dir, orig_name)

        if not os.path.exists(wav_path):
            skipped_missing_wav += 1
            if verbose:
                print(f"[ ] No replacement WAV for block {idx:03d}: {wav_path}")
            continue

        print(f"\n[+] Block {idx:03d} @0x{b['start']:X}, rate={rate} Hz, "
              f"slot={slot_bytes} bytes")
        print(f"    Using WAV: {orig_name}")

        # 1) Normalize + resample WAV
        try:
            pcm16 = convert_wav_to_mono_16bit_resampled(wav_path, rate)
        except Exception as e:
            skipped_resample += 1
            print(f"    [!] WAV normalization/resample failed: {e}")
            continue

        if verbose:
            print(f"    [norm] {len(pcm16)} samples @ {rate} Hz")

        # 2) Encode to GP ADPCM
        try:
            encoded = gp_adpcm_encode(pcm16)
        except Exception as e:
            skipped_encode += 1
            print(f"    [!] ADPCM encode failed: {e}")
            continue

        if len(encoded) == 0:
            skipped_encode += 1
            print("    [!] Encoded ADPCM is empty, skipping for safety")
            continue

        # 3) Fit into slot (trim or pad)
        if len(encoded) > slot_bytes:
            # too long: trim -> audio clipped
            print(f"    [!] ADPCM too large ({len(encoded)} > {slot_bytes}), TRIMMING")
            encoded = encoded[:slot_bytes]
            trimmed += 1
        elif len(encoded) < slot_bytes:
            # shorter: pad with zeros (silence-ish)
            pad_len = slot_bytes - len(encoded)
            encoded += bytes(pad_len)
            padded += 1
            if verbose:
                print(f"    [pad] Padded ADPCM by {pad_len} bytes")

        # Final safety check
        if len(encoded) != slot_bytes:
            print("    [!] Size mismatch after trim/pad, skipping for safety")
            skipped_encode += 1
            continue

        if dry_run:
            print("    [✓] DRY RUN — would write ADPCM safely into BIN")
        else:
            data[adpcm_start:adpcm_end] = encoded
            print("    [✓] Written ADPCM safely into BIN")

        replaced += 1

    # Write output
    if dry_run:
        print("\n[*] Dry run; NOT writing output BIN.")
    else:
        with open(output_bin, "wb") as f:
            f.write(data)
        print(f"\n[✓] Output BIN written -> {output_bin}")

    # Summary
    print("\n--- Summary ---")
    print(f"  Replaced blocks:          {replaced}")
    print(f"  Trimmed (clipped):        {trimmed}")
    print(f"  Padded (silence):         {padded}")
    print(f"  Skipped (no WAV file):    {skipped_missing_wav}")
    print(f"  Skipped (resample fail):  {skipped_resample}")
    print(f"  Skipped (encode/size):    {skipped_encode}")


# ---------------------------------------------------------
# CLI
# ---------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="Safely reimport SPF2ALP GeneralPlus ADPCM WAVs into BIN (no CSV)"
    )
    p.add_argument("original_bin", help="Input BIN (original)")
    p.add_argument("output_bin", help="Output BIN (patched)")
    p.add_argument("wav_dir", help="Directory containing replacement WAVs (spf2alp_xxx.wav)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate only; do NOT write output BIN",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose logs",
    )

    args = p.parse_args()

    import_spf2alp_sounds(
        original_bin=args.original_bin,
        output_bin=args.output_bin,
        wav_dir=args.wav_dir,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
