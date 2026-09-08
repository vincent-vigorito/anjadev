"""Evidence for code.search: current source snapshots, not semantic confidence."""
from __future__ import annotations

import hashlib
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

MAX_SOURCE_BYTES = 500_000


class InvalidEvidence(ValueError):
    pass


def source_snapshot(root, relative):
    path = Path(relative)
    if path.is_absolute() or not path.parts or '..' in path.parts:
        raise InvalidEvidence('unsafe_path')
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise InvalidEvidence('unsafe_path')
    if not current.resolve().is_relative_to(root):
        raise InvalidEvidence('unsafe_path')
    fd = os.open(current, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise InvalidEvidence('not_regular_file')
        data = stream.read(MAX_SOURCE_BYTES + 1)
        after = os.fstat(stream.fileno())
    if len(data) > MAX_SOURCE_BYTES:
        raise InvalidEvidence('source_too_large')
    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
        raise InvalidEvidence('source_changed')
    # split on physical newlines, consistently with the index pipeline.
    text = data.decode('utf-8', errors='replace')
    lines = text.removesuffix('\n').split('\n') if text else []
    return hashlib.sha256(data).hexdigest(), lines


def verify(item, root, query):
    revision, lines = source_snapshot(root, item['path'])
    vector = 'line_start' in item
    if vector:
        if revision != item.get('_indexed_revision'):
            raise InvalidEvidence('stale_index_snapshot')
        spans = [(item['line_start'], item['line_end'])]
    else:
        spans = [(p['line'], p['line']) for p in item['preview']]
    if not spans or not all(1 <= a <= b <= len(lines) for a, b in spans):
        raise InvalidEvidence('invalid_lines')
    evidence = ['\n'.join(lines[a - 1:b]) for a, b in spans]
    if vector:
        if evidence[0] != item.get('_indexed_content'):
            raise InvalidEvidence('index_content_mismatch')
    elif any(p['text'].rstrip('\r') != text.rstrip('\r') for p, text in zip(item['preview'], evidence)):
        raise InvalidEvidence('source_changed')
    return dict(source_revision=revision, revision_algorithm='sha256', freshness='verified_at_read',
                spans=[dict(line_start=a, line_end=b) for a, b in spans],
                match='literal' if any(query.casefold() in text.casefold() for text in evidence) else 'candidate')


def finalize(result, root, query, max_preview_chars):
    """Preserve ranking; reject unverifiable hits and bound only preview text."""
    if 'error' in result:
        return result
    accepted, rejected = [], []
    remaining = max_preview_chars
    truncated = False
    for item in result.get('results', []):
        try:
            evidence = verify(item, root, query)
        except (InvalidEvidence, OSError) as exc:
            rejected.append({'reason': str(exc) if isinstance(exc, InvalidEvidence) else 'source_unavailable'})
            continue
        hit = {k: v for k, v in item.items() if not k.startswith('_indexed_')}
        preview = hit['preview']
        if isinstance(preview, str):
            hit['preview'] = preview[:remaining]
            remaining -= len(hit['preview'])
            clipped = len(hit['preview']) < len(preview)
        else:
            hit['preview'] = []
            clipped = False
            for entry in preview:
                shown = entry['text'][:remaining]
                remaining -= len(shown)
                clipped |= len(shown) < len(entry['text'])
                hit['preview'].append({**entry, 'path': hit['path'], 'text': shown})
        hit['evidence'] = {**evidence, 'preview_truncated': clipped}
        truncated |= clipped
        accepted.append(hit)
    literal = sum(hit['evidence']['match'] == 'literal' for hit in accepted)
    # A literal occurrence is evidence of text, never proof of behavior or test coverage.
    status = 'literal_evidence' if literal else ('candidates_only' if accepted else 'no_evidence')
    return {**result, 'results': accepted, 'count': len(accepted),
            'evidence': {'status': status, 'abstain': not bool(literal),
                         'meaning': 'Literal text only; semantic relevance and behavioral claims require review.',
                         'checked_at': datetime.now(timezone.utc).isoformat(),
                         'literal_matches': literal, 'rejected': rejected,
                         'preview_chars': max_preview_chars - remaining,
                         'max_preview_chars': max_preview_chars, 'preview_truncated': truncated,
                         'scope': 'Returned source snapshots only; not an atomic repository snapshot.'}}
