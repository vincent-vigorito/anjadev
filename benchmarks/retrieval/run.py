"""Offline retrieval benchmark. Run with a Python supporting sqlite-vec."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parents[1] / 'scripts'))
import index_pipeline  # noqa: E402

import code_search  # noqa: E402
import embed_providers  # noqa: E402


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def references(root, result):
    hits = []
    for item in result['results']:
        path = item['path']
        spans = ([(item['line_start'], item['line_end'])] if 'line_start' in item
                 else [(p['line'], p['line']) for p in item['preview']])
        source = root / path
        lines = len(source.read_text().splitlines()) if source.is_file() else 0
        hits.append(dict(path=path, spans=spans, valid=bool(spans) and all(
            1 <= a <= b <= lines for a, b in spans), revision=digest(source) if lines else None,
            preview=item['preview'], distance=item.get('distance')))
    return hits


def fuse(first, second, k=60):
    """Experimental file-level RRF; retain all evidence from both rankings."""
    scores, evidence = {}, {}
    for ranking in (first, second):
        seen = set()
        for hit in ranking:
            path = hit['path']
            if path not in seen:
                seen.add(path)
                scores[path] = scores.get(path, 0) + 1 / (k + len(seen))
            if path not in evidence:
                evidence[path] = dict(hit, spans=list(hit['spans']))
            else:
                evidence[path]['spans'].extend(hit['spans'])
                evidence[path]['valid'] &= hit['valid']
    return [evidence[p] for p in sorted(scores, key=lambda p: (-scores[p], p))][:5]


def evaluate(hits, relevant):
    ranks = [next((i for i, h in enumerate(hits, 1) if h['valid'] and h['path'] == r['path']
                   and any(a <= r['end'] and b >= r['start'] for a, b in h['spans'])), None)
             for r in relevant]
    return {'recall': {str(k): sum(r is not None and r <= k for r in ranks) / len(ranks)
                       for k in (1, 3, 5)} if ranks else None,
            'rr': 1 / min(r for r in ranks if r is not None) if any(ranks) else 0,
            'no_answer_correct': not hits if not relevant else None,
            'invalid_references': sum(not h['valid'] for h in hits)}


def percentile(values, p):
    return sorted(values)[max(0, math.ceil(len(values) * p) - 1)]


def aggregate(rows):
    answerable = [r for r in rows if r['recall'] is not None]
    absent = [r for r in rows if r['no_answer_correct'] is not None]
    return dict(queries=len(rows), answerable=len(answerable),
                recall={str(k): statistics.mean(r['recall'][str(k)] for r in answerable) for k in (1, 3, 5)},
                mrr=statistics.mean(r['rr'] for r in answerable),
                no_answer_accuracy=statistics.mean(r['no_answer_correct'] for r in absent) if absent else None,
                invalid_references=sum(r['invalid_references'] for r in rows),
                latency_ms_p50=statistics.median(r['latency_ms'] for r in rows),
                latency_ms_p95=percentile([r['latency_ms'] for r in rows], .95),
                returned_tokens_estimate_mean=statistics.mean(r['tokens_estimate'] for r in rows))


def setup(source, destination):
    shutil.copytree(source, destination)
    (destination / '.anjawiki/wiki').mkdir(parents=True)
    (destination / '.anjawiki/meta.yaml').write_text('name: benchmark\ntype: dev\n')
    # Prevent parent checkout discovery from influencing recency ranking.
    subprocess.run(['git', 'init', '-q', str(destination)], check=True, capture_output=True)
    index_pipeline.refresh(destination, 'code')
    assert index_pipeline.index_status(destination, embed_providers.get_provider())['status'] == 'ready'


def scenarios(temp):
    """Ten fixed edits, with changed behavior tested before and after refresh."""
    edits = [
        ('checkout', 'pricing.py', '>= 100', '>= 80', 'shipping_cost', 'shipping_cost(80) == 0'),
        ('checkout', 'pricing.py', 'else 8', 'else 5', 'shipping_cost', 'shipping_cost(20) == 5'),
        ('checkout', 'pricing.py', 'min(100,', 'min(50,', 'discounted_total', 'discounted_total(100, 90) == 50'),
        ('worker', 'queueing.py', 'min(60,', 'min(90,', 'retry_delay', 'retry_delay(10) == 90'),
        ('worker', 'queueing.py', '2 **', '3 **', 'retry_delay', 'retry_delay(2) == 9'),
        ('worker', 'queueing.py', 'now >= deadline', 'now > deadline', 'lease_expired', 'not lease_expired(10, 10)'),
        ('cache', 'storage.py', '< ttl', '<= ttl', 'is_fresh', 'is_fresh(120, 0, 120)'),
        ('cache', 'storage.py', '":"', '"/"', 'cache_key', 'cache_key("a", "x") == "a/x"'),
        ('cache', 'storage.py', 'return tenant +', 'return tenant.lower() +', 'cache_key', 'cache_key("A", "x") == "a:x"'),
        ('checkout', 'config.py', 'TIMEOUT = 30', 'TIMEOUT = 45', 'PAYMENT_TIMEOUT', 'PAYMENT_TIMEOUT == 45'),
    ]
    results = []
    for i, (project, file, old, new, query, assertion) in enumerate(edits):
        root = temp / f'scenario-{i}'
        setup(BASE / 'fixtures' / project, root)
        before = references(root, code_search.search_level_2(query, root, limit=5))
        source = root / file
        revision = digest(source)
        check = f'from {Path(file).stem} import {query}; assert {assertion}'
        def test(check=check, root=root):
            return subprocess.run([sys.executable, '-B', '-c', check], cwd=root,
                                  capture_output=True, timeout=15).returncode
        fails_before = test() != 0
        source.write_text(source.read_text().replace(old, new))
        stale = code_search.search_level_2(query, root)
        passes_after = test() == 0
        index_pipeline.refresh(root, 'code')
        after_result = code_search.search_level_2(query, root, limit=5)
        after = references(root, after_result)
        invalid = sum(not h['valid'] for h in before + after)
        fresh_content = any(h['path'] == file and new in h['preview'] for h in after)
        stale_hits = sum(h['path'] == file and old in h['preview'] for h in after)
        success = (fails_before and passes_after and stale.get('index_status') == 'stale'
                   and after_result.get('level') == 2 and any(h['path'] == file for h in before)
                   and any(h['path'] == file and h['revision'] != revision for h in after) and not invalid and fresh_content and not stale_hits)
        results.append(dict(id=i + 1, project=project, file=file, success=success,
                            fails_before=fails_before, passes_after=passes_after,
                            stale_detected=stale.get('index_status') == 'stale', invalid_references=invalid,
                            fresh_content=fresh_content, stale_hits=stale_hits,
                            before_revision=revision, after_revision=digest(source),
                            tokens_estimate=math.ceil(len(json.dumps(before + after)) / 4)))
    return results


def run():
    # This harness deliberately has no remote-provider mode or credential access.
    os.environ['ANJA_EMBED_PROVIDER'] = 'mock'
    dataset = json.loads((BASE / 'dataset.json').read_text())
    provider = embed_providers.get_provider()
    rows = []
    with tempfile.TemporaryDirectory(prefix='anja-retrieval-') as folder:
        temp = Path(folder)
        roots = {}
        for name in sorted({q['project'] for q in dataset['queries']}):
            roots[name] = temp / name
            setup(BASE / 'fixtures' / name, roots[name])
        for query in dataset['queries']:
            root = roots[query['project']]
            for phase in ('first_pass', 'repeat'):
                start = time.perf_counter()
                lexical = references(root, code_search.search_level_0(query['query'], root, limit=5))
                lexical_ms = (time.perf_counter() - start) * 1000
                start = time.perf_counter()
                vector_result = code_search.search_level_2(query['query'], root, limit=5)
                if vector_result.get('level') != 2:
                    raise RuntimeError(f'Vector baseline fell back: {vector_result}')
                vector = references(root, vector_result)
                vector_ms = (time.perf_counter() - start) * 1000
                start = time.perf_counter()
                hybrid = fuse(lexical, vector)
                hybrid_ms = lexical_ms + vector_ms + (time.perf_counter() - start) * 1000
                for method, hits, elapsed in [('lexical', lexical, lexical_ms), ('vector_mock', vector, vector_ms),
                                               ('hybrid_rrf60_mock', hybrid, hybrid_ms)]:
                    rows.append(dict(query_id=query['id'], split=query['split'], method=method, phase=phase,
                                     latency_ms=elapsed, tokens_estimate=math.ceil(len(json.dumps(hits)) / 4),
                                     hits=hits, **evaluate(hits, query['relevant'])))
        workflows = scenarios(temp)
    return dict(dataset_version=dataset['version'], dataset_sha256=digest(BASE / 'dataset.json'),
                source_revisions={str(p.relative_to(BASE)): digest(p) for p in sorted((BASE / 'fixtures').rglob('*'))
                                  if p.is_file()},
                runner_sha256=digest(Path(__file__)),
                implementation_sha256={name: digest(BASE.parents[1] / 'scripts' / name)
                                       for name in ('code_index.py', 'code_db.py', 'code_search.py', 'index_pipeline.py')},
                environment=dict(python=platform.python_version(), os=platform.platform(), machine=platform.machine(),
                                 processor=platform.processor(), provider=provider.name, model=provider.model,
                                 dimension=provider.dim, harness='deterministic-python-v1', llm=None),
                limitations=['Mock measures hashed lexical overlap, not semantic quality.',
                             'First pass is not a cold filesystem cache; index already built. No cache eviction.',
                             'Tokens are ceil(JSON characters / 4), not tokenizer usage. Mock embedding cost is zero.',
                             'RRF is an offline experiment, not a product search mode. No ranking tuning performed.',
                             'Scenarios use prescribed edits and assertions, not autonomous model programming.',
                             'Reference revisions are added by the harness, not returned by product search.',
                             'Fixture tests indicate explicit examples, not exhaustive behavioral coverage.'],
                embedding_cost_usd=0, thresholds=dataset['thresholds'],
                metrics={f'{method}/{split}/{phase}': aggregate([r for r in rows if r['method'] == method
                         and r['split'] == split and r['phase'] == phase])
                         for method in ('lexical', 'vector_mock', 'hybrid_rrf60_mock')
                         for split in ('development', 'evaluation') for phase in ('first_pass', 'repeat')},
                scenarios=workflows, rows=rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = run()
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'report': str(args.output), 'scenarios_passed': sum(s['success'] for s in report['scenarios']),
                      'metrics': report['metrics']}, indent=2))
