"""Q7: v0.30.0 layout (e5604f1), process death, rerun and restore; no network."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from _helpers import cov_env
from test_embed_mock import sqlite_vec_usable

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
PYTHON = os.environ.get('ANJA_TEST_PYTHON', sys.executable)
pytestmark = pytest.mark.skipif(not sqlite_vec_usable(), reason='sqlite-vec unavailable')

DRIVER = r"""
import json, os, sqlite3, subprocess, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import code_db, index_pipeline, project_recovery, roadmap_io, upgrade_triade
from anja import trust
root = Path(sys.argv[2]) / 'project'
mode = sys.argv[3]
wiki = root / '.anjawiki/wiki'
(wiki / 'concepts').mkdir(parents=True)
roadmap = wiki / 'roadmap.md'
roadmap.write_text('---\ntitle: Roadmap\ntype: roadmap\ncreated: 2026-09-04\nupdated: 2026-09-04\n---\n# Roadmap\n\n## Open\n- [ ] (P1) Same task | owner: alice\n- [~] (P2) Same task | owner: bob\n\n## Done\n- [x] Finished | done: 2026-09-04\n\n## Blocked\n')
page = wiki / 'concepts/auth.md'
page.write_text('---\ntitle: Auth\ntype: concept\nverified:\n  - by: human:vincent\n    at: 2026-09-04\n---\nAuthentication knowledge.\n')
(root / 'auth.py').write_text('def authenticate():\n    return True\n')
(root / '.anjawiki/meta.yaml').write_text('name: fixture\ntype: dev\n')
(root / '.anjawiki/.schema-version').write_text('1.2\n')
config = {'memory': {'hot_budget_tokens': 1234}, 'sessions': {'archive_max': 87}, 'custom': 'preserve'}
(root / '.anjawiki/config.json').write_text(json.dumps(config))
(root / 'AGENTS.src.md').write_text('# Custom project instructions\n')
(root / 'SOUL.md').write_text('# Custom preferences\n')
(root / '.anjawiki/.secrets.env').write_text('FIXTURE_ONLY=not-a-real-secret\n')
os.environ['HOME'] = str(Path(sys.argv[2]) / 'home')
Path(os.environ['HOME']).mkdir()
os.environ['ANJA_EMBED_PROVIDER'] = 'mock'
os.environ['ANJA_EMBED_MODEL'] = ''
os.environ['ANJA_WIKI_EMBED'] = '0'

# SQL contract from code_db.py at e5604f1: cosine + kind, no fingerprint/manifests.
database = root / '.anjawiki/code-index.db'
db = sqlite3.connect(database)
code_db._ensure_sqlite_vec(db)
db.executescript('''
CREATE TABLE chunks (id INTEGER PRIMARY KEY AUTOINCREMENT, file_path TEXT NOT NULL,
 func_name TEXT, line_start INTEGER, line_end INTEGER, content TEXT NOT NULL,
 lang TEXT, last_modified TEXT, content_sha TEXT, kind TEXT NOT NULL DEFAULT 'code',
 UNIQUE(file_path,line_start,line_end));
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE VIRTUAL TABLE chunk_vec USING vec0(embedding float[3] distance_metric=cosine);
''')
db.executemany('INSERT INTO meta VALUES (?,?)', [('embed_dim','3'),('embed_metric','cosine'),('last_indexed_sha','legacy-checkpoint')])
for number, (path, content, kind) in enumerate([('auth.py', 'legacy code', 'code'), (str(page), 'legacy wiki', 'wiki')], 1):
    db.execute('INSERT INTO chunks(id,file_path,line_start,line_end,content,kind) VALUES (?,?,1,1,?,?)', (number,path,content,kind))
    db.execute('INSERT INTO chunk_vec(rowid,embedding) VALUES (?,?)', (number, code_db._serialize_vec([1.,2.,3.])))
db.commit()
db.close()
def published():
    db = sqlite3.connect(database)
    code_db._ensure_sqlite_vec(db)
    try:
        return {t: db.execute('SELECT * FROM ' + t + ' ORDER BY 1').fetchall() for t in ('chunks','chunk_vec','meta')}
    finally:
        db.close()
legacy = published()
original = {str(p.relative_to(root)): p.read_bytes() for p in [roadmap,page,root/'AGENTS.src.md',root/'SOUL.md',root/'.anjawiki/config.json',root/'.anjawiki/.schema-version']}
backup = Path(project_recovery.snapshot_project(root, reason='release_migration_fixture'))
assert trust.status(page.read_text())['trust_tier'] == 'legacy'
assert len(roadmap_io.list_tasks(roadmap_io.parse_roadmap(roadmap))) == 3

child = r'''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import code_db, index_pipeline, project_recovery, roadmap_io, upgrade_triade
from anja import persistence
root = Path(sys.argv[2])
mode = sys.argv[3]
roadmap = root / '.anjawiki/wiki/roadmap.md'
if mode.startswith('roadmap'):
    real = persistence.os.replace
    def stop(source, target):
        if Path(target) == roadmap and mode == 'roadmap_before':
            os._exit(73)
        real(source, target)
        if Path(target) == roadmap:
            os._exit(73)
    persistence.os.replace = stop
    roadmap_io.write_roadmap(roadmap, roadmap_io.parse_roadmap(roadmap))
elif mode == 'index':
    real = code_db.upsert_chunk
    def stop(*args, **kwargs):
        real(*args, **kwargs)
        os._exit(73)
    code_db.upsert_chunk = stop
    index_pipeline.refresh(root, kind='code')
elif mode == 'restore':
    real = project_recovery.os.rename
    def stop(source, target):
        real(source, target)
        os._exit(73)
    project_recovery.os.rename = stop
    project_recovery.restore(Path(sys.argv[4]), root.parent / 'partial-restore')
elif mode == 'upgrade':
    real = Path.write_text
    def stop(path, *args, **kwargs):
        result = real(path, *args, **kwargs)
        if path == root / 'TOOLS.md':
            os._exit(73)
        return result
    Path.write_text = stop
    upgrade_triade.upgrade_project(root, 'dev')
'''
if mode != 'clean':
    result = subprocess.run([sys.executable, '-c', child, sys.argv[1], str(root), mode, str(backup)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 73, (mode, result.returncode, result.stdout, result.stderr)
    assert page.read_bytes() == original[str(page.relative_to(root))]
    if mode != 'roadmap_after':
        assert roadmap.read_bytes() == original[str(roadmap.relative_to(root))]
    else:
        assert roadmap_io.parse_roadmap(roadmap)['frontmatter']['roadmap_schema'] == '2'
    if mode == 'restore':
        partial = root.parent / 'partial-restore'
        assert partial.is_dir() and list(partial.iterdir())
        try:
            project_recovery.restore(backup, partial)
            raise AssertionError('partial restore overwritten')
        except ValueError:
            pass
    current = published()
    assert current['chunks'] == legacy['chunks']
    assert current['chunk_vec'] == legacy['chunk_vec']
    assert dict(current['meta']).get('index_fingerprint') is None

# Retry the complete supported sequence, then repeat it to check stable identities.
for attempt in range(2):
    assert upgrade_triade.upgrade_project(root, 'dev') == 0
    assert (root / 'AGENTS.md').is_file()
    assert '@AGENTS.md' in (root / 'CLAUDE.md').read_text().splitlines()
    assert 'anja_memory' in json.loads((root / '.mcp.json').read_text())['mcpServers']
    data = roadmap_io.parse_roadmap(roadmap)
    roadmap_io.write_roadmap(roadmap, data)
    tasks = roadmap_io.list_tasks(roadmap_io.parse_roadmap(roadmap))
    assert len(tasks) == 3 and len({t['id'] for t in tasks}) == 3
    assert [(t['title'],t.get('owner')) for t in tasks[:2]] == [('Same task','alice'),('Same task','bob')]
    try:
        roadmap_io.resolve_task(roadmap_io.parse_roadmap(roadmap), 'same-task')
        raise AssertionError('ambiguous legacy ID accepted')
    except roadmap_io.PersistenceError as exc:
        assert exc.result['code'] == 'ambiguous_task_id'
    result = index_pipeline.refresh(root, kind='code')
    assert result['status'] == 'ready', result
    assert trust.status(page.read_text())['trust_tier'] == 'legacy'
    assert page.read_bytes() == original[str(page.relative_to(root))]
    assert json.loads((root / '.anjawiki/config.json').read_text()) == config
    assert (root/'AGENTS.src.md').read_bytes() == original['AGENTS.src.md']
    assert (root/'SOUL.md').read_bytes() == original['SOUL.md']
    db = sqlite3.connect(database)
    assert {r[0] for r in db.execute('SELECT DISTINCT kind FROM indexed_files')} == {'code','wiki'}
    fingerprint = json.loads(dict(db.execute('SELECT * FROM meta'))['index_fingerprint'])
    assert fingerprint['pipeline'] == code_db.PIPELINE_VERSION
    db.close()
    if attempt == 0:
        ids = [t['id'] for t in tasks]
        migrated = roadmap.read_bytes()
    else:
        assert [t['id'] for t in tasks] == ids
        assert roadmap.read_bytes() == migrated

# Both the original v0.30.0 data and the migrated IDs can be restored byte-for-byte.
for source, expected, name in [(backup, original, 'restored-legacy'), (Path(project_recovery.snapshot_project(root)), {'.anjawiki/wiki/roadmap.md': migrated}, 'restored-current')]:
    destination = root.parent / name
    result = project_recovery.restore(source, destination)
    assert result['index'] == 'rebuild_required'
    for rel, content in expected.items():
        assert (destination / rel).read_bytes() == content
    assert not (destination / '.anjawiki/code-index.db').exists()
    assert not (destination / '.anjawiki/.secrets.env').exists()
    assert not (destination / 'auth.py').exists()
    try:
        project_recovery.restore(source, destination)
        raise AssertionError('restore overwrote existing destination')
    except ValueError:
        pass
assert roadmap.read_bytes() == migrated
print('migration, crash recovery, rerun and restore OK:', mode)
"""


@pytest.mark.parametrize('mode', ['clean', 'upgrade', 'roadmap_before', 'roadmap_after', 'index', 'restore'])
def test_v030_migration_lifecycle(tmp_path, mode):
    result = subprocess.run([PYTHON, '-c', DRIVER, str(SCRIPTS), str(tmp_path), mode],
                            capture_output=True, text=True, timeout=90, env={**os.environ, **cov_env()})
    assert result.returncode == 0, result.stdout + result.stderr
