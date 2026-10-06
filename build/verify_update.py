"""Verify every changed file can be reconstructed from every supported installation."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import zipfile

from make_update import installed_states, read_ref, ref_hash, validate_manifest
from vgdelta import apply_delta


def verify(base: Path, previous: list[Path], update: Path) -> dict:
    hashes = {}
    with ExitStack() as stack:
        states = installed_states(stack, base, previous, hashes)
        archive = stack.enter_context(zipfile.ZipFile(update))
        names = [n for n in archive.namelist() if n.count('/') == 1 and n.endswith('/manifest.json')]
        if len(names) != 1:
            raise ValueError("Invalid update manifest")
        prefix = names[0].rsplit('/', 1)[0] + '/'
        manifest = json.loads(archive.read(names[0]))
        validate_manifest(manifest)
        checked = 0
        for state in states:
            for entry in manifest['required']:
                if entry['path'] not in state or ref_hash(state[entry['path']], hashes) != entry['sha256']:
                    raise ValueError(f"Required runtime mismatch: {entry['path']}")
            for entry in manifest['files']:
                old = state.get(entry['path'])
                before = ref_hash(old, hashes) if old is not None else None
                if before == entry['sha256']:
                    continue
                delta = next((d for d in entry.get('deltas', []) if d['base_sha256'] == before), None)
                if delta:
                    patch = archive.read(prefix + delta['patch'])
                    if hashlib.sha256(patch).hexdigest() != delta['sha256']:
                        raise ValueError('Damaged delta')
                    data = apply_delta(read_ref(old), patch)
                    actual = hashlib.sha256(data).hexdigest()
                else:
                    with archive.open(prefix + 'payload/' + entry['path']) as stream:
                        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
                if actual != entry['sha256']:
                    raise ValueError(f"Reconstructed file mismatch: {entry['path']}")
                checked += 1
        return {'supported_installations': len(states), 'reconstructed_files': checked,
                'runtime_files_per_installation': len(manifest['required'])}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base', type=Path, required=True)
    ap.add_argument('--previous', type=Path, action='append', default=[])
    ap.add_argument('--update', type=Path, required=True)
    args = ap.parse_args()
    result = verify(args.base, args.previous, args.update)
    args.update.with_suffix('.validation.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('UPDATE VERIFIED: ' + json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
