"""Source-backed syntax facts must not masquerade as behavioral verification."""
import hashlib
import sys
from pathlib import Path

import pytest
from test_hardening import call, payload, project, rejected, rpc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from code_inspect import inspect_source  # noqa: E402


def test_mcp_inspect_selects_symbol_and_preserves_revision(tmp_path):
    project(tmp_path)
    text = 'import socket\n\ndef heartbeat_due(now, previous, interval):\n    return now - previous >= interval\n'
    (tmp_path / 'heartbeat.py').write_text(text)
    revision = hashlib.sha256(text.encode()).hexdigest()
    result = payload(rpc(tmp_path, [call('code.inspect', dict(path='heartbeat.py', symbol='heartbeat_due',
                                                           expected_revision=revision))])[0])
    assert result['source_revision'] == revision
    assert 'return now - previous >= interval' in result['source']
    assert {o['kind'] for o in result['operations']} == {'Return', 'BinOp', 'Compare'}
    assert result['relations'][0]['type'] == 'imports'
    assert result['relations'][0]['resolution'] == 'not_resolved'
    assert result['behavioral_claims'] == 'not_assessed' and result['tests_executed'] is False
    assert result['scope_span'] == {'line_start': 3, 'line_end': 4}


def test_changed_revision_rejected(tmp_path):
    project(tmp_path)
    (tmp_path / 'app.py').write_text('value = 2\n')
    result = inspect_source(tmp_path, 'app.py', expected_revision=hashlib.sha256(b'value = 1\n').hexdigest())
    assert result['code'] == 'revision_conflict'
    assert 'source' not in result


def test_dead_assertions_are_syntax_not_test_results(tmp_path):
    project(tmp_path)
    (tmp_path / 'test_app.py').write_text('def test_app():\n    if False:\n        assert missing_call()\n')
    result = inspect_source(tmp_path, 'test_app.py', symbol='test_app')
    assert {'Assert', 'Call'} <= {o['kind'] for o in result['operations']}
    assert result['tests_executed'] is False
    assert 'missing_call()' in result['source']


def test_nested_definitions_have_qualified_owners(tmp_path):
    project(tmp_path)
    (tmp_path / 'app.py').write_text('class Client:\n    def send(self):\n        def inner():\n            return 1\n        return 2\n')
    result = inspect_source(tmp_path, 'app.py', symbol='Client.send')
    assert [o['line_start'] for o in result['operations'] if o['kind'] == 'Return'] == [5]
    assert result['symbols'][0]['name'] == 'Client.send'


def test_duplicate_symbols_are_ambiguous(tmp_path):
    project(tmp_path)
    (tmp_path / 'app.py').write_text('def f():\n    return 1\ndef f():\n    return 2\n')
    assert inspect_source(tmp_path, 'app.py', symbol='f')['code'] == 'symbol_not_unique'


def test_markdown_relation_records_declaration_not_correctness(tmp_path):
    project(tmp_path)
    (tmp_path / 'app.py').write_text('def f():\n    return 1\n')
    (tmp_path / 'decision.md').write_text('Implemented by: app.py:f\nImplemented by: app.py:missing\nImplemented by: ../outside.py:f\n')
    result = inspect_source(tmp_path, 'decision.md')
    assert [r['resolution'] for r in result['relations']] == ['symbol_exists', 'symbol_absent_or_ambiguous', 'unavailable']
    first = result['relations'][0]
    assert first['type'] == 'implemented_by' and not first['behavior_verified']
    assert first['target_span'] == {'line_start': 1, 'line_end': 2}
    assert first['target_revision'] == hashlib.sha256((tmp_path / 'app.py').read_bytes()).hexdigest()


@pytest.mark.parametrize('path', ['../app.py', '/tmp/app.py', '.hidden/app.py', 'secrets.py', 'alias.py'])
def test_excluded_or_unsafe_paths_cannot_be_inspected(tmp_path, path):
    project(tmp_path)
    (tmp_path / '.hidden').mkdir()
    (tmp_path / '.hidden/app.py').write_text('secret = 1\n')
    (tmp_path / 'secrets.py').write_text('secret = 2\n')
    (tmp_path / 'alias.py').symlink_to(tmp_path / 'secrets.py')
    assert 'error' in inspect_source(tmp_path, path)


def test_budget_and_empty_file_have_explicit_limits(tmp_path):
    project(tmp_path)
    (tmp_path / 'app.py').write_text('def f():\n    assert 1 == 1\n    return 1\n')
    result = inspect_source(tmp_path, 'app.py', max_items=1, max_chars=5)
    assert len(result['source']) == 5 and result['source_truncated']
    assert sum(len(result[k]) for k in ('symbols', 'operations', 'relations')) == 1
    assert result['facts_truncated']
    (tmp_path / 'app.py').write_text('')
    assert inspect_source(tmp_path, 'app.py')['scope_span'] is None


def test_invalid_budget_and_parse_error_are_explicit(tmp_path):
    project(tmp_path)
    (tmp_path / 'app.py').write_text('def broken(\n')
    assert inspect_source(tmp_path, 'app.py')['code'] == 'parse_error'
    assert inspect_source(tmp_path, 'app.py', max_chars=-1)['code'] == 'invalid_inspect_options'


def test_inspect_respects_tool_group_filter(tmp_path):
    project(tmp_path)
    response = rpc(tmp_path, [call('code.inspect', dict(path='app.py'))], extra_env={'ANJA_TOOL_GROUPS': 'memory'})[0]
    assert rejected(response)


def test_module_constant_is_a_binding_not_a_behavior(tmp_path):
    project(tmp_path)
    (tmp_path / 'config.py').write_text('MAX_ATTEMPTS = 3\nWORKER_TIMEOUT = 300\n')
    result = inspect_source(tmp_path, 'config.py', symbol='WORKER_TIMEOUT')
    assert result['source'] == 'WORKER_TIMEOUT = 300'
    assert result['symbols'][0]['kind'] == 'Assign'
    assert [o['kind'] for o in result['operations']] == ['Assign']
    assert result['behavioral_claims'] == 'not_assessed'


def test_inspection_does_not_execute_source_or_initialize_provider(tmp_path, monkeypatch):
    import embed_providers
    project(tmp_path)
    (tmp_path / 'app.py').write_text('raise RuntimeError("must not execute")\n')
    def forbidden():
        raise AssertionError('must not initialize provider')
    monkeypatch.setattr(embed_providers, 'get_provider', forbidden)
    result = inspect_source(tmp_path, 'app.py')
    assert 'error' not in result
    assert {o['kind'] for o in result['operations']} == {'Raise', 'Call'}
