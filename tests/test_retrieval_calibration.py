"""Train-only threshold freezing and abstention accounting."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[1] / 'benchmarks/retrieval'
sys.path.insert(0, str(BASE))
spec = importlib.util.spec_from_file_location('calibration', BASE / 'calibrate.py')
calibration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calibration)


def row(ident, split, negative, distance):
    return dict(id=ident, split=split, method='vector', recall=None if negative else {'5': 1},
                hits=[dict(path='a.py', spans=[(1, 2)], valid=True, distance=distance)])


def test_fit_cannot_use_evaluation_rows_and_rejects_boundary():
    rows = [row('p', 'development', False, .2), row('n', 'development', True, .6)]
    fitted = calibration.fit(rows)
    assert fitted['threshold'] == .6
    assert calibration.filter_hits(rows[0]['hits'], .6)
    assert not calibration.filter_hits(rows[1]['hits'], .6)
    with pytest.raises(ValueError, match='development'):
        calibration.fit(rows + [row('e', 'evaluation', True, .1)])


def test_threshold_is_frozen_before_and_unchanged_after_evaluation(tmp_path):
    queries = [dict(split='development'), dict(split='evaluation')]
    observer = calibration.FreezeThreshold(queries, tmp_path / 'threshold.json')
    rows = [row('n', 'development', True, .6)]
    observer(rows)
    assert json.loads((tmp_path / 'threshold.json').read_text())['threshold'] == .6
    observer(rows + [row('e', 'evaluation', True, .1)])
    assert observer.frozen['threshold'] == .6
    with pytest.raises(ValueError, match='precede'):
        calibration.FreezeThreshold(list(reversed(queries)), tmp_path / 'bad.json')


def test_filtered_metrics_penalize_missed_answers_and_false_accepts():
    rows = [row('p', s, False, .7) for s in ('development', 'evaluation')]
    rows += [row('n', s, True, .2) for s in ('development', 'evaluation')]
    queries = [dict(id='p', relevant=[dict(path='a.py', start=1, end=2)]), dict(id='n', relevant=[])]
    summary = calibration.assess(rows, queries, .6)['summary']['evaluation']
    assert summary['mrr'] == 0 and summary['recall_at_5'] == 0
    assert summary['false_accepts'] == 1 and summary['answerable_accepted'] == 0


def test_new_evaluation_has_no_reused_queries_and_valid_labels():
    original = json.loads((BASE / 'v2/dataset.json').read_text())['queries']
    current = json.loads((calibration.CORPUS / 'dataset.json').read_text())['queries']
    assert [q for q in current if q['split'] == 'development'] == [q for q in original if q['split'] == 'development']
    new = [q for q in current if q['split'] == 'evaluation']
    assert len(new) == 24 and sum(not q['relevant'] for q in new) == 12
    assert not {q['query'] for q in new} & {q['query'] for q in original}
    for query in new:
        for target in query['relevant']:
            lines = (calibration.CORPUS / 'fixtures' / query['project'] / target['path']).read_text().splitlines()
            assert 1 <= target['start'] <= target['end'] <= len(lines)
