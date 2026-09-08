"""Search/inspect/test/refresh/decision-diff workflows on disposable repositories."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parents[1] / 'scripts'))
from code_inspect import inspect_source  # noqa: E402
from decision_compare import compare_decision  # noqa: E402
from index_pipeline import refresh  # noqa: E402

import code_search  # noqa: E402


def run():
    os.environ['ANJA_EMBED_PROVIDER'] = 'mock'
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_AUTHOR_NAME': 'Fixture', 'GIT_COMMITTER_NAME': 'Fixture',
           'GIT_AUTHOR_EMAIL': 'fixture@example.invalid', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid',
           'GIT_AUTHOR_DATE': '2026-01-01T00:00:00+00:00', 'GIT_COMMITTER_DATE': '2026-01-01T00:00:00+00:00'}
    cases = [
        ('checkout', 'pricing.py', 'shipping_cost', '>= 100', '>= 80', 'test_pricing.py',
         'shipping_cost(99) == 8', 'shipping_cost(99) == 0', 'shipping_cost(80) == 0'),
        ('worker', 'queueing.py', 'lease_expired', 'now >= deadline', 'now > deadline', 'test_queueing.py',
         'assert lease_expired(10, 10)', 'assert not lease_expired(10, 10)', 'not lease_expired(10, 10)'),
        ('cache', 'storage.py', 'cache_key', '":"', '"/"', 'test_storage.py',
         'def test_tenant_isolation():', 'def test_tenant_isolation():\n    assert cache_key("a", "x") == "a/x"',
         'cache_key("a", "x") == "a/x"'),
    ]
    reports = []
    with tempfile.TemporaryDirectory(prefix='anja-decision-workflows-') as folder:
        for project, source, symbol, old, new, test_file, old_test, new_test, assertion in cases:
            root = Path(folder) / project
            shutil.copytree(BASE / 'v2/fixtures' / project, root)
            (root / '.anjawiki/wiki').mkdir(parents=True)
            (root / '.anjawiki/meta.yaml').write_text('name: fixture\n')
            def git(*args, root=root):
                return subprocess.check_output(['git', '-C', str(root), *args], env=env).decode().strip()
            git('init', '--quiet', '--template=')
            git('add', '.')
            git('-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'decision baseline fixture')
            commit = git('rev-parse', 'HEAD')
            assert refresh(root, 'code')['status'] == 'ready'
            found = code_search.code_search(symbol, root=root, smart_level=0)
            paths = {h['path'] for h in found['results']}
            assert {source, test_file} <= paths
            hit = next(h for h in found['results'] if h['path'] == source)
            inspected = inspect_source(root, source, symbol, expected_revision=hit['evidence']['source_revision'])
            assert 'error' not in inspected
            tests = inspect_source(root, test_file)
            assert any(o['kind'] == 'Assert' for o in tests['operations'])
            check = f'from {Path(source).stem} import {symbol}; assert {assertion}'
            before = subprocess.run([sys.executable, '-B', '-c', check], cwd=root, env=env, capture_output=True)
            assert before.returncode != 0
            (root / source).write_text((root / source).read_text().replace(old, new))
            (root / test_file).write_text((root / test_file).read_text().replace(old_test, new_test))
            stale = code_search.code_search(symbol, root=root, smart_level=2)
            assert stale['index_status'] == 'stale' and stale['level'] == 0
            assert inspect_source(root, source, symbol, expected_revision=hit['evidence']['source_revision'])['code'] == 'revision_conflict'
            test_code = f'import runpy; ns = runpy.run_path({test_file!r}); [f() for n, f in ns.items() if n.startswith("test_") and callable(f)]'
            subprocess.run([sys.executable, '-B', '-c', test_code], cwd=root, env=env, check=True, capture_output=True)
            subprocess.run([sys.executable, '-B', '-c', check], cwd=root, env=env, check=True, capture_output=True)
            assert refresh(root, 'code')['status'] == 'ready'
            after = code_search.code_search(symbol, root=root, smart_level=2)
            assert after['level'] == 2 and not after['evidence']['rejected']
            diff = compare_decision(root, 'decision.md', commit, [source, test_file], max_chars=2000)
            assert diff['status'] == 'compared' and diff['decision']['status'] == 'unchanged'
            assert all(f['status'] == 'modified' for f in diff['files'])
            assert diff['decision_fulfillment'] == 'not_assessed' and diff['diff_chars'] <= 2000
            reports.append(dict(project=project, success=True, search_paths=sorted(paths),
                                new_requirement_failed_before=True, tests_executed=True, tests_passed=True,
                                stale_detected=True, stale_revision_rejected=True, refreshed=True, comparison=diff))
    return dict(workflows=reports, passed=len(reports), outbound_requests=0,
                limitation='Prescribed fixture edits and assertions; no autonomous programming or semantic compliance verdict.')


if __name__ == '__main__':
    report = run()
    (BASE / 'decision-workflow-results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'passed': report['passed'], 'outbound_requests': report['outbound_requests']}))
