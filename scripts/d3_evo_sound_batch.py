"""Validated batch edits for the evolution sound GUI; no Qt dependency."""
from pathlib import Path
import hashlib
import os
import stat
import tempfile
import replace_d3_evo_sound as backend

def digest(data):return hashlib.sha256(data).hexdigest()

def load(path,names):
    data=Path(path).read_bytes()
    archive=backend.Archive(data)
    audio=backend.audio_offsets(data)
    if set(names)!=set(range(len(audio))):
        raise ValueError('Sound map and discovered audio catalog disagree.')
    return {'data':data,'archive':archive,'sha256':digest(data)}

def apply(data,edits,names):
    if not edits:raise ValueError('Select a replacement.')
    original=backend.Archive(data);offsets=set()
    # Resolve all jobs against one snapshot before changing anything.
    for e in edits:
        if e['source'] not in names or e['destination'] not in names:
            raise ValueError('A selected sound is absent from the sound map.')
        rows=[r for r in original.sounds(e['evo']) if r['occurrence']==e['occurrence']]
        if len(rows)!=1 or rows[0]['chunk']!=e['source']:
            raise ValueError('The selected source no longer matches this BIN. Refresh and select it again.')
        if rows[0]['offset'] in offsets:raise ValueError('Two edits target the same sound command, possibly through layout aliases.')
        offsets.add(rows[0]['offset'])
    out=bytes(data)
    for e in edits:
        out,_,_=backend.patch(out,e['evo'],e['source'],e['destination'],occurrence=e['occurrence'])
    allowed={p+d for p in offsets for d in (0,1)}
    changed=[i for i,(a,b) in enumerate(zip(data,out)) if a!=b]
    if len(out)!=len(data) or not set(changed)<=allowed:raise ValueError('Batch byte verification failed.')
    return out,changed

def save(path,snapshot,edits,names):
    """Save atomically in place with stale-file rejection; no backup file."""
    path=Path(path).resolve()
    current=path.read_bytes()
    if digest(current)!=snapshot['sha256']:raise ValueError('The BIN changed in another tab/program. Refresh this tab and select the replacement again.')
    output,changed=apply(current,edits,names)
    fd,tmp=tempfile.mkstemp(prefix='.evo_sound_',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(output);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,stat.S_IMODE(path.stat().st_mode))
        if path.read_bytes()!=current:raise ValueError('The BIN changed while preparing the save. Refresh and try again.')
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return {'data':output,'archive':backend.Archive(output),'sha256':digest(output),
            'changed':changed}
