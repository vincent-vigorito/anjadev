"""Coda locale degli embedding wiki; un worker per progetto, retry finiti."""
from __future__ import annotations

import fcntl
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import index_pipeline
import index_policy

import code_db
import embed_providers

MAX_ATTEMPTS = 3
LEASE_SECONDS = 300


def connect(root):
    root = Path(root).resolve()
    if (root / '.anjawiki').is_symlink() or (root / '.anjawiki/wiki-jobs.db').is_symlink():
        raise ValueError('queue state must not be a symlink')
    db = sqlite3.connect(str(Path(root) / '.anjawiki/wiki-jobs.db'), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY, path TEXT NOT NULL, snapshot TEXT NOT NULL,
        fingerprint TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        lease_until REAL, next_attempt REAL NOT NULL DEFAULT 0, error TEXT,
        created REAL NOT NULL, updated REAL NOT NULL)''')
    db.commit()
    return db


def snapshot(path):
    return index_pipeline.snapshot_file(path)[0]['hash'] if path.is_file() else 'missing'


def enqueue(root, path, start=True, retry_failed=False):
    root, path = Path(root).resolve(), Path(path).absolute()
    index_pipeline.discover(root, 'wiki', single=path)
    index_policy.authorize(root)
    provider = embed_providers.get_provider()
    fp = code_db.fingerprint(provider) if provider else 'unavailable'
    db = connect(root)
    try:
        stamp = time.time()
        db.execute('BEGIN IMMEDIATE')
        digest = snapshot(path)
        db.execute("UPDATE jobs SET state='superseded',updated=? WHERE path=? AND state IN ('pending','retry') AND (snapshot!=? OR fingerprint!=?)",
                   (stamp, str(path), digest, fp))
        row = db.execute('SELECT * FROM jobs WHERE path=? ORDER BY id DESC LIMIT 1', (str(path),)).fetchone()
        if row and row['snapshot'] == digest and row['fingerprint'] == fp and row['state'] != 'superseded' and not (retry_failed and row['state'] == 'failed'):
            job_id = row['id']
        else:
            job_id = db.execute("INSERT INTO jobs(path,snapshot,fingerprint,state,created,updated) VALUES (?,?,?,'pending',?,?)",
                                (str(path), digest, fp, stamp, stamp)).lastrowid
        db.commit()
    finally:
        db.close()
    if start:
        launch(root)
    return job_id


def enqueue_scan(root):
    root = Path(root).resolve()
    paths = set(index_pipeline.discover(root, 'wiki'))
    index = root / '.anjawiki/code-index.db'
    if index.exists():
        db = sqlite3.connect(index.as_uri() + '?mode=ro', uri=True)
        try:
            paths.update(Path(r[0]) for r in db.execute("SELECT DISTINCT file_path FROM chunks WHERE kind='wiki'")
                         if '/sessions/' not in r[0])
        finally:
            db.close()
    for path in sorted(paths):
        enqueue(root, path, start=False)
    launch(root)


def _lock(root):
    root = Path(root).resolve()
    if (root / '.anjawiki').is_symlink() or (root / '.anjawiki/wiki-worker.lock').is_symlink():
        raise ValueError('worker state must not be a symlink')
    fd = os.open(str(Path(root) / '.anjawiki/wiki-worker.lock'), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def launch(root):
    fd = _lock(root)
    if fd is None:
        return
    try:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(root), '--worker', '--lock-fd', str(fd)],
                         pass_fds=(fd,), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        db = connect(root)
        try:
            db.execute("UPDATE jobs SET error=? WHERE state IN ('pending','retry')", ("worker launch failed: " + str(exc),))
            db.commit()
        finally:
            db.close()
        raise
    finally:
        os.close(fd)


def run_job(root, job):
    path = Path(job['path'])
    index_pipeline.discover(Path(root).resolve(), 'wiki', single=path)
    index_policy.authorize(root)
    provider = embed_providers.get_provider()
    if snapshot(path) != job['snapshot']:
        return {'status': 'superseded', 'error': 'page changed since enqueue'}
    if provider is None:
        return {'status': 'failed', 'error': 'embedding provider unavailable'}
    if code_db.fingerprint(provider) != job['fingerprint']:
        return {'status': 'superseded', 'error': 'embedding fingerprint changed since enqueue'}
    return index_pipeline.refresh(root, kind='wiki', single=path,
                                  expected_snapshot=job['snapshot'], expected_fingerprint=job['fingerprint'])


def finish(db, job_id, attempts, outcome):
    state = outcome.get('status')
    if state in ('ready', 'partial') and not outcome.get('error'):
        state = 'done'
    elif state in ('stale', 'superseded'):
        state = 'superseded'
    else:
        state = 'failed' if attempts >= MAX_ATTEMPTS else 'retry'
    db.execute("UPDATE jobs SET state=?,lease_until=NULL,next_attempt=?,error=?,updated=? WHERE id=? AND state='running'",
               (state, time.time() + 2 ** attempts, outcome.get('error'), time.time(), job_id))
    db.commit()


def worker(root, lock_fd=None):
    root = Path(root).resolve()
    fd = _lock(root) if lock_fd is None else lock_fd
    if fd is None:
        return
    db = connect(root)
    try:
        # Possedere il flock dimostra che il worker precedente non è più attivo.
        db.execute("UPDATE jobs SET state=CASE WHEN attempts>=? THEN 'failed' ELSE 'retry' END, lease_until=NULL,error='worker interrupted',updated=? WHERE state='running'",
                   (MAX_ATTEMPTS, time.time()))
        db.commit()
        while True:
            row = db.execute("SELECT * FROM jobs WHERE state IN ('pending','retry') ORDER BY next_attempt,id LIMIT 1").fetchone()
            if row is None:
                break
            delay = row['next_attempt'] - time.time()
            if delay > 0:
                time.sleep(min(delay, 1))
                continue
            stamp = time.time()
            changed = db.execute("UPDATE jobs SET state='running',attempts=attempts+1,lease_until=?,updated=? WHERE id=? AND state IN ('pending','retry')",
                                 (stamp + LEASE_SECONDS, stamp, row['id'])).rowcount
            db.commit()
            if not changed:
                continue
            try:
                result = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(root), '--job', str(row['id']), '--lock-fd', str(fd)],
                                        capture_output=True, text=True, timeout=LEASE_SECONDS, pass_fds=(fd,))
                outcome = json.loads(result.stdout) if result.returncode == 0 else {'status': 'failed', 'error': result.stderr[-2000:]}
            except Exception as exc:
                outcome = {'status': 'failed', 'error': str(exc)}
            finish(db, row['id'], row['attempts'] + 1, outcome)

    finally:
        db.close()
        os.close(fd)
    # Chiude la finestra fra ultimo SELECT e rilascio del lock.
    db = connect(root)
    try:
        pending = db.execute("SELECT 1 FROM jobs WHERE state IN ('pending','retry') LIMIT 1").fetchone()
    finally:
        db.close()
    if pending:
        launch(root)


def retry_failed(root):
    db = connect(root)
    try:
        paths = [r[0] for r in db.execute("SELECT path FROM jobs WHERE id IN (SELECT max(id) FROM jobs GROUP BY path) AND state='failed'")]
    finally:
        db.close()
    for path in paths:
        enqueue(root, Path(path), start=False, retry_failed=True)
    launch(root)


def _timeout(_signal, _frame):
    raise TimeoutError('embedding job exceeded its lease')


def status(root):
    path = Path(root) / '.anjawiki/wiki-jobs.db'
    if not path.exists():
        return {'counts': {}, 'jobs': []}
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        return {'counts': dict(db.execute('SELECT state,count(*) FROM jobs GROUP BY state')),
                'jobs': [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY updated DESC LIMIT 20')]}
    finally:
        db.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--retry-failed', action='store_true')
    parser.add_argument('--lock-fd', type=int)
    parser.add_argument('--job', type=int)
    parser.add_argument('--single', type=Path)
    args = parser.parse_args()
    import secrets_loader
    secrets_loader.load_secrets(args.root)
    if args.job is not None:
        connection = connect(args.root)
        job = connection.execute('SELECT * FROM jobs WHERE id=?', (args.job,)).fetchone()
        connection.close()
        signal.signal(signal.SIGALRM, _timeout)
        signal.alarm(LEASE_SECONDS)
        try:
            outcome = run_job(args.root, job)
        except Exception as exc:
            outcome = {'status': 'failed', 'error': str(exc)}
        finally:
            signal.alarm(0)
        connection = connect(args.root)
        try:
            finish(connection, job['id'], job['attempts'], outcome)
        finally:
            connection.close()
        print(json.dumps(outcome), flush=True)
        if args.lock_fd is not None:
            os.close(args.lock_fd)
        # Se il parent è morto, il figlio conclude il job e risveglia gli altri.
        launch(args.root)
    elif args.status:
        print(json.dumps(status(args.root)))
    elif args.retry_failed:
        retry_failed(args.root)
    elif args.worker:
        worker(args.root, args.lock_fd)
    elif args.single:
        enqueue(args.root, args.single)
    else:
        enqueue_scan(args.root)
