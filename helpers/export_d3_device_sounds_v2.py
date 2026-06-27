#!/usr/bin/env python3
"""
D-3 25th Color sound exporter — corrected codec implementation.

Device WAV files are named device_000.wav through device_037.wav.

This program does NOT use A3600.dll.

The D-3 firmware contains multiple codecs inside SPF2ALP containers:

1. The 38 resource-tree device sounds use the GeneralPlus/SunPlus SPG
   ordinary IMA ADPCM mode:
       id1 = 0x007F3C55
       id2 = 0x64407F00

   They are not the ADPCM36/A3600 variant documented for:
       id1 = 0x007F3CD5
       id2 = 0x64407F00

2. Seven additional SPF2ALP objects before the resource tree contain
   unsigned/offset-binary 16-bit PCM:
       id2 = 0x64400302

The IMA decoder follows the state update used by MAME's SPG2xx audio device:
- initial predictor = 0
- initial step index = 0
- 16-bit little-endian source words
- nibble order: bits 0..3, 4..7, 8..11, 12..15
- a source word of 0xFFFF terminates a one-shot stream
- output is signed 16-bit PCM

Python 3.9+; standard library only.

Usage:
    python export_d3_sounds_corrected.py D-3.bin exported_sounds

Only the 38 normal device sounds:
    python export_d3_sounds_corrected.py D-3.bin exported_sounds --device-only

Suppress exact compressed SPF2 copies:
    python export_d3_sounds_corrected.py D-3.bin exported_sounds --no-raw
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import struct
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


EXPECTED_TESTED_SHA256 = (
    "bd25229a3ee01c87e635a0e84e06daa574881271047e13436acc0616e594934e"
)

PACK_ROOT = 0x00140000
PACK_MAGIC_LOW16 = 0x3232

SPF_MAGIC = b"SPF2ALP"
SPF_HEADER_SIZE = 0x28

D3_IMA_ID1 = 0x007F3C55
D3_IMA_ID2 = 0x64407F00

D3_PCM16_ID2 = 0x64400302


@dataclass(frozen=True)
class PackLeaf:
    parent_offset: int
    entry_index: int
    absolute_offset: int
    size: int


@dataclass(frozen=True)
class SpfInfo:
    offset: int
    sample_rate: int
    length_words: int
    unknown1: int
    unknown2: int
    id1: int
    id2: int

    @property
    def payload_size(self) -> int:
        return self.length_words * 2

    @property
    def total_size(self) -> int:
        return SPF_HEADER_SIZE + self.payload_size


@dataclass(frozen=True)
class ImaDecodeResult:
    samples: list[int]
    source_words_consumed: int
    terminator_found: bool
    trailing_words: int


def read_u32le(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ValueError(f"32-bit read outside file at 0x{offset:X}")
    return struct.unpack_from("<I", data, offset)[0]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_pack_at(data: bytes, offset: int) -> bool:
    if offset < 0 or offset + 0x10 > len(data):
        return False

    word0 = read_u32le(data, offset)
    child_count = word0 >> 16
    return (
        (word0 & 0xFFFF) == PACK_MAGIC_LOW16
        and 0 < child_count <= 4096
    )


def read_pack_children(data: bytes, pack_offset: int) -> list[PackLeaf]:
    if not is_pack_at(data, pack_offset):
        raise ValueError(f"No valid resource pack at 0x{pack_offset:08X}")

    child_count = read_u32le(data, pack_offset) >> 16
    table_offset = pack_offset + 0x10
    children: list[PackLeaf] = []

    # Entry zero describes the pack's own metadata/table region. Actual
    # child entries begin at one.
    for entry_index in range(1, child_count):
        record_offset = table_offset + (entry_index - 1) * 0x10
        if record_offset + 0x10 > len(data):
            raise ValueError(
                f"Truncated table in pack 0x{pack_offset:08X}"
            )

        relative_offset = read_u32le(data, record_offset + 0x08)
        size = read_u32le(data, record_offset + 0x0C)
        if relative_offset == 0 or size == 0:
            continue

        absolute_offset = pack_offset + relative_offset
        if absolute_offset + size > len(data):
            raise ValueError(
                f"Pack 0x{pack_offset:08X}, entry {entry_index}, "
                f"points outside the BIN"
            )

        children.append(
            PackLeaf(
                parent_offset=pack_offset,
                entry_index=entry_index,
                absolute_offset=absolute_offset,
                size=size,
            )
        )

    return children


def walk_resource_tree(
    data: bytes,
    root_offset: int,
) -> tuple[list[int], list[PackLeaf]]:
    stack = [root_offset]
    visited: set[int] = set()
    packs: list[int] = []
    leaves: list[PackLeaf] = []

    while stack:
        pack_offset = stack.pop()
        if pack_offset in visited:
            continue
        visited.add(pack_offset)

        if not is_pack_at(data, pack_offset):
            raise ValueError(
                f"Expected a resource pack at 0x{pack_offset:08X}"
            )

        packs.append(pack_offset)
        for child in read_pack_children(data, pack_offset):
            if is_pack_at(data, child.absolute_offset):
                stack.append(child.absolute_offset)
            else:
                leaves.append(child)

    return sorted(packs), sorted(leaves, key=lambda leaf: leaf.absolute_offset)


def find_all(data: bytes, needle: bytes) -> Iterable[int]:
    start = 0
    while True:
        offset = data.find(needle, start)
        if offset < 0:
            return
        yield offset
        start = offset + 1


def parse_spf_at(data: bytes, offset: int) -> SpfInfo:
    if offset < 0 or offset + SPF_HEADER_SIZE > len(data):
        raise ValueError("Truncated SPF2ALP header")
    if data[offset : offset + len(SPF_MAGIC)] != SPF_MAGIC:
        raise ValueError(f"No SPF2ALP magic at 0x{offset:08X}")

    info = SpfInfo(
        offset=offset,
        sample_rate=read_u32le(data, offset + 0x10),
        length_words=read_u32le(data, offset + 0x14),
        unknown1=read_u32le(data, offset + 0x18),
        unknown2=read_u32le(data, offset + 0x1C),
        id1=read_u32le(data, offset + 0x20),
        id2=read_u32le(data, offset + 0x24),
    )

    if not 1000 <= info.sample_rate <= 192000:
        raise ValueError(
            f"Implausible sample rate {info.sample_rate} "
            f"at 0x{offset:08X}"
        )
    if info.length_words <= 0:
        raise ValueError(f"Zero-length SPF2ALP at 0x{offset:08X}")
    if offset + info.total_size > len(data):
        raise ValueError(
            f"SPF2ALP at 0x{offset:08X} extends past end of file"
        )

    return info


def build_ima_step_table() -> list[int]:
    """
    Reproduce the 89-entry table generated by MAME's ima_adpcm_state.

    It is equivalent to the standard IMA step table, including the two
    early-value corrections used by MAME.
    """
    result: list[int] = []
    for exponent in range(-8, 81):
        step_value = min(
            math.floor(16.0 * math.pow(11.0 / 10.0, exponent)),
            32767,
        )
        if exponent in (-5, -4):
            step_value += 1
        result.append(int(step_value))
    return result


IMA_STEP_TABLE = build_ima_step_table()
IMA_INDEX_SHIFT = (-1, -1, -1, -1, 2, 4, 6, 8)


def decode_spg_ima(payload: bytes) -> ImaDecodeResult:
    """
    Decode the ordinary SPG IMA ADPCM stream used by the D-3 device sounds.

    The hardware reads one little-endian 16-bit word at a time and consumes
    its low nibble first. 0xFFFF is checked as a whole-word terminator before
    any of its nibbles are decoded.
    """
    if len(payload) & 1:
        raise ValueError("IMA payload has an odd byte length")

    predictor = 0
    step_index = 0
    samples: list[int] = []
    total_words = len(payload) // 2
    consumed_words = 0
    terminator_found = False
    trailing_words = 0

    for word_index in range(total_words):
        word = struct.unpack_from("<H", payload, word_index * 2)[0]

        if word == 0xFFFF:
            terminator_found = True
            trailing_words = total_words - word_index - 1
            break

        consumed_words += 1
        for shift in (0, 4, 8, 12):
            nibble = (word >> shift) & 0x0F
            step = IMA_STEP_TABLE[step_index]

            difference = step // 8
            if nibble & 0x04:
                difference += step
            if nibble & 0x02:
                difference += step // 2
            if nibble & 0x01:
                difference += step // 4

            if nibble & 0x08:
                predictor -= difference
            else:
                predictor += difference

            if predictor > 32767:
                predictor = 32767
            elif predictor < -32768:
                predictor = -32768

            step_index += IMA_INDEX_SHIFT[nibble & 0x07]
            if step_index > 88:
                step_index = 88
            elif step_index < 0:
                step_index = 0

            samples.append(predictor)

    return ImaDecodeResult(
        samples=samples,
        source_words_consumed=consumed_words,
        terminator_found=terminator_found,
        trailing_words=trailing_words,
    )


def decode_offset_binary_pcm16(payload: bytes) -> list[int]:
    """
    Convert unsigned/offset-binary 16-bit PCM to signed WAV PCM.

    The stored midpoint is 0x8000. XOR with 0x8000 maps it to signed
    two's-complement PCM without altering amplitude.
    """
    if len(payload) & 1:
        raise ValueError("PCM16 payload has an odd byte length")

    output: list[int] = []
    for offset in range(0, len(payload), 2):
        stored = struct.unpack_from("<H", payload, offset)[0]
        signed_bits = stored ^ 0x8000
        if signed_bits & 0x8000:
            signed_bits -= 0x10000
        output.append(signed_bits)
    return output


def write_wav(
    path: Path,
    samples: Sequence[int],
    sample_rate: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)

        chunk_size = 32768
        for start in range(0, len(samples), chunk_size):
            chunk = samples[start : start + chunk_size]
            output.writeframes(
                struct.pack("<" + ("h" * len(chunk)), *chunk)
            )


def sample_metrics(samples: Sequence[int]) -> tuple[float, int, int]:
    if not samples:
        return 0.0, 0, 0

    square_sum = 0
    clipping = 0
    nonzero = 0
    for sample in samples:
        square_sum += sample * sample
        if sample in (-32768, 32767):
            clipping += 1
        if sample != 0:
            nonzero += 1

    rms = math.sqrt(square_sum / len(samples))
    return rms, clipping, nonzero


def safe_write_raw(
    output_path: Path,
    data: bytes,
    info: SpfInfo,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(
        data[info.offset : info.offset + info.total_size]
    )


def export(bin_path: Path, output_dir: Path, device_only: bool, save_raw: bool) -> int:
    data = bin_path.read_bytes()
    firmware_hash = sha256(data)

    packs, leaves = walk_resource_tree(data, PACK_ROOT)
    leaf_by_offset = {leaf.absolute_offset: leaf for leaf in leaves}

    # The normal device bank consists of SPF2ALP leaf resources with the
    # D-3 IMA identifiers.
    device_records: list[tuple[PackLeaf, SpfInfo]] = []
    for leaf in leaves:
        if data[leaf.absolute_offset : leaf.absolute_offset + 7] != SPF_MAGIC:
            continue

        info = parse_spf_at(data, leaf.absolute_offset)
        if leaf.size != info.total_size:
            raise ValueError(
                f"Resource-tree SPF2ALP at 0x{info.offset:08X} has "
                f"slot size {leaf.size}, but declares {info.total_size}"
            )
        if info.id1 == D3_IMA_ID1 and info.id2 == D3_IMA_ID2:
            device_records.append((leaf, info))

    if len(device_records) != 38:
        raise ValueError(
            f"Expected 38 normal D-3 device sounds, found "
            f"{len(device_records)}. Refusing to guess."
        )

    all_spf: list[SpfInfo] = []
    for offset in find_all(data, SPF_MAGIC):
        try:
            all_spf.append(parse_spf_at(data, offset))
        except ValueError:
            continue

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows: list[dict[str, object]] = []

    device_hash_to_index: dict[str, int] = {}

    for device_index, (leaf, info) in enumerate(device_records):
        object_bytes = data[info.offset : info.offset + info.total_size]
        object_hash = sha256(object_bytes)
        device_hash_to_index[object_hash] = device_index

        payload = data[
            info.offset + SPF_HEADER_SIZE :
            info.offset + info.total_size
        ]
        decoded = decode_spg_ima(payload)

        if not decoded.terminator_found:
            raise ValueError(
                f"Device sound {device_index} at 0x{info.offset:08X} "
                f"has no 0xFFFF terminator"
            )

        # Keep device filenames stable and easy to edit/import.
        # Offsets and pack entries remain available in sound_manifest.csv.
        base_name = f"device_{device_index:03d}"

        wav_path = (
            output_dir
            / "device_sounds"
            / "wav"
            / f"{base_name}.wav"
        )
        raw_path = (
            output_dir
            / "device_sounds"
            / "raw_spf2"
            / f"{base_name}.spf2"
        )

        write_wav(wav_path, decoded.samples, info.sample_rate)
        if save_raw:
            safe_write_raw(raw_path, data, info)

        rms, clipping, nonzero = sample_metrics(decoded.samples)
        manifest_rows.append({
            "category": "device_ima",
            "category_index": device_index,
            "absolute_offset_hex": f"0x{info.offset:08X}",
            "resource_pack_hex": f"0x{leaf.parent_offset:08X}",
            "resource_entry": leaf.entry_index,
            "sample_rate": info.sample_rate,
            "source_length_words": info.length_words,
            "decoded_samples": len(decoded.samples),
            "duration_seconds": f"{len(decoded.samples) / info.sample_rate:.6f}",
            "codec": "SPG ordinary IMA ADPCM",
            "id1_hex": f"0x{info.id1:08X}",
            "id2_hex": f"0x{info.id2:08X}",
            "terminator_found": int(decoded.terminator_found),
            "source_words_before_terminator": decoded.source_words_consumed,
            "trailing_words_after_terminator": decoded.trailing_words,
            "rms": f"{rms:.3f}",
            "clipped_samples": clipping,
            "nonzero_samples": nonzero,
            "object_sha256": object_hash,
            "duplicate_of": "",
            "wav_file": str(wav_path.relative_to(output_dir)),
            "raw_file": (
                str(raw_path.relative_to(output_dir))
                if save_raw else ""
            ),
        })

    if not device_only:
        pcm_index = 0
        orphan_index = 0

        for info in all_spf:
            # Skip normal tree-owned device records; they were exported above.
            if info.offset in leaf_by_offset:
                continue

            object_bytes = data[info.offset : info.offset + info.total_size]
            object_hash = sha256(object_bytes)
            payload = data[
                info.offset + SPF_HEADER_SIZE :
                info.offset + info.total_size
            ]

            # The one non-tree IMA object in this tested BIN is an exact
            # duplicate of normal device sound 36.
            if info.id1 == D3_IMA_ID1 and info.id2 == D3_IMA_ID2:
                duplicate_index = device_hash_to_index.get(object_hash)
                if duplicate_index is not None:
                    category = "orphan_duplicate"
                    duplicate_of = f"device_ima:{duplicate_index:03d}"
                else:
                    category = "orphan_ima"
                    duplicate_of = ""

                decoded = decode_spg_ima(payload)
                base_name = (
                    f"extra_ima_{orphan_index:03d}"
                    f"_off_{info.offset:08X}"
                )
                wav_path = (
                    output_dir / "other_recognized_sounds" / "wav"
                    / f"{base_name}.wav"
                )
                raw_path = (
                    output_dir / "other_recognized_sounds" / "raw_spf2"
                    / f"{base_name}.spf2"
                )
                write_wav(wav_path, decoded.samples, info.sample_rate)
                if save_raw:
                    safe_write_raw(raw_path, data, info)

                rms, clipping, nonzero = sample_metrics(decoded.samples)
                manifest_rows.append({
                    "category": category,
                    "category_index": orphan_index,
                    "absolute_offset_hex": f"0x{info.offset:08X}",
                    "resource_pack_hex": "",
                    "resource_entry": "",
                    "sample_rate": info.sample_rate,
                    "source_length_words": info.length_words,
                    "decoded_samples": len(decoded.samples),
                    "duration_seconds": f"{len(decoded.samples) / info.sample_rate:.6f}",
                    "codec": "SPG ordinary IMA ADPCM",
                    "id1_hex": f"0x{info.id1:08X}",
                    "id2_hex": f"0x{info.id2:08X}",
                    "terminator_found": int(decoded.terminator_found),
                    "source_words_before_terminator": decoded.source_words_consumed,
                    "trailing_words_after_terminator": decoded.trailing_words,
                    "rms": f"{rms:.3f}",
                    "clipped_samples": clipping,
                    "nonzero_samples": nonzero,
                    "object_sha256": object_hash,
                    "duplicate_of": duplicate_of,
                    "wav_file": str(wav_path.relative_to(output_dir)),
                    "raw_file": (
                        str(raw_path.relative_to(output_dir))
                        if save_raw else ""
                    ),
                })
                orphan_index += 1
                continue

            if info.id2 == D3_PCM16_ID2:
                samples = decode_offset_binary_pcm16(payload)
                base_name = (
                    f"pcm16_{pcm_index:03d}_off_{info.offset:08X}"
                )
                wav_path = (
                    output_dir / "other_recognized_sounds" / "wav"
                    / f"{base_name}.wav"
                )
                raw_path = (
                    output_dir / "other_recognized_sounds" / "raw_spf2"
                    / f"{base_name}.spf2"
                )
                write_wav(wav_path, samples, info.sample_rate)
                if save_raw:
                    safe_write_raw(raw_path, data, info)

                rms, clipping, nonzero = sample_metrics(samples)
                manifest_rows.append({
                    "category": "pcm16_offset_binary",
                    "category_index": pcm_index,
                    "absolute_offset_hex": f"0x{info.offset:08X}",
                    "resource_pack_hex": "",
                    "resource_entry": "",
                    "sample_rate": info.sample_rate,
                    "source_length_words": info.length_words,
                    "decoded_samples": len(samples),
                    "duration_seconds": f"{len(samples) / info.sample_rate:.6f}",
                    "codec": "16-bit PCM, offset-binary",
                    "id1_hex": f"0x{info.id1:08X}",
                    "id2_hex": f"0x{info.id2:08X}",
                    "terminator_found": "",
                    "source_words_before_terminator": "",
                    "trailing_words_after_terminator": "",
                    "rms": f"{rms:.3f}",
                    "clipped_samples": clipping,
                    "nonzero_samples": nonzero,
                    "object_sha256": object_hash,
                    "duplicate_of": "",
                    "wav_file": str(wav_path.relative_to(output_dir)),
                    "raw_file": (
                        str(raw_path.relative_to(output_dir))
                        if save_raw else ""
                    ),
                })
                pcm_index += 1

    manifest_path = output_dir / "sound_manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(manifest_rows[0].keys()),
        )
        writer.writeheader()
        writer.writerows(manifest_rows)

    device_count = sum(
        row["category"] == "device_ima" for row in manifest_rows
    )
    pcm_count = sum(
        row["category"] == "pcm16_offset_binary"
        for row in manifest_rows
    )
    duplicate_count = sum(
        row["category"] == "orphan_duplicate"
        for row in manifest_rows
    )
    orphan_ima_count = sum(
        row["category"] == "orphan_ima"
        for row in manifest_rows
    )

    report_path = output_dir / "export_report.txt"
    report_path.write_text(
        "\n".join([
            "D-3 25TH COLOR SOUND EXPORT — CORRECTED",
            "======================================",
            f"Input BIN: {bin_path}",
            f"BIN size: {len(data)} bytes (0x{len(data):X})",
            f"BIN SHA-256: {firmware_hash}",
            f"Matches tested firmware: {firmware_hash == EXPECTED_TESTED_SHA256}",
            f"Resource root: 0x{PACK_ROOT:08X}",
            "Resource packs: " + ", ".join(
                f"0x{offset:08X}" for offset in packs
            ),
            f"Valid SPF2ALP signatures: {len(all_spf)}",
            f"Normal device IMA sounds: {device_count}",
            f"Additional PCM16 sounds: {pcm_count}",
            f"Non-tree duplicate IMA objects: {duplicate_count}",
            f"Other non-tree IMA objects: {orphan_ima_count}",
            "",
            "CORRECT DEVICE CODEC",
            "--------------------",
            "SPG ordinary IMA ADPCM",
            "id1 = 0x007F3C55",
            "id2 = 0x64407F00",
            "",
            "This is not the A3600/ADPCM36 variant.",
            "No DLL and no ADPCM36 decoder were used.",
            "",
            "IMA framing:",
            "  predictor starts at 0",
            "  step index starts at 0",
            "  source is little-endian 16-bit words",
            "  low nibble is decoded first",
            "  0xFFFF source word is the one-shot terminator",
            "",
            "OTHER RECOGNIZED OBJECTS",
            "------------------------",
            "Seven SPF2ALP objects with id2 = 0x64400302 decode as",
            "16-bit unsigned/offset-binary PCM centered at 0x8000.",
            "",
            "The non-tree IMA object at 0x00141274 is an exact",
            "duplicate of normal device sound 36 in this firmware.",
        ]) + "\n",
        encoding="utf-8",
    )

    print(f"[*] BIN SHA-256: {firmware_hash}")
    if firmware_hash != EXPECTED_TESTED_SHA256:
        print(
            "[!] Warning: this is not the exact firmware revision used "
            "for validation."
        )
    print(f"[+] Device IMA WAVs: {device_count}")
    print(f"[+] Additional PCM16 WAVs: {pcm_count}")
    print(f"[+] Non-tree duplicate IMA WAVs: {duplicate_count}")
    print(f"[+] Manifest: {manifest_path}")
    print(f"[+] Report: {report_path}")
    print("[✓] No A3600.dll or ADPCM36 decoding was used.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export D-3 sounds using ordinary SPG IMA ADPCM and "
            "offset-binary PCM16 where identified."
        )
    )
    parser.add_argument("bin_path", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--device-only",
        action="store_true",
        help="Export only the 38 normal device-sound resources.",
    )
    parser.add_argument(
        "--no-raw",
        action="store_true",
        help="Do not save exact compressed SPF2ALP objects.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    bin_path = args.bin_path.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()

    if not bin_path.is_file():
        print(f"[!] BIN not found: {bin_path}", file=sys.stderr)
        return 1

    try:
        return export(
            bin_path=bin_path,
            output_dir=output_dir,
            device_only=args.device_only,
            save_raw=not args.no_raw,
        )
    except KeyboardInterrupt:
        print("\n[!] Cancelled", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"[!] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
