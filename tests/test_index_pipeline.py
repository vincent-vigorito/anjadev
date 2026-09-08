"""Q3: regressioni reali SQLite/vec, isolate dal progetto e senza rete."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from _helpers import cov_env
from test_embed_mock import sqlite_vec_usable

PYTHON = os.environ.get("ANJA_TEST_PYTHON", sys.executable)
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
pytestmark = pytest.mark.skipif(not sqlite_vec_usable(), reason="sqlite-vec extension unavailable")

SETUP = r'''
import sys, json, os
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import code_db, code_index, index_pipeline as pipeline
root = Path(sys.argv[2])
os.environ['ANJA_EMBED_PROVIDER'] = 'mock'
(root / '.anjawiki/wiki/concepts').mkdir(parents=True)
(root / 'a.py').write_text('value = 1\n')
(root / 'b.py').write_text('other = 2\n')
page = root / '.anjawiki/wiki/concepts/test.md'
page.write_text('---\ntitle: Test\ntype: concept\n---\nWiki original\n')
class Provider:
    name = 'test'
    model = 'one'
    dim = 3
    def embed(self, texts):
        return [[float(len(t)), 2., 3.] + [4.] * (self.dim - 3) for t in texts]
p = Provider()
pipeline.embed_providers.get_provider = lambda: p

def refresh(**kwargs):
    return pipeline.refresh(root, kind='code', **kwargs)

def contents():
    db = code_db.open_db(root / '.anjawiki', dim=p.dim, allow_dimension_mismatch=True)
    try:
        return {table: [tuple(r) for r in db.execute('SELECT * FROM ' + table + ' ORDER BY 1')]
                for table in ('chunks', 'chunk_vec', 'indexed_files', 'meta')}
    finally:
        db.close()
assert refresh()['status'] == 'ready'
assert pipeline.refresh(root, kind='wiki')['status'] == 'ready'
before = contents()
'''


def run(tmp_path, body):
    result = subprocess.run([PYTHON, "-c", SETUP + body, str(SCRIPTS), str(tmp_path)],
                            capture_output=True, text=True, timeout=45, env={**os.environ, **cov_env()})
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("fault", ["[]", "[[1.]]", "[[float('nan'),2,3]]", "[[float('inf'),2,3]]",
                                  "[[1e100,2,3]]", "[[0,0,0]]", "[[True,2,3]]"])
def test_invalid_batch_preserves_published_index(tmp_path, fault):
    run(tmp_path, f'''
p.embed = lambda texts: {fault}
r = refresh(force=True, batch_size=1)
assert r['status'] == 'failed', r
assert contents() == before
''')


def test_later_batch_failure_preserves_force_index(tmp_path):
    run(tmp_path, r'''
original = p.embed
calls = 0
def fail(texts):
    global calls
    calls += 1
    if calls == 2: raise RuntimeError('provider offline')
    return original(texts)
p.embed = fail
assert refresh(force=True, batch_size=1)['status'] == 'failed'
assert calls == 2
assert contents() == before
''')


@pytest.mark.parametrize("dim", [3, 5])
def test_migration_rebuilds_both_scopes_and_rolls_back_ddl(tmp_path, dim):
    run(tmp_path, f'''
p.model = 'two'
p.dim = {dim}
try:
    code_db.open_db(root / '.anjawiki', dim=p.dim, create_if_missing=False, provider=p)
except RuntimeError: pass
else: raise AssertionError('incompatible vectors accepted')
original = code_db.upsert_chunk
with patch.object(code_db, 'upsert_chunk', side_effect=RuntimeError('publish interrupted')):
    r = refresh()
assert r['status'] == 'failed', r
assert contents() == before
r = pipeline.refresh(root, kind='wiki', single=page)
assert r['status'] == 'ready' and r['migration'], r
assert set(r['scopes']) == {{'code','wiki'}}
after = contents()
assert len(after['chunks']) == len(before['chunks'])
assert all(len(row[1]) == p.dim * 4 for row in after['chunk_vec'])
assert dict(after['meta'])['index_fingerprint'] == code_db.fingerprint(p)
s = pipeline.index_status(root, p)
assert s['scopes']['code']['status'] == 'ready', s
assert s['scopes']['wiki']['status'] == 'ready', s
''')


def test_partial_worktree_rename_delete_and_exclusion(tmp_path):
    run(tmp_path, r'''
(root / 'a.py').write_text('value = 33\n')
(root / 'b.py').rename(root / 'nome con\taccento è.py')
r = refresh(limit=1)
assert r['status'] == 'partial' and r['deferred_files'], r
assert dict(contents()['meta'])['last_indexed_at'] == dict(before['meta'])['last_indexed_at']
assert refresh()['status'] == 'ready'
paths = {r[1] for r in contents()['chunks'] if r[-1] == 'code'}
assert paths == {'a.py', 'nome con\taccento è.py'}, paths
(root / 'a.py').write_text('')
(root / 'nome con\taccento è.py').write_text('x' * 500001)
r = refresh()
assert {item['reason'] for item in r['excluded_files']} == {'empty','too_large'}, r
assert not [r for r in contents()['chunks'] if r[-1] == 'code']
assert pipeline.index_status(root, p)['status'] == 'ready'
''')


def test_filesystem_race_does_not_publish_snapshot(tmp_path):
    run(tmp_path, r'''
original = p.embed
def race(texts):
    (root / 'a.py').write_text('changed while embedding = True\n')
    return original(texts)
p.embed = race
r = refresh(force=True)
assert r['status'] == 'stale', r
assert contents() == before
assert pipeline.index_status(root, p)['status'] == 'stale'
''')


def test_publish_failure_rolls_back_chunk_deletions(tmp_path):
    run(tmp_path, r'''
original = code_db.upsert_chunk
calls = 0
def fail(*args, **kwargs):
    global calls
    calls += 1
    if calls == 2: raise RuntimeError('disk failure')
    return original(*args, **kwargs)
with patch.object(code_db, 'upsert_chunk', side_effect=fail):
    assert refresh(force=True)['status'] == 'failed'
assert contents() == before
assert refresh(force=True)['status'] == 'ready'
''')


def test_concurrent_publication_retries_current_content(tmp_path):
    run(tmp_path, r'''
original = p.embed
entered = False
def competing(texts):
    global entered
    if not entered:
        entered = True
        (root / 'a.py').write_text('winner = 987654\n')
        assert refresh()['status'] == 'ready'
    return original(texts)
p.embed = competing
r = refresh(force=True)
assert r['status'] == 'ready' and r['attempts'] == 2, r
assert any('winner = 987654' in row[5] for row in contents()['chunks'])
''')


@pytest.mark.parametrize("suffix,text", [
    ('.py', '\n'.join(f'x{i} = {i}' for i in range(150)) + '\n@decorator\ndef f():\n' + '    pass\n' * 200),
    ('.js', '// header\n' * 100 + 'function f() {\n' + '  work();\n' * 200 + '}\n'),
    ('.go', '// header\n' * 100 + 'func main() {\n' + ' work()\n' * 200 + '}\n'),
])
def test_chunk_coverage_and_line_references(tmp_path, suffix, text):
    run(tmp_path, f'''
text = {text!r}
lines = text.split('\\n')
chunks = code_index.chunk_text(text, {suffix!r})
covered = set()
for chunk in chunks:
    start, end = chunk['line_start'], chunk['line_end']
    assert end - start < 80
    assert chunk['content'] == '\\n'.join(lines[start-1:end])
    covered.update(range(start, end+1))
assert {{i for i,line in enumerate(lines,1) if line.strip()}} <= covered
''')


def test_process_death_during_publication_recovers_previous_index(tmp_path):
    run(tmp_path, r'''
import subprocess
child = """
import sys, os
sys.path.insert(0, sys.argv[1])
from pathlib import Path
import code_db, index_pipeline as pipeline
class P:
    name='test'; model='one'; dim=3
    def embed(self, texts): return [[9.,2.,3.] for t in texts]
pipeline.embed_providers.get_provider = P
original = code_db.upsert_chunk
def crash(*args, **kwargs):
    original(*args, **kwargs)
    os._exit(71)
code_db.upsert_chunk = crash
pipeline.refresh(Path(sys.argv[2]), kind='code', force=True)
"""
r = subprocess.run([sys.executable, '-c', child, sys.argv[1], str(root)], timeout=20)
assert r.returncode == 71
assert contents() == before
s = pipeline.index_status(root, p)
assert s['status'] == 'failed', s
assert 'interrupted' in s['scopes']['code']['last_attempt']['error']
assert refresh()['status'] == 'ready'
''')


def test_git_staged_untracked_and_worktree_with_same_head(tmp_path):
    run(tmp_path, r'''
import subprocess
def git(*args):
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
git('init')
git('add', 'a.py', 'b.py')
git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'fixture')
assert refresh()['status'] == 'ready'
head = git('rev-parse', 'HEAD')
(root / 'a.py').write_text('staged = 4\n')
git('add', 'a.py')
(root / 'b.py').write_text('worktree = 5\n')
(root / 'new è\tname.py').write_text('untracked = 6\n')
assert refresh()['status'] == 'ready'
assert git('rev-parse', 'HEAD') == head
rows = contents()['chunks']
assert any('staged = 4' in row[5] for row in rows)
assert any('worktree = 5' in row[5] for row in rows)
assert any('untracked = 6' in row[5] for row in rows)
''')


def test_status_and_lexical_fallback_for_stale_and_incompatible(tmp_path):
    run(tmp_path, r'''
import code_search
assert code_search.search_level_2('value', root)['level'] == 2
(root / 'a.py').write_text('fresh_keyword = 123\n')
r = code_search.search_level_2('fresh_keyword', root)
assert r['level'] == 0 and r['index_status'] == 'stale', r
assert r['results'] and r['_fallback_reason'], r
assert refresh()['status'] == 'ready'
p.model = 'new'
r = code_search.search_level_2('fresh_keyword', root)
assert r['level'] == 0 and r['index_status'] == 'incompatible', r
assert refresh(limit=1)['status'] == 'partial'
assert refresh()['status'] == 'ready'
''')


def test_legacy_fingerprint_requires_successful_full_rebuild(tmp_path):
    run(tmp_path, r'''
db = code_db.open_db(root / '.anjawiki', dim=3)
db.execute("DELETE FROM meta WHERE key='index_fingerprint'")
db.commit()
db.close()
before = contents()
original = p.embed
p.embed = lambda texts: []
assert refresh()['status'] == 'failed'
assert contents() == before
assert pipeline.index_status(root, p)['status'] == 'incompatible'
p.embed = original
r = refresh()
assert r['status'] == 'ready' and r['migration'], r
assert set(r['scopes']) == {'code','wiki'}
''')


def test_compatible_concurrent_jobs_preserve_manifests(tmp_path):
    run(tmp_path, r'''
(root / 'a.py').write_text('new_a = 44\n')
(root / 'b.py').write_text('new_b = 55\n')
original = p.embed
entered = False
def competing(texts):
    global entered
    if not entered:
        entered = True
        for _ in range(4):
            assert refresh(force=True)['status'] == 'ready'
    return original(texts)
p.embed = competing
r = refresh(limit=1)
assert r['status'] == 'partial' and r['attempts'] == 1, r
s = pipeline.index_status(root, p)
assert s['scopes']['code']['changed_files'] == [], s
assert dict(contents()['meta'])['index_generation'] == '7'
assert refresh()['indexed_chunks'] == 0
''')


def test_search_rejects_change_during_query_embedding(tmp_path):
    run(tmp_path, r'''
import code_search
(root / '.anjawiki/meta.yaml').write_text('name: fixture\n')
original = p.embed
def changing_embed(texts):
    (root / 'a.py').write_text('value = 999\n')
    return original(texts)
p.embed = changing_embed
result = code_search.code_search('value', smart_level=2, root=root)
assert result['evidence']['rejected']
assert not any(h['path'] == 'a.py' for h in result['results'])
assert all(not any(k.startswith('_indexed_') for k in h) for h in result['results'])
''')


def test_search_verified_vector_and_stale_fallback_contract(tmp_path):
    run(tmp_path, r'''
import code_search
(root / '.anjawiki/meta.yaml').write_text('name: fixture\n')
result = code_search.code_search('value', smart_level=2, root=root, max_preview_chars=5)
assert result['level'] == 2
assert result['evidence']['status'] == 'literal_evidence'
assert result['evidence']['preview_chars'] <= 5
assert all(h['evidence']['freshness'] == 'verified_at_read' for h in result['results'])
(root / 'a.py').write_text('new_keyword = 999\n')
result = code_search.code_search('new_keyword', smart_level=2, root=root)
assert result['level'] == 0 and result['index_status'] == 'stale'
assert result['evidence']['status'] == 'literal_evidence'
assert result['results'][0]['path'] == 'a.py'
''')
