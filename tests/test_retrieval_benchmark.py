"""Guard the benchmark's labels and metrics, independently of observed quality."""
import importlib.util
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / 'benchmarks/retrieval'
spec = importlib.util.spec_from_file_location('retrieval_benchmark', BASE / 'run.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_dataset_has_frozen_holdout_and_valid_labels():
    data = json.loads((BASE / 'dataset.json').read_text())
    queries = data['queries']
    assert len(queries) >= 40
    assert len({q['id'] for q in queries}) == len(queries)
    assert len({q['project'] for q in queries}) >= 3
    assert sum(q['split'] == 'evaluation' for q in queries) >= 10
    assert {q['category'] for q in queries} >= {'symbol', 'behavior', 'bug', 'config', 'decision', 'no_answer'}
    for query in queries:
        for target in query['relevant']:
            lines = (BASE / 'fixtures' / query['project'] / target['path']).read_text().splitlines()
            assert 1 <= target['start'] <= target['end'] <= len(lines)


def test_metrics_require_matching_path_and_evidence_not_just_valid_lines():
    relevant = [{'path': 'a.py', 'start': 10, 'end': 12}, {'path': 'b.py', 'start': 1, 'end': 2}]
    hits = [{'path': 'a.py', 'spans': [(1, 3)], 'valid': True},
            {'path': 'a.py', 'spans': [(9, 10)], 'valid': True},
            {'path': 'b.py', 'spans': [(1, 2)], 'valid': False}]
    result = benchmark.evaluate(hits, relevant)
    assert result['recall'] == {'1': 0, '3': .5, '5': .5}
    assert result['rr'] == .5
    assert result['invalid_references'] == 1
    assert benchmark.evaluate([], [])['no_answer_correct'] is True
    assert benchmark.evaluate(hits, [])['no_answer_correct'] is False


def test_fusion_does_not_count_repeated_chunks_as_extra_votes():
    a = dict(path='a.py', spans=[(1, 3)], valid=True)
    b = dict(path='b.py', spans=[(1, 3)], valid=True)
    fused = benchmark.fuse([a, a, b], [b, a])
    assert len(fused) == 2
    assert {h['path'] for h in fused} == {'a.py', 'b.py'}
    assert len(next(h for h in fused if h['path'] == 'a.py')['spans']) == 3


def test_chunks_do_not_reference_phantom_line_after_terminal_newline():
    import code_index
    for suffix, text in [('.md', '# Decision\nUse leases.\n'), ('.py', 'TIMEOUT = 30\n'),
                         ('.js', 'function f() {\n  return 1;\n}\n'), ('.txt', 'one\n\n')]:
        chunks = code_index.chunk_text(text, suffix)
        assert chunks
        assert all(1 <= c['line_start'] <= c['line_end'] <= len(text.splitlines()) for c in chunks)
        assert chunks[-1]['line_end'] == len(text.splitlines())
