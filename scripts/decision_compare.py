"""Compare a caller-selected Git snapshot with current decision/code sources."""
from __future__ import annotations

import difflib
import hashlib
import os
import re
import subprocess
from pathlib import Path

import index_policy
from search_evidence import MAX_SOURCE_BYTES, source_snapshot


class BaselineError(ValueError):
    pass


def git(root, *args):
    env = {**os.environ, 'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_NO_LAZY_FETCH': '1'}
    try:
        result = subprocess.run(['git', '-C', str(root), *args], env=env,
                                capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BaselineError('git_unavailable') from exc
    if result.returncode:
        raise BaselineError('git_object_unavailable')
    return result.stdout


def historical(root, commit, repository_path):
    raw = git(root, 'ls-tree', '-z', commit, '--', ':(literal)' + repository_path)
    if not raw:
        return None
    records = raw.rstrip(b'\0').split(b'\0')
    if len(records) != 1:
        raise BaselineError('ambiguous_tree_entry')
    header, name = records[0].split(b'\t', 1)
    mode, kind, oid = header.split()
    if os.fsdecode(name) != repository_path or mode not in (b'100644', b'100755') or kind != b'blob':
        raise BaselineError('unsupported_tree_entry')
    oid = oid.decode('ascii')
    if int(git(root, 'cat-file', '-s', oid)) > MAX_SOURCE_BYTES:
        raise BaselineError('baseline_too_large')
    data = git(root, 'cat-file', 'blob', oid)
    if len(data) > MAX_SOURCE_BYTES:
        raise BaselineError('baseline_too_large')
    return dict(revision=hashlib.sha256(data).hexdigest(), blob_oid=oid,
                lines=data.decode('utf-8', errors='replace').removesuffix('\n').split('\n') if data else [])


def relative(path):
    if not isinstance(path, str) or not path or Path(path).is_absolute() or '..' in Path(path).parts:
        raise ValueError('invalid_path')
    return Path(path).as_posix()


def comparison(path, baseline, current_revision, current_lines, budget):
    old = baseline['lines'] if baseline else []
    status = 'added' if baseline is None else ('unchanged' if baseline['revision'] == current_revision else 'modified')
    # Consume the generator only to the output budget; no quadratic full diff string allocation.
    parts, used, clipped = [], 0, False
    if status != 'unchanged':
        for line in difflib.unified_diff(old, current_lines, fromfile='baseline/' + path,
                                         tofile='current/' + path, lineterm=''):
            piece = line + '\n'
            shown = piece[:budget - used]
            parts.append(shown)
            used += len(shown)
            if len(shown) < len(piece):
                clipped = True
                break
    return dict(path=path, status=status, baseline_revision=baseline['revision'] if baseline else None,
                baseline_blob_oid=baseline['blob_oid'] if baseline else None,
                current_revision=current_revision, baseline_lines=len(old), current_lines=len(current_lines),
                diff=''.join(parts), diff_truncated=clipped, normalized_text_equal=(old == current_lines)), used


def compare_decision(root, decision_path, base_commit, paths, expected_decision_revision=None, max_chars=12000):
    if not isinstance(base_commit, str) or not re.fullmatch(r'[0-9a-fA-F]{40}|[0-9a-fA-F]{64}', base_commit):
        return {'error': 'base_commit must be a full immutable Git commit ID', 'code': 'invalid_base_commit'}
    if (not isinstance(paths, list) or not 1 <= len(paths) <= 20
            or type(max_chars) is not int or not 0 <= max_chars <= 100000):
        return {'error': 'provide 1–20 paths and max_chars between 0 and 100000', 'code': 'invalid_compare_options'}
    try:
        decision_path = relative(decision_path)
        paths = list(dict.fromkeys(relative(p) for p in paths))
    except ValueError:
        return {'error': 'paths must be project-relative', 'code': 'invalid_compare_path'}
    if not decision_path.endswith('.md'):
        return {'error': 'decision_path must be Markdown', 'code': 'invalid_decision_path'}
    root = Path(root).resolve()
    try:
        code_allowed = {p.relative_to(root).as_posix() for p in index_policy.discover(root, 'code')[0]}
        decision_allowed = set(code_allowed)
        if (root / '.anjawiki/wiki').is_dir():
            decision_allowed.update(p.relative_to(root).as_posix() for p in index_policy.discover(root, 'wiki')[0])
        if decision_path not in decision_allowed:
            return {'error': 'decision missing or excluded by policy', 'code': 'decision_unavailable'}
        decision_revision, decision_lines = source_snapshot(root, decision_path)
        if expected_decision_revision is not None and expected_decision_revision != decision_revision:
            return {'error': 'decision changed since supplied revision', 'code': 'revision_conflict',
                    'current_revision': decision_revision}
        repo = Path(os.fsdecode(git(root, 'rev-parse', '--show-toplevel').rstrip(b'\n'))).resolve()
        prefix = root.relative_to(repo)
        commit = base_commit.lower()
        if git(root, 'cat-file', '-t', commit).strip() != b'commit':
            raise BaselineError('not_a_commit')
        old_decision = historical(repo, commit, (prefix / decision_path).as_posix())
        if old_decision is None:
            raise BaselineError('decision_missing_at_baseline')
    except (OSError, ValueError) as exc:
        return {'error': 'cannot establish the requested baseline',
                'code': str(exc) if isinstance(exc, BaselineError) else 'baseline_unavailable'}
    decision, used = comparison(decision_path, old_decision, decision_revision, decision_lines, max_chars)
    files = []
    for path in paths:
        if path not in code_allowed:
            files.append({'path': path, 'status': 'unavailable', 'reason': 'current_missing_or_excluded'})
            continue
        try:
            revision, lines = source_snapshot(root, path)
            old = historical(repo, commit, (prefix / path).as_posix())
            item, size = comparison(path, old, revision, lines, max_chars - used)
            used += size
            files.append(item)
        except (OSError, ValueError) as exc:
            files.append({'path': path, 'status': 'unavailable',
                          'reason': str(exc) if isinstance(exc, BaselineError) else 'current_unavailable'})
    return dict(base_commit=commit, baseline_relationship='caller_selected_not_inferred',
                decision=decision, files=files, diff_chars=used, max_chars=max_chars,
                status='partial' if any(f['status'] == 'unavailable' for f in files) else 'compared',
                revision_algorithm='sha256', freshness='verified_at_read', decision_fulfillment='not_assessed',
                limits=['Diffs are textual observations, not proof that a decision was implemented or violated.',
                        'The caller selects the commit; no date or embedding checkpoint is treated as a source snapshot.',
                        'Missing/excluded current files are unavailable; deletion and rename are not inferred.',
                        'Per-file snapshots are not an atomic repository snapshot; UTF-8 decoding replaces invalid bytes.',
                        'Diff hunks refer to their respective baseline/current versions, not interchangeable line numbers.'])
