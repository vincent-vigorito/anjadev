"""Trust legato ai contenuti, API automatica e writer reali."""
import sys
from pathlib import Path

import pytest
from test_hardening import call, payload, rpc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from anja import trust  # noqa: E402

PAGE = '---\ntitle: Test\ntype: entity\nupdated: 2026-09-07\ngenerated: { by: process:test }\n---\n# Test\n\nOriginal\n'


def test_verification_does_not_invalidate_itself_and_preserves_history():
    first = trust.append(PAGE, 'process:test')
    second = trust.append(first, 'process:other')
    assert trust.content_revision(PAGE) == trust.content_revision(first) == trust.content_revision(second)
    assert trust.status(second)['current_verifications'] == 2
    assert trust.status(second)['trust_tier'] == 'machine-confirmed'
    assert trust.status(second.replace('Original', 'Changed'))['trust_tier'] == 'stale'
    assert trust.status(second.replace('2026-09-07', '2026-09-08'))['trust_tier'] == 'machine-confirmed'


@pytest.mark.parametrize('before,after', [('title: Test','title: New'), ('type: entity','type: concept'),
                                         ('Original','New'), ('# Test','# New')])
def test_content_and_metadata_changes_invalidate_trust(before, after):
    verified = trust.append(PAGE, 'process:test')
    assert trust.status(verified.replace(before, after))['trust_tier'] == 'stale'


def test_legacy_and_human_boundary():
    old = PAGE.replace('type: entity', 'type: entity\nverified: [{ by: human:vincent, at: old }]')
    assert trust.status(old)['trust_tier'] == 'legacy'
    current = trust.append(old, 'process:test')
    assert trust.status(current)['trust_tier'] == 'machine-confirmed'
    assert trust.status(current)['verifications'] == 2
    with pytest.raises(ValueError, match='local operator'):
        trust.append(PAGE, 'human:invented')
    assert trust.status(trust.append(PAGE, 'human:operator', origin='local-cli'))['trust_tier'] == 'human-reviewed'


def test_api_verify_read_edit_and_rename(tmp_path):
    folder = tmp_path / '.anjawiki/wiki/entities'
    folder.mkdir(parents=True)
    page = folder / 'test.md'
    page.write_text(PAGE)
    answers = rpc(tmp_path, [call('wiki.verify', {'slug':'test'}),
                            call('wiki.read', {'slug':'test', 'max_chars':10}),
                            call('wiki.verify', {'slug':'test', 'by':'human:invented'})])
    assert payload(answers[0])['trust_tier'] == 'machine-confirmed'
    assert payload(answers[1])['trust_tier'] == 'machine-confirmed'
    assert payload(answers[2])['code'] == 'human_verification_requires_operator'
    answers = rpc(tmp_path, [call('wiki.upsert_entity', {'slug':'test', 'sections':{'New':'changed'}}),
                            call('wiki.read', {'slug':'test'})])
    assert payload(answers[1])['trust_tier'] == 'stale'
    page.write_text(trust.append(page.read_text(), 'process:test'))
    page.write_text(page.read_text().replace('changed', 'direct edit'))
    assert payload(rpc(tmp_path, [call('wiki.read', {'slug':'test'})])[0])['trust_tier'] == 'stale'


def test_multiline_legacy_history_is_retained():
    old = PAGE.replace('type: entity', 'type: entity\nverified:\n  - by: human:old\n    at: yesterday')
    assert trust.status(old)['trust_tier'] == 'legacy'
    verified = trust.append(old, 'process:new')
    assert trust.status(verified)['trust_tier'] == 'machine-confirmed'
    assert 'human:old' in trust.events(verified)[0]['legacy']


def test_human_cli_requires_matching_file_revision(tmp_path):
    import subprocess

    from anja.persistence import revision
    from test_hardening import PYTHON
    path = tmp_path / 'page.md'
    path.write_text(PAGE)
    command = [PYTHON, str(Path(__file__).resolve().parents[1] / 'scripts/verify_page.py'),
               str(path), '--human', 'operator', '--expected-revision', revision(PAGE)]
    done = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert done.returncode == 0, done.stderr
    assert trust.status(path.read_text())['trust_tier'] == 'human-reviewed'
    stale = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert stale.returncode != 0
    assert trust.status(path.read_text())['verifications'] == 1


def test_rename_preserves_unchanged_content_and_invalidates_backlinks(tmp_path):
    folder = tmp_path / '.anjawiki/wiki/entities'
    folder.mkdir(parents=True)
    (folder / 'test.md').write_text(trust.append(PAGE, 'process:test'))
    (folder / 'ref.md').write_text(trust.append(PAGE + '\n[[test]]\n', 'process:test'))
    answers = rpc(tmp_path, [call('wiki.rename', {'old_slug':'test', 'new_slug':'renamed'}),
                            call('wiki.read', {'slug':'renamed'}), call('wiki.read', {'slug':'ref'})])
    assert 'error' not in payload(answers[0])
    assert payload(answers[1])['trust_tier'] == 'machine-confirmed'
    assert payload(answers[2])['trust_tier'] == 'stale'


def test_steward_edit_keeps_history_but_invalidates_trust(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from anja import common
    from anja import wiki as wiki_tools

    import steward

    wiki = tmp_path / '.anjawiki/wiki'
    (wiki / 'concepts').mkdir(parents=True)
    page = wiki / 'concepts/test.md'
    page.write_text(trust.append(PAGE.replace('type: entity', 'type: concept'), 'process:test'))
    writer = object.__new__(steward.Writer)
    writer.wroot = wiki
    monkeypatch.setattr(wiki_tools, 'ROOT', tmp_path)
    monkeypatch.setattr(wiki_tools, '_wiki_root', lambda: wiki)
    monkeypatch.setattr(wiki_tools, '_trigger_wiki_embed_bg', lambda path: None)
    writer.srv = SimpleNamespace(_parse_frontmatter=common._parse_frontmatter, _parse_sections=common._parse_sections,
                                 tool_wiki_upsert_concept=wiki_tools.tool_wiki_upsert_concept)
    result = writer.apply({'action':'upsert_concept', 'slug':'test', 'body':'New knowledge'}, 'cluster')
    assert 'error' not in result
    assert trust.status(page.read_text())['trust_tier'] == 'stale'
    assert trust.status(page.read_text())['verifications'] == 1


def test_lint_uses_same_revision_rules(tmp_path):
    from lint_checks import check_trust

    wiki = tmp_path / '.anjawiki/wiki/entities'
    wiki.mkdir(parents=True)
    page = wiki / 'test.md'
    page.write_text(trust.append(PAGE, 'process:test').replace('Original','Changed'))
    _, tiers = check_trust({'test':(page,'entities')})
    answer = payload(rpc(tmp_path, [call('wiki.lint', {'categories':['trust']})])[0])
    assert tiers['stale'] == 1
    assert answer['summary']['trust_tiers']['stale'] == 1
