#!/usr/bin/env python3
"""
D-3: Encode WAVs with a1800.dll and safely reinject into D-3.bin

This script:

  - Uses a1800.dll's `enc` function to convert WAV -> .a18
  - Uses the same reinjection logic as d3_reinject_from_a18_flex.py
  - Safely writes the encoded audio into D-3.bin

Requirements / assumptions:
  - Input WAVs must be:
        mono, 16-bit PCM, 16000 Hz
    (same format your extractor produces)
  - a1800.dll (or a1800e.dll) is in the same folder or you pass --dll
  - CSV mapping is like your reinject map:
        original,new
        chunk_00A3.a18.wav,angemon_warp_shinka.wav
    Here:
      - "original" is the chunk name (as used by extract / decode)
      - "new" is the WAV filename we will encode

Usage:
    python d3_encode_wav_and_reinject.py \
        original.bin \
        output.bin \
        reinject_map.csv \
        wav_dir \
        --dll a1800e.dll \
        --dry-run \
        --verbose
"""

import sys
import os
import csv
import ctypes
from ctypes.wintypes import LPCSTR, UINT
import wave
import struct
import numpy as np

def convert_wav_to_mono_16bit_16000(in_path, out_path,
                                    target_rms_db=-12.0,
                                    limit_ceiling=0.98):
    """
    Convert WAV to mono, 16-bit PCM, 16000 Hz.
    Adds:
        - Loudness normalization (RMS → target level)
        - Limiting to avoid clipping

    target_rms_db : desired RMS loudness (default: -12 dBFS)
                    -12 dBFS is strong but clean for the D-3 speaker.
    limit_ceiling : max allowed peak (0.98 = 98% of full-scale)
    """

    # ---- LOAD WAV ----
    with wave.open(in_path, "rb") as wf:
        nch = wf.getnchannels()
        sw  = wf.getsampwidth()
        sr  = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)

    # ---- CONVERT SAMPLE WIDTH ----
    if sw == 1:
        # 8-bit PCM unsigned → convert to signed int16
        pcm = np.frombuffer(raw, dtype=np.uint8).astype(np.int16)
        pcm = (pcm - 128) * 256
    elif sw == 2:
        pcm = np.frombuffer(raw, dtype=np.int16)
    else:
        raise ValueError(f"Unsupported sample width {sw} in {in_path}")

    # ---- CHANNEL MIX ----
    if nch == 2:
        pcm = pcm.reshape(-1, 2).mean(axis=1).astype(np.int16)
    elif nch != 1:
        raise ValueError(f"Unsupported channel count {nch}")

    # ---- RESAMPLE TO 16000 Hz ----
    if sr != 16000:
        duration = len(pcm) / sr
        new_length = int(duration * 16000)
        pcm = np.interp(
            np.linspace(0, len(pcm), new_length),
            np.arange(len(pcm)),
            pcm
        ).astype(np.int16)

    # Convert to float for DSP
    fpcm = pcm.astype(np.float32)

    # ---- LOUDNESS NORMALIZATION ----
    # Compute RMS (normalized to 1.0 scale)
    rms = np.sqrt(np.mean(fpcm**2))

    if rms > 0:
        target_rms = 10.0 ** (target_rms_db / 20.0) * 32767
        gain = target_rms / rms
    else:
        gain = 1.0

    fpcm *= gain

    # ---- LIMITING / ANTI-CLIPPING ----
    peak = np.max(np.abs(fpcm))
    if peak > 0:
        peak_limit = limit_ceiling * 32767
        if peak > peak_limit:
            limiter_gain = peak_limit / peak
            fpcm *= limiter_gain

    # ---- CONVERT BACK TO INT16 ----
    fpcm = np.clip(fpcm, -32768, 32767)
    pcm_out = fpcm.astype(np.int16)

    # ---- WRITE NORMALIZED WAV ----
    with wave.open(out_path, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(pcm_out.tobytes())

    return out_path

A18_HEADER = bytes([0x00, 0x00, 0x80, 0x3E])
MAX_VALID_LEN = 0x200000  # 2 MB

# ---------------------------------------------------------
# DLL BINDING (ENC)
# ---------------------------------------------------------

def load_a1800_enc(dll_path: str):
    """
    Load a1800.dll / a1800e.dll and bind the enc function.

    C signature (from your Ghidra decomp):

        int a1800_enc(
            LPCSTR infile,
            LPCSTR outfile,
            int    samplerate,
            short* fh_out,
            void*  progress_cb  // can be NULL
        );

    We expose it as:

        enc(wav_path, a18_path, samplerate, &fh, None) -> int error_code
    """
    dll = ctypes.WinDLL(os.path.abspath(dll_path))

    # argtypes: const char*, const char*, int, short*, void*
    dll.enc.argtypes = [
        LPCSTR,                        # infile
        LPCSTR,                        # outfile
        ctypes.c_int,                  # samplerate
        ctypes.POINTER(ctypes.c_short),# fh_out
        ctypes.c_void_p,               # callback (NULL)
    ]
    dll.enc.restype = ctypes.c_int

    return dll.enc


def encode_wav_to_a18_via_dll(
    encfunc,
    wav_path: str,
    out_a18_path: str,
    expected_samplerate: int = 16000,
    verbose: bool = False,
):
    """
    Call a1800_enc to convert a WAV into an A18 file.

    Preconditions:
      - wav_path: mono, 16-bit, PCM, expected_samplerate Hz.
    """

    # Verify WAV format
    with wave.open(wav_path, "rb") as wf:
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr = wf.getframerate()

    if nch != 1 or sw != 2 or sr != expected_samplerate:
        raise ValueError(
            f"WAV {wav_path} must be mono, 16-bit, {expected_samplerate} Hz "
            f"(got channels={nch}, sampwidth={sw}, rate={sr})"
        )

    in_c = LPCSTR(os.path.abspath(wav_path).encode("mbcs", errors="replace"))
    out_c = LPCSTR(os.path.abspath(out_a18_path).encode("mbcs", errors="replace"))

    fh = ctypes.c_short(0)

    if verbose:
        print(f"  [enc] {wav_path} -> {out_a18_path} @ {expected_samplerate} Hz")

    ret = encfunc(in_c, out_c, expected_samplerate, ctypes.byref(fh), None)

    if verbose:
        print(f"  [enc] DLL returned {ret}, fh={fh.value}")

    if ret != 0:
        raise RuntimeError(f"a1800_enc failed for {wav_path} with code {ret}")

    if not os.path.exists(out_a18_path):
        raise RuntimeError(f"a1800_enc did not create output file: {out_a18_path}")

    return out_a18_path


# ---------------------------------------------------------
# CHUNK SCANNING (same as d3_reinject_from_a18_flex.py)
# ---------------------------------------------------------

def scan_chunks(data: bytearray):
    chunks = []
    filesize = len(data)
    pos = 0
    idx = 0

    while True:
        header = data.find(A18_HEADER, pos)
        if header == -1:
            break

        len_pos = header - 2
        if len_pos < 0:
            pos = header + 1
            continue

        payload_len = data[len_pos] | (data[len_pos + 1] << 8)
        if payload_len == 0 or payload_len > MAX_VALID_LEN:
            pos = header + 1
            continue

        start = len_pos
        end = start + 2 + 4 + payload_len
        if end > filesize:
            pos = header + 1
            continue

        chunks.append((idx, start, end, payload_len))
        idx += 1
        pos = header + 3

    return chunks


# ---------------------------------------------------------
# CSV mapping (same semantics as your reinject script)
# ---------------------------------------------------------

def parse_index_string(s):
    s = s.strip()
    if not s:
        return None
    if s.lower().startswith("0x"):
        try:
            return int(s, 16)
        except ValueError:
            return None
    try:
        return int(s)
    except ValueError:
        return None


def load_replace_map(csv_path):
    """
    Returns:
      name_map:  dict[original_name] -> new_name
      index_map: dict[index] -> new_name

    original can be "chunk_XXXX.a18.wav" or numeric index.
    new is interpreted here as WAV filename.
    """
    name_map = {}
    index_map = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            o = row["original"].strip()
            n = row["new"].strip()
            if not o or not n:
                continue
            if o.lower().startswith("chunk_") and o.lower().endswith(".a18.wav"):
                name_map[o] = n
            else:
                idx = parse_index_string(o)
                if idx is not None:
                    index_map[idx] = n
                else:
                    name_map[o] = n
    return name_map, index_map


# ---------------------------------------------------------
# Extract D-3-style chunk from raw .a18 (same as flex script)
# ---------------------------------------------------------

def extract_d3_chunk_like(raw: bytes):
    n = len(raw)

    # Case 1: raw already looks like D-3/A18:
    #   [len_lo len_hi][00 00 80 3E][payload...]
    if n >= 6 and raw[2:6] == A18_HEADER:
        length = raw[0] | (raw[1] << 8)
        needed = 2 + 4 + length
        if needed <= n <= needed + 16:
            return raw[:needed]

    # Case 2: search inside for header
    best = None
    best_overrun = None

    pos = 0
    while True:
        header = raw.find(A18_HEADER, pos)
        if header == -1:
            break
        pos = header + 1

        if header < 2:
            continue
        len_pos = header - 2

        length = raw[len_pos] | (raw[len_pos + 1] << 8)
        if length == 0 or length > MAX_VALID_LEN:
            continue

        start = len_pos
        end = start + 6 + length
        overrun = end - n

        if overrun <= 4:
            if best is None or overrun < best_overrun:
                best = (start, end)
                best_overrun = overrun

    if best is None:
        return None

    start, end = best
    chunk = raw[start:n]
    if end > n:
        chunk += bytes(end - n)

    return chunk


# ---------------------------------------------------------
# Main pipeline: encode + reinject
# ---------------------------------------------------------

def encode_and_reinject(
    original_bin: str,
    output_bin: str,
    csv_path: str,
    wav_dir: str,
    dll_path: str | None,
    dry_run: bool = False,
    verbose: bool = False,
):
    # Auto-detect DLL if not provided
    search_paths = []

    # If user passed something, try that first
    if dll_path:
        search_paths.append(dll_path)

    # Script directory (where this .py lives)
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        script_dir = os.getcwd()

    # Prefer a1800.dll, then a1800e.dll
    for name in ("a1800.dll", "a1800e.dll"):
        search_paths.append(os.path.join(script_dir, name))
        search_paths.append(os.path.join(os.getcwd(), name))

    chosen_dll = None
    tried = []

    for cand in search_paths:
        cand = os.path.abspath(cand)
        if cand in tried:
            continue
        tried.append(cand)
        if os.path.exists(cand):
            chosen_dll = cand
            break

    if chosen_dll is None:
        msg = "[!] Could not find a1800 DLL. Tried:\n"
        msg += "\n".join(f"    {p}" for p in tried)
        raise FileNotFoundError(msg)

    # Load DLL encoder
    encfunc = load_a1800_enc(chosen_dll)
    print(f"[*] Loaded encoder DLL: {chosen_dll}")

    # Load BIN
    with open(original_bin, "rb") as f:
        data = bytearray(f.read())

    chunks = scan_chunks(data)
    print(f"[*] Found {len(chunks)} A18 chunks")

    name_map, index_map = load_replace_map(csv_path)
    print(f"[*] Loaded {len(name_map)} name mappings, {len(index_map)} index mappings")

    # Temp dir for encoded .a18 files
    import tempfile
    tmp_dir = tempfile.mkdtemp(prefix="d3_enc_a18_")

    replaced = skipped_map = skipped_missing = skipped_extract = 0
    trimmed = 0

    for idx, start, end, orig_payload_len in chunks:
        orig_name = f"chunk_{idx:04X}.a18.wav"

        if orig_name in name_map:
            wav_name = name_map[orig_name]
        elif idx in index_map:
            wav_name = index_map[idx]
        else:
            skipped_map += 1
            continue

        wav_path = os.path.join(wav_dir, wav_name)
        if not os.path.exists(wav_path):
            skipped_missing += 1
            print(f"[!] Missing WAV file for chunk {idx:04X}: {wav_path}")
            continue

        print(f"\n[+] Chunk {idx:04X}: encoding and reinjecting from {wav_name}")

        # 1) Encode WAV -> .a18 via DLL into temp file
        tmp_a18 = os.path.join(tmp_dir, f"chunk_{idx:04X}.a18")
        # Normalize WAV first
        norm_wav = os.path.join(tmp_dir, f"chunk_{idx:04X}_norm.wav")
        try:
            convert_wav_to_mono_16bit_16000(wav_path, norm_wav)
            if verbose:
                print(f"    [norm] Normalized WAV saved: {norm_wav}")
        except Exception as e:
            print(f"    [!] WAV normalization failed: {e}")
            skipped_extract += 1
            continue

        # Encode normalized WAV
        try:
            encode_wav_to_a18_via_dll(encfunc, norm_wav, tmp_a18, verbose=verbose)
        except Exception as e:
            print(f"    [!] Encoding failed: {e}")
            skipped_extract += 1
            continue

        # 2) Extract D-3-style chunk from .a18 (in case of container headers)
        raw = open(tmp_a18, "rb").read()
        chunk = extract_d3_chunk_like(raw)
        if chunk is None:
            skipped_extract += 1
            print("    [!] Cannot extract D-3-style chunk from encoded file")
            continue

        slot_size = end - start
        new_len = chunk[0] | (chunk[1] << 8)
        new_payload = chunk[6:6 + new_len]

        slot_payload = slot_size - 6

        # SAFE TRIM if payload too large
        if len(new_payload) > slot_payload:
            print(f"    [!] Payload too large ({len(new_payload)} > {slot_payload}), TRIMMING")
            trimmed += 1
            new_payload = new_payload[:slot_payload]

            new_len_lo = slot_payload & 0xFF
            new_len_hi = (slot_payload >> 8) & 0xFF
            chunk = bytes([new_len_lo, new_len_hi]) + A18_HEADER + new_payload

        # Pad if short
        if len(chunk) < slot_size:
            chunk += bytes(slot_size - len(chunk))

        if len(chunk) != slot_size:
            print("    [!] Size mismatch after trim/pad — skipping for safety")
            continue

        if dry_run:
            print("    [✓] DRY RUN — would write safely")
        else:
            data[start:end] = chunk
            print("    [✓] Written safely")

        replaced += 1

    if dry_run:
        print("\n[*] Dry run; not writing output BIN.")
    else:
        with open(output_bin, "wb") as f:
            f.write(data)
        print(f"\n[✓] Output written -> {output_bin}")

    print("\n--- Summary ---")
    print(f"  Replaced:            {replaced}")
    print(f"  Trimmed:             {trimmed}")
    print(f"  Skipped (no map):    {skipped_map}")
    print(f"  Skipped (missing):   {skipped_missing}")
    print(f"  Skipped (encode/extract): {skipped_extract}")


# ---------------------------------------------------------
# CLI
# ---------------------------------------------------------

def main():
    import argparse

    p = argparse.ArgumentParser(
        description="Encode WAVs with a1800.dll and safely reinject into D-3.bin"
    )
    p.add_argument("original_bin", help="Input D-3.bin (original)")
    p.add_argument("output_bin", help="Output patched BIN")
    p.add_argument("csv_map", help="CSV mapping (original,new) where new is WAV filename")
    p.add_argument("wav_dir", help="Directory containing WAV files")
    p.add_argument(
        "--dll",
        dest="dll_path",
        default=None,
        help="Optional path to a1800 DLL; if omitted, tries a1800.dll / a1800e.dll next to this script and in CWD",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate only; do not write output BIN",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose DLL/encoding logs",
    )

    args = p.parse_args()

    encode_and_reinject(
        original_bin=args.original_bin,
        output_bin=args.output_bin,
        csv_path=args.csv_map,
        wav_dir=args.wav_dir,
        dll_path=args.dll_path,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
