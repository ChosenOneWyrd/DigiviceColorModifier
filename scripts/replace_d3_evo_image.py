#!/usr/bin/env python3
"""
replace_d3_evo_image.py

Replace exactly ONE image/subimage with another image/subimage inside the
D-3 25th Color scene group associated with an evolution animation ID.

Example: Holydramon evolution 410, replace Angewomon image 338 with Wormmon 487:

    python replace_d3_evo_image.py \
        D3.bin 410 338 487 D3_out.bin

In-place:

    python replace_d3_evo_image.py \
        D3.bin 410 338 487 D3.bin

Preview only:

    python replace_d3_evo_image.py \
        D3.bin 410 338 487 D3_out.bin --dry-run

Important behavior
------------------
- Replaces ONLY the exact source image you specify.
- It does NOT automatically replace neighboring images such as 339/340/341.
- If the exact same source image is referenced multiple times inside the
  selected evolution scene, every exact occurrence is changed.  This is
  necessary for cases such as animation 410, where group 79 contains two
  meaningful image-338 references.
- It changes animation-command references only.
- It NEVER edits the sprite package itself, so the original image remains
  unchanged for normal appearances elsewhere.
- Animation record sizes and offset tables are never changed.

Currently established late-evolution scene mapping
--------------------------------------------------
The D3 late-evolution scene-family sequence is:

    evo_animation_id 409 -> group 77
    evo_animation_id 410 -> group 79
    evo_animation_id 411 -> group 81
    evo_animation_id 412 -> group 83
    evo_animation_id 413 -> group 85

For another animation ID, this program refuses to guess.  Supply a known group:

    python replace_d3_evo_image.py \
        D3.bin 500 123 456 D3_out.bin --group 100

Subimages
---------
Default source/replacement subimage is 0.

For an explicit subimage:

    --source-subimage N
    --replacement-subimage N

If a source reference uses a compact/default-subimage opcode, replacing it with
a nonzero subimage would require changing the command layout, so the program
refuses that edit.

D3 layout used
--------------
Animation archive:     0x001B3000
Sprite package:        0x001EF000
Animation offsets:     archive section 13
Animation payload:     archive section 14
Animation groups:      archive section 18
Groups:                67 (animation_id, flag) pairs each
"""

import argparse
import os
import struct
import tempfile
from collections import defaultdict
from pathlib import Path

GP_HEADER = b"GP-SPIF-HEADER"

ARCHIVE_BASE = 0x001B3000
ARCHIVE_SIZE = 0x00038BCC
SECTION_COUNT = 21

ANIMATION_OFFSET_SECTION = 13
ANIMATION_PAYLOAD_SECTION = 14
GROUP_SECTION = 18
GROUP_PAIR_COUNT = 67
EMPTY_ID = 0xFFFF

SPRITE_PACKAGE_BASE = 0x001EF000

DEFAULT_SUBIMAGE_OPCODES = {
    0x8001,
    0x8201,
    0x8601,
    0xC201,
}
EXPLICIT_SUBIMAGE_OPCODES = {
    0xA001,
    0xA201,
    0xC001,
    0xE001,
}
ALL_IMAGE_OPCODES = DEFAULT_SUBIMAGE_OPCODES | EXPLICIT_SUBIMAGE_OPCODES

# Current experimentally/structurally established late-evolution family.
KNOWN_EVO_SCENE_GROUPS = {
    409: 77,
    410: 79,
    411: 81,
    412: 83,
    413: 85,
}


def le16(data, off):
    return struct.unpack_from("<H", data, off)[0]


def le32(data, off):
    return struct.unpack_from("<I", data, off)[0]


def read_words(data, start, end):
    if end < start or ((end - start) % 2):
        raise RuntimeError(
            f"Invalid uint16 range 0x{start:X}..0x{end:X}"
        )

    count = (end - start) // 2

    if count == 0:
        return []

    return list(
        struct.unpack_from(
            "<" + "H" * count,
            data,
            start,
        )
    )


# ----------------------------------------------------------------------
# Sprite package
# ----------------------------------------------------------------------

def parse_sprite_package(data):
    """
    Only used for validating image/subimage indexes.
    This function never writes to the sprite package.
    """
    base = SPRITE_PACKAGE_BASE

    if base + 16 > len(data):
        raise RuntimeError(
            "D3 sprite package lies outside the BIN"
        )

    image_defs_rel = le32(data, base)
    sprite_defs_rel = le32(data, base + 4)
    palettes_rel = le32(data, base + 8)
    chars_rel = le32(data, base + 12)

    if not (
        0
        < image_defs_rel
        < sprite_defs_rel
        < palettes_rel
        < chars_rel
        <= len(data) - base
    ):
        raise RuntimeError(
            "Invalid D3 sprite package header at 0x001EF000"
        )

    if (
        (sprite_defs_rel - image_defs_rel) % 6
        or (palettes_rel - sprite_defs_rel) % 8
    ):
        raise RuntimeError(
            "Invalid D3 sprite-package table sizes"
        )

    num_images = (
        sprite_defs_rel - image_defs_rel
    ) // 6

    num_sprites = (
        palettes_rel - sprite_defs_rel
    ) // 8

    def image_def(index):
        off = (
            base
            + image_defs_rel
            + index * 6
        )

        return {
            "sprite_start": le16(data, off),
            "width": data[off + 2],
            "height": data[off + 3],
            "palette_start": le16(data, off + 4),
        }

    def subimage_count(index):
        if not 0 <= index < num_images:
            raise RuntimeError(
                f"image index {index} is outside "
                f"0..{num_images - 1}"
            )

        current = image_def(index)
        sprites_per_subimage = (
            current["width"]
            * current["height"]
        )

        if sprites_per_subimage <= 0:
            raise RuntimeError(
                f"image {index} has an invalid "
                "zero-size sprite grid"
            )

        if index + 1 < num_images:
            next_start = image_def(
                index + 1
            )["sprite_start"]
        else:
            next_start = num_sprites

        allocated_sprites = (
            next_start
            - current["sprite_start"]
        )

        if (
            allocated_sprites < 0
            or allocated_sprites
            % sprites_per_subimage
        ):
            raise RuntimeError(
                f"image {index} has an invalid "
                "sprite/subimage allocation"
            )

        return (
            allocated_sprites
            // sprites_per_subimage
        )

    return num_images, subimage_count


# ----------------------------------------------------------------------
# Animation archive
# ----------------------------------------------------------------------

def parse_sections(data):
    if (
        ARCHIVE_BASE
        + ARCHIVE_SIZE
        > len(data)
    ):
        raise RuntimeError(
            "D3 animation archive lies outside the BIN"
        )

    word_offsets = [
        le32(
            data,
            ARCHIVE_BASE + i * 4,
        )
        for i in range(SECTION_COUNT)
    ]

    if (
        word_offsets[0]
        != SECTION_COUNT * 2
    ):
        raise RuntimeError(
            "Unexpected D3 animation archive header"
        )

    if any(
        word_offsets[i]
        > word_offsets[i + 1]
        for i in range(
            len(word_offsets) - 1
        )
    ):
        raise RuntimeError(
            "Animation archive section offsets "
            "are not monotonic"
        )

    sections = []

    for i, word_off in enumerate(
        word_offsets
    ):
        start = (
            ARCHIVE_BASE
            + word_off * 2
        )

        if i + 1 < len(word_offsets):
            end = (
                ARCHIVE_BASE
                + word_offsets[i + 1] * 2
            )
        else:
            end = (
                ARCHIVE_BASE
                + ARCHIVE_SIZE
            )

        sections.append({
            "index": i,
            "start": start,
            "end": end,
            "size": end - start,
        })

    return sections


def parse_animation_records(
    data,
    sections,
):
    offset_sec = sections[
        ANIMATION_OFFSET_SECTION
    ]
    payload_sec = sections[
        ANIMATION_PAYLOAD_SECTION
    ]

    offsets = read_words(
        data,
        offset_sec["start"],
        offset_sec["end"],
    )

    if len(offsets) < 2:
        raise RuntimeError(
            "Animation offset table is too small"
        )

    if any(
        offsets[i] > offsets[i + 1]
        for i in range(
            len(offsets) - 1
        )
    ):
        raise RuntimeError(
            "Animation record offsets are "
            "not monotonic"
        )

    if (
        offsets[-1] * 4
        != payload_sec["size"]
    ):
        raise RuntimeError(
            "Animation payload/table size "
            "validation failed"
        )

    records = []

    for animation_id in range(
        len(offsets) - 1
    ):
        start = (
            payload_sec["start"]
            + offsets[animation_id] * 4
        )
        end = (
            payload_sec["start"]
            + offsets[
                animation_id + 1
            ] * 4
        )

        records.append({
            "animation_id": animation_id,
            "start": start,
            "end": end,
            "words": read_words(
                data,
                start,
                end,
            ),
        })

    return records


def parse_groups(
    data,
    sections,
):
    sec = sections[GROUP_SECTION]

    if sec["size"] % 4:
        raise RuntimeError(
            "Animation group section size "
            "is invalid"
        )

    pair_count = sec["size"] // 4

    if pair_count % GROUP_PAIR_COUNT:
        raise RuntimeError(
            f"Group pair count {pair_count} "
            f"is not divisible by "
            f"{GROUP_PAIR_COUNT}"
        )

    groups = []

    for group_index in range(
        pair_count // GROUP_PAIR_COUNT
    ):
        start = (
            sec["start"]
            + group_index
            * GROUP_PAIR_COUNT
            * 4
        )

        pairs = []

        for slot in range(
            GROUP_PAIR_COUNT
        ):
            off = start + slot * 4

            pairs.append({
                "slot": slot,
                "animation_id": le16(
                    data,
                    off,
                ),
                "flag": le16(
                    data,
                    off + 2,
                ),
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
            animation_id = pair[
                "animation_id"
            ]

            if animation_id == EMPTY_ID:
                continue

            membership[
                animation_id
            ].append({
                "group_index": group[
                    "group_index"
                ],
                "slot": pair["slot"],
                "flag": pair["flag"],
            })

    return membership


# ----------------------------------------------------------------------
# Direct image-reference decoding
# ----------------------------------------------------------------------

def all_image_refs(
    record,
    num_images,
    subimage_count,
):
    """
    Conservatively decode direct physical image references.

    Default-subimage forms:
        8001 IMAGE
        8201 IMAGE ...
        8601 IMAGE ...
        C201 IMAGE ...

    Explicit-subimage forms:
        A001 IMAGE SUBIMAGE
        A201 IMAGE SUBIMAGE ...
        C001 IMAGE SUBIMAGE
        E001 IMAGE SUBIMAGE ...

    For D3, a parameter after 8201 is NOT treated as a subimage selector.
    """
    words = record["words"]
    refs = []

    for i in range(
        len(words) - 1
    ):
        opcode = words[i]

        if opcode not in ALL_IMAGE_OPCODES:
            continue

        image_index = words[i + 1]

        if not (
            0
            <= image_index
            < num_images
        ):
            continue

        if (
            opcode
            in DEFAULT_SUBIMAGE_OPCODES
        ):
            refs.append({
                "kind": "default",
                "opcode": opcode,
                "opcode_word_index": i,
                "image_word_index": i + 1,
                "subimage_word_index": None,
                "image_index": image_index,
                "subimage_index": 0,
            })
            continue

        if i + 2 >= len(words):
            continue

        selector = words[i + 2]

        try:
            count = subimage_count(
                image_index
            )
        except Exception:
            continue

        if 0 <= selector < count:
            refs.append({
                "kind": "explicit",
                "opcode": opcode,
                "opcode_word_index": i,
                "image_word_index": i + 1,
                "subimage_word_index": i + 2,
                "image_index": image_index,
                "subimage_index": selector,
            })

    return refs


def find_exact_matches(
    group,
    records,
    num_images,
    subimage_count,
    source_image,
    source_subimage,
):
    matches = []

    for pair in group["pairs"]:
        animation_id = pair[
            "animation_id"
        ]

        if (
            animation_id == EMPTY_ID
            or not 0 <= animation_id < len(
                records
            )
        ):
            continue

        record = records[
            animation_id
        ]

        for ref in all_image_refs(
            record,
            num_images,
            subimage_count,
        ):
            if (
                ref["image_index"]
                == source_image
                and
                ref["subimage_index"]
                == source_subimage
            ):
                matches.append({
                    "group_index": group[
                        "group_index"
                    ],
                    "slot": pair["slot"],
                    "animation_id": animation_id,
                    "record": record,
                    "ref": ref,
                })

    return matches


# ----------------------------------------------------------------------
# Patch
# ----------------------------------------------------------------------

def patch_matches(
    data,
    matches,
    replacement_image,
    replacement_subimage,
):
    changes = []

    for match in matches:
        record = match["record"]
        ref = match["ref"]

        image_off = (
            record["start"]
            + ref["image_word_index"] * 2
        )

        old_image = le16(
            data,
            image_off,
        )

        if old_image != ref[
            "image_index"
        ]:
            raise RuntimeError(
                f"Internal image validation failed "
                f"at 0x{image_off:08X}"
            )

        struct.pack_into(
            "<H",
            data,
            image_off,
            replacement_image,
        )

        changes.append({
            "group": match[
                "group_index"
            ],
            "slot": match["slot"],
            "record": match[
                "animation_id"
            ],
            "opcode": ref["opcode"],
            "offset": image_off,
            "field": "image",
            "old": old_image,
            "new": replacement_image,
        })

        if ref["kind"] == "explicit":
            sub_off = (
                record["start"]
                + ref[
                    "subimage_word_index"
                ] * 2
            )

            old_sub = le16(
                data,
                sub_off,
            )

            if old_sub != ref[
                "subimage_index"
            ]:
                raise RuntimeError(
                    f"Internal subimage validation "
                    f"failed at 0x{sub_off:08X}"
                )

            if old_sub != replacement_subimage:
                struct.pack_into(
                    "<H",
                    data,
                    sub_off,
                    replacement_subimage,
                )

                changes.append({
                    "group": match[
                        "group_index"
                    ],
                    "slot": match["slot"],
                    "record": match[
                        "animation_id"
                    ],
                    "opcode": ref["opcode"],
                    "offset": sub_off,
                    "field": "subimage",
                    "old": old_sub,
                    "new": replacement_subimage,
                })

        elif replacement_subimage != 0:
            raise RuntimeError(
                "The source uses a compact/default-subimage "
                "command. A nonzero replacement subimage "
                "would require changing the animation "
                "record layout, so this edit is refused."
            )

    return changes


def safe_write(
    src,
    dst,
    data,
):
    src = Path(src)
    dst = Path(dst)

    dst.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if src.resolve() != dst.resolve():
        dst.write_bytes(data)

        if (
            dst.stat().st_size
            != len(data)
        ):
            raise RuntimeError(
                "Output BIN size validation failed"
            )

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

        if (
            temp_path.stat().st_size
            != len(data)
        ):
            raise RuntimeError(
                "Temporary BIN size "
                "validation failed"
            )

        os.replace(
            temp_path,
            dst,
        )

    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def main():
    ap = argparse.ArgumentParser(
        description=(
            "Replace one exact physical image reference "
            "inside a D3 evolution scene."
        )
    )

    ap.add_argument(
        "input_bin",
    )
    ap.add_argument(
        "evo_animation_id",
        type=int,
    )
    ap.add_argument(
        "source_image",
        type=int,
    )
    ap.add_argument(
        "replacement_image",
        type=int,
    )
    ap.add_argument(
        "output_bin",
    )

    ap.add_argument(
        "--source-subimage",
        type=int,
        default=0,
    )
    ap.add_argument(
        "--replacement-subimage",
        type=int,
        default=0,
    )
    ap.add_argument(
        "--group",
        type=int,
        default=None,
        help=(
            "Explicit scene-group override. Required "
            "for evo IDs outside the currently mapped "
            "409..413 late-evolution family."
        ),
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
    )

    args = ap.parse_args()

    src = Path(args.input_bin)
    dst = Path(args.output_bin)

    original = src.read_bytes()
    data = bytearray(original)

    if not data.startswith(GP_HEADER):
        raise RuntimeError(
            "Input does not look like a compatible "
            "D3 GP-SPIF BIN"
        )

    num_images, subimage_count = (
        parse_sprite_package(data)
    )

    if not (
        0
        <= args.source_image
        < num_images
    ):
        raise RuntimeError(
            f"source image {args.source_image} "
            f"is outside 0..{num_images - 1}"
        )

    if not (
        0
        <= args.replacement_image
        < num_images
    ):
        raise RuntimeError(
            f"replacement image "
            f"{args.replacement_image} "
            f"is outside 0..{num_images - 1}"
        )

    source_sub_count = subimage_count(
        args.source_image
    )
    replacement_sub_count = (
        subimage_count(
            args.replacement_image
        )
    )

    if not (
        0
        <= args.source_subimage
        < source_sub_count
    ):
        raise RuntimeError(
            f"source image {args.source_image} "
            f"has {source_sub_count} subimage(s); "
            f"subimage {args.source_subimage} "
            "is invalid"
        )

    if not (
        0
        <= args.replacement_subimage
        < replacement_sub_count
    ):
        raise RuntimeError(
            f"replacement image "
            f"{args.replacement_image} "
            f"has {replacement_sub_count} "
            f"subimage(s); subimage "
            f"{args.replacement_subimage} "
            "is invalid"
        )

    sections = parse_sections(data)
    records = parse_animation_records(
        data,
        sections,
    )
    groups = parse_groups(
        data,
        sections,
    )
    membership = build_membership(
        groups
    )

    if args.group is not None:
        scene_group = args.group
        selection_reason = (
            "explicit --group override"
        )
    else:
        if (
            args.evo_animation_id
            not in KNOWN_EVO_SCENE_GROUPS
        ):
            known = ", ".join(
                str(x)
                for x in sorted(
                    KNOWN_EVO_SCENE_GROUPS
                )
            )

            raise RuntimeError(
                f"No safely-established automatic "
                f"scene-group mapping is encoded for "
                f"evo_animation_id "
                f"{args.evo_animation_id}. "
                f"Known automatic IDs: {known}. "
                "Use --group only if you have already "
                "identified the correct scene group."
            )

        scene_group = (
            KNOWN_EVO_SCENE_GROUPS[
                args.evo_animation_id
            ]
        )
        selection_reason = (
            "known late-evolution "
            "scene-family mapping"
        )

    if not (
        0
        <= scene_group
        < len(groups)
    ):
        raise RuntimeError(
            f"group {scene_group} is outside "
            f"0..{len(groups) - 1}"
        )

    group = groups[scene_group]

    matches = find_exact_matches(
        group,
        records,
        num_images,
        subimage_count,
        args.source_image,
        args.source_subimage,
    )

    print(
        f"evo_animation_id: "
        f"{args.evo_animation_id}"
    )

    controller_membership = (
        membership.get(
            args.evo_animation_id,
            [],
        )
    )

    if controller_membership:
        where = ", ".join(
            (
                f"group {m['group_index']} "
                f"slot {m['slot']} "
                f"flag {m['flag']}"
            )
            for m in controller_membership
        )
    else:
        where = "none"

    print(
        f"controller membership: {where}"
    )
    print(
        f"selected scene group: "
        f"{scene_group} "
        f"({selection_reason})"
    )
    print(
        f"source:      "
        f"{args.source_image}_"
        f"{args.source_subimage}"
    )
    print(
        f"replacement: "
        f"{args.replacement_image}_"
        f"{args.replacement_subimage}"
    )
    print()

    if not matches:
        raise RuntimeError(
            f"No exact reference to image "
            f"{args.source_image}, subimage "
            f"{args.source_subimage} was found "
            f"in scene group {scene_group}."
        )

    print(
        f"Exact source occurrence(s): "
        f"{len(matches)}"
    )

    touched_records = sorted(
        {
            m["animation_id"]
            for m in matches
        }
    )

    for i, match in enumerate(
        matches,
        1,
    ):
        record = match["record"]
        ref = match["ref"]

        image_off = (
            record["start"]
            + ref["image_word_index"] * 2
        )

        print(
            f"  {i}. group "
            f"{match['group_index']} "
            f"slot {match['slot']} "
            f"record "
            f"{match['animation_id']} "
            f"opcode 0x"
            f"{ref['opcode']:04X} "
            f"image word @ "
            f"0x{image_off:08X}"
        )

    for animation_id in touched_records:
        other = [
            m
            for m in membership.get(
                animation_id,
                [],
            )
            if m["group_index"]
            != scene_group
        ]

        if other:
            where = ", ".join(
                (
                    f"group "
                    f"{m['group_index']} "
                    f"slot {m['slot']}"
                )
                for m in other
            )

            print(
                f"[WARN] record "
                f"{animation_id} is also "
                f"used by {where}. "
                "Those callers would see "
                "the same image-reference "
                "change."
            )

    patched = bytearray(data)

    changes = patch_matches(
        patched,
        matches,
        args.replacement_image,
        args.replacement_subimage,
    )

    # Hard safety property:
    # sprite package must remain byte-for-byte identical.
    if (
        patched[
            SPRITE_PACKAGE_BASE:
        ]
        != original[
            SPRITE_PACKAGE_BASE:
        ]
    ):
        raise RuntimeError(
            "Safety check failed: sprite-package "
            "bytes would change. Output was NOT "
            "written."
        )

    # Every changed byte must lie within the animation payload.
    payload_sec = sections[
        ANIMATION_PAYLOAD_SECTION
    ]

    diff_offsets = [
        i
        for i, (a, b)
        in enumerate(
            zip(
                original,
                patched,
            )
        )
        if a != b
    ]

    outside = [
        off
        for off in diff_offsets
        if not (
            payload_sec["start"]
            <= off
            < payload_sec["end"]
        )
    ]

    if outside:
        raise RuntimeError(
            "Safety check failed: byte(s) outside "
            "the animation payload would change: "
            + ", ".join(
                f"0x{x:08X}"
                for x in outside[:20]
            )
        )

    print()
    print(
        "Planned changes:"
        if args.dry_run
        else
        "Changes:"
    )

    for ch in changes:
        print(
            f"  group {ch['group']} "
            f"slot {ch['slot']} "
            f"record {ch['record']} "
            f"@ 0x{ch['offset']:08X}: "
            f"{ch['field']} "
            f"{ch['old']} -> "
            f"{ch['new']}"
        )

    if args.dry_run:
        print()
        print(
            "[DRY RUN] No BIN was written."
        )
        print(
            "[SAFE] Sprite package was "
            "not modified."
        )
        return

    safe_write(
        src,
        dst,
        patched,
    )

    print()
    print(
        f"[DONE] Wrote {dst}"
    )
    print(
        f"[DONE] Patched "
        f"{len(matches)} exact source "
        "occurrence(s)."
    )
    print(
        "[SAFE] The actual sprite "
        "package was not modified."
    )


if __name__ == "__main__":
    main()
