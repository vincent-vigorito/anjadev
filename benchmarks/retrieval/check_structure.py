"""Offline inspection checks: syntactic evidence, not natural-language accuracy."""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parents[1] / 'scripts'))
from code_inspect import inspect_source  # noqa: E402


def run():
    fixtures = BASE / 'v2/fixtures'
    rows = []
    for source in sorted(fixtures.glob('*/*')):
        result = inspect_source(source.parent, source.name)
        assert 'error' not in result, result
        length = len(source.read_text().splitlines())
        for kind in ('symbols', 'operations', 'relations'):
            for fact in result[kind]:
                assert 1 <= fact['line_start'] <= fact['line_end'] <= length
        assert result['behavioral_claims'] == 'not_assessed'
        rows.append(dict(project=source.parent.name, **result))
    cases = [
        ('checkout', 'config.py', 'PAYMENT_TIMEOUT', ['Assign']),
        ('worker', 'config.py', 'MAX_ATTEMPTS', ['Assign']),
        ('worker', 'queueing.py', 'lease_expired', ['Return', 'Compare']),
        ('worker', 'config.py', 'WORKER_TIMEOUT', ['Assign']),
        ('worker', 'heartbeat.py', 'heartbeat_due', ['Return', 'Compare', 'BinOp']),
        ('cache', 'config.py', 'CACHE_CAPACITY', ['Assign']),
        ('cache', 'storage.py', 'cache_key', ['Return', 'BinOp']),
    ]
    focused = []
    for project, path, symbol, expected in cases:
        result = inspect_source(fixtures / project, path, symbol=symbol)
        assert 'error' not in result
        assert {o['kind'] for o in result['operations']} == set(expected)
        assert result['behavioral_claims'] == 'not_assessed'
        focused.append(dict(project=project, **result))
    return dict(files=len(rows), focused_cases=len(focused), invalid_references=0,
                imports=sum(r['type'] == 'imports' for row in rows for r in row['relations']),
                declarations=sum(r['type'] == 'implemented_by' for row in rows for r in row['relations']),
                outbound_requests=0, rows=rows, focused=focused,
                limitation='Expected AST categories check extraction only. This is not an entailment or no-answer classifier.')


if __name__ == '__main__':
    result = run()
    output = BASE / 'structure-results.json'
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('rows', 'focused')}))
