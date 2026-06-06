#!/usr/bin/env python3
"""
Robust D-3 WAV importer / reinjector
Uses the same candidate discovery logic as the robust exporter,
then safely encodes WAV -> A18 and reinjects into the BIN without
changing file layout.

Usage:
    python import_sounds.py D3_orig.bin D3_patched.bin d3_sound_map.csv input_sounds
    python import_sounds.py D3_mod.bin  D3_mod_patched.bin d3_sound_map.csv input_sounds
"""

import os
import re
import csv
import wave
import ctypes
import shutil
import argparse
import tempfile
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional, Set
from ctypes.wintypes import LPCSTR
import numpy as np


PACK_BASE = 0x140000
A18_MARKER = b"\x00\x00\x80\x3E"

MAX_AUDIO_SIZE = 0x400000
MIN_AUDIO_SIZE = 8

PACK_ENTRY_SIZE = 16
HEADER_SIZE_CANDIDATES = (0, 8, 12, 16, 20, 24, 28, 32, 36, 40)

MAX_PACK_ENTRIES = 512
MIN_GOOD_ENTRIES_FOR_PACK = 3
MAX_PACK_RECURSION_DEPTH = 4
MAX_TOTAL_PACKS_VISITED = 256
MAX_TOTAL_PACK_AUDIO_FOUND = 512
MAX_CHILD_PACKS_PER_PARENT = 64
GIANT_PACK_RECURSE_SCORE = 6

AUDIO_TOTAL_DELTA_CANDIDATES = (6, 4, 8, 2, 10)


@dataclass
class PackEntry:
    index: int
    rel_off: int
    abs_off: int
    stored_size: int
    payload_size: int
    flags: int


@dataclass
class AudioCandidate:
    absolute_offset: int
    blob: bytes
    source: str
    pack_offset: int = -1
    entry_index: int = -1
    stored_size: int = 0
    payload_size: int = 0
    declared_len: int = 0
    path_hint: str = ""
    size_variant: str = ""
    notes: str = ""


@dataclass
class WalkState:
    packs_visited: int = 0
    audio_found: int = 0


def u16le(data: bytes, off: int) -> int:
    return int.from_bytes(data[off:off + 2], "little")


def u32le(data: bytes, off: int) -> int:
    return int.from_bytes(data[off:off + 4], "little")


def safe_slice(data: bytes, off: int, size: int) -> bytes:
    if off < 0 or size < 0 or off + size > len(data):
        return b""
    return data[off:off + size]


def is_probable_audio_u32_at(data: bytes, off: int) -> Tuple[bool, int]:
    if off < 0 or off + 6 > len(data):
        return False, 0
    declared = u32le(data, off)
    if declared <= 0 or declared > MAX_AUDIO_SIZE:
        return False, 0
    if data[off + 4] != 0x80 or data[off + 5] != 0x3E:
        return False, 0
    return True, declared


def best_u32_audio_blob(data: bytes, off: int) -> Optional[Tuple[bytes, int, str]]:
    ok, declared = is_probable_audio_u32_at(data, off)
    if not ok:
        return None

    for delta in AUDIO_TOTAL_DELTA_CANDIDATES:
        total = declared + delta
        if total < MIN_AUDIO_SIZE or total > MAX_AUDIO_SIZE:
            continue
        blob = safe_slice(data, off, total)
        if len(blob) != total:
            continue
        if len(blob) >= 6 and blob[4] == 0x80 and blob[5] == 0x3E:
            return blob, declared, f"u32+{delta}"
    return None


def best_u16_audio_blob(data: bytes, start: int) -> Optional[Tuple[bytes, int, str]]:
    if start < 0 or start + 6 > len(data):
        return None

    declared = u16le(data, start)
    if declared <= 0 or declared > MAX_AUDIO_SIZE:
        return None

    if data[start + 2:start + 6] != A18_MARKER:
        return None

    total = 2 + 4 + declared
    blob = safe_slice(data, start, total)
    if len(blob) != total:
        return None

    return blob, declared, "u16+6"


def score_entry_plausibility(data: bytes, base: int, p: int) -> Tuple[bool, Optional[PackEntry], int]:
    if p < 0 or p + PACK_ENTRY_SIZE > len(data):
        return False, None, 0

    rel_off = u32le(data, p + 0)
    stored_size = u32le(data, p + 4)
    payload_size = u32le(data, p + 8)
    flags = u32le(data, p + 12)

    if rel_off == 0:
        return False, None, 0

    abs_off = base + rel_off
    if abs_off < 0 or abs_off >= len(data):
        return False, None, 0

    if stored_size <= 0 or stored_size > MAX_AUDIO_SIZE:
        return False, None, 0

    if abs_off + stored_size > len(data):
        return False, None, 0

    score = 0
    if 0 < payload_size <= stored_size + 16:
        score += 2
    if abs(stored_size - payload_size) <= 16:
        score += 1

    audio_ok, _ = is_probable_audio_u32_at(data, abs_off)
    if audio_ok:
        score += 4

    if (rel_off & 0x3) == 0:
        score += 1

    entry = PackEntry(
        index=0,
        rel_off=rel_off,
        abs_off=abs_off,
        stored_size=stored_size,
        payload_size=payload_size,
        flags=flags,
    )
    return True, entry, score


def quick_pack_score(data: bytes, base: int) -> int:
    best = 0
    for hdr in HEADER_SIZE_CANDIDATES:
        table = base + hdr
        good = 0
        for i in range(4):
            p = table + i * PACK_ENTRY_SIZE
            ok, _, score = score_entry_plausibility(data, base, p)
            if not ok or score < 2:
                break
            good += 1
        if good > best:
            best = good
    return best


def parse_tolerant_pack(data: bytes, base: int) -> Optional[List[PackEntry]]:
    if base < 0 or base + 32 > len(data):
        return None

    best_entries = None
    best_score = -1

    for hdr in HEADER_SIZE_CANDIDATES:
        table = base + hdr
        entries = []
        total_score = 0
        bad_streak = 0
        weak_streak = 0

        for i in range(MAX_PACK_ENTRIES):
            p = table + i * PACK_ENTRY_SIZE
            if p + PACK_ENTRY_SIZE > len(data):
                break

            ok, ent, score = score_entry_plausibility(data, base, p)
            if not ok:
                bad_streak += 1
                if not entries or bad_streak >= 2:
                    break
                continue

            bad_streak = 0

            if score < 2:
                weak_streak += 1
                if weak_streak >= 2:
                    break
            else:
                weak_streak = 0

            if score < 1:
                break

            ent.index = i
            entries.append(ent)
            total_score += score

        if len(entries) >= MIN_GOOD_ENTRIES_FOR_PACK and total_score > best_score:
            best_entries = entries
            best_score = total_score

    return best_entries


def scan_u32_audio(data: bytes) -> List[AudioCandidate]:
    out = []
    i = 0
    n = len(data)

    while i < n - 8:
        if data[i + 4:i + 6] == b"\x80\x3E":
            info = best_u32_audio_blob(data, i)
            if info is not None:
                blob, declared, variant = info
                out.append(AudioCandidate(
                    absolute_offset=i,
                    blob=blob,
                    source="raw_u32_scan",
                    declared_len=declared,
                    stored_size=len(blob),
                    payload_size=max(0, len(blob) - 2),
                    size_variant=variant,
                ))
                i += max(4, min(len(blob) // 8, 0x1000))
                continue
        i += 1

    return out


def scan_u16_audio(data: bytes) -> List[AudioCandidate]:
    out = []
    pos = 0
    while True:
        idx = data.find(A18_MARKER, pos)
        if idx == -1:
            break
        start = idx - 2
        info = best_u16_audio_blob(data, start)
        if info is not None:
            blob, declared, variant = info
            out.append(AudioCandidate(
                absolute_offset=start,
                blob=blob,
                source="raw_u16_scan",
                declared_len=declared,
                stored_size=len(blob),
                payload_size=max(0, len(blob) - 2),
                size_variant=variant,
            ))
        pos = idx + 1
    return out


def discover_root_packs_from_audio(data: bytes, raw_u32: List[AudioCandidate]) -> List[int]:
    candidate_bases = set()

    for cand in raw_u32:
        aoff = cand.absolute_offset

        for rel in (
            0x20, 0x40, 0x60, 0x80, 0x100, 0x200, 0x400, 0x800,
            0x1000, 0x2000, 0x4000, 0x8000, 0x10000, 0x20000,
            0x40000, 0x80000
        ):
            base = aoff - rel
            if base < 0:
                continue
            candidate_bases.add(base & ~0x3)

        if PACK_BASE <= aoff <= PACK_BASE + 0x300000:
            for delta in range(0, 0x100, 0x10):
                base = (aoff - delta) & ~0x3
                if base >= 0:
                    candidate_bases.add(base)

    candidate_bases.add(PACK_BASE)

    scored = []
    for base in sorted(candidate_bases):
        score = quick_pack_score(data, base)
        if score >= 3:
            scored.append((score, base))

    scored.sort(reverse=True)

    roots = []
    for score, base in scored:
        if any(abs(base - existing) < 0x400 for existing in roots):
            continue
        roots.append(base)

    return roots[:10]


def should_recurse_into_pack(data: bytes, base: int, depth: int) -> bool:
    score = quick_pack_score(data, base)
    if score < 3:
        return False
    if depth >= MAX_PACK_RECURSION_DEPTH:
        return False
    if base <= PACK_BASE + 0x1000:
        return score >= GIANT_PACK_RECURSE_SCORE
    return True


def walk_tolerant_packs(
    data: bytes,
    pack_offset: int,
    path_parts: List[str],
    out_audio: List[AudioCandidate],
    seen_packs: Set[int],
    state: WalkState,
    depth: int = 0,
):
    if pack_offset in seen_packs:
        return
    if state.packs_visited >= MAX_TOTAL_PACKS_VISITED:
        return
    if state.audio_found >= MAX_TOTAL_PACK_AUDIO_FOUND:
        return

    seen_packs.add(pack_offset)
    state.packs_visited += 1

    entries = parse_tolerant_pack(data, pack_offset)
    if entries is None:
        return

    child_pack_count = 0

    for ent in entries:
        if state.audio_found >= MAX_TOTAL_PACK_AUDIO_FOUND:
            return

        child_name = f"pack_{pack_offset:08X}_entry_{ent.index:03d}"

        u32_blob = best_u32_audio_blob(data, ent.abs_off)
        if u32_blob is not None:
            blob, declared, variant = u32_blob
            out_audio.append(AudioCandidate(
                absolute_offset=ent.abs_off,
                blob=blob,
                source="pack_traversal",
                pack_offset=pack_offset,
                entry_index=ent.index,
                stored_size=ent.stored_size,
                payload_size=ent.payload_size,
                declared_len=declared,
                path_hint="/".join(path_parts + [child_name]),
                size_variant=variant,
            ))
            state.audio_found += 1
            continue

        u16_blob = best_u16_audio_blob(data, ent.abs_off)
        if u16_blob is not None:
            blob, declared, variant = u16_blob
            out_audio.append(AudioCandidate(
                absolute_offset=ent.abs_off,
                blob=blob,
                source="pack_traversal_legacy",
                pack_offset=pack_offset,
                entry_index=ent.index,
                stored_size=ent.stored_size,
                payload_size=ent.payload_size,
                declared_len=declared,
                path_hint="/".join(path_parts + [child_name]),
                size_variant=variant,
            ))
            state.audio_found += 1
            continue

        if child_pack_count >= MAX_CHILD_PACKS_PER_PARENT:
            continue

        if should_recurse_into_pack(data, ent.abs_off, depth):
            child_pack_count += 1
            walk_tolerant_packs(
                data=data,
                pack_offset=ent.abs_off,
                path_parts=path_parts + [child_name],
                out_audio=out_audio,
                seen_packs=seen_packs,
                state=state,
                depth=depth + 1,
            )


def choose_better_candidate(a: AudioCandidate, b: AudioCandidate) -> AudioCandidate:
    source_rank = {
        "pack_traversal": 4,
        "pack_traversal_legacy": 3,
        "raw_u32_scan": 2,
        "raw_u16_scan": 1,
    }
    ra = source_rank.get(a.source, 0)
    rb = source_rank.get(b.source, 0)

    if ra != rb:
        return a if ra > rb else b
    if len(a.blob) != len(b.blob):
        return a if len(a.blob) > len(b.blob) else b
    return a


def merge_candidates(cands: List[AudioCandidate]) -> List[AudioCandidate]:
    by_off: Dict[int, AudioCandidate] = {}
    for c in cands:
        old = by_off.get(c.absolute_offset)
        if old is None:
            by_off[c.absolute_offset] = c
        else:
            by_off[c.absolute_offset] = choose_better_candidate(old, c)
    return sorted(by_off.values(), key=lambda x: x.absolute_offset)


def discover_audio_candidates(bin_path: str) -> Tuple[bytearray, List[AudioCandidate], Dict[str, int]]:
    with open(bin_path, "rb") as f:
        data = bytearray(f.read())

    print(f"[*] Loaded {bin_path} ({len(data)} bytes)")
    stats = {
        "root_packs": 0,
        "pack_audio": 0,
        "raw_u32": 0,
        "raw_u16": 0,
        "merged": 0,
    }

    print("[*] Running raw u32 scan...")
    raw_u32 = scan_u32_audio(data)
    stats["raw_u32"] = len(raw_u32)
    print(f"[*] Audio leaves found by raw u32 scan: {len(raw_u32)}")

    print("[*] Running raw u16 scan...")
    raw_u16 = scan_u16_audio(data)
    stats["raw_u16"] = len(raw_u16)
    print(f"[*] Audio leaves found by raw u16 scan: {len(raw_u16)}")

    print("[*] Discovering candidate root packs...")
    roots = discover_root_packs_from_audio(data, raw_u32)
    stats["root_packs"] = len(roots)
    print(f"[*] Candidate root packs found near 0x{PACK_BASE:06X}: {len(roots)}")

    pack_audio = []
    seen_packs = set()
    state = WalkState()

    for i, root in enumerate(roots):
        print(f"[*] Traversing root {i + 1}/{len(roots)} at 0x{root:08X}")
        if root == PACK_BASE and quick_pack_score(data, root) < GIANT_PACK_RECURSE_SCORE:
            print(f"[*] Skipping deep traversal of weak giant root 0x{root:08X}")
            continue

        walk_tolerant_packs(
            data=data,
            pack_offset=root,
            path_parts=[f"root_{root:08X}"],
            out_audio=pack_audio,
            seen_packs=seen_packs,
            state=state,
            depth=0,
        )

    stats["pack_audio"] = len(pack_audio)
    print(f"[*] Audio leaves found by pack traversal: {len(pack_audio)}")

    merged = merge_candidates(pack_audio + raw_u32 + raw_u16)
    stats["merged"] = len(merged)
    print(f"[*] Total unique audio leaves after merge: {len(merged)}")

    return data, merged, stats


def parse_index_string(s: str) -> Optional[int]:
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


def load_replace_map(csv_path: str) -> Tuple[Dict[str, str], Dict[int, str]]:
    name_map: Dict[str, str] = {}
    index_map: Dict[int, str] = {}

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            orig = row.get("original", "").strip()
            new = row.get("new", "").strip()
            if not orig or not new:
                continue

            idx = None
            if orig.lower().startswith("chunk_") and orig.lower().endswith(".a18.wav"):
                m = re.match(r"^chunk_(\d+)\.a18\.wav$", orig, re.IGNORECASE)
                if m:
                    idx = int(m.group(1))
                else:
                    name_map[orig] = new
            else:
                idx = parse_index_string(orig)

            if idx is not None:
                index_map[idx] = new
            else:
                name_map[orig] = new

    return name_map, index_map


def convert_wav_to_mono_16bit_16000(
    in_path: str,
    out_path: str,
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

    if sr != 16000:
        duration = len(pcm) / sr
        new_length = int(duration * 16000)
        if new_length <= 0:
            new_length = 1
        pcm = np.interp(
            np.linspace(0, len(pcm), new_length, endpoint=False),
            np.arange(len(pcm)),
            pcm
        ).astype(np.int16)

    fpcm = pcm.astype(np.float32)

    rms = float(np.sqrt(np.mean(fpcm ** 2))) if len(fpcm) else 0.0
    if rms > 0:
        target_rms = (10.0 ** (target_rms_db / 20.0)) * 32767.0
        gain = target_rms / rms
    else:
        gain = 1.0

    fpcm *= gain

    peak = float(np.max(np.abs(fpcm))) if len(fpcm) else 0.0
    if peak > 0:
        peak_limit = limit_ceiling * 32767.0
        if peak > peak_limit:
            fpcm *= (peak_limit / peak)

    fpcm = np.clip(fpcm, -32768, 32767)
    pcm_out = fpcm.astype(np.int16)

    with wave.open(out_path, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(pcm_out.tobytes())

    return out_path


def load_a1800_enc(dll_path: str):
    dll = ctypes.WinDLL(os.path.abspath(dll_path))
    dll.enc.argtypes = [
        LPCSTR,
        LPCSTR,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_short),
        ctypes.c_void_p,
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
        print(f"    [enc] {wav_path} -> {out_a18_path}")

    ret = encfunc(in_c, out_c, expected_samplerate, ctypes.byref(fh), None)

    if verbose:
        print(f"    [enc] ret={ret}, fh={fh.value}")

    if ret != 0:
        raise RuntimeError(f"a1800_enc failed for {wav_path} with code {ret}")

    if not os.path.exists(out_a18_path):
        raise RuntimeError(f"a1800_enc did not create output file: {out_a18_path}")

    return out_a18_path


def extract_encoded_audio_payload(raw: bytes) -> Tuple[bytes, int, str]:
    """
    Accept encoder outputs in multiple forms and return:
        payload_bytes, declared_len, detected_format

    Supported:
      1) u16len + 00 00 80 3E + payload
      2) u32len + .. .. 80 3E + payload  (raw/u32 style)
      3) same layouts found at a nonzero offset inside the file
    """
    n = len(raw)

    # direct u16 form
    if n >= 6 and raw[2:6] == A18_MARKER:
        declared = u16le(raw, 0)
        total = 2 + 4 + declared
        if 0 < declared <= MAX_AUDIO_SIZE and total <= n:
            return raw[6:6 + declared], declared, "u16_direct"

    # direct u32 form
    if n >= 8 and raw[4] == 0x80 and raw[5] == 0x3E:
        declared = u32le(raw, 0)
        if 0 < declared <= MAX_AUDIO_SIZE:
            for delta in AUDIO_TOTAL_DELTA_CANDIDATES:
                total = declared + delta
                if total <= n and total >= 8:
                    return raw[6:total], declared, f"u32_direct+{delta}"

    # scan inside file for best embedded candidate
    best = None  # (score, payload, declared, fmt)

    for pos in range(0, max(0, n - 6)):
        # embedded u16
        if pos + 6 <= n and raw[pos + 2:pos + 6] == A18_MARKER:
            declared = u16le(raw, pos)
            total = 2 + 4 + declared
            if 0 < declared <= MAX_AUDIO_SIZE and pos + total <= n:
                payload = raw[pos + 6:pos + 6 + declared]
                score = len(payload)
                cand = (score, payload, declared, f"u16_embedded@0x{pos:X}")
                if best is None or cand[0] > best[0]:
                    best = cand

        # embedded u32
        if pos + 8 <= n and raw[pos + 4] == 0x80 and raw[pos + 5] == 0x3E:
            declared = u32le(raw, pos)
            if 0 < declared <= MAX_AUDIO_SIZE:
                for delta in AUDIO_TOTAL_DELTA_CANDIDATES:
                    total = declared + delta
                    if pos + total <= n and total >= 8:
                        payload = raw[pos + 6:pos + total]
                        score = len(payload)
                        cand = (score, payload, declared, f"u32_embedded@0x{pos:X}+{delta}")
                        if best is None or cand[0] > best[0]:
                            best = cand
                        break

    if best is not None:
        _, payload, declared, fmt = best
        return payload, declared, fmt

    raise RuntimeError("Cannot extract usable encoded audio from encoder output")


def find_dll(dll_path: Optional[str]) -> str:
    search_paths = []

    if dll_path:
        search_paths.append(dll_path)

    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        script_dir = os.getcwd()

    for name in ("a1800.dll", "a1800e.dll"):
        search_paths.append(os.path.join(script_dir, name))
        search_paths.append(os.path.join(os.getcwd(), name))

    tried = []
    for cand in search_paths:
        cand = os.path.abspath(cand)
        if cand in tried:
            continue
        tried.append(cand)
        if os.path.exists(cand):
            return cand

    msg = "[!] Could not find a1800 DLL. Tried:\n"
    msg += "\n".join(f"    {p}" for p in tried)
    raise FileNotFoundError(msg)


def build_lookup_maps(candidates: List[AudioCandidate]) -> Tuple[Dict[str, int], Dict[int, AudioCandidate]]:
    name_to_idx: Dict[str, int] = {}
    idx_to_cand: Dict[int, AudioCandidate] = {}

    for idx, cand in enumerate(candidates):
        name = f"chunk_{idx:04d}.a18.wav"
        name_to_idx[name] = idx
        idx_to_cand[idx] = cand

    return name_to_idx, idx_to_cand


def rebuild_chunk_for_slot(slot_blob: bytes, new_payload: bytes) -> bytes:
    """
    Rebuild replacement chunk to match the original slot's container style.
    Supports:
      - u16 style slots
      - u32 style slots
    Keeps total slot size unchanged.
    """
    slot_size = len(slot_blob)

    # Original slot is u16 style
    if slot_size >= 6 and slot_blob[2:6] == A18_MARKER:
        slot_payload_cap = slot_size - 6
        payload = new_payload[:slot_payload_cap]
        rebuilt = bytes([
            len(payload) & 0xFF,
            (len(payload) >> 8) & 0xFF,
        ]) + A18_MARKER + payload
        if len(rebuilt) < slot_size:
            rebuilt += bytes(slot_size - len(rebuilt))
        return rebuilt

    # Original slot is u32 style
    ok, _decl = is_probable_audio_u32_at(slot_blob, 0)
    if ok:
        payload_cap = max(0, slot_size - 6)
        payload = new_payload[:payload_cap]

        # Preserve original slot total size exactly.
        # Stored format is: u32 declared + bytes [4]=80 [5]=3E + payload + possible tail padding
        # For compatibility with the exporter/discovery logic, set declared so that
        # declared + 6 = actual used content length.
        declared = len(payload)
        rebuilt = (
            declared.to_bytes(4, "little") +
            bytes([0x80, 0x3E]) +
            payload
        )
        if len(rebuilt) < slot_size:
            rebuilt += bytes(slot_size - len(rebuilt))
        return rebuilt[:slot_size]

    # Fallback: treat like u16 style if unknown
    slot_payload_cap = max(0, slot_size - 6)
    payload = new_payload[:slot_payload_cap]
    rebuilt = bytes([
        len(payload) & 0xFF,
        (len(payload) >> 8) & 0xFF,
    ]) + A18_MARKER + payload
    if len(rebuilt) < slot_size:
        rebuilt += bytes(slot_size - len(rebuilt))
    return rebuilt[:slot_size]


def reinject_wavs(
    original_bin: str,
    output_bin: str,
    csv_path: str,
    wav_dir: str,
    dll_path: Optional[str],
    dry_run: bool = False,
    verbose: bool = False,
):
    print("\n=== STEP 1: DISCOVER AUDIO CANDIDATES ===")
    data, candidates, stats = discover_audio_candidates(original_bin)
    name_to_idx, idx_to_cand = build_lookup_maps(candidates)

    print("\n=== STEP 2: LOAD MAP ===")
    name_map, index_map = load_replace_map(csv_path)
    print(f"[*] Loaded {len(name_map)} name mappings, {len(index_map)} index mappings")

    chosen_dll = find_dll(dll_path)
    encfunc = load_a1800_enc(chosen_dll)
    print(f"[*] Loaded encoder DLL: {chosen_dll}")

    tmp_dir = tempfile.mkdtemp(prefix="d3_import_tmp_")

    replaced = 0
    trimmed = 0
    padded = 0
    skipped_no_map = 0
    skipped_missing = 0
    skipped_encode = 0
    skipped_bad_idx = 0

    print("\n=== STEP 3: ENCODE + SAFE REINJECT ===")

    final_replace: Dict[int, str] = {}

    for orig_name, wav_name in name_map.items():
        idx = name_to_idx.get(orig_name)
        if idx is None:
            print(f"[!] Mapping name not found in discovered candidates: {orig_name}")
            skipped_bad_idx += 1
            continue
        final_replace[idx] = wav_name

    for idx, wav_name in index_map.items():
        if idx not in idx_to_cand:
            print(f"[!] Mapping index not found in discovered candidates: {idx}")
            skipped_bad_idx += 1
            continue
        final_replace[idx] = wav_name

    total_candidates = len(candidates)

    for idx in range(total_candidates):
        if idx not in final_replace:
            skipped_no_map += 1
            continue

        cand = idx_to_cand[idx]
        wav_name = final_replace[idx]
        wav_path = os.path.join(wav_dir, wav_name)

        if not os.path.exists(wav_path):
            skipped_missing += 1
            print(f"[!] Missing WAV for chunk_{idx:04d}: {wav_path}")
            continue

        slot_blob = cand.blob
        slot_size = len(slot_blob)
        start = cand.absolute_offset
        end = start + slot_size

        print(f"\n[+] chunk_{idx:04d}.a18.wav <- {wav_name}")
        print(f"    offset=0x{start:08X}, slot_size={slot_size}, source={cand.source}")

        norm_wav = os.path.join(tmp_dir, f"chunk_{idx:04d}_norm.wav")
        tmp_a18 = os.path.join(tmp_dir, f"chunk_{idx:04d}.a18")

        try:
            convert_wav_to_mono_16bit_16000(wav_path, norm_wav)
            encode_wav_to_a18_via_dll(encfunc, norm_wav, tmp_a18, verbose=verbose)
            raw = open(tmp_a18, "rb").read()
            new_payload, declared_len, enc_fmt = extract_encoded_audio_payload(raw)

            original_cap = max(0, slot_size - 6)
            if len(new_payload) > original_cap:
                print(f"    [!] Payload too large ({len(new_payload)} > {original_cap}), trimming")
                trimmed += 1
                new_payload = new_payload[:original_cap]

            rebuilt = rebuild_chunk_for_slot(slot_blob, new_payload)

            if len(rebuilt) < slot_size:
                rebuilt += bytes(slot_size - len(rebuilt))
                padded += 1

            if len(rebuilt) != slot_size:
                raise RuntimeError(
                    f"rebuilt slot size mismatch ({len(rebuilt)} != {slot_size})"
                )

            if dry_run:
                print(f"    [✓] DRY RUN — would write safely ({enc_fmt}, payload={len(new_payload)})")
            else:
                data[start:end] = rebuilt
                print(f"    [✓] Written safely ({enc_fmt}, payload={len(new_payload)})")

            replaced += 1

        except Exception as e:
            skipped_encode += 1
            print(f"    [!] Encode/extract failed: {e}")
            continue

    print("\n=== STEP 4: WRITE OUTPUT ===")
    if dry_run:
        print("[*] Dry run enabled; not writing output BIN.")
    else:
        with open(output_bin, "wb") as f:
            f.write(data)
        print(f"[✓] Output written -> {output_bin}")

    try:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass

    print("\nSummary:")
    print(f"  candidate root packs found = {stats['root_packs']}")
    print(f"  audio by pack traversal    = {stats['pack_audio']}")
    print(f"  audio by raw u32 scan      = {stats['raw_u32']}")
    print(f"  audio by raw u16 scan      = {stats['raw_u16']}")
    print(f"  total unique audio leaves  = {stats['merged']}")
    print(f"  replaced safely            = {replaced}")
    print(f"  trimmed                    = {trimmed}")
    print(f"  padded                     = {padded}")
    print(f"  skipped (no map)           = {skipped_no_map}")
    print(f"  skipped (missing wav)      = {skipped_missing}")
    print(f"  skipped (bad idx/name)     = {skipped_bad_idx}")
    print(f"  skipped (encode/extract)   = {skipped_encode}")


def main():
    p = argparse.ArgumentParser(
        description="Safely encode WAVs and reinject them into D-3 BIN using robust candidate discovery."
    )
    p.add_argument("original_bin", help="Input BIN to patch")
    p.add_argument("output_bin", help="Output BIN")
    p.add_argument("csv_map", help="CSV mapping file: original,new")
    p.add_argument("wav_dir", help="Directory containing replacement WAV files")
    p.add_argument(
        "--dll",
        dest="dll_path",
        default=None,
        help="Optional path to a1800.dll or a1800e.dll"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover and encode, but do not write the output BIN"
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose encoder logs"
    )

    args = p.parse_args()

    reinject_wavs(
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