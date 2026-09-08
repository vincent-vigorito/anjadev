"""Evidence is checked against source content, including races after retrieval."""
import hashlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from test_hardening import call, payload, project, rpc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from search_evidence import finalize  # noqa: E402

import code_search  # noqa: E402


def lexical(path='app.py', text='needle = 1', line=1):
    return {'level': 0, 'results': [{'path': path, 'preview': [{'line': line, 'text': text}]}]}


def vector(root):
    return {'level': 2, 'results': [{'path': 'app.py', 'line_start': 1, 'line_end': 1,
                                    'preview': 'needle = 1', '_indexed_content': 'needle = 1',
                                    '_indexed_revision': hashlib.sha256((root / 'app.py').read_bytes()).hexdigest()}]}


def test_mcp_reports_revision_and_bounds_total_previews(tmp_path):
    project(tmp_path)
    for name in ('app.py', 'other.py'):
        (tmp_path / name).write_text('needle = 1\n')
    result = payload(rpc(tmp_path, [call('code.search', {'query': 'needle', 'smart_level': 0,
                                                       'max_preview_chars': 3})])[0])
    assert result['count'] == 2
    assert result['evidence']['status'] == 'literal_evidence'
    assert result['evidence']['abstain'] is False
    assert result['evidence']['preview_truncated']
    assert sum(len(p['text']) for h in result['results'] for p in h['preview']) == 3
    for hit in result['results']:
        assert hit['evidence']['source_revision'] == hashlib.sha256((tmp_path / hit['path']).read_bytes()).hexdigest()
        assert hit['evidence']['spans'] == [{'line_start': 1, 'line_end': 1}]


def test_candidates_remain_visible_but_do_not_claim_literal_support(tmp_path):
    (tmp_path / 'app.py').write_text('needle = 1\n')
    result = finalize(vector(tmp_path), tmp_path, 'quantum teleportation', 100)
    assert result['count'] == 1
    assert result['evidence']['status'] == 'candidates_only'
    assert result['evidence']['abstain']
    assert not any(key.startswith('_indexed_') for key in result['results'][0])


@pytest.mark.parametrize('kind', ['lexical', 'vector'])
def test_changed_source_is_not_presented_as_current_evidence(tmp_path, kind):
    (tmp_path / 'app.py').write_text('needle = 1\n')
    result = vector(tmp_path) if kind == 'vector' else lexical()
    (tmp_path / 'app.py').write_text('needle = 2\n')
    checked = finalize(result, tmp_path, 'needle', 100)
    assert checked['results'] == []
    assert checked['evidence']['abstain']
    assert checked['evidence']['rejected']


def test_index_content_must_match_even_when_manifest_hash_matches(tmp_path):
    (tmp_path / 'app.py').write_text('needle = 1\n')
    result = vector(tmp_path)
    result['results'][0]['_indexed_content'] = 'needle = 2'
    assert finalize(result, tmp_path, 'needle', 100)['evidence']['rejected'] == [
        {'reason': 'index_content_mismatch'}]


@pytest.mark.parametrize('path,line', [('../outside.py', 1), ('/tmp/outside.py', 1), ('app.py', 2)])
def test_invalid_references_are_excluded(tmp_path, path, line):
    (tmp_path / 'app.py').write_text('needle = 1\n')
    checked = finalize(lexical(path=path, line=line), tmp_path, 'needle', 100)
    assert checked['count'] == 0
    assert checked['evidence']['status'] == 'no_evidence'


def test_symlink_source_is_not_accepted(tmp_path):
    (tmp_path / 'real.py').write_text('needle = 1\n')
    (tmp_path / 'app.py').symlink_to(tmp_path / 'real.py')
    assert finalize(lexical(), tmp_path, 'needle', 100)['evidence']['rejected'] == [{'reason': 'unsafe_path'}]


@pytest.mark.parametrize('options', [{'limit': 0}, {'limit': 51}, {'limit': True},
                                    {'max_preview_chars': -1}, {'max_preview_chars': 100001}])
def test_invalid_budgets_do_not_start_search(tmp_path, options):
    project(tmp_path)
    with patch.object(code_search, 'search_level_0', side_effect=AssertionError('must not retrieve')):
        assert code_search.code_search('needle', smart_level=0, root=tmp_path, **options)['code'] == 'invalid_search_options'
