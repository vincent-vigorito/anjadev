"""Expanded corpus and fixture-only outbound approval guards."""
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BASE = Path(__file__).resolve().parents[1] / 'benchmarks/retrieval'
sys.path.insert(0, str(BASE))
spec = importlib.util.spec_from_file_location('retrieval_v2', BASE / 'run_v2.py')
v2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v2)


def test_v2_preserves_original_labels_and_adds_real_holdout_negatives():
    original = json.loads((BASE / 'dataset.json').read_text())
    data = json.loads((v2.CORPUS / 'dataset.json').read_text())
    queries = data['queries']
    assert queries[:len(original['queries'])] == original['queries']
    assert len(queries) == 72 and len({q['id'] for q in queries}) == 72
    for name in {q['project'] for q in queries}:
        files = list((v2.CORPUS / 'fixtures' / name).glob('*'))
        assert len(files) >= 10
        assert sum(q['project'] == name and q['split'] == 'evaluation' and not q['relevant'] for q in queries) >= 3
        for p in (BASE / 'fixtures' / name).glob('*'):
            assert p.read_bytes() == (v2.CORPUS / 'fixtures' / name / p.name).read_bytes()
    assert sum(len(q['relevant']) > 1 for q in queries) == 6
    for query in queries:
        for target in query['relevant']:
            lines = (v2.CORPUS / 'fixtures' / query['project'] / target['path']).read_text().splitlines()
            assert 1 <= target['start'] <= target['end'] <= len(lines)


def test_remote_preview_does_not_initialize_provider():
    with patch.object(v2.embed_providers, 'get_provider', side_effect=AssertionError('network')):
        preview = v2.plan('openrouter', 'openai/text-embedding-3-small', 3)
    assert preview['source_bytes'] == 4125
    assert preview['query_count'] == 72
    assert preview['embedding_cost_usd'] is None
    assert preview['max_http_requests'] == 253


def test_wrong_approval_rejected_before_provider_initialization():
    preview = v2.plan('openrouter', 'openai/text-embedding-3-small', 3)
    with patch.object(v2.embed_providers, 'get_provider', side_effect=AssertionError('network')):
        with pytest.raises(ValueError, match='hash mismatch'):
            v2.execute(preview, 'not-the-reviewed-preview')


def test_changed_fixture_invalidates_preview(tmp_path):
    import shutil
    corpus = tmp_path / 'corpus'
    shutil.copytree(v2.CORPUS / 'fixtures', corpus / 'fixtures')
    (corpus / 'dataset.json').write_bytes((v2.CORPUS / 'dataset.json').read_bytes())
    with patch.object(v2, 'CORPUS', corpus):
        preview = v2.plan('mock', 'mock-bow', 1)
        (corpus / 'fixtures/cache/storage.py').write_text('new_private_content = 1\n')
        with patch.object(v2.embed_providers, 'get_provider', side_effect=AssertionError('must not initialize')):
            with pytest.raises(ValueError, match='snapshot changed'):
                v2.execute(preview, preview['approval_sha256'])


def test_meter_blocks_unlisted_content_and_excess_calls():
    provider = v2.embed_providers.MockProvider()
    meter = v2.MeteredProvider(provider, {'reviewed fixture'}, 1, 1, 16)
    with patch.object(provider, 'embed', wraps=provider.embed) as embed:
        with pytest.raises(ValueError, match='scope'):
            meter.embed(['private code not in the preview'])
        assert embed.call_count == 0
        meter.embed(['reviewed fixture'])
        with pytest.raises(ValueError, match='budget'):
            meter.embed(['reviewed fixture'])
        assert embed.call_count == 1


def test_meter_caps_total_input_even_with_allowlisted_strings():
    provider = v2.embed_providers.MockProvider()
    meter = v2.MeteredProvider(provider, {'fixture'}, 10, 2, 7)
    with patch.object(provider, 'embed', side_effect=AssertionError('must not send')):
        with pytest.raises(ValueError, match='budget'):
            meter.embed(['fixture', 'fixture'])
