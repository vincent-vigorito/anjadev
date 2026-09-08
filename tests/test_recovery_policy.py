"""Backup, restore e transcript: prove su copie isolate senza rete."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import project_recovery  # noqa: E402
import session_archive  # noqa: E402


def project(tmp_path):
    wiki = tmp_path / '.anjawiki/wiki'
    wiki.mkdir(parents=True)
    (wiki / 'roadmap.md').write_text('---\nroadmap_schema: 2\n---\n## Open\n- [ ] Task | id: task-123\n')
    (wiki / 'overview.md').write_text('Original knowledge')
    return wiki


def test_backup_restore_preserves_ids_and_refuses_overwrite(tmp_path):
    root = tmp_path / 'original'
    wiki = project(root)
    backup = Path(project_recovery.snapshot_project(root))
    original = (wiki / 'roadmap.md').read_bytes()
    (wiki / 'roadmap.md').write_text('broken migration')
    destination = tmp_path / 'restored'
    result = project_recovery.restore(backup, destination)
    assert result['index'] == 'rebuild_required'
    assert (destination / '.anjawiki/wiki/roadmap.md').read_bytes() == original
    assert (destination / '.anjawiki/wiki/overview.md').read_text() == 'Original knowledge'
    with pytest.raises(ValueError, match='new, nonexistent'):
        project_recovery.restore(backup, destination)
    assert (wiki / 'roadmap.md').read_text() == 'broken migration'


def test_corrupt_backup_is_rejected_before_destination_creation(tmp_path):
    root = tmp_path / 'original'
    project(root)
    backup = Path(project_recovery.snapshot_project(root))
    (backup / 'files/.anjawiki/wiki/roadmap.md').write_text('tampered')
    destination = tmp_path / 'restored'
    with pytest.raises(ValueError, match='checksum'):
        project_recovery.restore(backup, destination)
    assert not destination.exists()


def test_backup_failure_leaves_no_published_snapshot(tmp_path, monkeypatch):
    project(tmp_path)
    original = Path.write_bytes
    def fail(path, data):
        if path.name == 'manifest.json':
            raise OSError('disk full')
        return original(path, data)
    monkeypatch.setattr(Path, 'write_bytes', fail)
    with pytest.raises(OSError):
        project_recovery.snapshot_project(tmp_path)
    assert list((tmp_path / '.anjawiki/backups').iterdir()) == []


def test_transcript_default_optin_missing_and_retention(tmp_path, monkeypatch):
    wiki = project(tmp_path)
    journal = wiki / 'sessions/day/test.md'
    journal.parent.mkdir(parents=True)
    source = tmp_path / 'source.jsonl'
    source.write_text('{"message":"full transcript"}\n')
    text = f'---\ntitle: Session\ntranscript_path: {source}\n---\n## Summary\nJournal\n'
    journal.write_text(text)
    assert session_archive.archive(tmp_path, journal)['status'] == 'disabled'
    assert session_archive.availability(tmp_path, text)['status'] == 'external'
    monkeypatch.setenv('ANJA_ARCHIVE_TRANSCRIPTS','1')
    result = session_archive.archive(tmp_path, journal)
    copied = tmp_path / result['path']
    assert copied.read_bytes() == source.read_bytes()
    source.unlink()
    assert session_archive.availability(tmp_path, journal.read_text())['status'] == 'archived'
    os.utime(copied, (1,1))
    assert session_archive.purge(tmp_path)['files'] == [result['path']]
    assert copied.exists()
    session_archive.purge(tmp_path, apply=True)
    assert session_archive.availability(tmp_path, journal.read_text())['status'] == 'missing'
    assert session_archive.diagnose(tmp_path)['missing'] == 1
    assert 'Journal' in journal.read_text()


def test_diagnostics_does_not_construct_provider(tmp_path, monkeypatch):
    import project_diagnostics

    import embed_providers
    project(tmp_path)
    monkeypatch.setenv('ANJA_EMBED_PROVIDER','openrouter')
    monkeypatch.setenv('ANJA_EMBED_MODEL','unknown-model')
    monkeypatch.setenv('OPENROUTER_API_KEY','secret-value')
    monkeypatch.setattr(embed_providers, 'get_provider', lambda: pytest.fail('network-capable constructor'))
    result = project_diagnostics.diagnose(tmp_path)
    assert result['status'] == 'missing'
    assert result['provider_config']['dimension'] is None
    assert 'secret-value' not in str(result)
    assert not (tmp_path / '.anjawiki/code-index.db').exists()


def test_failed_embedding_migration_has_restorable_snapshot(tmp_path):
    from test_embed_mock import sqlite_vec_usable
    from test_index_pipeline import run
    if not sqlite_vec_usable():
        pytest.skip('sqlite-vec unavailable')
    run(tmp_path, r'''
import project_recovery
p.model = 'changed-model'
p.dim = 5
with patch.object(code_db, 'upsert_chunk', side_effect=RuntimeError('interrupted migration')):
    result = refresh()
assert result['status'] == 'failed'
backup = Path(result['backup'])
metadata = project_recovery.verify(backup)
assert dict(metadata['index_manifest']['meta'])['index_fingerprint'] == dict(before['meta'])['index_fingerprint']
destination = root / 'restored'
project_recovery.restore(backup, destination)
assert (destination / '.anjawiki/wiki/concepts/test.md').read_bytes() == page.read_bytes()
assert contents() == before
''')


def test_retention_keeps_pending_jobs_and_active_staging(tmp_path):
    from test_embed_mock import sqlite_vec_usable
    from test_index_pipeline import run
    if not sqlite_vec_usable():
        pytest.skip('sqlite-vec unavailable')
    run(tmp_path, r'''
import project_maintenance, wiki_jobs
job = wiki_jobs.enqueue(root, page, start=False)
db = wiki_jobs.connect(root)
db.execute("INSERT INTO jobs(path,snapshot,fingerprint,state,created,updated) VALUES ('old','hash','fp','done',1,1)")
db.commit()
db.close()
stage = root / '.anjawiki/.index-stage-abandoned'
stage.mkdir()
os.utime(stage, (1,1))
db = code_db.open_db(root / '.anjawiki', dim=3)
db.execute("INSERT INTO index_runs(id,kind,status,started,pid,details) VALUES ('active','code','building','old',?,'{}')", (os.getpid(),))
db.commit()
preview = project_maintenance.cleanup(root)
assert preview['jobs'] and not preview['staging'] and preview['staging_skipped_active_build']
assert wiki_jobs.status(root)['counts'] == {'done':1,'pending':1}
project_maintenance.cleanup(root, apply=True)
assert wiki_jobs.status(root)['counts'] == {'pending':1}
assert stage.exists()
db.execute("UPDATE index_runs SET status='failed' WHERE id='active'")
db.commit()
db.close()
project_maintenance.cleanup(root, apply=True)
assert not stage.exists()
''')


def test_corrupt_transcript_is_not_reported_recoverable(tmp_path, monkeypatch):
    wiki = project(tmp_path)
    journal = wiki / 'sessions/test.md'
    journal.parent.mkdir()
    source = tmp_path / 'source.jsonl'
    source.write_text('original')
    journal.write_text(f'---\ntranscript_path: {source}\n---\nJournal')
    monkeypatch.setenv('ANJA_ARCHIVE_TRANSCRIPTS','1')
    result = session_archive.archive(tmp_path, journal)
    (tmp_path / result['path']).write_text('corrupt')
    assert session_archive.availability(tmp_path, journal.read_text()) == {'status':'archive_corrupt','recoverable':False}


def test_retention_does_not_remove_unowned_jsonl(tmp_path):
    directory = tmp_path / '.anjawiki/transcripts'
    directory.mkdir(parents=True)
    user_file = directory / 'my-data.jsonl'
    user_file.write_text('user data')
    os.utime(user_file, (1,1))
    assert session_archive.purge(tmp_path, apply=True)['files'] == []
    assert user_file.exists()
