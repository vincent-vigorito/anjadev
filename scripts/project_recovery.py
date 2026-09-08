"""Snapshot verificati del wiki; restore su destinazione nuova, senza sovrascritture."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _files(root):
    wiki = root / '.anjawiki/wiki'
    if not wiki.resolve().is_relative_to(root):
        raise ValueError('wiki escapes project')
    if any(p.is_symlink() for p in wiki.rglob('*')):
        raise ValueError('backup refuses symlinks in wiki')
    paths = [p for p in list(wiki.rglob('*.md')) + list(wiki.rglob('.gitignore')) if p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(wiki)]
    for name in ('.gitignore', '.anjaignore', '.anjawiki/meta.yaml', '.anjawiki/.schema-version', '.anjawiki/config.json', '.anjawiki/index-policy.json', 'AGENTS.src.md', 'SOUL.md', 'TOOLS.md'):
        path = root / name
        if path.is_file() and not path.is_symlink():
            paths.append(path)
    return sorted(paths)


def snapshot_project(root, reason='manual'):
    root = Path(root).resolve()
    directory = root / '.anjawiki/backups'
    if (root / '.anjawiki').is_symlink() or directory.is_symlink() or not directory.resolve().is_relative_to(root):
        raise ValueError('backup directory escapes project')
    directory.mkdir(parents=True, exist_ok=True)
    name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix='.preparing-', dir=directory) as temporary:
        stage = Path(temporary)
        metadata = {'version': 1, 'created': datetime.now(timezone.utc).isoformat(), 'reason': reason, 'files': {}}
        paths = _files(root)
        for path in paths:
            data = path.read_bytes()
            rel = str(path.relative_to(root))
            target = stage / 'files' / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            metadata['files'][rel] = digest(data)
        database = root / '.anjawiki/code-index.db'
        if database.is_file():
            db = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
            try:
                db.execute('BEGIN')
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                metadata['index_manifest'] = {name: db.execute('SELECT * FROM ' + name).fetchall()
                                              for name in ('meta', 'indexed_files') if name in tables}
            finally:
                db.close()
        if _files(root) != paths or any(digest(p.read_bytes()) != metadata['files'][str(p.relative_to(root))] for p in paths):
            raise RuntimeError('wiki changed during backup; retry')
        manifest = json.dumps(metadata, ensure_ascii=False, indent=2).encode()
        (stage / 'manifest.json').write_bytes(manifest)
        (stage / 'manifest.sha256').write_text(digest(manifest))
        for path in stage.rglob('*'):
            if path.is_file():
                with path.open('rb') as stream:
                    os.fsync(stream.fileno())
        target = directory / name
        os.rename(stage, target)
    return str(target)


def verify(backup):
    backup = Path(backup).resolve()
    raw = (backup / 'manifest.json').read_bytes()
    if digest(raw) != (backup / 'manifest.sha256').read_text().strip():
        raise ValueError('backup manifest checksum mismatch')
    metadata = json.loads(raw)
    if metadata.get('version') != 1:
        raise ValueError('unsupported backup version')
    for rel, checksum in metadata['files'].items():
        path = backup / 'files' / rel
        if Path(rel).is_absolute() or '..' in Path(rel).parts or path.is_symlink() or not path.resolve().is_relative_to(backup / 'files'):
            raise ValueError('unsafe backup path')
        if digest(path.read_bytes()) != checksum:
            raise ValueError('backup content checksum mismatch: ' + rel)
    return metadata


def restore(backup, destination):
    backup, destination = Path(backup).resolve(), Path(destination).absolute()
    metadata = verify(backup)
    if destination.exists() or destination.is_symlink():
        raise ValueError('restore requires a new, nonexistent destination')
    with tempfile.TemporaryDirectory(prefix='.anja-restore-', dir=destination.parent) as temporary:
        stage = Path(temporary)
        for rel, checksum in metadata['files'].items():
            data = (backup / 'files' / rel).read_bytes()
            if digest(data) != checksum:
                raise ValueError('backup changed during restore')
            path = stage / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        directory = stage / '.anjawiki'
        directory.mkdir(exist_ok=True)
        (directory / 'restored-index-manifest.json').write_text(json.dumps(metadata.get('index_manifest', {})))
        # mkdir esclusivo riserva la destinazione; nessuna directory preesistente viene sostituita.
        destination.mkdir()
        try:
            for item in stage.iterdir():
                os.rename(item, destination / item.name)
        except Exception:
            raise RuntimeError('restore interrupted; partial destination retained for inspection') from None
    return {'destination': str(destination), 'files': len(metadata['files']), 'index': 'rebuild_required'}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    snap = sub.add_parser('backup')
    snap.add_argument('root', type=Path)
    check = sub.add_parser('verify')
    check.add_argument('backup', type=Path)
    res = sub.add_parser('restore')
    res.add_argument('backup', type=Path)
    res.add_argument('destination', type=Path)
    args = parser.parse_args()
    try:
        result = snapshot_project(args.root) if args.command == 'backup' else (verify(args.backup) if args.command == 'verify' else restore(args.backup, args.destination))
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({'error': str(exc), 'code': 'recovery_failed'}))
        raise SystemExit(1) from None
