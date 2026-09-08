"""Immutable baseline comparison in disposable Git fixtures only."""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_hardening import call, payload, project, rpc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from decision_compare import compare_decision  # noqa: E402


def command(root, *args):
    env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_AUTHOR_NAME': 'Fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
           'GIT_COMMITTER_NAME': 'Fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid',
           'GIT_AUTHOR_DATE': '2026-01-01T00:00:00+00:00', 'GIT_COMMITTER_DATE': '2026-01-01T00:00:00+00:00'}
    return subprocess.check_output(['git', '-C', str(root), *args], env=env).decode().strip()


@pytest.fixture
def history(tmp_path):
    project(tmp_path)
    (tmp_path / 'decision.md').write_text('# Delivery\nFree shipping from 100.\n')
    (tmp_path / 'pricing.py').write_text('def shipping(total):\n    return 0 if total >= 100 else 8\n')
    command(tmp_path, 'init', '--quiet', '--template=')
    command(tmp_path, 'add', '.')
    command(tmp_path, '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'fixture baseline')
    return tmp_path, command(tmp_path, 'rev-parse', 'HEAD')


def test_mcp_current_code_diff_against_unchanged_decision(history):
    root, commit = history
    (root / 'pricing.py').write_text('def shipping(total):\n    return 0 if total >= 80 else 8\n')
    result = payload(rpc(root, [call('code.compare_decision', dict(decision_path='decision.md', base_commit=commit,
                                                                paths=['pricing.py']))])[0])
    assert result['decision']['status'] == 'unchanged'
    assert result['files'][0]['status'] == 'modified'
    assert '-    return 0 if total >= 100' in result['files'][0]['diff']
    assert '+    return 0 if total >= 80' in result['files'][0]['diff']
    assert result['decision_fulfillment'] == 'not_assessed'
    assert result['base_commit'] == commit
    assert result['files'][0]['baseline_revision'] == hashlib.sha256(b'def shipping(total):\n    return 0 if total >= 100 else 8\n').hexdigest()


def test_changed_decision_and_new_file_are_distinct(history):
    root, commit = history
    (root / 'decision.md').write_text('# Delivery\nFree shipping from 80.\n')
    (root / 'new.py').write_text('new = 1\n')
    result = compare_decision(root, 'decision.md', commit, ['pricing.py', 'new.py'])
    assert result['decision']['status'] == 'modified'
    assert [f['status'] for f in result['files']] == ['unchanged', 'added']


def test_missing_current_file_does_not_infer_deletion(history):
    root, commit = history
    (root / 'pricing.py').rename(root / 'renamed.py')
    result = compare_decision(root, 'decision.md', commit, ['pricing.py', 'renamed.py'])
    assert result['status'] == 'partial'
    assert result['files'][0]['reason'] == 'current_missing_or_excluded'
    assert result['files'][1]['status'] == 'added'


def test_missing_decision_history_is_not_invented(history):
    root, commit = history
    (root / 'new-decision.md').write_text('# New decision\n')
    assert compare_decision(root, 'new-decision.md', commit, ['pricing.py'])['code'] == 'decision_missing_at_baseline'


@pytest.mark.parametrize('commit', ['HEAD', '--help', 'a' * 39, 'a' * 40 + ':pricing.py'])
def test_only_full_commit_ids_are_accepted(history, commit):
    root, _ = history
    assert compare_decision(root, 'decision.md', commit, ['pricing.py'])['code'] == 'invalid_base_commit'


def test_blob_is_not_a_commit_and_revision_conflicts_fail(history):
    root, commit = history
    blob = command(root, 'rev-parse', commit + ':decision.md')
    assert compare_decision(root, 'decision.md', blob, ['pricing.py'])['code'] == 'not_a_commit'
    assert compare_decision(root, 'decision.md', commit, ['pricing.py'], expected_decision_revision='wrong')['code'] == 'revision_conflict'


def test_current_policy_prevents_historical_secret_reads(history):
    root, commit = history
    (root / '.anjawiki/index-policy.json').write_text('{"exclude":["pricing.py"]}')
    result = compare_decision(root, 'decision.md', commit, ['pricing.py'])
    assert result['files'][0] == dict(path='pricing.py', status='unavailable', reason='current_missing_or_excluded')
    assert compare_decision(root, '../decision.md', commit, ['pricing.py'])['code'] == 'invalid_compare_path'


def test_wiki_decision_and_nested_project_root(history):
    root, _ = history
    nested = root / 'nested'
    project(nested)
    (nested / '.anjawiki/wiki/entities/decision.md').write_text('# Nested decision\n')
    (nested / 'app.py').write_text('value = 1\n')
    command(root, 'add', '.')
    command(root, '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'nested fixture')
    commit = command(root, 'rev-parse', 'HEAD')
    result = compare_decision(nested, '.anjawiki/wiki/entities/decision.md', commit, ['app.py'])
    assert result['status'] == 'compared'
    assert result['files'][0]['status'] == 'unchanged'


def test_historical_symlink_is_not_read_as_source(history):
    root, _ = history
    (root / 'alias.py').symlink_to(root / 'pricing.py')
    command(root, 'add', 'alias.py')
    command(root, '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'symlink fixture')
    commit = command(root, 'rev-parse', 'HEAD')
    (root / 'alias.py').unlink()
    (root / 'alias.py').write_text('safe = 1\n')
    result = compare_decision(root, 'decision.md', commit, ['alias.py'])
    assert result['files'][0]['reason'] == 'unsupported_tree_entry'


def test_literal_git_pathspec_and_shared_diff_budget(history):
    root, commit = history
    (root / '[file].py').write_text('value = 1\n')
    command(root, 'add', '.')
    command(root, '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'literal filename fixture')
    commit = command(root, 'rev-parse', 'HEAD')
    (root / '[file].py').write_text('value = 2\n')
    (root / 'decision.md').write_text('# Changed decision\n')
    result = compare_decision(root, 'decision.md', commit, ['[file].py'], max_chars=10)
    assert result['diff_chars'] <= 10
    assert result['files'][0]['status'] == 'modified'
    assert result['files'][0]['diff_truncated']
    assert len(result['decision']['diff']) + len(result['files'][0]['diff']) <= 10


def test_terminal_newline_change_is_visible_in_hash_status(history):
    root, commit = history
    path = root / 'pricing.py'
    path.write_text(path.read_text().removesuffix('\n'))
    result = compare_decision(root, 'decision.md', commit, ['pricing.py'])
    assert result['files'][0]['status'] == 'modified'
    assert result['files'][0]['normalized_text_equal'] is True


def test_git_replace_cannot_rewrite_the_selected_baseline(history):
    root, original = history
    (root / 'pricing.py').write_text('def shipping(total):\n    return 0 if total >= 80 else 8\n')
    command(root, 'add', '.')
    command(root, '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'replacement fixture')
    replacement = command(root, 'rev-parse', 'HEAD')
    command(root, 'replace', original, replacement)
    result = compare_decision(root, 'decision.md', original, ['pricing.py'])
    assert result['files'][0]['status'] == 'modified'
    assert '-    return 0 if total >= 100' in result['files'][0]['diff']
