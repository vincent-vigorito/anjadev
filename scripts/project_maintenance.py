"""Retention esplicita; preview di default, mai job pendenti o backup del wiki."""
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

import session_archive


def cleanup(root, days=30, apply=False):
    if days < 1:
        raise ValueError('retention days must be positive')
    root = Path(root).resolve()
    directory = root / '.anjawiki'
    if not directory.resolve().is_relative_to(root):
        raise ValueError('state directory escapes project')
    cutoff = time.time() - days * 86400
    report = {'dry_run':not apply, 'transcripts':session_archive.purge(root, days, apply), 'jobs':[], 'staging':[]}
    queue = directory / 'wiki-jobs.db'
    if queue.is_symlink() or (directory / 'code-index.db').is_symlink():
        raise ValueError('maintenance refuses symlink databases')
    if queue.exists():
        db = sqlite3.connect(queue.as_uri() + ('?mode=rw' if apply else '?mode=ro'), uri=True)
        try:
            condition = "state IN ('done','failed','superseded') AND updated<?"
            report['jobs'] = [r[0] for r in db.execute('SELECT id FROM jobs WHERE ' + condition, (cutoff,))]
            if apply:
                db.execute('DELETE FROM jobs WHERE ' + condition, (cutoff,))
                db.commit()
        finally:
            db.close()
    active = False
    index = directory / 'code-index.db'
    if index.exists():
        db = sqlite3.connect(index.as_uri() + '?mode=ro', uri=True)
        try:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='index_runs'").fetchone():
                for row in db.execute("SELECT pid FROM index_runs WHERE status='building'"):
                    try:
                        os.kill(row[0], 0)
                        active = True
                    except ProcessLookupError:
                        pass
                    except (TypeError, PermissionError):
                        active = True
        finally:
            db.close()
    if not active:
        for path in directory.glob('.index-stage-*'):
            if path.is_dir() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                report['staging'].append(path.name)
                if apply:
                    shutil.rmtree(path)
    report['staging_skipped_active_build'] = active
    return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(cleanup(args.root, args.days, args.apply)))
    except Exception as exc:
        print(json.dumps({'error':str(exc), 'code':'maintenance_failed'}))
        raise SystemExit(1) from None
