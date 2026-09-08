"""Offline calibration experiment; never changes product search policy."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import run_v2 as runner
from run import evaluate

CORPUS = Path(__file__).resolve().parent / 'calibration'


def fit(development):
    if not development or any(r['split'] != 'development' for r in development):
        raise ValueError('fit accepts development rows only')
    negatives = [r for r in development if r['recall'] is None]
    if not negatives:
        raise ValueError('development negatives required')
    distances = [h['distance'] for r in negatives for h in r['hits']]
    if not distances or any(d is None for d in distances):
        raise ValueError('negative distances required')
    return dict(threshold=min(distances), comparison='distance < threshold',
                method='largest strict cutoff accepting no development negative hits',
                training_ids=[r['id'] for r in development],
                limitations='Distances rounded to four decimals by code.search; not a probability or universal threshold.')


def filter_hits(hits, threshold):
    return [h for h in hits if h['distance'] < threshold]


def assess(rows, queries, threshold):
    by_id = {q['id']: q for q in queries}
    results = []
    for row in rows:
        if row['method'] != 'vector':
            continue
        hits = filter_hits(row['hits'], threshold)
        results.append(dict(id=row['id'], split=row['split'], accepted=bool(hits),
                            relevant_found=evaluate(hits, by_id[row['id']]['relevant'])['rr'] > 0,
                            **evaluate(hits, by_id[row['id']]['relevant'])))
    summary = {}
    for split in ('development', 'evaluation'):
        subset = [r for r in results if r['split'] == split]
        positives = [r for r in subset if r['recall'] is not None]
        negatives = [r for r in subset if r['recall'] is None]
        summary[split] = dict(answerable=len(positives), no_answer=len(negatives),
                             answerable_accepted=sum(r['accepted'] for r in positives),
                             answerable_with_relevant_hit=sum(r['relevant_found'] for r in positives),
                             false_accepts=sum(r['accepted'] for r in negatives),
                             correct_abstentions=sum(not r['accepted'] for r in negatives),
                             recall_at_5=sum(r['recall']['5'] for r in positives) / len(positives),
                             mrr=sum(r['rr'] for r in positives) / len(positives))
    return dict(summary=summary, rows=results)


class FreezeThreshold:
    def __init__(self, queries, output):
        splits = [q['split'] for q in queries]
        count = splits.count('development')
        if splits != ['development'] * count + ['evaluation'] * (len(splits) - count):
            raise ValueError('all development queries must precede evaluation')
        self.count, self.output, self.frozen = count, output, None

    def __call__(self, rows):
        if self.frozen is not None:
            return
        vectors = [r for r in rows if r['method'] == 'vector']
        if len(vectors) == self.count:
            self.frozen = fit(vectors)
            self.output.write_text(json.dumps(self.frozen, indent=2) + '\n')
            print(json.dumps({'stage': 'threshold_frozen_before_evaluation', 'threshold': self.frozen['threshold']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--approved-preview')
    parser.add_argument('--provider', default='openrouter', choices=['mock', 'openrouter'])
    parser.add_argument('--model', default='google/gemini-embedding-2')
    args = parser.parse_args()
    runner.CORPUS = CORPUS
    preview = runner.plan(args.provider, args.model, 1)
    (CORPUS / 'preview.json').write_text(json.dumps(preview, indent=2) + '\n')
    if not args.execute:
        print(json.dumps({'approval_sha256': preview['approval_sha256'], 'max_http_requests': preview['max_http_requests']}))
    else:
        queries = json.loads((CORPUS / 'dataset.json').read_text())['queries']
        freeze = FreezeThreshold(queries, CORPUS / 'threshold.json')
        report = runner.execute(preview, args.approved_preview, observer=freeze)
        if freeze.frozen is None:
            raise RuntimeError('threshold was not frozen')
        (CORPUS / 'raw-results.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
        calibrated = assess(report['rows'], queries, freeze.frozen['threshold'])
        (CORPUS / 'evaluation.json').write_text(json.dumps(calibrated, indent=2) + '\n')
        print(json.dumps(calibrated['summary'], indent=2))
