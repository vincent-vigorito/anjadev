"""Regressioni Q0/Q1: scope, filesystem e continuità del trasporto MCP."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from _helpers import cov_env

PLUGIN = Path(__file__).resolve().parents[1]
PYTHON = os.environ.get("ANJA_TEST_PYTHON") or sys.executable


def project(path):
    wiki = path / ".anjawiki" / "wiki"
    (wiki / "entities").mkdir(parents=True)
    (path / ".anjawiki" / "meta.yaml").write_text("name: fixture\n")
    return wiki


def call(name, args=None, ident=1):
    return {"jsonrpc": "2.0", "id": ident, "method": "tools/call",
            "params": {"name": name, "arguments": args or {}}}


def rpc(root, requests, server="mcp_memory_server.py", cwd=None, extra_env=None):
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(root),
           "ANJA_ROOT": str(root), "ANJA_SCOPE": "project", "ANJA_WIKI_EMBED": "0",
           "ANJA_CLAUDE_BIN": str(root / "missing-claude"), **cov_env(), **(extra_env or {})}
    result = subprocess.run([PYTHON, str(PLUGIN / "scripts" / server)], cwd=cwd or root,
                            env=env, input="\n".join(json.dumps(r) for r in requests) + "\n",
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in result.stdout.split("\n") if line.strip()]


def payload(response):
    return json.loads(response["result"]["content"][0]["text"])


def rejected(response):
    return "error" in response or response.get("result", {}).get("isError") is True


def test_search_uses_explicit_root(tmp_path):
    target, other = tmp_path / "target", tmp_path / "other"
    project(target); project(other)
    (target / "right.py").write_text("needle = 1\n")
    (other / "wrong.py").write_text("needle = 2\n")
    result = payload(rpc(target, [call("code.search", {"query": "needle", "smart_level": 0})], cwd=other)[0])
    assert [r["path"] for r in result["results"]] == ["right.py"]
    invalid = tmp_path / "invalid"
    invalid.mkdir()
    assert rejected(rpc(invalid, [call("code.search", {"query": "needle"})], cwd=other)[0])


def test_reranker_cannot_consume_mcp_input(tmp_path):
    project(tmp_path)
    (tmp_path / "app.py").write_text("needle = 1\n")
    (tmp_path / ".anjawiki/index-policy.json").write_text('{"rerank_model":"haiku"}')
    child = tmp_path / "reranker"
    child.write_text(f"#!{PYTHON}\nimport sys\ndata = sys.stdin.read()\nprint('0')\n")
    child.chmod(0o700)
    # Supera il buffer del reader del server: il figlio non deve leggere il resto della pipe.
    requests = [call("code.search", {"query": "needle", "smart_level": 1})]
    requests += [{"jsonrpc": "2.0", "id": i, "method": "ping", "params": {"padding": "x" * 1000}}
                 for i in range(2, 32)]
    result = rpc(tmp_path, requests, extra_env={"ANJA_CLAUDE_BIN": str(child)})
    assert [r["id"] for r in result] == list(range(1, 32))
    assert payload(result[0])["level"] == 1


@pytest.mark.parametrize("server", ["mcp_memory_server.py", "mcp_code_server.py"])
def test_invalid_requests_do_not_stop_server(tmp_path, server):
    project(tmp_path)
    invalid = [None, [], 3, "bad", {}, {"jsonrpc": "1.0", "id": 1, "method": "ping"},
               {"jsonrpc": "2.0", "id": [], "method": "ping"},
               {"jsonrpc": "2.0", "id": 1, "method": []},
               {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": []},
               {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": []}},
               {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "wiki.read", "arguments": []}}]
    requests = []
    for n, bad in enumerate(invalid, 100):
        requests.extend([bad, {"jsonrpc": "2.0", "id": n, "method": "ping"}])
    result = rpc(tmp_path, requests, server=server)
    assert len(result) == len(requests)
    for n in range(len(invalid)):
        assert result[n * 2]["error"]["code"] in (-32600, -32602)
        assert result[n * 2 + 1] == {"jsonrpc": "2.0", "id": n + 100, "result": {}}
    notifications = [{"jsonrpc": "2.0", "method": "ping"},
                     {"jsonrpc": "2.0", "method": "unknown"},
                     {"jsonrpc": "2.0", "method": "notifications/initialized"}]
    assert rpc(tmp_path, notifications, server=server) == []


@pytest.mark.parametrize("name", ["wiki.read", "wiki.verify"])
@pytest.mark.parametrize("slug", ["../outside", "/tmp/outside", "*", "entities/../../outside"])
def test_wiki_rejects_non_slug_paths(tmp_path, name, slug):
    project(tmp_path)
    outside = tmp_path / ".anjawiki" / "outside.md"
    before = "---\ntitle: private\n---\nprivate content\n"
    outside.write_text(before)
    assert rejected(rpc(tmp_path, [call(name, {"slug": slug})])[0])
    assert outside.read_text() == before


def test_wiki_symlinks_and_valid_nested_page(tmp_path):
    wiki = project(tmp_path)
    outside = tmp_path / "outside.md"
    original = "---\ntitle: private\n---\nprivate content [[good]]\n"
    outside.write_text(original)
    (wiki / "entities" / "escape.md").symlink_to(outside)
    good = wiki / "entities" / "good.md"
    good.write_text("---\ntitle: Good\n---\npublic content\n")
    for name, args in [("wiki.read", {"slug": "escape"}), ("wiki.verify", {"slug": "escape"}),
                       ("wiki.upsert_entity", {"slug": "escape", "sections": {"Sintesi": "bad"}}),
                       ("wiki.rename", {"old_slug": "escape", "new_slug": "other"}),
                       ("wiki.delete", {"slug": "escape", "confirm": True})]:
        assert rejected(rpc(tmp_path, [call(name, args)])[0]), name
        assert outside.read_text() == original
    assert payload(rpc(tmp_path, [call("wiki.read", {"slug": "good.md"})])[0])["content"].endswith("public content\n")
    assert not rejected(rpc(tmp_path, [call("wiki.verify", {"slug": "good", "by": "process:test"})])[0])
    result = payload(rpc(tmp_path, [call("wiki.export")])[0])
    exported = Path(result["output_path"]).read_text()
    assert "private content" not in exported
    assert not rejected(rpc(tmp_path, [call("wiki.rename", {"old_slug": "good", "new_slug": "better"})])[0])
    assert outside.read_text() == original


@pytest.mark.parametrize("filename,name,args", [
    ("overview.md", "wiki.update_overview", {"sections": {"Stato": "bad"}}),
    ("index.md", "wiki.index_update", {"category": "Entities", "entries": ["- bad"]}),
    ("log.md", "wiki.log_append", {"type": "note", "description": "bad"}),
])
def test_special_page_writes_are_confined(tmp_path, filename, name, args):
    wiki = project(tmp_path)
    external = tmp_path / "external.md"
    external.write_text("private\n")
    (wiki / filename).symlink_to(external)
    assert rejected(rpc(tmp_path, [call(name, args)])[0])
    assert external.read_text() == "private\n"


def test_directory_symlink_and_export_destination(tmp_path):
    wiki = project(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    (wiki / "concepts").symlink_to(external, target_is_directory=True)
    assert rejected(rpc(tmp_path, [call("wiki.upsert_concept", {"slug": "bad", "sections": {"Definizione": "bad"}})])[0])
    assert list(external.iterdir()) == []
    exports = tmp_path / ".anjawiki" / "exports"
    exports.mkdir()
    victim = tmp_path.parent / (tmp_path.name + "-external-export")
    victim.write_text("unchanged")
    from datetime import datetime
    (exports / f"wiki-export-{datetime.now().date()}.json").symlink_to(victim)
    assert rejected(rpc(tmp_path, [call("wiki.export")])[0])
    assert victim.read_text() == "unchanged"


def test_attachment_source_allowed_destination_confined(tmp_path):
    wiki = project(tmp_path)
    good = wiki / "entities" / "good.md"
    good.write_text("---\ntitle: Good\n---\npublic\n")
    original = good.read_text()
    image = tmp_path / "image.png"
    image.write_bytes(b"image bytes")
    raw = tmp_path / ".anjawiki" / "raw" / "good"
    raw.mkdir(parents=True)
    victim = tmp_path / "victim.png"
    victim.write_bytes(b"unchanged")
    (raw / image.name).symlink_to(victim)
    args = {"slug": "good", "image_path": str(image)}
    assert rejected(rpc(tmp_path, [call("wiki.attach_image", args)])[0])
    assert victim.read_bytes() == b"unchanged"
    assert good.read_text() == original
    (raw / image.name).unlink()
    assert not rejected(rpc(tmp_path, [call("wiki.attach_image", args)])[0])
    assert (raw / image.name).read_bytes() == b"image bytes"


def test_internal_symlink_and_search_escape(tmp_path):
    wiki = project(tmp_path)
    good = wiki / "entities" / "good.md"
    good.write_text("---\ntitle: Good\n---\nneedle public\n")
    (wiki / "alias.md").symlink_to(good)
    assert "needle public" in payload(rpc(tmp_path, [call("wiki.read", {"slug": "alias"})])[0])["content"]
    outside = tmp_path / ".anjawiki" / "outside.md"
    outside.write_text("needle private\n")
    response = rpc(tmp_path, [call("wiki.search", {"query": "needle", "type": "../outside"})])[0]
    assert "needle private" not in json.dumps(response)


@pytest.mark.parametrize("server", ["mcp_memory_server.py", "mcp_code_server.py"])
def test_json_parse_error_then_valid_request(tmp_path, server):
    result = subprocess.run([PYTHON, str(PLUGIN / "scripts" / server)],
                            env={"ANJA_ROOT": str(tmp_path), "ANJA_CODE_EXEC": "0", **cov_env()},
                            input='{\n{"jsonrpc":"2.0","id":42,"method":"ping"}\n',
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    replies = [json.loads(line) for line in result.stdout.split("\n") if line]
    assert replies[0]["error"]["code"] == -32700
    assert replies[1] == {"jsonrpc": "2.0", "id": 42, "result": {}}


def test_root_precedence_and_discovery(tmp_path):
    target, other = tmp_path / "target", tmp_path / "other"
    project(target); project(other)
    sub = other / "nested"
    sub.mkdir()
    probe = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from code_search import _project_root
assert _project_root() == Path(sys.argv[2])
assert _project_root(Path(sys.argv[3])) == Path(sys.argv[3])
os.environ['ANJA_ROOT'] = sys.argv[2] + '/missing'
assert _project_root() is None
os.environ.pop('ANJA_ROOT')
assert _project_root() == Path(sys.argv[3])
"""
    result = subprocess.run([PYTHON, "-c", probe, str(PLUGIN / "scripts"), str(target.resolve()), str(other.resolve())],
                            cwd=sub, env={"ANJA_ROOT": str(target), **cov_env()},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_wiki_root_cannot_redirect_outside_project(tmp_path):
    target, other = tmp_path / "target", tmp_path / "other"
    project(other)
    (target / ".anjawiki").mkdir(parents=True)
    (target / ".anjawiki" / "wiki").symlink_to(other / ".anjawiki" / "wiki", target_is_directory=True)
    assert rejected(rpc(target, [call("wiki.upsert_entity", {"slug": "bad", "sections": {"Sintesi": "bad"}})])[0])
    assert not (other / ".anjawiki" / "wiki" / "entities" / "bad.md").exists()
