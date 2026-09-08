"""Q2: identità, migrazione, conflitti e interruzioni su fixture locali."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from test_hardening import PYTHON, call, payload, project, rpc

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from anja import persistence as store  # noqa: E402

import roadmap_io as rio  # noqa: E402


def task_ids(data):
    return {t["owner"]: t["id"] for t in rio.list_tasks(data)}


def test_legacy_migration_preserves_identity_and_backup(tmp_path):
    path = tmp_path / "roadmap.md"
    original = "---\ntitle: Roadmap\ncustom: retained\n---\n# Tasks\n\n## Open\n<!-- retain me -->\n- [ ] Fix login | owner: Alice\n- [ ] Fix login | owner: Bob\n"
    path.write_text(original)
    data = rio.parse_roadmap(path)
    ids = task_ids(data)
    assert ids == task_ids(rio.parse_roadmap(path))
    assert path.read_text() == original
    assert list(tmp_path.iterdir()) == [path]
    with pytest.raises(store.PersistenceError, match="ambiguous"):
        rio.resolve_task(data, "fix-login")
    with pytest.raises(store.PersistenceError, match="ambiguous"):
        rio.resolve_task(data, "fix-login-2")
    task = rio.move_task_to_section(data["sections"], "Open", 0, "Done")
    task["status"] = "done"
    task["title"] = "New title"
    rio.write_roadmap(path, data)
    assert task_ids(rio.parse_roadmap(path)) == ids
    backup = list(tmp_path.glob("*.bak"))
    assert len(backup) == 1 and backup[0].read_text() == original
    assert "<!-- retain me -->" in path.read_text() and "custom: retained" in path.read_text()
    for section, status in [("Open", "open"), ("Blocked", "blocked"), ("Done", "done")]:
        data = rio.parse_roadmap(path)
        sec, idx = rio.resolve_task(data, ids["Alice"])
        task = rio.move_task_to_section(data["sections"], sec, idx, section)
        task["status"] = status
        rio.write_roadmap(path, data)
        assert task_ids(rio.parse_roadmap(path)) == ids
    assert len(list(tmp_path.glob("*.bak"))) == 1
    # Ripristino da backup: il contenuto e gli ID transitori originali sono recuperabili.
    restored = tmp_path / "restored.md"
    restored.write_bytes(backup[0].read_bytes())
    assert task_ids(rio.parse_roadmap(restored)) == ids


def test_legacy_alias_is_not_reassigned(tmp_path):
    path = tmp_path / "roadmap.md"
    path.write_text("## Open\n- [ ] Original\n")
    data = rio.parse_roadmap(path)
    sec, idx = rio.resolve_task(data, "original")
    old_id = data["sections"][sec][idx]["id"]
    data["sections"][sec][idx]["title"] = "Renamed"
    rio.write_roadmap(path, data)
    data = rio.parse_roadmap(path)
    data["sections"]["Open"].append({"title": "Original", "status": "open", "id": rio._assign_id({}, set())})
    rio.write_roadmap(path, data)
    data = rio.parse_roadmap(path)
    sec, idx = rio.resolve_task(data, "original")
    assert data["sections"][sec][idx]["id"] == old_id
    data["sections"][sec].pop(idx)
    rio.write_roadmap(path, data)
    assert rio.resolve_task(rio.parse_roadmap(path), "original") == (None, None)


def test_roadmap_rejects_stale_snapshot_and_duplicate_ids(tmp_path):
    path = tmp_path / "roadmap.md"
    path.write_text("## Open\n- [ ] One\n")
    first, second = rio.parse_roadmap(path), rio.parse_roadmap(path)
    first["sections"]["Open"][0]["title"] = "Winner"
    rio.write_roadmap(path, first)
    saved = path.read_bytes()
    with pytest.raises(store.PersistenceError):
        rio.write_roadmap(path, second)
    assert path.read_bytes() == saved
    line = next(line for line in path.read_text().splitlines() if line.startswith("- ["))
    path.write_text(path.read_text() + "\n" + line)
    with pytest.raises(store.PersistenceError, match="duplicate"):
        rio.parse_roadmap(path)


def test_two_processes_same_revision_only_one_wins(tmp_path):
    path = tmp_path / "page.md"
    path.write_text("original\n")
    probe = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from anja.persistence import read_text, revision, write_text, PersistenceError
p = Path(sys.argv[2]); rev = revision(read_text(p))
print('ready', flush=True)
sys.stdin.readline()
try:
    write_text(p, sys.argv[3], rev)
    print('saved')
except PersistenceError as e:
    print(e.result['code'])
"""
    children = [subprocess.Popen([PYTHON, "-c", probe, str(SCRIPTS), str(path), value],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for value in ("first", "second")]
    try:
        for child in children:
            assert child.stdout.readline().strip() == "ready"
        results = [child.communicate("go\n", timeout=10) for child in children]
        assert sorted(out.strip() for out, err in results) == ["revision_conflict", "saved"]
        assert all(child.returncode == 0 for child in children)
        assert path.read_text() in {"first", "second"}
    finally:
        for child in children:
            if child.poll() is None:
                child.kill(); child.wait()


def test_failed_publish_preserves_original_and_mode(tmp_path, monkeypatch):
    path = tmp_path / "page.md"
    path.write_text("original\n")
    path.chmod(0o640)
    expected = store.revision(store.read_text(path))
    def fail(*args):
        raise OSError("interrupted before replace")
    with monkeypatch.context() as patch:
        patch.setattr(store.os, "replace", fail)
        with pytest.raises(OSError):
            store.write_text(path, "replacement\n", expected)
    assert path.read_text() == "original\n"
    assert not list(tmp_path.glob("*.tmp"))
    store.write_text(path, "replacement\n", expected)
    assert path.stat().st_mode & 0o777 == 0o640


def test_wire_wiki_conflict_and_full_revision(tmp_path):
    wiki = project(tmp_path)
    page = wiki / "entities" / "test.md"
    page.write_bytes(b"---\r\ntitle: Test\r\ncustom: kept\r\n---\r\n## Keep\r\nretained\r\n")
    first = payload(rpc(tmp_path, [call("wiki.read", {"slug": "test", "max_chars": 5})])[0])
    assert first["revision"] == store.revision(store.read_text(page))
    args = {"slug": "test", "sections": {"New": "first"}, "expected_revision": first["revision"]}
    second = payload(rpc(tmp_path, [call("wiki.upsert_entity", args)])[0])
    assert second["revision"] != first["revision"]
    args["sections"] = {"New": "stale"}
    failure = payload(rpc(tmp_path, [call("wiki.upsert_entity", args)])[0])
    assert failure["code"] == "revision_conflict"
    assert "first" in page.read_text() and "stale" not in page.read_text()
    assert "custom: kept" in page.read_text() and "retained" in page.read_text()


def test_wire_roadmap_archive_keeps_ids_and_rejects_stale(tmp_path):
    wiki = project(tmp_path)
    added = payload(rpc(tmp_path, [call("roadmap.add", {"title": "A"})])[0])
    task_id = added["id"]
    result = payload(rpc(tmp_path, [call("roadmap.update", {"id": task_id, "status": "done", "done": "2020-01-01",
                                                           "expected_revision": added["revision"]})])[0])
    stale = payload(rpc(tmp_path, [call("roadmap.add", {"title": "B", "expected_revision": added["revision"]})])[0])
    assert stale["code"] == "revision_conflict"
    archived = payload(rpc(tmp_path, [call("roadmap.archive", {"expected_revision": result["revision"]})])[0])
    assert archived["archived"] == 1
    assert task_id in (tmp_path / archived["archive_file"]).read_text()
    assert rio.list_tasks(rio.parse_roadmap(wiki / "roadmap.md")) == []
    assert payload(rpc(tmp_path, [call("roadmap.archive")])[0])["archived"] == 0


def test_memory_same_title_never_overwrites(tmp_path):
    project(tmp_path)
    requests = [call("memory.write", {"title": "Same", "content": f"note {n}"}, n) for n in range(20)]
    results = [payload(r) for r in rpc(tmp_path, requests)]
    paths = [tmp_path / r["path"] for r in results]
    assert len(set(paths)) == 20
    for n, path in enumerate(paths):
        assert path.read_text().endswith(f"note {n}\n")


def test_migration_interrupted_then_retry(tmp_path, monkeypatch):
    path = tmp_path / "roadmap.md"
    original = "## Open\n- [ ] Legacy\n"
    path.write_text(original)
    data = rio.parse_roadmap(path)
    ids = [t["id"] for t in rio.list_tasks(data)]
    with monkeypatch.context() as patch:
        patch.setattr(store.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("interrupted")))
        with pytest.raises(OSError):
            rio.write_roadmap(path, data)
    assert path.read_text() == original
    assert len(list(tmp_path.glob("*.bak"))) == 1
    rio.write_roadmap(path, rio.parse_roadmap(path))
    assert [t["id"] for t in rio.list_tasks(rio.parse_roadmap(path))] == ids
    assert len(list(tmp_path.glob("*.bak"))) == 1


def test_batch_conflict_changes_nothing(tmp_path):
    first, second = tmp_path / "a.md", tmp_path / "b.md"
    first.write_text("one"); second.write_text("two")
    with pytest.raises(store.PersistenceError):
        store.write_many({first: ("new one", store.revision("one")), second: ("new two", store.revision("old"))})
    assert first.read_text() == "one" and second.read_text() == "two"


def test_lock_released_after_process_killed(tmp_path):
    path = tmp_path / "page.md"
    path.write_text("original")
    probe = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from anja.persistence import file_lock
with file_lock(Path(sys.argv[2])):
    print('locked', flush=True)
    sys.stdin.readline()
"""
    child = subprocess.Popen([PYTHON, "-c", probe, str(SCRIPTS), str(path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "locked"
    finally:
        child.kill(); child.communicate(timeout=10)
    store.write_text(path, "recovered", store.revision("original"))
    assert path.read_text() == "recovered"


def test_archive_partial_publish_retry_does_not_duplicate(tmp_path, monkeypatch):
    from anja import roadmap
    wiki = project(tmp_path)
    monkeypatch.setattr(roadmap, "ROOT", tmp_path)
    monkeypatch.setattr(roadmap, "_wiki_root", lambda: wiki)
    task = roadmap.tool_roadmap_add({"title": "Archive me"})
    roadmap.tool_roadmap_update({"id": task["id"], "status": "done", "done": "2020-01-01"})
    publish = store._publish
    def fail_roadmap(path, text, exclusive=False):
        if path.name == "roadmap.md":
            raise OSError("interrupted after archive")
        return publish(path, text, exclusive)
    with monkeypatch.context() as patch:
        patch.setattr(store, "_publish", fail_roadmap)
        with pytest.raises(OSError):
            roadmap.tool_roadmap_archive({})
    assert len(roadmap.tool_roadmap_list({})["tasks"]) == 1
    result = roadmap.tool_roadmap_archive({})
    archived = (tmp_path / result["archive_file"]).read_text()
    assert archived.count(task["id"]) == 1
    assert roadmap.tool_roadmap_list({})["tasks"] == []


def test_memory_with_frozen_clock(tmp_path, monkeypatch):
    from datetime import datetime

    from anja import memory
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(memory, "ROOT", tmp_path)
    monkeypatch.setattr(memory, "_raw_root", lambda: raw)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 6, 12, 0, 0)
    monkeypatch.setattr(memory, "datetime", Clock)
    results = [memory.tool_memory_write({"title": "same", "content": str(n)}) for n in range(5)]
    assert len({r["path"] for r in results}) == 5
    for n, result in enumerate(results):
        assert (tmp_path / result["path"]).read_text().endswith(f"\n{n}\n")


def test_steward_passes_merge_revision(tmp_path, monkeypatch):
    import steward
    wiki = project(tmp_path)
    page = wiki / "concepts" / "test.md"
    page.parent.mkdir()
    page.write_text("---\ntitle: Test\n---\n## Summary\nHuman\n")
    writer = object.__new__(steward.Writer)
    writer.wroot = wiki
    # Import del facade con scope fissato localmente per non dipendere dalla cache dei test.
    from anja import common
    from anja import wiki as wiki_tools
    monkeypatch.setattr(wiki_tools, "ROOT", tmp_path)
    monkeypatch.setattr(wiki_tools, "_wiki_root", lambda: wiki)
    monkeypatch.setattr(wiki_tools, "_trigger_wiki_embed_bg", lambda path: None)
    from types import SimpleNamespace
    def racing_write(args):
        page.write_text(page.read_text() + "\nConcurrent human edit\n")
        return wiki_tools.tool_wiki_upsert_concept(args)
    writer.srv = SimpleNamespace(_parse_frontmatter=common._parse_frontmatter, _parse_sections=common._parse_sections,
                                 tool_wiki_upsert_concept=racing_write)
    result = writer.apply({"action": "upsert_concept", "slug": "test", "body": "Steward addition"}, "cluster")
    assert result["code"] == "revision_conflict"
    assert "Concurrent human edit" in page.read_text()
    assert "Steward addition" not in page.read_text()


def test_replace_links_preview_revision_conflict(tmp_path):
    wiki = project(tmp_path)
    page = wiki / "entities" / "test.md"
    page.write_text("[[old]]\n")
    preview = payload(rpc(tmp_path, [call("wiki.replace_links", {"old": "old", "new": "new", "dry_run": True})])[0])
    revisions = {p["path"]: p["revision"] for p in preview["files_touched"]}
    page.write_text("[[old]]\nHuman edit\n")
    result = payload(rpc(tmp_path, [call("wiki.replace_links", {"old": "old", "new": "new", "expected_revisions": revisions})])[0])
    assert result["code"] == "revision_conflict"
    assert page.read_text() == "[[old]]\nHuman edit\n"


def test_steward_failed_pending_is_retained(tmp_path, monkeypatch):
    import json

    import steward
    wiki = project(tmp_path)
    session = wiki / "sessions" / "day" / "session.md"
    session.parent.mkdir(parents=True)
    session.write_text("---\ntitle: Session\n---\nHuman notes\n")
    pending = tmp_path / ".anjawiki" / ".steward-pending.json"
    pending.write_text(json.dumps({"clusters": [{"id": "cluster", "session_ids": ["session"],
                                               "patches": [{"action": "log_append", "body": "Note"}]}]}))
    class Writer:
        fail = True
        def __init__(self, *args):
            pass
        def apply(self, *args):
            return {"error": "conflict", "code": "revision_conflict"} if self.fail else {"status": "ok"}
    monkeypatch.setattr(steward, "Writer", Writer)
    monkeypatch.setenv("ANJA_STEWARD", "1")
    report = steward.run(tmp_path, "apply-pending")
    assert report["patches_applied"] == 0 and report["distilled"] == 0
    assert report["errors"] and pending.exists()
    assert "distilled:" not in session.read_text()
    Writer.fail = False
    report = steward.run(tmp_path, "apply-pending")
    assert report["patches_applied"] == 1 and report["distilled"] == 1
    assert not pending.exists()


@pytest.mark.parametrize("title", ["Title\n- [ ] Injected", "Title | id: task-" + "a" * 32])
def test_task_title_cannot_inject_identity(tmp_path, title):
    wiki = project(tmp_path)
    result = payload(rpc(tmp_path, [call("roadmap.add", {"title": title})])[0])
    assert result["code"] in {"invalid_task_value", "invalid_task_title"}
    assert not (wiki / "roadmap.md").exists()


def test_concurrent_creation_never_replaces_existing_file(tmp_path):
    path = tmp_path / "note.md"
    store.create_text(path, "first")
    with pytest.raises(FileExistsError):
        store.create_text(path, "second")
    assert path.read_text() == "first"
    assert not list(tmp_path.glob("*.tmp"))
