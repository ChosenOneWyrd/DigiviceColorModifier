"""Resolve D3 names through resource 4 / entry 0 in the current BIN.

The root directory stays at 0x140000 in this D3 format. The name resource's
address is read on each call; neither its filename nor an old address is used.
"""
import struct

ROOT = 0x140000
NAME_PATH = (4, 0)


def get_entry_view(buf, root=ROOT, path=NAME_PATH):
    base, limit = root, len(buf)
    if not path:
        raise RuntimeError('Empty archive path')
    for index in path:
        if (base < 0 or base + 4 > limit
                or struct.unpack_from('<H', buf, base)[0] != 0x3232):
            raise RuntimeError(f'Archive not found at 0x{base:X}')
        count = struct.unpack_from('<H', buf, base + 2)[0]
        table_end = base + 4 + count * 16
        if not 1 <= count <= 10000 or table_end > limit or not 0 <= index < count:
            raise RuntimeError(f'Invalid archive directory at 0x{base:X}')
        flags, relative, stored, decoded = struct.unpack_from(
            '<4I', buf, base + 4 + index * 16)
        if flags != 0 or (decoded and decoded != stored):
            raise RuntimeError('Compressed or flagged name resources are not supported')
        start = base + relative
        end = start + stored
        if not stored or start < table_end or end > limit:
            raise RuntimeError(f'Name resource lies outside its parent archive at 0x{base:X}')
        base, limit = start, end
    return buf[base:limit], base


def parse_text_archive(view):
    if len(view) < 4:
        raise RuntimeError('Truncated name table')
    count = struct.unpack_from('<H', view, 0)[0]
    table_end = 2 + 2 * count
    if not 1 <= count <= 20000 or table_end > len(view):
        raise RuntimeError('Invalid name offset table')
    offsets = list(struct.unpack_from('<' + 'H' * count, view, 2))
    if (offsets != sorted(offsets)
            or any(word * 2 < table_end or word * 2 + 2 > len(view) for word in offsets)):
        raise RuntimeError('Invalid name string offsets')
    return offsets


def locate_names(buf):
    view, base = get_entry_view(buf)
    return view, base, parse_text_archive(view)
