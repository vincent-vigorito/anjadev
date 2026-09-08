"""Policy condivisa di discovery e invio; nessuna inizializzazione del provider."""
from __future__ import annotations

import fnmatch
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import code_index

REMOTE = {'openrouter', 'openai', 'voyage'}
DEFAULT_MODELS = {'openrouter': 'openai/text-embedding-3-small', 'openai': 'text-embedding-3-small',
                  'voyage': 'voyage-code-3', 'local': 'BAAI/bge-small-en', 'mock': 'mock-bow', 'none': None}
SENSITIVE = ('*.pem', '*.key', '*.p12', '*.pfx', '*.sqlite*', '*.db', 'credentials.*', 'secrets.*', '*_credentials.*', '*_secrets.*',
             'id_rsa*', 'id_ed25519*', '*.local.*', 'transcript*.json*')


class PolicyError(ValueError):
    code = 'index_policy_error'


def config(root):
    root = Path(root).resolve()
    state = root / '.anjawiki'
    if state.is_symlink() or not state.resolve().is_relative_to(root):
        raise PolicyError('project state escapes root')
    path = state / 'index-policy.json'
    if not path.exists():
        return {}
    if path.is_symlink() or not path.resolve().is_relative_to(Path(root).resolve()):
        raise PolicyError('policy must stay inside the project')
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError) as exc:
        raise PolicyError('invalid or unreadable index-policy.json') from exc
    if not isinstance(data, dict) or set(data) - {'include', 'exclude', 'remote', 'rerank_model'}:
        raise PolicyError('unknown index policy fields')
    for key in ('include', 'exclude'):
        if key in data and (not isinstance(data[key], list) or any(not isinstance(p, str) or not p or Path(p).is_absolute() or '..' in Path(p).parts or any(c in p for c in '*?[') for p in data[key])):
            raise PolicyError(key + ' must contain literal root-relative file/directory paths')
    if 'remote' in data and (not isinstance(data['remote'], dict) or set(data['remote']) != {'provider', 'model'}):
        raise PolicyError('remote must specify exactly provider and model')
    if 'rerank_model' in data and not isinstance(data['rerank_model'], str):
        raise PolicyError('rerank_model must be a string')
    return data


def provider_info():
    name = os.environ.get('ANJA_EMBED_PROVIDER', 'openrouter').strip().lower()
    return {'provider': name, 'model': os.environ.get('ANJA_EMBED_MODEL', DEFAULT_MODELS.get(name)),
            'destination': 'remote' if name in REMOTE else ('local' if name in ('mock','local') else 'disabled')}


def authorize(root, provider=None):
    info = provider_info() if provider is None else {'provider': provider.name, 'model': provider.model}
    if info['provider'] in REMOTE and config(root).get('remote') != {'provider': info['provider'], 'model': info['model']}:
        raise PolicyError('remote indexing requires matching provider/model in .anjawiki/index-policy.json')


def _git(command, **kwargs):
    try:
        result = subprocess.run(command, capture_output=True, timeout=30, **kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PolicyError('Git is required to evaluate ignore rules') from exc
    if result.returncode not in (0, 1):
        raise PolicyError('Git ignore evaluation failed')
    return result.stdout


def ignored(root, paths):
    if not paths:
        return {}, {}
    parents = {root} | {parent for path in paths for parent in path.parents if parent.is_relative_to(root)}
    has_rules = (root / '.anjaignore').is_file() or any((parent / '.gitignore').is_file() for parent in parents)
    in_git = shutil.which('git') and subprocess.run(['git', '-C', str(root), 'rev-parse', '--show-toplevel'], capture_output=True).returncode == 0
    if not has_rules and not in_git:
        return {}, {}
    if not shutil.which('git'):
        raise PolicyError('Git unavailable: cannot evaluate ignore files')
    raw_paths = b''.join(os.fsencode(str(p.relative_to(root))) + b'\0' for p in paths)
    with tempfile.TemporaryDirectory(prefix='anja-ignore-') as temp:
        gitdir = str(Path(temp) / 'repo.git')
        _git(['git', 'init', '--bare', '--quiet', gitdir])
        isolated = ['git', '--git-dir=' + gitdir, '--work-tree=' + str(root), '-c', 'core.bare=false', '-c', 'core.excludesFile=/dev/null']
        standard = ['git', '-C', str(root)] if in_git else isolated
        output = _git(standard + ['check-ignore', '--no-index', '-z', '--stdin'], input=raw_paths, cwd=root)
        git_ignored = {os.fsdecode(p): 'gitignore' for p in output.split(b'\0') if p}
        anja_ignored = {}
        rules = root / '.anjaignore'
        if rules.is_file():
            output = _git(isolated + ['ls-files', '--others', '--ignored', '--exclude-from=' + str(rules), '-z'], cwd=root)
            anja_ignored = {os.fsdecode(p): 'anjaignore' for p in output.split(b'\0') if p}
        return git_ignored, anja_ignored


def _literal_match(path, patterns):
    return any(path == p.rstrip('/') or path.startswith(p.rstrip('/') + '/') for p in patterns)


def discover(root, kind='code', include_sessions=False, single=None):
    root = Path(root).resolve()
    cfg = config(root)
    base = root if kind == 'code' else root / '.anjawiki/wiki'
    if base.is_symlink() or not base.resolve().is_relative_to(root):
        raise PolicyError('discovery root escapes project')
    candidates, excluded = [], []
    def reject(path, reason):
        excluded.append({'path': str(path.relative_to(root)), 'reason': reason, 'size': path.lstat().st_size if not path.is_dir() else None})
    if single is not None:
        path = Path(single).absolute()
        if not path.resolve().is_relative_to(base) or path.is_symlink() or any(p.is_symlink() for p in path.parents if p.is_relative_to(base)):
            raise PolicyError('wiki page must stay inside the wiki')
        entries = [path] if path.is_file() else []
    else:
        entries = []
        def fail(error):
            raise error
        for directory, dirs, names in os.walk(base, onerror=fail):
            for name in list(dirs):
                path = Path(directory) / name
                if name.startswith('.') or name in code_index.EXCLUDE_DIR_NAMES or path.is_symlink() or (path / '.git').exists() or (kind == 'wiki' and not include_sessions and name == 'sessions'):
                    reject(path, 'symlink' if path.is_symlink() else 'default_directory')
                    dirs.remove(name)
            dirs.sort()
            entries.extend(Path(directory) / name for name in sorted(names))
    for path in entries:
        rel = str(path.relative_to(root))
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            reject(path, 'symlink')
        elif path.name.startswith('.') or any(fnmatch.fnmatch(path.name.lower(), p) for p in SENSITIVE):
            reject(path, 'sensitive_or_hidden')
        elif (kind == 'code' and path.suffix.lower() not in code_index.LANG_BY_EXT) or (kind == 'wiki' and path.suffix != '.md'):
            reject(path, 'unsupported_type')
        elif _literal_match(rel, cfg.get('exclude', [])):
            reject(path, 'config_exclude')
        elif cfg.get('include') and not _literal_match(rel, cfg['include']):
            reject(path, 'outside_config_include')
        else:
            candidates.append(path)
    git_rules, anja_rules = ignored(root, candidates)
    allowed = []
    for path in candidates:
        rel = str(path.relative_to(root))
        reason = git_rules.get(rel) or anja_rules.get(rel)
        if reason:
            reject(path, reason)
        else:
            allowed.append(path)
    return allowed, excluded


def preview(root, kind='code', include_sessions=False, single=None):
    from index_pipeline import snapshot_file
    root = Path(root).resolve()
    import sqlite3
    from types import SimpleNamespace

    from project_diagnostics import provider

    import code_db
    settings = provider()
    database = root / '.anjawiki/code-index.db'
    migration = False
    if database.is_symlink():
        raise PolicyError('index database must not be a symlink')
    if database.is_file():
        db = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
        try:
            rows = dict(db.execute('SELECT key,value FROM meta'))
            expected = code_db.fingerprint(SimpleNamespace(name=settings['provider'], model=settings['model'], dim=settings['dimension']))
            migration = rows.get('index_fingerprint') != expected
            if migration:
                include_sessions = include_sessions or any('/wiki/sessions/' in row[0] for row in db.execute("SELECT file_path FROM chunks WHERE kind='wiki'"))
        finally:
            db.close()
    scopes = ('code','wiki') if migration else (kind,)
    files, excluded = [], []
    for scope in scopes:
        allowed, denied = discover(root, scope, include_sessions, single if scope == 'wiki' and not migration else None)
        excluded.extend(denied)
        for path in allowed:
            info, _ = snapshot_file(path)
            row = {'path': str(path.relative_to(root)), 'kind':scope, 'size': info['size'], 'hash': info['hash']}
            if info['excluded']:
                excluded.append({**row, 'reason': info['excluded']})
            else:
                files.append(row)
    permitted = True
    try:
        authorize(root)
    except PolicyError:
        permitted = False
    return {'dry_run': True, 'root': str(root), 'kind': kind, 'scopes':list(scopes), 'migration':migration, 'selection':'eligible_files', 'files': files, 'excluded': excluded,
            'total_bytes': sum(f['size'] for f in files), 'provider': provider_info(), 'remote_authorized': permitted}
