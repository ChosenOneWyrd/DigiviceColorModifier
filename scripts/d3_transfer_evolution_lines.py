#!/usr/bin/env python3
"""Persistent, map-only D-3 battle-selector transfer settings and CLI."""
from __future__ import annotations
import argparse
import hashlib
import json
import struct
from pathlib import Path

from d3_paging_codegen import assemble
from d3_paging_legacy import CODE as LEGACY_CODE, HOOKS as LEGACY_HOOKS

START, CODE_START, END = 0xAD060, 0xAD0A0, 0xAE000
MAGIC = b'D3PAGES1'
STOCK = {0x22C84:(0xF045,0x71FC),0x22E88:(0x98E4,0xD818),0x22EEE:(0x96E4,0x0908,7)}
SINGLE_STOCK={0x22A3C:(0x4841,0xAE36),0x22ACC:(0x4841,0xAE34)}
STOCK.update(SINGLE_STOCK)
CALLBACK_HASH = '929a04b562f887d537fc177242720a068a2a6c526f5f95927791825cd9b59ad2'


def packed(words):
    return struct.pack('<'+'H'*len(words),*words)


def hooks_match(data, hooks):
    return all(data[o:o+len(w)*2]==packed(w) for o,w in hooks.items())


def is_probe_c(data):
    return hooks_match(data,LEGACY_HOOKS) and data[0xACED8:0xACED8+len(LEGACY_CODE)]==LEGACY_CODE


def strip_probe_c(data):
    if not is_probe_c(data):
        raise ValueError('Probe C code was changed; automatic migration refused.')
    for offset,words in STOCK.items():data[offset:offset+len(words)*2]=packed(words)
    data[0xACED8:0xACED8+len(LEGACY_CODE)]=bytes(len(LEGACY_CODE))


def expected_hooks(labels):
    hooks = {0x22C84:(0xF045,labels['paging']&65535),
            0x22E88:(0xF045,labels['old_flags']&65535),
            0x22EEE:(0xFE85,labels['old_label']&65535,0x0040)}
    hooks.update(SINGLE_STOCK)
    if 'single_right' in labels:
        hooks.update({0x22A3C:(0xFE85,labels['single_right']&65535),0x22ACC:(0xFE85,labels['single_left']&65535)})
    return hooks


def validate_mappings(mappings):
    if any(type(a) is not int or type(b) is not int or a not in range(7) or b not in range(7) or a==b for a,b in mappings.items()):
        raise ValueError('Source/donor lines must be different line IDs between 0 and 6.')


def read_state(data):
    """Read and authenticate owned code without deriving lists from edited records."""
    import d3_additional_partners as additional
    if additional.detected(data):return dict(additional.read_state(data)['mappings'])
    if is_probe_c(data):return {1:3}
    if data[START:START+8]!=MAGIC:
        if not hooks_match(data,STOCK):
            raise ValueError('Unrecognized selector callback patch. Use mod1, confirmed probe C, or a BIN saved by this tool.')
        return {}
    version,code_len,json_len,reserved=struct.unpack_from('<4H',data,START+8)
    stop=CODE_START+code_len+json_len
    if version!=1 or reserved or code_len%2 or stop>END or code_len<100:
        raise ValueError('Invalid transfer metadata header.')
    if any(data[START+48:CODE_START]) or any(data[stop:END]):
        raise ValueError('Unrecognized data in reserved transfer storage.')
    payload=bytes(data[CODE_START:stop])
    if hashlib.sha256(payload).digest()!=data[START+16:START+48]:
        raise ValueError('Transfer code/settings checksum mismatch; no changes made.')
    try:
        meta=json.loads(payload[code_len:]);mappings={int(k):v for k,v in meta['mappings'].items()}
        pages={int(k):v for k,v in meta['pages'].items()}
        validate_mappings(mappings)
        if set(mappings)!=set(pages) or not mappings:raise ValueError('Invalid transfer page map.')
        for host,donor in pages.values():
            if not 2<=len(host)<=9 or not 2<=len(donor)<=8 or set(host)&set(donor):raise ValueError('Invalid page choices.')
            for page in (host,donor):
                if len(set(page))!=len(page) or any(type(i) is not int or not 0<=i<38 for i in page):raise ValueError('Invalid physical record.')
        code,labels=assemble(pages)
        if code!=payload[:code_len] or not hooks_match(data,expected_hooks(labels)):
            raise ValueError('Transfer machine code does not match its settings.')
    except (KeyError,TypeError,UnicodeError,json.JSONDecodeError) as ex:
        raise ValueError('Invalid transfer settings.') from ex
    return mappings


def check_layout(data):
    import d3_evolution_core as core
    errors=core.validate_layout(data)
    if errors:raise ValueError('\n'.join(errors))
    if struct.unpack_from('<I',data,0x30)[0]!=0xFFFF:
        raise ValueError('Unsupported firmware boot-checksum endpoint.')
    value=sum(data[0x80:0xE000])&0xFFFFFFFF
    if struct.unpack_from('<II',data,0x20)!=(value,value):
        raise ValueError('The input boot checksum is invalid; use a working BIN.')
    callback=bytearray(data[0x2279E:0x233FA])
    for off,words in STOCK.items():callback[off-0x2279E:off-0x2279E+len(words)*2]=packed(words)
    if hashlib.sha256(callback).hexdigest()!=CALLBACK_HASH:
        raise ValueError('The selector callback differs from the supported D-3 firmware.')


def derive_pages(data,mappings,lines=None):
    import d3_evolution_core as core
    validate_mappings(mappings)
    if lines is None:lines=core.read_source_lines(data)[0]
    records=core.read_partner_records(data)
    active={line:[i for i in values if records[i].stage>0] for line,values in lines.items()}
    pages={}
    for source,donor_line in sorted(mappings.items()):
        host=active[source]
        # Exclude the first non-baby donor and forms already on the host page.
        donor=[i for i in active[donor_line][1:] if i not in host]
        if not 2<=len(host)<=9 or not 2<=len(donor)<=8:
            raise ValueError(f'Line {source} → line {donor_line}: each page needs at least two choices (source {len(host)}, additional donor {len(donor)}). The stock menu disables navigation for a one-choice page. Change this transfer or remove it before saving these slot/Partner edits.')
        pages[source]=(host,donor)
    return pages


def install_into(data,mappings,lines=None):
    """Replace only authenticated transfer-owned code; caller commits atomically."""
    import d3_additional_partners as additional
    if additional.detected(data):
        state=additional.read_state(data)
        if lines is None:
            import d3_evolution_core as core
            lines=core.read_source_lines(data)[0]
        additional.install_into(data,state['entries'],mappings,lines,additional.merge_slots(lines,state['entries'],state.get('slots')) if state.get('slots') else None)
        return additional.read_state(data)['pages']
    validate_mappings(mappings)
    legacy=is_probe_c(data)
    previous=read_state(data)
    if legacy:strip_probe_c(data)
    check_layout(data)
    owned=data[START:START+8]==MAGIC
    if not owned and any(data[START:END]):
        raise ValueError('The transfer-code region is occupied by another modification.')
    pages=derive_pages(data,mappings,lines)
    code,labels=assemble(pages) if mappings else (b'',{})
    meta=json.dumps({'mappings':mappings,'pages':pages},sort_keys=True,separators=(',',':')).encode() if mappings else b''
    payload=code+meta
    if CODE_START+len(payload)>END:raise ValueError('Transfer code exceeds its reserved ROM space.')
    data[START:END]=bytes(END-START)
    if mappings:
        data[START:START+8]=MAGIC
        struct.pack_into('<4H',data,START+8,1,len(code),len(meta),0)
        data[START+16:START+48]=hashlib.sha256(payload).digest()
        data[CODE_START:CODE_START+len(payload)]=payload
    for off,words in (expected_hooks(labels) if mappings else STOCK).items():
        data[off:off+len(words)*2]=packed(words)
    if read_state(data)!=mappings:raise RuntimeError('Transfer writeback validation failed.')
    return pages


def update_bytes(original,source,donor):
    """donor=None removes only this source's transfer; others remain installed."""
    import d3_evolution_core as core
    mappings=read_state(original)
    if donor is None:mappings.pop(source,None)
    else:mappings[source]=donor
    validate_mappings(mappings)
    if type(source) is not int or source not in range(7):raise ValueError('Invalid source line.')
    import d3_additional_partners as additional
    if additional.detected(original):
        state=additional.read_state(original)
        return additional.update_bytes(original,state['entries'],mappings)
    work=bytearray(original)
    if is_probe_c(work):strip_probe_c(work)
    check_layout(work)
    # Install/reconcile v15 first; private function deliberately avoids refreshing
    # the old mapping before the requested replacement/removal is applied.
    lines,*_=core._synchronize_existing_bin_without_transfers(work)
    install_into(work,mappings,lines)
    if work[:0xE000]!=original[:0xE000]:raise RuntimeError('Unexpected startup-region change.')
    return bytes(work)


def save_transfer(path,source,donor,expected_digest=None):
    import d3_evolution_core as core
    path=Path(path);original=path.read_bytes()
    digest=hashlib.sha256(original).hexdigest()
    if expected_digest is not None and digest!=expected_digest:
        raise ValueError('The BIN changed since Refresh. Refresh to load the latest edits, then save again.')
    result=update_bytes(original,source,donor)
    if path.read_bytes()!=original:raise ValueError('The BIN changed while saving. No changes written; refresh and retry.')
    core.atomic_write(path,result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('bin',type=Path)
    p.add_argument('--source',type=int);g=p.add_mutually_exclusive_group();g.add_argument('--donor',type=int);g.add_argument('--remove',action='store_true')
    p.add_argument('--output',type=Path);p.add_argument('--show',action='store_true');args=p.parse_args()
    if args.show:
        print(json.dumps(read_state(args.bin.read_bytes()),indent=2));return
    if args.source is None or (args.donor is None and not args.remove):p.error('Specify --source and either --donor or --remove, or use --show.')
    if args.output and args.output.resolve()!=args.bin.resolve():
        from d3_evolution_core import atomic_write
        atomic_write(args.output,update_bytes(args.bin.read_bytes(),args.source,args.donor))
    else:save_transfer(args.bin,args.source,args.donor)
    print('Saved transfer settings. No .bak file generated.')
if __name__=='__main__':main()
