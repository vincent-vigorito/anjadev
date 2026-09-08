"""Diagnostica locale: non inizializza né interroga provider di embedding."""
import importlib.util
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import index_pipeline
import index_policy
import session_archive
import wiki_jobs

import code_db
import embed_providers


def provider():
    info = index_policy.provider_info()
    name, model = info['provider'], info['model']
    known = {**embed_providers.OpenRouterProvider._KNOWN_DIMS, 'text-embedding-3-small':1536,
             'text-embedding-3-large':3072, 'voyage-code-3':1024, 'voyage-3':1024, 'voyage-3-lite':512,
             'BAAI/bge-small-en':384, 'mock-bow':64}
    dim = known.get(model)
    if dim is None and os.environ.get('ANJA_EMBED_DIM', '').isdigit():
        dim = int(os.environ['ANJA_EMBED_DIM'])
    info['dimension'] = dim
    key = {'openrouter':'OPENROUTER_API_KEY', 'openai':'OPENAI_API_KEY', 'voyage':'VOYAGE_API_KEY'}.get(name)
    info['credential_available'] = bool(os.environ.get(key, '') or os.environ.get('ANJA_EMBED_API_KEY')) if key else None
    return info


def diagnose(root):
    root = Path(root).resolve()
    state = root / '.anjawiki'
    if not state.resolve().is_relative_to(root) or state.is_symlink() or (state / 'code-index.db').is_symlink() or (state / 'wiki-jobs.db').is_symlink():
        return {'status':'failed', 'code':'unsafe_project_state', 'error':'project state must remain inside root'}
    info = provider()
    candidate = SimpleNamespace(name=info['provider'], model=info['model'], dim=info['dimension']) if info['dimension'] else None
    try:
        result = index_pipeline.index_status(root, candidate)
    except (sqlite3.Error, ValueError, OSError) as exc:
        result = {'status':'failed', 'code':'index_database_error', 'error':str(exc)}
    manifest = Path(__file__).resolve().parents[1] / '.claude-plugin/plugin.json'
    schema_file = root / '.anjawiki/.schema-version'
    result.update({'schema': schema_file.read_text().strip() if schema_file.is_file() and not schema_file.is_symlink() else 'legacy_or_missing', 'root':str(root), 'version':json.loads(manifest.read_text())['version'],
                   'pipeline_version':code_db.PIPELINE_VERSION, 'provider_config':info,
                   'capabilities':{'git': bool(__import__('shutil').which('git')),
                                   'sqlite_vec': importlib.util.find_spec('sqlite_vec') is not None,
                                   'sqlite_extensions': hasattr(sqlite3.Connection, 'enable_load_extension')}})
    try:
        index_policy.authorize(root)
        result['remote_authorized'] = True
    except Exception:
        result['remote_authorized'] = False
    for scope in result.get('scopes', {}).values():
        stamp = scope.get('last_success')
        scope['age_seconds'] = max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds())) if stamp else None
    result['wiki_jobs'] = wiki_jobs.status(root)
    result['transcripts'] = session_archive.diagnose(root)
    return result
