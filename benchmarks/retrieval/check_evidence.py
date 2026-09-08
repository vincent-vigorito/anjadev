"""Measure conservative evidence decisions through the public search entry point."""
import argparse
import json
import platform
import tempfile
from pathlib import Path

from run import BASE, code_search, digest, evaluate, references, setup


def run():
    import os
    os.environ['ANJA_EMBED_PROVIDER'] = 'mock'
    dataset = json.loads((BASE / 'dataset.json').read_text())
    rows = []
    with tempfile.TemporaryDirectory(prefix='anja-evidence-') as folder:
        roots = {}
        for name in sorted({q['project'] for q in dataset['queries']}):
            roots[name] = Path(folder) / name
            setup(BASE / 'fixtures' / name, roots[name])
        for query in dataset['queries']:
            for level in (0, 2):
                result = code_search.code_search(query['query'], root=roots[query['project']], smart_level=level,
                                                 limit=5, max_preview_chars=400)
                assert result.get('level') == level, result
                evidence = result['evidence']
                assert evidence['preview_chars'] <= 400
                hits = references(roots[query['project']], result)
                metrics = evaluate(hits, query['relevant'])
                assert metrics['invalid_references'] == 0
                assert not evidence['rejected'], evidence
                rows.append(dict(id=query['id'], split=query['split'], level=level,
                                 answerable=bool(query['relevant']), count=result['count'],
                                 evidence=evidence, retrieval_metrics=metrics))
    return dict(environment=dict(python=platform.python_version(), platform=platform.platform(),
                                 provider='mock', model='mock-bow', harness='public-code-search-evidence-v1'),
                implementation_sha256={name: digest(BASE.parents[1] / 'scripts' / name)
                                       for name in ('search_evidence.py', 'code_search.py')},
                dataset_sha256=digest(BASE / 'dataset.json'), rows=rows,
                summary={str(level): dict(
                    no_answer_abstained=sum(r['evidence']['abstain'] for r in rows if r['level'] == level and not r['answerable']),
                    no_answer_total=sum(not r['answerable'] for r in rows if r['level'] == level),
                    answerable_abstained=sum(r['evidence']['abstain'] for r in rows if r['level'] == level and r['answerable']),
                    answerable_total=sum(r['answerable'] for r in rows if r['level'] == level)) for level in (0, 2)},
                limitations=['Abstention means no literal evidence among returned spans, not calibrated semantic no-answer.',
                             'Candidates remain visible; retrieval no-answer metrics still count those hits.',
                             'Paraphrases can trigger abstention even when relevant candidates exist.'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = run()
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report['summary'], indent=2))
