"""Disponibilità dei transcript e copia integrale opt-in, separata dal journal."""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from pathlib import Path

from anja.persistence import read_text, revision, write_text

MAX_BYTES = 100 * 1024 * 1024


def field(text, key):
    if not text.startswith('---\n'):
        return ''
    block = text.split('\n---', 1)[0]
    match = re.search(r'^' + re.escape(key) + r':\s*(.*)$', block, re.M)
    return match.group(1).strip().strip('\"\'') if match else ''


def availability(root, text):
    root = Path(root).resolve()
    archived = field(text, 'archived_transcript')
    original = field(text, 'transcript_path')
    if archived:
        path = root / archived
        if not path.is_symlink() and path.resolve().is_relative_to(root / '.anjawiki/transcripts') and path.is_file():
            checksum = field(text, 'archived_transcript_sha256')
            if checksum and hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
                return {'status': 'archive_corrupt', 'recoverable': False}
            return {'status': 'archived', 'recoverable': True, 'path': archived}
    if original:
        source = Path(original).expanduser()
        if not source.is_absolute():
            source = root / source
        if source.is_file():
            return {'status': 'external', 'recoverable': True, 'path': original}
        return {'status': 'missing', 'recoverable': False, 'path': original}
    return {'status': 'journal_only', 'recoverable': False}


def archive(root, session):
    root, session = Path(root).resolve(), Path(session).resolve()
    if os.environ.get('ANJA_ARCHIVE_TRANSCRIPTS', '0') != '1':
        return {'status': 'disabled'}
    if not session.is_relative_to(root / '.anjawiki/wiki/sessions'):
        raise ValueError('journal must stay inside project sessions')
    text = read_text(session)
    source = field(text, 'transcript_path')
    if not source:
        return {'status': 'journal_only'}
    source = Path(source).expanduser()
    if not source.is_absolute():
        source = root / source
    if not source.is_file():
        return {'status': 'missing', 'recoverable': False}
    with source.open('rb') as stream:
        before = os.fstat(stream.fileno())
        data = stream.read(MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        return {'status': 'source_changed', 'recoverable': False}
    if len(data) > MAX_BYTES:
        return {'status': 'too_large', 'recoverable': False}
    directory = root / '.anjawiki/transcripts'
    if (root / '.anjawiki').is_symlink() or directory.is_symlink() or not directory.resolve().is_relative_to(root):
        raise ValueError('transcript archive escapes project')
    directory.mkdir(parents=True, exist_ok=True)
    checksum = hashlib.sha256(data).hexdigest()
    path = directory / (checksum + '.jsonl')
    fd, temporary = tempfile.mkstemp(prefix='.transcript-', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != data:
                raise ValueError('archive content mismatch') from None
    finally:
        os.unlink(temporary)
    # I campi duplicati non sono ammessi; il journal resta protetto dalla revisione Q2.
    updated = re.sub(r'^archived_transcript(?:_sha256)?:.*\n', '', text, flags=re.M)
    updated = updated.replace('\n---\n', f'\narchived_transcript: {path.relative_to(root)}\narchived_transcript_sha256: {checksum}\n---\n', 1)
    write_text(session, updated, revision(text))
    return {'status': 'archived', 'path': str(path.relative_to(root))}


def diagnose(root):
    root = Path(root).resolve()
    sessions = root / '.anjawiki/wiki/sessions'
    entries = []
    for path in sessions.rglob('*.md'):
        if path.is_symlink() or not path.resolve().is_relative_to(sessions):
            continue
        info = availability(root, path.read_text(encoding='utf-8', errors='replace'))
        entries.append({'journal': str(path.relative_to(root)), **info})
    return {'sessions': entries, 'missing': sum(e['status'] in ('missing','archive_corrupt') for e in entries),
            'archive_enabled': os.environ.get('ANJA_ARCHIVE_TRANSCRIPTS', '0') == '1'}


def purge(root, days=30, apply=False):
    if days < 1:
        raise ValueError('retention days must be positive')
    root = Path(root).resolve()
    cutoff = time.time() - days * 86400
    directory = root / '.anjawiki/transcripts'
    if (root / '.anjawiki').is_symlink() or directory.is_symlink() or not directory.resolve().is_relative_to(root):
        raise ValueError('archive escapes project')
    candidates = [p for p in directory.glob('*.jsonl') if re.fullmatch(r'[a-f0-9]{64}\.jsonl', p.name) and not p.is_symlink() and p.stat().st_mtime < cutoff]
    deleted = []
    for path in candidates:
        if apply:
            path.unlink()
        deleted.append(str(path.relative_to(root)))
    # I riferimenti restano nello storico: availability riporta missing, mai lossless.
    return {'dry_run': not apply, 'files': deleted, 'days': days}
