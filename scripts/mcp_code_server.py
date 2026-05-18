#!/usr/bin/env python3
"""mcp_code_server.py — Fase 17 — MCP server `anja_code`.

Tool: `execute_python` — esegue script Python in subprocess isolato.

Sicurezza (no shell injection):
  - subprocess.Popen con lista args, no shell=True
  - Env vars scrubbed (no API keys leak)
  - Cwd whitelist (scope hub o workspace:<name>)
  - Timeout hard 5min, output cap 50KB, memory limit
  - resource.setrlimit per cap memoria/file/cpu
  - No recursion (block reference a execute_python/anja_code)
"""

import json
import os
import resource
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional


PROTO_VERSION = "2024-11-05"
SERVER_NAME = "anja_code"
SERVER_VERSION = "0.1.0"

SCOPE = os.environ.get("ANJA_SCOPE", "hub")
ROOT = Path(os.environ.get("ANJA_ROOT", os.getcwd())).resolve()

DEFAULT_TIMEOUT_SEC = 300
MAX_TIMEOUT_SEC = 600
DEFAULT_MAX_OUTPUT_KB = 50
MAX_OUTPUT_KB_CAP = 200
DEFAULT_MEMORY_MB = 512
SCRIPT_MAX_BYTES = 100_000

SCRUB_PATTERNS = (
    "API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL",
    "TELEGRAM", "BOT_TOKEN", "AWS_", "GCP_", "AZURE_",
    "ANTHROPIC", "OPENAI", "XAI", "GROQ", "MISTRAL", "GEMINI",
    "OPENROUTER", "GROK", "DEEPSEEK", "STRIPE",
)
KEEP_ENV = ("HOME", "USER", "LANG", "LC_ALL", "PYTHONIOENCODING", "TERM", "TMPDIR")


def _hub_root_from_scope() -> Optional[Path]:
    if SCOPE == "hub":
        return ROOT
    env_hub = os.environ.get("ANJA_HUB")
    if env_hub:
        return Path(env_hub).expanduser().resolve()
    return None


def _scrub_env() -> dict:
    safe = {}
    for k, v in os.environ.items():
        if any(p in k.upper() for p in SCRUB_PATTERNS):
            continue
        if k in KEEP_ENV or k.startswith("PYTHON") or k == "PATH":
            safe[k] = v
    if "PATH" not in safe:
        safe["PATH"] = "/usr/local/bin:/usr/bin:/bin"
    safe["PYTHONIOENCODING"] = "utf-8"
    safe["PYTHONUNBUFFERED"] = "1"
    return safe


def _resolve_cwd_for_scope(scope: str, mode: str) -> tuple:
    if mode == "strict":
        return Path(tempfile.mkdtemp(prefix="anja-code-")), None
    hub = _hub_root_from_scope()
    if scope == "hub":
        if not hub:
            return None, "hub scope but hub root not resolvable"
        return hub, None
    if scope.startswith("workspace:"):
        if not hub:
            return None, "workspace scope but hub root not resolvable"
        name = scope.split(":", 1)[1].strip()
        ws = hub / "workspaces" / name
        if ws.is_symlink():
            ws = ws.resolve()
        if not ws.is_dir():
            return None, "workspace '" + name + "' not found"
        if (ws / ".anjawiki").is_dir():
            return ws / ".anjawiki", None
        return ws, None
    return None, "invalid scope: " + scope


def _set_limits(memory_mb: int):
    def _preexec():
        try:
            mem_bytes = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        except (ValueError, OSError):
            pass
        try:
            resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
        except (ValueError, OSError):
            pass
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (300, 600))
        except (ValueError, OSError):
            pass
    return _preexec


def tool_execute_python(args: dict) -> dict:
    script = args.get("script", "")
    if not isinstance(script, str) or not script.strip():
        return {"error": "script required (non-empty string)"}
    if len(script.encode("utf-8")) > SCRIPT_MAX_BYTES:
        return {"error": "script too large"}

    timeout = int(args.get("timeout", DEFAULT_TIMEOUT_SEC))
    timeout = max(5, min(timeout, MAX_TIMEOUT_SEC))
    max_output_kb = int(args.get("max_output_kb", DEFAULT_MAX_OUTPUT_KB))
    max_output_kb = max(1, min(max_output_kb, MAX_OUTPUT_KB_CAP))
    memory_mb = int(args.get("memory_mb", DEFAULT_MEMORY_MB))
    memory_mb = max(64, min(memory_mb, 2048))

    mode = (args.get("mode") or "project").strip()
    if mode not in ("project", "strict"):
        return {"error": "invalid mode: " + mode}

    if "execute_python" in script or "anja_code" in script:
        return {"error": "recursion guard: script references forbidden tokens"}

    cwd, err = _resolve_cwd_for_scope(SCOPE, mode)
    if err:
        return {"error": err}
    if not cwd or not cwd.is_dir():
        return {"error": "cwd not valid"}

    py_exe = sys.executable
    if mode == "project":
        for venv_dir in (cwd / ".venv", cwd / "venv", cwd.parent / ".venv"):
            venv_py = venv_dir / "bin" / "python3"
            if venv_py.is_file():
                py_exe = str(venv_py)
                break

    env = _scrub_env()
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, dir=str(cwd)) as tf:
        tf.write(script)
        script_path = tf.name

    output_bytes = max_output_kb * 1024
    try:
        # NB: subprocess.Popen with list args, NO shell. Path is a tempfile we just wrote.
        proc = subprocess.Popen(
            [py_exe, "-u", script_path],
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=_set_limits(memory_mb) if sys.platform != "win32" else None,
            start_new_session=True,
        )
        try:
            stdout_b, stderr_b = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
            return {"error": "timeout", "killed": True, "exit_code": -1, "timeout_sec": timeout}

        out_truncated = False
        err_truncated = False
        if len(stdout_b) > output_bytes:
            stdout_b = stdout_b[:output_bytes] + b"\n... [stdout truncated]"
            out_truncated = True
        if len(stderr_b) > output_bytes:
            stderr_b = stderr_b[:output_bytes] + b"\n... [stderr truncated]"
            err_truncated = True

        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": stdout_b.decode("utf-8", errors="replace"),
            "stderr": stderr_b.decode("utf-8", errors="replace") or None,
            "stdout_truncated": out_truncated,
            "stderr_truncated": err_truncated,
            "cwd": str(cwd),
            "mode": mode,
            "python": py_exe,
        }
    except FileNotFoundError as e:
        return {"error": "python not found: " + str(e)}
    except Exception as e:
        return {"error": type(e).__name__ + ": " + str(e)}
    finally:
        try:
            Path(script_path).unlink()
        except Exception:
            pass


TOOLS = [
    {
        "name": "execute_python",
        "description": (
            "Esegue uno script Python in subprocess isolato e ritorna stdout. "
            "Sandbox: env scrubbed (no API keys), timeout 5min, output 50KB cap, memory 512MB, "
            "cwd whitelist al workspace corrente. Solo print() ritorna al modello. "
            "PATTERN: collassa workflow multi-step (analisi dati, parsing, calcoli) in 1 sola call invece di 5+ tool. "
            "Es: leggi csv → calcola stats → scrivi report.md tutto in 1 script."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "Codice Python completo. Output via print(). Max 100KB."},
                "timeout": {"type": "integer", "description": "Timeout secondi (default 300, max 600)"},
                "max_output_kb": {"type": "integer", "description": "Output stdout cap KB (default 50, max 200)"},
                "memory_mb": {"type": "integer", "description": "Memory MB (default 512, max 2048)"},
                "mode": {"type": "string", "enum": ["project", "strict"],
                         "description": "project (default) = cwd workspace + venv. strict = tempdir isolato pulito"},
            },
            "required": ["script"],
        },
    },
]

TOOL_HANDLERS = {"execute_python": tool_execute_python}


def handle_request(req: dict):
    method = req.get("method")
    params = req.get("params") or {}
    req_id = req.get("id")

    if method == "initialize":
        return _ok(req_id, {
            "protocolVersion": PROTO_VERSION,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {"listChanged": False}},
        })
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return _ok(req_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            return _err(req_id, -32601, "unknown tool: " + str(name))
        try:
            result = handler(args)
            content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]
            return _ok(req_id, {"content": content, "isError": "error" in result})
        except Exception as e:
            return _err(req_id, -32603, "tool failed: " + type(e).__name__ + ": " + str(e))
    if method == "ping":
        return _ok(req_id, {})
    return _err(req_id, -32601, "method not found: " + str(method))


def _ok(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def main():
    print("[anja_code] starting (scope=" + SCOPE + " root=" + str(ROOT) + ")", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            err = _err(None, -32700, "parse error: " + str(e))
            sys.stdout.write(json.dumps(err) + "\n")
            sys.stdout.flush()
            continue
        resp = handle_request(req)
        if resp is None:
            continue
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
