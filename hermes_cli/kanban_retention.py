"""Research evidence archive verification before disposable workspace removal."""
from __future__ import annotations
import hashlib
import json
import re
import zipfile
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def manifest_entries(workspace):
    root=Path(workspace).resolve()
    manifest=json.loads((root/'evidence-manifest.json').read_text())
    entries=manifest['entries']
    if not entries or not any(e.get('kind')=='raw_source' for e in entries):
        raise ValueError('complete raw-source evidence manifest required')
    paths=set()
    for entry in entries:
        name=entry['path']; path=(root/name).resolve()
        if name in paths or not path.is_relative_to(root) or path==root or not path.is_file():
            raise ValueError(f'invalid or missing evidence file: {name}')
        paths.add(name)
        if entry['sha256']!=digest(path): raise ValueError(f'evidence changed: {name}')
        if entry.get('kind')=='raw_source' and any(not entry.get(k) for k in ('url','publisher','published_at','retrieved_at')):
            raise ValueError(f'raw source lacks provenance: {name}')
    inventory = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()
                 and p.name not in {'evidence-manifest.json', 'retention-required.json', 'retention-verified.json'}}
    if inventory != paths:
        raise ValueError('evidence manifest does not cover the complete workspace inventory')
    cited=set()
    for source_list in root.rglob('sources.md'):
        cited.update(re.findall(r'https?://[^\s)>]+',source_list.read_text()))
    if cited-{e.get('url') for e in entries if e.get('kind')=='raw_source'}:
        raise ValueError('cited sources missing raw evidence in manifest')
    return manifest


def verify_archive(archive, expected_hash):
    path=Path(archive)
    if digest(path)!=expected_hash: raise ValueError('retained archive hash mismatch')
    with zipfile.ZipFile(path) as z:
        if z.testzip(): raise ValueError('retained archive is corrupt')
        manifest=json.loads(z.read('evidence-manifest.json'))
        entries=manifest['entries']
        if not entries or not any(e.get('kind')=='raw_source' for e in entries):
            raise ValueError('archive has no raw-source manifest')
        names=[e['path'] for e in entries]
        if len(names)!=len(set(names)): raise ValueError('duplicate archive evidence identity')
        for entry in entries:
            if hashlib.sha256(z.read(entry['path'])).hexdigest()!=entry['sha256']:
                raise ValueError(f"archived evidence differs: {entry['path']}")
    return manifest


def archive_research(workspace, archive):
    root=Path(workspace).resolve(); dest=Path(archive).resolve()
    if dest.is_relative_to(root): raise ValueError('archive must survive workspace cleanup')
    manifest=manifest_entries(root)
    dest.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive creation never overwrites an existing evidence archive.
    with zipfile.ZipFile(dest,'x',compression=zipfile.ZIP_DEFLATED) as z:
        z.write(root/'evidence-manifest.json','evidence-manifest.json')
        for entry in manifest['entries']: z.write(root/entry['path'],entry['path'])
    sha=digest(dest);verify_archive(dest,sha)
    receipt={'archive':str(dest),'sha256':sha,'manifest_sha256':digest(root/'evidence-manifest.json')}
    (root/'retention-verified.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


def require_retention(workspace, *, research=False):
    root=Path(workspace)
    evidence = any((root/name).exists() for name in (
        'retention-required.json', 'retention-verified.json', 'evidence-manifest.json', 'gauntlet.json'))
    if not (research or evidence or any(root.rglob('sources.md'))):
        return
    receipt=json.loads((root/'retention-verified.json').read_text())
    archive=Path(receipt['archive']).resolve()
    if archive.is_relative_to(root.resolve()): raise ValueError('archive is inside disposable workspace')
    if receipt['manifest_sha256']!=digest(root/'evidence-manifest.json'):
        raise ValueError('manifest changed since archive verification')
    current=manifest_entries(root)
    if current!=verify_archive(archive,receipt['sha256']): raise ValueError('archive manifest differs')
