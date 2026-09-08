#!/usr/bin/env python3
"""
Replace one directly-addressable sprite image/subimage reference inside a
D-Ark 25th Color animation scene.

Example:
    python replace_d_ark_animation_image.py \
        D-Ark.bin 944 1884_2_0.png 1917_0_0.png D-Ark_out.bin

What the PNG-style identifiers mean:
    IMAGE_SUBIMAGE_BANK.png

The animation bytecode selects IMAGE and sometimes SUBIMAGE.
The BANK component is palette-bank information from the sprite package and is
not encoded in these animation commands, so BANK is validated syntactically
but is not patched here.

This tool is conservative:
- It parses the real D-Ark animation database.
- It traces possible child animation groups from the requested high-level
  animation ID.
- It patches only an exact source IMAGE/SUBIMAGE reference inside that traced
  scene chain.
- It refuses ambiguous matches unless --all-matches is given.
- It never changes animation-record sizes or offset tables.

Known direct encodings:
    8001 IMAGE           -> default subimage 0
    C001 IMAGE           -> default subimage 0
    C201 IMAGE           -> default subimage 0
    A001 IMAGE SUBIMAGE  -> explicit subimage

For an explicit A001 reference, replacing with subimage 0 uses:
    A001 NEW_IMAGE 0000

The stock compiler usually emits the shorter 8001 form for subimage 0, so
A001 ... 0000 is an experimental fixed-size equivalent. It avoids changing
record sizes. Test the resulting BIN on hardware before relying on it.
"""

import argparse
import os
import re
import struct
import tempfile
from collections import defaultdict, deque
from pathlib import Path

GP_HEADER = b"GP-SPIF-HEADER"

# Sprite package.
SPRITE_PACKAGE_BASE = 0x00280000

# Animation archive.
ARCHIVE_BASE = 0x00243000
ARCHIVE_SIZE = 0x0003AC40
SECTION_COUNT = 21
ANIMATION_OFFSET_SECTION = 13
ANIMATION_PAYLOAD_SECTION = 14
GROUP_SECTION = 18
GROUP_PAIR_COUNT = 67
EMPTY_ID = 0xFFFF

RE_PNG = re.compile(r"^(\d+)_(\d+)_(\d+)\.png$", re.IGNORECASE)

OP_DEFAULT_SUBIMAGE = {0x8001, 0xC001, 0xC201}
OP_EXPLICIT_SUBIMAGE = 0xA001

# These opcodes often carry IDs used to enter another animation/group family.
POSSIBLE_LINK_OPCODES = {0x8001, 0x8201, 0x8601, 0xA001, 0xC001, 0xC201}


def le16(data, off):
    return struct.unpack_from("<H", data, off)[0]


def le32(data, off):
    return struct.unpack_from("<I", data, off)[0]


def parse_png_id(text):
    name = os.path.basename(text)
    m = RE_PNG.match(name)
    if not m:
        raise RuntimeError(
            f"{text!r} must look like IMAGE_SUBIMAGE_BANK.png, "
            "for example 1884_2_0.png"
        )
    return tuple(int(m.group(i)) for i in range(1, 4))


def read_words(data, start, end):
    if end < start or ((end - start) % 2):
        raise RuntimeError(f"Invalid word range 0x{start:X}..0x{end:X}")
    return list(struct.unpack_from("<" + "H" * ((end - start) // 2), data, start))


# ----------------------------------------------------------------------
# Sprite package: used only to validate image/subimage IDs.
# ----------------------------------------------------------------------

def parse_sprite_package(data):
    base = SPRITE_PACKAGE_BASE

    if base + 16 > len(data):
        raise RuntimeError("D-Ark sprite package lies outside the BIN")

    img_defs = le32(data, base)
    spr_defs = le32(data, base + 4)
    palettes = le32(data, base + 8)
    chars = le32(data, base + 12)

    if not (0 < img_defs < spr_defs < palettes < chars <= len(data) - base):
        raise RuntimeError("Invalid D-Ark sprite package header at 0x280000")

    if (spr_defs - img_defs) % 6:
        raise RuntimeError("Invalid D-Ark ImageDef table")

    num_images = (spr_defs - img_defs) // 6
    num_sprites = (palettes - spr_defs) // 8

    def image_def(index):
        off = base + img_defs + index * 6
        return {
            "sprite_start": le16(data, off),
            "width": data[off + 2],
            "height": data[off + 3],
            "palette_start": le16(data, off + 4),
        }

    def subimage_count(index):
        if not 0 <= index < num_images:
            raise RuntimeError(f"image index {index} is outside 0..{num_images - 1}")

        current = image_def(index)
        spp = current["width"] * current["height"]

        if spp <= 0:
            raise RuntimeError(f"image {index} has an invalid zero sprite grid")

        if index + 1 < num_images:
            next_start = image_def(index + 1)["sprite_start"]
        else:
            next_start = num_sprites

        total = next_start - current["sprite_start"]

        if total < 0 or total % spp:
            raise RuntimeError(
                f"image {index} has an invalid sprite/subimage allocation"
            )

        return total // spp

    return num_images, subimage_count


# ----------------------------------------------------------------------
# Animation database.
# ----------------------------------------------------------------------

def parse_sections(data):
    word_offsets = [
        le32(data, ARCHIVE_BASE + i * 4)
        for i in range(SECTION_COUNT)
    ]

    if word_offsets[0] != SECTION_COUNT * 2:
        raise RuntimeError("Unexpected D-Ark animation archive header")

    if any(
        word_offsets[i] > word_offsets[i + 1]
        for i in range(len(word_offsets) - 1)
    ):
        raise RuntimeError("Animation archive section offsets are not monotonic")

    sections = []

    for i, word_off in enumerate(word_offsets):
        start = ARCHIVE_BASE + word_off * 2

        if i + 1 < len(word_offsets):
            end = ARCHIVE_BASE + word_offsets[i + 1] * 2
        else:
            end = ARCHIVE_BASE + ARCHIVE_SIZE

        sections.append({
            "index": i,
            "start": start,
            "end": end,
            "size": end - start,
        })

    return sections


def parse_animation_records(data, sections):
    offset_sec = sections[ANIMATION_OFFSET_SECTION]
    payload_sec = sections[ANIMATION_PAYLOAD_SECTION]

    offsets = read_words(data, offset_sec["start"], offset_sec["end"])

    if len(offsets) < 2:
        raise RuntimeError("Animation offset table is too small")

    if any(offsets[i] > offsets[i + 1] for i in range(len(offsets) - 1)):
        raise RuntimeError("Animation offsets are not monotonic")

    if offsets[-1] * 4 != payload_sec["size"]:
        raise RuntimeError("Animation payload end marker mismatch")

    records = []

    for animation_id in range(len(offsets) - 1):
        start = payload_sec["start"] + offsets[animation_id] * 4
        end = payload_sec["start"] + offsets[animation_id + 1] * 4

        records.append({
            "animation_id": animation_id,
            "start": start,
            "end": end,
            "words": read_words(data, start, end),
        })

    return records


def parse_groups(data, sections):
    sec = sections[GROUP_SECTION]

    if sec["size"] % 4:
        raise RuntimeError("Animation group section size is invalid")

    pair_count = sec["size"] // 4

    if pair_count % GROUP_PAIR_COUNT:
        raise RuntimeError("Unexpected animation group table size")

    groups = []

    for group_index in range(pair_count // GROUP_PAIR_COUNT):
        start = sec["start"] + group_index * GROUP_PAIR_COUNT * 4
        pairs = []

        for slot in range(GROUP_PAIR_COUNT):
            off = start + slot * 4
            pairs.append({
                "slot": slot,
                "animation_id": le16(data, off),
                "flag": le16(data, off + 2),
                "offset": off,
            })

        groups.append({
            "group_index": group_index,
            "start": start,
            "pairs": pairs,
        })

    return groups


def build_membership(groups):
    membership = defaultdict(list)

    for group in groups:
        for pair in group["pairs"]:
            animation_id = pair["animation_id"]

            if animation_id != EMPTY_ID:
                membership[animation_id].append({
                    "group_index": group["group_index"],
                    "slot": pair["slot"],
                    "flag": pair["flag"],
                })

    return membership


def possible_link_animation_ids(record, animation_count, membership):
    words = record["words"]
    out = []

    for i in range(len(words) - 1):
        opcode = words[i]

        if opcode not in POSSIBLE_LINK_OPCODES:
            continue

        value = words[i + 1]

        if not 0 <= value < animation_count:
            continue

        # Stronger as a scene/group link if that value really is an animation
        # participating in the fixed group table.
        if value not in membership:
            continue

        out.append({
            "animation_id": value,
            "opcode": opcode,
            "word_index": i + 1,
        })

    return out


def follow_group_base_chain(start_group, groups, membership, max_hops):
    result = []
    current = start_group
    seen = set()

    while current not in seen and len(result) <= max_hops:
        seen.add(current)
        result.append(current)

        base_animation = groups[current]["pairs"][0]["animation_id"]

        next_groups = [
            item["group_index"]
            for item in membership.get(base_animation, [])
            if item["group_index"] != current
        ]

        if not next_groups:
            break

        # In the validated D-Ark data this linkage is normally unique.
        current = next_groups[0]

    return result


def traced_groups_for_animation(
    animation_id,
    records,
    groups,
    membership,
    max_group_hops,
):
    if not 0 <= animation_id < len(records):
        raise RuntimeError(
            f"animation_id {animation_id} is outside 0..{len(records) - 1}"
        )

    seeds = set()

    # The animation's own group(s).
    for item in membership.get(animation_id, []):
        seeds.add(item["group_index"])

    # More importantly for high-level evolution animations such as 944:
    # inspect embedded operands that are also real grouped animation IDs.
    for link in possible_link_animation_ids(
        records[animation_id],
        len(records),
        membership,
    ):
        for item in membership.get(link["animation_id"], []):
            seeds.add(item["group_index"])

    traced = set()

    for seed in seeds:
        traced.update(
            follow_group_base_chain(
                seed,
                groups,
                membership,
                max_group_hops,
            )
        )

    return sorted(traced), sorted(seeds)


# ----------------------------------------------------------------------
# Exact IMAGE/SUBIMAGE references.
# ----------------------------------------------------------------------

def exact_image_refs(record, image_index, subimage_index):
    words = record["words"]
    hits = []

    for i in range(len(words) - 1):
        opcode = words[i]

        # Default subimage 0.
        if subimage_index == 0 and opcode in OP_DEFAULT_SUBIMAGE:
            if words[i + 1] == image_index:
                hits.append({
                    "kind": "default",
                    "opcode_word_index": i,
                    "image_word_index": i + 1,
                    "subimage_word_index": None,
                    "opcode": opcode,
                    "image_index": image_index,
                    "subimage_index": 0,
                })

        # Explicit subimage form.
        if opcode == OP_EXPLICIT_SUBIMAGE and i + 2 < len(words):
            if (
                words[i + 1] == image_index
                and words[i + 2] == subimage_index
            ):
                hits.append({
                    "kind": "explicit",
                    "opcode_word_index": i,
                    "image_word_index": i + 1,
                    "subimage_word_index": i + 2,
                    "opcode": opcode,
                    "image_index": image_index,
                    "subimage_index": subimage_index,
                })

    return hits


def find_scene_matches(
    source_image,
    source_subimage,
    traced_groups,
    records,
    groups,
):
    found = []

    for group_index in traced_groups:
        group = groups[group_index]

        for pair in group["pairs"]:
            animation_id = pair["animation_id"]

            if animation_id == EMPTY_ID:
                continue

            if not 0 <= animation_id < len(records):
                continue

            record = records[animation_id]

            for hit in exact_image_refs(
                record,
                source_image,
                source_subimage,
            ):
                found.append({
                    "group_index": group_index,
                    "slot": pair["slot"],
                    "animation_id": animation_id,
                    "record": record,
                    "hit": hit,
                })

    # Remove duplicates caused by an animation appearing in two adjacent groups.
    unique = {}
    for item in found:
        key = (
            item["animation_id"],
            item["hit"]["opcode_word_index"],
        )
        unique[key] = item

    return list(unique.values())


def patch_match(
    data,
    match,
    replacement_image,
    replacement_subimage,
):
    record = match["record"]
    hit = match["hit"]

    image_off = record["start"] + hit["image_word_index"] * 2
    old_image = le16(data, image_off)

    if old_image != hit["image_index"]:
        raise RuntimeError(
            f"Internal validation failed at 0x{image_off:08X}: "
            f"expected image {hit['image_index']}, found {old_image}"
        )

    struct.pack_into("<H", data, image_off, replacement_image)

    changes = [{
        "offset": image_off,
        "old": old_image,
        "new": replacement_image,
        "field": "image_index",
    }]

    if hit["kind"] == "explicit":
        sub_off = record["start"] + hit["subimage_word_index"] * 2
        old_sub = le16(data, sub_off)

        if old_sub != hit["subimage_index"]:
            raise RuntimeError(
                f"Internal validation failed at 0x{sub_off:08X}: "
                f"expected subimage {hit['subimage_index']}, found {old_sub}"
            )

        struct.pack_into("<H", data, sub_off, replacement_subimage)

        changes.append({
            "offset": sub_off,
            "old": old_sub,
            "new": replacement_subimage,
            "field": "subimage_index",
        })

        experimental_zero = replacement_subimage == 0

    else:
        if replacement_subimage != 0:
            raise RuntimeError(
                "The source reference uses the compact default-subimage encoding, "
                "which has no room for a nonzero replacement subimage without "
                "changing the animation record size. This tool refuses that edit."
            )

        experimental_zero = False

    return changes, experimental_zero


def safe_write(src, dst, data):
    dst.parent.mkdir(parents=True, exist_ok=True)

    if src.resolve() != dst.resolve():
        dst.write_bytes(data)
        return

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{dst.name}.",
        suffix=".tmp",
        dir=str(dst.parent),
    )
    os.close(fd)
    temp_path = Path(temp_name)

    try:
        temp_path.write_bytes(data)

        if temp_path.stat().st_size != len(data):
            raise RuntimeError("Temporary BIN size validation failed")

        os.replace(temp_path, dst)

    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_bin")
    ap.add_argument("animation_id", type=int)
    ap.add_argument("source_png", help="Example: 1884_2_0.png")
    ap.add_argument("replacement_png", help="Example: 1917_0_0.png")
    ap.add_argument("output_bin")
    ap.add_argument(
        "--max-group-hops",
        type=int,
        default=32,
        help="Maximum number of linked animation groups to follow.",
    )
    ap.add_argument(
        "--all-matches",
        action="store_true",
        help="Patch every exact source match found in the traced scene.",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Find and display the patch without writing a BIN.",
    )
    args = ap.parse_args()

    src = Path(args.input_bin)
    dst = Path(args.output_bin)

    data = bytearray(src.read_bytes())

    if not data.startswith(GP_HEADER):
        raise RuntimeError(
            "Input does not look like a compatible D-Ark GP-SPIF BIN"
        )

    source_image, source_subimage, source_bank = parse_png_id(
        args.source_png
    )
    replacement_image, replacement_subimage, replacement_bank = parse_png_id(
        args.replacement_png
    )

    num_images, subimage_count = parse_sprite_package(data)

    source_count = subimage_count(source_image)
    replacement_count = subimage_count(replacement_image)

    if not 0 <= source_subimage < source_count:
        raise RuntimeError(
            f"{args.source_png}: image {source_image} has "
            f"{source_count} subimage(s), so subimage {source_subimage} is invalid"
        )

    if not 0 <= replacement_subimage < replacement_count:
        raise RuntimeError(
            f"{args.replacement_png}: image {replacement_image} has "
            f"{replacement_count} subimage(s), so subimage "
            f"{replacement_subimage} is invalid"
        )

    sections = parse_sections(data)
    records = parse_animation_records(data, sections)
    groups = parse_groups(data, sections)
    membership = build_membership(groups)

    traced_groups, seed_groups = traced_groups_for_animation(
        args.animation_id,
        records,
        groups,
        membership,
        args.max_group_hops,
    )

    matches = find_scene_matches(
        source_image,
        source_subimage,
        traced_groups,
        records,
        groups,
    )

    print(f"Target animation_id: {args.animation_id}")
    print(f"Source:      {source_image}_{source_subimage}_{source_bank}.png")
    print(
        f"Replacement: {replacement_image}_{replacement_subimage}_"
        f"{replacement_bank}.png"
    )
    print()
    print(f"Seed animation group(s): {seed_groups}")
    print(f"Traced animation groups: {traced_groups}")
    print()

    if not matches:
        raise RuntimeError(
            f"No exact reference to image {source_image}, subimage "
            f"{source_subimage} was found in the traced scene for "
            f"animation {args.animation_id}."
        )

    print(f"Exact match(es): {len(matches)}")

    for i, match in enumerate(matches, 1):
        record = match["record"]
        hit = match["hit"]

        image_off = (
            record["start"]
            + hit["image_word_index"] * 2
        )

        sub_text = ""

        if hit["subimage_word_index"] is not None:
            sub_off = (
                record["start"]
                + hit["subimage_word_index"] * 2
            )
            sub_text = f", subimage word @ 0x{sub_off:08X}"

        print(
            f"  {i}. group {match['group_index']}, "
            f"slot {match['slot']}, animation record "
            f"{match['animation_id']}, "
            f"{hit['kind']} encoding, "
            f"image word @ 0x{image_off:08X}{sub_text}"
        )

    if len(matches) > 1 and not args.all_matches:
        raise RuntimeError(
            "More than one exact match was found. Re-run with --all-matches "
            "only if you intentionally want every listed occurrence changed."
        )

    to_patch = matches if args.all_matches else [matches[0]]
    all_changes = []
    experimental_zero = False

    for match in to_patch:
        changes, is_experimental_zero = patch_match(
            data,
            match,
            replacement_image,
            replacement_subimage,
        )

        all_changes.extend(changes)
        experimental_zero = (
            experimental_zero
            or is_experimental_zero
        )

    print()
    print("Planned changes:" if args.dry_run else "Changes:")

    for change in all_changes:
        print(
            f"  0x{change['offset']:08X}: "
            f"{change['field']} "
            f"{change['old']} -> {change['new']}"
        )

    if source_bank != replacement_bank:
        print()
        print(
            "[NOTE] The filename palette-bank component changed from "
            f"{source_bank} to {replacement_bank}, but animation commands "
            "do not encode that bank. No palette-bank field was patched."
        )

    if experimental_zero:
        print()
        print(
            "[WARN] The source used explicit A001 IMAGE SUBIMAGE encoding "
            "and the replacement requests subimage 0. The patch keeps the "
            "record the same size and writes A001 NEW_IMAGE 0000. The stock "
            "data usually uses the shorter 8001 form for subimage 0, so this "
            "specific case is experimental and should be hardware-tested."
        )

    if args.dry_run:
        print()
        print("[DRY] No output BIN was written.")
        return

    safe_write(src, dst, data)

    print()
    print(f"[DONE] Wrote {dst}")


if __name__ == "__main__":
    main()
