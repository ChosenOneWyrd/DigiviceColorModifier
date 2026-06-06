#!/usr/bin/env python3
"""
Robust D-3 audio extractor with bounded pack traversal

Usage:
    python export_sounds.py D3_orig.bin exported_sounds_orig none
    python export_sounds.py D3_mod.bin  exported_sounds_mod  none
"""

import sys
import os
import csv
import wave
import ctypes
import re
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional, Set
from ctypes.wintypes import LPCSTR, UINT

PACK_BASE = 0x140000
A18_MARKER = b"\x00\x00\x80\x3E"

MAX_AUDIO_SIZE = 0x400000
MIN_AUDIO_SIZE = 8

PACK_ENTRY_SIZE = 16
HEADER_SIZE_CANDIDATES = (0, 8, 12, 16, 20, 24, 28, 32, 36, 40)

# stricter limits to prevent runaway parsing
MAX_PACK_ENTRIES = 512
MIN_GOOD_ENTRIES_FOR_PACK = 3
MAX_PACK_RECURSION_DEPTH = 4
MAX_TOTAL_PACKS_VISITED = 256
MAX_TOTAL_PACK_AUDIO_FOUND = 512
MAX_CHILD_PACKS_PER_PARENT = 64

# if a pack is too huge and only weakly convincing, don't recurse into it
GIANT_PACK_RECURSE_SCORE = 6

AUDIO_TOTAL_DELTA_CANDIDATES = (6, 4, 8, 2, 10)
AUTO_DELETE_CHUNKS = False


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


def get_wav_info(wav_path: str) -> Tuple[int, float]:
    try:
        with wave.open(wav_path, "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            if rate <= 0:
                return 0, 0.0
            return rate, frames / float(rate)
    except Exception:
        return 0, 0.0


def load_decoder():
    dll_path = os.path.join(os.path.dirname(__file__), "a1800.dll")
    try:
        dll = ctypes.WinDLL(dll_path)
    except OSError:
        print("ERROR: Could not load a1800.dll")
        print(f"Expected at: {dll_path}")
        sys.exit(1)

    decproto = ctypes.WINFUNCTYPE(
        ctypes.c_uint, LPCSTR, LPCSTR, ctypes.POINTER(UINT), UINT, UINT
    )
    decfunc = decproto(
        ("dec", dll),
        ((1, "infile"), (1, "outfile"), (2, "fp"), (1, "unk1", 16000), (1, "unk2", 0)),
    )
    return decfunc


def decode_one(decfunc, in_path: str, out_path: str) -> int:
    try:
        return int(decfunc(
            in_path.encode("ascii", errors="replace"),
            out_path.encode("ascii", errors="replace"),
        ))
    except TypeError:
        try:
            return int(decfunc(
                infile=in_path.encode("ascii", errors="replace"),
                outfile=out_path.encode("ascii", errors="replace"),
            ))
        except Exception:
            return 0
    except Exception:
        return 0


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

    # small boost for alignment
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

            # stop if too many weak-looking entries in a row
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
    tested = 0
    for base in sorted(candidate_bases):
        tested += 1
        score = quick_pack_score(data, base)
        if score >= 3:
            scored.append((score, base))

    scored.sort(reverse=True)

    roots = []
    for score, base in scored:
        # aggressively dedupe nearby bases
        if any(abs(base - existing) < 0x400 for existing in roots):
            continue
        roots.append(base)

    print(f"[*] Candidate pack bases tested: {tested}")
    return roots[:10]


def should_recurse_into_pack(data: bytes, base: int, depth: int) -> bool:
    score = quick_pack_score(data, base)
    if score < 3:
        return False

    if depth >= MAX_PACK_RECURSION_DEPTH:
        return False

    # special safety gate for the nominal root/base area
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


def extract_all_audio(bin_path: str, chunks_dir: str) -> Tuple[List[AudioCandidate], Dict[str, int]]:
    with open(bin_path, "rb") as f:
        data = f.read()

    print(f"[*] Loaded {bin_path} ({len(data)} bytes)")
    os.makedirs(chunks_dir, exist_ok=True)

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

        # extra protection for huge ambiguous nominal root
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

        print(
            f"    visited packs so far={state.packs_visited}, "
            f"pack audio so far={state.audio_found}"
        )

    stats["pack_audio"] = len(pack_audio)
    print(f"[*] Audio leaves found by pack traversal: {len(pack_audio)}")

    merged = merge_candidates(pack_audio + raw_u32 + raw_u16)
    stats["merged"] = len(merged)
    print(f"[*] Total unique audio leaves after merge: {len(merged)}")

    return merged, stats


def write_chunk_file(chunks_dir: str, idx: int, cand: AudioCandidate) -> str:
    pack_part = f"_pack_{cand.pack_offset:08X}" if cand.pack_offset >= 0 else ""
    ent_part = f"_ent_{cand.entry_index:03d}" if cand.entry_index >= 0 else ""
    src_part = f"_{cand.source}"
    fname = f"chunk_{idx:04d}_off_{cand.absolute_offset:08X}{pack_part}{ent_part}{src_part}.a18"
    with open(os.path.join(chunks_dir, fname), "wb") as f:
        f.write(cand.blob)
    return fname


def try_decode_candidate_variants(decfunc, temp_dir: str, wav_out: str, cand: AudioCandidate) -> Tuple[bool, int, float, str]:
    variants = [(cand.blob, cand.size_variant or "as_is")]

    ok, declared = is_probable_audio_u32_at(cand.blob, 0)
    if ok:
        seen_sizes = {len(cand.blob)}
        for delta in AUDIO_TOTAL_DELTA_CANDIDATES:
            total = declared + delta
            if total <= 0 or total > len(cand.blob) + 16:
                continue
            blob2 = cand.blob[:total]
            if len(blob2) in seen_sizes:
                continue
            seen_sizes.add(len(blob2))
            variants.append((blob2, f"retry_u32+{delta}"))

    for vi, (blob, tag) in enumerate(variants):
        tmp_a18 = os.path.join(temp_dir, f"decode_try_{vi:02d}.a18")
        with open(tmp_a18, "wb") as f:
            f.write(blob)

        if os.path.exists(wav_out):
            try:
                os.remove(wav_out)
            except Exception:
                pass

        ret = decode_one(decfunc, tmp_a18, wav_out)
        if ret and os.path.exists(wav_out):
            rate, dur = get_wav_info(wav_out)
            if rate > 0 and dur > 0:
                return True, rate, dur, tag

    return False, 0, 0.0, ""


def rename_wavs(out_base: str, wav_list: List[str], csv_path: str) -> Dict[str, str]:
    if csv_path.lower() == "none" or not csv_path.strip():
        print("[*] No CSV provided, skipping rename.")
        return {}

    if not os.path.exists(csv_path):
        print(f"[!] CSV file not found: {csv_path}")
        return {}

    rename_map = {}
    with open(csv_path, newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            orig = row.get("original", "").strip()
            new = row.get("new", "").strip()
            if orig and new:
                rename_map[orig] = new

    applied = {}
    for wav in wav_list:
        if wav in rename_map:
            src = os.path.join(out_base, wav)
            dst = os.path.join(out_base, rename_map[wav])
            if os.path.exists(src):
                os.replace(src, dst)
                applied[wav] = rename_map[wav]
    return applied


def write_manifest(out_base: str, rows: List[dict]):
    manifest_path = os.path.join(out_base, "audio_manifest.csv")
    fieldnames = [
        "chunk_index", "a18_file", "wav_file", "renamed_wav_file",
        "decode_ok", "sample_rate", "duration_seconds",
        "absolute_offset_hex", "pack_offset_hex", "entry_index",
        "stored_size", "payload_size", "declared_len",
        "source", "size_variant", "path_hint", "notes",
    ]

    rows = sorted(rows, key=lambda r: (-float(r["duration_seconds"]), r["chunk_index"]))

    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    longest_path = os.path.join(out_base, "longest_audio.txt")
    with open(longest_path, "w", encoding="utf-8") as f:
        for row in rows:
            if not row["decode_ok"]:
                continue
            shown = row["renamed_wav_file"] or row["wav_file"]
            f.write(
                f"{shown}, {row['duration_seconds']}s, {row['sample_rate']} Hz, "
                f"{row['absolute_offset_hex']}, {row['source']}, {row['size_variant']}\n"
            )

    print(f"[*] Wrote {manifest_path}")
    print(f"[*] Wrote {longest_path}")


def main():
    if len(sys.argv) < 4:
        print("Usage: python export_sounds.py D3.bin output_dir sound_map.csv")
        sys.exit(1)

    bin_path = sys.argv[1]
    out_base = sys.argv[2]
    csv_path = sys.argv[3]

    os.makedirs(out_base, exist_ok=True)
    chunks_dir = os.path.join(out_base, "chunks")
    os.makedirs(chunks_dir, exist_ok=True)

    print("\n=== STEP 1: DISCOVER AUDIO CANDIDATES ===")
    candidates, stats = extract_all_audio(bin_path, chunks_dir)

    print("\n=== STEP 2: WRITE A18 CHUNKS ===")
    manifest_rows = []
    wav_list = []

    for idx, cand in enumerate(candidates):
        a18_name = write_chunk_file(chunks_dir, idx, cand)
        wav_name = re.sub(r'^(chunk_\d{4}).*?(\.[^.]+)$', r'\1\2', a18_name) + ".wav"
        manifest_rows.append({
            "chunk_index": idx,
            "a18_file": a18_name,
            "wav_file": wav_name,
            "renamed_wav_file": "",
            "decode_ok": 0,
            "sample_rate": 0,
            "duration_seconds": 0.0,
            "absolute_offset_hex": f"0x{cand.absolute_offset:08X}",
            "pack_offset_hex": f"0x{cand.pack_offset:08X}" if cand.pack_offset >= 0 else "",
            "entry_index": cand.entry_index if cand.entry_index >= 0 else "",
            "stored_size": cand.stored_size if cand.stored_size else len(cand.blob),
            "payload_size": cand.payload_size if cand.payload_size else max(0, len(cand.blob) - 2),
            "declared_len": cand.declared_len,
            "source": cand.source,
            "size_variant": cand.size_variant,
            "path_hint": cand.path_hint,
            "notes": cand.notes,
        })

    print(f"[*] Wrote {len(manifest_rows)} A18 files")

    print("\n=== STEP 3: DECODE A18 TO WAV ===")
    decfunc = load_decoder()
    temp_decode_dir = os.path.join(out_base, "_decode_tmp")
    os.makedirs(temp_decode_dir, exist_ok=True)

    decoded_ok = 0
    for row in manifest_rows:
        idx = row["chunk_index"]
        cand = candidates[idx]
        wav_path = os.path.join(out_base, row["wav_file"])

        ok, rate, dur, retry_tag = try_decode_candidate_variants(
            decfunc=decfunc,
            temp_dir=temp_decode_dir,
            wav_out=wav_path,
            cand=cand,
        )

        if ok:
            row["decode_ok"] = 1
            row["sample_rate"] = rate
            row["duration_seconds"] = round(dur, 3)
            if retry_tag and retry_tag != cand.size_variant:
                row["notes"] = (row["notes"] + f" decoded_with={retry_tag}").strip()
            wav_list.append(row["wav_file"])
            decoded_ok += 1
            print(f"[+] {row['a18_file']} -> OK ({rate} Hz, {dur:.2f}s)")
        else:
            print(f"[!] {row['a18_file']} -> FAILED")

    try:
        for name in os.listdir(temp_decode_dir):
            try:
                os.remove(os.path.join(temp_decode_dir, name))
            except Exception:
                pass
        os.rmdir(temp_decode_dir)
    except Exception:
        pass

    print("\n=== STEP 4: OPTIONAL RENAME ===")
    rename_applied = rename_wavs(out_base, wav_list, csv_path)
    for row in manifest_rows:
        row["renamed_wav_file"] = rename_applied.get(row["wav_file"], "")

    # print("\n=== STEP 5: WRITE MANIFEST ===")
    # write_manifest(out_base, manifest_rows)

    print("\nSummary:")
    print(f"  candidate root packs found = {stats['root_packs']}")
    print(f"  audio by pack traversal    = {stats['pack_audio']}")
    print(f"  audio by raw u32 scan      = {stats['raw_u32']}")
    print(f"  audio by raw u16 scan      = {stats['raw_u16']}")
    print(f"  total unique audio leaves  = {stats['merged']}")
    print(f"  WAVs decoded successfully  = {decoded_ok}")

    if AUTO_DELETE_CHUNKS:
        try:
            import shutil
            shutil.rmtree(chunks_dir, ignore_errors=True)
        except Exception:
            pass

    print("\nAll done!")


if __name__ == "__main__":
    main()