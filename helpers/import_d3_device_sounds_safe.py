#!/usr/bin/env python3
"""
Safely import replacement WAV files into the 38 D-3 25th Color device-sound
slots.

Expected WAV names:
    device_000.wav
    device_001.wav
    ...
    device_037.wav

The importer is intentionally conservative:

* The input BIN is never modified.
* It writes a separate output BIN atomically.
* It parses and validates the real resource tree.
* It preserves every resource offset, pack-table entry, slot size, SPF2ALP
  header field, and original sample rate.
* It rejects a replacement that is too long for its original fixed slot.
* It writes a whole-word 0xFFFF terminator and fills unused words with 0xFFFF.
* It prevents accidental 0xFFFF words inside encoded audio.
* It decodes every replacement back to PCM and verifies it before writing.
* It skips WAVs whose PCM is identical to the sound already in the BIN.
* When the tested firmware's non-tree duplicate of device_036 is still an
  exact duplicate, it is synchronized automatically.
* It produces verification WAV files and a detailed CSV report.

No A3600.dll is used. Device sounds use the SPG ordinary IMA ADPCM mode:
    id1 = 0x007F3C55
    id2 = 0x64407F00

Usage:
    python import_d3_device_sounds.py INPUT.bin WAV_FOLDER OUTPUT.bin

The WAV_FOLDER may be either:
    exported_sounds/device_sounds/wav
or the export root:
    exported_sounds

Only exact device_NNN.wav names are considered.

Python 3.9+; standard library only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import os
import re
import shutil
import struct
import sys
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence


EXPECTED_TESTED_SHA256 = (
    "bd25229a3ee01c87e635a0e84e06daa574881271047e13436acc0616e594934e"
)

PACK_ROOT = 0x00140000
PACK_MAGIC_LOW16 = 0x3232

SPF_MAGIC = b"SPF2ALP"
SPF_HEADER_SIZE = 0x28

D3_IMA_ID1 = 0x007F3C55
D3_IMA_ID2 = 0x64407F00

DEVICE_COUNT = 38
DEVICE_NAME_RE = re.compile(r"^device_(\d{3})\.wav$", re.IGNORECASE)


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


@dataclass(frozen=True)
class WavData:
    samples: list[int]
    sample_rate: int
    channels: int
    sample_width: int
    frame_count: int


@dataclass(frozen=True)
class EncodedIma:
    payload: bytes
    decoded_samples: list[int]
    data_words: int
    terminator_word_index: int
    collision_avoidance_count: int
    padded_sample_count: int


def read_u32le(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ValueError(f"32-bit read outside file at 0x{offset:X}")
    return struct.unpack_from("<I", data, offset)[0]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


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
    result: list[PackLeaf] = []

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
                "points outside the BIN"
            )

        result.append(
            PackLeaf(
                parent_offset=pack_offset,
                entry_index=entry_index,
                absolute_offset=absolute_offset,
                size=size,
            )
        )

    return result


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

    return sorted(packs), sorted(
        leaves, key=lambda leaf: leaf.absolute_offset
    )


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
    if info.length_words <= 1:
        raise ValueError(
            f"Invalid audio length {info.length_words} "
            f"at 0x{offset:08X}"
        )
    if offset + info.total_size > len(data):
        raise ValueError(
            f"SPF2ALP at 0x{offset:08X} extends past end of file"
        )
    return info


def locate_device_records(
    data: bytes,
) -> tuple[list[int], list[tuple[PackLeaf, SpfInfo]]]:
    packs, leaves = walk_resource_tree(data, PACK_ROOT)
    records: list[tuple[PackLeaf, SpfInfo]] = []

    for leaf in leaves:
        if data[
            leaf.absolute_offset : leaf.absolute_offset + len(SPF_MAGIC)
        ] != SPF_MAGIC:
            continue

        info = parse_spf_at(data, leaf.absolute_offset)
        if info.id1 != D3_IMA_ID1 or info.id2 != D3_IMA_ID2:
            continue
        if leaf.size != info.total_size:
            raise ValueError(
                f"Device object at 0x{info.offset:08X} has pack size "
                f"{leaf.size}, but its SPF2ALP header declares "
                f"{info.total_size}"
            )
        records.append((leaf, info))

    if len(records) != DEVICE_COUNT:
        raise ValueError(
            f"Expected {DEVICE_COUNT} device sounds, found {len(records)}. "
            "No changes were made."
        )
    return packs, records


def build_ima_step_table() -> list[int]:
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


def apply_ima_nibble(
    predictor: int,
    step_index: int,
    nibble: int,
) -> tuple[int, int]:
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

    predictor = max(-32768, min(32767, predictor))
    step_index += IMA_INDEX_SHIFT[nibble & 0x07]
    step_index = max(0, min(88, step_index))
    return predictor, step_index


def decode_spg_ima(payload: bytes) -> ImaDecodeResult:
    if len(payload) & 1:
        raise ValueError("IMA payload has an odd byte length")

    predictor = 0
    step_index = 0
    samples: list[int] = []
    total_words = len(payload) // 2
    consumed_words = 0

    for word_index in range(total_words):
        word = struct.unpack_from("<H", payload, word_index * 2)[0]
        if word == 0xFFFF:
            return ImaDecodeResult(
                samples=samples,
                source_words_consumed=consumed_words,
                terminator_found=True,
                trailing_words=total_words - word_index - 1,
            )

        consumed_words += 1
        for shift in (0, 4, 8, 12):
            nibble = (word >> shift) & 0x0F
            predictor, step_index = apply_ima_nibble(
                predictor, step_index, nibble
            )
            samples.append(predictor)

    return ImaDecodeResult(
        samples=samples,
        source_words_consumed=consumed_words,
        terminator_found=False,
        trailing_words=0,
    )


def choose_ima_nibble(
    target: int,
    predictor: int,
    step_index: int,
    disallow_f: bool,
) -> tuple[int, int, int]:
    """
    Choose the code whose result is closest to the target under the exact
    D-3/MAME decoder state transition.

    If the first three nibbles of a source word are all F, code F is
    disallowed for the fourth nibble so encoded audio can never accidentally
    contain the hardware's 0xFFFF whole-word terminator.
    """
    best_key: Optional[tuple[int, int, int, int]] = None
    best_result: Optional[tuple[int, int, int]] = None

    desired_sign = 0x08 if target <= predictor else 0x00
    upper = 15 if disallow_f else 16

    for nibble in range(upper):
        candidate_predictor, candidate_index = apply_ima_nibble(
            predictor, step_index, nibble
        )
        error = abs(target - candidate_predictor)
        sign_penalty = (
            0 if (nibble & 0x08) == desired_sign else 1
        )
        key = (error, sign_penalty, nibble & 0x07, nibble)

        if best_key is None or key < best_key:
            best_key = key
            best_result = (
                nibble,
                candidate_predictor,
                candidate_index,
            )

    assert best_result is not None
    return best_result


def encode_spg_ima(
    samples: Sequence[int],
    slot_words: int,
) -> EncodedIma:
    if not samples:
        raise ValueError("Cannot encode an empty sound")
    if slot_words <= 1:
        raise ValueError("Audio slot has no usable payload capacity")

    padded = list(samples)
    while len(padded) % 4:
        # At most three extra samples are needed. Holding the final sample
        # avoids an artificial jump inside the final encoded word.
        padded.append(padded[-1])

    data_words = len(padded) // 4
    if data_words + 1 > slot_words:
        maximum_samples = (slot_words - 1) * 4
        raise ValueError(
            f"Replacement needs {data_words + 1} source words including "
            f"its terminator, but the slot has {slot_words}. "
            f"Maximum decoded length is {maximum_samples} samples."
        )

    predictor = 0
    step_index = 0
    output_words: list[int] = []
    collision_avoidance_count = 0

    for base in range(0, len(padded), 4):
        nibbles: list[int] = []

        for position in range(4):
            disallow_f = (
                position == 3
                and nibbles == [0x0F, 0x0F, 0x0F]
            )
            nibble, predictor, step_index = choose_ima_nibble(
                int(padded[base + position]),
                predictor,
                step_index,
                disallow_f,
            )
            if disallow_f:
                collision_avoidance_count += 1
            nibbles.append(nibble)

        word = (
            nibbles[0]
            | (nibbles[1] << 4)
            | (nibbles[2] << 8)
            | (nibbles[3] << 12)
        )
        if word == 0xFFFF:
            raise AssertionError("Internal terminator-collision error")
        output_words.append(word)

    terminator_word_index = len(output_words)
    output_words.append(0xFFFF)

    # Keep the original header length and pack slot unchanged. All unused
    # words are additional terminators, which cannot be played.
    output_words.extend(
        [0xFFFF] * (slot_words - len(output_words))
    )

    payload = struct.pack(
        "<" + ("H" * len(output_words)),
        *output_words,
    )
    decoded = decode_spg_ima(payload)

    if not decoded.terminator_found:
        raise AssertionError("Encoded stream has no terminator")
    if decoded.source_words_consumed != data_words:
        raise AssertionError(
            "Encoded stream terminated earlier than intended"
        )
    if len(decoded.samples) != len(padded):
        raise AssertionError("Encoded sample count failed verification")

    return EncodedIma(
        payload=payload,
        decoded_samples=decoded.samples,
        data_words=data_words,
        terminator_word_index=terminator_word_index,
        collision_avoidance_count=collision_avoidance_count,
        padded_sample_count=len(padded),
    )


def read_pcm_wav(path: Path) -> WavData:
    try:
        with wave.open(str(path), "rb") as input_wav:
            if input_wav.getcomptype() != "NONE":
                raise ValueError(
                    f"{path.name} is compressed WAV "
                    f"({input_wav.getcomptype()})"
                )

            channels = input_wav.getnchannels()
            sample_width = input_wav.getsampwidth()
            sample_rate = input_wav.getframerate()
            frame_count = input_wav.getnframes()
            raw = input_wav.readframes(frame_count)
    except wave.Error as exc:
        raise ValueError(f"Invalid/unsupported WAV {path.name}: {exc}") from exc

    if channels < 1 or channels > 32:
        raise ValueError(
            f"{path.name}: unsupported channel count {channels}"
        )
    if sample_width not in (1, 2, 3, 4):
        raise ValueError(
            f"{path.name}: unsupported PCM width "
            f"{sample_width * 8} bits"
        )
    if sample_rate <= 0:
        raise ValueError(f"{path.name}: invalid sample rate")
    if frame_count <= 0:
        raise ValueError(f"{path.name}: contains no audio frames")

    expected = frame_count * channels * sample_width
    if len(raw) != expected:
        raise ValueError(
            f"{path.name}: truncated PCM data "
            f"({len(raw)} bytes; expected {expected})"
        )

    samples: list[int] = []
    frame_size = channels * sample_width

    for frame_start in range(0, len(raw), frame_size):
        channel_values: list[int] = []

        for channel in range(channels):
            start = frame_start + channel * sample_width
            chunk = raw[start : start + sample_width]

            if sample_width == 1:
                value = (chunk[0] - 128) << 8
            elif sample_width == 2:
                value = int.from_bytes(
                    chunk, "little", signed=True
                )
            elif sample_width == 3:
                value = int.from_bytes(
                    chunk, "little", signed=True
                ) >> 8
            else:
                value = int.from_bytes(
                    chunk, "little", signed=True
                ) >> 16

            channel_values.append(value)

        mixed = round(sum(channel_values) / channels)
        samples.append(max(-32768, min(32767, mixed)))

    return WavData(
        samples=samples,
        sample_rate=sample_rate,
        channels=channels,
        sample_width=sample_width,
        frame_count=frame_count,
    )


def sinc(value: float) -> float:
    if abs(value) < 1.0e-12:
        return 1.0
    angle = math.pi * value
    return math.sin(angle) / angle


def resample_windowed_sinc(
    samples: Sequence[int],
    source_rate: int,
    target_rate: int,
    half_width: int = 16,
) -> list[int]:
    """
    Blackman-windowed sinc resampler.

    A low-pass cutoff is applied when reducing the sample rate. This is
    slower than linear interpolation but avoids obvious downsampling aliasing
    and needs no external package.
    """
    if source_rate == target_rate:
        return list(samples)
    if not samples:
        return []
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("Invalid sample rate")

    output_length = max(
        1,
        round(len(samples) * target_rate / source_rate),
    )
    source_per_output = source_rate / target_rate
    cutoff = min(1.0, target_rate / source_rate) * 0.95
    output: list[int] = []

    for output_index in range(output_length):
        center = output_index * source_per_output
        center_floor = math.floor(center)
        weighted_sum = 0.0
        weight_sum = 0.0

        for source_index in range(
            center_floor - half_width + 1,
            center_floor + half_width + 1,
        ):
            if source_index < 0 or source_index >= len(samples):
                continue

            distance = center - source_index
            normalized = distance / half_width
            if abs(normalized) >= 1.0:
                continue

            window = (
                0.42
                + 0.5 * math.cos(math.pi * normalized)
                + 0.08 * math.cos(2.0 * math.pi * normalized)
            )
            weight = cutoff * sinc(cutoff * distance) * window
            weighted_sum += samples[source_index] * weight
            weight_sum += weight

        if abs(weight_sum) < 1.0e-12:
            nearest = min(
                len(samples) - 1,
                max(0, round(center)),
            )
            value = int(samples[nearest])
        else:
            value = round(weighted_sum / weight_sum)

        output.append(max(-32768, min(32767, value)))

    return output


def apply_fade_out(
    samples: Sequence[int],
    sample_rate: int,
    fade_ms: float,
) -> list[int]:
    output = list(samples)
    if fade_ms <= 0.0 or not output:
        return output

    fade_samples = min(
        len(output),
        max(1, round(sample_rate * fade_ms / 1000.0)),
    )
    start = len(output) - fade_samples

    for index in range(fade_samples):
        # Last sample becomes exactly zero.
        gain = (
            (fade_samples - index - 1) / (fade_samples - 1)
            if fade_samples > 1 else 0.0
        )
        output[start + index] = round(output[start + index] * gain)

    return output


def write_pcm16_wav(
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


def audio_metrics(
    reference: Sequence[int],
    decoded: Sequence[int],
) -> tuple[float, int, float, float]:
    if len(reference) != len(decoded):
        raise ValueError("Metric sample-count mismatch")
    if not reference:
        return float("-inf"), 0, 0.0, 0.0

    signal_energy = 0
    error_energy = 0
    peak_error = 0
    decoded_peak = 0

    for expected, actual in zip(reference, decoded):
        signal_energy += expected * expected
        error = expected - actual
        error_energy += error * error
        peak_error = max(peak_error, abs(error))
        decoded_peak = max(decoded_peak, abs(actual))

    if error_energy == 0:
        snr = float("inf")
    elif signal_energy == 0:
        snr = float("-inf")
    else:
        snr = 10.0 * math.log10(signal_energy / error_energy)

    rms_error = math.sqrt(error_energy / len(reference))
    return snr, peak_error, rms_error, float(decoded_peak)


def locate_wav_folder(path: Path) -> Path:
    path = path.expanduser().resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"WAV folder not found: {path}")

    direct = any(
        DEVICE_NAME_RE.match(item.name)
        for item in path.iterdir()
        if item.is_file()
    )
    if direct:
        return path

    nested = path / "device_sounds" / "wav"
    if nested.is_dir():
        return nested

    return path


def collect_replacement_wavs(folder: Path) -> dict[int, Path]:
    replacements: dict[int, Path] = {}
    out_of_range: list[str] = []

    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue

        match = DEVICE_NAME_RE.match(path.name)
        if not match:
            continue

        index = int(match.group(1))
        if index < 0 or index >= DEVICE_COUNT:
            out_of_range.append(path.name)
            continue
        if index in replacements:
            raise ValueError(
                f"Multiple WAV files map to device_{index:03d}"
            )
        replacements[index] = path

    if out_of_range:
        raise ValueError(
            "Out-of-range device WAV names: "
            + ", ".join(out_of_range)
        )
    if not replacements:
        raise ValueError(
            f"No device_000.wav through device_037.wav files "
            f"were found in {folder}"
        )
    return replacements


def ranges_overlap(
    first_start: int,
    first_end: int,
    second_start: int,
    second_end: int,
) -> bool:
    return first_start < second_end and second_start < first_end


def find_device_036_duplicates(
    data: bytes,
    device_records: Sequence[tuple[PackLeaf, SpfInfo]],
) -> list[SpfInfo]:
    """
    Find non-tree SPF2ALP objects that are currently byte-for-byte identical
    to device_036. This identifies the tested firmware's duplicate at
    0x00141274 without blindly relying on a hard-coded offset.
    """
    _, device_info = device_records[36]
    device_object = data[
        device_info.offset : device_info.offset + device_info.total_size
    ]
    tree_offsets = {
        info.offset for _, info in device_records
    }
    duplicates: list[SpfInfo] = []

    for offset in find_all(data, SPF_MAGIC):
        if offset in tree_offsets:
            continue
        try:
            info = parse_spf_at(data, offset)
        except ValueError:
            continue

        if info.total_size != device_info.total_size:
            continue
        candidate = data[offset : offset + info.total_size]
        if candidate == device_object:
            duplicates.append(info)

    return duplicates


def write_atomic(
    output_path: Path,
    data: bytes,
    overwrite: bool,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Output already exists: {output_path}. "
            "Use --overwrite to replace it."
        )

    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            dir=str(output_path.parent),
            prefix=output_path.name + ".",
            suffix=".tmp",
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())

        if temporary_path.stat().st_size != len(data):
            raise IOError("Temporary output size verification failed")

        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def import_sounds(args: argparse.Namespace) -> int:
    input_bin = args.input_bin.expanduser().resolve()
    output_bin = args.output_bin.expanduser().resolve()
    wav_folder = locate_wav_folder(args.wav_folder)

    if not input_bin.is_file():
        raise FileNotFoundError(f"Input BIN not found: {input_bin}")
    if input_bin == output_bin:
        raise ValueError(
            "Input and output BIN paths must be different. "
            "The importer never edits the input file."
        )
    if output_bin.exists() and not args.overwrite and not args.dry_run:
        raise FileExistsError(
            f"Output already exists: {output_bin}. "
            "Use --overwrite to replace it."
        )

    original = input_bin.read_bytes()
    original_hash = sha256_bytes(original)
    packs, records = locate_device_records(original)
    replacements = collect_replacement_wavs(wav_folder)
    device_036_duplicates = find_device_036_duplicates(
        original, records
    )

    print(f"[*] Input BIN: {input_bin}")
    print(f"[*] Input SHA-256: {original_hash}")
    print(f"[*] Resource packs validated: {len(packs)}")
    print(f"[*] Device slots validated: {len(records)}")
    print(f"[*] Candidate WAV files: {len(replacements)}")
    if original_hash != EXPECTED_TESTED_SHA256:
        print(
            "[!] BIN hash differs from the original tested firmware, "
            "but its complete sound-resource structure is valid."
        )

    patched = bytearray(original)
    patch_ranges: list[tuple[int, int, str]] = []
    rows: list[dict[str, object]] = []
    changed_indices: list[int] = []
    skipped_indices: list[int] = []

    preview_dir = (
        args.preview_dir.expanduser().resolve()
        if args.preview_dir is not None
        else output_bin.parent / (
            output_bin.stem + "_sound_import_previews"
        )
    )

    for index in sorted(replacements):
        wav_path = replacements[index]
        leaf, info = records[index]

        wav_data = read_pcm_wav(wav_path)
        converted = resample_windowed_sinc(
            wav_data.samples,
            wav_data.sample_rate,
            info.sample_rate,
        )
        converted = apply_fade_out(
            converted,
            info.sample_rate,
            args.fade_out_ms,
        )

        old_payload = original[
            info.offset + SPF_HEADER_SIZE :
            info.offset + info.total_size
        ]
        old_decoded = decode_spg_ima(old_payload)
        if not old_decoded.terminator_found:
            raise ValueError(
                f"Existing device_{index:03d} has no terminator"
            )

        max_samples = (info.length_words - 1) * 4
        duration = len(converted) / info.sample_rate
        max_duration = max_samples / info.sample_rate

        common = {
            "index": index,
            "wav_file": str(wav_path),
            "absolute_offset_hex": f"0x{info.offset:08X}",
            "resource_pack_hex": f"0x{leaf.parent_offset:08X}",
            "resource_entry": leaf.entry_index,
            "slot_size_bytes": info.total_size,
            "slot_words": info.length_words,
            "target_sample_rate": info.sample_rate,
            "input_sample_rate": wav_data.sample_rate,
            "input_channels": wav_data.channels,
            "input_bits_per_sample": wav_data.sample_width * 8,
            "input_frames": wav_data.frame_count,
            "converted_samples": len(converted),
            "converted_duration_seconds": f"{duration:.6f}",
            "maximum_samples": max_samples,
            "maximum_duration_seconds": f"{max_duration:.6f}",
        }

        if (
            len(converted) == len(old_decoded.samples)
            and converted == old_decoded.samples
            and args.fade_out_ms == 0.0
        ):
            skipped_indices.append(index)
            rows.append({
                **common,
                "status": "skipped-identical-pcm",
                "encoded_data_words": "",
                "terminator_word_index": "",
                "unused_words": "",
                "collision_avoidance_count": "",
                "roundtrip_snr_db": "",
                "roundtrip_peak_error": "",
                "roundtrip_rms_error": "",
                "before_object_sha256": sha256_bytes(
                    original[
                        info.offset :
                        info.offset + info.total_size
                    ]
                ),
                "after_object_sha256": "",
                "duplicate_copies_updated": 0,
                "preview_wav": "",
            })
            print(
                f"[=] device_{index:03d}: PCM is unchanged; skipped"
            )
            continue

        if len(converted) > max_samples:
            raise ValueError(
                f"device_{index:03d}.wav is too long after conversion: "
                f"{len(converted)} samples ({duration:.6f}s) at "
                f"{info.sample_rate} Hz. This fixed slot permits at most "
                f"{max_samples} samples ({max_duration:.6f}s). "
                "No output BIN was written."
            )

        encoded = encode_spg_ima(
            converted,
            info.length_words,
        )
        padded_reference = list(converted)
        while len(padded_reference) < encoded.padded_sample_count:
            padded_reference.append(padded_reference[-1])

        snr, peak_error, rms_error, decoded_peak = audio_metrics(
            padded_reference,
            encoded.decoded_samples,
        )

        original_object = original[
            info.offset : info.offset + info.total_size
        ]
        new_object = (
            original_object[:SPF_HEADER_SIZE]
            + encoded.payload
        )
        if len(new_object) != info.total_size:
            raise AssertionError("Replacement object size changed")
        if new_object[:SPF_HEADER_SIZE] != original_object[:SPF_HEADER_SIZE]:
            raise AssertionError("SPF2ALP header changed unexpectedly")

        start = info.offset
        end = start + info.total_size
        patched[start:end] = new_object
        patch_ranges.append(
            (start, end, f"device_{index:03d}")
        )

        duplicate_updates = 0
        if index == 36:
            for duplicate in device_036_duplicates:
                duplicate_start = duplicate.offset
                duplicate_end = (
                    duplicate.offset + duplicate.total_size
                )
                patched[duplicate_start:duplicate_end] = new_object
                patch_ranges.append(
                    (
                        duplicate_start,
                        duplicate_end,
                        "device_036 duplicate",
                    )
                )
                duplicate_updates += 1

        preview_path = (
            preview_dir / f"device_{index:03d}.wav"
        )
        if not args.no_previews:
            write_pcm16_wav(
                preview_path,
                encoded.decoded_samples,
                info.sample_rate,
            )

        changed_indices.append(index)
        rows.append({
            **common,
            "status": "replaced",
            "encoded_data_words": encoded.data_words,
            "terminator_word_index": encoded.terminator_word_index,
            "unused_words": (
                info.length_words
                - encoded.data_words
                - 1
            ),
            "collision_avoidance_count": (
                encoded.collision_avoidance_count
            ),
            "roundtrip_snr_db": (
                "infinite" if math.isinf(snr)
                else f"{snr:.3f}"
            ),
            "roundtrip_peak_error": peak_error,
            "roundtrip_rms_error": f"{rms_error:.3f}",
            "before_object_sha256": sha256_bytes(
                original_object
            ),
            "after_object_sha256": sha256_bytes(new_object),
            "duplicate_copies_updated": duplicate_updates,
            "preview_wav": (
                str(preview_path)
                if not args.no_previews else ""
            ),
        })

        print(
            f"[+] device_{index:03d}: "
            f"{len(converted)} samples, "
            f"{encoded.data_words}/{info.length_words - 1} data words, "
            f"SNR={'∞' if math.isinf(snr) else f'{snr:.2f} dB'}"
        )
        if duplicate_updates:
            print(
                f"    synchronized {duplicate_updates} exact "
                "non-tree copy of device_036"
            )

    # Ensure patch ranges do not accidentally overlap each other.
    for first_index, first in enumerate(patch_ranges):
        for second in patch_ranges[first_index + 1 :]:
            if ranges_overlap(first[0], first[1], second[0], second[1]):
                raise AssertionError(
                    f"Patch ranges overlap: {first[2]} and {second[2]}"
                )

    # Full post-patch structural verification in memory.
    patched_bytes = bytes(patched)
    if len(patched_bytes) != len(original):
        raise AssertionError("BIN size changed")

    verified_packs, verified_records = locate_device_records(
        patched_bytes
    )
    if verified_packs != packs:
        raise AssertionError("Resource-pack topology changed")
    if len(verified_records) != DEVICE_COUNT:
        raise AssertionError("Device-record count changed")

    for index in changed_indices:
        _, info = verified_records[index]
        payload = patched_bytes[
            info.offset + SPF_HEADER_SIZE :
            info.offset + info.total_size
        ]
        decoded = decode_spg_ima(payload)
        if not decoded.terminator_found:
            raise AssertionError(
                f"Post-patch device_{index:03d} has no terminator"
            )

    # Prove every modified byte lies inside an intended range.
    for offset, (before, after) in enumerate(
        zip(original, patched_bytes)
    ):
        if before == after:
            continue
        if not any(
            start <= offset < end
            for start, end, _ in patch_ranges
        ):
            raise AssertionError(
                f"Unexpected change outside patch ranges at "
                f"0x{offset:08X}"
            )

    report_path = (
        args.report.expanduser().resolve()
        if args.report is not None
        else output_bin.with_name(
            output_bin.name + ".sound_import_report.csv"
        )
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open(
        "w", newline="", encoding="utf-8"
    ) as report_file:
        if rows:
            writer = csv.DictWriter(
                report_file,
                fieldnames=list(rows[0].keys()),
            )
            writer.writeheader()
            writer.writerows(rows)

    if args.dry_run:
        print()
        print("[✓] Dry run passed. No BIN was written.")
        print(f"[✓] Report: {report_path}")
        return 0

    write_atomic(
        output_bin,
        patched_bytes,
        overwrite=args.overwrite,
    )

    # Verify the bytes actually stored on disk.
    if output_bin.stat().st_size != len(original):
        raise IOError("Written output BIN has the wrong size")
    output_hash = sha256_file(output_bin)
    if output_hash != sha256_bytes(patched_bytes):
        raise IOError("Written output BIN hash verification failed")

    print()
    print(f"[✓] Replaced sounds: {len(changed_indices)}")
    print(f"[✓] Skipped identical WAVs: {len(skipped_indices)}")
    print(f"[✓] Output BIN: {output_bin}")
    print(f"[✓] Output SHA-256: {output_hash}")
    print(f"[✓] Import report: {report_path}")
    if changed_indices and not args.no_previews:
        print(f"[✓] Verification WAVs: {preview_dir}")
    print("[✓] Input BIN was not modified.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Safely import device_000.wav through device_037.wav "
            "into fixed D-3 sound slots."
        )
    )
    parser.add_argument(
        "input_bin",
        type=Path,
        help="Original or previously modified D-3 BIN",
    )
    parser.add_argument(
        "wav_folder",
        type=Path,
        help=(
            "Folder containing device_NNN.wav files, or the root "
            "folder produced by the exporter"
        ),
    )
    parser.add_argument(
        "output_bin",
        type=Path,
        help="New BIN to create; the input BIN is never edited",
    )
    parser.add_argument(
        "--fade-out-ms",
        type=float,
        default=0.0,
        help=(
            "Optionally fade the end before encoding. Default: 0 "
            "(do not alter the WAV content)."
        ),
    )
    parser.add_argument(
        "--preview-dir",
        type=Path,
        default=None,
        help=(
            "Where to write decoded verification WAVs. Default: "
            "<output stem>_sound_import_previews beside the output BIN."
        ),
    )
    parser.add_argument(
        "--no-previews",
        action="store_true",
        help="Do not write decoded verification WAVs.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help=(
            "CSV report path. Default: "
            "<output BIN>.sound_import_report.csv"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and encode everything without writing a BIN.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Permit replacement of an existing output BIN.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.fade_out_ms < 0:
        print(
            "[!] ERROR: --fade-out-ms cannot be negative",
            file=sys.stderr,
        )
        return 1

    try:
        return import_sounds(args)
    except KeyboardInterrupt:
        print("\n[!] Cancelled", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"[!] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
