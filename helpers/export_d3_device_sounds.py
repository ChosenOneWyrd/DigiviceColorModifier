#!/usr/bin/env python3
"""
Export D-3 25th Color device sounds (boss alerts, beeps, UI tones, etc.)
from a firmware BIN.

Key properties
--------------
* Parses the actual nested Generalplus resource-pack tree at 0x140000.
* Exports only leaf-owned SPF2ALP/DRM device sounds by default.
* Preserves each compressed SPF2ALP object exactly.
* Converts ADPCM36 to mono PCM WAV with a portable decoder adapted from
  MAME's preliminary Generalplus SPG2xx ADPCM36 implementation.
* Optionally uses A3600.dll on Windows as a vendor-reference decoder.
* Writes a detailed CSV manifest and an optional whole-BIN SPF2ALP audit.

This program was validated against the uploaded 32 MiB D-3 firmware:
SHA-256 bd25229a3ee01c87e635a0e84e06daa574881271047e13436acc0616e594934e

Typical usage
-------------
Portable, no external dependencies:
    python export_d3_device_sounds.py D-3.bin exported_device_sounds

Also audit every SPF2ALP signature in the BIN:
    python export_d3_device_sounds.py D-3.bin exported_device_sounds --audit-all-spf2

Use a vendor A3600.dll on Windows, falling back to the portable decoder if
the DLL fails or produces an invalid/silent WAV:
    python export_d3_device_sounds.py D-3.bin exported_device_sounds ^
        --decoder auto --dll a3600.dll

Raw extraction only:
    python export_d3_device_sounds.py D-3.bin exported_device_sounds --decoder raw

Python 3.9+; standard library only.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import math
import os
import shutil
import struct
import sys
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence


DEFAULT_PACK_BASE = 0x00140000
PACK_MAGIC_LOW16 = 0x3232
SPF_MAGIC = b"SPF2ALP"
SPF_HEADER_SIZE = 0x28
MAX_PACK_CHILDREN = 4096
MIN_VALID_RATE = 1000
MAX_VALID_RATE = 192000


@dataclass(frozen=True)
class PackChild:
    parent_offset: int
    entry_index: int
    relative_offset: int
    absolute_offset: int
    size: int
    is_metadata_region: bool = False


@dataclass(frozen=True)
class SpfInfo:
    sample_rate: int
    audio_length_words: int
    unknown1: int
    unknown2: int
    id1: int
    id2: int

    @property
    def payload_size(self) -> int:
        return self.audio_length_words * 2

    @property
    def declared_total_size(self) -> int:
        return SPF_HEADER_SIZE + self.payload_size


@dataclass
class DecodeResult:
    samples: list[int]
    decoder: str
    terminator_found: bool
    block_count: int
    words_consumed: int
    note: str = ""


def u32le(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ValueError(f"u32 read outside buffer at 0x{offset:X}")
    return struct.unpack_from("<I", data, offset)[0]


def parse_int(text: str) -> int:
    return int(text, 0)


def hex8(value: int) -> str:
    return f"0x{value:08X}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_pack_at(data: bytes, absolute_offset: int) -> bool:
    if absolute_offset < 0 or absolute_offset + 0x10 > len(data):
        return False
    word0 = u32le(data, absolute_offset)
    count = (word0 >> 16) & 0xFFFF
    return (
        (word0 & 0xFFFF) == PACK_MAGIC_LOW16
        and 0 < count <= MAX_PACK_CHILDREN
    )


def parse_pack_children(data: bytes, pack_offset: int) -> list[PackChild]:
    if not is_pack_at(data, pack_offset):
        raise ValueError(f"No valid resource pack at {hex8(pack_offset)}")

    count = (u32le(data, pack_offset) >> 16) & 0xFFFF
    children: list[PackChild] = []

    # In this firmware's pack format, child 0 describes the internal
    # table/metadata region. It is not a playable resource.
    metadata_size = u32le(data, pack_offset + 0x08)
    metadata_relative = u32le(data, pack_offset + 0x0C)
    metadata_absolute = pack_offset + metadata_relative
    if (
        metadata_relative > 0
        and metadata_size > 0
        and metadata_absolute + metadata_size <= len(data)
    ):
        children.append(
            PackChild(
                parent_offset=pack_offset,
                entry_index=0,
                relative_offset=metadata_relative,
                absolute_offset=metadata_absolute,
                size=metadata_size,
                is_metadata_region=True,
            )
        )

    table_offset = pack_offset + 0x10
    for entry_index in range(1, count):
        record_offset = table_offset + (entry_index - 1) * 0x10
        if record_offset + 0x10 > len(data):
            raise ValueError(
                f"Truncated child table in pack {hex8(pack_offset)} "
                f"at entry {entry_index}"
            )

        relative_offset = u32le(data, record_offset + 0x08)
        size = u32le(data, record_offset + 0x0C)
        if relative_offset == 0 or size == 0:
            continue

        absolute_offset = pack_offset + relative_offset
        if absolute_offset < 0 or absolute_offset + size > len(data):
            raise ValueError(
                f"Pack {hex8(pack_offset)} entry {entry_index} points "
                f"outside the BIN: off={hex8(absolute_offset)}, size={size}"
            )

        children.append(
            PackChild(
                parent_offset=pack_offset,
                entry_index=entry_index,
                relative_offset=relative_offset,
                absolute_offset=absolute_offset,
                size=size,
            )
        )

    return children


def walk_pack_tree(
    data: bytes,
    root_offset: int,
) -> tuple[list[PackChild], list[int]]:
    stack = [root_offset]
    visited: set[int] = set()
    packs: list[int] = []
    leaves: list[PackChild] = []

    while stack:
        pack_offset = stack.pop()
        if pack_offset in visited:
            continue
        visited.add(pack_offset)

        if not is_pack_at(data, pack_offset):
            raise ValueError(
                f"Expected nested pack at {hex8(pack_offset)}, but it is invalid"
            )

        packs.append(pack_offset)
        for child in parse_pack_children(data, pack_offset):
            if child.is_metadata_region:
                continue
            if is_pack_at(data, child.absolute_offset):
                stack.append(child.absolute_offset)
            else:
                leaves.append(child)

    return leaves, packs


def is_spf2alp(blob: bytes) -> bool:
    return len(blob) >= SPF_HEADER_SIZE and blob.startswith(SPF_MAGIC)


def parse_spf2alp(blob: bytes) -> SpfInfo:
    if not is_spf2alp(blob):
        raise ValueError("Not an SPF2ALP/DRM object")

    info = SpfInfo(
        sample_rate=u32le(blob, 0x10),
        audio_length_words=u32le(blob, 0x14),
        unknown1=u32le(blob, 0x18),
        unknown2=u32le(blob, 0x1C),
        id1=u32le(blob, 0x20),
        id2=u32le(blob, 0x24),
    )

    if info.audio_length_words <= 0:
        raise ValueError("SPF2ALP audio length is zero")
    if info.declared_total_size > len(blob):
        raise ValueError(
            f"SPF2ALP declares {info.declared_total_size} bytes, "
            f"but its pack slot contains only {len(blob)}"
        )
    if not (MIN_VALID_RATE <= info.sample_rate <= MAX_VALID_RATE):
        raise ValueError(f"Implausible SPF2ALP sample rate: {info.sample_rate}")

    return info


def signed16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def signed6(value: int) -> int:
    value &= 0x3F
    return value - 0x40 if value & 0x20 else value


def decode_adpcm36_mame(payload: bytes) -> DecodeResult:
    """
    Decode Generalplus ADPCM36 using the block framing and predictor model
    currently implemented by MAME for SPG2xx audio.

    Stream framing observed in this D-3 firmware:
        one 16-bit little-endian block header
        eight 16-bit packed-data words
        repeat

    Each packed-data word contains four 4-bit codes, low nibble first.
    A data word of 0xFFFF terminates a one-shot stream.

    This is intentionally described as a preliminary open-source decoder.
    The raw SPF2ALP file is always preserved as the authoritative source.
    """
    if len(payload) < 2:
        raise ValueError("ADPCM36 payload is empty")
    if len(payload) & 1:
        payload = payload[:-1]

    words = struct.unpack("<" + "H" * (len(payload) // 2), payload)
    samples: list[int] = []
    previous0 = 0
    previous1 = 0
    word_index = 0
    block_count = 0
    terminator_found = False

    while word_index < len(words):
        header = words[word_index]
        word_index += 1
        block_count += 1

        shift = header & 0x000F
        filter0 = signed6((header & 0x03F0) >> 4)
        filter1 = 0

        for _ in range(8):
            if word_index >= len(words):
                break

            packed = words[word_index]
            word_index += 1

            if packed == 0xFFFF:
                terminator_found = True
                return DecodeResult(
                    samples=samples,
                    decoder="mame-adpcm36-preliminary",
                    terminator_found=True,
                    block_count=block_count,
                    words_consumed=word_index,
                )

            for nibble_shift in (0, 4, 8, 12):
                code = (packed >> nibble_shift) & 0xF
                signed_code = code - 16 if code & 0x8 else code

                # Mirrors MAME's int16_t intermediate and arithmetic shift.
                current = signed_code << 12
                current = (
                    (current >> shift)
                    + (((previous0 * filter0) + (previous1 * filter1) + 32) >> 12)
                )
                current = signed16(current)

                previous1 = previous0
                previous0 = current
                samples.append(current)

    return DecodeResult(
        samples=samples,
        decoder="mame-adpcm36-preliminary",
        terminator_found=terminator_found,
        block_count=block_count,
        words_consumed=word_index,
        note="payload ended without an in-band 0xFFFF terminator",
    )


def write_pcm16_mono_wav(
    output_path: Path,
    samples: Sequence[int],
    sample_rate: int,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)

        chunk_samples = 32768
        for start in range(0, len(samples), chunk_samples):
            chunk = samples[start : start + chunk_samples]
            wav_file.writeframes(
                struct.pack("<" + "h" * len(chunk), *chunk)
            )


def wav_stats(path: Path) -> tuple[int, int, float, float, int]:
    with wave.open(str(path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        width = wav_file.getsampwidth()
        rate = wav_file.getframerate()
        frames = wav_file.getnframes()
        raw = wav_file.readframes(frames)

    if channels != 1 or width != 2:
        return rate, frames, (frames / rate if rate else 0.0), 0.0, 0

    count = len(raw) // 2
    if not count:
        return rate, frames, 0.0, 0.0, 0

    values = struct.unpack("<" + "h" * count, raw)
    square_sum = sum(value * value for value in values)
    rms = math.sqrt(square_sum / count)
    nonzero = sum(1 for value in values if value != 0)
    return rate, frames, frames / float(rate), rms, nonzero


def patch_wav_sample_rate(path: Path, sample_rate: int) -> None:
    data = bytearray(path.read_bytes())
    if len(data) < 12 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("A3600 output is not a RIFF/WAVE file")

    position = 12
    while position + 8 <= len(data):
        chunk_id = bytes(data[position : position + 4])
        chunk_size = struct.unpack_from("<I", data, position + 4)[0]
        chunk_data = position + 8

        if chunk_id == b"fmt ":
            if chunk_size < 16:
                raise ValueError("A3600 WAV has an invalid fmt chunk")
            block_align = struct.unpack_from("<H", data, chunk_data + 12)[0]
            struct.pack_into("<I", data, chunk_data + 4, sample_rate)
            struct.pack_into("<I", data, chunk_data + 8, sample_rate * block_align)
            path.write_bytes(data)
            return

        position = chunk_data + chunk_size + (chunk_size & 1)

    raise ValueError("A3600 WAV has no fmt chunk")


def load_a3600(dll_path: Path):
    if os.name != "nt":
        raise RuntimeError("A3600.dll decoding is available only on Windows")

    errors: list[str] = []
    libraries = []
    for loader_name, loader in (("CDLL/cdecl", ctypes.CDLL), ("WinDLL/stdcall", ctypes.WinDLL)):
        try:
            libraries.append((loader_name, loader(str(dll_path))))
        except OSError as exc:
            errors.append(f"{loader_name}: {exc}")

    for loader_name, library in libraries:
        try:
            decoder = library.a3600_dec
            decoder.argtypes = [
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_void_p,
            ]
            decoder.restype = ctypes.c_int

            initializer = getattr(library, "a3600_dec_init", None)
            if initializer is not None:
                initializer.argtypes = []
                initializer.restype = ctypes.c_int

            return loader_name, initializer, decoder
        except AttributeError as exc:
            errors.append(f"{loader_name}: {exc}")

    raise RuntimeError("Could not load a3600_dec: " + " | ".join(errors))


def decode_adpcm36_a3600(
    payload: bytes,
    sample_rate: int,
    output_path: Path,
    dll_path: Path,
) -> DecodeResult:
    """
    Vendor-reference route documented by community reverse engineering:
    replace the 0x28-byte DRM header with a little-endian payload-byte count,
    call a3600_dec(inFile, outFile, 0, NULL), then repair the WAV sample rate.
    """
    loader_name, initializer, decoder = load_a3600(dll_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_input_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".a36") as temp_input:
            temp_input.write(struct.pack("<I", len(payload)))
            temp_input.write(payload)
            temp_input_name = temp_input.name

        if initializer is not None:
            try:
                initializer()
            except Exception:
                pass

        result = int(
            decoder(
                os.fsencode(os.path.abspath(temp_input_name)),
                os.fsencode(os.path.abspath(output_path)),
                0,
                None,
            )
        )

        if result == 0 or not output_path.exists():
            raise RuntimeError(f"a3600_dec failed (return value {result})")

        patch_wav_sample_rate(output_path, sample_rate)
        rate, frames, duration, rms, nonzero = wav_stats(output_path)
        if rate != sample_rate or frames <= 0:
            raise RuntimeError("A3600 produced an invalid WAV")
        if nonzero == 0 or rms < 1.0:
            raise RuntimeError("A3600 produced a silent WAV")

        return DecodeResult(
            samples=[],
            decoder=f"a3600.dll ({loader_name})",
            terminator_found=True,
            block_count=0,
            words_consumed=len(payload) // 2,
            note=f"vendor DLL return={result}; rms={rms:.2f}",
        )
    finally:
        if temp_input_name:
            try:
                os.remove(temp_input_name)
            except OSError:
                pass


def find_all_magic(data: bytes, magic: bytes) -> Iterable[int]:
    position = 0
    while True:
        found = data.find(magic, position)
        if found < 0:
            return
        yield found
        position = found + 1


def read_name_map(path: Optional[Path]) -> dict[int, str]:
    if path is None:
        return {}

    mapping: dict[int, str] = {}
    with path.open(newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames:
            raise ValueError("Name-map CSV has no header")

        fields = {field.strip().lower(): field for field in reader.fieldnames}
        index_field = fields.get("index") or fields.get("device_index")
        name_field = fields.get("name") or fields.get("sound_name")
        if not index_field or not name_field:
            raise ValueError(
                "Name-map CSV must contain index,name "
                "(or device_index,sound_name) columns"
            )

        for row in reader:
            if not row.get(index_field, "").strip():
                continue
            index = int(row[index_field], 0)
            name = row.get(name_field, "").strip()
            if name:
                safe = "".join(
                    character if character.isalnum() or character in "-_ " else "_"
                    for character in name
                ).strip().replace(" ", "_")
                mapping[index] = safe
    return mapping


def write_audit(
    data: bytes,
    output_dir: Path,
    owned_offsets: set[int],
    export_unowned: bool,
) -> tuple[int, int]:
    rows: list[dict[str, object]] = []
    unowned_dir = output_dir / "unowned_spf2"

    for raw_index, offset in enumerate(find_all_magic(data, SPF_MAGIC)):
        status = "valid"
        info: Optional[SpfInfo] = None
        error = ""
        try:
            info = parse_spf2alp(data[offset:])
            if offset + info.declared_total_size > len(data):
                raise ValueError("declared object extends past end of BIN")
        except Exception as exc:
            status = "invalid"
            error = str(exc)

        owned = offset in owned_offsets
        row: dict[str, object] = {
            "raw_index": raw_index,
            "absolute_offset_hex": hex8(offset),
            "owned_by_resource_tree": int(owned),
            "status": status,
            "sample_rate": info.sample_rate if info else "",
            "audio_length_words": info.audio_length_words if info else "",
            "declared_total_size": info.declared_total_size if info else "",
            "id1": hex8(info.id1) if info else "",
            "id2": hex8(info.id2) if info else "",
            "error": error,
        }
        rows.append(row)

        if export_unowned and not owned and info is not None:
            unowned_dir.mkdir(parents=True, exist_ok=True)
            object_data = data[offset : offset + info.declared_total_size]
            (unowned_dir / f"unowned_{raw_index:03d}_off_{offset:08X}.spf2").write_bytes(
                object_data
            )

    audit_path = output_dir / "all_spf2_audit.csv"
    with audit_path.open("w", newline="", encoding="utf-8") as csv_file:
        fieldnames = [
            "raw_index",
            "absolute_offset_hex",
            "owned_by_resource_tree",
            "status",
            "sample_rate",
            "audio_length_words",
            "declared_total_size",
            "id1",
            "id2",
            "error",
        ]
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    valid = sum(1 for row in rows if row["status"] == "valid")
    unowned = sum(
        1
        for row in rows
        if row["status"] == "valid" and not row["owned_by_resource_tree"]
    )
    return valid, unowned


def export_device_sounds(args: argparse.Namespace) -> int:
    bin_path = Path(args.bin_path).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    raw_dir = output_dir / "raw_spf2"
    wav_dir = output_dir / "wav"

    if not bin_path.is_file():
        raise FileNotFoundError(f"BIN file not found: {bin_path}")

    data = bin_path.read_bytes()
    firmware_sha256 = sha256_bytes(data)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    if args.decoder != "raw":
        wav_dir.mkdir(parents=True, exist_ok=True)

    root_offset = args.pack_base
    if not is_pack_at(data, root_offset):
        raise RuntimeError(
            f"{hex8(root_offset)} is not a valid resource-pack root in this BIN"
        )

    leaves, packs = walk_pack_tree(data, root_offset)
    sounds: list[tuple[PackChild, bytes, SpfInfo]] = []
    for leaf in sorted(leaves, key=lambda item: item.absolute_offset):
        blob = data[leaf.absolute_offset : leaf.absolute_offset + leaf.size]
        if is_spf2alp(blob):
            info = parse_spf2alp(blob)
            sounds.append((leaf, blob, info))

    if not sounds:
        raise RuntimeError("No leaf-owned SPF2ALP device sounds were found")

    name_map = read_name_map(args.name_map)
    dll_path = args.dll.resolve() if args.dll else None
    rows: list[dict[str, object]] = []

    print(f"[*] BIN: {bin_path}")
    print(f"[*] Size: {len(data)} bytes")
    print(f"[*] SHA-256: {firmware_sha256}")
    print(f"[*] Resource root: {hex8(root_offset)}")
    print(f"[*] Packs visited: {len(packs)}")
    print(f"[*] Non-table leaves: {len(leaves)}")
    print(f"[*] Leaf-owned SPF2ALP sounds: {len(sounds)}")

    for index, (leaf, blob, info) in enumerate(sounds):
        mapped_name = name_map.get(index, "")
        base_name = (
            f"device_{index:03d}"
            + (f"_{mapped_name}" if mapped_name else "")
            + f"_off_{leaf.absolute_offset:08X}"
            + f"_ent_{leaf.entry_index:03d}"
        )
        raw_name = base_name + ".spf2"
        wav_name = base_name + ".wav"
        raw_path = raw_dir / raw_name
        wav_path = wav_dir / wav_name

        # Save exactly the resource slot bytes. For this tested firmware,
        # every slot size equals the 0x28 header plus 2*audio_length_words.
        raw_path.write_bytes(blob)

        exact_size_match = leaf.size == info.declared_total_size
        payload = blob[SPF_HEADER_SIZE : info.declared_total_size]

        decode_status = "raw-only"
        decoder_used = ""
        note = ""
        sample_count = 0
        duration = 0.0
        rms = 0.0
        terminator_found: object = ""
        block_count: object = ""
        words_consumed: object = ""

        if args.decoder != "raw":
            decode_result: Optional[DecodeResult] = None
            dll_error = ""

            should_try_dll = (
                args.decoder in ("auto", "a3600")
                and dll_path is not None
            )
            if args.decoder == "a3600" and dll_path is None:
                raise ValueError("--decoder a3600 requires --dll PATH")

            if should_try_dll:
                try:
                    decode_result = decode_adpcm36_a3600(
                        payload=payload,
                        sample_rate=info.sample_rate,
                        output_path=wav_path,
                        dll_path=dll_path,
                    )
                    decode_status = "ok"
                except Exception as exc:
                    dll_error = str(exc)
                    if args.decoder == "a3600" and not args.fallback_mame:
                        decode_status = "failed"
                        note = f"A3600.dll: {dll_error}"

            use_mame = (
                decode_result is None
                and (
                    args.decoder in ("mame", "auto")
                    or (args.decoder == "a3600" and args.fallback_mame)
                )
            )
            if use_mame:
                decode_result = decode_adpcm36_mame(payload)
                write_pcm16_mono_wav(
                    wav_path,
                    decode_result.samples,
                    info.sample_rate,
                )
                decode_status = "ok"
                if dll_error:
                    decode_result.note = (
                        f"A3600.dll failed ({dll_error}); "
                        f"used portable fallback. {decode_result.note}"
                    ).strip()

            if decode_result is not None and wav_path.exists():
                rate, frames, duration, rms, nonzero = wav_stats(wav_path)
                if rate != info.sample_rate or frames <= 0 or nonzero <= 0:
                    decode_status = "failed"
                    note = "Generated WAV failed validation"
                else:
                    decoder_used = decode_result.decoder
                    sample_count = frames
                    terminator_found = int(decode_result.terminator_found)
                    block_count = decode_result.block_count
                    words_consumed = decode_result.words_consumed
                    note = decode_result.note

        rows.append(
            {
                "index": index,
                "mapped_name": mapped_name,
                "pack_offset_hex": hex8(leaf.parent_offset),
                "entry_index": leaf.entry_index,
                "absolute_offset_hex": hex8(leaf.absolute_offset),
                "slot_size_bytes": leaf.size,
                "declared_total_size": info.declared_total_size,
                "exact_size_match": int(exact_size_match),
                "header_size": SPF_HEADER_SIZE,
                "payload_size_bytes": info.payload_size,
                "sample_rate": info.sample_rate,
                "audio_length_words": info.audio_length_words,
                "unknown1": hex8(info.unknown1),
                "unknown2": hex8(info.unknown2),
                "id1": hex8(info.id1),
                "id2": hex8(info.id2),
                "raw_file": f"raw_spf2/{raw_name}",
                "wav_file": f"wav/{wav_name}" if wav_path.exists() else "",
                "decode_status": decode_status,
                "decoder": decoder_used,
                "decoded_samples": sample_count,
                "duration_seconds": f"{duration:.6f}" if duration else "",
                "wav_rms": f"{rms:.3f}" if rms else "",
                "terminator_found": terminator_found,
                "adpcm_block_count": block_count,
                "adpcm_words_consumed": words_consumed,
                "note": note,
            }
        )

        print(
            f"[+] {index:03d} off={hex8(leaf.absolute_offset)} "
            f"entry={leaf.entry_index:03d} rate={info.sample_rate:5d} "
            f"size={leaf.size:6d} decode={decode_status}"
        )

    manifest_path = output_dir / "device_sound_manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    audit_valid = ""
    audit_unowned = ""
    if args.audit_all_spf2:
        owned_offsets = {leaf.absolute_offset for leaf, _, _ in sounds}
        audit_valid, audit_unowned = write_audit(
            data=data,
            output_dir=output_dir,
            owned_offsets=owned_offsets,
            export_unowned=args.export_unowned,
        )
        print(
            f"[*] Whole-BIN audit: {audit_valid} valid signatures; "
            f"{audit_unowned} not owned by the normal leaf tree"
        )

    report_path = output_dir / "export_report.txt"
    report_lines = [
        "D-3 DEVICE SOUND EXPORT REPORT",
        "==============================",
        f"Input BIN: {bin_path}",
        f"BIN size: {len(data)} bytes (0x{len(data):X})",
        f"BIN SHA-256: {firmware_sha256}",
        f"Resource-pack root: {hex8(root_offset)}",
        f"Packs visited ({len(packs)}): "
        + ", ".join(hex8(pack) for pack in sorted(packs)),
        f"Non-table resource leaves: {len(leaves)}",
        f"Leaf-owned SPF2ALP device sounds: {len(sounds)}",
        f"Decoder requested: {args.decoder}",
        f"A3600.dll: {dll_path if dll_path else 'not supplied'}",
        f"Manifest: {manifest_path.name}",
        f"Raw directory: {raw_dir.name}",
        f"WAV directory: {wav_dir.name if wav_dir.exists() else 'not created'}",
        "",
        "Format established from this firmware:",
        "  SPF2ALP/DRM header size = 0x28 bytes",
        "  payload bytes = 2 * little-endian u32 at header+0x14",
        "  total bytes = 0x28 + payload bytes",
        "",
        "Portable WAV decoding:",
        "  ADPCM36 is read as repeating 9-word blocks:",
        "  one 16-bit header plus eight 16-bit packed-nibble words.",
        "  Codes are consumed low nibble first; 0xFFFF in a data-word",
        "  position terminates a one-shot stream.",
        "",
        "Accuracy note:",
        "  Raw SPF2ALP files are exact extractions.",
        "  The portable WAV decoder follows MAME's preliminary SPG2xx",
        "  ADPCM36 implementation. Use --dll with a compatible A3600.dll",
        "  for a vendor-reference comparison on Windows.",
    ]
    if args.audit_all_spf2:
        report_lines += [
            "",
            f"Whole-BIN valid SPF2ALP signatures: {audit_valid}",
            f"Valid signatures outside normal leaf-owned bank: {audit_unowned}",
        ]
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    successful_wavs = sum(1 for row in rows if row["decode_status"] == "ok")
    print()
    print("[✓] Export complete")
    print(f"[✓] Raw SPF2ALP files: {len(rows)}")
    print(f"[✓] Validated WAV files: {successful_wavs}")
    print(f"[✓] Manifest: {manifest_path}")
    print(f"[✓] Report: {report_path}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export D-3 device sounds by parsing the real resource tree "
            "and preserving/decoding SPF2ALP ADPCM36 objects."
        )
    )
    parser.add_argument("bin_path", help="Input D-3 firmware BIN")
    parser.add_argument("output_dir", help="Output directory")
    parser.add_argument(
        "--pack-base",
        type=parse_int,
        default=DEFAULT_PACK_BASE,
        help="Resource-pack root; default: 0x140000",
    )
    parser.add_argument(
        "--decoder",
        choices=("auto", "mame", "a3600", "raw"),
        default="auto",
        help=(
            "auto: use A3600.dll when supplied, else portable decoder; "
            "mame: portable preliminary decoder; "
            "a3600: require DLL; raw: no WAV conversion"
        ),
    )
    parser.add_argument(
        "--dll",
        type=Path,
        default=None,
        help="Path to A3600.dll for optional vendor-reference decoding",
    )
    parser.add_argument(
        "--fallback-mame",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fall back to the portable decoder if A3600.dll fails",
    )
    parser.add_argument(
        "--audit-all-spf2",
        action="store_true",
        help="Scan the entire BIN and write all_spf2_audit.csv",
    )
    parser.add_argument(
        "--export-unowned",
        action="store_true",
        help=(
            "With --audit-all-spf2, also save valid SPF2ALP objects that "
            "are not leaf-owned device sounds"
        ),
    )
    parser.add_argument(
        "--name-map",
        type=Path,
        default=None,
        help="Optional CSV with index,name columns for friendly filenames",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return export_device_sounds(args)
    except KeyboardInterrupt:
        print("\n[!] Cancelled", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"[!] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
