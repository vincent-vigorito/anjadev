"""Job persistenti, dedup e recovery con SQLite/vec reale in subprocess."""
import pytest
from test_embed_mock import sqlite_vec_usable
from test_index_pipeline import run

pytestmark = pytest.mark.skipif(not sqlite_vec_usable(), reason='sqlite-vec extension unavailable')


def test_dedup_supersede_and_return_to_previous_content(tmp_path):
    run(tmp_path, r'''
import wiki_jobs as jobs
first = jobs.enqueue(root, page, start=False)
assert jobs.enqueue(root, page, start=False) == first
original = page.read_text()
page.write_text(original + 'B\n')
second = jobs.enqueue(root, page, start=False)
assert second != first
page.write_text(original)
third = jobs.enqueue(root, page, start=False)
assert third not in (first, second)
s = jobs.status(root)
assert s['counts'] == {'pending':1, 'superseded':2}, s
''')


def test_old_job_cannot_publish_after_newer_job(tmp_path):
    run(tmp_path, r'''
import wiki_jobs as jobs
first = jobs.enqueue(root, page, start=False)
db = jobs.connect(root)
old = dict(db.execute('SELECT * FROM jobs WHERE id=?', (first,)).fetchone())
original = p.embed
entered = False
page.write_text(page.read_text() + 'changed before old job starts\n')
assert jobs.run_job(root, old)['status'] == 'superseded'
first = jobs.enqueue(root, page, start=False)
old = dict(db.execute('SELECT * FROM jobs WHERE id=?', (first,)).fetchone())
def racing(texts):
    global entered
    if not entered:
        entered = True
        page.write_text(page.read_text() + 'newest revision\n')
        second = jobs.enqueue(root, page, start=False)
        newer = dict(db.execute('SELECT * FROM jobs WHERE id=?', (second,)).fetchone())
        assert jobs.run_job(root, newer)['status'] == 'partial'
    return original(texts)
p.embed = racing
r = jobs.run_job(root, old)
assert r['status'] == 'superseded', r
assert any('newest revision' in row[5] for row in contents()['chunks'])
db.close()
''')


def test_real_worker_dedup_and_deletion(tmp_path):
    run(tmp_path, r'''
import wiki_jobs as jobs
p = pipeline.embed_providers.MockProvider()
os.environ['ANJA_EMBED_PROVIDER'] = 'mock'
first = jobs.enqueue(root, page, start=False)
jobs.worker(root)
s = jobs.status(root)
assert s['counts'] == {'done':1}, s
assert s['jobs'][0]['attempts'] == 1
assert jobs.enqueue(root, page, start=False) == first
jobs.worker(root)
assert jobs.status(root)['jobs'][0]['attempts'] == 1
page.unlink()
jobs.enqueue(root, page, start=False)
jobs.worker(root)
assert jobs.status(root)['counts'] == {'done':2}
assert not [r for r in contents()['chunks'] if r[-1] == 'wiki']
''')


def test_worker_death_recovered_with_finite_attempt_count(tmp_path):
    run(tmp_path, r'''
import subprocess
import wiki_jobs as jobs
p = pipeline.embed_providers.MockProvider()
os.environ['ANJA_EMBED_PROVIDER'] = 'mock'
jobs.enqueue(root, page, start=False)
child = """
import sys, os
sys.path.insert(0, sys.argv[1])
import wiki_jobs
wiki_jobs.subprocess.run = lambda *a, **kw: os._exit(71)
wiki_jobs.worker(sys.argv[2])
"""
r = subprocess.run([sys.executable, '-c', child, sys.argv[1], str(root)], timeout=10)
assert r.returncode == 71
assert jobs.status(root)['counts'] == {'running':1}
jobs.worker(root)
s = jobs.status(root)
assert s['counts'] == {'done':1}, s
assert s['jobs'][0]['attempts'] == 2
''')


def test_timeout_retries_are_bounded_and_visible(tmp_path):
    run(tmp_path, r'''
import subprocess
import wiki_jobs as jobs
jobs.enqueue(root, page, start=False)
clock = 100000000000.
def tick():
    global clock
    clock += 1000
    return clock
with patch.object(jobs.time, 'time', side_effect=tick), patch.object(jobs.subprocess, 'run', side_effect=subprocess.TimeoutExpired('embedding', 300)) as calls:
    jobs.worker(root)
    assert calls.call_count == 3
s = jobs.status(root)
assert s['counts'] == {'failed':1}, s
assert s['jobs'][0]['attempts'] == 3 and 'timed out' in s['jobs'][0]['error']
with patch.object(jobs.subprocess, 'run') as calls:
    jobs.worker(root)
    assert calls.call_count == 0
''')


def test_multiple_worker_processes_claim_job_once(tmp_path):
    run(tmp_path, r'''
import subprocess
import wiki_jobs as jobs
p = pipeline.embed_providers.MockProvider()
os.environ['ANJA_EMBED_PROVIDER'] = 'mock'
jobs.enqueue(root, page, start=False)
processes = [subprocess.Popen([sys.executable, str(Path(sys.argv[1]) / 'wiki_jobs.py'), str(root), '--worker']) for _ in range(3)]
for process in processes:
    assert process.wait(timeout=20) == 0
s = jobs.status(root)
assert s['counts'] == {'done':1} and s['jobs'][0]['attempts'] == 1, s
''')


def test_child_finishes_and_resumes_queue_after_parent_death(tmp_path):
    run(tmp_path, r'''
import subprocess, time
import wiki_jobs as jobs
p = pipeline.embed_providers.MockProvider()
os.environ['ANJA_EMBED_PROVIDER'] = 'mock'
second_page = page.with_name('second.md')
second_page.write_text(page.read_text() + 'Second page\n')
jobs.enqueue(root, page, start=False)
jobs.enqueue(root, second_page, start=False)
parent = """
import sys, os, subprocess
sys.path.insert(0, sys.argv[1])
import wiki_jobs

def orphan(command, **kwargs):
    wrapper = 'import time,runpy,sys,pathlib; time.sleep(0.2); sys.argv=sys.argv[1:]; sys.path.insert(0,str(pathlib.Path(sys.argv[0]).parent)); runpy.run_path(sys.argv[0], run_name="__main__")'
    subprocess.Popen([sys.executable, '-c', wrapper, *command[1:]], pass_fds=kwargs['pass_fds'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os._exit(71)
wiki_jobs.subprocess.run = orphan
wiki_jobs.worker(sys.argv[2])
"""
r = subprocess.run([sys.executable, '-c', parent, sys.argv[1], str(root)], timeout=10)
assert r.returncode == 71
end = time.monotonic() + 10
while time.monotonic() < end:
    s = jobs.status(root)
    if s['counts'] == {'done':2}:
        break
    time.sleep(.05)
assert s['counts'] == {'done':2}, s
assert all(job['attempts'] == 1 for job in s['jobs']), s
''')


def test_failed_job_needs_explicit_retry_and_keeps_history(tmp_path):
    run(tmp_path, r'''
import wiki_jobs as jobs
first = jobs.enqueue(root, page, start=False)
db = jobs.connect(root)
db.execute("UPDATE jobs SET state='failed', attempts=3, error='offline' WHERE id=?", (first,))
db.commit()
db.close()
assert jobs.enqueue(root, page, start=False) == first
with patch.object(jobs, 'launch'):
    jobs.retry_failed(root)
s = jobs.status(root)
assert s['counts'] == {'failed':1, 'pending':1}, s
assert next(j for j in s['jobs'] if j['state'] == 'pending')['attempts'] == 0
''')


def test_enqueue_reads_snapshot_after_obtaining_queue_transaction(tmp_path):
    run(tmp_path, r'''
import wiki_jobs as jobs
original = jobs.connect
entered = False
newer = None
def delayed(root):
    global entered, newer
    if not entered:
        entered = True
        page.write_text(page.read_text() + 'Newest before queue transaction\n')
        newer = jobs.enqueue(root, page, start=False)
    return original(root)
with patch.object(jobs, 'connect', side_effect=delayed):
    older = jobs.enqueue(root, page, start=False)
assert older == newer
s = jobs.status(root)
assert s['counts'] == {'pending':1}, s
assert s['jobs'][0]['snapshot'] == jobs.snapshot(page)
''')
