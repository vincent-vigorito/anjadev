"""Fixture-only benchmark: preview first, explicit snapshot approval for execution."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import index_pipeline  # noqa: E402
import index_policy  # noqa: E402
from run import BASE, aggregate, code_search, digest, evaluate, fuse, references  # noqa: E402

import code_index  # noqa: E402
import embed_providers  # noqa: E402

CORPUS = BASE / 'v2'


def plan(provider, model, repeats):
    if not 1 <= repeats <= 10:
        raise ValueError('repeats must be between 1 and 10')
    dataset = json.loads((CORPUS / 'dataset.json').read_text())
    files = []
    chunk_characters = 0
    for path in sorted((CORPUS / 'fixtures').rglob('*')):
        if path.is_symlink():
            raise ValueError('fixture symlinks are forbidden')
        if not path.is_file():
            continue
        if path.suffix not in ('.py', '.md'):
            raise ValueError('unexpected fixture file')
        content = path.read_text()
        chunks = code_index.chunk_text(content, path.suffix)
        chunk_characters += sum(len(c['content']) for c in chunks)
        files.append(dict(path=str(path.relative_to(CORPUS / 'fixtures')), bytes=path.stat().st_size,
                          sha256=digest(path), chunks=len(chunks)))
    requests = sum(math.ceil(f['chunks'] / 64) for f in files) + len(dataset['queries']) * repeats
    result = dict(dataset_version=dataset['version'], dataset_sha256=digest(CORPUS / 'dataset.json'),
                  provider=provider, model=model, repeats=repeats, files=files,
                  source_bytes=sum(f['bytes'] for f in files), query_count=len(dataset['queries']),
                  query_characters=sum(len(q['query']) for q in dataset['queries']),
                  max_embedding_batches=requests, max_http_requests=requests + 1,
                  max_embedding_texts=sum(f['chunks'] for f in files) + len(dataset['queries']) * repeats,
                  max_embedding_characters=chunk_characters + sum(len(q['query']) for q in dataset['queries']) * repeats,
                  extra_request='At most one model-dimension probe; no automatic retries.',
                  destination='remote' if provider in index_policy.REMOTE else 'local',
                  embedding_cost_usd=0 if provider == 'mock' else None,
                  cost_note='Real cost is unknown until provider usage/cost metadata is available; no price estimate.',
                  scope='Only listed fixture chunks and dataset queries; disposable project copies.')
    result['approval_sha256'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


class MeteredProvider:
    def __init__(self, provider, allowed, max_batches, max_texts, max_characters):
        self.name, self.model, self.dim = provider.name, provider.model, provider.dim
        self.provider, self.allowed, self.max_batches = provider, allowed, max_batches
        self.max_texts, self.max_characters = max_texts, max_characters
        self.batches = 0
        self.texts = 0
        self.characters = 0

    def embed(self, texts):
        if (self.batches >= self.max_batches or self.texts + len(texts) > self.max_texts
                or self.characters + sum(map(len, texts)) > self.max_characters
                or any(text not in self.allowed for text in texts)):
            raise ValueError('embedding request exceeds approved fixture scope or batch budget')
        self.batches += 1
        self.texts += len(texts)
        self.characters += sum(map(len, texts))
        return self.provider.embed(texts)


def execute(preview, approved_hash, observer=None):
    if approved_hash != preview['approval_sha256']:
        raise ValueError('preview hash mismatch; review the current preview before execution')
    # Recompute independently so a stale saved preview cannot authorize changed sources.
    if plan(preview['provider'], preview['model'], preview['repeats']) != preview:
        raise ValueError('fixture snapshot changed after preview')
    dataset_bytes = (CORPUS / 'dataset.json').read_bytes()
    if hashlib.sha256(dataset_bytes).hexdigest() != preview['dataset_sha256']:
        raise ValueError('dataset changed after approval')
    dataset = json.loads(dataset_bytes)
    allowed = {q['query'] for q in dataset['queries']}
    for file in preview['files']:
        path = CORPUS / 'fixtures' / file['path']
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != file['sha256']:
            raise ValueError('source changed after approval')
        allowed.update(c['content'] for c in code_index.chunk_text(content.decode('utf-8'), path.suffix))
    os.environ['ANJA_EMBED_PROVIDER'] = preview['provider']
    os.environ['ANJA_EMBED_MODEL'] = preview['model']
    if preview['provider'] == 'local':
        os.environ['HF_HUB_OFFLINE'] = '1'
    from secrets_loader import load_secrets
    load_secrets(BASE.parents[1])
    http_usage = []
    original_http = embed_providers._http_post_json

    def measured_http(url, headers, payload, timeout=60):
        if len(http_usage) >= preview['max_http_requests']:
            raise ValueError('HTTP request budget exceeded')
        event = {'completed': False}
        http_usage.append(event)
        data = original_http(url, headers, payload, timeout)
        event.update(completed=True, usage={key: value for key, value in data.get('usage', {}).items()
                                          if key in ('prompt_tokens', 'total_tokens', 'input_tokens', 'cost')})
        return data

    rows = []
    with patch.object(embed_providers, '_http_post_json', measured_http):
        provider = embed_providers.get_provider()
        if provider is None:
            raise ValueError('provider unavailable: configure its API key locally, never in the dataset')
        if (provider.name, provider.model) != (preview['provider'], preview['model']):
            raise ValueError('provider identity differs from approved preview')
        provider = MeteredProvider(provider, allowed, preview['max_embedding_batches'],
                                   preview['max_embedding_texts'], preview['max_embedding_characters'])
        with patch.object(embed_providers, 'get_provider', lambda: provider), tempfile.TemporaryDirectory(prefix='anja-v2-') as folder:
            roots = {}
            for name in sorted({q['project'] for q in dataset['queries']}):
                root = Path(folder) / name
                roots[name] = root
                shutil.copytree(CORPUS / 'fixtures' / name, root)
                (root / '.anjawiki/wiki').mkdir(parents=True)
                (root / '.anjawiki/meta.yaml').write_text('name: fixture\n')
                if preview['provider'] in index_policy.REMOTE:
                    (root / '.anjawiki/index-policy.json').write_text(json.dumps({'remote': {
                        'provider': preview['provider'], 'model': preview['model']}}))
                result = index_pipeline.refresh(root, 'code')
                if result.get('status') != 'ready':
                    raise RuntimeError('fixture indexing failed: ' + str(result.get('code', result.get('status'))))
            for repetition in range(preview['repeats']):
                for query in dataset['queries']:
                    root = roots[query['project']]
                    rankings, elapsed = {}, {}
                    for method, level in [('lexical', 0), ('vector', 2)]:
                        start = time.perf_counter()
                        result = code_search.code_search(query['query'], root=root, smart_level=level, limit=5,
                                                         max_preview_chars=2000)
                        elapsed[method] = (time.perf_counter() - start) * 1000
                        if result.get('level') != level or result.get('evidence', {}).get('rejected'):
                            raise RuntimeError('baseline fell back or returned invalid evidence')
                        rankings[method] = references(root, result)
                        rows.append(dict(id=query['id'], split=query['split'], category=query['category'],
                                         method=method, repetition=repetition, latency_ms=elapsed[method],
                                         tokens_estimate=math.ceil(len(json.dumps(result)) / 4),
                                         abstain=result['evidence']['abstain'], hits=rankings[method],
                                         **evaluate(rankings[method], query['relevant'])))
                    start = time.perf_counter()
                    hybrid = fuse(rankings['lexical'], rankings['vector'])
                    rows.append(dict(id=query['id'], split=query['split'], category=query['category'],
                                     method='hybrid_rrf60', repetition=repetition,
                                     latency_ms=sum(elapsed.values()) + (time.perf_counter() - start) * 1000,
                                     tokens_estimate=math.ceil(len(json.dumps(hybrid)) / 4), hits=hybrid,
                                     **evaluate(hybrid, query['relevant'])))
                    if observer is not None:
                        observer(rows)
    if plan(preview['provider'], preview['model'], preview['repeats']) != preview:
        raise ValueError('fixture corpus changed during run; discard results')
    costs = [e.get('usage', {}).get('cost') for e in http_usage]
    cost = (sum(costs) if costs and all(isinstance(v, (int, float)) for v in costs) else
            (0 if preview['provider'] == 'mock' else None))
    return dict(preview=preview, environment=dict(python=platform.python_version(), platform=platform.platform(),
                machine=platform.machine(), provider=provider.name, model=provider.model, dimension=provider.dim),
                implementation_sha256={name: digest(BASE.parents[1] / 'scripts' / name) for name in
                                       ('code_search.py', 'code_index.py', 'search_evidence.py', 'embed_providers.py')},
                runner_sha256=digest(Path(__file__)), embedding=dict(batches=provider.batches, texts=provider.texts,
                characters=provider.characters, http_usage=http_usage, reported_cost_usd=cost),
                metrics={f'{method}/{split}/pass-{rep}': aggregate([r for r in rows if r['method'] == method
                         and r['split'] == split and r['repetition'] == rep])
                         for method in ('lexical', 'vector', 'hybrid_rrf60')
                         for split in ('development', 'evaluation') for rep in range(preview['repeats'])},
                limitations=['No tuning or semantic distance calibration; all labels frozen before this run.',
                             'First pass follows indexing, not a cold-cache measurement.',
                             'Tokens estimated as ceil(JSON characters / 4). Hybrid payload excludes API metadata.',
                             'Lexical and hybrid rank files; vector ranks chunks. RRF remains offline only.',
                             'Abstention tests literal evidence, not semantic answerability.',
                             'Mock is not a semantic provider. Real billing may differ from reported usage metadata.'], rows=rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=['mock', 'openrouter', 'openai', 'voyage', 'local'], default='mock')
    parser.add_argument('--model')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--approved-preview')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    preview = plan(args.provider, args.model or index_policy.DEFAULT_MODELS[args.provider], args.repeats)
    if args.execute:
        result = execute(preview, args.approved_preview)
    else:
        result = preview
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'output': str(args.output), 'approval_sha256': preview['approval_sha256'],
                      'source_bytes': preview['source_bytes'], 'max_http_requests': preview['max_http_requests']}))
