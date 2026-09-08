"""Policy dati con Git nativo, preview offline e isolamento dei contenuti."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import index_pipeline  # noqa: E402
import index_policy  # noqa: E402

import embed_providers  # noqa: E402


def write(root, name, content='value = 1\n'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_native_ignore_layers_include_tracked_nested_and_unicode(tmp_path):
    subprocess.run(['git','init','-q',str(tmp_path)], check=True)
    names = ['good.py', 'tracked.py', 'nested/secret.py', 'private/no.py', 'private/keep.py', 'nome con\tè.py']
    for name in names:
        write(tmp_path, name)
    subprocess.run(['git','-C',str(tmp_path),'add','tracked.py'], check=True)
    write(tmp_path, '.gitignore', 'tracked.py\nnome con\tè.py\n')
    write(tmp_path, 'nested/.gitignore', 'secret.py\n')
    write(tmp_path, '.anjaignore', 'private/*.py\n!private/keep.py\n')
    files, excluded = index_policy.discover(tmp_path)
    assert {str(p.relative_to(tmp_path)) for p in files} == {'good.py', 'private/keep.py'}
    reasons = {e['path']:e['reason'] for e in excluded}
    assert reasons['tracked.py'] == 'gitignore'
    assert reasons['private/no.py'] == 'anjaignore'


def test_ignore_outside_git_and_literal_configuration(tmp_path):
    write(tmp_path, 'keep.py')
    write(tmp_path, 'private.py')
    write(tmp_path, '.anjaignore', 'private.py\n')
    assert [p.name for p in index_policy.discover(tmp_path)[0]] == ['keep.py']
    write(tmp_path, '.anjawiki/index-policy.json', json.dumps({'exclude':['keep.py']}))
    assert index_policy.discover(tmp_path)[0] == []
    write(tmp_path, '.anjawiki/index-policy.json', json.dumps({'exclude':['../outside']}))
    with pytest.raises(index_policy.PolicyError):
        index_policy.discover(tmp_path)


def test_dry_run_never_constructs_provider_or_writes_index(tmp_path, monkeypatch):
    write(tmp_path, 'good.py')
    write(tmp_path, 'credentials.py', 'SECRET_TOKEN')
    write(tmp_path, '.anjawiki/meta.yaml', 'type: dev')
    monkeypatch.setenv('ANJA_EMBED_PROVIDER', 'openrouter')
    monkeypatch.setenv('ANJA_EMBED_MODEL', 'unknown-model-that-would-probe')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'private-key-not-for-output')
    monkeypatch.setattr(embed_providers, 'get_provider', lambda: pytest.fail('provider constructed'))
    before = {str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result = index_pipeline.refresh(tmp_path, kind='code', dry_run=True)
    assert result['dry_run'] and not result['remote_authorized']
    assert [f['path'] for f in result['files']] == ['good.py']
    assert 'private-key-not-for-output' not in json.dumps(result)
    assert before == {str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    denied = index_pipeline.refresh(tmp_path, kind='code')
    assert denied['code'] == 'index_policy_error'


def test_remote_authorization_binds_model(tmp_path, monkeypatch):
    monkeypatch.setenv('ANJA_EMBED_PROVIDER','openai')
    monkeypatch.setenv('ANJA_EMBED_MODEL','text-embedding-3-small')
    write(tmp_path, '.anjawiki/index-policy.json', json.dumps({'remote':{'provider':'openai','model':'text-embedding-3-small'}}))
    index_policy.authorize(tmp_path)
    monkeypatch.setenv('ANJA_EMBED_MODEL','other')
    with pytest.raises(index_policy.PolicyError):
        index_policy.authorize(tmp_path)


def test_missing_git_fails_closed_for_ignore_rules(tmp_path, monkeypatch):
    write(tmp_path, 'private.py')
    write(tmp_path, '.gitignore', 'private.py\n')
    monkeypatch.setattr(index_policy.shutil, 'which', lambda name: None)
    with pytest.raises(index_policy.PolicyError):
        index_policy.discover(tmp_path)


def test_policy_applies_to_provider_payload_and_removes_old_vectors(tmp_path):
    from test_embed_mock import sqlite_vec_usable
    from test_index_pipeline import run
    if not sqlite_vec_usable():
        pytest.skip('sqlite-vec unavailable')
    run(tmp_path, r'''
(root / 'credentials.py').write_text('NEVER_SEND_SECRET')
(root / '.anjaignore').write_text('b.py\n')
seen = []
original = p.embed
def record(texts):
    seen.extend(texts)
    return original(texts)
p.embed = record
assert refresh(force=True)['status'] == 'ready'
assert not any('NEVER_SEND_SECRET' in text or 'other = 2' in text for text in seen)
assert not any(row[1] == 'b.py' for row in contents()['chunks'])
seen.clear()
(root / 'a.py').write_text('incremental = 33\n')
assert refresh()['status'] == 'ready'
assert len(seen) == 1 and 'incremental = 33' in seen[0]
''')


def test_policy_change_stops_next_batch_before_sending(tmp_path):
    from test_embed_mock import sqlite_vec_usable
    from test_index_pipeline import run
    if not sqlite_vec_usable():
        pytest.skip('sqlite-vec unavailable')
    run(tmp_path, r'''
seen = []
original = p.embed
def change(texts):
    seen.extend(texts)
    (root / '.anjaignore').write_text('b.py\n')
    return original(texts)
p.embed = change
result = refresh(force=True, batch_size=1)
assert result['status'] == 'stale', result
assert len(seen) == 1
assert contents() == before
''')


def test_migration_preview_includes_both_scopes_without_mutating_db(tmp_path):
    from test_embed_mock import sqlite_vec_usable
    from test_index_pipeline import run
    if not sqlite_vec_usable():
        pytest.skip('sqlite-vec unavailable')
    run(tmp_path, r'''
import index_policy
result = refresh(dry_run=True)
assert result['migration'] and set(result['scopes']) == {'code','wiki'}, result
assert any(file['kind'] == 'wiki' for file in result['files'])
assert contents() == before
assert not (root / '.anjawiki/backups').exists()
''')


def test_rerank_respects_policy_and_explicit_model(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import code_search
    write(tmp_path, 'good.py')
    write(tmp_path, 'credentials.py', 'hidden')
    candidates = [{'path':'good.py', 'preview':[{'line':1,'text':'allowed'}]},
                  {'path':'credentials.py', 'preview':[{'line':1,'text':'NEVER_SEND_SECRET'}]}]
    monkeypatch.setattr(code_search, 'search_level_0', lambda *a, **k: {'level':0,'results':list(candidates),'count':2})
    denied = code_search.search_level_1('query', tmp_path)
    assert denied['level'] == 0 and 'not authorized' in denied['_fallback_reason']
    write(tmp_path, '.anjawiki/index-policy.json', '{"rerank_model":"haiku"}')
    original = code_search.subprocess.run
    sent = []
    def fake(command, **kwargs):
        if command[0] == 'claude':
            sent.append(command)
            return SimpleNamespace(returncode=0, stdout='0', stderr='')
        return original(command, **kwargs)
    monkeypatch.setattr(code_search.subprocess, 'run', fake)
    result = code_search.search_level_1('query', tmp_path)
    assert result['level'] == 1
    assert sent and 'NEVER_SEND_SECRET' not in str(sent)


def test_state_symlink_cannot_write_outside_project(tmp_path, monkeypatch):
    outside = tmp_path / 'outside'
    outside.mkdir()
    root = tmp_path / 'project'
    root.mkdir()
    (root / '.anjawiki').symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv('ANJA_EMBED_PROVIDER','mock')
    result = index_pipeline.refresh(root, kind='code')
    assert result['code'] == 'unsafe_project_state'
    assert list(outside.iterdir()) == []


def test_vector_query_cannot_probe_remote_without_policy(tmp_path, monkeypatch):
    import code_search
    write(tmp_path, '.anjawiki/code-index.db', '')
    monkeypatch.setenv('ANJA_EMBED_PROVIDER','openrouter')
    monkeypatch.setenv('ANJA_EMBED_MODEL','unknown-model')
    monkeypatch.setattr(embed_providers, 'get_provider', lambda: pytest.fail('unauthorized constructor'))
    monkeypatch.setattr(code_search, 'search_level_0', lambda *a, **k: {'level':0,'results':[],'count':0})
    result = code_search.search_level_2('query', tmp_path)
    assert result['level'] == 0 and result['code'] == 'index_policy_error'
